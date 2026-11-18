"""A small simulated S7 PLC so the gateway can be tried without hardware.

DB1 layout (matches ``config/example.yaml``)::

    DBX0.0  BOOL  pump_running
    DBX0.1  BOOL  high_pressure_alarm      (pressure > 6.0 bar)
    DBX0.2  BOOL  valve_xv104_closed
    DBW2    INT   machine_state            (0 stopped, 1 running, 2 alarm)
    DBD4    DINT  parts_count
    DBD8    REAL  pi_104_pressure          (bar)
    DBD12   REAL  tank_level               (%)
    DBD16   REAL  motor_temperature        (°C)

Every two minutes the line pressure peaks at 6.8 bar, the alarm trips, the valve
closes and pressure returns to normal: the same story told on andersein.com.
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import signal
import struct
import threading
import time

import snap7
from snap7.type import SrvArea

log = logging.getLogger("plc-sim")

DB_NUMBER = 1
DB_SIZE = 32
CYCLE_S = 120.0
NORMAL_BAR, PEAK_BAR, ALARM_BAR = 4.2, 6.8, 6.0


class SimulatedLine:
    def __init__(self, seed: int | None = None) -> None:
        self.db = bytearray(DB_SIZE)
        self._rng = random.Random(seed)
        self._parts = 0
        self._temp = 48.0
        self._valve_closed_at: float | None = None

    def pressure(self, t: float) -> float:
        phase = t % CYCLE_S
        noise = self._rng.uniform(-0.04, 0.04)
        if self._valve_closed_at is not None:
            # exponential decay back to normal once the valve is closed
            since = t - self._valve_closed_at
            return NORMAL_BAR + (PEAK_BAR - NORMAL_BAR) * math.exp(-since / 3) + noise
        if 60 <= phase < 68:
            return NORMAL_BAR + (PEAK_BAR - NORMAL_BAR) * (phase - 60) / 8 + noise
        return NORMAL_BAR + noise

    def update(self, t: float) -> None:
        phase = t % CYCLE_S
        if phase < 1:
            self._valve_closed_at = None  # operator reopens the valve each cycle
        pressure = self.pressure(t)
        alarm = pressure > ALARM_BAR
        if alarm and self._valve_closed_at is None and phase >= 67:
            self._valve_closed_at = t  # the inspection robot closes XV-104
        running = (phase % 40) < 30
        if running and self._rng.random() < 0.02:
            self._parts += 1
        target = 62.0 if running else 40.0
        self._temp += (target - self._temp) * 0.002 + self._rng.uniform(-0.05, 0.05)

        flags = running | (alarm << 1) | ((self._valve_closed_at is not None) << 2)
        self.db[0] = flags
        struct.pack_into(">h", self.db, 2, 2 if alarm else 1 if running else 0)
        struct.pack_into(">i", self.db, 4, self._parts)
        struct.pack_into(">f", self.db, 8, pressure)
        struct.pack_into(">f", self.db, 12, 60 + 20 * math.sin(2 * math.pi * t / 300))
        struct.pack_into(">f", self.db, 16, self._temp)


def serve(port: int, stop: threading.Event, period: float = 0.1) -> None:
    line = SimulatedLine()
    server = snap7.Server(log=False)
    server.register_area(SrvArea.DB, DB_NUMBER, line.db)
    server.start(tcp_port=port)
    log.info("simulated PLC listening on port %d (DB%d, %d bytes)", port, DB_NUMBER, DB_SIZE)
    start = time.monotonic()
    try:
        while not stop.is_set():
            line.update(time.monotonic() - start)
            stop.wait(period)
    finally:
        server.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plc-sim", description="Simulated Siemens S7 PLC")
    parser.add_argument("--port", type=int, default=1102, help="TCP port (S7 uses 102, which needs root)")
    args = parser.parse_args(argv)
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    serve(args.port, stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
