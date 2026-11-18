"""Configuration: a YAML file for the plant layout, environment variables for secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .tags import Tag, TagError


class ConfigError(ValueError):
    """Raised when the configuration file is missing or invalid."""


@dataclass
class PlcConfig:
    host: str
    rack: int = 0
    slot: int = 1
    port: int = 102


@dataclass
class MqttConfig:
    host: str = "localhost"
    port: int = 1883
    client_id: str = "plc-gateway"
    base_topic: str = "plant/line1/plc1"
    qos: int = 1
    retain: bool = True
    username: str | None = None
    password: str | None = field(default=None, repr=False)


@dataclass
class GatewayConfig:
    poll_interval_ms: int = 200
    heartbeat_s: float = 60.0
    reconnect_max_s: float = 30.0


@dataclass
class Config:
    plc: PlcConfig
    mqtt: MqttConfig
    gateway: GatewayConfig
    tags: list[Tag]


def _section(raw: dict, key: str) -> dict:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"'{key}' must be a mapping")
    return value


def _build(cls, values: dict, section: str):
    try:
        return cls(**values)
    except TypeError as exc:
        raise ConfigError(f"invalid '{section}' section: {exc}") from None


def _env_overrides(plc: dict, mqtt: dict) -> None:
    """Deployment-specific values and secrets come from the environment, never from YAML."""
    env = os.environ
    for key, target, field_name, cast in (
        ("PLC_HOST", plc, "host", str),
        ("PLC_PORT", plc, "port", int),
        ("MQTT_HOST", mqtt, "host", str),
        ("MQTT_PORT", mqtt, "port", int),
        ("MQTT_USERNAME", mqtt, "username", str),
        ("MQTT_PASSWORD", mqtt, "password", str),
    ):
        if env.get(key):
            target[field_name] = cast(env[key])


def parse_config(raw: dict) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("the configuration must be a YAML mapping")

    plc = _section(raw, "plc")
    mqtt = _section(raw, "mqtt")
    if "password" in mqtt:
        raise ConfigError("do not store the MQTT password in the YAML file; use MQTT_PASSWORD")
    _env_overrides(plc, mqtt)
    if not plc.get("host"):
        raise ConfigError("plc.host is required (or set PLC_HOST)")

    tags_raw = raw.get("tags")
    if not tags_raw or not isinstance(tags_raw, list):
        raise ConfigError("at least one tag is required under 'tags'")

    tags: list[Tag] = []
    for i, item in enumerate(tags_raw):
        if not isinstance(item, dict) or not {"name", "address", "type"} <= item.keys():
            raise ConfigError(f"tag #{i + 1} needs 'name', 'address' and 'type'")
        extra = {k: item[k] for k in ("deadband", "unit") if k in item}
        try:
            tags.append(Tag.from_address(str(item["name"]), str(item["address"]), str(item["type"]), **extra))
        except TagError as exc:
            raise ConfigError(str(exc)) from None

    names = [t.name for t in tags]
    duplicated = sorted({n for n in names if names.count(n) > 1})
    if duplicated:
        raise ConfigError(f"duplicated tag names: {', '.join(duplicated)}")

    mqtt_cfg = _build(MqttConfig, mqtt, "mqtt")
    if mqtt_cfg.qos not in (0, 1, 2):
        raise ConfigError("mqtt.qos must be 0, 1 or 2")
    mqtt_cfg.base_topic = mqtt_cfg.base_topic.strip("/")

    return Config(
        plc=_build(PlcConfig, plc, "plc"),
        mqtt=mqtt_cfg,
        gateway=_build(GatewayConfig, _section(raw, "gateway"), "gateway"),
        tags=tags,
    )


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"configuration file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return parse_config(yaml.safe_load(fh))
