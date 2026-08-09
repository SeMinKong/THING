"""
ROS 없이도 시험할 수 있는 8상태 안전 정책 코어 사용 안내.

① 역할
    외부 I/O 없이 시각과 검증된 상태값만 받아 안전 상태 전이, watchdog, reset 승인
    조건을 판정한다. 같은 상태와 같은 입력에는 같은 결과를 내므로 단위 테스트에서
    실제 ROS executor나 wall clock 없이 안전 규칙을 재현할 수 있다.

② 입력
    :class:`HardwareStatus`로 정규화된 MotorStatus, E-Stop level과 수신 시각,
    Guard가 승인한 command/validation activity, validation 실패, owner lease 만료,
    STOP barrier ACK, 사용자의 safety reset 요청, 그리고 매 tick의 monotonic 시각과
    전이 경계를 표시할 system/source 시각을 받는다.

③ 출력
    :class:`SafetySnapshot`으로 INIT·READY·RUN·HOLD·SAFE·FAULT·ESTOP·RESET 중 현재
    상태, 전이 epoch, 이유, timeout, fault code를 제공하고, 요청형 API는 승인 여부와
    이유를 반환한다. 코어는 ROS 메시지를 publish하거나 hardware 명령을 출력하지 않는다.

④ 주요 실행 흐름
    시작은 INIT이다. fresh MotorStatus와 비활성 E-Stop을 확인하면 READY, 첫 유효 명령은
    RUN으로 보낸다. 마지막 hardware-forwarded 명령에서 5000 ms가 지나면 HOLD,
    총 10000 ms가 지나면 SAFE다. HOLD 중 Guard가 검증한 activity가 최대 100 ms 간격으로
    300 ms 연속되면 RUN으로 회복한다. STOP은 Guard의 차단 ACK 뒤 RESET으로 들어가며,
    최소 500 ms 뒤 fresh torque-off 증거가 있으면 READY가 된다. 센서/heartbeat 이상은
    FAULT 또는 최우선 ESTOP으로 가고, SAFE action이나 RESET의 3000 ms deadline 실패도
    FAULT로 닫힌다.

⑤ 사용/실행 방법
    보통 ``safety_manager.py``의 ROS 노드가 이 클래스를 생성하고 callback마다 update/on
    메서드, 주기적으로 :meth:`tick`, 발행 직전에 :meth:`snapshot`을 호출한다. 코어만
    시험할 때는 명시적인 ``started_ns``와 필요시 :class:`SafetyLimits`를 넣고, 테스트가
    소유한 monotonic nanosecond 값을 각 메서드에 전달한다. 이 파일 자체는 실행 파일이
    아니며 별도 event loop를 만들지 않는다.

⑥ 책임 경계와 하지 않는 일
    이 코어는 "어떤 안전 상태여야 하는가"와 "어떤 fresh 증거가 필요한가"만 결정한다.
    실제 안전 자세 생성·torque-off 수행·모터 register 쓰기는 ``thing_hardware``가,
    raw command 검증은 Command Guard가 담당한다. SAFE/RESET 완료도 명령을 직접
    실행해서가 아니라
    진입 뒤 MotorStatus가 보고한 결과로만 인정한다. 입력 누락·역행 시각·오래된 cache를
    성공으로 추정하지 않고 제한 상태로 전이하는 fail-closed 원칙을 사용한다.
"""

from dataclasses import dataclass
from typing import Optional


INIT = 0
READY = 1
RUN = 2
HOLD = 3
SAFE = 4
FAULT = 5
ESTOP = 6
RESET = 7

_NS_PER_MS = 1_000_000
_MOTOR_COMMUNICATION_FAILURE_LIMIT = 3


