#!/usr/bin/env python3
"""Read or set Goal PWM(100) and Goal Current(102) for seven XL330 motors."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from dynamixel_sdk import COMM_SUCCESS, PacketHandler, PortHandler
except ImportError as error:
    print("ERROR: dynamixel_sdk is not installed.", file=sys.stderr)
    raise SystemExit(1) from error


ADDR_PWM_LIMIT = 36
ADDR_CURRENT_LIMIT = 38
ADDR_TORQUE_ENABLE = 64
ADDR_GOAL_PWM = 100
ADDR_GOAL_CURRENT = 102


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
    buses = [BusConfig(item["name"], item["device"], tuple(item["ids"])) for item in raw["buses"]]
    ids = [motor_id for bus in buses for motor_id in bus.ids]
    if sorted(ids) != list(range(1, 8)):
        raise ValueError("motor_config.json must contain IDs 1 through 7 exactly once")
    return int(raw["baudrate"]), buses


class Outputs:
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
        value, result, error = self.packet.read1ByteTxRx(self.ports[motor_id], motor_id, address)
        self.check(result, error, f"ID {motor_id} read")
        return value

    def read2(self, motor_id: int, address: int) -> int:
        value, result, error = self.packet.read2ByteTxRx(self.ports[motor_id], motor_id, address)
        self.check(result, error, f"ID {motor_id} read")
        return value

    def write2(self, motor_id: int, address: int, value: int) -> None:
        result, error = self.packet.write2ByteTxRx(self.ports[motor_id], motor_id, address, value)
        self.check(result, error, f"ID {motor_id} write")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("motor_config.json"))
    parser.add_argument("--pwm", type=int, default=885)
    parser.add_argument("--current", type=int, default=1470)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.pwm < 0 or args.current < 0:
        raise ValueError("PWM and current must be non-negative")

    baudrate, buses = load_config(args.config)
    outputs = Outputs(baudrate, buses)
    try:
        limits: dict[int, tuple[int, int]] = {}
        for motor_id in sorted(outputs.ports):
            torque = outputs.read1(motor_id, ADDR_TORQUE_ENABLE)
            pwm_limit = outputs.read2(motor_id, ADDR_PWM_LIMIT)
            current_limit = outputs.read2(motor_id, ADDR_CURRENT_LIMIT)
            goal_pwm = outputs.read2(motor_id, ADDR_GOAL_PWM)
            goal_current = outputs.read2(motor_id, ADDR_GOAL_CURRENT)
            limits[motor_id] = (pwm_limit, current_limit)
            print(
                f"ID {motor_id}: torque={torque}, PWM {goal_pwm}/{pwm_limit}, "
                f"Current {goal_current}/{current_limit} mA"
            )

        if not args.apply:
            return 0
        invalid = [
            str(motor_id)
            for motor_id, (pwm_limit, current_limit) in limits.items()
            if args.pwm > pwm_limit or args.current > current_limit
        ]
        if invalid:
            raise RuntimeError("requested output exceeds limit on IDs: " + ", ".join(invalid))

        for motor_id in sorted(outputs.ports):
            outputs.write2(motor_id, ADDR_GOAL_PWM, args.pwm)
            outputs.write2(motor_id, ADDR_GOAL_CURRENT, args.current)
            pwm = outputs.read2(motor_id, ADDR_GOAL_PWM)
            current = outputs.read2(motor_id, ADDR_GOAL_CURRENT)
            if (pwm, current) != (args.pwm, args.current):
                raise RuntimeError(f"ID {motor_id}: verification failed")
            print(f"ID {motor_id}: Goal PWM={pwm}, Goal Current={current} mA")
    finally:
        outputs.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
