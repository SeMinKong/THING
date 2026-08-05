"""
ROS 2 입출력을 8상태 안전 정책 코어에 연결하는 어댑터 사용 안내.

① 역할
    이 노드는 ROS 메시지와 파라미터를 검증 가능한 내부 값으로 바꾸고,
    :class:`SafetyManagerCore`가 내린 결정을 권위 있는 ``SafetyState``로 발행한다.
    ROS callback·QoS·시계 처리는 이 파일, 상태 전이 규칙은 코어 파일의 책임이다.

② 입력
    ``/thing/motor_status``의 모터 heartbeat, ``/thing/estop``의 E-Stop level,
    Guard가 승인한 ``/thing/command``와 ``/thing/command/validation_result``,
    ``/thing/control_state``의 owner lease 상태, 그리고 Guard가 STOP 차단을 끝냈음을
    알리는 ``/thing/control/stop_barrier_ack``를 받는다. 사용자가 위험 원인 해소 뒤
    호출하는 ``/thing/reset_safety`` 서비스와 안전 시간/임계값 파라미터도 입력이다.

③ 출력
    현재 8상태, 원인, fault code, command timeout 및 최근 hardware/E-Stop 요약을
    ``/thing/safety_state``로 발행한다. reset 서비스에는 승인 여부와 거절 이유를
    반환한다. 이 출력은 "허용 여부/안전 상태"이지 모터에 쓸 자세 명령이 아니다.

④ 주요 실행 흐름
    callback이 메시지 형식과 freshness를 확인하고 monotonic 수신 시각을 붙인다
    → 코어 입력 메서드를 호출한다 → 20 ms 기본 tick이 5000/10000 ms watchdog과
    action deadline을 판정한다 → 전이가 생기면 즉시, 전이가 없어도 100 ms 기본
    주기로 SafetyState를 heartbeat 발행한다. 상태는 INIT, READY, RUN, HOLD, SAFE,
    FAULT, ESTOP, RESET이며 자세한 전이 조건은 코어의 클래스 설명을 참고한다.

⑤ 사용/실행 방법
    ROS 환경과 workspace를 source한 뒤 패키지에 등록된 실행 파일을
    ``ros2 run thing_control safety_manager``로 실행한다. 코드에서 사용할 때는
    :func:`main`이 ``rclpy.init``·spin·shutdown 생명주기를 처리한다. 운영 전에는
    실제 부하 시험으로 보정한 trip limit을 넣고 ``trip_limits_validated=true``로
    명시해야 INIT에서 READY로 갈 수 있다.

⑥ 책임 경계와 하지 않는 일
    이 노드는 안전 상태를 판정하고 발행할 뿐, 실제 안전 자세를 생성하거나 torque를
    끄거나 모터 register에 쓰지 않는다. 안전 자세 생성·torque-off 수행·최종 motor
    write와 그 결과를 MotorStatus로 보고하는 일은 ``thing_hardware``의 책임이다.
    raw command의 형식·권한·시각·순서·값 범위·변화율 검사는 Guard의 책임이며,
    이 노드는 Guard가 보낸 validation 결과를 신뢰 경계의 입력으로 사용한다.
    Guard는 역기구학·충돌 검사·안전 자세 생성을 담당하지 않는다. 증거가 없거나
    heartbeat가 오래되면 동작을 추정해 허용하지 않고 더 제한적인 상태로 가는
    fail-closed 정책을 따른다.
"""

import math
from typing import Optional

import rclpy
from rclpy.clock import Clock, ClockType
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from std_msgs.msg import Bool, UInt64
from std_srvs.srv import Trigger

from thing_control.safety_manager_core import (
    ESTOP,
    FAULT,
    HardwareStatus,
    SafetyLimits,
    SafetyManagerCore,
)
from thing_interfaces.msg import (
    ControlState,
    HandCommand,
    MotorStatus,
    SafetyState,
)


_STATE_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)
_COMMAND_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
)
_INTERNAL_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
    reliability=ReliabilityPolicy.RELIABLE,
)
_HEARTBEAT_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.VOLATILE,
)
_MOTOR_STATUS_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=5,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.VOLATILE,
)
_MOTOR_STAMP_FUTURE_TOLERANCE_NS = 100_000_000


