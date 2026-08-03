"""
Manager가 선택한 명령을 마지막으로 다시 검사하는 fail-closed 정책 코어.

Manager가 '누가 보낸 명령인가'를 1차 중재해도, Guard는 SafetyState/ControlState
freshness, source, stamp, sequence, 축 범위와 변화율을 모두 다시 검사한다. 하나라도
확신할 수 없으면 hardware로 보내지 않는다. ROS 변환과 publish는 ``command_guard.py``가
맡는다.
"""

from dataclasses import dataclass
from math import isfinite
from typing import Dict, Mapping, Optional, Tuple


AXIS_NAMES = (
    'thumb_flex',
    'thumb_opp',
    'thumb_abd',
    'index_flex',
    'middle_flex',
    'ring_flex',
    'little_flex',
)

MODE_DISABLED = 0
MODE_MIMIC = 1
MODE_MANUAL = 2
MODE_TELEOP = 3

OWNER_NONE = 0
OWNER_WEB = 1
OWNER_LOCAL = 2

SOURCE_MIMIC = 1
SOURCE_TELEOP = 2
SOURCE_GESTURE = 3
SOURCE_SEQUENCE = 4
SOURCE_SAFETY = 5

SAFETY_READY = 1
SAFETY_RUN = 2
SAFETY_HOLD = 3

_FORWARD_SAFETY_STATES = frozenset((SAFETY_READY, SAFETY_RUN))
_VALIDATION_SAFETY_STATES = frozenset(
    (SAFETY_READY, SAFETY_RUN, SAFETY_HOLD)
)
_EXPECTED_CONTROL = {
    MODE_MIMIC: (OWNER_WEB, frozenset((SOURCE_MIMIC,))),
    MODE_MANUAL: (
        OWNER_WEB,
        frozenset((SOURCE_GESTURE, SOURCE_SEQUENCE)),
    ),
    MODE_TELEOP: (OWNER_LOCAL, frozenset((SOURCE_TELEOP,))),
}


@dataclass(frozen=True)
class GuardLimits:
    """version-controlled ROS parameter에서 읽는 명령 안전 한계."""

    command_stale_timeout_ms: int
    command_future_tolerance_ms: int
    safety_state_timeout_ms: int
    control_state_timeout_ms: int
    command_hold_ms: int
    axis_min: Mapping[str, float]
    axis_max: Mapping[str, float]
    max_axis_delta_per_second: Mapping[str, float]

    def __post_init__(self) -> None:
        for name in (
            'command_stale_timeout_ms',
            'command_future_tolerance_ms',
            'safety_state_timeout_ms',
            'control_state_timeout_ms',
            'command_hold_ms',
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f'{name} must be a positive integer')

        maximum_timeouts = {
            'command_stale_timeout_ms': 300,
            'command_future_tolerance_ms': 100,
            'safety_state_timeout_ms': 1500,
            'control_state_timeout_ms': 1500,
            'command_hold_ms': 300,
        }
        for name, maximum in maximum_timeouts.items():
            if getattr(self, name) > maximum:
                raise ValueError(f'{name} cannot exceed {maximum}')

        expected_axes = set(AXIS_NAMES)
        for name in ('axis_min', 'axis_max', 'max_axis_delta_per_second'):
            values = getattr(self, name)
            if set(values) != expected_axes:
                raise ValueError(f'{name} must define exactly seven axes')
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                for value in values.values()
            ):
                raise ValueError(f'{name} values must be finite numbers')

        for axis_name in AXIS_NAMES:
            if self.axis_min[axis_name] < 0.0:
                raise ValueError('axis_min cannot be below normalized zero')
            if self.axis_max[axis_name] > 1.0:
                raise ValueError('axis_max cannot exceed normalized one')
            if self.axis_min[axis_name] >= self.axis_max[axis_name]:
                raise ValueError('axis_min must be less than axis_max')
            if self.max_axis_delta_per_second[axis_name] <= 0.0:
                raise ValueError(
                    'max_axis_delta_per_second values must be positive'
                )


@dataclass(frozen=True)
class GuardCommand:
    """ROS-independent projection of ``HandCommand``."""

    stamp_ns: int
    sequence: int
    source: int
    axes: Mapping[str, float]
    speed_limit: float
    confidence: float


@dataclass(frozen=True)
class GuardDecision:
    """Result of validating one selected command."""

    accepted: bool
    reason: str
    forward_to_hardware: bool = True


