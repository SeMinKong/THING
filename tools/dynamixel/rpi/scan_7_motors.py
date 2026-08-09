#!/usr/bin/env python3
"""Read the configured seven XL330 motors without changing any register."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from dynamixel_sdk import COMM_SUCCESS, PacketHandler, PortHandler
except ImportError as error:
    print(
        "ERROR: dynamixel_sdk is not installed. Run: "
        "python3 -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1) from error


ADDR_OPERATING_MODE = 11
ADDR_TORQUE_ENABLE = 64
ADDR_PRESENT_POSITION = 132
ADDR_PRESENT_VOLTAGE = 144
ADDR_PRESENT_TEMPERATURE = 146


def signed_32(value: int) -> int:
    return value - (1 << 32) if value & (1 << 31) else value


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


def check(packet: PacketHandler, result: int, error: int) -> str | None:
    if result != COMM_SUCCESS:
        return packet.getTxRxResult(result)
    if error:
        return packet.getRxPacketError(error)
    return None


def read1(packet: PacketHandler, port: PortHandler, motor_id: int, address: int) -> int:
    value, result, error = packet.read1ByteTxRx(port, motor_id, address)
    message = check(packet, result, error)
    if message:
        raise RuntimeError(message)
    return value


def read4(packet: PacketHandler, port: PortHandler, motor_id: int, address: int) -> int:
    value, result, error = packet.read4ByteTxRx(port, motor_id, address)
    message = check(packet, result, error)
    if message:
        raise RuntimeError(message)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("motor_config.json")
    )
    args = parser.parse_args()

    baudrate, buses = load_config(args.config)
    packet = PacketHandler(2.0)
    print("ID BUS   MODEL MODE TQ POSITION VOLT TEMP RESULT")
    print("-- ----- ----- ---- -- -------- ---- ---- ------")

    failures = 0
    for bus in buses:
        port = PortHandler(bus.device)
        if not port.openPort():
            print(f"-- {bus.name:5s} ----- ---- -- -------- ---- ---- cannot open {bus.device}")
            failures += len(bus.ids)
            continue
        try:
            if not port.setBaudRate(baudrate):
                print(f"-- {bus.name:5s} ----- ---- -- -------- ---- ---- cannot set {baudrate}")
                failures += len(bus.ids)
                continue
            for motor_id in bus.ids:
                try:
                    model, result, error = packet.ping(port, motor_id)
                    message = check(packet, result, error)
                    if message:
                        raise RuntimeError(message)
                    mode = read1(packet, port, motor_id, ADDR_OPERATING_MODE)
                    torque = read1(packet, port, motor_id, ADDR_TORQUE_ENABLE)
                    position = signed_32(
                        read4(packet, port, motor_id, ADDR_PRESENT_POSITION)
                    )
                    voltage = read1(packet, port, motor_id, ADDR_PRESENT_VOLTAGE)
                    temperature = read1(packet, port, motor_id, ADDR_PRESENT_TEMPERATURE)
                    print(
                        f"{motor_id:2d} {bus.name:5s} {model:5d} {mode:4d} "
                        f"{torque:2d} {position:8d} {voltage / 10:4.1f} "
                        f"{temperature:4d} OK"
                    )
                except RuntimeError as error:
                    failures += 1
                    print(f"{motor_id:2d} {bus.name:5s} ----- ---- -- -------- ---- ---- {error}")
        finally:
            port.closePort()

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