class SafetyManager(Node):
    """
    센서 heartbeat와 제어 activity를 코어에 전달하는 ROS 2 노드.

    초보자는 이 클래스를 "번역기와 배선도"로 보면 된다. callback은 ROS 메시지를
    안전 코어의 입력으로 번역하고, 코어 snapshot을 다시 SafetyState 메시지로 만든다.
    상태를 직접 결정하지 않기 때문에 정책 변경은 ``SafetyManagerCore``에서 해야 한다.
    """

    def __init__(self, parameter_overrides=None) -> None:
        super().__init__(
            'safety_manager',
            parameter_overrides=parameter_overrides,
        )
        # 경과 시간/watchdog에는 시스템 시각 보정의 영향을 받지 않는 steady clock을,
        # 메시지 source stamp와 전이 세대 비교에는 system clock을 사용한다.
        self._steady_clock = Clock(clock_type=ClockType.STEADY_TIME)
        self._system_clock = Clock(clock_type=ClockType.SYSTEM_TIME)

        # 어댑터와 코어가 모두 범위를 검사한다. launch 설정 하나로 5000/10000 ms 같은
        # 안전 상한을 느슨하게 만들 수 없도록 이중으로 fail-closed 검증한다.
        limits = SafetyLimits(
            command_hold_ms=self._positive_parameter('command_hold_ms', 5000),
            command_safe_ms=self._positive_parameter('command_safe_ms', 10000),
            safe_action_timeout_ms=self._positive_parameter(
                'safe_action_timeout_ms', 3000
            ),
            recovery_stable_ms=self._positive_parameter(
                'recovery_stable_ms', 300
            ),
            recovery_max_gap_ms=self._positive_parameter(
                'recovery_max_gap_ms', 100
            ),
            reset_min_ms=self._positive_parameter('reset_min_ms', 500),
            reset_timeout_ms=self._positive_parameter('reset_timeout_ms', 3000),
            estop_release_ms=self._positive_parameter('estop_release_ms', 500),
            fault_clear_stable_ms=self._positive_parameter(
                'fault_clear_stable_ms', 1000
            ),
            hardware_status_timeout_ms=self._positive_parameter(
                'hardware_status_timeout_ms', 300
            ),
            estop_input_timeout_ms=self._positive_parameter(
                'estop_input_timeout_ms', 300
            ),
        )
        self._limits = limits
        self._tick_period_ms = self._positive_parameter(
            'tick_period_ms', 20
        )
        if self._tick_period_ms > 20:
            raise ValueError('tick_period_ms must be an integer in [1, 20]')
        self._state_publish_period_ms = self._positive_parameter(
            'state_publish_period_ms', 100
        )
        if self._state_publish_period_ms > 100:
            raise ValueError(
                'state_publish_period_ms must be an integer in [1, 100]'
            )
        self._trip_limits_validated = self.declare_parameter(
            'trip_limits_validated', False
        ).value
        if not isinstance(self._trip_limits_validated, bool):
            raise ValueError('trip_limits_validated must be a boolean')
        self._max_current_ampere = self._positive_number_parameter(
            'max_current_ampere', 0.145
        )
        self._max_temperature_celsius = self._positive_number_parameter(
            'max_temperature_celsius', 70.0
        )

        # 검증되지 않은 전류/온도 limit이면 노드는 뜨더라도 READY 진입은 막힌다.
        self._core = SafetyManagerCore(
            limits,
            started_ns=self._now_ns(),
            configuration_valid=self._trip_limits_validated,
        )
        self._last_stop_generation = 0
        self._last_published_epoch = -1
        self._wire_state_epoch = -1
        self._wire_state_stamp_ns = -1
        self._last_motor_communication_ok = False
        self._last_over_current = False
        self._last_over_temperature = False
        self._last_estop_active = False
        self._fault_publish_not_before_ns: Optional[int] = None

        # SafetyState는 늦게 참가한 소비자도 마지막 상태를 받도록 transient-local이다.
        # 반대로 센서 heartbeat는 과거 durable 표본의 재생을 freshness로 인정하지 않는다.
        self.safety_state_publisher = self.create_publisher(
            SafetyState,
            '/thing/safety_state',
            _STATE_QOS,
        )
        self.reset_safety_service = self.create_service(
            Trigger,
            '/thing/reset_safety',
            self.handle_reset_safety,
        )
        # raw STOP 요청이 아니라 Guard의 barrier ACK를 받는다. 이 ACK는 Guard가 먼저
        # command 통과 latch를 닫았다는 인과적 증거이므로, 이후에만 RESET에 들어간다.
        self.stop_subscription = self.create_subscription(
            UInt64,
            '/thing/control/stop_barrier_ack',
            self.handle_stop_requested,
            _INTERNAL_QOS,
        )
        self.motor_status_subscription = self.create_subscription(
            MotorStatus,
            '/thing/motor_status',
            self._on_motor_status,
            _MOTOR_STATUS_QOS,
        )
        self.estop_subscription = self.create_subscription(
            Bool,
            '/thing/estop',
            self._on_estop,
            _HEARTBEAT_QOS,
        )
        self.command_subscription = self.create_subscription(
            HandCommand,
            '/thing/command',
            self._on_validated_command,
            _COMMAND_QOS,
        )
        self.validation_result_subscription = self.create_subscription(
            Bool,
            '/thing/command/validation_result',
            self._on_validation_result,
            _INTERNAL_QOS,
        )
        self.control_state_subscription = self.create_subscription(
            ControlState,
            '/thing/control_state',
            self._on_control_state,
            _STATE_QOS,
        )

        # 두 timer 모두 steady clock 기반이다. tick은 deadline을 판정하고 state timer는
        # 전이가 없어도 downstream이 freshness를 확인할 heartbeat를 보낸다.
        self._tick_timer = self.create_timer(
            self._tick_period_ms / 1000.0,
            self._on_tick,
            clock=self._steady_clock,
        )
        self._state_timer = self.create_timer(
            self._state_publish_period_ms / 1000.0,
            self.publish_safety_state,
            clock=self._steady_clock,
        )
        self.publish_safety_state()
        if not self._trip_limits_validated:
            self.get_logger().error(
                'Current/temperature trip limits are not load-test validated; '
                'READY transition is calibration-gated.'
            )
        self.get_logger().info('Safety manager started.')

    @property
    def current_state(self) -> int:
        """테스트와 진단용 현재 상태 enum 값을 반환한다."""
        return self._core.snapshot().state

    @property
    def command_timeout(self) -> bool:
        """명령/owner activity 손실로 HOLD 계열 timeout이 발생했는지 반환한다."""
        return self._core.snapshot().command_timeout

    def _now_ns(self) -> int:
        """Deadline 계산에만 쓰는 monotonic nanosecond 시각을 반환한다."""
        return self._steady_clock.now().nanoseconds

    def _on_motor_status(self, message: MotorStatus) -> None:
        """
        MotorStatus의 형식·source stamp·측정값을 검사해 코어 입력으로 바꾼다.

        정확히 7개 모터, 중복 없는 ID, 모든 실수값의 유한성, bus/모터 통신 상태를
        확인한다. 이 callback이 최근에 실행됐다는 사실만으로 표본이 fresh한 것은
        아니므로 센서 source stamp도 별도로 검사한다. 실패한 검사를 정상값으로 보정하지
        않고 ``valid_measurement=False``로 전달하는 것이 fail-closed 경계다.
        """
        # source stamp는 센서가 측정한 시스템 시각, received_ns는 이 노드가 callback을
        # 받은 monotonic 시각이다. 둘 다 봐야 오래된 DDS queue와 로컬 통신 단절을
        # 구분하면서도 wall-clock 변경에 watchdog이 흔들리지 않는다.
        received_ns = self._now_ns()
        system_now_ns = self._system_clock.now().nanoseconds
        stamp_ns = self._stamp_to_ns(message.header.stamp)
        canonical_stamp = stamp_ns is not None
        normalized_stamp_ns = stamp_ns if stamp_ns is not None else 0
        motor_ids = [int(motor.motor_id) for motor in message.motors]
        unique_motor_ids = len(set(motor_ids)) == len(motor_ids)
        finite_measurements = all(
            math.isfinite(float(motor.goal_position_rad))
            and math.isfinite(float(motor.present_position_rad))
            and math.isfinite(float(motor.velocity_rad_s))
            and math.isfinite(float(motor.current_ampere))
            and math.isfinite(float(motor.voltage_volt))
            and math.isfinite(float(motor.temperature_celsius))
            for motor in message.motors
        )
        stamp_age_ns = system_now_ns - normalized_stamp_ns
        stamp_valid = bool(
            canonical_stamp
            and normalized_stamp_ns > 0
            and stamp_age_ns
            < self._limits.hardware_status_timeout_ms * 1_000_000
            and stamp_age_ns >= -_MOTOR_STAMP_FUTURE_TOLERANCE_NS
        )
        valid_measurement = bool(
            len(message.motors) == 7
            and unique_motor_ids
            and finite_measurements
            and stamp_valid
        )
        invalid_reason = 'invalid_hardware_status'
        if len(message.motors) != 7:
            invalid_reason = 'motor_count_invalid'
        elif not unique_motor_ids:
            invalid_reason = 'duplicate_motor_ids'
        elif not finite_measurements:
            invalid_reason = 'non_finite_motor_telemetry'
        elif not canonical_stamp:
            invalid_reason = 'motor_status_stamp_non_canonical'
        elif not stamp_valid:
            invalid_reason = 'motor_status_stamp_invalid'

        communication_ok = bool(
            valid_measurement
            and message.bus_communication_ok
            and all(motor.communication_ok for motor in message.motors)
        )
        over_current = (
            any(
                abs(float(motor.current_ampere)) > self._max_current_ampere
                for motor in message.motors
            )
            if finite_measurements else True
        )
        over_temperature = (
            any(
                float(motor.temperature_celsius)
                > self._max_temperature_celsius
                for motor in message.motors
            )
            if finite_measurements else True
        )
        self._last_motor_communication_ok = communication_ok
        self._last_over_current = over_current
        self._last_over_temperature = over_temperature
        self._core.update_hardware_status(HardwareStatus(
            received_ns=received_ns,
            stamp_ns=normalized_stamp_ns,
            motor_count=len(message.motors),
            bus_communication_ok=bool(message.bus_communication_ok),
            all_motors_communication_ok=bool(
                message.motors
                and all(motor.communication_ok for motor in message.motors)
            ),
            all_torque_off=(
                len(message.motors) == 7
                and all(not motor.torque_enabled for motor in message.motors)
            ),
            over_current=over_current,
            over_temperature=over_temperature,
            valid_measurement=valid_measurement,
            invalid_reason=invalid_reason,
        ))
        snapshot = self._core.snapshot()
        if snapshot.state == ESTOP:
            self._fault_publish_not_before_ns = None
        elif not communication_ok and snapshot.state != FAULT:
            self.get_logger().warning(
                'Motor communication warning; waiting for the V6.5 '
                'consecutive-failure or 300 ms bus threshold.',
                throttle_duration_sec=1.0,
            )
        self._publish_if_changed()

    def _on_estop(self, message: Bool) -> None:
        """
        E-Stop level heartbeat를 반영한다.

        ``False`` 한 번은 곧바로 안전 복귀를 뜻하지 않는다. 코어가 연속 비활성 시간과
        heartbeat freshness를 별도로 추적하며, heartbeat가 300 ms 이상 끊겨도 물리
        입력 상태를 알 수 없으므로 ESTOP으로 닫는다.
        """
        self._last_estop_active = bool(message.data)
        self._core.update_estop(message.data, self._now_ns())
        if self._core.snapshot().state == ESTOP:
            self._fault_publish_not_before_ns = None
        self._publish_if_changed()

    def _on_validated_command(self, message: HandCommand) -> None:
        """Guard 검증을 통과해 hardware 경로로 전달된 command activity를 기록한다."""
        del message
        self._core.on_validated_command(
            self._now_ns(),
            state_stamp_ns=self._system_clock.now().nanoseconds,
        )
        self._publish_if_changed()

    def _on_validation_result(self, message: Bool) -> None:
        """
        HOLD 복구 전용 validation 결과를 양·음 모두 순서대로 코어에 전달한다.

        성공 activity는 hardware write를 뜻하지 않는다. HOLD barrier를 유지한 채 입력이
        계속 유효한지만 증명한다. 실패를 무시하면 성공 두 번 사이의 잘못된 명령을
        건너뛰어 "300 ms 연속"으로 오인하므로 실패는 복구 window를 즉시 초기화한다.
        """
        now_ns = self._now_ns()
        if message.data:
            self._core.on_validated_activity(
                now_ns,
                state_stamp_ns=self._system_clock.now().nanoseconds,
            )
            self._publish_if_changed()
            return
        self._core.on_validation_failed(now_ns)

    def _on_control_state(self, message: ControlState) -> None:
        """
        Owner lease 만료를 command timeout보다 먼저 RUN→HOLD로 전달한다.

        단순 MODE_DISABLED가 아니라 manager가 명시한 ``owner_lease_expired`` 전이만
        사용한다. 제어권 heartbeat가 사라진 사실을 다음 5000 ms command watchdog까지
        숨기지 않고 즉시 제한 상태에 반영하기 위함이다.
        """
        if (
            message.active_mode == ControlState.MODE_DISABLED
            and message.active_owner == ControlState.OWNER_NONE
            and not message.owner_alive
            and message.last_transition_reason == 'owner_lease_expired'
            and self._core.on_owner_lease_expired(self._now_ns())
        ):
            self._publish_if_changed()

    def handle_stop_requested(self, message: UInt64) -> None:
        """
        Guard의 STOP barrier ACK를 받아 정상 제어용 RESET 진입을 요청한다.

        사용자 STOP 자체를 직접 받지 않는 이유는 command 통과 경로보다 RESET 상태가
        먼저 보이는 분산 순서 문제를 막기 위해서다. Guard가 latch를 닫은 뒤 보낸 ACK가
        있어야 READY/RUN/HOLD에서 RESET으로 갈 수 있으며, ACK가 없으면 전이하지 않는다.
        """
        generation = int(message.data)
        if generation <= self._last_stop_generation:
            return
        self._last_stop_generation = generation
        now_ns = self._now_ns()
        state_stamp_ns = self._system_clock.now().nanoseconds
        if self._core.on_control_stop_requested(
            now_ns=now_ns,
            state_stamp_ns=state_stamp_ns,
        ):
            self.publish_safety_state()

    def handle_reset_safety(
        self,
        request: Trigger.Request,
        response: Trigger.Response,
    ) -> Trigger.Response:
        """
        위험 상태 해제용 ``reset_safety`` 요청을 코어에 위임한다.

        이 서비스는 정상 STOP 뒤의 RESET 상태를 만드는 API가 아니다. SAFE/FAULT/ESTOP의
        원인, E-Stop release window, hardware 안정 조건이 모두 충족된 경우에만 새 INIT
        검사 세대를 시작한다. 거절 이유를 그대로 반환해 운영자가 부족한 증거를 알 수
        있게 하며, 서비스 호출만으로 motor write나 torque-off를 수행하지 않는다.
        """
        del request
        result = self._core.request_safety_reset(
            self._now_ns(),
            state_stamp_ns=self._system_clock.now().nanoseconds,
        )
        response.success = result.accepted
        response.message = result.reason
        if result.accepted:
            self.publish_safety_state()
        else:
            self.get_logger().warning(
                f'Safety reset rejected: {result.reason}'
            )
        return response

    def _on_tick(self) -> None:
        """주기적으로 freshness, 5000/10000 ms watchdog, action deadline을 평가한다."""
        self._core.tick(
            self._now_ns(),
            state_stamp_ns=self._system_clock.now().nanoseconds,
        )
        self._publish_if_changed()

    def _publish_if_changed(self) -> None:
        """
        전이 epoch가 바뀐 경우만 즉시 발행하되 ESTOP 우선순위를 보존한다.

        서로 다른 ROS topic 사이에는 전역 callback 순서가 없다. 따라서 FAULT가 먼저
        계산됐더라도 이미 도착 가능한 E-Stop callback에 한 tick의 우선 판정 기회를 주고,
        ESTOP은 즉시 발행한다. FAULT 대기는 한 tick으로 고정되어 위험을 무기한 숨기지
        않는다.
        """
        snapshot = self._core.snapshot()
        if snapshot.transition_epoch == self._last_published_epoch:
            return
        if snapshot.state == ESTOP:
            # E-Stop은 지연 없이 발행하며 대기 중인 lower-priority FAULT를 폐기한다.
            self._fault_publish_not_before_ns = None
        elif snapshot.state == FAULT:
            # ROS의 서로 다른 topic은 callback 전역 순서를 보장하지 않는다. 따라서 낮은
            # 우선순위 FAULT를 최대 한 tick(기본 20 ms)만 보류해 이미 도착 가능한 E-Stop
            # callback이 먼저 ESTOP을 확정할 기회를 준다. 창이 끝나면 FAULT도 반드시
            # 발행하므로 무기한 숨기지 않는다.
            if self._fault_publish_not_before_ns is None:
                self._fault_publish_not_before_ns = (
                    self._now_ns() + self._tick_period_ms * 1_000_000
                )
                return
        self.publish_safety_state()

    def publish_safety_state(self) -> None:
        """
        코어 snapshot과 최근 입력 요약을 하나의 SafetyState heartbeat로 발행한다.

        같은 전이 epoch의 주기 heartbeat는 같은 source stamp를 유지하고, 새 전이는 이전
        stamp보다 반드시 큰 값을 사용한다. 소비자가 누락된 중간 상태가 있어도 "같은
        상태의 재발행"과 "새 안전 전이"를 구분할 수 있게 하는 인과 경계다.
        """
        snapshot = self._core.snapshot()
        if (
            snapshot.state == FAULT
            and self._fault_publish_not_before_ns is not None
        ):
            if self._now_ns() < self._fault_publish_not_before_ns:
                return
            self._fault_publish_not_before_ns = None
        elif snapshot.state != FAULT:
            self._fault_publish_not_before_ns = None
        message = SafetyState()
        if snapshot.transition_epoch != self._wire_state_epoch:
            self._wire_state_epoch = snapshot.transition_epoch
            self._wire_state_stamp_ns = max(
                self._system_clock.now().nanoseconds,
                self._wire_state_stamp_ns + 1,
            )
        message.stamp.sec = self._wire_state_stamp_ns // 1_000_000_000
        message.stamp.nanosec = self._wire_state_stamp_ns % 1_000_000_000
        message.state = snapshot.state
        message.command_timeout = snapshot.command_timeout
        message.motor_communication_ok = self._last_motor_communication_ok
        message.over_current = self._last_over_current
        message.over_temperature = self._last_over_temperature
        message.estop_active = self._last_estop_active
        message.fault_code = snapshot.fault_code
        message.reason = snapshot.reason
        self.safety_state_publisher.publish(message)
        self._last_published_epoch = snapshot.transition_epoch

    def _positive_parameter(self, name: str, default: int) -> int:
        """bool·0·음수·정수가 아닌 timing parameter를 시작 단계에서 거부한다."""
        value = self.declare_parameter(name, default).value
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f'{name} must be a positive integer')
        return value

    def _positive_number_parameter(self, name: str, default: float) -> float:
        """Trip limit이 양의 유한한 수인지 검사한다."""
        value = self.declare_parameter(name, default).value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f'{name} must be a positive number')
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            raise ValueError(f'{name} must be a positive number')
        return float(value)

    @staticmethod
    def _stamp_to_ns(stamp) -> Optional[int]:
        """정규 범위의 ROS stamp만 nanosecond로 바꾸고 malformed 값은 거부한다."""
        nanosec = int(stamp.nanosec)
        if not 0 <= nanosec < 1_000_000_000:
            return None
        return int(stamp.sec) * 1_000_000_000 + nanosec


def main(args: Optional[list] = None) -> None:
    """Safety Manager 노드를 시작하고 종료 시 ROS 자원을 정리한다."""
    rclpy.init(args=args)
    node = SafetyManager()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
