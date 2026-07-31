#!/usr/bin/env python3
"""Small command-line controller for one ROBOTIS XL330-M288 motor.

The program does not modify EEPROM settings such as ID, baud rate, operating
mode, or position limits.  It uses DYNAMIXEL Protocol 2.0 at the requested
serial port and baud rate.
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    from dynamixel_sdk import COMM_SUCCESS, PacketHandler, PortHandler
except ImportError as error:
    COMM_SUCCESS = -1
    PacketHandler = None
    PortHandler = None
    SDK_IMPORT_ERROR: ImportError | None = error
else:
    SDK_IMPORT_ERROR = None


PROTOCOL_VERSION = 2.0

# XL330-M288 control-table addresses (Protocol 2.0).
ADDR_OPERATING_MODE = 11
ADDR_TORQUE_ENABLE = 64
ADDR_PROFILE_ACCELERATION = 108
ADDR_PROFILE_VELOCITY = 112
ADDR_GOAL_POSITION = 116
ADDR_PRESENT_POSITION = 132
ADDR_PRESENT_VOLTAGE = 144
ADDR_PRESENT_TEMPERATURE = 146

TORQUE_OFF = 0
TORQUE_ON = 1


class MotorError(RuntimeError):
    """A DYNAMIXEL transport or device error."""


def signed_32(value: int) -> int:
    """Interpret a DYNAMIXEL 4-byte value as a signed 32-bit integer."""
    return value - (1 << 32) if value & (1 << 31) else value


def unsigned_32(value: int) -> int:
    """Encode a signed Python integer for a DYNAMIXEL 4-byte register."""
    return value & 0xFFFFFFFF


class Motor:
    def __init__(self, device: str, baud: int, motor_id: int) -> None:
        if SDK_IMPORT_ERROR is not None:
            raise MotorError(
                "dynamixel_sdk is not installed. Run: "
                "python3 -m pip install -r requirements.txt"
            ) from SDK_IMPORT_ERROR
        self.motor_id = motor_id
        self.port = PortHandler(device)
        self.packet = PacketHandler(PROTOCOL_VERSION)
        if not self.port.openPort():
            raise MotorError(f"cannot open serial port: {device}")
        if not self.port.setBaudRate(baud):
            self.port.closePort()
            raise MotorError(f"cannot set baud rate {baud} on {device}")

    def close(self) -> None:
        self.port.closePort()

    def _check(self, result: int, error: int, action: str) -> None:
        if result != COMM_SUCCESS:
            raise MotorError(f"{action}: {self.packet.getTxRxResult(result)}")
        if error:
            raise MotorError(f"{action}: {self.packet.getRxPacketError(error)}")

    def ping(self) -> int:
        model, result, error = self.packet.ping(self.port, self.motor_id)
        self._check(result, error, "ping")
        return model

    def read1(self, address: int, name: str) -> int:
        value, result, error = self.packet.read1ByteTxRx(
            self.port, self.motor_id, address
        )
        self._check(result, error, f"read {name}")
        return value

    def read4(self, address: int, name: str) -> int:
        value, result, error = self.packet.read4ByteTxRx(
            self.port, self.motor_id, address
        )
        self._check(result, error, f"read {name}")
        return value

    def write1(self, address: int, value: int, name: str) -> None:
        result, error = self.packet.write1ByteTxRx(
            self.port, self.motor_id, address, value
        )
        self._check(result, error, f"write {name}")

    def write4(self, address: int, value: int, name: str) -> None:
        result, error = self.packet.write4ByteTxRx(
            self.port, self.motor_id, address, unsigned_32(value)
        )
        self._check(result, error, f"write {name}")

    def status(self) -> dict[str, int]:
        return {
            "model": self.ping(),
            "operating_mode": self.read1(ADDR_OPERATING_MODE, "operating mode"),
            "torque": self.read1(ADDR_TORQUE_ENABLE, "torque enable"),
            "position": signed_32(
                self.read4(ADDR_PRESENT_POSITION, "present position")
            ),
            "voltage_raw": self.read1(ADDR_PRESENT_VOLTAGE, "present voltage"),
            "temperature_c": self.read1(
                ADDR_PRESENT_TEMPERATURE, "present temperature"
            ),
        }

    def torque(self, enabled: bool) -> None:
        self.write1(
            ADDR_TORQUE_ENABLE,
            TORQUE_ON if enabled else TORQUE_OFF,
            "torque enable",
        )

    def move(
        self,
        goal: int,
        velocity: int,
        acceleration: int,
        tolerance: int,
        timeout: float,
        keep_torque: bool,
    ) -> int:
        """Move once, returning the final present position.

        Operating mode is deliberately not changed.  Check it with ``status``
        first.  Mode 3 accepts positions 0..4095; Mode 5 accepts extended
        multi-turn positions.
        """
        self.write4(ADDR_PROFILE_ACCELERATION, acceleration, "profile acceleration")
        self.write4(ADDR_PROFILE_VELOCITY, velocity, "profile velocity")
        self.write4(ADDR_GOAL_POSITION, goal, "goal position")
        self.torque(True)

        try:
            deadline = time.monotonic() + timeout
            while True:
                position = signed_32(
                    self.read4(ADDR_PRESENT_POSITION, "present position")
                )
                error = goal - position
                print(f"position={position} goal={goal} error={error:+d}")
                if abs(error) <= tolerance:
                    return position
                if time.monotonic() >= deadline:
                    raise MotorError(
                        f"move timeout: position={position}, goal={goal}, "
                        f"error={error:+d}"
                    )
                time.sleep(0.1)
        finally:
            if not keep_torque:
                self.torque(False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True, help="e.g. /dev/ttyUSB0")
    parser.add_argument("--id", required=True, type=int, dest="motor_id")
    parser.add_argument("--baud", type=int, default=57600)
    parser.add_argument(
        "action", choices=("status", "torque-on", "torque-off", "move"), nargs="?", default="status"
    )
    parser.add_argument("--position", type=int, help="goal position raw value")
    parser.add_argument("--velocity", type=int, default=10, help="profile velocity raw")
    parser.add_argument("--acceleration", type=int, default=1, help="profile acceleration raw")
    parser.add_argument("--tolerance", type=int, default=8, help="arrival tolerance raw")
    parser.add_argument("--timeout", type=float, default=8.0, help="move timeout seconds")
    parser.add_argument("--keep-torque", action="store_true")
    parser.add_argument(
        "--arm",
        action="store_true",
        help="required for move; prevents an accidental motion command",
    )
    return parser


def print_status(status: dict[str, int], motor_id: int) -> None:
    print(f"ID: {motor_id}")
    print(f"Model: {status['model']}")
    print(f"Operating mode: {status['operating_mode']}")
    print(f"Torque: {'ON' if status['torque'] else 'OFF'}")
    print(f"Present position: {status['position']}")
    print(f"Voltage: {status['voltage_raw'] / 10:.1f} V")
    print(f"Temperature: {status['temperature_c']} C")


def main() -> int:
    args = build_parser().parse_args()
    if not 0 <= args.motor_id <= 252:
        raise MotorError("ID must be in 0..252")
    if args.action == "move":
        if args.position is None:
            raise MotorError("move requires --position")
        if not args.arm:
            raise MotorError("move requires --arm")
        if args.velocity < 0 or args.acceleration < 0:
            raise MotorError("velocity and acceleration must be non-negative")
        if args.tolerance < 0 or args.timeout <= 0:
            raise MotorError("tolerance must be non-negative and timeout positive")

    motor = Motor(args.device, args.baud, args.motor_id)
    try:
        if args.action == "status":
            print_status(motor.status(), args.motor_id)
        elif args.action == "torque-on":
            motor.torque(True)
            print(f"ID {args.motor_id}: torque ON")
        elif args.action == "torque-off":
            motor.torque(False)
            print(f"ID {args.motor_id}: torque OFF")
        else:
            final_position = motor.move(
                args.position,
                args.velocity,
                args.acceleration,
                args.tolerance,
                args.timeout,
                args.keep_torque,
            )
            print(f"ID {args.motor_id}: reached {final_position}")
    finally:
        motor.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MotorError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
