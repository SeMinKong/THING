#!/usr/bin/env python3
"""Run a staged five-finger grasp with calibrated XL330 tendon motors.

The four finger motors close together first, then the thumb closes last to
wrap around them without interference. On release, the thumb opens first and
the four fingers open together. Ctrl+C or SIGTERM releases the grasp and
disables torque on all configured motors. Real movement requires --arm.
"""

from __future__ import annotations

import argparse
import signal
import time
from pathlib import Path

from wave_control_7 import (
    Hardware,
    MAX_EXTENDED_POSITION,
    MIN_EXTENDED_POSITION,
    ProcessLock,
    load_config,
    parse_csv_ints,
    return_to_start,
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
        help="index,middle,ring,little motor IDs",
    )
    parser.add_argument(
        "--open-positions",
        default="1820,558,117,695",
        help="calibrated open positions for IDs 4,3,1,7",
    )
    parser.add_argument(
        "--closed-positions",
        default="4368,3228,2865,3525",
        help="calibrated closed positions for IDs 4,3,1,7",
    )
    parser.add_argument(
        "--thumb-id",
        type=int,
        default=2,
        help="thumb motor ID",
    )
    parser.add_argument(
        "--thumb-open-position",
        type=int,
        default=3877,
        help="calibrated open position for the thumb",
    )
    parser.add_argument(
        "--thumb-closed-position",
        type=int,
        default=5117,
        help="calibrated grasp position for the thumb",
    )
    parser.add_argument(
        "--strength",
        type=float,
        default=1.0,
        help="fraction of each calibrated span, 0..1",
    )
    parser.add_argument(
        "--grasp-duration",
        type=float,
        default=1.5,
        help="seconds for all fingers to close together",
    )
    parser.add_argument(
        "--thumb-delay",
        type=float,
        default=0.3,
        help="seconds to wait after the four fingers close",
    )
    parser.add_argument(
        "--thumb-duration",
        type=float,
        default=1.0,
        help="seconds for the thumb to wrap around last",
    )
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=0.0,
        help="hold duration; 0 holds until Ctrl+C",
    )
    parser.add_argument("--rate", type=float, default=50.0)
    parser.add_argument("--velocity", type=int, default=300)
    parser.add_argument("--acceleration", type=int, default=1500)
    parser.add_argument("--goal-current", type=int, default=1470)
    parser.add_argument("--goal-pwm", type=int, default=885)
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
        help="seconds to open when stopping",
    )
    parser.add_argument(
        "--arm",
        action="store_true",
        help="allow real motor movement",
    )
    args = parser.parse_args()

    motor_ids = parse_csv_ints(args.ids, "--ids")
    open_values = parse_csv_ints(
        args.open_positions, "--open-positions"
    )
    closed_values = parse_csv_ints(
        args.closed_positions, "--closed-positions"
    )
    if len(motor_ids) != 4 or len(set(motor_ids)) != 4:
        raise ValueError("--ids must contain four unique motor IDs")
    if args.thumb_id in motor_ids:
        raise ValueError("--thumb-id must differ from the four finger IDs")
    if len(open_values) != 4 or len(closed_values) != 4:
        raise ValueError(
            "--open-positions and --closed-positions "
            "must each contain four integers"
        )
    for value in (
        open_values
        + closed_values
        + [args.thumb_open_position, args.thumb_closed_position]
    ):
        if not MIN_EXTENDED_POSITION <= value <= MAX_EXTENDED_POSITION:
            raise ValueError(
                f"calibrated position {value} outside mode 5 range"
            )
    if not 0 < args.strength <= 1:
        raise ValueError("--strength must be in (0, 1]")
    if args.grasp_duration <= 0:
        raise ValueError("--grasp-duration must be positive")
    if args.thumb_delay < 0:
        raise ValueError("--thumb-delay cannot be negative")
    if args.thumb_duration <= 0:
        raise ValueError("--thumb-duration must be positive")
    if args.hold_seconds < 0:
        raise ValueError("--hold-seconds cannot be negative")
    if not 5 <= args.rate <= 100:
        raise ValueError("--rate must be between 5 and 100 Hz")
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

    opened = dict(zip(motor_ids, open_values))
    targets = {
        motor_id: round(
            opened[motor_id]
            + (closed - opened[motor_id]) * args.strength
        )
        for motor_id, closed in zip(motor_ids, closed_values)
    }
    active_ids = motor_ids + [args.thumb_id]
    all_opened = dict(opened)
    all_opened[args.thumb_id] = args.thumb_open_position
    all_targets = dict(targets)
    all_targets[args.thumb_id] = args.thumb_closed_position

    if not args.arm:
        print("DRY RUN: no serial port will be opened")
        print(f"finger IDs: {motor_ids}")
        print(f"finger open goals:  {opened}")
        print(f"finger grasp goals: {targets}")
        print(
            f"thumb ID {args.thumb_id}: "
            f"{args.thumb_open_position} -> "
            f"{args.thumb_closed_position}"
        )
        print(
            "sequence: four fingers close together "
            f"({args.grasp_duration:.2f}s) -> "
            f"wait ({args.thumb_delay:.2f}s) -> "
            f"thumb closes last ({args.thumb_duration:.2f}s)"
        )
        print(
            "release: thumb opens first -> "
            "four fingers open together"
        )
        print(
            (
                f"hold {args.hold_seconds:.2f}s"
                if args.hold_seconds > 0
                else "hold until Ctrl+C"
            )
        )
        print("Real movement requires --arm.")
        return 0

    baudrate, buses = load_config(args.config)
    configured_ids = {
        motor_id for bus in buses for motor_id in bus.ids
    }
    unknown = sorted(set(active_ids) - configured_ids)
    if unknown:
        raise ValueError(f"unconfigured motor IDs: {unknown}")

    stop_requested = False

    def request_stop(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        print(
            f"\nSignal {signum}: releasing grasp...",
            flush=True,
        )
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    with ProcessLock(args.config.resolve().parent):
        hardware = Hardware(baudrate, buses)
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
                active_ids,
                args.velocity,
                args.acceleration,
                args.goal_pwm,
                args.goal_current,
            )

            print("Moving to calibrated open pose:")
            for motor_id in active_ids:
                print(
                    f"  ID {motor_id}: current={captured[motor_id]}, "
                    f"open={all_opened[motor_id]}, "
                    f"grasp={all_targets[motor_id]}"
                )
            return_to_start(
                hardware,
                active_ids,
                all_opened,
                captured,
                args.open_duration,
                args.rate,
            )
            last_goals = dict(all_opened)

            if not stop_requested:
                print(
                    "Grasping all four fingers together...",
                    flush=True,
                )
                return_to_start(
                    hardware,
                    motor_ids,
                    targets,
                    opened,
                    args.grasp_duration,
                    args.rate,
                )
                last_goals.update(targets)

            if not stop_requested and args.thumb_delay > 0:
                print(
                    f"Waiting {args.thumb_delay:.2f}s "
                    "before wrapping thumb...",
                    flush=True,
                )
                delay_deadline = (
                    time.monotonic() + args.thumb_delay
                )
                while (
                    not stop_requested
                    and time.monotonic() < delay_deadline
                ):
                    time.sleep(0.02)

            if not stop_requested:
                print(
                    f"Wrapping thumb ID {args.thumb_id} last...",
                    flush=True,
                )
                return_to_start(
                    hardware,
                    [args.thumb_id],
                    {
                        args.thumb_id:
                            args.thumb_closed_position
                    },
                    {
                        args.thumb_id:
                            args.thumb_open_position
                    },
                    args.thumb_duration,
                    args.rate,
                )
                last_goals[
                    args.thumb_id
                ] = args.thumb_closed_position

            if not stop_requested:
                if args.hold_seconds > 0:
                    print(
                        f"Holding for {args.hold_seconds:.2f}s. "
                        "Press Ctrl+C to release early.",
                        flush=True,
                    )
                    hold_deadline = (
                        time.monotonic() + args.hold_seconds
                    )
                    while (
                        not stop_requested
                        and time.monotonic() < hold_deadline
                    ):
                        time.sleep(0.05)
                else:
                    print(
                        "Holding grasp. Press Ctrl+C to open "
                        "and torque off.",
                        flush=True,
                    )
                    while not stop_requested:
                        time.sleep(0.05)

        except Exception:
            faulted = True
            raise
        finally:
            if last_goals and not faulted:
                try:
                    print(
                        "Opening thumb first...",
                        flush=True,
                    )
                    return_to_start(
                        hardware,
                        [args.thumb_id],
                        {
                            args.thumb_id:
                                args.thumb_open_position
                        },
                        {
                            args.thumb_id:
                                last_goals[args.thumb_id]
                        },
                        args.thumb_duration,
                        args.rate,
                    )
                    last_goals[
                        args.thumb_id
                    ] = args.thumb_open_position
                    print(
                        "Opening the other four fingers...",
                        flush=True,
                    )
                    return_to_start(
                        hardware,
                        motor_ids,
                        opened,
                        {
                            motor_id: last_goals[motor_id]
                            for motor_id in motor_ids
                        },
                        args.return_duration,
                        args.rate,
                    )
                except Exception as error:
                    print(
                        f"WARNING: return failed: {error}",
                        flush=True,
                    )
            errors = hardware.torque_off_all()
            hardware.close()
            if errors:
                print(
                    "Torque-off errors: " + "; ".join(errors),
                    flush=True,
                )
            else:
                print("All seven motors torque OFF.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)
