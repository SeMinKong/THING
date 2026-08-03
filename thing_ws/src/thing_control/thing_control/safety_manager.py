"""
ROS 2 입출력을 8상태 안전 정책 코어에 연결하는 adapter.

callback은 메시지 형식·QoS·두 종류의 시계를 다루고, 실제 상태 전이 판단은
``SafetyManagerCore``에 위임한다. 이 경계 덕분에 ROS가 없어도 안전 규칙을 시험할 수
있고, 이 파일에서는 "어떤 topic이 어떤 코어 입력이 되는가"에 집중할 수 있다.
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
from std_msgs.msg import Bool, Empty
from std_srvs.srv import Trigger

from thing_control.safety_manager_core import (
    ESTOP,
    FAULT,
    HardwareStatus,
    SafetyLimits,
    SafetyManagerCore,
)
from thing_interfaces.msg import ControlState, HandCommand, MotorStatus, SafetyState


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
    """센서 heartbeat와 제어 activity를 받아 권위 SafetyState를 발행한다."""

    def __init__(self, parameter_overrides=None) -> None:
        super().__init__(
            'safety_manager',
            parameter_overrides=parameter_overrides,
        )
        self._steady_clock = Clock(clock_type=ClockType.STEADY_TIME)
        self._system_clock = Clock(clock_type=ClockType.SYSTEM_TIME)

        limits = SafetyLimits(
            command_hold_ms=self._positive_parameter('command_hold_ms', 300),
            command_safe_ms=self._positive_parameter('command_safe_ms', 1000),
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

        self._core = SafetyManagerCore(
            limits,
            started_ns=self._now_ns(),
            configuration_valid=self._trip_limits_validated,
        )
        self._last_published_epoch = -1
        self._wire_state_epoch = -1
        self._wire_state_stamp_ns = -1
        self._last_motor_communication_ok = False
        self._last_over_current = False
        self._last_over_temperature = False
        self._last_estop_active = False
        self._fault_publish_not_before_ns: Optional[int] = None

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
        self.stop_subscription = self.create_subscription(
            Empty,
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
        return self._core.snapshot().state

    @property
    def command_timeout(self) -> bool:
        return self._core.snapshot().command_timeout

    def _now_ns(self) -> int:
        return self._steady_clock.now().nanoseconds

    def _on_motor_status(self, message: MotorStatus) -> None:
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
        self._last_estop_active = bool(message.data)
        self._core.update_estop(message.data, self._now_ns())
        if self._core.snapshot().state == ESTOP:
            self._fault_publish_not_before_ns = None
        self._publish_if_changed()

    def _on_validated_command(self, message: HandCommand) -> None:
        del message
        self._core.on_validated_command(
            self._now_ns(),
            state_stamp_ns=self._system_clock.now().nanoseconds,
        )
        self._publish_if_changed()

    def _on_validation_result(self, message: Bool) -> None:
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
        """Owner lease 만료를 command timeout보다 먼저 RUN→HOLD로 전달한다."""
        if (
            message.active_mode == ControlState.MODE_DISABLED
            and message.active_owner == ControlState.OWNER_NONE
            and not message.owner_alive
            and message.last_transition_reason == 'owner_lease_expired'
            and self._core.on_owner_lease_expired(self._now_ns())
        ):
            self._publish_if_changed()

    def handle_stop_requested(self, message: Empty) -> None:
        del message
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
        self._core.tick(
            self._now_ns(),
            state_stamp_ns=self._system_clock.now().nanoseconds,
        )
        self._publish_if_changed()

    def _publish_if_changed(self) -> None:
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
        value = self.declare_parameter(name, default).value
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f'{name} must be a positive integer')
        return value

    def _positive_number_parameter(self, name: str, default: float) -> float:
        value = self.declare_parameter(name, default).value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f'{name} must be a positive number')
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            raise ValueError(f'{name} must be a positive number')
        return float(value)

    @staticmethod
    def _stamp_to_ns(stamp) -> Optional[int]:
        nanosec = int(stamp.nanosec)
        if not 0 <= nanosec < 1_000_000_000:
            return None
        return int(stamp.sec) * 1_000_000_000 + nanosec


def main(args: Optional[list] = None) -> None:
    """Run the Safety Manager node."""
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
