"""실험 기록 생명주기를 관리하는 ROS 2 노드."""

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy
from rclpy.qos import QoSProfile
from rclpy.qos import ReliabilityPolicy
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time

from thing_interfaces.msg import ControlState
from thing_interfaces.msg import HandCommand
from thing_interfaces.msg import HandLandmarks
from thing_interfaces.msg import MotorStatus
from thing_interfaces.msg import RecordingState
from thing_interfaces.msg import SafetyState
from thing_interfaces.srv import SetMimicResult
from thing_interfaces.srv import StartRecording
from thing_interfaces.srv import StopRecording
from thing_logger.bag_recorder import BagRecorderError
from thing_logger.export_worker import ExportWorker
from thing_logger.exporter import ExportJob
from thing_logger.exporter import SessionExporter
from thing_logger.recording_worker import RecordingWorker
from thing_logger.session import SessionManager
from thing_logger.uploader_handoff import UnixSocketUploaderClient


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
        self.declare_parameter(
            'export_root',
            '/var/lib/thing-robot-data/tmp-upload',
        )
        self.declare_parameter('robot_id', 'THING-001')
        self.declare_parameter('time_sync', True)
        self.declare_parameter(
            'uploader_socket',
            '/run/thing-uploader/uploader.sock',
        )
        export_root = self.get_parameter('export_root').value
        robot_id = self.get_parameter('robot_id').value
        time_sync = self.get_parameter('time_sync').value
        uploader_socket = self.get_parameter('uploader_socket').value

        # 세션 상태와 rosbag2 기록 구현은 각 전담 객체에 맡긴다.
        self.session_manager = SessionManager(bag_root)
        self.bag_recorder = RecordingWorker()
        self.export_worker = ExportWorker(
            SessionExporter(
                robot_id,
                export_root,
                time_sync=time_sync,
            ),
            UnixSocketUploaderClient(uploader_socket),
        )

        # StartRecording 요청을 판단하기 위해 최신 제어 모드만 보관한다.
        self.active_mode = ControlState.MODE_DISABLED

        # 시작 직후에는 안전 검사가 끝나지 않은 것으로 취급한다.
        self.safety_state = SafetyState.INIT

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
        self.landmarks_subscription = self.create_subscription(
            HandLandmarks,
            '/thing/landmarks',
            self.handle_landmarks,
            self.landmarks_qos,
        )
        self.command_subscription = self.create_subscription(
            HandCommand,
            '/thing/command',
            self.handle_command,
            self.command_qos,
        )
        self.motor_status_subscription = self.create_subscription(
            MotorStatus,
            '/thing/motor_status',
            self.handle_motor_status,
            self.motor_status_qos,
        )
        self.safety_state_subscription = self.create_subscription(
            SafetyState,
            '/thing/safety_state',
            self.handle_safety_state,
            self.state_qos,
        )

        self.start_recording_service = self.create_service(
            StartRecording,
            '/thing/start_recording',
            self.handle_start_recording,
        )
        self.stop_recording_service = self.create_service(
            StopRecording,
            '/thing/stop_recording',
            self.handle_stop_recording,
        )
        self.set_mimic_result_service = self.create_service(
            SetMimicResult,
            '/thing/set_mimic_result',
            self.handle_set_mimic_result,
        )
        self.recording_error_timer = self.create_timer(
            0.05,
            self.handle_recording_worker_error,
        )
        self.export_result_timer = self.create_timer(
            0.1,
            self.handle_export_worker_result,
        )

        self.publish_recording_state()
        self.get_logger().info('Logger started.')

    def handle_control_state(self, message):
        """최신 제어 모드를 저장하고 녹화 중이면 rosbag2에 기록한다."""
        previous_mode = self.active_mode
        self.active_mode = message.active_mode
        self.write_message('/thing/control_state', message)

        # lease 만료 등으로 MIMIC이 해제되면 활성 녹화를 중단한다.
        if (
            previous_mode == ControlState.MODE_MIMIC
            and self.active_mode != ControlState.MODE_MIMIC
            and self.session_manager.active_session is not None
        ):
            self.interrupt_recording('mimic mode ended')

    def handle_landmarks(self, message):
        """손 landmark를 녹화 중인 rosbag2에 기록한다."""
        self.write_message('/thing/landmarks', message)

    def handle_command(self, message):
        """최종 HandCommand를 녹화 중인 rosbag2에 기록한다."""
        self.write_message('/thing/command', message)

    def handle_motor_status(self, message):
        """모터 상태를 녹화 중인 rosbag2에 기록한다."""
        self.write_message('/thing/motor_status', message)

    def handle_safety_state(self, message):
        """안전 상태를 기록하고 중단 또는 복구 전이를 처리한다."""
        self.safety_state = message.state
        self.write_message('/thing/safety_state', message)

        if message.state in (
            SafetyState.SAFE,
            SafetyState.FAULT,
            SafetyState.ESTOP,
        ):
            self.interrupt_recording('safety state changed')
            return

        # 안전 재검사 중에는 INTERRUPTED를 유지하고 실제 READY에서만 복구한다.
        if (
            message.state == SafetyState.READY
            and self.session_manager.state == RecordingState.INTERRUPTED
        ):
            self.session_manager.reset_to_idle()
            self.publish_recording_state()

    def write_message(self, topic_name, message):
        """녹화 중인 경우 현재 ROS 시각으로 메시지를 기록한다."""
        if not self.bag_recorder.is_recording:
            return

        try:
            self.bag_recorder.write(
                topic_name,
                message,
                self.get_clock().now().nanoseconds,
            )
        except BagRecorderError as error:
            self.get_logger().error(str(error))
            self.interrupt_recording('recording write failed')

    def handle_recording_worker_error(self):
        """worker의 비동기 write 오류를 Logger 상태 전이에 반영한다."""
        error = self.bag_recorder.take_async_error()
        if error is None:
            return
        self.get_logger().error(str(error))
        self.interrupt_recording('recording write failed')

    def handle_start_recording(self, request, response):
        """안전한 MIMIC 상태에서 새로운 rosbag2 기록을 시작한다."""
        accepted, reason = self.session_manager.can_start(
            self.active_mode,
            self.safety_state,
        )

        if not accepted:
            response.accepted = False
            response.reason = reason
            return response

        try:
            session = self.session_manager.begin_start(
                request.label,
                self.get_clock().now().nanoseconds,
            )
            self.publish_recording_state()

            self.bag_recorder.start(session.bag_path)
            self.session_manager.mark_recording()
            self.publish_recording_state()
        except Exception as error:
            self.get_logger().error(str(error))

            if self.bag_recorder.is_recording:
                try:
                    self.bag_recorder.interrupt()
                except BagRecorderError as cleanup_error:
                    self.get_logger().error(str(cleanup_error))

            if self.session_manager.state == RecordingState.STARTING:
                self.session_manager.cancel_start('start failed')

            self.publish_recording_state()
            response.accepted = False
            response.reason = 'start_failed'
            return response

        response.accepted = True
        response.session_id = session.session_id
        response.bag_path = session.bag_path
        response.reason = ''
        return response

    def handle_stop_recording(self, request, response):
        """활성 세션을 정상 종료하고 결과 판정 대기로 전환한다."""
        accepted, reason = self.session_manager.can_stop(
            request.session_id
        )

        if not accepted:
            response.accepted = False
            response.reason = reason
            return response

        try:
            self.session_manager.mark_stopping()
            self.publish_recording_state()
            self.bag_recorder.stop()

            session = self.session_manager.complete(
                self.get_clock().now().nanoseconds,
            )
            self.publish_recording_state()
        except Exception as error:
            self.get_logger().error(str(error))
            self.interrupt_recording('stop failed')
            response.accepted = False
            response.reason = 'stop_failed'
            return response

        response.accepted = True
        response.stopped_session_id = session.session_id
        response.bag_path = session.bag_path
        response.reason = ''
        return response

    def handle_set_mimic_result(self, request, response):
        """정상 종료된 세션의 SUCCESS 또는 FAILURE 판정을 저장한다."""
        accepted, reason = self.session_manager.set_result(
            request.session_id,
            request.result,
        )
        response.accepted = accepted
        response.reason = reason

        if accepted:
            self.publish_recording_state()
            result_name = {
                RecordingState.RESULT_SUCCESS: 'SUCCESS',
                RecordingState.RESULT_FAILURE: 'FAILURE',
            }[request.result]
            completed_session = self.session_manager.last_session
            job = ExportJob(
                completed_session.bag_path,
                result_name,
            )
            self.export_worker.submit(job)

        return response

    def handle_export_worker_result(self):
        """완료된 export 결과 또는 오류를 Logger 진단 로그에 남긴다."""
        completed = self.export_worker.take_completed()
        if completed is None:
            return
        if completed.error is not None:
            self.get_logger().error(
                f'export failed: {completed.error}'
            )
            return
        self.get_logger().info(
            'export completed: '
            f'session_id={completed.result.session_id} '
            f'content_digest={completed.result.content_digest}'
        )

    def interrupt_recording(self, message):
        """활성 writer를 닫고 중단된 bag 삭제를 시도한다."""
        if self.session_manager.active_session is None:
            return

        if self.bag_recorder.is_recording:
            try:
                self.bag_recorder.interrupt()
            except BagRecorderError as error:
                # 강제 종료나 I/O 오류에서는 삭제 성공을 보장하지 않는다.
                self.get_logger().error(str(error))

        self.session_manager.interrupt(
            self.get_clock().now().nanoseconds,
            message,
        )
        self.publish_recording_state()

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

        # Logger가 발행한 기록 상태도 명세의 필수 rosbag2 토픽이다.
        if self.bag_recorder.is_recording:
            try:
                self.bag_recorder.write(
                    '/thing/recording_state',
                    recording_state,
                    recording_state.header.stamp.sec * 1_000_000_000
                    + recording_state.header.stamp.nanosec,
                )
            except BagRecorderError as error:
                self.get_logger().error(str(error))
                self.interrupt_recording('recording state write failed')


def main(args=None):
    """ROS가 종료될 때까지 Logger 노드를 실행한다."""
    rclpy.init(args=args)
    logger = Logger()

    try:
        rclpy.spin(logger)
    except KeyboardInterrupt:
        pass
    finally:
        logger.interrupt_recording('process shutdown')
        logger.bag_recorder.shutdown()
        logger.export_worker.shutdown()
        logger.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
