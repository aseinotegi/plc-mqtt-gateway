"""The polling loop: read, detect changes, publish, survive failures."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from .change import ChangeDetector
from .config import Config
from .publisher import Publisher
from .tags import Value

log = logging.getLogger(__name__)


def iso_now(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class Stats:
    reads: int = 0
    read_errors: int = 0
    published: int = 0
    reconnects: int = 0
    last_cycle_ms: float = 0.0

    def as_dict(self) -> dict:
        return {
            "reads": self.reads,
            "read_errors": self.read_errors,
            "published": self.published,
            "reconnects": self.reconnects,
            "last_cycle_ms": round(self.last_cycle_ms, 2),
        }


class Gateway:
    """Polls the PLC and publishes by exception.

    * First read after (re)connecting: every tag is published.
    * Then only tags whose value changed (beyond their deadband).
    * Every ``heartbeat_s``: all tags again plus counters, so late subscribers and
      historians get a full picture even when nothing moves.
    * PLC unreachable: ``status/plc`` goes to ``disconnected`` and reconnection is
      retried with exponential backoff (1 s, 2 s, 4 s … up to ``reconnect_max_s``).
    """

    def __init__(
        self,
        config: Config,
        reader,
        publisher: Publisher,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cfg = config.gateway
        self._units = {t.name: t.unit for t in config.tags if t.unit}
        self._reader = reader
        self._publisher = publisher
        self._clock = clock
        self._monotonic = monotonic
        self._detector = ChangeDetector(config.tags)
        self._backoff = 1.0
        self._plc_state: str | None = None
        self._last_heartbeat: float | None = None
        self.stats = Stats()

    # -- public API -------------------------------------------------------------

    def run(self, stop: threading.Event) -> None:
        log.info("gateway running (poll every %d ms)", self._cfg.poll_interval_ms)
        while not stop.is_set():
            stop.wait(self.step())
        self._reader.close()

    def step(self) -> float:
        """Run one cycle and return how many seconds to wait before the next one."""
        started = self._monotonic()

        if not self._reader.connected:
            if not self._connect():
                delay = self._backoff
                self._backoff = min(self._backoff * 2, self._cfg.reconnect_max_s)
                return delay

        try:
            values = self._reader.read()
        except Exception as exc:  # noqa: BLE001 - any read failure means: reconnect
            self.stats.read_errors += 1
            log.warning("PLC read failed: %s", exc)
            self._reader.close()
            self._set_plc_state("disconnected", error=str(exc))
            return min(self._backoff, self._cfg.reconnect_max_s)

        self.stats.reads += 1
        now = self._clock()
        if self._last_heartbeat is None or now - self._last_heartbeat >= self._cfg.heartbeat_s:
            self._publish_values(self._detector.snapshot(values), now)
            self._last_heartbeat = now
            self._publisher.publish("stats", {**self.stats.as_dict(), "ts": iso_now(now)}, retain=False)
        else:
            self._publish_values(self._detector.changes(values), now)

        self.stats.last_cycle_ms = (self._monotonic() - started) * 1000
        return max(self._cfg.poll_interval_ms / 1000 - self.stats.last_cycle_ms / 1000, 0.0)

    # -- internals --------------------------------------------------------------

    def _connect(self) -> bool:
        try:
            self._reader.connect()
        except Exception as exc:  # noqa: BLE001
            log.warning("cannot reach PLC (%s); retrying in %.0f s", exc, self._backoff)
            self._set_plc_state("disconnected", error=str(exc))
            return False
        if self._plc_state is not None:
            self.stats.reconnects += 1
        self._backoff = 1.0
        self._detector.reset()
        self._last_heartbeat = None  # publish a full snapshot right away
        self._set_plc_state("connected")
        return True

    def _publish_values(self, values: dict[str, Value], now: float) -> None:
        ts = iso_now(now)
        for name, value in values.items():
            payload = {"value": value, "ts": ts}
            if name in self._units:
                payload["unit"] = self._units[name]
            self._publisher.publish(f"tags/{name}", payload)
        self.stats.published += len(values)

    def _set_plc_state(self, state: str, error: str | None = None) -> None:
        if state == self._plc_state:
            return
        self._plc_state = state
        payload = {"state": state, "ts": iso_now(self._clock())}
        if error:
            payload["error"] = error
        self._publisher.publish("status/plc", payload, retain=True)
