#!/usr/bin/env python3
"""Calibrate and run real tendon-driven hand poses with seven XL330 motors.

The program is dry-run by default.  Real calibration requires
``--arm --confirm CALIBRATE`` and real hand motion requires
``--arm --confirm HAND``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import select
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from dynamixel_sdk import (
    COMM_SUCCESS,
    GroupSyncRead,
    GroupSyncWrite,
    PacketHandler,
    PortHandler,
)


# XL330-M288-T control table (Protocol 2.0)
ADDR_OPERATING_MODE = 11
ADDR_CURRENT_LIMIT = 38
ADDR_TORQUE_ENABLE = 64
ADDR_HARDWARE_ERROR = 70
ADDR_GOAL_CURRENT = 102
ADDR_PROFILE_ACCELERATION = 108
ADDR_PROFILE_VELOCITY = 112
ADDR_GOAL_POSITION = 116
ADDR_PRESENT_CURRENT = 126
ADDR_PRESENT_VELOCITY = 128
ADDR_PRESENT_POSITION = 132
ADDR_PRESENT_VOLTAGE = 144
ADDR_PRESENT_TEMPERATURE = 146

CURRENT_BASED_POSITION_MODE = 5
TORQUE_OFF = 0
TORQUE_ON = 1
XL330_M288_MODEL_NUMBER = 1200

EXPECTED_ROLES = {
    "thumb_flex",
    "index_flex",
    "middle_flex",
    "ring_flex",
    "little_flex",
    "thumb_opposition",
    "thumb_abduction",
}

KNOWN_MOTOR_SCRIPTS = {
    "bench_test_7_motors.py",
    "change_dxl_id.py",
    "continuous_7_motors.py",
    "control_7_motors.py",
    "finger_demo_7_motors.py",
    "full_circle.py",
    "hand_motion_7_motors.py",
    "keyboard_7_motors.py",
    "max_speed_test.py",
    "move_all_to_center.py",
    "move_xl330.py",
    "ping_xl330.py",
    "scan_7_motors.py",
}

STOP_REQUESTED = False


class SafetyFault(RuntimeError):
    """A condition that requires immediate torque-off."""


class StopRequested(RuntimeError):
    """Raised after Ctrl+C, SIGHUP, or SIGTERM."""


class MotorProcessLock:
    """Best-effort exclusion against concurrent motor-control processes."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / ".hand_motor_control.lock"
        self.handle: Any | None = None
        self.fcntl: Any | None = None

    @staticmethod
    def find_conflicting_python_processes() -> list[str]:
        conflicts: list[str] = []
        own_pid = os.getpid()
        proc_root = Path("/proc")
        if not proc_root.is_dir():
            return conflicts

        for entry in proc_root.iterdir():
            if not entry.name.isdigit() or int(entry.name) == own_pid:
                continue
            try:
                executable = os.path.basename(os.readlink(entry / "exe"))
                if "python" not in executable.lower():
                    continue
                command = (
                    (entry / "cmdline")
                    .read_bytes()
                    .replace(b"\x00", b" ")
                    .decode("utf-8", errors="replace")
                    .strip()
                )
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            if any(name in command for name in KNOWN_MOTOR_SCRIPTS):
                conflicts.append(f"PID {entry.name}: {command}")
        return conflicts

    def acquire(self) -> None:
        try:
            import fcntl
        except ImportError as exc:
            raise RuntimeError(
                "Armed mode requires Linux fcntl process locking"
            ) from exc

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        self.fcntl = fcntl
        try:
            fcntl.flock(
                self.handle.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            self.handle.seek(0)
            owner = self.handle.read().strip() or "unknown owner"
            self.handle.close()
            self.handle = None
            raise RuntimeError(
                f"Another hand controller holds {self.path}: {owner}"
            ) from exc

        conflicts = self.find_conflicting_python_processes()
        if conflicts:
            detail = "\n  ".join(conflicts)
            self.release()
            raise RuntimeError(
                "Another known motor script is already running:\n  "
                + detail
            )

        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(
            f"pid={os.getpid()} "
            f"started={datetime.now().isoformat(timespec='seconds')}\n"
        )
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if self.fcntl is not None:
                self.fcntl.flock(self.handle.fileno(), self.fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


@dataclass(frozen=True)
class Settings:
    baudrate: int
    protocol_version: float
    profile_velocity_raw: int
    profile_acceleration_raw: int
    calibration_goal_current_raw: int
    hard_current_limit_raw: int
    fault_current_raw: int
    fault_current_hold_s: float
    contact_current_ratio: float
    contact_position_error_raw: int
    contact_hold_s: float
    max_temperature_c: int
    monitor_period_s: float
    jog_step_raw: int
    max_jog_command_raw: int
    minimum_endpoint_span_raw: int
    startup_endpoint_margin_raw: int
    pose_duration_s: float
    pose_dwell_s: float


@dataclass(frozen=True)
class BusConfig:
    name: str
    device: str
    ids: tuple[int, ...]


@dataclass(frozen=True)
class AxisConfig:
    motor_id: int
    role: str
    label: str
    min_raw: int
    max_raw: int
    goal_current_raw: int
    open_raw: int | None
    closed_raw: int | None
    calibrated: bool
    bus_name: str


@dataclass
class LoadedConfig:
    path: Path
    raw: dict[str, Any]
    settings: Settings
    buses: dict[str, BusConfig]
    axes: dict[int, AxisConfig]
    poses: dict[str, dict[str, float]]
    show_sequence: list[str]

    @property
    def mapping_signature(self) -> str:
        return "|".join(
            f"{axis.motor_id}:{axis.role}"
            for axis in sorted(self.axes.values(), key=lambda item: item.motor_id)
        )

    @property
    def mapping_is_confirmed(self) -> bool:
        return bool(self.raw.get("mapping_confirmed")) and (
            self.raw.get("confirmed_mapping_signature") == self.mapping_signature
        )


@dataclass
class RuntimeAxis:
    axis: AxisConfig
    port: PortHandler
    goal_raw: int = 0
    applied_goal_current_raw: int = 0
    applied_current_limit_raw: int = 0
    torque_enabled: bool = False


def signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def signed32(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def signal_handler(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def install_signal_handlers() -> None:
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal_handler)


def check_stop_requested() -> None:
    if STOP_REQUESTED:
        raise StopRequested("stop signal received")


def as_int(raw: dict[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def as_float(raw: dict[str, Any], key: str) -> float:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    return float(value)


def load_config(path: Path) -> LoadedConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ValueError("Unsupported configuration schema_version")

    settings = Settings(
        baudrate=as_int(raw, "baudrate"),
        protocol_version=as_float(raw, "protocol_version"),
        profile_velocity_raw=as_int(raw, "profile_velocity_raw"),
        profile_acceleration_raw=as_int(raw, "profile_acceleration_raw"),
        calibration_goal_current_raw=as_int(
            raw, "calibration_goal_current_raw"
        ),
        hard_current_limit_raw=as_int(raw, "hard_current_limit_raw"),
        fault_current_raw=as_int(raw, "fault_current_raw"),
        fault_current_hold_s=as_float(raw, "fault_current_hold_s"),
        contact_current_ratio=as_float(raw, "contact_current_ratio"),
        contact_position_error_raw=as_int(
            raw, "contact_position_error_raw"
        ),
        contact_hold_s=as_float(raw, "contact_hold_s"),
        max_temperature_c=as_int(raw, "max_temperature_c"),
        monitor_period_s=as_float(raw, "monitor_period_s"),
        jog_step_raw=as_int(raw, "jog_step_raw"),
        max_jog_command_raw=as_int(raw, "max_jog_command_raw"),
        minimum_endpoint_span_raw=as_int(
            raw, "minimum_endpoint_span_raw"
        ),
        startup_endpoint_margin_raw=as_int(
            raw, "startup_endpoint_margin_raw"
        ),
        pose_duration_s=as_float(raw, "pose_duration_s"),
        pose_dwell_s=as_float(raw, "pose_dwell_s"),
    )
    validate_settings(settings)

    buses: dict[str, BusConfig] = {}
    id_to_bus: dict[int, str] = {}
    for item in raw.get("buses", []):
        name = str(item["name"])
        ids = tuple(int(value) for value in item["ids"])
        bus = BusConfig(name=name, device=str(item["device"]), ids=ids)
        if name in buses:
            raise ValueError(f"Duplicate bus name: {name}")
        if not ids:
            raise ValueError(f"{name} has no motor IDs")
        buses[name] = bus
        for motor_id in ids:
            if motor_id in id_to_bus:
                raise ValueError(f"ID {motor_id} is assigned to more than one bus")
            id_to_bus[motor_id] = name
    if not buses:
        raise ValueError("No buses in configuration")

    axes: dict[int, AxisConfig] = {}
    roles: set[str] = set()
    for item in raw.get("axes", []):
        motor_id = int(item["id"])
        if motor_id in axes:
            raise ValueError(f"Duplicate axis ID: {motor_id}")
        if motor_id not in id_to_bus:
            raise ValueError(f"ID {motor_id} is not assigned to a bus")

        role = str(item["role"])
        if role in roles:
            raise ValueError(f"Duplicate axis role: {role}")
        roles.add(role)

        open_raw = item.get("open_raw")
        closed_raw = item.get("closed_raw")
        if open_raw is not None:
            open_raw = int(open_raw)
        if closed_raw is not None:
            closed_raw = int(closed_raw)

        axis = AxisConfig(
            motor_id=motor_id,
            role=role,
            label=str(item["label"]),
            min_raw=int(item["min_raw"]),
            max_raw=int(item["max_raw"]),
            goal_current_raw=int(item["goal_current_raw"]),
            open_raw=open_raw,
            closed_raw=closed_raw,
            calibrated=bool(item.get("calibrated", False)),
            bus_name=id_to_bus[motor_id],
        )
        validate_axis(axis, settings)
        axes[motor_id] = axis

    if set(axes) != set(id_to_bus):
        missing_axes = sorted(set(id_to_bus) - set(axes))
        extra_axes = sorted(set(axes) - set(id_to_bus))
        raise ValueError(
            f"Bus/axis ID mismatch; missing={missing_axes}, extra={extra_axes}"
        )
    if roles != EXPECTED_ROLES:
        raise ValueError(
            "Axis roles must exactly match: " + ", ".join(sorted(EXPECTED_ROLES))
        )

    poses: dict[str, dict[str, float]] = {}
    for pose_name, pose_raw in raw.get("poses", {}).items():
        pose: dict[str, float] = {}
        if set(pose_raw) != EXPECTED_ROLES:
            raise ValueError(
                f"Pose {pose_name!r} must define all seven roles exactly once"
            )
        for role, fraction_raw in pose_raw.items():
            fraction = float(fraction_raw)
            if not 0.0 <= fraction <= 1.0:
                raise ValueError(
                    f"Pose {pose_name!r}, role {role}: fraction must be 0..1"
                )
            pose[role] = fraction
        poses[str(pose_name)] = pose
    if not poses:
        raise ValueError("No poses in configuration")

    show_sequence = [str(value) for value in raw.get("show_sequence", [])]
    if not show_sequence:
        raise ValueError("show_sequence is empty")
    unknown_poses = sorted(set(show_sequence) - set(poses))
    if unknown_poses:
        raise ValueError(f"Unknown poses in show_sequence: {unknown_poses}")

    return LoadedConfig(
        path=path,
        raw=raw,
        settings=settings,
        buses=buses,
        axes=axes,
        poses=poses,
        show_sequence=show_sequence,
    )


def validate_settings(settings: Settings) -> None:
    if settings.baudrate <= 0:
        raise ValueError("baudrate must be positive")
    if settings.protocol_version != 2.0:
        raise ValueError("This program requires protocol_version 2.0")
    if not 1 <= settings.profile_velocity_raw <= 200:
        raise ValueError("profile_velocity_raw must be 1..200")
    if not 1 <= settings.profile_acceleration_raw <= 500:
        raise ValueError("profile_acceleration_raw must be 1..500")
    if not 1 <= settings.calibration_goal_current_raw:
        raise ValueError("calibration_goal_current_raw must be positive")
    if not 1 <= settings.hard_current_limit_raw <= 300:
        raise ValueError("hard_current_limit_raw must be 1..300")
    if not (
        settings.calibration_goal_current_raw
        <= settings.fault_current_raw
        <= settings.hard_current_limit_raw
    ):
        raise ValueError(
            "Current values must satisfy calibration <= fault <= hard limit"
        )
    if not 0.05 <= settings.fault_current_hold_s <= 2.0:
        raise ValueError("fault_current_hold_s must be 0.05..2.0")
    if not 0.5 <= settings.contact_current_ratio <= 1.0:
        raise ValueError("contact_current_ratio must be 0.5..1.0")
    if settings.contact_position_error_raw < 10:
        raise ValueError("contact_position_error_raw must be at least 10")
    if not 0.1 <= settings.contact_hold_s <= 3.0:
        raise ValueError("contact_hold_s must be 0.1..3.0")
    if not 30 <= settings.max_temperature_c <= 65:
        raise ValueError("max_temperature_c must be 30..65")
    if not 0.05 <= settings.monitor_period_s <= 0.5:
        raise ValueError("monitor_period_s must be 0.05..0.5")
    if not 1 <= settings.jog_step_raw <= settings.max_jog_command_raw:
        raise ValueError("Invalid jog step/maximum")
    if not 10 <= settings.minimum_endpoint_span_raw <= 1000:
        raise ValueError("minimum_endpoint_span_raw must be 10..1000")
    if not 0 <= settings.startup_endpoint_margin_raw <= 500:
        raise ValueError("startup_endpoint_margin_raw must be 0..500")
    if not 0.2 <= settings.pose_duration_s <= 10.0:
        raise ValueError("pose_duration_s must be 0.2..10.0")
    if not 0.0 <= settings.pose_dwell_s <= 10.0:
        raise ValueError("pose_dwell_s must be 0..10.0")


def validate_axis(axis: AxisConfig, settings: Settings) -> None:
    if not 0 <= axis.min_raw < axis.max_raw <= 4095:
        raise ValueError(
            f"ID {axis.motor_id}: limits must satisfy 0 <= min < max <= 4095"
        )
    if not 1 <= axis.goal_current_raw <= settings.hard_current_limit_raw:
        raise ValueError(
            f"ID {axis.motor_id}: goal_current_raw must be within hard limit"
        )
    if axis.calibrated:
        if axis.open_raw is None or axis.closed_raw is None:
            raise ValueError(
                f"ID {axis.motor_id}: calibrated axis needs both endpoints"
            )
        if not axis.min_raw <= axis.open_raw <= axis.max_raw:
            raise ValueError(f"ID {axis.motor_id}: open_raw outside safe range")
        if not axis.min_raw <= axis.closed_raw <= axis.max_raw:
            raise ValueError(f"ID {axis.motor_id}: closed_raw outside safe range")
        if (
            abs(axis.closed_raw - axis.open_raw)
            < settings.minimum_endpoint_span_raw
        ):
            raise ValueError(
                f"ID {axis.motor_id}: calibrated endpoint span is too small"
            )
    elif axis.open_raw is not None or axis.closed_raw is not None:
        raise ValueError(
            f"ID {axis.motor_id}: uncalibrated axis must use null endpoints"
        )


def save_config_atomic(config: LoadedConfig) -> None:
    config.path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{config.path.name}.",
        suffix=".tmp",
        dir=config.path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(config.raw, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, config.path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def update_axis_calibration(
    config: LoadedConfig,
    motor_id: int,
    open_raw: int,
    closed_raw: int,
) -> None:
    for item in config.raw["axes"]:
        if int(item["id"]) == motor_id:
            item["open_raw"] = int(open_raw)
            item["closed_raw"] = int(closed_raw)
            item["calibrated"] = True
            save_config_atomic(config)
            return
    raise ValueError(f"ID {motor_id} not found while saving calibration")


class Hardware:
    def __init__(self, config: LoadedConfig, motor_ids: Iterable[int]) -> None:
        self.config = config
        self.packet = PacketHandler(config.settings.protocol_version)
        selected_ids = sorted(set(motor_ids))
        bus_names = {config.axes[motor_id].bus_name for motor_id in selected_ids}
        self.ports = {
            bus_name: PortHandler(config.buses[bus_name].device)
            for bus_name in bus_names
        }
        self.opened_buses: set[str] = set()
        self.runtimes = {
            motor_id: RuntimeAxis(
                axis=config.axes[motor_id],
                port=self.ports[config.axes[motor_id].bus_name],
            )
            for motor_id in selected_ids
        }
        self.status_readers: dict[str, GroupSyncRead] = {}
        self.error_readers: dict[str, GroupSyncRead] = {}
        self.goal_writers: dict[str, GroupSyncWrite] = {}
        for bus_name, port in self.ports.items():
            status_reader = GroupSyncRead(
                port,
                self.packet,
                ADDR_PRESENT_CURRENT,
                ADDR_PRESENT_TEMPERATURE - ADDR_PRESENT_CURRENT + 1,
            )
            error_reader = GroupSyncRead(
                port,
                self.packet,
                ADDR_HARDWARE_ERROR,
                1,
            )
            for runtime in self.runtimes.values():
                if runtime.axis.bus_name != bus_name:
                    continue
                if not status_reader.addParam(runtime.axis.motor_id):
                    raise RuntimeError(
                        f"ID {runtime.axis.motor_id}: cannot add status "
                        "GroupSyncRead parameter"
                    )
                if not error_reader.addParam(runtime.axis.motor_id):
                    raise RuntimeError(
                        f"ID {runtime.axis.motor_id}: cannot add error "
                        "GroupSyncRead parameter"
                    )
            self.status_readers[bus_name] = status_reader
            self.error_readers[bus_name] = error_reader
            self.goal_writers[bus_name] = GroupSyncWrite(
                port,
                self.packet,
                ADDR_GOAL_POSITION,
                4,
            )

    def check_result(
        self, comm_result: int, dxl_error: int, action: str
    ) -> None:
        if comm_result != COMM_SUCCESS:
            raise RuntimeError(
                f"{action}: {self.packet.getTxRxResult(comm_result)}"
            )
        if dxl_error:
            raise RuntimeError(
                f"{action}: {self.packet.getRxPacketError(dxl_error)}"
            )

    def write1(
        self, runtime: RuntimeAxis, address: int, value: int, action: str
    ) -> None:
        result, error = self.packet.write1ByteTxRx(
            runtime.port, runtime.axis.motor_id, address, value
        )
        self.check_result(
            result, error, f"ID {runtime.axis.motor_id} {action}"
        )

    def write2(
        self, runtime: RuntimeAxis, address: int, value: int, action: str
    ) -> None:
        result, error = self.packet.write2ByteTxRx(
            runtime.port, runtime.axis.motor_id, address, value & 0xFFFF
        )
        self.check_result(
            result, error, f"ID {runtime.axis.motor_id} {action}"
        )

    def write4(
        self, runtime: RuntimeAxis, address: int, value: int, action: str
    ) -> None:
        result, error = self.packet.write4ByteTxRx(
            runtime.port, runtime.axis.motor_id, address, value & 0xFFFFFFFF
        )
        self.check_result(
            result, error, f"ID {runtime.axis.motor_id} {action}"
        )

    def read1(
        self, runtime: RuntimeAxis, address: int, action: str
    ) -> int:
        value, result, error = self.packet.read1ByteTxRx(
            runtime.port, runtime.axis.motor_id, address
        )
        self.check_result(
            result, error, f"ID {runtime.axis.motor_id} {action}"
        )
        return value

    def read2(
        self, runtime: RuntimeAxis, address: int, action: str
    ) -> int:
        value, result, error = self.packet.read2ByteTxRx(
            runtime.port, runtime.axis.motor_id, address
        )
        self.check_result(
            result, error, f"ID {runtime.axis.motor_id} {action}"
        )
        return value

    def read4(
        self, runtime: RuntimeAxis, address: int, action: str
    ) -> int:
        value, result, error = self.packet.read4ByteTxRx(
            runtime.port, runtime.axis.motor_id, address
        )
        self.check_result(
            result, error, f"ID {runtime.axis.motor_id} {action}"
        )
        return value

    def open_ports(self) -> None:
        for bus_name, port in self.ports.items():
            check_stop_requested()
            bus = self.config.buses[bus_name]
            if not port.openPort():
                raise RuntimeError(f"Cannot open {bus.device}")
            self.opened_buses.add(bus_name)
            if not port.setBaudRate(self.config.settings.baudrate):
                raise RuntimeError(
                    f"Cannot set {bus.device} to "
                    f"{self.config.settings.baudrate} bps"
                )

    def ping_and_check_model(self, runtime: RuntimeAxis) -> None:
        model, result, error = self.packet.ping(
            runtime.port, runtime.axis.motor_id
        )
        self.check_result(
            result, error, f"ID {runtime.axis.motor_id} ping"
        )
        if model != XL330_M288_MODEL_NUMBER:
            raise RuntimeError(
                f"ID {runtime.axis.motor_id}: model {model}, "
                f"expected XL330-M288 model {XL330_M288_MODEL_NUMBER}"
            )

    def prepare(
        self,
        requested_currents: dict[int, int],
        require_start_within_endpoints: bool,
    ) -> None:
        settings = self.config.settings
        self.open_ports()

        # Disable every selected axis first.  This also makes partial-init
        # cleanup deterministic if a later motor fails.
        for runtime in self.runtimes.values():
            self.ping_and_check_model(runtime)
            self.write1(
                runtime,
                ADDR_TORQUE_ENABLE,
                TORQUE_OFF,
                "disable torque before configuration",
            )

        for runtime in self.runtimes.values():
            check_stop_requested()
            axis = runtime.axis
            hardware_error = self.read1(
                runtime, ADDR_HARDWARE_ERROR, "read hardware error"
            )
            temperature = self.read1(
                runtime, ADDR_PRESENT_TEMPERATURE, "read temperature"
            )
            if hardware_error:
                raise SafetyFault(
                    f"ID {axis.motor_id}: hardware error "
                    f"0x{hardware_error:02X} before arming"
                )
            if temperature >= settings.max_temperature_c:
                raise SafetyFault(
                    f"ID {axis.motor_id}: temperature {temperature} C "
                    f"before arming"
                )

            # Changing mode resets Goal Current to Current Limit.  Torque is
            # still off here, and the hard limit is lowered before torque-on.
            self.write1(
                runtime,
                ADDR_OPERATING_MODE,
                CURRENT_BASED_POSITION_MODE,
                "set current-based position mode",
            )

            current_limit = self.read2(
                runtime, ADDR_CURRENT_LIMIT, "read current limit"
            )
            if current_limit <= 0:
                raise RuntimeError(
                    f"ID {axis.motor_id}: invalid Current Limit "
                    f"{current_limit}"
                )
            if current_limit > settings.hard_current_limit_raw:
                self.write2(
                    runtime,
                    ADDR_CURRENT_LIMIT,
                    settings.hard_current_limit_raw,
                    "lower persistent hard current limit",
                )
                current_limit = self.read2(
                    runtime, ADDR_CURRENT_LIMIT, "verify current limit"
                )
                if current_limit != settings.hard_current_limit_raw:
                    raise RuntimeError(
                        f"ID {axis.motor_id}: failed to verify hard "
                        "current limit"
                    )

            runtime.applied_current_limit_raw = current_limit
            self.write4(
                runtime,
                ADDR_PROFILE_ACCELERATION,
                settings.profile_acceleration_raw,
                "set profile acceleration",
            )
            self.write4(
                runtime,
                ADDR_PROFILE_VELOCITY,
                settings.profile_velocity_raw,
                "set profile velocity",
            )

            present = signed32(
                self.read4(
                    runtime,
                    ADDR_PRESENT_POSITION,
                    "read position before arming",
                )
            )
            if not axis.min_raw <= present <= axis.max_raw:
                raise SafetyFault(
                    f"ID {axis.motor_id}: startup position {present} "
                    f"outside [{axis.min_raw}, {axis.max_raw}]"
                )
            if require_start_within_endpoints:
                assert axis.open_raw is not None
                assert axis.closed_raw is not None
                low = min(axis.open_raw, axis.closed_raw)
                high = max(axis.open_raw, axis.closed_raw)
                margin = settings.startup_endpoint_margin_raw
                if not low - margin <= present <= high + margin:
                    raise SafetyFault(
                        f"ID {axis.motor_id}: startup {present} is too far "
                        f"outside calibrated endpoints [{low}, {high}]"
                    )

            runtime.goal_raw = present
            self.write4(
                runtime,
                ADDR_GOAL_POSITION,
                present,
                "hold present position before torque-on",
            )
            applied_current = min(
                requested_currents[axis.motor_id],
                runtime.applied_current_limit_raw,
                settings.hard_current_limit_raw,
            )
            if applied_current <= 0:
                raise RuntimeError(
                    f"ID {axis.motor_id}: no usable goal current"
                )
            runtime.applied_goal_current_raw = applied_current
            self.write2(
                runtime,
                ADDR_GOAL_CURRENT,
                applied_current,
                "set safe goal current before torque-on",
            )
            verified_goal_current = signed16(
                self.read2(
                    runtime,
                    ADDR_GOAL_CURRENT,
                    "verify safe goal current",
                )
            )
            if verified_goal_current != applied_current:
                raise RuntimeError(
                    f"ID {axis.motor_id}: Goal Current verification "
                    f"returned {verified_goal_current}, expected "
                    f"{applied_current}"
                )

        for runtime in self.runtimes.values():
            check_stop_requested()
            self.write1(
                runtime,
                ADDR_TORQUE_ENABLE,
                TORQUE_ON,
                "enable torque",
            )
            runtime.torque_enabled = True
            print(
                f"ARMED ID {runtime.axis.motor_id} "
                f"({runtime.axis.role}): hold={runtime.goal_raw}, "
                f"goal_current={runtime.applied_goal_current_raw} mA, "
                f"hard_limit={runtime.applied_current_limit_raw} mA"
            )

    def command_position(self, runtime: RuntimeAxis, goal_raw: int) -> None:
        goal_raw = clamp(
            int(goal_raw), runtime.axis.min_raw, runtime.axis.max_raw
        )
        self.write4(
            runtime,
            ADDR_GOAL_POSITION,
            goal_raw,
            f"set goal position {goal_raw}",
        )
        runtime.goal_raw = goal_raw

    def command_positions(self, goals: dict[int, int]) -> None:
        goals_by_bus: dict[str, list[tuple[RuntimeAxis, int]]] = {}
        for motor_id, requested_goal in goals.items():
            runtime = self.runtimes[motor_id]
            goal = clamp(
                int(requested_goal),
                runtime.axis.min_raw,
                runtime.axis.max_raw,
            )
            goals_by_bus.setdefault(runtime.axis.bus_name, []).append(
                (runtime, goal)
            )

        for bus_name, items in goals_by_bus.items():
            writer = self.goal_writers[bus_name]
            try:
                for runtime, goal in items:
                    parameter = [
                        goal & 0xFF,
                        (goal >> 8) & 0xFF,
                        (goal >> 16) & 0xFF,
                        (goal >> 24) & 0xFF,
                    ]
                    if not writer.addParam(
                        runtime.axis.motor_id, parameter
                    ):
                        raise RuntimeError(
                            f"ID {runtime.axis.motor_id}: cannot add "
                            "GroupSyncWrite goal"
                        )
                result = writer.txPacket()
                if result != COMM_SUCCESS:
                    raise RuntimeError(
                        f"{bus_name} goal GroupSyncWrite: "
                        f"{self.packet.getTxRxResult(result)}"
                    )
                for runtime, goal in items:
                    runtime.goal_raw = goal
            finally:
                writer.clearParam()

    def hold_present(
        self, runtime: RuntimeAxis, present_raw: int | None = None
    ) -> int:
        if present_raw is None:
            present_raw = signed32(
                self.read4(
                    runtime,
                    ADDR_PRESENT_POSITION,
                    "read position for hold",
                )
            )
        present_raw = clamp(
            present_raw, runtime.axis.min_raw, runtime.axis.max_raw
        )
        self.command_position(runtime, present_raw)
        return present_raw

    @staticmethod
    def group_value(
        reader: GroupSyncRead,
        motor_id: int,
        address: int,
        length: int,
        label: str,
    ) -> int:
        if not reader.isAvailable(motor_id, address, length):
            raise RuntimeError(
                f"ID {motor_id}: {label} unavailable in GroupSyncRead"
            )
        return reader.getData(motor_id, address, length)

    def read_all_statuses(self) -> dict[int, dict[str, int]]:
        statuses: dict[int, dict[str, int]] = {}
        for bus_name in self.status_readers:
            status_reader = self.status_readers[bus_name]
            error_reader = self.error_readers[bus_name]
            result = status_reader.txRxPacket()
            if result != COMM_SUCCESS:
                raise RuntimeError(
                    f"{bus_name} status GroupSyncRead: "
                    f"{self.packet.getTxRxResult(result)}"
                )
            result = error_reader.txRxPacket()
            if result != COMM_SUCCESS:
                raise RuntimeError(
                    f"{bus_name} error GroupSyncRead: "
                    f"{self.packet.getTxRxResult(result)}"
                )

            for motor_id, runtime in self.runtimes.items():
                if runtime.axis.bus_name != bus_name:
                    continue
                statuses[motor_id] = {
                    "goal": runtime.goal_raw,
                    "current": signed16(
                        self.group_value(
                            status_reader,
                            motor_id,
                            ADDR_PRESENT_CURRENT,
                            2,
                            "present current",
                        )
                    ),
                    "velocity": signed32(
                        self.group_value(
                            status_reader,
                            motor_id,
                            ADDR_PRESENT_VELOCITY,
                            4,
                            "present velocity",
                        )
                    ),
                    "position": signed32(
                        self.group_value(
                            status_reader,
                            motor_id,
                            ADDR_PRESENT_POSITION,
                            4,
                            "present position",
                        )
                    ),
                    "voltage": self.group_value(
                        status_reader,
                        motor_id,
                        ADDR_PRESENT_VOLTAGE,
                        2,
                        "present voltage",
                    ),
                    "temperature": self.group_value(
                        status_reader,
                        motor_id,
                        ADDR_PRESENT_TEMPERATURE,
                        1,
                        "present temperature",
                    ),
                    "hardware_error": self.group_value(
                        error_reader,
                        motor_id,
                        ADDR_HARDWARE_ERROR,
                        1,
                        "hardware error",
                    ),
                }
        return statuses

    def disable_all_torque(self) -> None:
        for runtime in self.runtimes.values():
            if runtime.axis.bus_name not in self.opened_buses:
                continue
            last_error: Exception | None = None
            for attempt in range(1, 4):
                try:
                    result, error = self.packet.write1ByteTxRx(
                        runtime.port,
                        runtime.axis.motor_id,
                        ADDR_TORQUE_ENABLE,
                        TORQUE_OFF,
                    )
                    self.check_result(
                        result,
                        error,
                        f"ID {runtime.axis.motor_id} torque-off "
                        f"attempt {attempt}",
                    )
                    value, result, error = self.packet.read1ByteTxRx(
                        runtime.port,
                        runtime.axis.motor_id,
                        ADDR_TORQUE_ENABLE,
                    )
                    self.check_result(
                        result,
                        error,
                        f"ID {runtime.axis.motor_id} verify torque-off "
                        f"attempt {attempt}",
                    )
                    if value != TORQUE_OFF:
                        raise RuntimeError(
                            f"ID {runtime.axis.motor_id}: Torque Enable "
                            f"readback is {value}"
                        )
                    runtime.torque_enabled = False
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < 3:
                        time.sleep(0.05)
            if last_error is not None:
                print(
                    f"WARNING: torque-off could not be verified after "
                    f"3 attempts: {last_error}. CUT MOTOR POWER NOW.",
                    file=sys.stderr,
                )

    def close_ports(self) -> None:
        for bus_name in list(self.opened_buses):
            try:
                self.ports[bus_name].closePort()
            except Exception as exc:
                print(
                    f"WARNING: failed to close {bus_name}: {exc}",
                    file=sys.stderr,
                )
            finally:
                self.opened_buses.discard(bus_name)

    def shutdown(self) -> None:
        self.disable_all_torque()
        self.close_ports()


class TelemetryLogger:
    def __init__(self, config_path: Path, command_name: str) -> None:
        log_dir = config_path.parent / "logs" / "hand"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.path = log_dir / f"{command_name}_{stamp}.csv"
        self.handle = self.path.open("w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.handle)
        self.writer.writerow(
            [
                "timestamp",
                "phase",
                "event",
                "motor_id",
                "role",
                "goal_raw",
                "present_raw",
                "velocity_raw",
                "current_raw_mA",
                "voltage_raw_0.1V",
                "temperature_C",
                "hardware_error",
            ]
        )

    def write(
        self,
        phase: str,
        event: str,
        runtime: RuntimeAxis,
        status: dict[str, int],
    ) -> None:
        self.writer.writerow(
            [
                datetime.now().isoformat(timespec="milliseconds"),
                phase,
                event,
                runtime.axis.motor_id,
                runtime.axis.role,
                status["goal"],
                status["position"],
                status["velocity"],
                status["current"],
                status["voltage"],
                status["temperature"],
                f"0x{status['hardware_error']:02X}",
            ]
        )
        self.handle.flush()

    def close(self) -> None:
        self.handle.close()


class SafetyMonitor:
    def __init__(
        self,
        hardware: Hardware,
        logger: TelemetryLogger,
    ) -> None:
        self.hardware = hardware
        self.logger = logger
        self.overcurrent_since: dict[int, float] = {}
        self.contact_since: dict[int, float] = {}

    def sample(
        self, phase: str, auto_hold_contact: bool = True
    ) -> tuple[dict[int, dict[str, int]], set[int]]:
        check_stop_requested()
        settings = self.hardware.config.settings
        now = time.monotonic()
        statuses = self.hardware.read_all_statuses()
        blocked: set[int] = set()

        for motor_id, runtime in self.hardware.runtimes.items():
            status = statuses[motor_id]
            axis = runtime.axis

            if status["hardware_error"]:
                self.logger.write(
                    phase, "hardware_error", runtime, status
                )
                raise SafetyFault(
                    f"ID {motor_id}: hardware error "
                    f"0x{status['hardware_error']:02X}"
                )
            if status["temperature"] >= settings.max_temperature_c:
                self.logger.write(
                    phase, "over_temperature", runtime, status
                )
                raise SafetyFault(
                    f"ID {motor_id}: temperature "
                    f"{status['temperature']} C >= "
                    f"{settings.max_temperature_c} C"
                )
            if not axis.min_raw <= status["position"] <= axis.max_raw:
                self.logger.write(
                    phase, "position_out_of_range", runtime, status
                )
                raise SafetyFault(
                    f"ID {motor_id}: position {status['position']} "
                    f"outside [{axis.min_raw}, {axis.max_raw}]"
                )

            if abs(status["current"]) >= settings.fault_current_raw:
                started = self.overcurrent_since.setdefault(motor_id, now)
                if now - started >= settings.fault_current_hold_s:
                    self.logger.write(
                        phase, "persistent_overcurrent", runtime, status
                    )
                    raise SafetyFault(
                        f"ID {motor_id}: current {status['current']} mA "
                        f"persisted above {settings.fault_current_raw} mA"
                    )
            else:
                self.overcurrent_since.pop(motor_id, None)

            contact_threshold = max(
                1,
                math.ceil(
                    runtime.applied_goal_current_raw
                    * settings.contact_current_ratio
                ),
            )
            position_error = abs(runtime.goal_raw - status["position"])
            contact_condition = (
                position_error >= settings.contact_position_error_raw
                and abs(status["current"]) >= contact_threshold
            )
            if contact_condition:
                started = self.contact_since.setdefault(motor_id, now)
                if (
                    auto_hold_contact
                    and now - started >= settings.contact_hold_s
                ):
                    self.hardware.hold_present(
                        runtime, status["position"]
                    )
                    status["goal"] = runtime.goal_raw
                    blocked.add(motor_id)
                    self.contact_since.pop(motor_id, None)
                    self.logger.write(
                        phase, "contact_auto_hold", runtime, status
                    )
                    print(
                        f"CONTACT ID {motor_id} ({axis.role}): "
                        f"held at {status['position']} raw, "
                        f"current={status['current']} mA"
                    )
                    continue
            else:
                self.contact_since.pop(motor_id, None)

            self.logger.write(phase, "", runtime, status)

        return statuses, blocked

    def wait(self, seconds: float, phase: str) -> None:
        if seconds <= 0:
            return
        deadline = time.monotonic() + seconds
        while True:
            check_stop_requested()
            self.sample(phase)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(
                min(self.hardware.config.settings.monitor_period_s, remaining)
            )


def target_for_pose(axis: AxisConfig, fraction: float) -> int:
    if axis.open_raw is None or axis.closed_raw is None:
        raise ValueError(f"ID {axis.motor_id} is not calibrated")
    raw = round(axis.open_raw + (axis.closed_raw - axis.open_raw) * fraction)
    low = min(axis.open_raw, axis.closed_raw)
    high = max(axis.open_raw, axis.closed_raw)
    return clamp(raw, low, high)


def pose_targets(
    config: LoadedConfig, pose_name: str
) -> dict[int, int]:
    if pose_name not in config.poses:
        raise ValueError(
            f"Unknown pose {pose_name!r}; choices: "
            + ", ".join(sorted(config.poses))
        )
    pose = config.poses[pose_name]
    return {
        motor_id: target_for_pose(axis, pose[axis.role])
        for motor_id, axis in config.axes.items()
    }


def ensure_mapping_confirmed(config: LoadedConfig) -> None:
    if not config.mapping_is_confirmed:
        raise ValueError(
            "Motor-to-hand mapping is not confirmed (or changed after "
            "confirmation). Run plan, verify the physical tendons, then run "
            "confirm-map --confirm MAP."
        )


def ensure_motion_ready(config: LoadedConfig) -> None:
    ensure_mapping_confirmed(config)
    incomplete = [
        axis.motor_id for axis in config.axes.values() if not axis.calibrated
    ]
    if incomplete:
        raise ValueError(
            "Calibrate every axis before coordinated motion; incomplete IDs: "
            + ", ".join(str(value) for value in incomplete)
        )


def mapping_table(config: LoadedConfig) -> str:
    lines = [
        "ID  BUS    ROLE                 LABEL          OPEN  CLOSED  STATE",
        "--  -----  -------------------  -------------  ----  ------  -----",
    ]
    for motor_id in sorted(config.axes):
        axis = config.axes[motor_id]
        state = "OK" if axis.calibrated else "NOT_CALIBRATED"
        lines.append(
            f"{motor_id:2d}  {axis.bus_name:5s}  {axis.role:19s}  "
            f"{axis.label:13s}  {str(axis.open_raw):>4s}  "
            f"{str(axis.closed_raw):>6s}  {state}"
        )
    return "\n".join(lines)


def print_plan(config: LoadedConfig) -> None:
    print("REAL HAND MOTION PLAN (DRY RUN)")
    print("No serial port will be opened; no torque command will be sent.\n")
    print(mapping_table(config))
    print()
    print(
        "Mapping confirmation: "
        + ("CONFIRMED" if config.mapping_is_confirmed else "NOT CONFIRMED")
    )
    print(
        "Control: mode=5, "
        f"velocity={config.settings.profile_velocity_raw} raw, "
        f"acceleration={config.settings.profile_acceleration_raw} raw, "
        f"hard current limit={config.settings.hard_current_limit_raw} mA"
    )

    calibrated = all(axis.calibrated for axis in config.axes.values())
    if calibrated:
        print("\nPose targets (absolute raw):")
        for pose_name in config.poses:
            targets = pose_targets(config, pose_name)
            compact = " ".join(
                f"ID{motor_id}={targets[motor_id]}"
                for motor_id in sorted(targets)
            )
            print(f"  {pose_name:8s} {compact}")
    else:
        print(
            "\nPose raw targets are unavailable until every axis has "
            "open/closed endpoints."
        )

    print("\nShow sequence: " + " -> ".join(config.show_sequence))
    print("\nNext required commands:")
    if not config.mapping_is_confirmed:
        print("  1. Physically verify/edit the ID-to-role map.")
        print(
            "  2. python hand_motion_7_motors.py "
            "confirm-map --confirm MAP"
        )
    incomplete = [
        axis.motor_id for axis in config.axes.values() if not axis.calibrated
    ]
    if incomplete:
        ids = ", ".join(str(value) for value in incomplete)
        print(f"  3. Calibrate IDs: {ids} (one at a time).")
    if config.mapping_is_confirmed and not incomplete:
        print(
            "  Ready for: python hand_motion_7_motors.py "
            "show --arm --confirm HAND"
        )


def confirm_map(config: LoadedConfig, confirmation: str) -> None:
    print(mapping_table(config))
    if confirmation != "MAP":
        raise ValueError(
            "Mapping confirmation requires exactly --confirm MAP"
        )
    config.raw["mapping_confirmed"] = True
    config.raw["confirmed_mapping_signature"] = config.mapping_signature
    save_config_atomic(config)
    print(
        f"Saved confirmed mapping signature to {config.path}. "
        "Changing any ID/role invalidates this confirmation."
    )


def print_status_line(runtime: RuntimeAxis, status: dict[str, int]) -> None:
    print(
        f"ID {runtime.axis.motor_id} ({runtime.axis.role}): "
        f"present={status['position']}, goal={runtime.goal_raw}, "
        f"error={runtime.goal_raw - status['position']}, "
        f"current={status['current']} mA, "
        f"velocity={status['velocity']} raw, "
        f"voltage={status['voltage'] * 0.1:.1f} V, "
        f"temp={status['temperature']} C, "
        f"hw=0x{status['hardware_error']:02X}"
    )


def calibration_help(settings: Settings) -> None:
    print(
        "\nCalibration commands\n"
        "  + [ticks]  increase raw (CCW from motor front)\n"
        "  - [ticks]  decrease raw (CW from motor front)\n"
        "  open       capture current physical OPEN endpoint\n"
        "  closed     capture safe CLOSED/MAX-ACTIVE endpoint\n"
        "  active     alias of closed for opposition/abduction axes\n"
        "  status     print live telemetry\n"
        "  save       atomically save both endpoints and quit\n"
        "  quit       torque-off without saving\n"
        "  help       show this text\n"
        f"Default jog={settings.jog_step_raw} raw; "
        f"maximum per command={settings.max_jog_command_raw} raw.\n"
    )


def parse_jog_command(line: str, settings: Settings) -> int | None:
    tokens = line.split()
    if not tokens or tokens[0] not in {"+", "-"}:
        return None
    if len(tokens) > 2:
        raise ValueError("Jog syntax: + [ticks] or - [ticks]")
    amount = settings.jog_step_raw
    if len(tokens) == 2:
        amount = int(tokens[1])
    if not 1 <= amount <= settings.max_jog_command_raw:
        raise ValueError(
            f"Jog must be 1..{settings.max_jog_command_raw} raw"
        )
    return amount if tokens[0] == "+" else -amount


def calibrate_axis(
    config: LoadedConfig,
    motor_id: int,
    armed: bool,
    confirmation: str,
) -> int:
    if motor_id not in config.axes:
        raise ValueError(f"Unknown motor ID {motor_id}")
    axis = config.axes[motor_id]

    print(
        f"Calibration target: ID {motor_id}, {axis.role} "
        f"({axis.label}), bus={axis.bus_name}"
    )
    print(
        f"Limits=[{axis.min_raw}, {axis.max_raw}], "
        f"jog={config.settings.jog_step_raw} raw, "
        f"current={config.settings.calibration_goal_current_raw} mA"
    )
    if not armed:
        print(
            "DRY RUN: no port opened.  Real calibration requires "
            "--arm --confirm CALIBRATE."
        )
        return 0
    if confirmation != "CALIBRATE":
        raise ValueError(
            "Real calibration requires --arm --confirm CALIBRATE"
        )
    ensure_mapping_confirmed(config)
    if not sys.stdin.isatty():
        raise ValueError("Calibration requires an interactive SSH terminal")

    install_signal_handlers()
    control_lock = MotorProcessLock(Path(__file__).resolve().parent)
    control_lock.acquire()
    hardware: Hardware | None = None
    logger: TelemetryLogger | None = None
    try:
        hardware = Hardware(config, [motor_id])
        logger = TelemetryLogger(config.path, f"calibrate_id{motor_id}")
        runtime = hardware.runtimes[motor_id]
        monitor = SafetyMonitor(hardware, logger)
        # A recalibration session must recapture both endpoints.  Existing
        # values are never silently reused by the save command.
        candidate_open: int | None = None
        candidate_closed: int | None = None

        hardware.prepare(
            {
                motor_id: config.settings.calibration_goal_current_raw,
            },
            require_start_within_endpoints=False,
        )
        print(f"Telemetry: {logger.path}")
        if axis.calibrated:
            print(
                f"Existing calibration: open={axis.open_raw}, "
                f"closed={axis.closed_raw}. "
                "Recapture both endpoints to replace it."
            )
        calibration_help(config.settings)

        print("calibrate> ", end="", flush=True)
        while True:
            check_stop_requested()
            readable, _, _ = select.select(
                [sys.stdin],
                [],
                [],
                config.settings.monitor_period_s,
            )
            if not readable:
                monitor.sample("calibration_idle")
                continue

            line = sys.stdin.readline()
            if line == "":
                print("\nSSH input closed; stopping without saving.")
                return 0
            line = line.strip().lower()
            if not line:
                print("calibrate> ", end="", flush=True)
                continue

            try:
                jog = parse_jog_command(line, config.settings)
                if jog is not None:
                    target = clamp(
                        runtime.goal_raw + jog,
                        axis.min_raw,
                        axis.max_raw,
                    )
                    if target == runtime.goal_raw:
                        print("Already at configured hard position limit.")
                    else:
                        hardware.command_position(runtime, target)
                        print(f"Goal -> {target} raw")
                        # Always monitor long enough to detect contact before
                        # accepting another queued jog command.
                        monitor.wait(
                            config.settings.contact_hold_s
                            + config.settings.monitor_period_s,
                            "calibration_jog",
                        )
                    print("calibrate> ", end="", flush=True)
                    continue

                if line == "open":
                    statuses, _ = monitor.sample("capture_open")
                    candidate_open = hardware.hold_present(
                        runtime, statuses[motor_id]["position"]
                    )
                    print(f"Candidate OPEN={candidate_open} raw")
                elif line in {"closed", "close", "active"}:
                    statuses, _ = monitor.sample("capture_closed")
                    candidate_closed = hardware.hold_present(
                        runtime, statuses[motor_id]["position"]
                    )
                    print(f"Candidate CLOSED={candidate_closed} raw")
                elif line in {"status", "s"}:
                    statuses, _ = monitor.sample("manual_status")
                    print_status_line(runtime, statuses[motor_id])
                    print(
                        f"Candidate endpoints: open={candidate_open}, "
                        f"closed={candidate_closed}"
                    )
                elif line == "save":
                    if candidate_open is None or candidate_closed is None:
                        raise ValueError(
                            "Capture both open and closed before save"
                        )
                    span = abs(candidate_closed - candidate_open)
                    if span < config.settings.minimum_endpoint_span_raw:
                        raise ValueError(
                            f"Endpoint span {span} raw is smaller than "
                            f"{config.settings.minimum_endpoint_span_raw}"
                        )
                    if candidate_open == candidate_closed:
                        raise ValueError("Open and closed cannot be equal")
                    update_axis_calibration(
                        config,
                        motor_id,
                        candidate_open,
                        candidate_closed,
                    )
                    print(
                        f"Saved ID {motor_id}: open={candidate_open}, "
                        f"closed={candidate_closed}, span={span} raw"
                    )
                    return 0
                elif line in {"quit", "q", "exit"}:
                    print("Stopping without changing the configuration.")
                    return 0
                elif line in {"help", "h", "?"}:
                    calibration_help(config.settings)
                else:
                    print("Unknown command. Type help.")
            except ValueError as exc:
                print(f"Command rejected: {exc}")
            print("calibrate> ", end="", flush=True)
    finally:
        try:
            if hardware is not None:
                hardware.shutdown()
                print("Torque disabled; ports closed.")
        finally:
            try:
                if logger is not None:
                    logger.close()
            finally:
                control_lock.release()


def smoothstep(value: float) -> float:
    return value * value * (3.0 - 2.0 * value)


def move_to_pose(
    hardware: Hardware,
    monitor: SafetyMonitor,
    pose_name: str,
    duration_s: float,
    dwell_s: float,
) -> None:
    config = hardware.config
    targets = pose_targets(config, pose_name)
    statuses, _ = monitor.sample(f"{pose_name}_start")
    starts = {
        motor_id: statuses[motor_id]["position"]
        for motor_id in hardware.runtimes
    }
    steps = max(
        1,
        math.ceil(duration_s / config.settings.monitor_period_s),
    )
    blocked: set[int] = set()
    print(f"POSE {pose_name}: moving for {duration_s:.2f} s")

    start_time = time.monotonic()
    for step in range(1, steps + 1):
        check_stop_requested()
        phase = smoothstep(step / steps)
        step_goals: dict[int, int] = {}
        for motor_id, runtime in hardware.runtimes.items():
            if motor_id in blocked:
                continue
            step_goals[motor_id] = round(
                starts[motor_id]
                + (targets[motor_id] - starts[motor_id]) * phase
            )
        hardware.command_positions(step_goals)

        _, newly_blocked = monitor.sample(f"pose_{pose_name}")
        blocked.update(newly_blocked)

        deadline = (
            start_time + step * duration_s / steps
        )
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    compact = " ".join(
        f"ID{motor_id}={targets[motor_id]}"
        + ("(contact)" if motor_id in blocked else "")
        for motor_id in sorted(targets)
    )
    print(f"POSE {pose_name}: targets {compact}")
    monitor.wait(dwell_s, f"dwell_{pose_name}")


def countdown(monitor: SafetyMonitor, seconds: int = 3) -> None:
    for remaining in range(seconds, 0, -1):
        print(f"Motion starts in {remaining}...")
        monitor.wait(1.0, "armed_countdown")


def run_motion(
    config: LoadedConfig,
    pose_names: list[str],
    armed: bool,
    confirmation: str,
    duration_s: float,
    dwell_s: float,
    command_name: str,
) -> int:
    for pose_name in pose_names:
        if pose_name not in config.poses:
            raise ValueError(
                f"Unknown pose {pose_name!r}; choices: "
                + ", ".join(sorted(config.poses))
            )

    if not armed:
        print("DRY RUN: no serial port will be opened.")
        print("Requested poses: " + " -> ".join(pose_names))
        if all(axis.calibrated for axis in config.axes.values()):
            for pose_name in dict.fromkeys(pose_names):
                targets = pose_targets(config, pose_name)
                compact = " ".join(
                    f"ID{motor_id}={targets[motor_id]}"
                    for motor_id in sorted(targets)
                )
                print(f"  {pose_name:8s} {compact}")
        else:
            print("Raw targets unavailable: calibration is incomplete.")
        print("No torque command was sent.")
        return 0

    if confirmation != "HAND":
        raise ValueError("Real hand motion requires --arm --confirm HAND")
    ensure_motion_ready(config)
    if not 0.2 <= duration_s <= 10.0:
        raise ValueError("duration must be 0.2..10.0 seconds")
    if not 0.0 <= dwell_s <= 10.0:
        raise ValueError("dwell must be 0..10.0 seconds")

    install_signal_handlers()
    motor_ids = sorted(config.axes)
    requested_currents = {
        motor_id: axis.goal_current_raw
        for motor_id, axis in config.axes.items()
    }
    control_lock = MotorProcessLock(Path(__file__).resolve().parent)
    control_lock.acquire()
    hardware: Hardware | None = None
    logger: TelemetryLogger | None = None
    try:
        hardware = Hardware(config, motor_ids)
        logger = TelemetryLogger(config.path, command_name)
        monitor = SafetyMonitor(hardware, logger)
        hardware.prepare(
            requested_currents,
            require_start_within_endpoints=True,
        )
        print(f"Telemetry: {logger.path}")
        countdown(monitor)
        for pose_name in pose_names:
            move_to_pose(
                hardware,
                monitor,
                pose_name,
                duration_s,
                dwell_s,
            )
        print("Requested hand motion completed.")
        return 0
    finally:
        try:
            if hardware is not None:
                hardware.shutdown()
                print("Torque disabled; ports closed.")
        finally:
            try:
                if logger is not None:
                    logger.close()
            finally:
                control_lock.release()


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=script_dir / "hand_motion_config.json",
        help="JSON configuration path",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "plan", help="Print mapping/calibration/pose plan without hardware I/O"
    )

    map_parser = subparsers.add_parser(
        "confirm-map",
        help="Confirm the physically verified ID-to-hand mapping",
    )
    map_parser.add_argument("--confirm", default="")

    calibrate_parser = subparsers.add_parser(
        "calibrate", help="Calibrate one real tendon axis"
    )
    calibrate_parser.add_argument("--id", type=int, required=True)
    calibrate_parser.add_argument("--arm", action="store_true")
    calibrate_parser.add_argument("--confirm", default="")

    show_parser = subparsers.add_parser(
        "show", help="Run the configured real-hand pose sequence"
    )
    show_parser.add_argument("--cycles", type=int, default=1)
    show_parser.add_argument("--arm", action="store_true")
    show_parser.add_argument("--confirm", default="")
    show_parser.add_argument("--duration", type=float)
    show_parser.add_argument("--dwell", type=float)

    pose_parser = subparsers.add_parser(
        "pose", help="Move once to one named hand pose"
    )
    pose_parser.add_argument("name")
    pose_parser.add_argument("--arm", action="store_true")
    pose_parser.add_argument("--confirm", default="")
    pose_parser.add_argument("--duration", type=float)
    pose_parser.add_argument("--dwell", type=float)

    return parser.parse_args()


def main() -> int:
    global STOP_REQUESTED
    STOP_REQUESTED = False
    args = parse_args()

    try:
        config = load_config(args.config.resolve())
        command = args.command or "plan"

        if command == "plan":
            print_plan(config)
            return 0
        if command == "confirm-map":
            confirm_map(config, args.confirm)
            return 0
        if command == "calibrate":
            return calibrate_axis(
                config,
                args.id,
                args.arm,
                args.confirm,
            )
        if command == "show":
            if not 1 <= args.cycles <= 3:
                raise ValueError("--cycles must be 1..3")
            duration = (
                config.settings.pose_duration_s
                if args.duration is None
                else args.duration
            )
            dwell = (
                config.settings.pose_dwell_s
                if args.dwell is None
                else args.dwell
            )
            poses = config.show_sequence * args.cycles
            return run_motion(
                config,
                poses,
                args.arm,
                args.confirm,
                duration,
                dwell,
                "hand_show",
            )
        if command == "pose":
            duration = (
                config.settings.pose_duration_s
                if args.duration is None
                else args.duration
            )
            dwell = (
                config.settings.pose_dwell_s
                if args.dwell is None
                else args.dwell
            )
            return run_motion(
                config,
                [args.name],
                args.arm,
                args.confirm,
                duration,
                dwell,
                f"hand_pose_{args.name}",
            )
        raise ValueError(f"Unknown command: {command}")
    except StopRequested:
        print("STOP: signal received; torque-off cleanup requested.")
        return 130
    except KeyboardInterrupt:
        print("\nSTOP: Ctrl+C received; torque-off cleanup requested.")
        return 130
    except SafetyFault as exc:
        print(f"SAFETY STOP: {exc}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
