"""
Command Manager 정책을 ROS 2 topic/service에 연결하는 어댑터.

초보자용 간단 매뉴얼
---------------------
1. 역할
   여러 producer(mimic, teleop, gesture/sequence)가 동시에 명령을 보내더라도 현재
   mode·owner·lease 조건을 만족하는 한 종류만 골라 ``/thing/command/selected``로
   전달한다. 제어권 변경과 STOP은 service로 받고 현재 중재 상태는 topic으로 알린다.
2. 입력
   ``/thing/command/{mimic,teleop,manual}``의 ``HandCommand``, Safety/Recording 상태,
   Gesture executor를 포함한 동작 실행기의 ``/thing/control/motion_active``, 그리고
   ``/thing/set_control_mode`` service 요청을 입력으로 받는다.
3. 출력
   선택된 ``HandCommand``, latched ``/thing/control_state``, STOP 요청 이벤트와 mode
   service 응답을 출력한다. STOP 완료 확인은 Guard의 barrier ACK를 기다린 뒤 응답한다.
4. 주요 실행 흐름
   mode 요청 → 코어가 owner 충돌·lease·Safety·녹화·재획득 gate를 판정 → 각 producer
   명령의 topic과 ``source``를 함께 검증 → 통과한 명령만 publish한다. STOP이면 먼저
   코어를 DISABLED/NONE으로 닫고 Guard ACK까지 제한 시간 동안 기다린다.
5. 사용/실행 방법
   ROS 2 workspace에서 패키지를 build/source한 뒤 패키지의 ``command_manager``
   executable을 실행한다(일반적으로 ``ros2 run thing_control command_manager``).
   단위 정책만 시험하려면 ROS 의존성이 없는 ``command_manager_core.py``를 사용한다.
6. 책임 경계와 하지 않는 일
   이 파일은 '누가 명령할 수 있는가'와 ROS 통신만 담당한다. 관절 수치·속도 같은 명령
   내용의 안전성 검사는 Command Guard, 최종 안전 상태 전이는 Safety Manager, gesture나
   sequence의 생성·실행은 각각의 producer/executor 책임이다. 이 노드는 명령을 보정하거나
   대신 실행하지 않는다.

동시성 핵심: mode service가 STOP ACK를 기다리는 동안 ACK callback이 다른 executor
thread에서 진행되어야 한다. 따라서 callback group을 분리하고, 실제 중재 상태 접근은
재진입 mutex(``RLock``) 하나로 직렬화하여 검사와 publish 사이의 경쟁을 막는다.
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
from std_msgs.msg import Bool, UInt64

from thing_interfaces.msg import (
    ControlState,
    HandCommand,
    RecordingState,
    SafetyState,
)
from thing_interfaces.srv import SetControlMode

from thing_control.command_manager_core import CommandManagerCore


# 명령은 최신 값 하나만 필요하지만 유실되면 안 되므로 RELIABLE/KEEP_LAST(1)을 쓴다.
_COMMAND_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
)
# 늦게 접속한 노드도 현재 상태를 즉시 받아야 하므로 상태 topic은 TRANSIENT_LOCAL이다.
_STATE_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)
# STOP transaction과 동작 여부는 순간 이벤트도 보존할 수 있도록 여유 있는 depth를 둔다.
_INTERNAL_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
    reliability=ReliabilityPolicy.RELIABLE,
)


class CommandManagerNode(Node):
    """
    ROS callback을 직렬화해 ``CommandManagerCore`` 정책을 외부에 제공한다.

    ``_arbitration_lock``은 command, timer, state, service callback이 서로 다른 executor
    thread에서 실행되어도 '상태 확인 → 결정 → publish'를 한 덩어리로 보이게 한다.
    코어에도 자체 lock이 있지만, 노드 lock은 코어 변경과 ROS 발행 사이까지 보호한다.
    """

    def __init__(self, parameter_overrides=None) -> None:
        """파라미터를 검증하고 중재 코어, ROS 입출력, 주기 timer를 구성한다."""
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

        # RLock인 이유는 lock을 잡은 callback이 _publish_control_state()를 호출하면서
        # 동일 lock을 다시 획득하기 때문이다. 일반 Lock이면 이 경로가 교착된다.
        self._arbitration_lock = RLock()
        # STOP service가 ACK를 기다리는 동안 같은 default callback group의 ACK callback이
        # 굶지 않도록 두 callback을 별도 group에 둔다. 중재 상태 자체는 위 lock으로
        # 계속 직렬화한다.
        self._mode_service_group = MutuallyExclusiveCallbackGroup()
        self._stop_ack_group = MutuallyExclusiveCallbackGroup()
        # Condition은 ACK를 기다리는 service thread를 잠들게 하고 ACK callback이 깨운다.
        # count snapshot은 요청 전에 이미 수신·계수된 ACK를 이번 응답에서 제외한다.
        # 요청별 correlation ID는 없으므로 count 자체가 개별 transaction을 식별하지는 않는다.
        self._stop_ack_condition = Condition()
        self._stop_generation = 0
        self._pending_stop_generation = None
        self._last_ack_generation = 0
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
            UInt64,
            '/thing/control/stop_requested',
            _INTERNAL_QOS,
        )
        self._stop_ack_subscription = self.create_subscription(
            UInt64,
            '/thing/control/stop_barrier_ack',
            self._on_stop_barrier_ack,
            _INTERNAL_QOS,
            callback_group=self._stop_ack_group,
        )

        # producer 선택은 topic 이름만으로 끝나지 않는다. callback에 허용 source를 함께
        # 묶으므로 잘못된 topic으로 들어온 명령도 거부된다. manual topic은 Gesture와
        # Sequence producer가 공유한다.
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
        """안전 관련 시간값이 양의 정수이며 설계 상한 이하인지 시작 시 확인한다."""
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f'{name} must be a positive integer')
        if value > maximum:
            raise ValueError(f'{name} cannot exceed {maximum}')

    def _on_command(
        self,
        message: HandCommand,
        expected_sources: Iterable[int],
    ) -> None:
        """
        topic/source와 현재 중재 상태가 모두 맞는 명령만 selected로 전달한다.

        명령 payload는 여기서 해석하거나 보정하지 않는다. source 선택 이후의 값 검사는
        Command Guard가 담당하므로 이 callback은 '누가 말하는가'만 판정한다.
        """
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
        """유효한 source timestamp를 나노초로 바꿔 순서 보장 상태로 코어에 전달한다."""
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
        """녹화 중 새 mode 획득을 막는 데 필요한 상태를 코어에 반영한다."""
        with self._arbitration_lock:
            self._core.update_recording_state(
                message.state,
                message.result_pending,
            )

    def _on_motion_active(self, message: Bool) -> None:
        """
        Gesture/Sequence 실행기의 실제 동작 여부를 재획득 중재에 반영한다.

        executor의 동작 시작은 MANUAL owner가 유효할 때만 수용된다. 동작 중에는 다른
        mode/owner가 끼어들 수 없고 STOP·lease 만료 시 running 상태도 함께 닫힌다.
        """
        with self._arbitration_lock:
            if self._core.set_sequence_running(message.data):
                self._publish_control_state()

    def _on_stop_barrier_ack(self, message: UInt64) -> None:
        """
        Guard가 latch를 닫은 뒤 보낸 ACK로 대기 중인 STOP transaction을 깨운다.

        mode service와 다른 callback group/thread에서 실행되어야 한다. 그렇지 않으면
        service가 ACK를 기다리는 동안 ACK callback도 실행되지 못하는 교착이 난다.
        """
        with self._stop_ack_condition:
            if (
                self._pending_stop_generation is None
                or message.data != self._pending_stop_generation
            ):
                return
            self._last_ack_generation = int(message.data)
            self._pending_stop_generation = None
            self._stop_barrier_pending = False
            self._stop_ack_condition.notify_all()

    def _on_set_control_mode(
        self,
        request: SetControlMode.Request,
        response: SetControlMode.Response,
    ) -> SetControlMode.Response:
        """
        mode·owner 획득/갱신/STOP을 처리하고 실제 활성 상태를 응답한다.

        fail-closed 원칙에 따라 lease 만료나 미완료 STOP barrier가 보이면 활성 요청을
        받지 않는다. STOP 성공은 이벤트 발행이 아니라 Guard ACK 수신까지를 뜻하며,
        timeout이어도 코어 상태는 이미 DISABLED/NONE인 채 실패로 응답한다.
        """
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
                stop_event = UInt64()
                stop_stamp_ns = self._system_clock.now().nanoseconds
                with self._stop_ack_condition:
                    # A system-time-derived generation remains ordered across a
                    # Command Manager restart; max(+1) also tolerates equal ticks
                    # or a small in-process wall-clock adjustment.
                    self._stop_generation = max(
                        self._stop_generation + 1,
                        stop_stamp_ns,
                    )
                    stop_generation = self._stop_generation
                    self._pending_stop_generation = stop_generation
                    self._stop_barrier_pending = True
                stop_event.data = stop_generation
                self._stop_event_publisher.publish(stop_event)
                with self._stop_ack_condition:
                    acked = self._stop_ack_condition.wait_for(
                        lambda: self._last_ack_generation == stop_generation,
                        timeout=self._stop_barrier_timeout_sec,
                    )
                    if acked:
                        self._pending_stop_generation = None
                        self._stop_barrier_pending = False
                if not acked:
                    response.accepted = False
                    response.reason = 'stop_barrier_timeout'

            self._publish_control_state()
            return response

    def _on_lease_timer(self) -> None:
        """heartbeat가 끊긴 owner를 주기적으로 해제하고 변경 상태를 즉시 알린다."""
        with self._arbitration_lock:
            if self._core.check_lease():
                self._publish_control_state()

    def _publish_control_state(self) -> None:
        """코어의 일관된 snapshot을 ROS ``ControlState`` 메시지로 발행한다."""
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
