"""
ROS 없이도 시험할 수 있는 8상태 안전 정책 코어.

이 파일은 안전 판단만 담당한다. ROS topic을 받거나 publish하는 일은
``safety_manager.py``가 담당하고, 이 코어는 전달받은 시각과 상태만으로 같은 입력에
항상 같은 결과를 낸다. 쉽게 말해 adapter가 센서와 배선을 맡고, core가 비상 규칙표를
판정한다. 둘을 분리해야 callback 순서나 실제 시계 없이도 상태 전이를 단위 테스트할 수
있다.

V6.5는 V6.4의 7상태에 사용자가 후속 결정한 ``RESET=7``을 추가하고, HOLD에서 Guard가
검증한 activity가 300 ms 연속되면 RUN으로 자동 복귀하는 정책을 사용한다.
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
    V6.5 전이 시간·trip limit.

    parameter로 값을 받더라도 명세보다 느슨해지지 않게 ``__post_init__``에서 상한과
    하한을 다시 검사한다. 잘못된 설정으로 watchdog을 사실상 끄는 일을 막기 위함이다.
    """

    command_hold_ms: int = 300
    command_safe_ms: int = 1000
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
        if self.command_hold_ms > 300:
            raise ValueError('command_hold_ms cannot exceed 300')
        if self.command_safe_ms > 1000:
            raise ValueError('command_safe_ms cannot exceed 1000')
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
    """ROS ``MotorStatus``에서 안전 판단에 필요한 값만 복사한 입력."""

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
    """Current state plus an internal-only transition epoch."""

    state: int
    transition_epoch: int
    reason: str
    command_timeout: bool
    fault_code: int


@dataclass(frozen=True)
class RequestResult:
    accepted: bool
    reason: str


class SafetyManagerCore:
    """
    8상태 안전 상태기와 watchdog deadline을 관리한다.

    상태는 INIT·READY·RUN·HOLD·SAFE·FAULT·ESTOP·RESET이다.

    INIT은 시작/안전 reset 재검사, READY는 첫 명령 대기, RUN은 정상 명령 흐름,
    HOLD는 300 ms 공백 또는 lease 만료, SAFE는 총 1000 ms 명령 공백, FAULT는
    모터·내부 오류, ESTOP은 물리 E-Stop 또는 입력 heartbeat 손실, RESET은 정상 STOP
    후 torque-off 확인 상태다. 알 수 없는 입력은 동작 허용이 아니라 더 안전한 상태로
    보내는 fail-closed 원칙을 따른다.
    """

    def __init__(
        self,
        limits: Optional[SafetyLimits] = None,
        *,
        started_ns: int,
        configuration_valid: bool = True,
    ) -> None:
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
        FAULT다. 반면 V6.5의 통신 정책은 일시적인 packet loss를 구분하여 모터별 통신
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
        self._fault_stability_required = True
        self._fault_clear_started_ns = None
        if self._estop_input_stale(now_ns):
            self._estop_release_started_ns = None
            self._transition(ESTOP, 'estop_input_stale')
            return
        self._transition(FAULT, reason)

    def update_estop(self, active: bool, received_ns: int) -> None:
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
        """hardware까지 전달된 Guard-approved 명령의 activity를 기록한다."""
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
        """HOLD 중 검증됐지만 hardware에는 전달하지 않은 activity를 기록한다."""
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
        # HOLD 자동복귀는 "명령 한 번"이 아니라 최대 gap 100 ms인 300 ms 연속 흐름을
        # 요구한다. 간헐 입력으로 1000 ms SAFE 마감시각을 연장하지 않는다.
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
        """HOLD 복구 중 Guard 실패가 끼면 300 ms 연속 activity window를 닫는다."""
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
        """Guard가 STOP latch를 닫았다는 ACK 뒤 정상 제어 RESET에 진입한다."""
        if self._state not in (READY, RUN, HOLD):
            return False
        self._command_timeout = False
        self._transition(RESET, 'control_stop_requested')
        self._reset_entered_ns = now_ns
        self._reset_state_stamp_ns = state_stamp_ns
        return True

    def on_owner_lease_expired(self, now_ns: int) -> bool:
        """RUN의 제어권 heartbeat 상실을 command watchdog보다 먼저 HOLD로 전파한다."""
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
        self._safe_action_started_ns = now_ns
        self._safe_action_entry_stamp_ns = state_stamp_ns
        self._transition(SAFE, reason)

    def _update_fault_clear_window(self, now_ns: int) -> None:
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
        return value * _NS_PER_MS