@dataclass(frozen=True)
class SafetyLimits:
    """
    상태 전이 시간과 freshness limit의 변경 불가능한 묶음.

    5000 ms HOLD, 10000 ms SAFE, 300 ms heartbeat freshness를 포함한 값들이다.
    어댑터 parameter로 값을 받더라도 명세보다 느슨해지지 않게 ``__post_init__``에서
    타입·관계·상한/하한을 다시 검사한다. ``True``도 Python에서는 정수처럼 보이므로
    명시적으로 거부한다. 잘못된 설정으로 watchdog을 사실상 끄는 일을 막기 위함이다.
    """

    command_hold_ms: int = 5000
    command_safe_ms: int = 10000
    safe_action_timeout_ms: int = 3000
    recovery_stable_ms: int = 300
    recovery_max_gap_ms: int = 100
    reset_min_ms: int = 500
    reset_timeout_ms: int = 3000
    estop_release_ms: int = 500
    fault_clear_stable_ms: int = 1000
    hardware_status_timeout_ms: int = 300
    estop_input_timeout_ms: int = 300

    def __post_init__(self) -> None:
        positive = (
            'command_hold_ms',
            'command_safe_ms',
            'safe_action_timeout_ms',
            'recovery_stable_ms',
            'recovery_max_gap_ms',
            'reset_min_ms',
            'reset_timeout_ms',
            'estop_release_ms',
            'fault_clear_stable_ms',
            'hardware_status_timeout_ms',
            'estop_input_timeout_ms',
        )
        for name in positive:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f'{name} must be a positive integer')
        if self.command_hold_ms > 5000:
            raise ValueError('command_hold_ms cannot exceed 5000')
        if self.command_safe_ms > 10000:
            raise ValueError('command_safe_ms cannot exceed 10000')
        if self.command_safe_ms <= self.command_hold_ms:
            raise ValueError('command_safe_ms must exceed command_hold_ms')
        if self.safe_action_timeout_ms > 3000:
            raise ValueError('safe_action_timeout_ms cannot exceed 3000')
        if self.recovery_max_gap_ms > self.recovery_stable_ms:
            raise ValueError(
                'recovery_max_gap_ms cannot exceed recovery_stable_ms'
            )
        if self.recovery_stable_ms < 300:
            raise ValueError('recovery_stable_ms cannot be below 300')
        if self.recovery_stable_ms > 1000:
            raise ValueError('recovery_stable_ms cannot exceed 1000')
        if self.recovery_max_gap_ms > 100:
            raise ValueError('recovery_max_gap_ms cannot exceed 100')
        if self.reset_min_ms < 500:
            raise ValueError('reset_min_ms cannot be below 500')
        if self.reset_min_ms >= 3000:
            raise ValueError('reset_min_ms must be below 3000')
        if self.reset_timeout_ms > 3000:
            raise ValueError('reset_timeout_ms cannot exceed 3000')
        if self.reset_timeout_ms <= self.reset_min_ms:
            raise ValueError('reset_timeout_ms must exceed reset_min_ms')
        if self.estop_release_ms < 500:
            raise ValueError('estop_release_ms cannot be below 500')
        if self.estop_release_ms > 500:
            raise ValueError('estop_release_ms cannot exceed 500')
        if self.fault_clear_stable_ms < 1000:
            raise ValueError('fault_clear_stable_ms cannot be below 1000')
        if self.fault_clear_stable_ms > 1000:
            raise ValueError('fault_clear_stable_ms cannot exceed 1000')
        if self.hardware_status_timeout_ms > 300:
            raise ValueError('hardware_status_timeout_ms cannot exceed 300')
        if self.estop_input_timeout_ms > 300:
            raise ValueError('estop_input_timeout_ms cannot exceed 300')


@dataclass(frozen=True)
class HardwareStatus:
    """
    ROS ``MotorStatus``에서 안전 판단에 필요한 값만 복사한 불변 입력.

    ``received_ns``는 코어가 freshness/deadline에 쓰는 로컬 monotonic 수신 시각이고,
    ``stamp_ns``는 센서 표본이 생성된 system/source 시각이다. 두 시각을 함께 검사해야
    callback은 방금 실행됐지만 표본은 SAFE/RESET 진입 전에 만들어진 DDS cache인 경우를
    완료 증거로 잘못 인정하지 않는다. 실제 메시지 구조·NaN·ID 중복 검사는 어댑터가
    ``valid_measurement``와 ``invalid_reason``으로 정규화한다.
    """

    received_ns: int
    stamp_ns: int
    motor_count: int
    bus_communication_ok: bool
    all_motors_communication_ok: bool
    all_torque_off: bool
    over_current: bool
    over_temperature: bool
    valid_measurement: bool = True
    invalid_reason: str = 'invalid_hardware_status'


@dataclass(frozen=True)
class SafetySnapshot:
    """
    외부 발행과 테스트가 읽는 현재 안전 판정의 불변 snapshot.

    ``transition_epoch``는 실제 상태가 바뀔 때만 증가하는 내부 세대 번호다. 같은 상태의
    heartbeat와 새 전이를 구분해 wire stamp를 일정하게 유지하는 데 사용한다.
    """

    state: int
    transition_epoch: int
    reason: str
    command_timeout: bool
    fault_code: int


@dataclass(frozen=True)
class RequestResult:
    """reset 같은 요청의 승인 여부와 사람이 읽을 수 있는 결정 이유."""

    accepted: bool
    reason: str


