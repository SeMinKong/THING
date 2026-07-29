"""ROS 2 node that arbitrates command ownership and active input."""

from functools import partial
from threading import RLock
from typing import Iterable, Optional

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from std_msgs.msg import Bool, Empty

from thing_interfaces.msg import (
    ControlState,
    HandCommand,
    RecordingState,
    SafetyState,
)
from thing_interfaces.srv import SetControlMode

from thing_control.command_manager_core import CommandManagerCore


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
_INTERNAL_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
    reliability=ReliabilityPolicy.RELIABLE,
)


class CommandManagerNode(Node):
    """Select one command source using mode, owner, and safety state."""

    def __init__(self, parameter_overrides=None) -> None:
        super().__init__(
            'command_manager',
            parameter_overrides=parameter_overrides,
        )

        owner_lease_timeout_ms = self.declare_parameter(
            'owner_lease_timeout_ms',
            3000,
        ).value
        stop_reacquire_delay_ms = self.declare_parameter(
            'stop_reacquire_delay_ms',
            500,
        ).value
        lease_check_period_ms = self.declare_parameter(
            'lease_check_period_ms',
            100,
        ).value
        state_publish_period_ms = self.declare_parameter(
            'state_publish_period_ms',
            1000,
        ).value
        self._validate_positive_parameter(
            'owner_lease_timeout_ms',
            owner_lease_timeout_ms,
        )
        self._validate_positive_parameter(
            'stop_reacquire_delay_ms',
            stop_reacquire_delay_ms,
        )
        self._validate_positive_parameter(
            'lease_check_period_ms',
            lease_check_period_ms,
        )
        self._validate_positive_parameter(
            'state_publish_period_ms',
            state_publish_period_ms,
        )

        self._arbitration_lock = RLock()
        self._core = CommandManagerCore(
            owner_lease_timeout_ms=int(owner_lease_timeout_ms),
            stop_reacquire_delay_ms=int(stop_reacquire_delay_ms),
        )
        self._selected_publisher = self.create_publisher(
            HandCommand,
            '/thing/command/selected',
            _COMMAND_QOS,
        )
        self._control_state_publisher = self.create_publisher(
            ControlState,
            '/thing/control_state',
            _STATE_QOS,
        )
        self._stop_event_publisher = self.create_publisher(
            Empty,
            '/thing/control/stop_requested',
            _INTERNAL_QOS,
        )

        self._command_subscriptions = [
            self.create_subscription(
                HandCommand,
                '/thing/command/mimic',
                partial(
                    self._on_command,
                    expected_sources=(HandCommand.SOURCE_MIMIC,),
                ),
                _COMMAND_QOS,
            ),
            self.create_subscription(
                HandCommand,
                '/thing/command/teleop',
                partial(
                    self._on_command,
                    expected_sources=(HandCommand.SOURCE_TELEOP,),
                ),
                _COMMAND_QOS,
            ),
            self.create_subscription(
                HandCommand,
                '/thing/command/manual',
                partial(
                    self._on_command,
                    expected_sources=(
                        HandCommand.SOURCE_GESTURE,
                        HandCommand.SOURCE_SEQUENCE,
                    ),
                ),
                _COMMAND_QOS,
            ),
        ]
        self._safety_subscription = self.create_subscription(
            SafetyState,
            '/thing/safety_state',
            self._on_safety_state,
            _STATE_QOS,
        )
        self._recording_subscription = self.create_subscription(
            RecordingState,
            '/thing/recording_state',
            self._on_recording_state,
            _STATE_QOS,
        )
        self._motion_subscription = self.create_subscription(
            Bool,
            '/thing/control/motion_active',
            self._on_motion_active,
            _INTERNAL_QOS,
        )
        self._mode_service = self.create_service(
            SetControlMode,
            '/thing/set_control_mode',
            self._on_set_control_mode,
        )

        self._lease_timer = self.create_timer(
            float(lease_check_period_ms) / 1000.0,
            self._on_lease_timer,
        )
        self._state_timer = self.create_timer(
            float(state_publish_period_ms) / 1000.0,
            self._publish_control_state,
        )
        self._publish_control_state()

    @staticmethod
    def _validate_positive_parameter(name: str, value) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f'{name} must be a positive integer')

    def _on_command(
        self,
        message: HandCommand,
        expected_sources: Iterable[int],
    ) -> None:
        with self._arbitration_lock:
            if message.source not in expected_sources:
                return
            if not self._core.accepts_source(message.source):
                return
            self._selected_publisher.publish(message)

    def _on_safety_state(self, message: SafetyState) -> None:
        with self._arbitration_lock:
            if self._core.update_safety_state(message.state):
                self._publish_control_state()

    def _on_recording_state(self, message: RecordingState) -> None:
        with self._arbitration_lock:
            self._core.update_recording_state(
                message.state,
                message.result_pending,
            )

    def _on_motion_active(self, message: Bool) -> None:
        with self._arbitration_lock:
            if self._core.set_sequence_running(message.data):
                self._publish_control_state()

    def _on_set_control_mode(
        self,
        request: SetControlMode.Request,
        response: SetControlMode.Response,
    ) -> SetControlMode.Response:
        with self._arbitration_lock:
            result = self._core.request_mode(
                request.requested_mode,
                request.requested_owner,
            )
            response.accepted = result.accepted
            response.active_mode = result.active_mode
            response.active_owner = result.active_owner
            response.reason = result.reason
            if result.accepted:
                if request.requested_mode == ControlState.MODE_DISABLED:
                    self._stop_event_publisher.publish(Empty())
                self._publish_control_state()
            return response

    def _on_lease_timer(self) -> None:
        with self._arbitration_lock:
            if self._core.check_lease():
                self._publish_control_state()

    def _publish_control_state(self) -> None:
        with self._arbitration_lock:
            state = self._core.snapshot()
            message = ControlState()
            message.stamp = self.get_clock().now().to_msg()
            message.active_mode = state.active_mode
            message.active_owner = state.active_owner
            message.owner_alive = state.owner_alive
            message.sequence_running = state.sequence_running
            message.last_transition_reason = state.last_transition_reason
            self._control_state_publisher.publish(message)


def main(args: Optional[list] = None) -> None:
    """Run the command manager node."""
    rclpy.init(args=args)
    node = CommandManagerNode()
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
