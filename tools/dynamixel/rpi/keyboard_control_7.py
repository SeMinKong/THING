#!/usr/bin/env python3
"""Keyboard controller for seven configured XL330 motors.

Press 1..7 to select a motor and use arrow keys to move its goal position.
No operating mode or EEPROM setting is changed by this program.
"""

from __future__ import annotations

import argparse
import curses
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
ADDR_PROFILE_ACCELERATION = 108
ADDR_PROFILE_VELOCITY = 112
ADDR_GOAL_PWM = 100
ADDR_GOAL_CURRENT = 102
ADDR_GOAL_POSITION = 116
ADDR_PRESENT_POSITION = 132


def signed_32(value: int) -> int:
    return value - (1 << 32) if value & (1 << 31) else value


def unsigned_32(value: int) -> int:
    return value & 0xFFFFFFFF


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


class Controller:
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

    def _check(self, result: int, error: int, action: str) -> None:
        if result != COMM_SUCCESS:
            raise RuntimeError(f"{action}: {self.packet.getTxRxResult(result)}")
        if error:
            raise RuntimeError(f"{action}: {self.packet.getRxPacketError(error)}")

    def read1(self, motor_id: int, address: int) -> int:
        value, result, error = self.packet.read1ByteTxRx(
            self.ports[motor_id], motor_id, address
        )
        self._check(result, error, f"ID {motor_id} read")
        return value

    def read_position(self, motor_id: int) -> int:
        value, result, error = self.packet.read4ByteTxRx(
            self.ports[motor_id], motor_id, ADDR_PRESENT_POSITION
        )
        self._check(result, error, f"ID {motor_id} read position")
        return signed_32(value)

    def write1(self, motor_id: int, address: int, value: int) -> None:
        result, error = self.packet.write1ByteTxRx(
            self.ports[motor_id], motor_id, address, value
        )
        self._check(result, error, f"ID {motor_id} write")

    def write2(self, motor_id: int, address: int, value: int) -> None:
        result, error = self.packet.write2ByteTxRx(
            self.ports[motor_id], motor_id, address, value
        )
        self._check(result, error, f"ID {motor_id} write")

    def write4(self, motor_id: int, address: int, value: int) -> None:
        result, error = self.packet.write4ByteTxRx(
            self.ports[motor_id], motor_id, address, unsigned_32(value)
        )
        self._check(result, error, f"ID {motor_id} write")

    def torque(self, motor_id: int, enabled: bool) -> None:
        self.write1(motor_id, ADDR_TORQUE_ENABLE, 1 if enabled else 0)

    def toggle_torque(self, motor_id: int) -> bool:
        enabled = self.read1(motor_id, ADDR_TORQUE_ENABLE) == 0
        self.torque(motor_id, enabled)
        return enabled

    def move(
        self,
        motor_id: int,
        delta: int,
        velocity: int,
        acceleration: int,
        goal_pwm: int,
        goal_current: int,
    ) -> int:
        present = self.read_position(motor_id)
        goal = present + delta
        # In normal Position Control Mode, Goal Position is limited to one turn.
        if self.modes[motor_id] == 3:
            goal = max(0, min(4095, goal))
        # Send the current position before torque-on to avoid jumping to a stale goal.
        self.write4(motor_id, ADDR_PROFILE_ACCELERATION, acceleration)
        self.write4(motor_id, ADDR_PROFILE_VELOCITY, velocity)
        self.write2(motor_id, ADDR_GOAL_PWM, goal_pwm)
        self.write2(motor_id, ADDR_GOAL_CURRENT, goal_current)
        self.write4(motor_id, ADDR_GOAL_POSITION, present)
        self.torque(motor_id, True)
        self.write4(motor_id, ADDR_GOAL_POSITION, goal)
        return goal

    def torque_states(self) -> dict[int, int]:
        return {motor_id: self.read1(motor_id, ADDR_TORQUE_ENABLE) for motor_id in self.ports}

    def torque_off_all(self) -> list[str]:
        errors: list[str] = []
        for motor_id in sorted(self.ports):
            try:
                self.torque(motor_id, False)
            except RuntimeError as error:
                errors.append(str(error))
        return errors


