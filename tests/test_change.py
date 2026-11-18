from plc_gateway.change import ChangeDetector
from plc_gateway.tags import Tag

TAGS = [
    Tag("pressure", 1, 0, "REAL", deadband=0.1),
    Tag("running", 1, 4, "BOOL", bit=0),
    Tag("count", 1, 6, "DINT"),
]


def test_first_read_publishes_everything():
    det = ChangeDetector(TAGS)
    values = {"pressure": 4.2, "running": True, "count": 1}
    assert det.changes(values) == values


def test_only_changes_are_reported():
    det = ChangeDetector(TAGS)
    det.changes({"pressure": 4.2, "running": True, "count": 1})
    assert det.changes({"pressure": 4.2, "running": True, "count": 1}) == {}
    assert det.changes({"pressure": 4.2, "running": False, "count": 2}) == {"running": False, "count": 2}


def test_deadband_is_measured_from_last_published_value():
    det = ChangeDetector(TAGS)
    det.changes({"pressure": 4.20})
    assert det.changes({"pressure": 4.25}) == {}
    # small steps that each stay inside the deadband still get reported once they add up
    assert det.changes({"pressure": 4.29}) == {}
    assert det.changes({"pressure": 4.31}) == {"pressure": 4.31}


def test_snapshot_and_reset():
    det = ChangeDetector(TAGS)
    det.snapshot({"pressure": 4.2, "running": True})
    assert det.changes({"pressure": 4.2, "running": True}) == {}
    det.reset()
    assert det.changes({"pressure": 4.2}) == {"pressure": 4.2}
