"""실험 기록 생명주기를 관리하는 ROS 2 노드."""

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy
from rclpy.qos import QoSProfile
from rclpy.qos import ReliabilityPolicy
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time

from thing_interfaces.msg import ControlState
from thing_interfaces.msg import RecordingState
from thing_logger.bag_recorder import BagRecorder
from thing_logger.session import SessionManager


class Logger(Node):
    """기록 상태를 발행하고 현재 제어 모드를 확인한다."""

    def __init__(self):
        """Logger 노드와 현재 단계의 ROS 인터페이스를 초기화한다."""
        super().__init__('logger')

        # 명세의 상태 토픽 QoS: Reliable, Transient Local
        self.state_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )

        # 명세의 최종 명령 QoS: Reliable, depth 1
        self.command_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
        )

        # 명세의 모터 상태 QoS: Reliable, depth 5
        self.motor_status_qos = QoSProfile(
            depth=5,
            reliability=ReliabilityPolicy.RELIABLE,
        )

        # landmark 구독 단계에서 사용할 Sensor Data QoS
        self.landmarks_qos = qos_profile_sensor_data

        # 로컬 rosbag2 저장 위치는 실행 시 ROS parameter로 변경할 수 있다.
        self.declare_parameter(
            'bag_root',
            '/var/lib/thing-robot-data/rosbag2',
        )
        bag_root = self.get_parameter('bag_root').value

        # 세션 상태와 rosbag2 기록 구현은 각 전담 객체에 맡긴다.
        self.session_manager = SessionManager(bag_root)
        self.bag_recorder = BagRecorder()

        # StartRecording 요청을 판단하기 위해 최신 제어 모드만 보관한다.
        self.active_mode = ControlState.MODE_DISABLED

        self.recording_state_publisher = self.create_publisher(
            RecordingState,
            '/thing/recording_state',
            self.state_qos,
        )
        self.control_state_subscription = self.create_subscription(
            ControlState,
            '/thing/control_state',
            self.handle_control_state,
            self.state_qos,
        )

        self.publish_recording_state()
        self.get_logger().info('Logger started.')

    def handle_control_state(self, message):
        """기록 시작 조건을 확인할 수 있도록 최신 제어 모드를 저장한다."""
        self.active_mode = message.active_mode

    def publish_recording_state(self):
        """SessionManager의 현재 상태를 RecordingState로 발행한다."""
        manager = self.session_manager
        recording_state = RecordingState()
        recording_state.header.stamp = self.get_clock().now().to_msg()
        recording_state.state = manager.state

        active_session = manager.active_session
        if active_session is not None:
            recording_state.active_session_id = active_session.session_id
            recording_state.active_bag_path = active_session.bag_path
            recording_state.active_started_at = Time(
                nanoseconds=active_session.started_at_ns,
            ).to_msg()

        last_session = manager.last_session
        if last_session is not None:
            recording_state.last_session_id = last_session.session_id
            recording_state.last_bag_path = last_session.bag_path
            recording_state.last_started_at = Time(
                nanoseconds=last_session.started_at_ns,
            ).to_msg()

            if last_session.ended_at_ns is not None:
                recording_state.last_ended_at = Time(
                    nanoseconds=last_session.ended_at_ns,
                ).to_msg()

            recording_state.last_mimic_result = last_session.result

        recording_state.result_pending = manager.result_pending
        recording_state.message = manager.message

        self.recording_state_publisher.publish(recording_state)


def main(args=None):
    """ROS가 종료될 때까지 Logger 노드를 실행한다."""
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