class SafetyManagerCore:
    """
    8상태 안전 상태기와 watchdog deadline을 관리한다.

    상태별 의미와 대표 전이는 다음과 같다.

    * ``INIT``: 시작 또는 ``reset_safety`` 승인 뒤 재검사한다. 설정, torque-off 상태의
      MotorStatus, 비활성 E-Stop이 확인되면 ``READY``가 된다. safety reset 뒤에는 INIT
      진입보다 새 hardware/E-Stop 표본을 모두 요구해 이전 cache 재사용을 막는다.
    * ``READY``: 안전 조건은 만족했지만 아직 동작 명령이 없는 상태다. 첫 Guard-approved
      명령은 ``RUN``, Guard의 STOP barrier ACK는 ``RESET``으로 보낸다.
    * ``RUN``: 정상 명령 흐름이다. 마지막으로 hardware에 전달된 유효 명령 후 5000 ms가
      지나거나 owner lease가 만료되면 ``HOLD``다.
    * ``HOLD``: hardware command barrier가 닫힌 일시 정지다. 마지막 전달 명령 기준 총
      10000 ms가 되면 ``SAFE``다. 그 전에 Guard validation activity가 최대 100 ms
      간격으로 300 ms 연속되면 ``RUN``으로 복귀한다. 이 activity는 검증 증거일 뿐
      hardware에 전달된 명령으로 세지 않는다.
    * ``SAFE``: hardware 계층이 안전 자세/torque-off를 수행해야 하는 제한 상태다.
      진입 뒤 fresh MotorStatus의 전체 torque-off를 완료 증거로 삼고, 3000 ms 안에
      증거가 없으면 ``FAULT``다. 코어 자체는 자세를 생성하거나 모터에 쓰지 않는다.
    * ``FAULT``: malformed/stale MotorStatus, 통신 지속 실패, 과전류·과온, action timeout
      등 hardware/내부 이상 상태다. 원인이 1000 ms 안정적으로 해소된 뒤 사용자의
      ``reset_safety``가 승인되어야 ``INIT``으로 간다.
    * ``ESTOP``: 물리 E-Stop active 또는 E-Stop heartbeat 300 ms 손실 상태다. 모든
      상태보다 우선하며 다른 callback이 FAULT/SAFE로 낮출 수 없다. 비활성 heartbeat가
      500 ms 안정되고 필요한 hardware fault window도 끝난 뒤 safety reset이 가능하다.
    * ``RESET``: 정상 STOP 전용 action 상태다. Guard가 command latch를 먼저 닫았다는
      barrier ACK로만 READY/RUN/HOLD에서 들어온다. 최소 500 ms와 진입 후 fresh
      torque-off 표본을 만족하면 READY, 3000 ms를 넘기면 FAULT다.

    이름이 비슷하지만 ``RESET``과 ``reset_safety``는 반대 방향의 서로 다른 절차다.
    RESET은 정상 STOP 뒤 READY로 돌아가기 위한 상태이고, ``reset_safety``는
    SAFE/FAULT/ESTOP의 위험 원인이 해소됐음을 사용자가 확인한 뒤 INIT 재검사를 여는
    서비스다. 둘 다 실제 torque-off를 실행하지 않고 hardware의 fresh 결과를 기다린다.

    우선순위는 E-Stop 불명/active를 최상위로 둔다. ESTOP 동안 동시에 발견한 hardware
    fault도 별도 latch로 기억해, E-Stop만 해제했다고 1000 ms fault 안정 조건이 사라지지
    않게 한다. 이처럼 복구 증거가 부족하면 기존 제한 상태를 유지하는 것이 fail-closed다.
    """

    def __init__(
        self,
        limits: Optional[SafetyLimits] = None,
        *,
        started_ns: int,
        configuration_valid: bool = True,
    ) -> None:
        """
        초기 상태와 모든 독립 deadline/latch를 만든다.

        ``started_ns``와 이후 ``now_ns``는 같은 monotonic 시간축이어야 한다. 설정이
        검증되지 않았으면 INIT 자체는 유지하되 READY 조건을 영구히 만족시키지 않는다.
        """
        self._limits = limits or SafetyLimits()
        self._started_ns = started_ns
        self._configuration_valid = bool(configuration_valid)
        self._state = INIT
        self._transition_epoch = 0
        self._reason = (
            'startup'
            if self._configuration_valid
            else 'trip_limits_unvalidated'
        )
        self._command_timeout = False
        self._fault_code = 0
        self._hardware: Optional[HardwareStatus] = None
        # INIT은 단순히 "최근 cache가 건강한가"를 보지 않는다. INIT에 들어간 뒤 실제로
        # 새 입력을 다시 받았는지 확인해야 reset 전 cache 재사용을 막을 수 있다.
        self._init_entered_ns = started_ns
        self._init_state_stamp_ns = 0
        self._require_post_init_inputs = False
        # 서로 다른 위험 원인과 action generation의 시각을 별도 필드로 둔다. 하나의
        # 공개 enum만으로는 ESTOP 아래에 가려진 fault나 진입 후 fresh 증거를 보존할 수 없다.
        self._estop_active: Optional[bool] = None
        self._estop_received_ns: Optional[int] = None
        self._estop_release_started_ns: Optional[int] = None
        self._last_validated_command_ns: Optional[int] = None
        self._safe_action_started_ns: Optional[int] = None
        self._safe_action_entry_stamp_ns: Optional[int] = None
        self._recovery_started_ns: Optional[int] = None
        self._last_recovery_activity_ns: Optional[int] = None
        self._reset_entered_ns: Optional[int] = None
        self._reset_state_stamp_ns: Optional[int] = None
        self._fault_clear_started_ns: Optional[int] = None
        # published enum이 ESTOP이어도 동시에 발견한 hardware fault의 1000 ms 안정 조건을
        # 잃지 않는다. 상태 우선순위와 reset 전제조건은 서로 다른 정보다.
        self._fault_stability_required = False
        self._motor_communication_failures = 0
        self._bus_failure_started_ns: Optional[int] = None

    def snapshot(self) -> SafetySnapshot:
        """상태를 바꾸지 않고 현재 판정의 불변 복사본을 반환한다."""
        return SafetySnapshot(
            state=self._state,
            transition_epoch=self._transition_epoch,
            reason=self._reason,
            command_timeout=self._command_timeout,
            fault_code=self._fault_code,
        )

    def update_hardware_status(self, status: HardwareStatus) -> None:
        """
        새 motor heartbeat를 반영하고 즉시 판정 가능한 fault를 처리한다.

        측정 형식 오류·모터 수 오류·과전류·과온은 한 번만으로도 위험하므로 즉시
        FAULT다. 반면 현재 안전 계약은 일시적인 packet loss를 구분하여 모터별 통신
        실패 3회 연속 또는 bus 실패 300 ms 연속일 때 FAULT로 올린다.
        """
        hardware_gap = self._hardware_status_stale(status.received_ns)
        if (
            self._hardware is not None
            and status.received_ns < self._hardware.received_ns
        ):
            hardware_gap = True
        self._hardware = status
        if hardware_gap:
            self._fault_clear_started_ns = None
            self._transition_hardware_fault(
                'motor_status_stale',
                status.received_ns,
            )
            return
        if not status.valid_measurement:
            self._fault_clear_started_ns = None
            self._transition_hardware_fault(
                status.invalid_reason,
                status.received_ns,
            )
            return
        if status.motor_count != 7:
            self._fault_clear_started_ns = None
            self._transition_hardware_fault(
                'motor_count_invalid',
                status.received_ns,
            )
            return
        if (
            self._state == SAFE
            and self._safe_action_started_ns is not None
            and status.received_ns - self._safe_action_started_ns
            >= self._ms(self._limits.safe_action_timeout_ms)
        ):
            # executor가 지연되어 deadline tick보다 MotorStatus callback이 먼저 와도
            # 늦은 torque-off 표본이 timeout을 취소할 수 없게 callback에서 닫는다.
            self._transition_hardware_fault(
                'safe_action_timeout',
                status.received_ns,
            )
            return

        if status.bus_communication_ok:
            self._bus_failure_started_ns = None
        elif self._bus_failure_started_ns is None:
            self._bus_failure_started_ns = status.received_ns

        if status.all_motors_communication_ok:
            self._motor_communication_failures = 0
        else:
            self._motor_communication_failures += 1

        if not status.bus_communication_ok or not status.all_motors_communication_ok:
            self._fault_clear_started_ns = None
            if (
                self._motor_communication_failures
                >= _MOTOR_COMMUNICATION_FAILURE_LIMIT
            ):
                self._transition_hardware_fault(
                    'motor_communication_failed',
                    status.received_ns,
                )
            elif (
                self._bus_failure_started_ns is not None
                and status.received_ns - self._bus_failure_started_ns
                >= self._ms(self._limits.hardware_status_timeout_ms)
            ):
                self._transition_hardware_fault(
                    'bus_communication_failed',
                    status.received_ns,
                )
            return
        if status.over_current:
            self._fault_clear_started_ns = None
            self._transition_hardware_fault('over_current', status.received_ns)
            return
        if status.over_temperature:
            self._fault_clear_started_ns = None
            self._transition_hardware_fault(
                'over_temperature',
                status.received_ns,
            )
            return
        if (
            self._state == SAFE
            and self._safe_action_started_ns is not None
            and self._safe_action_entry_stamp_ns is not None
            and status.received_ns > self._safe_action_started_ns
            and status.stamp_ns > self._safe_action_entry_stamp_ns
            and status.all_torque_off
        ):
            # SAFE 진입 전 torque-off cache는 안전 자세 완료 증거로 재사용하지 않는다.
            # 진입 뒤 fresh MotorStatus에서 전체 torque-off가 확인돼야 3000 ms
            # action deadline을 닫는다.
            self._safe_action_started_ns = None
            self._safe_action_entry_stamp_ns = None
        self._update_fault_clear_window(status.received_ns)

    def _transition_hardware_fault(self, reason: str, now_ns: int) -> None:
        """
        Hardware fault 원인을 latch하고 E-Stop freshness 우선순위까지 함께 적용한다.

        같은 시점에 E-Stop heartbeat도 stale이면 낮은 FAULT를 먼저 노출하지 않고 ESTOP을
        선택한다. 다만 hardware fault 안정 조건은 남겨 후속 reset을 우회하지 못하게 한다.
        """
        self._fault_stability_required = True
        self._fault_clear_started_ns = None
        if self._estop_input_stale(now_ns):
            self._estop_release_started_ns = None
            self._transition(ESTOP, 'estop_input_stale')
            return
        self._transition(FAULT, reason)

    def update_estop(self, active: bool, received_ns: int) -> None:
        """
        새 E-Stop level heartbeat를 반영하고 active/stale이면 즉시 ESTOP으로 전이한다.

        최초 비활성 또는 active→inactive 표본부터 release 안정 window를 시작한다. 입력
        시각 역행이나 이전 heartbeat와 300 ms 이상 간격은 중간 상태를 알 수 없다는
        뜻이므로 release 진행을 버리고 ESTOP을 유지한다.
        """
        previous_active = self._estop_active
        input_gap = self._estop_input_stale(received_ns)
        if (
            self._estop_received_ns is not None
            and received_ns < self._estop_received_ns
        ):
            input_gap = True
        if input_gap:
            self._estop_release_started_ns = None
            self._fault_clear_started_ns = None
            self._transition(ESTOP, 'estop_input_stale')
        self._estop_active = bool(active)
        self._estop_received_ns = received_ns
        if active:
            self._estop_release_started_ns = None
            self._fault_clear_started_ns = None
            self._transition(ESTOP, 'estop_active')
            return
        if self._state == ESTOP and (
            previous_active is not False
            or self._estop_release_started_ns is None
        ):
            self._estop_release_started_ns = received_ns
        self._update_fault_clear_window(received_ns)

    def tick(
        self,
        now_ns: int,
        *,
        state_stamp_ns: Optional[int] = None,
    ) -> None:
        """
        입력이 오지 않을 때도 모든 freshness와 상태별 deadline을 닫는 주기 판정점.

        판정 순서는 E-Stop stale → MotorStatus stale/bus timeout → fault clear 갱신 →
        현재 상태 전이다. 위험 우선순위를 먼저 검사하므로 같은 tick에 RUN timeout과
        heartbeat 손실이 겹쳐도 더 높은 ESTOP/FAULT가 선택된다. 경계는 ``>=``이므로
        설정된 각 제한 시간에 도달한 순간 이미 timeout이다.
        """
        if state_stamp_ns is None:
            state_stamp_ns = now_ns
        if self._estop_input_stale(now_ns):
            self._estop_release_started_ns = None
            self._fault_clear_started_ns = None
            self._transition(ESTOP, 'estop_input_stale')
            return
        if self._hardware_status_stale(now_ns):
            self._transition_hardware_fault('motor_status_stale', now_ns)
            return
        if (
            self._bus_failure_started_ns is not None
            and now_ns - self._bus_failure_started_ns
            >= self._ms(self._limits.hardware_status_timeout_ms)
        ):
            self._transition_hardware_fault(
                'bus_communication_failed',
                now_ns,
            )
            return
        self._update_fault_clear_window(now_ns)

        if self._state == INIT:
            if (
                self._configuration_valid
                and self._hardware_ready_for_init()
                and self._estop_active is False
                and self._inputs_received_after_init_entry()
            ):
                self._transition(READY, 'init_checks_passed')
            return

        if self._state == RUN:
            if (
                self._last_validated_command_ns is not None
                and now_ns - self._last_validated_command_ns
                >= self._ms(self._limits.command_hold_ms)
            ):
                self._command_timeout = True
                self._transition(HOLD, 'command_timeout_hold')
            return

        if self._state == HOLD:
            if (
                self._last_validated_command_ns is not None
                and now_ns - self._last_validated_command_ns
                >= self._ms(self._limits.command_safe_ms)
            ):
                self._enter_safe(
                    now_ns,
                    'command_timeout_safe',
                    state_stamp_ns,
                )
            return

        if self._state == SAFE:
            if (
                self._safe_action_started_ns is not None
                and now_ns - self._safe_action_started_ns
                >= self._ms(self._limits.safe_action_timeout_ms)
            ):
                self._transition_hardware_fault('safe_action_timeout', now_ns)
            return

        if self._state == RESET:
            if (
                self._reset_entered_ns is not None
                and now_ns - self._reset_entered_ns
                >= self._ms(self._limits.reset_timeout_ms)
            ):
                self._transition(FAULT, 'reset_action_timeout')
                return
            if self._reset_complete(now_ns):
                self._command_timeout = False
                self._transition(READY, 'reset_completed')
                return

    def on_validated_command(
        self,
        now_ns: int,
        *,
        state_stamp_ns: Optional[int] = None,
    ) -> None:
        """
        hardware까지 전달된 Guard-approved 명령의 activity를 기록한다.

        READY의 첫 명령은 RUN을 열고 RUN의 후속 명령은 5000/10000 ms 기준시각을
        갱신한다. HOLD에서는 barrier를 우회해 hardware-forwarded로 간주하지 않고 복구
        validation 흐름으로만 처리한다. SAFE/FAULT/ESTOP/RESET 입력은 상태를 열지 않는다.
        """
        if state_stamp_ns is None:
            state_stamp_ns = now_ns
        if self._state == READY:
            self._last_validated_command_ns = now_ns
            self._command_timeout = False
            self._transition(RUN, 'first_valid_command')
            return
        if self._state == RUN:
            self._last_validated_command_ns = now_ns
            self._command_timeout = False
            return
        if self._state != HOLD:
            return
        self._record_hold_activity(now_ns, state_stamp_ns)

    def on_validated_activity(
        self,
        now_ns: int,
        *,
        state_stamp_ns: Optional[int] = None,
    ) -> None:
        """
        HOLD 중 검증됐지만 hardware에는 전달하지 않은 activity를 기록한다.

        executor 지연으로 RUN의 5000 ms deadline 직후 이 callback이 먼저 실행된 경우에도
        먼저 HOLD를 확정한 뒤 recovery activity로 취급한다. 이 activity로 원래의
        10000 ms SAFE deadline을 연장하지 않는다.
        """
        if state_stamp_ns is None:
            state_stamp_ns = now_ns
        if self._state == RUN:
            if (
                self._last_validated_command_ns is None
                or now_ns - self._last_validated_command_ns
                < self._ms(self._limits.command_hold_ms)
            ):
                return
            self._command_timeout = True
            self._transition(HOLD, 'command_timeout_hold')
        if self._state != HOLD:
            return
        self._record_hold_activity(now_ns, state_stamp_ns)

    def _record_hold_activity(
        self,
        now_ns: int,
        state_stamp_ns: int,
    ) -> None:
        """최대 100 ms gap의 연속 activity를 모아 300 ms HOLD 복구를 판정한다."""
        # HOLD 자동복귀는 "명령 한 번"이 아니라 최대 gap 100 ms인 300 ms 연속 흐름을
        # 요구한다. 간헐 입력으로 10000 ms SAFE 마감시각을 연장하지 않는다.
        if (
            self._last_validated_command_ns is not None
            and now_ns - self._last_validated_command_ns
            >= self._ms(self._limits.command_safe_ms)
        ):
            self._enter_safe(
                now_ns,
                'command_timeout_safe',
                state_stamp_ns,
            )
            return

        gap_ns = None
        if self._last_recovery_activity_ns is not None:
            gap_ns = now_ns - self._last_recovery_activity_ns
        if (
            self._recovery_started_ns is None
            or gap_ns is None
            or gap_ns < 0
            or gap_ns > self._ms(self._limits.recovery_max_gap_ms)
        ):
            self._recovery_started_ns = now_ns
        self._last_recovery_activity_ns = now_ns
        if (
            now_ns - self._recovery_started_ns
            >= self._ms(self._limits.recovery_stable_ms)
        ):
            self._last_validated_command_ns = now_ns
            self._command_timeout = False
            self._transition(RUN, 'command_stream_recovered')

    def on_validation_failed(self, now_ns: int) -> bool:
        """
        HOLD 복구 중 Guard 실패가 끼면 300 ms 연속 activity window를 닫는다.

        성공 event의 시간 간격만 보면 그 사이의 실패를 놓칠 수 있다. 실패 event도 같은
        ordered validation 결과 흐름으로 받아 즉시 window를 지우며, HOLD 밖에서는 상태를
        바꾸지 않았음을 ``False``로 알린다.
        """
        del now_ns
        if self._state != HOLD:
            return False
        self._recovery_started_ns = None
        self._last_recovery_activity_ns = None
        return True

    def on_control_stop_requested(
        self,
        *,
        now_ns: int,
        state_stamp_ns: int,
    ) -> bool:
        """
        Guard가 STOP latch를 닫았다는 ACK 뒤 정상 제어 RESET에 진입한다.

        READY/RUN/HOLD에서만 허용하며, raw 사용자 STOP보다 Guard ACK를 경계로 삼아
        RESET 공개 전에 command 통과가 이미 막혔음을 보장한다. SAFE/FAULT/ESTOP은
        정상 STOP으로 낮추지 않고 전용 ``reset_safety`` 조건을 요구한다.
        """
        if self._state not in (READY, RUN, HOLD):
            return False
        self._command_timeout = False
        self._transition(RESET, 'control_stop_requested')
        self._reset_entered_ns = now_ns
        self._reset_state_stamp_ns = state_stamp_ns
        return True

    def on_owner_lease_expired(self, now_ns: int) -> bool:
        """
        RUN의 제어권 heartbeat 상실을 command watchdog보다 먼저 HOLD로 전파한다.

        lease 만료는 유효 command가 최근이어도 제어 주체를 신뢰할 수 없다는 뜻이다.
        다른 상태를 임의로 변경하지 않으며 실제 전이 여부를 bool로 반환한다.
        """
        del now_ns
        if self._state != RUN:
            return False
        self._command_timeout = True
        self._transition(HOLD, 'owner_lease_expired')
        return True

    def request_safety_reset(
        self,
        now_ns: int,
        *,
        state_stamp_ns: Optional[int] = None,
    ) -> RequestResult:
        """
        SAFE/FAULT/ESTOP 원인 해소 뒤 새 INIT 검사 cycle을 시작한다.

        승인 순간을 새 INIT generation의 경계로 저장한다. 이후 READY 전이는 이 경계
        뒤에 새로 수신한 MotorStatus와 E-Stop heartbeat가 둘 다 있어야 가능하다.
        """
        if self._state not in (SAFE, FAULT, ESTOP):
            return RequestResult(False, 'safety_reset_not_allowed')
        if self._estop_input_stale(now_ns):
            return RequestResult(False, 'estop_input_stale')
        if self._estop_active is not False:
            return RequestResult(False, 'estop_still_active')
        if self._state == ESTOP:
            if self._estop_release_started_ns is None:
                return RequestResult(False, 'estop_release_not_stable')
            if (
                now_ns - self._estop_release_started_ns
                < self._ms(self._limits.estop_release_ms)
            ):
                return RequestResult(False, 'estop_release_not_stable')
        if self._hardware_status_stale(now_ns):
            return RequestResult(False, 'motor_status_stale')
        if not self._hardware_ready_for_init():
            return RequestResult(False, 'fault_still_active')
        if self._state in (SAFE, FAULT) or self._fault_stability_required:
            if self._fault_clear_started_ns is None:
                return RequestResult(False, 'fault_clear_not_stable')
            if (
                now_ns - self._fault_clear_started_ns
                < self._ms(self._limits.fault_clear_stable_ms)
            ):
                return RequestResult(False, 'fault_clear_not_stable')

        self._fault_code = 0
        self._fault_stability_required = False
        self._command_timeout = False
        self._transition(INIT, 'safety_reset_accepted')
        self._init_entered_ns = now_ns
        self._init_state_stamp_ns = (
            now_ns if state_stamp_ns is None else state_stamp_ns
        )
        self._require_post_init_inputs = True
        return RequestResult(True, 'safety_reset_accepted')

    def force_fault(self, reason: str, fault_code: int = 1) -> None:
        """내부 진단이 발견한 fault를 안정화 latch와 함께 강제로 기록한다."""
        self._fault_code = fault_code
        self._fault_stability_required = True
        self._fault_clear_started_ns = None
        self._transition(FAULT, reason)

    def _enter_safe(
        self,
        now_ns: int,
        reason: str,
        state_stamp_ns: int,
    ) -> None:
        """
        SAFE action generation의 두 시각 경계를 저장한 뒤 SAFE로 전이한다.

        monotonic 진입 시각은 3000 ms deadline/로컬 수신 순서에, system stamp는 센서
        표본 생성 순서에 쓴다. 둘 다 진입 후인 MotorStatus만 torque-off 완료 증거다.
        """
        self._safe_action_started_ns = now_ns
        self._safe_action_entry_stamp_ns = state_stamp_ns
        self._transition(SAFE, reason)

    def _update_fault_clear_window(self, now_ns: int) -> None:
        """
        Reset 전 필요한 연속 healthy window의 시작 또는 중단을 관리한다.

        ESTOP 아래에 hardware fault가 latch된 경우에는 E-Stop release 500 ms와 hardware
        healthy 1000 ms를 독립적으로 센다. 하나의 원인이 다시 나빠지면 해당 안정 증거를
        버려 순간적인 정상 표본 한 개로 reset되지 않게 한다.
        """
        if (
            self._state not in (SAFE, FAULT)
            and not self._fault_stability_required
        ):
            return
        hardware_healthy = bool(
            not self._hardware_status_stale(now_ns)
            and self._hardware_ready_for_init()
        )
        if self._fault_stability_required:
            # ESTOP 해제 500 ms와 hardware fault clear 1000 ms는 독립 window다.
            healthy = hardware_healthy
        else:
            healthy = bool(
                not self._estop_input_stale(now_ns)
                and self._estop_active is False
                and hardware_healthy
            )
        if not healthy:
            self._fault_clear_started_ns = None
            return
        if self._fault_clear_started_ns is None:
            self._fault_clear_started_ns = now_ns

    def _reset_complete(self, now_ns: int) -> bool:
        """
        정상 STOP의 RESET action이 fresh torque-off 증거로 완료됐는지 확인한다.

        최소 500 ms 전에는 완료하지 않고, RESET 진입 후 로컬 수신이면서 source stamp도
        진입 stamp보다 새롭고 3000 ms timeout 안에 수신된 건강한 7-motor 표본만 받는다.
        실제 torque-off 수행은 hardware 책임이며 이 함수는 보고된 결과만 검증한다.
        """
        if self._reset_entered_ns is None or self._reset_state_stamp_ns is None:
            return False
        if now_ns - self._reset_entered_ns < self._ms(self._limits.reset_min_ms):
            return False
        status = self._hardware
        return bool(
            status is not None
            and status.received_ns >= self._reset_entered_ns
            and status.received_ns
            < self._reset_entered_ns + self._ms(self._limits.reset_timeout_ms)
            and status.stamp_ns > self._reset_state_stamp_ns
            and status.valid_measurement
            and status.motor_count == 7
            and status.bus_communication_ok
            and status.all_motors_communication_ok
            and status.all_torque_off
        )

    def _hardware_ready_for_init(self) -> bool:
        """INIT/reset gate에 필요한 7-motor 통신·torque-off·trip 상태를 검사한다."""
        status = self._hardware
        return bool(
            status is not None
            and status.valid_measurement
            and status.motor_count == 7
            and status.bus_communication_ok
            and status.all_motors_communication_ok
            and status.all_torque_off
            and not status.over_current
            and not status.over_temperature
        )

    def _inputs_received_after_init_entry(self) -> bool:
        """현재 INIT 진입 뒤 새 hardware·E-Stop 입력을 모두 받았는지 확인한다."""
        if not self._require_post_init_inputs:
            return True
        status = self._hardware
        return bool(
            status is not None
            # strict ``>``는 reset 요청과 같은 tick 직전에 받은 cache도 배제한다.
            and status.received_ns > self._init_entered_ns
            and status.stamp_ns > self._init_state_stamp_ns
            and self._estop_received_ns is not None
            and self._estop_received_ns > self._init_entered_ns
        )

    def _estop_input_stale(self, now_ns: int) -> bool:
        """시작 또는 마지막 E-Stop heartbeat부터 허용된 300 ms가 끝났는지 판정한다."""
        if self._estop_received_ns is None:
            return (
                now_ns - self._started_ns
                >= self._ms(self._limits.estop_input_timeout_ms)
            )
        return (
            now_ns - self._estop_received_ns
            >= self._ms(self._limits.estop_input_timeout_ms)
        )

    def _hardware_status_stale(self, now_ns: int) -> bool:
        """시작 또는 마지막 MotorStatus 수신부터 허용된 300 ms가 끝났는지 판정한다."""
        if self._hardware is None:
            return (
                now_ns - self._started_ns
                >= self._ms(self._limits.hardware_status_timeout_ms)
            )
        return (
            now_ns - self._hardware.received_ns
            >= self._ms(self._limits.hardware_status_timeout_ms)
        )

    def _transition(self, state: int, reason: str) -> None:
        """
        공통 latch 정리와 ESTOP 우선순위를 적용해 상태를 원자적으로 바꾼다.

        실제 enum이 달라질 때만 epoch를 증가시킨다. 같은 상태의 새 reason은 기록하되 새
        전이 세대로 위장하지 않는다. 상태를 벗어날 때 그 상태 전용 deadline을 지워 이전
        action의 완료 표본이 다음 action에 재사용되지 않게 한다.
        """
        if state in (SAFE, FAULT) and (
            state != self._state or reason != self._reason
        ):
            # SAFE/FAULT가 ESTOP에 가려져도 해당 원인의 1000 ms 안정 gate를 보존한다.
            self._fault_stability_required = True
            self._fault_clear_started_ns = None
        # ESTOP은 가장 높은 우선순위다. 명시적 safety reset으로 INIT에 들어가는 경우를
        # 제외하면 다른 callback이 ESTOP을 FAULT/SAFE 등으로 낮출 수 없다.
        if self._state == ESTOP and state not in (ESTOP, INIT):
            return
        if state == self._state:
            self._reason = reason
            return
        self._state = state
        self._transition_epoch += 1
        self._reason = reason
        self._recovery_started_ns = None
        self._last_recovery_activity_ns = None
        if state != RESET:
            self._reset_entered_ns = None
            self._reset_state_stamp_ns = None
        if state not in (RUN, HOLD):
            self._last_validated_command_ns = None
        if state != ESTOP:
            self._estop_release_started_ns = None
        if state not in (SAFE, FAULT):
            self._fault_clear_started_ns = None
        if state != SAFE:
            self._safe_action_started_ns = None
            self._safe_action_entry_stamp_ns = None

    @staticmethod
    def _ms(value: int) -> int:
        """정수 millisecond를 부동소수점 오차 없이 nanosecond로 변환한다."""
        return value * _NS_PER_MS