def draw(screen: curses.window, selected: int, step: int, controller: Controller, message: str) -> None:
    screen.erase()
    screen.addstr(0, 0, "XL330 7-motor keyboard control")
    screen.addstr(2, 0, "1-7: select   Left/Right: move one step   Up/Down: move four steps")
    screen.addstr(3, 0, "t: toggle torque   Space: selected stop   s: ALL STOP   q: quit")
    screen.addstr(4, 0, "r: refresh   [ / ]: step down/up")
    screen.addstr(5, 0, f"Selected: ID {selected}   step: {step} raw")
    screen.addstr(7, 0, "ID  MODE  TORQUE  POSITION")
    try:
        torques = controller.torque_states()
        for row, motor_id in enumerate(sorted(controller.ports), start=8):
            position = controller.read_position(motor_id)
            marker = ">" if motor_id == selected else " "
            torque = "ON " if torques[motor_id] else "OFF"
            screen.addstr(
                row,
                0,
                f"{marker} {motor_id}   {controller.modes[motor_id]:4d}  {torque:6s}  {position:8d}",
            )
    except RuntimeError as error:
        message = str(error)
    screen.addstr(17, 0, message[: max(1, curses.COLS - 1)])
    screen.refresh()


def run_ui(
    screen: curses.window,
    controller: Controller,
    velocity: int,
    acceleration: int,
    goal_pwm: int,
    goal_current: int,
) -> None:
    curses.curs_set(0)
    screen.keypad(True)
    selected = 1
    step = 32
    message = "Select ID, then use arrow keys. q exits and turns all torque off."
    while True:
        draw(screen, selected, step, controller, message)
        key = screen.getch()
        message = ""
        try:
            if key in (ord("q"), ord("Q")):
                return
            if ord("1") <= key <= ord("7"):
                selected = key - ord("0")
            elif key == curses.KEY_LEFT:
                goal = controller.move(selected, -step, velocity, acceleration, goal_pwm, goal_current)
                message = f"ID {selected} goal: {goal}"
            elif key == curses.KEY_RIGHT:
                goal = controller.move(selected, step, velocity, acceleration, goal_pwm, goal_current)
                message = f"ID {selected} goal: {goal}"
            elif key == curses.KEY_DOWN:
                goal = controller.move(selected, -(step * 4), velocity, acceleration, goal_pwm, goal_current)
                message = f"ID {selected} goal: {goal}"
            elif key == curses.KEY_UP:
                goal = controller.move(selected, step * 4, velocity, acceleration, goal_pwm, goal_current)
                message = f"ID {selected} goal: {goal}"
            elif key in (ord("["), ord("{")):
                step = max(1, step // 2)
                message = f"step: {step} raw"
            elif key in (ord("]"), ord("}")):
                step = min(1024, step * 2)
                message = f"step: {step} raw"
            elif key in (ord("t"), ord("T")):
                enabled = controller.toggle_torque(selected)
                message = f"ID {selected} torque {'ON' if enabled else 'OFF'}"
            elif key == ord(" "):
                controller.torque(selected, False)
                message = f"ID {selected} STOP (torque OFF)"
            elif key in (ord("s"), ord("S")):
                errors = controller.torque_off_all()
                message = "ALL STOP: every motor torque OFF"
                if errors:
                    message += " | " + "; ".join(errors)
            elif key in (ord("r"), ord("R")):
                message = "refreshed"
        except RuntimeError as error:
            message = f"ERROR: {error}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("motor_config.json")
    )
    parser.add_argument("--velocity", type=int, default=300)
    parser.add_argument("--acceleration", type=int, default=1500)
    parser.add_argument("--goal-pwm", type=int, default=885)
    parser.add_argument("--goal-current", type=int, default=1470)
    parser.add_argument(
        "--leave-torque-on",
        action="store_true",
        help="do not issue torque-off commands when the program exits",
    )
    args = parser.parse_args()
    if args.velocity < 0 or args.acceleration < 0:
        raise ValueError("velocity and acceleration must be non-negative")
    if not 0 <= args.goal_pwm <= 885:
        raise ValueError("goal PWM must be 0..885")
    if not 0 <= args.goal_current <= 1470:
        raise ValueError("goal current must be 0..1470 mA")

    baudrate, buses = load_config(args.config)
    controller = Controller(baudrate, buses)
    try:
        curses.wrapper(
            run_ui,
            controller,
            args.velocity,
            args.acceleration,
            args.goal_pwm,
            args.goal_current,
        )
    finally:
        if not args.leave_torque_on:
            errors = controller.torque_off_all()
            controller.close()
            if errors:
                print("Torque-off errors: " + "; ".join(errors), file=sys.stderr)
        else:
            controller.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
