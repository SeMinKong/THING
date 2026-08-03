"""
여러 명령원 중 현재 제어권을 가진 하나만 고르는 ROS 2 adapter.

Command Manager는 교차로의 신호수처럼 mode·owner·lease를 관리하고 선택된 명령만
``/thing/command/selected``로 보낸다. 명령 수치가 안전한지는 Command Guard가 다시
검사하며, 최종 안전 상태 전이는 Safety Manager가 담당한다.
"""

from functools import partial
from threading import Condition, RLock
from typing import Iterable, Optional

import rclpy
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.clock import Clock, ClockType
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
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
    """mode·owner·SafetyState로 명령원 하나를 선택하고 lease를 관리한다."""

    def __init__(self, parameter_overrides=None) -> None:
        super().__init__(
            'command_manager',
            parameter_overrides=parameter_overrides,
        )
        self._steady_clock = Clock(clock_type=ClockType.STEADY_TIME)
        self._system_clock = Clock(clock_type=ClockType.SYSTEM_TIME)

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
        stop_barrier_timeout_ms = self.declare_parameter(
            'stop_barrier_timeout_ms',
            300,
        ).value
        self._validate_bounded_parameter(
            'owner_lease_timeout_ms',
            owner_lease_timeout_ms,
            3000,
        )
        self._validate_bounded_parameter(
            'stop_reacquire_delay_ms',
            stop_reacquire_delay_ms,
            500,
        )
        self._validate_bounded_parameter(
            'lease_check_period_ms',
            lease_check_period_ms,
            100,
        )
        self._validate_bounded_parameter(
            'state_publish_period_ms',
            state_publish_period_ms,
            1000,
        )
        self._validate_bounded_parameter(
            'stop_barrier_timeout_ms',
            stop_barrier_timeout_ms,
            500,
        )

        self._arbitration_lock = RLock()
        # STOP service가 ACK를 기다리는 동안 같은 default callback group의 ACK callback이
        # 굶지 않도록 두 callback을 별도 group에 둔다. 중재 상태 자체는 위 lock으로
        # 계속 직렬화한다.
        self._mode_service_group = MutuallyExclusiveCallbackGroup()
        self._stop_ack_group = MutuallyExclusiveCallbackGroup()
        self._stop_ack_condition = Condition()
        self._stop_ack_count = 0
        self._stop_barrier_pending = False
        self._stop_barrier_timeout_sec = float(stop_barrier_timeout_ms) / 1000.0
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
        self._stop_ack_subscription = self.create_subscription(
            Empty,
            '/thing/control/stop_barrier_ack',
            self._on_stop_barrier_ack,
            _INTERNAL_QOS,
            callback_group=self._stop_ack_group,
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
            callback_group=self._mode_service_group,
        )

        self._lease_timer = self.create_timer(
            float(lease_check_period_ms) / 1000.0,
            self._on_lease_timer,
            clock=self._steady_clock,
        )
        self._state_timer = self.create_timer(
            float(state_publish_period_ms) / 1000.0,
            self._publish_control_state,
            clock=self._steady_clock,
        )
        self._publish_control_state()

    @staticmethod
    def _validate_bounded_parameter(name: str, value, maximum: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f'{name} must be a positive integer')
        if value > maximum:
            raise ValueError(f'{name} cannot exceed {maximum}')

    def _on_command(
        self,
        message: HandCommand,
        expected_sources: Iterable[int],
    ) -> None:
        with self._arbitration_lock:
            if message.source not in expected_sources:
                return
            # accepts_source()도 만료를 fail-closed 처리하지만 bool만 반환한다. 먼저 변경
            # 여부를 consume해 만료 ControlState가 timer보다 늦어지지 않게 즉시 발행한다.
            if self._core.check_lease():
                self._publish_control_state()
                return
            if not self._core.accepts_source(message.source):
                # 첫 검사 직후 deadline을 넘은 경우 accepts_source()는 mutation 없이
                # 거부하고, 여기서 만료를 consume해 ControlState를 누락 없이 발행한다.
                if self._core.check_lease():
                    self._publish_control_state()
                return
            self._selected_publisher.publish(message)

    def _on_safety_state(self, message: SafetyState) -> None:
        with self._arbitration_lock:
            if message.stamp.nanosec < 0 or message.stamp.nanosec >= 1_000_000_000:
                return
            source_stamp_ns = (
                int(message.stamp.sec) * 1_000_000_000
                + int(message.stamp.nanosec)
            )
            if source_stamp_ns <= 0:
                return
            if self._core.update_safety_state(
                message.state,
                source_stamp_ns,
            ):
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

    def _on_stop_barrier_ack(self, message: Empty) -> None:
        """Guard가 latch를 닫은 뒤 보낸 ACK로 대기 중인 STOP transaction을 깨운다."""
        del message
        with self._stop_ack_condition:
            self._stop_ack_count += 1
            self._stop_barrier_pending = False
            self._stop_ack_condition.notify_all()

    def _on_set_control_mode(
        self,
        request: SetControlMode.Request,
        response: SetControlMode.Response,
    ) -> SetControlMode.Response:
        with self._arbitration_lock:
            if self._core.check_lease():
                self._publish_control_state()
                state = self._core.snapshot()
                response.accepted = False
                response.active_mode = state.active_mode
                response.active_owner = state.active_owner
                response.reason = 'owner_lease_expired'
                return response
            # 직전 STOP의 ACK가 오지 않았다면 재획득을 허용하지 않는다. mode/owner는
            # 이미 DISABLED/NONE이므로 실패 시에도 fail-closed 상태가 유지된다.
            with self._stop_ack_condition:
                barrier_pending = self._stop_barrier_pending
            if (
                request.requested_mode != ControlState.MODE_DISABLED
                and barrier_pending
            ):
                state = self._core.snapshot()
                response.accepted = False
                response.active_mode = state.active_mode
                response.active_owner = state.active_owner
                response.reason = 'stop_barrier_pending'
                return response

            result = self._core.request_mode(
                request.requested_mode,
                request.requested_owner,
                (
                    self._system_clock.now().nanoseconds
                    if request.requested_mode == ControlState.MODE_DISABLED
                    else None
                ),
            )
            response.accepted = result.accepted
            response.active_mode = result.active_mode
            response.active_owner = result.active_owner
            response.reason = result.reason
            if not result.accepted:
                if result.reason == 'owner_lease_expired':
                    # 위 check 직후 deadline을 넘긴 경계에서도 만료 상태를 먼저
                    # 발행하고 현재 요청은 재시도 대상으로 남긴다.
                    self._publish_control_state()
                return response

            if request.requested_mode == ControlState.MODE_DISABLED:
                # service 성공은 publish 호출이 아니라 Guard latch 완료를 뜻한다. ACK 전
                # response를 보내면 호출자가 "정지 완료"로 오해할 수 있으므로 bounded
                # wait로 분산 STOP transaction을 선형화한다.
                with self._stop_ack_condition:
                    ack_count_before_request = self._stop_ack_count
                    self._stop_barrier_pending = True
                self._stop_event_publisher.publish(Empty())
                with self._stop_ack_condition:
                    acked = self._stop_ack_condition.wait_for(
                        lambda: self._stop_ack_count > ack_count_before_request,
                        timeout=self._stop_barrier_timeout_sec,
                    )
                if not acked:
                    response.accepted = False
                    response.reason = 'stop_barrier_timeout'

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
    """STOP service와 ACK callback을 병행 처리하는 2-thread executor로 실행한다."""
    rclpy.init(args=args)
    node = CommandManagerNode()
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        executor.shutdown(timeout_sec=1.0)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
