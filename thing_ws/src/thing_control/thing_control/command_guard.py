"""
Manager가 선택한 ``HandCommand``를 최종 검증하는 ROS 2 adapter.

이 node는 command chain의 마지막 소프트웨어 문이다. Guard core가 수락한 경우에만
``/thing/command``를 발행하며, HOLD에서는 motor 명령 대신 복구 판단용
``validation_result``만 발행한다. 실제 전류·온도·torque 차단은 Safety Manager와
thing_hardware의 책임이다.
"""

from threading import RLock
from time import monotonic_ns
from typing import Optional

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
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

from thing_control.command_guard_core import (
    AXIS_NAMES,
    CommandGuardCore,
    GuardCommand,
    GuardLimits,
)
from thing_interfaces.msg import ControlState, HandCommand, SafetyState


_COMMAND_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
)
_STATE_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)
_DIAGNOSTIC_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
    reliability=ReliabilityPolicy.RELIABLE,
)
_INTERNAL_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
    reliability=ReliabilityPolicy.RELIABLE,
)


class CommandGuardNode(Node):
    """command·control·safety 계약이 전부 맞을 때만 hardware 경로를 연다."""

    def __init__(self, parameter_overrides=None) -> None:
        super().__init__(
            'command_guard',
            parameter_overrides=parameter_overrides,
        )
        self._transaction_lock = RLock()
        self._steady_clock = Clock(clock_type=ClockType.STEADY_TIME)
        self._system_clock = Clock(clock_type=ClockType.SYSTEM_TIME)
        self._core = CommandGuardCore(self._declare_limits())

        self._command_publisher = self.create_publisher(
            HandCommand,
            '/thing/command',
            _COMMAND_QOS,
        )
        self._validation_result_publisher = self.create_publisher(
            Bool,
            '/thing/command/validation_result',
            _INTERNAL_QOS,
        )
        self._stop_ack_publisher = self.create_publisher(
            Empty,
            '/thing/control/stop_barrier_ack',
            _INTERNAL_QOS,
        )
        self._diagnostic_publisher = self.create_publisher(
            DiagnosticArray,
            '/thing/diagnostics',
            _DIAGNOSTIC_QOS,
        )
        self._last_diagnostic_reason = 'startup'
        self._last_diagnostic_accepted = False
        self._last_diagnostic_source = -1
        self._last_diagnostic_sequence = -1
        diagnostic_period_ms = self._declare_diagnostic_period_ms()
        self._diagnostic_timer = self.create_timer(
            diagnostic_period_ms / 1000.0,
            self._publish_periodic_diagnostic,
            clock=self._steady_clock,
        )
        self._selected_subscription = self.create_subscription(
            HandCommand,
            '/thing/command/selected',
            self._on_selected_command,
            _COMMAND_QOS,
        )
        self._safety_subscription = self.create_subscription(
            SafetyState,
            '/thing/safety_state',
            self._on_safety_state,
            _STATE_QOS,
        )
        self._control_subscription = self.create_subscription(
            ControlState,
            '/thing/control_state',
            self._on_control_state,
            _STATE_QOS,
        )
        self._stop_subscription = self.create_subscription(
            Empty,
            '/thing/control/stop_requested',
            self._on_stop_requested,
            _INTERNAL_QOS,
        )

    def _declare_diagnostic_period_ms(self) -> int:
        period_ms = self.declare_parameter(
            'diagnostic_period_ms',
            1000,
        ).value
        if (
            isinstance(period_ms, bool)
            or not isinstance(period_ms, int)
            or not 0 < period_ms <= 1000
        ):
            raise ValueError(
                'diagnostic_period_ms must be an integer in [1, 1000]'
            )
        return period_ms

    def _declare_limits(self) -> GuardLimits:
        command_timeout_ms = self.declare_parameter(
            'command_timeout_ms',
            300,
        ).value
        future_tolerance_ms = self.declare_parameter(
            'command_future_tolerance_ms',
            100,
        ).value
        safety_timeout_ms = self.declare_parameter(
            'safety_state_timeout_ms',
            1500,
        ).value
        control_timeout_ms = self.declare_parameter(
            'control_state_timeout_ms',
            1500,
        ).value
        command_hold_ms = self.declare_parameter(
            'command_hold_ms',
            300,
        ).value

        axis_min = {}
        axis_max = {}
        max_delta = {}
        for axis_name in AXIS_NAMES:
            prefix = f'axis_limits.{axis_name}'
            axis_min[axis_name] = self.declare_parameter(
                f'{prefix}.min',
                0.0,
            ).value
            axis_max[axis_name] = self.declare_parameter(
                f'{prefix}.max',
                1.0,
            ).value
            max_delta[axis_name] = self.declare_parameter(
                f'{prefix}.max_delta_per_second',
                1.5,
            ).value

        return GuardLimits(
            command_stale_timeout_ms=command_timeout_ms,
            command_future_tolerance_ms=future_tolerance_ms,
            safety_state_timeout_ms=safety_timeout_ms,
            control_state_timeout_ms=control_timeout_ms,
            command_hold_ms=command_hold_ms,
            axis_min=axis_min,
            axis_max=axis_max,
            max_axis_delta_per_second=max_delta,
        )

    def _on_safety_state(self, message: SafetyState) -> None:
        with self._transaction_lock:
            nanosec = int(message.stamp.nanosec)
            if not 0 <= nanosec < 1_000_000_000:
                return
            source_stamp_ns = (
                int(message.stamp.sec) * 1_000_000_000 + nanosec
            )
            if source_stamp_ns <= 0:
                return
            self._core.update_safety_state(
                message.state,
                monotonic_ns(),
                source_stamp_ns=source_stamp_ns,
                reason=str(message.reason),
            )

    def _on_control_state(self, message: ControlState) -> None:
        with self._transaction_lock:
            self._core.update_control_state(
                message.active_mode,
                message.active_owner,
                message.owner_alive,
                monotonic_ns(),
            )

    def _on_stop_requested(self, _: Empty) -> None:
        with self._transaction_lock:
            # ACK는 단순 수신 확인이 아니다. on_stop_requested가 latch와 activation trust를
            # 먼저 닫은 뒤에만 발행하므로 Manager가 service 완료 경계로 사용할 수 있다.
            self._core.on_stop_requested()
            self._stop_ack_publisher.publish(Empty())

    def _on_selected_command(self, message: HandCommand) -> None:
        with self._transaction_lock:
            # stamp의 nanosec 범위는 ROS message 생성기가 항상 보장한다고 가정하지 않는다.
            # malformed input을 정규화하거나 예외로 죽지 않고 진단과 함께 거부한다.
            nanosec = int(message.stamp.nanosec)
            if not 0 <= nanosec < 1_000_000_000:
                self._publish_rejection(message, 'command_stamp_non_canonical')
                return
            command = GuardCommand(
                stamp_ns=(
                    int(message.stamp.sec) * 1_000_000_000
                    + nanosec
                ),
                sequence=int(message.sequence),
                source=int(message.source),
                axes={
                    axis_name: float(getattr(message, axis_name))
                    for axis_name in AXIS_NAMES
                },
                speed_limit=float(message.speed_limit),
                confidence=float(message.confidence),
            )
            decision = self._core.validate(
                command,
                now_ros_ns=self._system_clock.now().nanoseconds,
                now_monotonic_ns=monotonic_ns(),
            )
            if decision.accepted:
                self._last_diagnostic_reason = decision.reason
                self._last_diagnostic_accepted = True
                self._last_diagnostic_source = int(message.source)
                self._last_diagnostic_sequence = int(message.sequence)
                if not decision.forward_to_hardware:
                    # HOLD 복구 activity는 safety 판단에만 쓰며 motor에는 전달하지 않는다.
                    validation_result = Bool()
                    validation_result.data = True
                    self._validation_result_publisher.publish(validation_result)
                    return
                self._command_publisher.publish(message)
                return
            self._publish_rejection(message, decision.reason)

    def _publish_periodic_diagnostic(self) -> None:
        with self._transaction_lock:
            self._publish_diagnostic(
                reason=self._last_diagnostic_reason,
                accepted=self._last_diagnostic_accepted,
                source=self._last_diagnostic_source,
                sequence=self._last_diagnostic_sequence,
            )

    def _publish_rejection(self, message: HandCommand, reason: str) -> None:
        # valid-invalid-valid를 하나의 HOLD recovery window로 세지 않게 실패도 알린다.
        validation_result = Bool()
        validation_result.data = False
        self._validation_result_publisher.publish(validation_result)
        self._last_diagnostic_reason = reason
        self._last_diagnostic_accepted = False
        self._last_diagnostic_source = int(message.source)
        self._last_diagnostic_sequence = int(message.sequence)
        self._publish_diagnostic(
            reason=reason,
            accepted=False,
            source=int(message.source),
            sequence=int(message.sequence),
        )
        self.get_logger().warning(
            f'HandCommand rejected: {reason}',
            throttle_duration_sec=1.0,
        )

    def _publish_diagnostic(
        self,
        *,
        reason: str,
        accepted: bool,
        source: int,
        sequence: int,
    ) -> None:
        diagnostic = DiagnosticArray()
        diagnostic.header.stamp = self._system_clock.now().to_msg()
        status = DiagnosticStatus()
        status.level = (
            DiagnosticStatus.OK if accepted else DiagnosticStatus.WARN
        )
        status.name = 'thing_control/command_guard'
        status.hardware_id = 'thing-control'
        status.message = reason
        status.values = [
            KeyValue(key='source', value=str(source)),
            KeyValue(key='sequence', value=str(sequence)),
            KeyValue(key='accepted', value=str(accepted).lower()),
        ]
        diagnostic.status = [status]
        self._diagnostic_publisher.publish(diagnostic)


def main(args: Optional[list] = None) -> None:
    """Run the command guard node."""
    rclpy.init(args=args)
    node = CommandGuardNode()
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
