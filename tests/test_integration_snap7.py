"""End-to-end read against a real Snap7 server (the simulator's DB layout)."""

import socket
import struct

import pytest
import snap7
from snap7.type import SrvArea

from plc_gateway.config import load_config
from plc_gateway.plc import PlcReader
from plc_gateway.simulator import SimulatedLine

from .test_config import EXAMPLE


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def plc_server():
    line = SimulatedLine(seed=1)
    line.update(65.0)  # inside the pressure ramp
    server = snap7.Server(log=False)
    server.register_area(SrvArea.DB, 1, line.db)
    port = free_port()
    server.start(tcp_port=port)
    yield line, port
    server.stop()


def test_reader_decodes_simulated_db(plc_server, monkeypatch):
    line, port = plc_server
    monkeypatch.setenv("PLC_HOST", "127.0.0.1")
    monkeypatch.setenv("PLC_PORT", str(port))
    cfg = load_config(EXAMPLE)

    reader = PlcReader(cfg.plc, cfg.tags)
    assert len(reader.blocks) == 1  # all eight tags in one request
    reader.connect()
    try:
        values = reader.read()
    finally:
        reader.close()

    expected_pressure = struct.unpack_from(">f", line.db, 8)[0]
    assert values["pi_104_pressure"] == pytest.approx(expected_pressure, abs=1e-5)
    assert 4.0 < values["pi_104_pressure"] < 7.0
    assert values["machine_state"] in (0, 1, 2)
    assert isinstance(values["pump_running"], bool)
    assert set(values) == {t.name for t in cfg.tags}
