import pytest

from plc_gateway.config import parse_config
from plc_gateway.gateway import Gateway


class FakeReader:
    def __init__(self):
        self.connected = False
        self.values = {"pressure": 4.2, "running": True}
        self.fail_connect = 0
        self.fail_read = False

    def connect(self):
        if self.fail_connect:
            self.fail_connect -= 1
            raise ConnectionError("unreachable")
        self.connected = True

    def read(self):
        if self.fail_read:
            raise OSError("connection reset")
        return dict(self.values)

    def close(self):
        self.connected = False


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, subtopic, payload, retain=None, absolute=False):
        self.messages.append((("/" if absolute else "") + subtopic, payload))

    def take(self):
        out, self.messages = self.messages, []
        return out


class Clock:
    def __init__(self):
        self.t = 1_700_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def setup():
    cfg = parse_config(
        {
            "plc": {"host": "plc"},
            "gateway": {"poll_interval_ms": 100, "heartbeat_s": 60, "reconnect_max_s": 8},
            "tags": [
                {"name": "pressure", "address": "DB1.DBD0", "type": "REAL", "unit": "bar", "deadband": 0.1},
                {"name": "running", "address": "DB1.DBX4.0", "type": "BOOL"},
            ],
        }
    )
    reader, pub, clock = FakeReader(), FakePublisher(), Clock()
    return Gateway(cfg, reader, pub, clock=clock, monotonic=clock), reader, pub, clock


def topics(messages):
    return [m[0] for m in messages]


def test_first_cycle_publishes_status_snapshot_and_stats(setup):
    gw, _, pub, _ = setup
    wait = gw.step()
    msgs = pub.take()
    assert topics(msgs) == ["status/plc", "tags/pressure", "tags/running", "stats"]
    assert msgs[0][1]["state"] == "connected"
    assert msgs[1][1]["value"] == 4.2 and msgs[1][1]["unit"] == "bar"
    assert msgs[1][1]["ts"].endswith("+00:00")
    assert wait == pytest.approx(0.1)


def test_publishes_only_changes_between_heartbeats(setup):
    gw, reader, pub, clock = setup
    gw.step()
    pub.take()
    clock.t += 1
    gw.step()
    assert pub.take() == []
    reader.values["pressure"] = 4.25  # inside deadband
    reader.values["running"] = False
    clock.t += 1
    gw.step()
    assert topics(pub.take()) == ["tags/running"]


def test_heartbeat_republishes_everything(setup):
    gw, _, pub, clock = setup
    gw.step()
    pub.take()
    clock.t += 61
    gw.step()
    assert topics(pub.take()) == ["tags/pressure", "tags/running", "stats"]


def test_unreachable_plc_backs_off_exponentially_and_reports_once(setup):
    gw, reader, pub, _ = setup
    reader.fail_connect = 5
    delays = [gw.step() for _ in range(5)]
    assert delays == [1, 2, 4, 8, 8]
    msgs = pub.take()
    assert topics(msgs) == ["status/plc"]  # state change reported once, not every retry
    assert msgs[0][1]["state"] == "disconnected"


def test_read_failure_reconnects_and_republishes_full_snapshot(setup):
    gw, reader, pub, clock = setup
    gw.step()
    pub.take()
    reader.fail_read = True
    clock.t += 1
    gw.step()
    assert [m[1].get("state") for m in pub.take()] == ["disconnected"]
    assert gw.stats.read_errors == 1
    reader.fail_read = False
    clock.t += 1
    gw.step()
    msgs = pub.take()
    assert topics(msgs)[:3] == ["status/plc", "tags/pressure", "tags/running"]
    assert gw.stats.reconnects == 1


def test_tag_with_its_own_topic_is_published_there():
    cfg = parse_config(
        {
            "plc": {"host": "plc"},
            "tags": [
                {
                    "name": "pt_104",
                    "address": "DB1.DBD0",
                    "type": "REAL",
                    "unit": "bar",
                    "topic": "planta1/exterior/pt-104/presion",
                },
                {"name": "running", "address": "DB1.DBX4.0", "type": "BOOL"},
            ],
        }
    )
    reader, pub, clock = FakeReader(), FakePublisher(), Clock()
    reader.values = {"pt_104": 6.7, "running": True}
    Gateway(cfg, reader, pub, clock=clock, monotonic=clock).step()
    published = dict(pub.take())
    assert published["/planta1/exterior/pt-104/presion"]["value"] == 6.7
    assert published["/planta1/exterior/pt-104/presion"]["unit"] == "bar"
    assert "tags/running" in published
