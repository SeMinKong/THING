"""ROS 2 node for experiment recording lifecycle management."""

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy
from rclpy.qos import QoSProfile
from rclpy.qos import ReliabilityPolicy

from thing_interfaces.msg import ControlState
from thing_interfaces.msg import RecordingState


class Logger(Node):
    """Publish recording state and observe the current control mode."""

    def __init__(self):
        """Initialize the logger node and its ROS interfaces."""
        super().__init__('logger')

        state_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )

        self.state = RecordingState.IDLE
        self.active_session_id = 0
        self.active_bag_path = ''
        self.active_started_at = None

        self.last_session_id = 0
        self.last_bag_path = ''
        self.last_started_at = None
        self.last_ended_at = None

        self.result_pending = False
        self.last_mimic_result = RecordingState.RESULT_UNSET
        self.active_mode = ControlState.MODE_DISABLED

        self.recording_state_publisher = self.create_publisher(
            RecordingState,
            '/thing/recording_state',
            state_qos,
        )
        self.control_state_subscription = self.create_subscription(
            ControlState,
            '/thing/control_state',
            self.handle_control_state,
            state_qos,
        )

        self.publish_recording_state('idle')
        self.get_logger().info('Logger started.')

    def handle_control_state(self, message):
        """Store the latest control mode for recording admission checks."""
        self.active_mode = message.active_mode

    def publish_recording_state(self, message=''):
        """Publish an immutable snapshot of the current recording state."""
        recording_state = RecordingState()
        recording_state.header.stamp = self.get_clock().now().to_msg()
        recording_state.state = self.state

        recording_state.active_session_id = self.active_session_id
        recording_state.active_bag_path = self.active_bag_path
        if self.active_started_at is not None:
            recording_state.active_started_at = self.active_started_at

        recording_state.last_session_id = self.last_session_id
        recording_state.last_bag_path = self.last_bag_path
        if self.last_started_at is not None:
            recording_state.last_started_at = self.last_started_at
        if self.last_ended_at is not None:
            recording_state.last_ended_at = self.last_ended_at

        recording_state.result_pending = self.result_pending
        recording_state.last_mimic_result = self.last_mimic_result
        recording_state.message = message

        self.recording_state_publisher.publish(recording_state)


def main(args=None):
    """Run the logger node until ROS shutdown."""
    rclpy.init(args=args)
    logger = Logger()

    try:
        rclpy.spin(logger)
    except KeyboardInterrupt:
        pass
    finally:
        logger.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
