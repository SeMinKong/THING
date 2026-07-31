#!/usr/bin/env python3
"""Read or set Current Limit(38) for the seven configured XL330 motors."""

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
        "./.venv/bin/python -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1) from error


ADDR_CURRENT_LIMIT = 38
ADDR_TORQUE_ENABLE = 64
MAX_CURRENT_LIMIT_MA = 1750


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


class Limits:
    def __init__(self, baudrate: int, buses: list[BusConfig]) -> None:
        self.packet = PacketHandler(2.0)
        self.ports: dict[int, PortHandler] = {}
        for bus in buses:
            port = PortHandler(bus.device)
            if not port.openPort():
                self.close()
                raise RuntimeError(f"cannot open {bus.device}")
            if not port.setBaudRate(baudrate):
                port.closePort()
                self.close()
                raise RuntimeError(f"cannot set {baudrate} on {bus.device}")
            for motor_id in bus.ids:
                self.ports[motor_id] = port

    def close(self) -> None:
        closed: set[int] = set()
        for port in self.ports.values():
            if id(port) not in closed:
                port.closePort()
                closed.add(id(port))

    def check(self, result: int, error: int, action: str) -> None:
        if result != COMM_SUCCESS:
            raise RuntimeError(f"{action}: {self.packet.getTxRxResult(result)}")
        if error:
            raise RuntimeError(f"{action}: {self.packet.getRxPacketError(error)}")

    def read1(self, motor_id: int, address: int) -> int:
        value, result, error = self.packet.read1ByteTxRx(
            self.ports[motor_id], motor_id, address
        )
        self.check(result, error, f"ID {motor_id} read")
        return value

    def read2(self, motor_id: int, address: int) -> int:
        value, result, error = self.packet.read2ByteTxRx(
            self.ports[motor_id], motor_id, address
        )
        self.check(result, error, f"ID {motor_id} read")
        return value

    def write2(self, motor_id: int, address: int, value: int) -> None:
        result, error = self.packet.write2ByteTxRx(
            self.ports[motor_id], motor_id, address, value
        )
        self.check(result, error, f"ID {motor_id} write")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("motor_config.json")
    )
    parser.add_argument("--limit", type=int, default=1000, help="0..1750 mA")
    parser.add_argument(
        "--apply", action="store_true", help="write the requested limit; otherwise read only"
    )
    args = parser.parse_args()
    if not 0 <= args.limit <= MAX_CURRENT_LIMIT_MA:
        raise ValueError(f"limit must be 0..{MAX_CURRENT_LIMIT_MA} mA")

    baudrate, buses = load_config(args.config)
    limits = Limits(baudrate, buses)
    try:
        before: dict[int, tuple[int, int]] = {}
        for motor_id in sorted(limits.ports):
            torque = limits.read1(motor_id, ADDR_TORQUE_ENABLE)
            current_limit = limits.read2(motor_id, ADDR_CURRENT_LIMIT)
            before[motor_id] = (torque, current_limit)
            print(f"ID {motor_id}: torque={torque}, current_limit={current_limit} mA")

        if not args.apply:
            return 0

        active = [str(motor_id) for motor_id, (torque, _) in before.items() if torque]
        if active:
            raise RuntimeError(
                "Current Limit is EEPROM. Torque must be OFF first; active IDs: "
                + ", ".join(active)
            )

        for motor_id in sorted(limits.ports):
            limits.write2(motor_id, ADDR_CURRENT_LIMIT, args.limit)
            verified = limits.read2(motor_id, ADDR_CURRENT_LIMIT)
            if verified != args.limit:
                raise RuntimeError(
                    f"ID {motor_id}: verification failed ({verified} != {args.limit})"
                )
            print(f"ID {motor_id}: Current Limit set to {verified} mA")
    finally:
        limits.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
