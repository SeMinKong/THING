#!/usr/bin/env python3
"""Move the seven configured XL330 motors, one at a time, to physical 2048.

For XL330 Extended Position / Current-based Position modes, this means the
nearest whole-turn position whose value modulo 4096 is 2048.  For normal
Position Control Mode, the target is literal 2048.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
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


ADDR_OPERATING_MODE = 11
ADDR_TORQUE_ENABLE = 64
ADDR_PROFILE_ACCELERATION = 108
ADDR_PROFILE_VELOCITY = 112
ADDR_GOAL_POSITION = 116
ADDR_PRESENT_POSITION = 132
BASE_POSITION = 2048
POSITION_TURN = 4096


def signed_32(value: int) -> int:
    return value - (1 << 32) if value & (1 << 31) else value


def unsigned_32(value: int) -> int:
    return value & 0xFFFFFFFF


def base_target(present: int, mode: int) -> int:
    """Return the closest coordinate that represents physical 2048."""
    if mode == 3:
        return BASE_POSITION
    if mode in (4, 5):
        turns = round((present - BASE_POSITION) / POSITION_TURN)
        return BASE_POSITION + turns * POSITION_TURN
    raise RuntimeError(
        f"unsupported operating mode {mode}; use Position Control (3), "
        "Extended Position (4), or Current-based Position (5)"
    )


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


class HomeController:
    def __init__(self, baudrate: int, buses: list[BusConfig]) -> None:
        self.packet = PacketHandler(2.0)
        self.ports: dict[int, PortHandler] = {}
        self.modes: dict[int, int] = {}
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
                self.modes[motor_id] = self.read1(motor_id, ADDR_OPERATING_MODE)

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

    def read_position(self, motor_id: int) -> int:
        value, result, error = self.packet.read4ByteTxRx(
            self.ports[motor_id], motor_id, ADDR_PRESENT_POSITION
        )
        self.check(result, error, f"ID {motor_id} read position")
        return signed_32(value)

    def write1(self, motor_id: int, address: int, value: int) -> None:
        result, error = self.packet.write1ByteTxRx(
            self.ports[motor_id], motor_id, address, value
        )
        self.check(result, error, f"ID {motor_id} write")

    def write4(self, motor_id: int, address: int, value: int) -> None:
        result, error = self.packet.write4ByteTxRx(
            self.ports[motor_id], motor_id, address, unsigned_32(value)
        )
        self.check(result, error, f"ID {motor_id} write")

    def torque_off_all(self) -> list[str]:
        errors: list[str] = []
        for motor_id in sorted(self.ports):
            try:
                self.write1(motor_id, ADDR_TORQUE_ENABLE, 0)
            except RuntimeError as error:
                errors.append(str(error))
        return errors

    def home_one(
        self,
        motor_id: int,
        velocity: int,
        acceleration: int,
        tolerance: int,
        timeout: float,
        keep_torque: bool,
    ) -> tuple[int, int]:
        present = self.read_position(motor_id)
        target = base_target(present, self.modes[motor_id])
        print(f"ID {motor_id}: {present} -> {target}")

        # Avoid torque-on jumping to a previous goal position.
        self.write4(motor_id, ADDR_PROFILE_ACCELERATION, acceleration)
        self.write4(motor_id, ADDR_PROFILE_VELOCITY, velocity)
        self.write4(motor_id, ADDR_GOAL_POSITION, present)
        self.write1(motor_id, ADDR_TORQUE_ENABLE, 1)
        self.write4(motor_id, ADDR_GOAL_POSITION, target)
        try:
            deadline = time.monotonic() + timeout
            while True:
                current = self.read_position(motor_id)
                if abs(target - current) <= tolerance:
                    print(f"ID {motor_id}: reached {current}")
                    return current, target
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"ID {motor_id}: timeout at {current}, target={target}"
                    )
                time.sleep(0.1)
        finally:
            if not keep_torque:
                self.write1(motor_id, ADDR_TORQUE_ENABLE, 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("motor_config.json")
    )
    parser.add_argument("--velocity", type=int, default=10)
    parser.add_argument("--acceleration", type=int, default=1)
    parser.add_argument("--tolerance", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--keep-torque", action="store_true")
    args = parser.parse_args()
    if args.velocity < 0 or args.acceleration < 0:
        raise ValueError("velocity and acceleration must be non-negative")
    if args.tolerance < 0 or args.timeout <= 0:
        raise ValueError("tolerance must be non-negative and timeout positive")

    baudrate, buses = load_config(args.config)
    controller = HomeController(baudrate, buses)
    try:
        for motor_id in range(1, 8):
            controller.home_one(
                motor_id,
                args.velocity,
                args.acceleration,
                args.tolerance,
                args.timeout,
                args.keep_torque,
            )
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        if not args.keep_torque:
            errors = controller.torque_off_all()
            if errors:
                print("Torque-off errors: " + "; ".join(errors), file=sys.stderr)
        controller.close()
    print("All seven motors are at physical base (mod 4096 = 2048).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
