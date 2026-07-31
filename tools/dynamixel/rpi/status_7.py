#!/usr/bin/env python3
"""Read-only status monitor for seven configured XL330 motors."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    from dynamixel_sdk import COMM_SUCCESS, PacketHandler, PortHandler
except ImportError as error:
    print(
        "ERROR: dynamixel_sdk is not installed. Run: "
        "./.venv/bin/python -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1) from error


ADDR_OPERATING_MODE = 11
ADDR_TORQUE_ENABLE = 64
ADDR_HARDWARE_ERROR = 70
ADDR_GOAL_POSITION = 116
ADDR_MOVING = 122
ADDR_MOVING_STATUS = 123
ADDR_PRESENT_CURRENT = 126
ADDR_PRESENT_VELOCITY = 128
ADDR_PRESENT_POSITION = 132
ADDR_PRESENT_VOLTAGE = 144
ADDR_PRESENT_TEMPERATURE = 146

COUNTS_PER_REV = 4096
DEGREES_PER_COUNT = 360.0 / COUNTS_PER_REV


def signed_16(value: int) -> int:
    return value - (1 << 16) if value & (1 << 15) else value


def signed_32(value: int) -> int:
    return value - (1 << 32) if value & (1 << 31) else value


def nearest_zero_error(position: int) -> int:
    """Return signed distance from the nearest 0 + 4096*n coordinate."""
    return (position + COUNTS_PER_REV // 2) % COUNTS_PER_REV - COUNTS_PER_REV // 2


@dataclass(frozen=True)
class BusConfig:
    name: str
    device: str
    ids: tuple[int, ...]


def load_config(path: Path) -> tuple[int, list[BusConfig]]:
    with path.open(encoding="utf-8") as stream:
        raw = json.load(stream)
    if raw.get("protocol_version") != 2.0:
        raise ValueError("motor_config.json must use protocol_version 2.0")
    buses = [
        BusConfig(item["name"], item["device"], tuple(item["ids"]))
        for item in raw["buses"]
    ]
    ids = [motor_id for bus in buses for motor_id in bus.ids]
    if sorted(ids) != list(range(1, 8)):
        raise ValueError("motor_config.json must contain IDs 1 through 7 exactly once")
    return int(raw["baudrate"]), buses


class Reader:
    def __init__(self, packet: PacketHandler, port: PortHandler, motor_id: int) -> None:
        self.packet = packet
        self.port = port
        self.motor_id = motor_id

    def check(self, result: int, error: int) -> None:
        if result != COMM_SUCCESS:
            raise RuntimeError(self.packet.getTxRxResult(result))
        if error:
            raise RuntimeError(self.packet.getRxPacketError(error))

    def read1(self, address: int) -> int:
        value, result, error = self.packet.read1ByteTxRx(
            self.port, self.motor_id, address
        )
        self.check(result, error)
        return value

    def read2(self, address: int) -> int:
        value, result, error = self.packet.read2ByteTxRx(
            self.port, self.motor_id, address
        )
        self.check(result, error)
        return value

    def read4(self, address: int) -> int:
        value, result, error = self.packet.read4ByteTxRx(
            self.port, self.motor_id, address
        )
        self.check(result, error)
        return value


def print_status(baudrate: int, buses: list[BusConfig]) -> int:
    packet = PacketHandler(2.0)
    failures = 0
    print(datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"))
    print(
        "ID BUS   MODE TQ  POSITION ZERO_ERR   DEG    GOAL CURR  VEL "
        "MV IP FE HW VOLT TEMP RESULT"
    )
    print(
        "-- ----- ---- -- --------- -------- ------ ------- ----- ---- "
        "-- -- -- -- ---- ---- ------"
    )

    for bus in buses:
        port = PortHandler(bus.device)
        if not port.openPort():
            for motor_id in bus.ids:
                failures += 1
                print(f"{motor_id:2d} {bus.name:5s} cannot open {bus.device}")
            continue
        try:
            if not port.setBaudRate(baudrate):
                for motor_id in bus.ids:
                    failures += 1
                    print(f"{motor_id:2d} {bus.name:5s} cannot set {baudrate}")
                continue
            for motor_id in bus.ids:
                reader = Reader(packet, port, motor_id)
                try:
                    _, result, error = packet.ping(port, motor_id)
                    reader.check(result, error)
                    mode = reader.read1(ADDR_OPERATING_MODE)
                    torque = reader.read1(ADDR_TORQUE_ENABLE)
                    hardware_error = reader.read1(ADDR_HARDWARE_ERROR)
                    goal = signed_32(reader.read4(ADDR_GOAL_POSITION))
                    moving = reader.read1(ADDR_MOVING)
                    moving_status = reader.read1(ADDR_MOVING_STATUS)
                    current = signed_16(reader.read2(ADDR_PRESENT_CURRENT))
                    velocity = signed_32(reader.read4(ADDR_PRESENT_VELOCITY))
                    position = signed_32(reader.read4(ADDR_PRESENT_POSITION))
                    voltage = reader.read1(ADDR_PRESENT_VOLTAGE) / 10
                    temperature = reader.read1(ADDR_PRESENT_TEMPERATURE)
                    zero_error = nearest_zero_error(position)
                    in_position = 1 if moving_status & 0x01 else 0
                    following_error = 1 if moving_status & 0x08 else 0
                    print(
                        f"{motor_id:2d} {bus.name:5s} {mode:4d} {torque:2d} "
                        f"{position:9d} {zero_error:8d} "
                        f"{zero_error * DEGREES_PER_COUNT:6.2f} {goal:7d} "
                        f"{current:5d} {velocity:4d} {moving:2d} "
                        f"{in_position:2d} {following_error:2d} "
                        f"{hardware_error:02X} {voltage:4.1f} "
                        f"{temperature:4d} OK"
                    )
                except RuntimeError as error:
                    failures += 1
                    print(f"{motor_id:2d} {bus.name:5s} ERROR: {error}")
        finally:
            port.closePort()
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("motor_config.json")
    )
    parser.add_argument(
        "--watch",
        type=float,
        metavar="SECONDS",
        help="repeat status output at this interval until Ctrl-C",
    )
    args = parser.parse_args()
    if args.watch is not None and args.watch <= 0:
        raise ValueError("--watch must be greater than zero")

    baudrate, buses = load_config(args.config)
    try:
        while True:
            failures = print_status(baudrate, buses)
            if args.watch is None:
                return 1 if failures else 0
            print()
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