class CommandGuardCore:
    """활성 source 하나를 검증하고 마지막 수락 sequence·축 상태를 기억한다."""

    def __init__(self, limits: GuardLimits) -> None:
        self._limits = limits
        self._safety_state = None
        self._safety_received_ns = None
        self._safety_source_stamp_ns: Optional[int] = None
        self._safety_reason = ''
        self._control = None
        self._control_received_ns = None
        self._saw_disabled = False
        self._activation_trusted = False
        self._stop_latched = False
        self._last_sequence: Dict[int, int] = {}
        self._last_accepted: Dict[int, Tuple[Mapping[str, float], int]] = {}
        self._last_hardware_forwarded_ns: Optional[int] = None

    def update_safety_state(
        self,
        state: int,
        received_monotonic_ns: int,
        *,
        source_stamp_ns: Optional[int] = None,
        reason: str = '',
    ) -> bool:
        """Source stamp 역행을 거부하며 권위 SafetyState transition을 반영한다."""
        new_source_transition = False
        if source_stamp_ns is not None:
            if self._safety_source_stamp_ns is not None:
                if source_stamp_ns < self._safety_source_stamp_ns:
                    return False
                if source_stamp_ns == self._safety_source_stamp_ns:
                    if (
                        state != self._safety_state
                        or reason != self._safety_reason
                    ):
                        return False
                    self._safety_received_ns = received_monotonic_ns
                    return True
            self._safety_source_stamp_ns = source_stamp_ns
            new_source_transition = True

        previous_state = self._safety_state
        previous_reason = self._safety_reason
        self._safety_state = state
        self._safety_reason = reason
        self._safety_received_ns = received_monotonic_ns
        if state == SAFETY_RUN and (
            previous_state == SAFETY_HOLD
            or (
                reason == 'command_stream_recovered'
                and (
                    new_source_transition
                    or previous_reason != reason
                )
            )
        ):
            # Safety Manager가 300 ms 복구 activity를 확인해 RUN을 열었으므로, HOLD 진입
            # 표본을 놓쳤더라도 recovery reason/stamp로 새 hardware 기준을 연다.
            self._last_hardware_forwarded_ns = received_monotonic_ns
        if state not in _VALIDATION_SAFETY_STATES:
            self._activation_trusted = False
            self._last_hardware_forwarded_ns = None
        return True

    def update_control_state(
        self,
        active_mode: int,
        active_owner: int,
        owner_alive: bool,
        received_monotonic_ns: int,
    ) -> None:
        """
        실제로 관측한 ``DISABLED/NONE → active`` 획득 경계를 추적한다.

        transient-local로 과거 active 상태만 받은 재시작 Guard가 이전 제어권을 자동
        재생하지 않게 하려면, 먼저 DISABLED를 보고 그 뒤 새 active를 봐야 한다.
        """
        previous = self._control
        current = (active_mode, active_owner, bool(owner_alive))
        self._control = current
        self._control_received_ns = received_monotonic_ns

        inactive = (
            active_mode == MODE_DISABLED
            and active_owner == OWNER_NONE
            and not owner_alive
        )
        if inactive:
            self._saw_disabled = True
            self._activation_trusted = False
            return

        if current != previous:
            self._activation_trusted = self._saw_disabled
            self._saw_disabled = False
            if self._activation_trusted:
                self._stop_latched = False
                self._last_hardware_forwarded_ns = None
                for source in self._sources_for_mode(active_mode):
                    self._last_sequence.pop(source, None)
                    self._last_accepted.pop(source, None)

    def on_stop_requested(self) -> None:
        """새 DISABLED→active cycle 전까지 모든 명령을 막는 STOP latch를 닫는다."""
        self._stop_latched = True
        self._activation_trusted = False
        self._saw_disabled = False
        self._last_hardware_forwarded_ns = None

    def validate(
        self,
        command: GuardCommand,
        now_ros_ns: int,
        now_monotonic_ns: int,
    ) -> GuardDecision:
        """한 명령을 검증하고 모든 검사가 끝난 뒤에만 sequence 기준을 갱신한다."""
        # 1) 권위 상태가 존재하고 fresh한지 먼저 확인한다. command 값이 정상이어도
        # SafetyState/ControlState를 믿을 수 없으면 fail-closed한다.
        state_error = self._state_error(now_monotonic_ns)
        if state_error is not None:
            return GuardDecision(False, state_error)

        active_mode, active_owner, owner_alive = self._control
        expected_owner, allowed_sources = _EXPECTED_CONTROL.get(
            active_mode,
            (None, frozenset()),
        )
        if not owner_alive or active_owner != expected_owner:
            return GuardDecision(False, 'control_inactive')
        if command.source not in allowed_sources:
            return GuardDecision(False, 'source_mode_mismatch')

        # 2) source stamp는 replay와 미래 시각 명령을 막는다. callback 처리 시간에는
        # monotonic clock을 쓰지만 wire stamp 비교에는 producer와 공유하는 system time을 쓴다.
        command_age_ns = now_ros_ns - command.stamp_ns
        stale_ns = self._limits.command_stale_timeout_ms * 1_000_000
        future_ns = self._limits.command_future_tolerance_ms * 1_000_000
        if command_age_ns > stale_ns:
            return GuardDecision(False, 'command_stale')
        if command_age_ns < -future_ns:
            return GuardDecision(False, 'command_from_future')

        if set(command.axes) != set(AXIS_NAMES):
            return GuardDecision(False, 'axis_set_invalid')
        for axis_name in AXIS_NAMES:
            axis_value = command.axes[axis_name]
            if not isfinite(axis_value):
                return GuardDecision(False, 'axis_non_finite')
            if not (
                self._limits.axis_min[axis_name]
                <= axis_value
                <= self._limits.axis_max[axis_name]
            ):
                return GuardDecision(False, 'axis_out_of_range')

        if not isfinite(command.speed_limit):
            return GuardDecision(False, 'speed_limit_non_finite')
        if not 0.0 < command.speed_limit <= 1.0:
            return GuardDecision(False, 'speed_limit_out_of_range')
        if not isfinite(command.confidence):
            return GuardDecision(False, 'confidence_non_finite')
        if not 0.0 <= command.confidence <= 1.0:
            return GuardDecision(False, 'confidence_out_of_range')

        # 3) uint32 sequence는 단순 대소 비교가 아니라 serial-number arithmetic을 쓴다.
        # 따라서 0xffffffff → 0은 정상 wrap이고, 반 바퀴 이상 역방향은 replay다.
        if not 0 <= command.sequence <= 0xFFFFFFFF:
            return GuardDecision(False, 'sequence_out_of_range')
        previous_sequence = self._last_sequence.get(command.source)
        if previous_sequence is not None:
            sequence_delta = (
                command.sequence - previous_sequence
            ) & 0xFFFFFFFF
            if sequence_delta == 0:
                return GuardDecision(False, 'sequence_duplicate')
            if sequence_delta >= 0x80000000:
                return GuardDecision(False, 'sequence_out_of_order')

        # 4) 변화율은 마지막 '수락' 명령과의 차이다. 거부된 명령은 기준을 갱신하지 않아
        # 공격자가 여러 작은 invalid step으로 목표를 밀어 올리지 못하게 한다.
        previous_accepted = self._last_accepted.get(command.source)
        if previous_accepted is not None:
            previous_axes, previous_monotonic_ns = previous_accepted
            elapsed_ns = now_monotonic_ns - previous_monotonic_ns
            if elapsed_ns < 0:
                return GuardDecision(False, 'monotonic_time_regressed')
            elapsed_seconds = elapsed_ns / 1_000_000_000.0
            for axis_name in AXIS_NAMES:
                allowed_delta = (
                    self._limits.max_axis_delta_per_second[axis_name]
                    * command.speed_limit
                    * elapsed_seconds
                )
                actual_delta = abs(
                    command.axes[axis_name] - previous_axes[axis_name]
                )
                if actual_delta > allowed_delta + 1e-9:
                    return GuardDecision(False, 'axis_rate_exceeded')

        # 모든 검사가 끝난 뒤에만 replay/rate-limit 기준을 commit한다.
        self._last_sequence[command.source] = command.sequence
        self._last_accepted[command.source] = (
            dict(command.axes),
            now_monotonic_ns,
        )
        local_hold = bool(
            self._last_hardware_forwarded_ns is not None
            and now_monotonic_ns - self._last_hardware_forwarded_ns
            >= self._limits.command_hold_ms * 1_000_000
        )
        if self._safety_state == SAFETY_HOLD or local_hold:
            # HOLD 중에도 복구 판단용 검증은 계속하지만 실제 motor topic에는 publish하지
            # 않는다. adapter는 이 결정을 ordered validation_result Bool로 변환한다.
            return GuardDecision(True, 'hold_activity', False)
        self._last_hardware_forwarded_ns = now_monotonic_ns
        return GuardDecision(True, 'accepted', True)

    def _state_error(self, now_monotonic_ns: int) -> Optional[str]:
        if self._stop_latched:
            return 'stop_latched'
        if self._safety_received_ns is None:
            return 'safety_state_missing'
        safety_age_ns = now_monotonic_ns - self._safety_received_ns
        if safety_age_ns > self._limits.safety_state_timeout_ms * 1_000_000:
            return 'safety_state_stale'
        if self._safety_state not in _VALIDATION_SAFETY_STATES:
            return 'safety_not_ready'

        if self._control_received_ns is None or self._control is None:
            return 'control_state_missing'
        control_age_ns = now_monotonic_ns - self._control_received_ns
        if control_age_ns > self._limits.control_state_timeout_ms * 1_000_000:
            return 'control_state_stale'
        active_mode, active_owner, owner_alive = self._control
        expected_owner, _ = _EXPECTED_CONTROL.get(
            active_mode,
            (None, frozenset()),
        )
        if not owner_alive or active_owner != expected_owner:
            return 'control_inactive'
        if not self._activation_trusted:
            return 'control_activation_not_observed'
        return None

    @staticmethod
    def _sources_for_mode(mode: int):
        control = _EXPECTED_CONTROL.get(mode)
        if control is None:
            return frozenset()
        return control[1]
