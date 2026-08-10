"""
Manager가 선택한 명령을 마지막으로 재검사하는 ROS 비의존 fail-closed 코어.

간단 매뉴얼
-----------
① 역할
    Manager의 1차 중재 결과를 그대로 신뢰하지 않고 SafetyState/ControlState, source,
    stamp, sequence, 값 범위와 변화율을 다시 검사해 hardware 전달 가능 여부를 결정한다.
② 입력
    ``GuardLimits`` 설정, adapter가 전달하는 safety/control 상태 갱신, STOP 사건,
    ``GuardCommand``, 검증 시점의 system time과 monotonic time을 입력으로 받는다.
③ 출력
    상태 갱신의 반영 여부 또는 명령의 ``GuardDecision``을 반환한다. 결정에는 수락 여부,
    기계가 읽을 수 있는 reason, hardware topic으로 전달해도 되는지가 포함된다.
④ 주요 실행 흐름
    먼저 STOP과 권위 상태의 존재·freshness·재획득 신뢰를 확인하고, mode/owner/source,
    명령 시각, 축·속도·confidence, uint32 sequence, 축 변화율 순서로 검사한다. 모든
    검사를 통과한 뒤에만 replay와 rate-limit 기준 상태를 commit한다. HOLD이면 검증
    activity는 수락하되 hardware 전달은 막는다.
⑤ 사용/실행 방법
    ``GuardLimits``로 ``CommandGuardCore``를 만들고, 수신 callback에서 상태 갱신
    메서드를 호출한 뒤 각 후보 명령에 ``validate``를 호출한다. 이 module 자체는 ROS
    node를 실행하지 않으며 보통 ``command_guard.py`` adapter를 통해 사용한다.
⑥ 책임 경계와 하지 않는 일
    이 파일은 결정론적인 검증 정책과 최소 상태만 담당한다. ROS message 변환, QoS,
    topic publish, callback 간 locking은 adapter 책임이다. SafetyState/ControlState를
    생성하거나 motor를 제어하지 않고, 전류·온도·torque의 물리 안전 차단도 수행하지
    않는다. 또한 호출 동시성을 내부에서 직렬화하지 않으므로 호출자가 lock을 제공한다.
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
# mode별로 권한을 가진 owner와 허용 source를 한곳에 고정한다. 이 표에 없는 mode는
# 아래 검증에서 owner/source를 확정할 수 없으므로 빈 허용 집합으로 fail-closed된다.
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
    """
    외부 설정이 안전 범위를 넓히지 못하도록 생성 시 검증되는 명령 한계.

    mapping 네 개는 일곱 축을 정확히 한 번씩 포함해야 한다. timeout과 normalized 축
    범위에는 hard envelope가 있어 parameter가 더 보수적으로 줄일 수는 있지만 상한을
    넘겨 완화할 수는 없다. 객체를 frozen으로 두어 실행 중 검증 기준 변경도 막는다.
    """

    command_stale_timeout_ms: int
    command_future_tolerance_ms: int
    safety_state_timeout_ms: int
    control_state_timeout_ms: int
    command_hold_ms: int
    axis_min: Mapping[str, float]
    axis_max: Mapping[str, float]
    max_axis_delta_per_second: Mapping[str, float]
    mimic_max_axis_delta_per_second: Mapping[str, float]

    def __post_init__(self) -> None:
        """타입·hard timeout·축 집합·normalized 범위를 시작 시 한 번에 검증한다."""
        # bool은 int의 하위 타입이므로 명시적으로 제외한다. True를 1 ms처럼 받아들이면
        # 설정 오류가 조용히 통과해 운영자가 의도한 안전 계약과 달라질 수 있다.
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
            'command_hold_ms': 5000,
        }
        # parameter는 hard envelope를 더 엄격하게 만들 수만 있고 넓힐 수는 없다.
        for name, maximum in maximum_timeouts.items():
            if getattr(self, name) > maximum:
                raise ValueError(f'{name} cannot exceed {maximum}')

        expected_axes = set(AXIS_NAMES)
        # 누락 축은 무검증 통로가 되고 추가 축은 message 계약 불일치이므로 둘 다 거부한다.
        for name in (
            'axis_min',
            'axis_max',
            'max_axis_delta_per_second',
            'mimic_max_axis_delta_per_second',
        ):
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
            mimic_rate = self.mimic_max_axis_delta_per_second[axis_name]
            if not 0.0 < mimic_rate <= 10.0:
                raise ValueError(
                    'mimic_max_axis_delta_per_second values must be in '
                    '(0, 10.0]'
                )


@dataclass(frozen=True)
class GuardCommand:
    """
    ROS ``HandCommand``에서 검증에 필요한 값만 옮긴 불변 입력 모델.

    adapter가 ROS stamp와 축 field를 기본 Python 값으로 변환해 이 객체를 만든다. core는
    ROS type을 몰라도 되므로 단위 테스트에서 같은 정책을 결정론적으로 검증할 수 있다.
    """

    stamp_ns: int
    sequence: int
    source: int
    axes: Mapping[str, float]
    speed_limit: float
    confidence: float


@dataclass(frozen=True)
class GuardDecision:
    """
    후보 명령 하나에 대한 수락 여부, 이유, hardware 전달 허용을 담는 결과.

    ``accepted=True``라도 HOLD recovery activity이면 ``forward_to_hardware=False``다.
    이 구분 덕분에 유효한 복구 입력은 Safety Manager에 알리면서 motor 경로는 닫아 둔다.
    """

    accepted: bool
    reason: str
    forward_to_hardware: bool = True


class CommandGuardCore:
    """
    권위 상태와 source별 이력을 기억하며 후보 명령을 fail-closed 검증한다.

    상태 수신 시각, STOP latch, 실제 재획득을 관측했는지, source별 마지막 sequence와
    수락 축을 보관한다. 거부된 명령은 이력을 바꾸지 않으므로 replay/rate-limit 기준을
    오염시킬 수 없다. 내부 lock은 없으며 adapter가 상태 갱신과 ``validate`` 호출을
    직렬화해야 한다.
    """

    def __init__(self, limits: GuardLimits) -> None:
        self._limits = limits
        # source stamp는 SafetyState 생산자의 시간 순서를, received_ns는 이 프로세스가
        # 마지막 유효 표본을 본 뒤의 freshness를 판단한다. 두 시간축을 섞지 않는다.
        self._safety_state = None
        self._safety_received_ns = None
        self._safety_source_stamp_ns: Optional[int] = None
        self._safety_reason = ''
        self._control = None
        self._control_received_ns = None
        # transient-local 과거 active 표본만으로 권한이 부활하지 않도록, 실제로 관측한
        # DISABLED/NONE 경계와 그 뒤의 active transition을 별도로 기억한다.
        self._saw_disabled = False
        self._activation_trusted = False
        # STOP은 상태 표본 하나가 아니라 새 제어권 획득 전까지 유지되는 latch다.
        self._stop_latched = False
        # sequence와 rate 기준은 source별로 분리해 서로 다른 producer 이력이 섞이지 않는다.
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
        """
        SafetyState의 생산자 시각 순서를 검증하고 freshness/전환 상태를 갱신한다.

        더 오래된 source stamp나 같은 stamp에 내용이 다른 표본은 권위 상태를 되돌릴 수
        있으므로 ``False``로 거부한다. 같은 stamp와 같은 내용의 heartbeat는 로컬 수신
        시각만 새로 하여 freshness를 유지한다. 비검증 safety state로 바뀌면 기존 control
        activation trust와 hardware 전달 기준을 폐기해 상태 복귀만으로 재개되지 않는다.
        """
        new_source_transition = False
        if source_stamp_ns is not None:
            if self._safety_source_stamp_ns is not None:
                if source_stamp_ns < self._safety_source_stamp_ns:
                    # 늦게 도착한 과거 상태가 현재 안전 판단과 freshness를 덮지 못한다.
                    return False
                if source_stamp_ns == self._safety_source_stamp_ns:
                    if (
                        state != self._safety_state
                        or reason != self._safety_reason
                    ):
                        # 동일한 권위 시각에 서로 다른 내용은 순서를 판별할 수 없어 거부한다.
                        return False
                    # 동일 내용 재발행은 heartbeat로 인정하되 transition으로 세지는 않는다.
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
            # 비정상/비활성 safety state를 한 번이라도 관측하면 과거 mode 권한은 폐기한다.
            # 이후 READY/RUN heartbeat만으로 열리지 않고 새 DISABLED→active가 필요하다.
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
            # 재시작 직후 active 표본을 먼저 받은 경우와 실제 새 획득을 구분하는 기준점이다.
            self._saw_disabled = True
            self._activation_trusted = False
            return

        if current != previous:
            # 같은 active heartbeat는 획득 사건이 아니다. 내용이 바뀐 transition에서만
            # 직전에 DISABLED를 보았는지 확인해 activation trust를 결정한다.
            self._activation_trusted = self._saw_disabled
            self._saw_disabled = False
            if self._activation_trusted:
                # 새 획득만 STOP latch와 해당 mode source의 replay/rate 기준을 초기화한다.
                # rejected/heartbeat 입력은 이 권한 경계를 대신할 수 없다.
                self._stop_latched = False
                self._last_hardware_forwarded_ns = None
                for source in self._sources_for_mode(active_mode):
                    self._last_sequence.pop(source, None)
                    self._last_accepted.pop(source, None)

    def on_stop_requested(self) -> None:
        """
        새 DISABLED→active 획득 cycle 전까지 모든 명령을 막는 STOP latch를 건다.

        단순히 현재 명령 하나를 거부하는 것이 아니라 activation trust와 hardware 시간
        기준도 폐기한다. 따라서 STOP 뒤에 남아 있던 active state나 명령이 재전달되어도
        latch가 풀리지 않으며, 명시적인 비활성→활성 재획득이 필요하다.
        """
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
        """
        후보 명령을 정해진 순서로 검사하고 성공한 경우에만 source 이력을 commit한다.

        system time은 producer stamp의 과거/미래 여부에, monotonic time은 상태 수신 age와
        축 변화율에 사용한다. 첫 실패에서 즉시 이유를 반환하며 어떤 거부도 sequence나
        마지막 수락 축을 소비하지 않는다. 성공 후 safety/local HOLD이면 activity만
        인정하고 hardware 전달은 금지한다.
        """
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
        # mode가 허용한 source가 아니면 Manager 중재 오류나 topic 오접속으로 보고 막는다.
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
            # 빠진 축을 기본값으로 보충하지 않는다. 정확한 interface가 아니면 전체 거부다.
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
            rate_limits = (
                self._limits.mimic_max_axis_delta_per_second
                if command.source == SOURCE_MIMIC
                else self._limits.max_axis_delta_per_second
            )
            for axis_name in AXIS_NAMES:
                allowed_delta = (
                    rate_limits[axis_name]
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
        # Safety Manager의 명시적 HOLD뿐 아니라 마지막 hardware forward 이후 로컬
        # command_hold_ms가 지난 경우도 motor 경로를 닫는다. 유효 activity 자체는
        # recovery 판단에 필요하므로 sequence/rate 검증과 commit 후 별도 결과로 알린다.
        if self._safety_state == SAFETY_HOLD or local_hold:
            # HOLD 중에도 복구 판단용 검증은 계속하지만 실제 motor topic에는 publish하지
            # 않는다. adapter는 이 결정을 ordered validation_result Bool로 변환한다.
            return GuardDecision(True, 'hold_activity', False)
        self._last_hardware_forwarded_ns = now_monotonic_ns
        return GuardDecision(True, 'accepted', True)

    def _state_error(self, now_monotonic_ns: int) -> Optional[str]:
        """
        명령 값 검사 전에 STOP·상태 freshness·재획득 신뢰의 차단 이유를 반환한다.

        STOP을 가장 먼저 확인하고, safety와 control이 실제로 수신됐는지와 timeout을
        차례로 확인한다. 어떤 권위 상태도 없거나 오래됐거나 신뢰할 activation 경계를
        보지 못했다면 명령 내용이 정상이어도 hardware 검증 단계로 진행하지 않는다.
        """
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
        """mode에 허용된 source 집합을 반환하며 미등록 mode는 빈 집합으로 닫는다."""
        control = _EXPECTED_CONTROL.get(mode)
        if control is None:
            return frozenset()
        return control[1]
