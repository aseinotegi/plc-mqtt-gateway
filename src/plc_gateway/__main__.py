"""Command line entry point: ``plc-gateway -c config.yaml``."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from . import __version__
from .config import ConfigError, load_config
from .gateway import Gateway
from .plc import PlcReader
from .publisher import MqttPublisher


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plc-gateway", description="Siemens S7 PLC -> MQTT gateway")
    parser.add_argument("-c", "--config", default="config.yaml", help="YAML configuration file")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logging.error("configuration error: %s", exc)
        return 2

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    publisher = MqttPublisher(config.mqtt)
    publisher.start()
    gateway = Gateway(config, PlcReader(config.plc, config.tags), publisher)
    try:
        gateway.run(stop)
    finally:
        publisher.stop()
        logging.info("gateway stopped: %s", gateway.stats.as_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
