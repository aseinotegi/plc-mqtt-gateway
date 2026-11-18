"""MQTT publishing with a stable topic and payload contract."""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

import paho.mqtt.client as mqtt

from .config import MqttConfig

log = logging.getLogger(__name__)


class Publisher(Protocol):
    def publish(self, subtopic: str, payload: dict[str, Any], retain: bool | None = None) -> None: ...


def encode(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


class MqttPublisher:
    """Publishes under ``<base_topic>/...``.

    * ``<base>/status`` (retained): gateway and PLC state. The broker publishes
      ``{"gateway": "offline"}`` by itself if the gateway dies (Last Will).
    * ``<base>/tags/<name>`` (retained): ``{"value": ..., "ts": ..., "unit": ...}``.
    * ``<base>/stats``: counters sent with every heartbeat.

    paho-mqtt reconnects on its own; messages with QoS >= 1 are queued meanwhile.
    """

    def __init__(self, config: MqttConfig) -> None:
        self._cfg = config
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=config.client_id)
        if config.username:
            self._client.username_pw_set(config.username, config.password)
        self._client.will_set(self.topic("status"), encode({"gateway": "offline"}), qos=1, retain=True)
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    def topic(self, subtopic: str) -> str:
        return f"{self._cfg.base_topic}/{subtopic}"

    def start(self) -> None:
        self._client.connect_async(self._cfg.host, self._cfg.port, keepalive=30)
        self._client.loop_start()

    def publish(self, subtopic: str, payload: dict[str, Any], retain: bool | None = None) -> None:
        retain = self._cfg.retain if retain is None else retain
        self._client.publish(self.topic(subtopic), encode(payload), qos=self._cfg.qos, retain=retain)

    def stop(self) -> None:
        info = self._client.publish(self.topic("status"), encode({"gateway": "offline"}), qos=1, retain=True)
        try:
            info.wait_for_publish(timeout=2)
        except (RuntimeError, ValueError):
            pass
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.error("MQTT connection refused: %s", reason_code)
            return
        log.info("connected to MQTT broker %s:%d", self._cfg.host, self._cfg.port)
        client.publish(self.topic("status"), encode({"gateway": "online"}), qos=1, retain=True)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            log.warning("MQTT connection lost (%s); reconnecting", reason_code)
