#!/usr/bin/env python3
"""Run a calibrated index-to-little-to-index tendon wave on XL330 motors.

The selected motors first move to their calibrated open positions, then wave
between the calibrated open and closed positions. Commands are sent with one
GroupSyncWrite packet per physical bus. Ctrl+C or SIGTERM returns to the
calibrated open pose and then disables torque on all configured motors.
SIGKILL and sudden power loss cannot run the shutdown sequence.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from dynamixel_sdk import (
        COMM_SUCCESS,
        GroupSyncWrite,
        PacketHandler,
        PortHandler,
    )
except ImportError as error:
    print(
        "ERROR: dynamixel_sdk is not installed. Run: "
        "./.venv/bin/python -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1) from error


ADDR_OPERATING_MODE = 11
ADDR_CURRENT_LIMIT = 38
ADDR_TORQUE_ENABLE = 64
ADDR_GOAL_PWM = 100
ADDR_GOAL_CURRENT = 102
ADDR_PROFILE_ACCELERATION = 108
ADDR_PROFILE_VELOCITY = 112
ADDR_GOAL_POSITION = 116
ADDR_PRESENT_POSITION = 132

CURRENT_BASED_POSITION_MODE = 5
MIN_EXTENDED_POSITION = -1_048_575
MAX_EXTENDED_POSITION = 1_048_575


def signed_32(value: int) -> int:
    return value - (1 << 32) if value & (1 << 31) else value


def u32_bytes(value: int) -> list[int]:
    encoded = value & 0xFFFFFFFF
    return [
        encoded & 0xFF,
        (encoded >> 8) & 0xFF,
        (encoded >> 16) & 0xFF,
        (encoded >> 24) & 0xFF,
    ]


def smootherstep(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * value * (
        value * (value * 6.0 - 15.0) + 10.0
    )


def finger_profile(
    local_time: float,
    bend_time: float,
    hold_time: float,
    release_time: float,
) -> float:
    if local_time <= 0:
        return 0.0
    if local_time < bend_time:
        return smootherstep(local_time / bend_time)
    if local_time < bend_time + hold_time:
        return 1.0
    release_elapsed = local_time - bend_time - hold_time
    if release_elapsed < release_time:
        return 1.0 - smootherstep(
            release_elapsed / release_time
        )
    return 0.0


def parse_csv_ints(text: str, label: str) -> list[int]:
    try:
        values = [int(item.strip()) for item in text.split(",")]
    except ValueError as error:
        raise ValueError(f"{label} must be comma-separated integers") from error
    if not values:
        raise ValueError(f"{label} cannot be empty")
    return values


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
    configured_ids = [
        motor_id for bus in buses for motor_id in bus.ids
    ]
    if sorted(configured_ids) != list(range(1, 8)):
        raise ValueError(
            "motor_config.json must contain IDs 1 through 7 exactly once"
        )
    return int(raw["baudrate"]), buses


class ProcessLock:
    def __init__(self, directory: Path) -> None:
        self.path = directory / ".wave_control.lock"
        self.stream = None

    def __enter__(self) -> "ProcessLock":
        self.stream = self.path.open("w", encoding="utf-8")
        try:
            fcntl.flock(
                self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
            )
        except BlockingIOError as error:
            self.stream.close()
            raise RuntimeError(
                "another wave_control_7.py process is already running"
            ) from error
        self.stream.write(f"{Path('/proc/self').resolve().name}\n")
        self.stream.flush()
        return self

    def __exit__(self, *_: object) -> None:
        if self.stream is not None:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()


class Hardware:
    def __init__(
        self, baudrate: int, buses: list[BusConfig]
    ) -> None:
        self.packet = PacketHandler(2.0)
        self.buses = {bus.name: bus for bus in buses}
        self.id_to_bus: dict[int, str] = {}
        self.ports: dict[str, PortHandler] = {}
        self.writers: dict[str, GroupSyncWrite] = {}
        self.opened_buses: list[str] = []

        try:
            for bus in buses:
                port = PortHandler(bus.device)
                if not port.openPort():
                    raise RuntimeError(f"cannot open {bus.device}")
                self.opened_buses.append(bus.name)
                if not port.setBaudRate(baudrate):
                    raise RuntimeError(
                        f"cannot set {baudrate} on {bus.device}"
                    )
                self.ports[bus.name] = port
                self.writers[bus.name] = GroupSyncWrite(
                    port, self.packet, ADDR_GOAL_POSITION, 4
                )
                for motor_id in bus.ids:
                    self.id_to_bus[motor_id] = bus.name
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        for bus_name in self.opened_buses:
            port = self.ports.get(bus_name)
            if port is not None:
                port.closePort()
        self.opened_buses.clear()

    def port_for(self, motor_id: int) -> PortHandler:
        return self.ports[self.id_to_bus[motor_id]]

    def check(
        self, result: int, error: int, action: str
    ) -> None:
        if result != COMM_SUCCESS:
            raise RuntimeError(
                f"{action}: {self.packet.getTxRxResult(result)}"
            )
        if error:
            raise RuntimeError(
                f"{action}: {self.packet.getRxPacketError(error)}"
            )

    def read1(self, motor_id: int, address: int) -> int:
        value, result, error = self.packet.read1ByteTxRx(
            self.port_for(motor_id), motor_id, address
        )
        self.check(result, error, f"ID {motor_id} read {address}")
        return value

    def read2(self, motor_id: int, address: int) -> int:
        value, result, error = self.packet.read2ByteTxRx(
            self.port_for(motor_id), motor_id, address
        )
        self.check(result, error, f"ID {motor_id} read {address}")
        return value

    def read_position(self, motor_id: int) -> int:
        value, result, error = self.packet.read4ByteTxRx(
            self.port_for(motor_id), motor_id, ADDR_PRESENT_POSITION
        )
        self.check(
            result, error, f"ID {motor_id} read present position"
        )
        return signed_32(value)

    def write1(
        self, motor_id: int, address: int, value: int
    ) -> None:
        result, error = self.packet.write1ByteTxRx(
            self.port_for(motor_id), motor_id, address, value
        )
        self.check(result, error, f"ID {motor_id} write {address}")

    def write2(
        self, motor_id: int, address: int, value: int
    ) -> None:
        result, error = self.packet.write2ByteTxRx(
            self.port_for(motor_id), motor_id, address, value & 0xFFFF
        )
        self.check(result, error, f"ID {motor_id} write {address}")

    def write4(
        self, motor_id: int, address: int, value: int
    ) -> None:
        result, error = self.packet.write4ByteTxRx(
            self.port_for(motor_id),
            motor_id,
            address,
            value & 0xFFFFFFFF,
        )
        self.check(result, error, f"ID {motor_id} write {address}")

    def torque_off_all(self) -> list[str]:
        errors: list[str] = []
        for motor_id in sorted(self.id_to_bus):
            try:
                self.write1(motor_id, ADDR_TORQUE_ENABLE, 0)
            except RuntimeError as error:
                errors.append(str(error))
        return errors

    def prepare(
        self,
        motor_ids: list[int],
        velocity: int,
        acceleration: int,
        goal_pwm: int,
        goal_current: int,
    ) -> dict[int, int]:
        starts: dict[int, int] = {}
        for motor_id in motor_ids:
            mode = self.read1(motor_id, ADDR_OPERATING_MODE)
            if mode != CURRENT_BASED_POSITION_MODE:
                raise RuntimeError(
                    f"ID {motor_id}: mode {mode}, expected mode 5"
                )
            current_limit = self.read2(
                motor_id, ADDR_CURRENT_LIMIT
            )
            if goal_current > current_limit:
                raise RuntimeError(
                    f"ID {motor_id}: requested {goal_current} mA "
                    f"exceeds Current Limit {current_limit} mA"
                )
            starts[motor_id] = self.read_position(motor_id)

        for motor_id in motor_ids:
            self.write4(
                motor_id, ADDR_PROFILE_ACCELERATION, acceleration
            )
            self.write4(
                motor_id, ADDR_PROFILE_VELOCITY, velocity
            )
            self.write2(motor_id, ADDR_GOAL_PWM, goal_pwm)
            self.write2(
                motor_id, ADDR_GOAL_CURRENT, goal_current
            )
            self.write4(
                motor_id, ADDR_GOAL_POSITION, starts[motor_id]
            )

        for motor_id in motor_ids:
            self.write1(motor_id, ADDR_TORQUE_ENABLE, 1)
        return starts

    def command_positions(self, goals: dict[int, int]) -> None:
        goals_by_bus: dict[str, list[tuple[int, int]]] = {}
        for motor_id, goal in goals.items():
            if not MIN_EXTENDED_POSITION <= goal <= MAX_EXTENDED_POSITION:
                raise RuntimeError(
                    f"ID {motor_id}: goal {goal} outside mode 5 range"
                )
            bus_name = self.id_to_bus[motor_id]
            goals_by_bus.setdefault(bus_name, []).append(
                (motor_id, goal)
            )

        for bus_name, items in goals_by_bus.items():
            writer = self.writers[bus_name]
            try:
                for motor_id, goal in items:
                    if not writer.addParam(
                        motor_id, u32_bytes(goal)
                    ):
                        raise RuntimeError(
                            f"ID {motor_id}: cannot add SyncWrite goal"
                        )
                result = writer.txPacket()
                if result != COMM_SUCCESS:
                    raise RuntimeError(
                        f"{bus_name} GroupSyncWrite: "
                        f"{self.packet.getTxRxResult(result)}"
                    )
            finally:
                writer.clearParam()


def interpolate_positions(
    starts: dict[int, int],
    targets: dict[int, int],
    phase: float,
) -> dict[int, int]:
    eased = smootherstep(phase)
    return {
        motor_id: round(
            starts[motor_id]
            + (targets[motor_id] - starts[motor_id]) * eased
        )
        for motor_id in starts
    }


def return_to_start(
    hardware: Hardware,
    motor_ids: list[int],
    starts: dict[int, int],
    last_goals: dict[int, int],
    duration: float,
    rate_hz: float,
) -> None:
    if duration <= 0:
        hardware.command_positions(starts)
        return
    steps = max(1, math.ceil(duration * rate_hz))
    begin = time.monotonic()
    for step in range(1, steps + 1):
        goals = interpolate_positions(
            last_goals, starts, step / steps
        )
        hardware.command_positions(goals)
        deadline = begin + step / rate_hz
        time.sleep(max(0.0, deadline - time.monotonic()))


def print_dry_run(
    motor_ids: list[int],
    open_positions: list[int],
    closed_positions: list[int],
    bend_time: float,
    hold_time: float,
    release_time: float,
    finger_delay: float,
    strength: float,
) -> None:
    print("DRY RUN: no serial port will be opened")
    print(f"IDs: {motor_ids}")
    print(f"open positions: {open_positions}")
    print(f"closed positions: {closed_positions}")
    print(
        "spans: "
        f"{[closed - opened for opened, closed in zip(open_positions, closed_positions)]}"
    )
    action_duration = bend_time + hold_time + release_time
    pass_duration = (
        (len(motor_ids) - 1) * finger_delay + action_duration
    )
    print(
        f"per finger: bend={bend_time:.2f}s, "
        f"hold={hold_time:.2f}s, release={release_time:.2f}s"
    )
    print(
        f"finger delay={finger_delay:.2f}s, "
        f"one pass={pass_duration:.2f}s, "
        f"one full cycle={2 * pass_duration:.2f}s"
    )
    print("direction ID start hold_start hold_end end peak_goal")
    for label, order, pass_offset in [
        ("forward", list(range(len(motor_ids))), 0.0),
        (
            "reverse",
            list(reversed(range(len(motor_ids)))),
            pass_duration,
        ),
    ]:
        for order_index, finger_index in enumerate(order):
            start = pass_offset + order_index * finger_delay
            opened = open_positions[finger_index]
            closed = closed_positions[finger_index]
            peak_goal = round(
                opened + (closed - opened) * strength
            )
            print(
                f"{label:7s} {motor_ids[finger_index]:2d} "
                f"{start:5.2f} {start + bend_time:10.2f} "
                f"{start + bend_time + hold_time:8.2f} "
                f"{start + action_duration:5.2f} {peak_goal:9d}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("motor_config.json"),
    )
    parser.add_argument(
        "--ids",
        default="4,3,1,7",
        help=(
            "index,middle,ring,little motor IDs "
            "(default: 4,3,1,7)"
        ),
    )
    parser.add_argument(
        "--open-positions",
        default="1820,558,117,695",
        help=(
            "calibrated open positions for IDs 4,3,1,7 "
            "(default: 1820,558,117,695)"
        ),
    )
    parser.add_argument(
        "--closed-positions",
        default="4368,3228,2865,3525",
        help=(
            "calibrated closed positions for IDs 4,3,1,7 "
            "(default: 4368,3228,2865,3525)"
        ),
    )
    parser.add_argument(
        "--bend-time",
        type=float,
        default=0.8,
        help="seconds for each finger to reach its closed goal",
    )
    parser.add_argument(
        "--hold-time",
        type=float,
        default=0.5,
        help="seconds each finger stays at its closed goal",
    )
    parser.add_argument(
        "--release-time",
        type=float,
        default=0.8,
        help="seconds for each finger to return to open",
    )
    parser.add_argument(
        "--finger-delay",
        type=float,
        default=0.4,
        help="seconds between starting adjacent fingers",
    )
    parser.add_argument(
        "--strength",
        type=float,
        default=0.30,
        help="fraction of each configured span, 0..1",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=50.0,
        help="control update rate in Hz",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=0,
        help="full forward/reverse cycles; 0 runs until stopped",
    )
    parser.add_argument("--velocity", type=int, default=10)
    parser.add_argument("--acceleration", type=int, default=1)
    parser.add_argument("--goal-current", type=int, default=200)
    parser.add_argument("--goal-pwm", type=int, default=300)
    parser.add_argument(
        "--open-duration",
        type=float,
        default=2.0,
        help="seconds to move from the current pose to calibrated open",
    )
    parser.add_argument(
        "--return-duration",
        type=float,
        default=1.5,
        help="seconds to return to the calibrated open pose",
    )
    parser.add_argument(
        "--arm",
        action="store_true",
        help="allow real motor movement",
    )
    args = parser.parse_args()

    motor_ids = parse_csv_ints(args.ids, "--ids")
    if len(motor_ids) != 4 or len(set(motor_ids)) != 4:
        raise ValueError("--ids must contain four unique motor IDs")

    open_positions = parse_csv_ints(
        args.open_positions, "--open-positions"
    )
    closed_positions = parse_csv_ints(
        args.closed_positions, "--closed-positions"
    )
    if len(open_positions) != 4:
        raise ValueError(
            "--open-positions must contain four integers"
        )
    if len(closed_positions) != 4:
        raise ValueError(
            "--closed-positions must contain four integers"
        )
    if any(
        opened == closed
        for opened, closed in zip(
            open_positions, closed_positions
        )
    ):
        raise ValueError(
            "each open and closed position pair must differ"
        )
    for value in open_positions + closed_positions:
        if not MIN_EXTENDED_POSITION <= value <= MAX_EXTENDED_POSITION:
            raise ValueError(
                f"calibrated position {value} outside mode 5 range"
            )

    if args.bend_time <= 0:
        raise ValueError("--bend-time must be positive")
    if args.hold_time < 0:
        raise ValueError("--hold-time cannot be negative")
    if args.release_time <= 0:
        raise ValueError("--release-time must be positive")
    if args.finger_delay < 0:
        raise ValueError("--finger-delay cannot be negative")
    if not 0 < args.strength <= 1:
        raise ValueError("--strength must be in (0, 1]")
    if not 5 <= args.rate <= 100:
        raise ValueError("--rate must be between 5 and 100 Hz")
    if args.cycles < 0:
        raise ValueError("--cycles must be non-negative")
    if args.velocity < 0 or args.acceleration < 0:
        raise ValueError(
            "--velocity and --acceleration must be non-negative"
        )
    if not 0 <= args.goal_current <= 1470:
        raise ValueError("--goal-current must be 0..1470 mA")
    if not 0 <= args.goal_pwm <= 885:
        raise ValueError("--goal-pwm must be 0..885")
    if args.open_duration < 0 or args.return_duration < 0:
        raise ValueError(
            "--open-duration and --return-duration cannot be negative"
        )

    if not args.arm:
        print_dry_run(
            motor_ids,
            open_positions,
            closed_positions,
            args.bend_time,
            args.hold_time,
            args.release_time,
            args.finger_delay,
            args.strength,
        )
        print(
            "\nReal movement requires --arm."
        )
        return 0

    baudrate, buses = load_config(args.config)
    configured_ids = {
        motor_id for bus in buses for motor_id in bus.ids
    }
    unknown = sorted(set(motor_ids) - configured_ids)
    if unknown:
        raise ValueError(f"unconfigured motor IDs: {unknown}")

    stop_requested = False

    def request_stop(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        print(f"\nSignal {signum}: stopping wave...", flush=True)
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    config_directory = args.config.resolve().parent
    with ProcessLock(config_directory):
        hardware = Hardware(baudrate, buses)
        starts: dict[int, int] = {}
        last_goals: dict[int, int] = {}
        faulted = False
        try:
            torque_errors = hardware.torque_off_all()
            if torque_errors:
                raise RuntimeError(
                    "initial torque-off failed: "
                    + "; ".join(torque_errors)
                )
            captured = hardware.prepare(
                motor_ids,
                args.velocity,
                args.acceleration,
                args.goal_pwm,
                args.goal_current,
            )
            starts = {
                motor_id: position
                for motor_id, position in zip(
                    motor_ids, open_positions
                )
            }
            closed = {
                motor_id: position
                for motor_id, position in zip(
                    motor_ids, closed_positions
                )
            }

            print("Moving to calibrated open pose:")
            for motor_id in motor_ids:
                print(
                    f"  ID {motor_id}: current={captured[motor_id]}, "
                    f"open={starts[motor_id]}, closed={closed[motor_id]}"
                )
            return_to_start(
                hardware,
                motor_ids,
                starts,
                captured,
                args.open_duration,
                args.rate,
            )
            last_goals = dict(starts)
            print(
                "Running index -> little -> index. "
                "Press Ctrl+C to return to calibrated open "
                "and torque off.",
                flush=True,
            )

            action_duration = (
                args.bend_time
                + args.hold_time
                + args.release_time
            )
            pass_duration = (
                (len(motor_ids) - 1) * args.finger_delay
                + action_duration
            )
            full_cycle_duration = 2.0 * pass_duration
            start_time = time.monotonic()
            next_tick = start_time
            previous_completed_cycles = 0

            while not stop_requested:
                now = time.monotonic()
                elapsed = now - start_time
                completed_cycles = int(
                    elapsed / full_cycle_duration
                )
                if (
                    args.cycles > 0
                    and completed_cycles >= args.cycles
                ):
                    break

                cycle_time = elapsed % full_cycle_duration
                if cycle_time < pass_duration:
                    pass_time = cycle_time
                    order = list(range(len(motor_ids)))
                else:
                    pass_time = cycle_time - pass_duration
                    order = list(
                        reversed(range(len(motor_ids)))
                    )

                flexions = [0.0] * len(motor_ids)
                for order_index, finger_index in enumerate(order):
                    local_time = (
                        pass_time
                        - order_index * args.finger_delay
                    )
                    flexions[finger_index] = (
                        finger_profile(
                            local_time,
                            args.bend_time,
                            args.hold_time,
                            args.release_time,
                        )
                        * args.strength
                    )
                goals: dict[int, int] = {}
                for index, motor_id in enumerate(motor_ids):
                    goals[motor_id] = round(
                        starts[motor_id]
                        + (closed[motor_id] - starts[motor_id])
                        * flexions[index]
                    )
                hardware.command_positions(goals)
                last_goals = goals

                if completed_cycles != previous_completed_cycles:
                    previous_completed_cycles = completed_cycles
                    print(
                        f"Completed cycles: {completed_cycles}",
                        flush=True,
                    )

                next_tick += 1.0 / args.rate
                time.sleep(max(0.0, next_tick - time.monotonic()))

        except Exception:
            faulted = True
            raise
        finally:
            if starts and last_goals and not faulted:
                try:
                    print(
                        "Returning to calibrated open pose...",
                        flush=True,
                    )
                    return_to_start(
                        hardware,
                        motor_ids,
                        starts,
                        last_goals,
                        args.return_duration,
                        args.rate,
                    )
                except Exception as error:
                    print(
                        f"WARNING: return failed: {error}",
                        file=sys.stderr,
                    )
            errors = hardware.torque_off_all()
            hardware.close()
            if errors:
                print(
                    "Torque-off errors: " + "; ".join(errors),
                    file=sys.stderr,
                )
            else:
                print("All seven motors torque OFF.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
