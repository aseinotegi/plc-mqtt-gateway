"""Read-only access to a Siemens S7 PLC through Snap7."""

from __future__ import annotations

import logging
from collections.abc import Callable

import snap7

from .config import PlcConfig
from .tags import ReadBlock, Tag, Value, plan_reads

log = logging.getLogger(__name__)


class PlcReader:
    """Reads the configured tags, one ``db_read`` per planned block.

    The gateway never writes to the PLC: it only reads data blocks, so it cannot
    interfere with the machine it is observing.
    """

    def __init__(
        self,
        config: PlcConfig,
        tags: list[Tag],
        client_factory: Callable[[], snap7.Client] = snap7.Client,
    ) -> None:
        self._config = config
        self._factory = client_factory
        self._client: snap7.Client | None = None
        self.blocks: list[ReadBlock] = plan_reads(tags)
        log.info(
            "%d tags grouped into %d read(s): %s",
            len(tags),
            len(self.blocks),
            ", ".join(f"DB{b.db}[{b.start}..{b.start + b.size - 1}]" for b in self.blocks),
        )

    @property
    def connected(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(self._client.get_connected())
        except Exception:  # noqa: BLE001 - a broken socket can raise here
            return False

    def connect(self) -> None:
        self.close()
        cfg = self._config
        client = self._factory()
        client.connect(cfg.host, cfg.rack, cfg.slot, tcp_port=cfg.port)
        self._client = client
        log.info("connected to PLC %s:%d (rack %d, slot %d)", cfg.host, cfg.port, cfg.rack, cfg.slot)

    def read(self) -> dict[str, Value]:
        if self._client is None:
            raise ConnectionError("PLC not connected")
        values: dict[str, Value] = {}
        for block in self.blocks:
            data = self._client.db_read(block.db, block.start, block.size)
            for tag in block.tags:
                values[tag.name] = tag.decode(data, block.start)
        return values

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:  # noqa: BLE001 - closing a dead connection must never fail
                pass
            self._client = None
