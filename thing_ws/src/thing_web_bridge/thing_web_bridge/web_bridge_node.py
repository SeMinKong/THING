"""Bridge fixed browser WebSocket JSON to validated ROS 2 interfaces."""

from threading import Event
from typing import Any, Callable, Dict, Optional

import rclpy
from rclpy.action import ActionClient
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy
from rclpy.qos import HistoryPolicy
from rclpy.qos import QoSProfile
from rclpy.qos import ReliabilityPolicy
from std_srvs.srv import Trigger
from thing_interfaces.action import ExecuteSequence
from thing_interfaces.msg import ControlState
from thing_interfaces.msg import HandCommand
from thing_interfaces.msg import HandLandmarks
from thing_interfaces.msg import MotorStatus
from thing_interfaces.msg import RecordingState
from thing_interfaces.msg import SafetyState
from thing_interfaces.srv import ExecuteGesture
from thing_interfaces.srv import SetControlMode
from thing_interfaces.srv import SetMimicResult
from thing_interfaces.srv import StartRecording
from thing_interfaces.srv import StopRecording

from thing_web_bridge.protocol import BridgeRequest
from thing_web_bridge.protocol import CONTROL_MODES
from thing_web_bridge.protocol import CONTROL_OWNERS
from thing_web_bridge.protocol import make_ack
from thing_web_bridge.protocol import RECORDING_RESULTS
from thing_web_bridge.protocol import SnapshotStore
from thing_web_bridge.websocket_server import WebSocketServer


def _sensor_qos() -> QoSProfile:
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE,
    )


def _reliable_qos(depth: int) -> QoSProfile:
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=depth,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
    )


def _state_qos() -> QoSProfile:
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
    )


def _guarded(
    update: Callable[[Any], None],
    logger: Any,
) -> Callable[[Any], None]:
    """
    Keep one bad ROS message from killing the whole bridge.

    enum 범위 밖 값 등은 상대 노드와의 버전 불일치에서 실제로 올 수 있고
    (NaN 크래시와 같은 계열), 구독 콜백 밖으로 예외가 나가면 spin이 죽어
    모니터링 전체가 끊긴다. 표시 전용 경로이므로 그 메시지만 버리고
    직전 상태를 유지한다.
    """
    def callback(message: Any) -> None:
        try:
            update(message)
        except Exception as error:
            logger.warning(
                f'state update dropped: {error}',
                throttle_duration_sec=5.0,
            )
    return callback


