from pathlib import Path

import pytest

from plc_gateway.config import ConfigError, load_config, parse_config

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "example.yaml"


def minimal(**overrides):
    raw = {
        "plc": {"host": "10.0.0.10"},
        "tags": [{"name": "level", "address": "DB1.DBD0", "type": "REAL"}],
    }
    raw.update(overrides)
    return raw


def test_example_config_is_valid():
    cfg = load_config(EXAMPLE)
    assert cfg.mqtt.base_topic == "plant/line1/plc1"
    assert len(cfg.tags) == 8
    pressure = next(t for t in cfg.tags if t.name == "pi_104_pressure")
    assert (pressure.db, pressure.offset, pressure.type, pressure.unit) == (1, 8, "REAL", "bar")


def test_defaults():
    cfg = parse_config(minimal())
    assert (cfg.plc.rack, cfg.plc.slot, cfg.plc.port) == (0, 1, 102)
    assert cfg.gateway.poll_interval_ms == 200


def test_environment_overrides_hosts_and_secrets(monkeypatch):
    monkeypatch.setenv("PLC_HOST", "192.168.0.50")
    monkeypatch.setenv("MQTT_HOST", "broker")
    monkeypatch.setenv("MQTT_PASSWORD", "s3cret")
    cfg = parse_config(minimal())
    assert cfg.plc.host == "192.168.0.50"
    assert cfg.mqtt.host == "broker"
    assert cfg.mqtt.password == "s3cret"
    assert "s3cret" not in repr(cfg.mqtt)


def test_password_in_yaml_is_rejected():
    with pytest.raises(ConfigError, match="MQTT_PASSWORD"):
        parse_config(minimal(mqtt={"password": "plain"}))


@pytest.mark.parametrize(
    "raw",
    [
        {"tags": [{"name": "a", "address": "DB1.DBD0", "type": "REAL"}]},  # no host
        {"plc": {"host": "x"}},  # no tags
        {"plc": {"host": "x"}, "tags": [{"name": "a", "address": "DB1.DBD0"}]},  # no type
        {"plc": {"host": "x"}, "tags": [{"name": "a", "address": "DB1.DBW0", "type": "REAL"}]},
        {"plc": {"host": "x", "unknown": 1}, "tags": [{"name": "a", "address": "DB1.DBD0", "type": "REAL"}]},
    ],
)
def test_invalid_configs(raw, monkeypatch):
    monkeypatch.delenv("PLC_HOST", raising=False)
    with pytest.raises(ConfigError):
        parse_config(raw)


def test_duplicated_tag_names():
    tags = [{"name": "a", "address": "DB1.DBD0", "type": "REAL"}] * 2
    with pytest.raises(ConfigError, match="duplicated"):
        parse_config(minimal(tags=tags))


BAD_TOPICS = ["planta1/+/presion", "/planta1/pt-104", "planta1//pt-104", "planta1/pt-104/", "a b"]


@pytest.mark.parametrize("topic", BAD_TOPICS)
def test_rejects_invalid_tag_topics(topic):
    tag = {"name": "p", "address": "DB1.DBD0", "type": "REAL", "topic": topic}
    raw = {"plc": {"host": "plc"}, "tags": [tag]}
    with pytest.raises(ConfigError, match="topic"):
        parse_config(raw)