class WebBridgeNode(Node):
    """Expose ROS 2 monitoring and allowed control requests to one endpoint."""

    def __init__(self, start_server: bool = True) -> None:
        """Create subscriptions, clients, and optionally the WebSocket server."""
        super().__init__('web_bridge_node')
        self.declare_parameter('bind_address', '0.0.0.0')
        self.declare_parameter('port', 8000)
        # 내부 제어 웹이 이 주기에서 장치 up/down 판정 임계값을 파생하므로
        # 바꾸면 web/docs/pending-decisions.md A-1로 웹에 알려야 한다.
        self.declare_parameter('snapshot_period_ms', 200)
        self.declare_parameter('service_timeout_ms', 2000)

        bind_address = str(self.get_parameter('bind_address').value)
        port = int(self.get_parameter('port').value)
        snapshot_period_ms = int(
            self.get_parameter('snapshot_period_ms').value)
        self._service_timeout = (
            int(self.get_parameter('service_timeout_ms').value) / 1000.0
        )
        if snapshot_period_ms <= 0:
            raise ValueError('snapshot_period_ms must be positive')
        if self._service_timeout <= 0.0:
            raise ValueError('service_timeout_ms must be positive')

        # 표시용 파생 임계값은 protocol.py의 명세 기본값을 그대로 쓴다.
        # hand_target_node YAML에 같은 값이 이미 있어 여기에 또 두면 한쪽만
        # 고쳤을 때 화면과 제어 판정이 갈린다. 실제 제어 판정은 Raspberry Pi
        # 소관이고(FR-27) 브리지는 표시만 만든다.
        self._snapshot_store = SnapshotStore()
        store = self._snapshot_store
        logger = self.get_logger()
        self._subscriptions = [
            self.create_subscription(
                ControlState,
                '/thing/control_state',
                _guarded(store.update_control_state, logger),
                _state_qos(),
            ),
            self.create_subscription(
                RecordingState,
                '/thing/recording_state',
                _guarded(store.update_recording_state, logger),
                _state_qos(),
            ),
            self.create_subscription(
                HandLandmarks,
                '/thing/landmarks',
                _guarded(store.update_landmarks, logger),
                _sensor_qos(),
            ),
            self.create_subscription(
                MotorStatus,
                '/thing/motor_status',
                _guarded(store.update_motor_state, logger),
                _reliable_qos(5),
            ),
            self.create_subscription(
                SafetyState,
                '/thing/safety_state',
                _guarded(store.update_safety_state, logger),
                _state_qos(),
            ),
            self.create_subscription(
                HandCommand,
                '/thing/command',
                _guarded(store.update_hand_command, logger),
                _reliable_qos(1),
            ),
        ]

        self._set_mode_client = self.create_client(
            SetControlMode, '/thing/set_control_mode')
        self._gesture_client = self.create_client(
            ExecuteGesture, '/thing/execute_gesture')
        self._start_recording_client = self.create_client(
            StartRecording, '/thing/start_recording')
        self._stop_recording_client = self.create_client(
            StopRecording, '/thing/stop_recording')
        self._mimic_result_client = self.create_client(
            SetMimicResult, '/thing/set_mimic_result')
        self._reset_safety_client = self.create_client(
            Trigger, '/thing/reset_safety')
        self._sequence_client = ActionClient(
            self, ExecuteSequence, '/thing/execute_sequence')

        self._server = WebSocketServer(
            snapshot_store=self._snapshot_store,
            request_handler=self._dispatch_request,
            host=bind_address,
            port=port,
            snapshot_period=snapshot_period_ms / 1000.0,
        )
        self._server_started = False
        if start_server:
            self._server.start()
            self._server_started = True
            self.get_logger().info(
                f'Web Bridge listening on ws://{bind_address}:{port}'
                '/ws/robot-state',
            )

    @property
    def snapshot_store(self) -> SnapshotStore:
        """Expose the store for contract and integration tests."""
        return self._snapshot_store

    def destroy_node(self) -> bool:
        """Stop WebSocket activity before destroying ROS entities."""
        if self._server_started:
            self._server_started = False
            try:
                self._server.stop()
            except RuntimeError as error:
                # stop 실패(스레드 join timeout)가 action client와 rclpy
                # 자원 정리까지 건너뛰게 두지 않는다. 스레드는 daemon이라
                # 프로세스 종료를 막지 못한다.
                self.get_logger().error(
                    f'WebSocket server stop failed: {error}')
        self._sequence_client.destroy()
        return super().destroy_node()

    def _wait_future(
        self,
        future: Any,
        client: Optional[Any] = None,
    ) -> tuple[bool, Any]:
        """
        Block off the executor thread until the future finishes or times out.

        timeout으로 포기한 요청은 rclpy Client의 pending 목록에 남는다. 응답이
        영영 오지 않으면(서비스 프로세스가 죽는 등) 항목이 계속 쌓이므로
        같이 정리한다.
        """
        completed = Event()
        future.add_done_callback(lambda unused: completed.set())
        if not completed.wait(self._service_timeout):
            if client is not None:
                remove = getattr(client, 'remove_pending_request', None)
                if callable(remove):
                    remove(future)
            return False, None
        try:
            return True, future.result()
        except Exception as error:
            self.get_logger().error(f'ROS request failed: {error}')
            return True, None

    def _call_service(
        self,
        client: Any,
        ros_request: Any,
    ) -> tuple[Optional[Any], str]:
        if not client.service_is_ready():
            return None, 'service_unavailable'
        completed, response = self._wait_future(
            client.call_async(ros_request), client)
        if not completed:
            return None, 'service_timeout'
        if response is None:
            return None, 'service_failed'
        return response, ''

    def _service_ack(
        self,
        request_id: str,
        response: Optional[Any],
        failure_reason: str,
        extra: Optional[Callable[[Any], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if response is None:
            return make_ack(request_id, False, failure_reason)
        accepted = bool(getattr(response, 'accepted', False))
        reason = str(getattr(response, 'reason', ''))
        if not reason:
            reason = 'accepted' if accepted else 'service_rejected'
        fields = extra(response) if extra is not None else {}
        return make_ack(request_id, accepted, reason, **fields)

    def _dispatch_request(self, request: BridgeRequest) -> Dict[str, Any]:
        dispatchers = {
            'set_control_mode': self._set_control_mode,
            'stop': self._set_control_mode,
            'execute_gesture': self._execute_gesture,
            'execute_sequence': self._execute_sequence,
            'start_recording': self._start_recording,
            'stop_recording': self._stop_recording,
            'set_mimic_result': self._set_mimic_result,
            'reset_safety': self._reset_safety,
        }
        return dispatchers[request.type](request)

    def _set_control_mode(self, request: BridgeRequest) -> Dict[str, Any]:
        ros_request = SetControlMode.Request()
        ros_request.requested_mode = CONTROL_MODES.index(
            request.payload['requested_mode'])
        ros_request.requested_owner = CONTROL_OWNERS.index(
            request.payload['requested_owner'])
        response, failure = self._call_service(
            self._set_mode_client, ros_request)

        def extra(result: Any) -> Dict[str, Any]:
            mode = int(result.active_mode)
            owner = int(result.active_owner)
            return {
                'active_mode': (
                    CONTROL_MODES[mode]
                    if 0 <= mode < len(CONTROL_MODES)
                    else f'UNKNOWN({mode})'
                ),
                'active_owner': (
                    CONTROL_OWNERS[owner]
                    if 0 <= owner < len(CONTROL_OWNERS)
                    else f'UNKNOWN({owner})'
                ),
            }

        return self._service_ack(
            request.request_id, response, failure, extra)

    def _execute_gesture(self, request: BridgeRequest) -> Dict[str, Any]:
        ros_request = ExecuteGesture.Request()
        ros_request.gesture_name = request.payload['gesture_name']
        ros_request.speed_limit = float(request.payload['speed_limit'])
        response, failure = self._call_service(
            self._gesture_client, ros_request)
        return self._service_ack(request.request_id, response, failure)

    def _start_recording(self, request: BridgeRequest) -> Dict[str, Any]:
        ros_request = StartRecording.Request()
        ros_request.label = request.payload['label']
        response, failure = self._call_service(
            self._start_recording_client, ros_request)

        def extra(result: Any) -> Dict[str, Any]:
            session_id = int(result.session_id)
            return {
                'session_id': '' if session_id == 0 else str(session_id),
                'bag_path': str(result.bag_path),
            }

        return self._service_ack(
            request.request_id, response, failure, extra)

    def _stop_recording(self, request: BridgeRequest) -> Dict[str, Any]:
        ros_request = StopRecording.Request()
        ros_request.session_id = int(request.payload['session_id'])
        response, failure = self._call_service(
            self._stop_recording_client, ros_request)

        def extra(result: Any) -> Dict[str, Any]:
            session_id = int(result.stopped_session_id)
            return {
                'stopped_session_id': (
                    '' if session_id == 0 else str(session_id)
                ),
                'bag_path': str(result.bag_path),
            }

        return self._service_ack(
            request.request_id, response, failure, extra)

    def _set_mimic_result(self, request: BridgeRequest) -> Dict[str, Any]:
        ros_request = SetMimicResult.Request()
        ros_request.session_id = int(request.payload['session_id'])
        ros_request.result = RECORDING_RESULTS.index(request.payload['result'])
        response, failure = self._call_service(
            self._mimic_result_client, ros_request)
        return self._service_ack(request.request_id, response, failure)

    def _reset_safety(self, request: BridgeRequest) -> Dict[str, Any]:
        response, failure = self._call_service(
            self._reset_safety_client, Trigger.Request())
        if response is None:
            return make_ack(request.request_id, False, failure)
        reason = str(response.message)
        if not reason:
            reason = 'accepted' if response.success else 'reset_rejected'
        return make_ack(request.request_id, bool(response.success), reason)

    def _execute_sequence(self, request: BridgeRequest) -> Dict[str, Any]:
        if not self._sequence_client.server_is_ready():
            return make_ack(
                request.request_id, False, 'action_unavailable')
        goal = ExecuteSequence.Goal()
        goal.sequence_name = request.payload['sequence_name']
        goal.speed_limit = float(request.payload['speed_limit'])
        completed, goal_handle = self._wait_future(
            self._sequence_client.send_goal_async(goal))
        if not completed:
            return make_ack(request.request_id, False, 'action_timeout')
        if goal_handle is None:
            return make_ack(request.request_id, False, 'action_failed')
        accepted = bool(goal_handle.accepted)
        reason = 'accepted' if accepted else 'motion_active'
        return make_ack(request.request_id, accepted, reason)


def main(args: Optional[list[str]] = None) -> None:
    """Run the Web Bridge until ROS shutdown."""
    rclpy.init(args=args)
    node: Optional[WebBridgeNode] = None
    try:
        node = WebBridgeNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
