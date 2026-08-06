"""
Gesture service와 Sequence action을 하나의 MANUAL 명령 통로로 실행하는 ROS 2 노드.

초보자용 간단 매뉴얼
---------------------
1. 역할
   Gesture와 Sequence를 각각 따로 움직이는 노드가 아니라, 두 요청을 하나의 실행 차선에
   세워 한 번에 하나만 ``/thing/command/manual``로 보낸다. 즉 같은 손을 두 운전자가
   동시에 조작하지 못하게 하는 'MANUAL 동작 실행 창구'다.
2. 입력
   ``/thing/execute_gesture`` service, ``/thing/execute_sequence`` action, 현재 제어권을
   나타내는 ``/thing/control_state``, Safety 상태, STOP 요청과 Guard barrier ACK, 그리고
   gesture pose·duration·sequence·상태 freshness 관련 파라미터를 입력으로 받는다.
3. 출력
   실행 중 pose와 정상 완료 뒤 마지막 pose를 ``HandCommand``로 변환해
   ``/thing/command/manual``에 주기적으로 발행한다. 동작 유지시간이 끝나면
   ``/thing/control/motion_active``는 false가 되어 다음 요청을 받을 수 있지만 마지막
   자세 heartbeat는 이어진다. Sequence 실행이면 cancel·STOP·제어권 상실·Safety 이상도
   Action 종료 결과로 알린다. Gesture service는 시작 승인만 응답한다.
4. 주요 실행 흐름
   요청 수신 → service/action 공통 admission 차선에서 상호 배제 → 코어가 이름·속도·최신
   제어권·Safety를 검증 → timer가 현재 pose를 주기적으로(기본 20 Hz) 발행 → 정상 완료
   뒤에는 마지막 pose heartbeat를 유지하면서 실행 슬롯만 비운다. 새 요청은 retained pose를
   교체하고, STOP·제어권/Safety 상실은 출력을 닫는다. STOP 뒤에는 ACK 뒤에 관측한
   DISABLED와 RESET/INIT을 각각 확인하고, 그 뒤 READY와 MANUAL 재획득까지 모두 새
   상태 표본으로 확인할 때까지 새 동작을 받지 않는다.
5. 사용/실행 방법
   ROS 2 workspace를 build/source한 뒤 일반적으로
   ``ros2 run thing_control manual_executor``로 실행한다. Web 등 호출자는 먼저 Command
   Manager에서 MANUAL/WEB 제어권을 얻고 Gesture service 또는 Sequence action을 요청한다.
6. 책임 경계와 하지 않는 일
   이 파일은 요청 생명주기와 ROS 통신, 단일 MANUAL producer만 담당한다. mode·owner
   중재는 Command Manager, 최종 명령 범위·순서 검사는 Command Guard, Safety 상태 전이와
   토크·하드웨어 제어는 Safety Manager와 hardware node 책임이다. 이 노드는 pose를
   보간하거나 물리 E-Stop 전원을 차단하지 않는다.

동시성 핵심: Gesture service와 ActionServer의 goal/accepted 경로는 같은
``MutuallyExclusiveCallbackGroup``으로 묶어 승인 순간부터 한 줄로 세운다. 반면 상태,
STOP, timer는 별도 재진입 group과 4-thread executor에서 계속 진행되어 실행 중 동작을
즉시 중단할 수 있다. 공유 상태는 하나의 ``Condition(RLock)`` 아래에서만 변경한다.
"""

from threading import Condition, RLock
from time import monotonic
from typing import Optional

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import (
    MutuallyExclusiveCallbackGroup,
    ReentrantCallbackGroup,
)
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

from thing_control.manual_executor_core import (
    CommandFrame,
    ManualExecutorCore,
    MotionOutcome,
    SequenceStep,
)
from thing_interfaces.action import ExecuteSequence
from thing_interfaces.msg import ControlState, HandCommand, SafetyState
from thing_interfaces.srv import ExecuteGesture


# 손 명령은 최신 frame 하나만 필요하지만 빠뜨리면 pose 유지가 끊기므로 RELIABLE을 쓴다.
_COMMAND_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
)
# 늦게 접속해도 현재 제어권·Safety를 즉시 받도록 상태 topic은 TRANSIENT_LOCAL이다.
_STATE_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)
# STOP transaction과 motion 상태는 순간 이벤트를 놓치지 않도록 여유 있는 depth를 둔다.
_INTERNAL_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
    reliability=ReliabilityPolicy.RELIABLE,
)


_DEFAULT_POSES = {
    'open': (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    'fist': (1.0, 0.6, 0.2, 1.0, 1.0, 1.0, 1.0),
    'pinch': (1.0, 0.6, 0.2, 1.0, 0.0, 0.0, 0.0),
    'cylindrical_grasp': (0.8, 0.6, 0.2, 0.85, 0.85, 0.85, 0.85),
    # Sequence 안에서만 쓰는 내부 pose다. ExecuteGesture의 외부 이름으로는 허용하지 않는다.
    'count_4': (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    'count_3': (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
    'scissors': (1.0, 0.3, 0.2, 0.0, 0.0, 1.0, 1.0),
    'count_1': (1.0, 0.3, 0.2, 0.0, 1.0, 1.0, 1.0),
}
_DEFAULT_GESTURE_DURATIONS_MS = {
    'open': 1000,
    'fist': 1000,
    'pinch': 3000,
    'cylindrical_grasp': 3000,
}
_DEFAULT_SEQUENCES = {
    'countdown': (
        ('open', 800),
        ('count_4', 800),
        ('count_3', 800),
        ('scissors', 800),
        ('count_1', 800),
    ),
    'scissors_rock_paper': (
        ('scissors', 1000),
        ('fist', 1000),
        ('open', 1000),
    ),
}


class ManualExecutorNode(Node):
    """
    Gesture service와 Sequence action을 단 하나의 MANUAL publisher로 직렬화한다.

    ``_condition``은 core, goal-generation 대응표, STOP latch를 한 critical section으로
    보호한다. admission callback group은 '승인 검사 → core 시작' 사이에 다른 종류의
    요청이 끼어드는 것을 막고, 재진입 callback group은 실행 중에도 STOP·상태·timer가
    병행되어 fail-closed 중단을 수행하게 한다.
    """

    def __init__(self, parameter_overrides=None) -> None:
        """파라미터와 pose를 검증하고 공통 실행 코어, ROS 입출력, timer를 구성한다."""
        super().__init__(
            'manual_executor',
            parameter_overrides=parameter_overrides,
        )
        # steady time은 duration/freshness처럼 경과 시간을, system time은 wire stamp의
        # 순서와 미래 timestamp 검사를 담당한다. use_sim_time에 안전 판단이 흔들리지 않는다.
        self._steady_clock = Clock(clock_type=ClockType.STEADY_TIME)
        self._system_clock = Clock(clock_type=ClockType.SYSTEM_TIME)
        self._lock = RLock()
        self._condition = Condition(self._lock)
        # 상태·STOP·timer는 실행 중에도 서로 병행되어야 하므로 재진입 group에 둔다.
        self._callback_group = ReentrantCallbackGroup()
        # ActionServer의 goal 응답부터 handle accepted까지와 Gesture service가 같은 실행
        # 차선을 공유한다. rclpy가 ActionServer entity callback 전체에서 이 group을 잡으므로
        # 'goal은 수락됐지만 handle은 아직 없는 틈'에도 Gesture가 끼어들 수 없다.
        self._admission_callback_group = MutuallyExclusiveCallbackGroup()

        publish_period_ms = self._bounded_positive_int_parameter(
            'publish_period_ms',
            50,
            maximum=50,
        )
        control_timeout_ms = self._bounded_positive_int_parameter(
            'control_state_timeout_ms',
            1500,
            maximum=1500,
        )
        safety_timeout_ms = self._bounded_positive_int_parameter(
            'safety_state_timeout_ms',
            300,
            maximum=300,
        )

        # 외부 설정을 코어에 넘기기 전에 시간 상한과 sequence 구조를 fail-fast 검증한다.
        gestures = {
            name: self.declare_parameter(
                f'gestures.{name}.axes',
                list(default_axes),
            ).value
            for name, default_axes in _DEFAULT_POSES.items()
        }
        gesture_durations_ms = {
            name: self._bounded_positive_int_parameter(
                f'gestures.{name}.duration_ms',
                default_duration,
                maximum=10_000,
            )
            for name, default_duration in _DEFAULT_GESTURE_DURATIONS_MS.items()
        }
        sequences = self._load_sequences()
        self._core = ManualExecutorCore(
            gestures=gestures,
            gesture_durations_ms=gesture_durations_ms,
            sequences=sequences,
            control_state_timeout_ms=control_timeout_ms,
            safety_state_timeout_ms=safety_timeout_ms,
        )

        self._manual_publisher = self.create_publisher(
            HandCommand,
            '/thing/command/manual',
            _COMMAND_QOS,
        )
        self._motion_publisher = self.create_publisher(
            Bool,
            '/thing/control/motion_active',
            _INTERNAL_QOS,
        )
        self._control_subscription = self.create_subscription(
            ControlState,
            '/thing/control_state',
            self._on_control_state,
            _STATE_QOS,
            callback_group=self._callback_group,
        )
        self._safety_subscription = self.create_subscription(
            SafetyState,
            '/thing/safety_state',
            self._on_safety_state,
            _STATE_QOS,
            callback_group=self._callback_group,
        )
        self._stop_subscription = self.create_subscription(
            UInt64,
            '/thing/control/stop_requested',
            self._on_stop_requested,
            _INTERNAL_QOS,
            callback_group=self._callback_group,
        )
        self._stop_ack_subscription = self.create_subscription(
            UInt64,
            '/thing/control/stop_barrier_ack',
            self._on_stop_barrier_ack,
            _INTERNAL_QOS,
            callback_group=self._callback_group,
        )
        self._gesture_service = self.create_service(
            ExecuteGesture,
            '/thing/execute_gesture',
            self._on_execute_gesture,
            callback_group=self._admission_callback_group,
        )

        # STOP latch는 단순 bool 해제가 아니라 같은 generation의 ACK와 그 이후 상태 전이를
        # 로컬 관측 순서로 확인한다. 동일 프로세스에서 topic 순서가 뒤바뀌어도 오래된
        # 복구 표본이 새 동작 admission을 열지 못하게 한다.
        self._stop_latched = False
        self._active_stop_generation = 0
        self._completed_stop_generation = 0
        self._recovery_observation = 0
        self._stop_ack_observation = 0
        self._latest_disabled_observation = 0
        self._latest_manual_observation = 0
        self._latest_recovery_entry_observation = 0
        self._latest_ready_observation = 0
        self._shutting_down = False
        self._last_control_stamp_ns = None
        self._last_control_payload = None
        self._last_safety_stamp_ns = None
        self._last_safety_payload = None
        # rclpy goal handle과 코어 generation을 양방향으로 연결해 feedback/cancel/result가
        # 언제나 자신이 승인된 동작에만 적용되게 한다.
        self._goal_to_generation = {}
        self._generation_to_goal = {}
        self._goal_outcomes = {}
        self._goal_failures = {}
        self._pending_cancel_generations = set()
        # goal response 이후 handle materialization 전의 짧은 구간과 early cancel을 별도로
        # 기록한다. Humble에서 cancel 상태 노출이 늦어도 승인된 cancel을 잃지 않기 위함이다.
        self._goal_reservations = {}
        self._goal_launches_in_progress = 0
        self._goal_callbacks_outstanding = set()
        self._early_cancel_goal_keys = set()
        self._prestart_cancel_handles = {}
        self._pending_stop_acks = {}
        self._last_feedback_step = {}
        self._action_server = ActionServer(
            self,
            ExecuteSequence,
            '/thing/execute_sequence',
            execute_callback=self._execute_sequence,
            goal_callback=self._on_sequence_goal,
            cancel_callback=self._on_sequence_cancel,
            handle_accepted_callback=self._on_sequence_accepted,
            callback_group=self._admission_callback_group,
        )

        self._wire_sequence = 0
        self._publish_timer = self.create_timer(
            float(publish_period_ms) / 1000.0,
            self._on_publish_timer,
            callback_group=self._callback_group,
            clock=self._steady_clock,
        )
        self._publish_motion_state(False)

    def _load_sequences(self):
        """파라미터의 step 이름·duration 배열을 길이와 상한이 검증된 단계들로 바꾼다."""
        sequences = {}
        for sequence_name, defaults in _DEFAULT_SEQUENCES.items():
            default_steps = [step_name for step_name, _ in defaults]
            default_durations = [duration for _, duration in defaults]
            steps = list(
                self.declare_parameter(
                    f'sequences.{sequence_name}.steps',
                    default_steps,
                ).value
            )
            durations = list(
                self.declare_parameter(
                    f'sequences.{sequence_name}.step_durations_ms',
                    default_durations,
                ).value
            )
            if not steps or len(steps) != len(durations):
                raise ValueError(
                    f'sequence {sequence_name} steps and durations must have '
                    'the same non-zero length'
                )
            if len(steps) > 100:
                raise ValueError(
                    f'sequence {sequence_name} cannot exceed 100 steps'
                )
            validated = []
            for step_name, duration in zip(steps, durations):
                if not isinstance(step_name, str) or not step_name:
                    raise ValueError('sequence step names must be non-empty strings')
                if (
                    isinstance(duration, bool)
                    or not isinstance(duration, int)
                    or not 0 < duration <= 10_000
                ):
                    raise ValueError(
                        'sequence step durations must be integers in [1, 10000]'
                    )
                validated.append(SequenceStep(step_name, duration))
            sequences[sequence_name] = tuple(validated)
        return sequences

    def _on_execute_gesture(self, request, response):
        """공통 admission과 코어 검증을 통과한 단일 Gesture를 즉시 시작한다."""
        with self._condition:
            admission_issue = self._local_admission_issue_locked()
            if admission_issue is not None:
                response.accepted = False
                response.reason = admission_issue
                return response
            result = self._core.start_gesture(
                request.gesture_name,
                request.speed_limit,
                now_ns=self._now_ns(),
            )
            response.accepted = result.accepted
            response.reason = result.reason
            if result.accepted:
                self._publish_motion_state(True)
                self._drive_once_locked()
            return response

    def _on_sequence_goal(self, request):
        """수신한 Action goal을 검사하고 handle accepted 전의 예약 표지만 남긴다."""
        with self._condition:
            if self._local_admission_issue_locked() is not None:
                return GoalResponse.REJECT
            validation = self._core.validate_sequence(
                request.sequence_name,
                request.speed_limit,
                now_ns=self._now_ns(),
            )
            if not validation.accepted:
                return GoalResponse.REJECT
            # 이 항목은 종료 정리를 위한 표지일 뿐, Gesture와의 상호 배제 근거는 아니다.
            # 실제 배제는 callback group이 보장하므로 전송 실패로 표지가 남아도 정상 요청
            # 차선 전체가 영구히 닫히지 않는다.
            self._goal_reservations[id(request)] = self._now_ns()
            return GoalResponse.ACCEPT

    def _on_sequence_accepted(self, goal_handle) -> None:
        """수락된 goal을 코어 generation에 연결하고 Action 실행 callback을 예약한다."""
        key = self._goal_key(goal_handle)
        with self._condition:
            self._goal_reservations.pop(id(goal_handle.request), None)
            self._goal_launches_in_progress += 1
            self._goal_callbacks_outstanding.add(key)
        try:
            with self._condition:
                if key in self._early_cancel_goal_keys:
                    self._prestart_cancel_handles[key] = goal_handle
                    start_execution = True
                else:
                    start_execution = False
                admission_issue = self._local_admission_issue_locked()
                if start_execution:
                    pass
                elif admission_issue is not None:
                    self._goal_failures[key] = admission_issue
                else:
                    result = self._core.start_sequence(
                        goal_handle.request.sequence_name,
                        goal_handle.request.speed_limit,
                        now_ns=self._now_ns(),
                    )
                    if not result.accepted:
                        self._goal_failures[key] = result.reason
                    else:
                        self._goal_to_generation[key] = result.generation
                        self._generation_to_goal[result.generation] = goal_handle
                        self._publish_motion_state(True)
                        self._drive_once_locked()
                self._condition.notify_all()
            goal_handle.execute()
        except Exception as error:
            # 수락된 Action이 terminal인데 코어만 계속 발행하면 안 된다. execute 예약 자체가
            # 실패한 경우 코어와 모든 대응표를 먼저 되돌린 뒤 goal을 abort한다.
            with self._condition:
                outcome = self._core.cancel('execute_schedule_failed')
                generation = self._goal_to_generation.pop(key, None)
                if generation is not None:
                    self._generation_to_goal.pop(generation, None)
                    self._pending_cancel_generations.discard(generation)
                    self._last_feedback_step.pop(generation, None)
                self._goal_failures.pop(key, None)
                self._goal_outcomes.pop(key, None)
                self._early_cancel_goal_keys.discard(key)
                self._prestart_cancel_handles.pop(key, None)
                if outcome is not None:
                    try:
                        self._publish_motion_state(False)
                    except Exception:
                        pass
            try:
                goal_handle.abort()
            except Exception:
                pass
            with self._condition:
                self._goal_callbacks_outstanding.discard(key)
                self._condition.notify_all()
            self.get_logger().error(
                f'failed to schedule accepted sequence goal: {error!r}'
            )
        finally:
            with self._condition:
                self._goal_launches_in_progress -= 1
                self._condition.notify_all()

    def _on_sequence_cancel(self, goal_handle):
        """예약 전·실행 중 cancel을 구분해 해당 generation의 출력부터 먼저 막는다."""
        key = self._goal_key(goal_handle)
        with self._condition:
            generation = self._goal_to_generation.get(key)
            if generation is None:
                if id(goal_handle.request) not in self._goal_reservations:
                    return CancelResponse.REJECT
                self._early_cancel_goal_keys.add(key)
                self._prestart_cancel_handles[key] = goal_handle
                return CancelResponse.ACCEPT
            if generation != self._core.active_generation:
                return CancelResponse.REJECT
            # rclpy는 이 callback이 반환된 뒤에야 EXECUTING→CANCELING으로 바꾼다. 즉시
            # canceled()를 부르면 아직 EXECUTING인 middleware 상태와 충돌하므로, 지금은
            # generation을 표시해 출력만 막고 timer가 CANCELING 확인 후 코어를 닫게 한다.
            self._pending_cancel_generations.add(generation)
            return CancelResponse.ACCEPT

    def _execute_sequence(self, goal_handle):
        """Condition에서 코어 종료 결과를 기다린 뒤 정확한 Action terminal 상태를 기록한다."""
        key = self._goal_key(goal_handle)
        with self._condition:
            while (
                key not in self._goal_outcomes
                and key not in self._goal_failures
                and rclpy.ok()
            ):
                self._condition.wait(timeout=0.1)
            failure = self._goal_failures.pop(key, None)
            outcome = self._goal_outcomes.pop(key, None)
            generation = self._goal_to_generation.pop(key, None)
            self._early_cancel_goal_keys.discard(key)
            self._prestart_cancel_handles.pop(key, None)
            if generation is not None:
                self._generation_to_goal.pop(generation, None)
                self._pending_cancel_generations.discard(generation)
                self._last_feedback_step.pop(generation, None)

        try:
            result = ExecuteSequence.Result()
            if failure is not None:
                result.success = False
                result.reason = failure
                if failure == 'cancel_requested':
                    goal_handle.canceled()
                else:
                    goal_handle.abort()
                return result
            if outcome is None:
                result.success = False
                result.reason = 'shutdown'
                goal_handle.abort()
                return result

            result.success = outcome.success
            result.reason = outcome.reason
            if outcome.success:
                goal_handle.succeed()
            elif outcome.reason == 'cancel_requested':
                goal_handle.canceled()
            else:
                goal_handle.abort()
            return result
        finally:
            # request_shutdown()이 이 표지를 기다리므로 middleware terminal 전이가 끝난
            # 뒤에만 제거한다. 먼저 지우면 node가 result 전송 전에 파괴될 수 있다.
            with self._condition:
                self._goal_callbacks_outstanding.discard(key)
                self._condition.notify_all()

    def _on_control_state(self, message: ControlState) -> None:
        """순서가 유효한 제어권 표본을 코어와 STOP 재획득 gate에 반영한다."""
        with self._condition:
            source_stamp_ns = self._ordered_state_stamp_locked(
                kind='control',
                stamp=message.stamp,
                payload=(
                    int(message.active_mode),
                    int(message.active_owner),
                    bool(message.owner_alive),
                ),
            )
            if source_stamp_ns is None:
                return
            observation = self._next_recovery_observation_locked()
            outcome = self._core.update_control_state(
                active_mode=message.active_mode,
                active_owner=message.active_owner,
                owner_alive=message.owner_alive,
                now_ns=self._now_ns(),
            )
            is_disabled = (
                message.active_mode == ControlState.MODE_DISABLED
                and message.active_owner == ControlState.OWNER_NONE
                and not message.owner_alive
            )
            is_reacquired = (
                message.active_mode == ControlState.MODE_MANUAL
                and message.active_owner == ControlState.OWNER_WEB
                and message.owner_alive
            )
            if is_disabled:
                self._latest_disabled_observation = observation
            elif is_reacquired:
                self._latest_manual_observation = observation
            self._try_release_stop_latch_locked()
            if outcome is not None:
                self._finish_outcome_locked(outcome)

    def _on_safety_state(self, message: SafetyState) -> None:
        """순서가 유효한 Safety 표본을 반영하고 이상 상태면 실행을 즉시 종료한다."""
        with self._condition:
            source_stamp_ns = self._ordered_state_stamp_locked(
                kind='safety',
                stamp=message.stamp,
                payload=(int(message.state),),
            )
            if source_stamp_ns is None:
                return
            observation = self._next_recovery_observation_locked()
            outcome = self._core.update_safety_state(
                message.state,
                now_ns=self._now_ns(),
            )
            if message.state in (SafetyState.RESET, SafetyState.INIT):
                self._latest_recovery_entry_observation = observation
            elif message.state == SafetyState.READY:
                self._latest_ready_observation = observation
            self._try_release_stop_latch_locked()
            if outcome is not None:
                self._finish_outcome_locked(outcome)

    def _on_stop_requested(self, message: UInt64) -> None:
        """새 STOP generation을 latch하고 현재 Gesture/Sequence 출력을 즉시 닫는다."""
        with self._condition:
            generation = int(message.data)
            if generation <= 0:
                return
            if generation <= self._completed_stop_generation:
                return
            if generation < self._active_stop_generation:
                return
            if generation > self._active_stop_generation:
                self._active_stop_generation = generation
                # ACK topic이 raw STOP topic보다 먼저 도착할 수 있다. 동일 generation은
                # Guard가 latch를 닫은 뒤 만든 인과 증거이므로 수신 순서를 바꾸지 않고
                # 보존하고, 이후 상태 heartbeat가 ACK 경계 뒤인지 별도로 확인한다.
                self._stop_ack_observation = self._pending_stop_acks.pop(
                    generation,
                    0,
                )
            self._stop_latched = True
            outcome = self._core.cancel('stop_requested')
            if outcome is not None:
                self._finish_outcome_locked(outcome)
            self._try_release_stop_latch_locked()

    def _on_stop_barrier_ack(self, message: UInt64) -> None:
        """Guard generation별 최초 ACK의 로컬 관측 순서만 복구 경계로 저장한다."""
        with self._condition:
            generation = int(message.data)
            if generation <= 0:
                return
            if generation == self._active_stop_generation:
                if self._stop_ack_observation > 0:
                    return
                # matching generation은 Guard가 STOP latch를 닫은 뒤 만든 causal ACK다.
                # raw STOP보다 먼저 전달될 수도 있으므로 최초 ACK 관측 순서를 고정한다.
                self._stop_ack_observation = (
                    self._next_recovery_observation_locked()
                )
                self._try_release_stop_latch_locked()
                return
            if generation > self._active_stop_generation:
                if generation not in self._pending_stop_acks:
                    self._pending_stop_acks[generation] = (
                        self._next_recovery_observation_locked()
                    )
                if len(self._pending_stop_acks) > 16:
                    oldest = min(self._pending_stop_acks)
                    self._pending_stop_acks.pop(oldest, None)

    def _on_publish_timer(self) -> None:
        """오래된 예약을 정리하고 cancel을 명령 발행보다 먼저 처리한 뒤 코어를 진행한다."""
        with self._condition:
            if not self._shutting_down:
                cutoff_ns = self._now_ns() - 5_000_000_000
                for request_id, reserved_ns in tuple(
                    self._goal_reservations.items()
                ):
                    if reserved_ns < cutoff_ns:
                        self._goal_reservations.pop(request_id, None)
            prestart_cancel_pending = self._process_prestart_cancels_locked()
            active_cancel_pending = self._process_pending_cancels_locked()
            if not prestart_cancel_pending and not active_cancel_pending:
                self._drive_once_locked()

    def _local_admission_issue_locked(self):
        """ROS 종료 또는 STOP latch가 새 요청 차선을 닫고 있는지 반환한다."""
        if self._shutting_down:
            return 'shutdown'
        if self._stop_latched:
            return 'stop_latched'
        return None

    def _ordered_state_stamp_locked(self, *, kind, stamp, payload):
        """
        Control/Safety timestamp의 형식·미래값·topic별 순서를 검증하고 수신 이력을 갱신한다.

        같은 timestamp와 같은 payload는 heartbeat 재전송으로 허용하지만, 같은 timestamp에
        내용이 바뀌거나 더 오래된 표본은 상태 전이를 되돌릴 수 있으므로 버린다.
        """
        nanosec = int(stamp.nanosec)
        if not 0 <= nanosec < 1_000_000_000:
            return None
        source_stamp_ns = int(stamp.sec) * 1_000_000_000 + nanosec
        if source_stamp_ns <= 0:
            return None
        if source_stamp_ns > self._system_clock.now().nanoseconds + 100_000_000:
            return None

        if kind == 'control':
            previous_stamp = self._last_control_stamp_ns
            previous_payload = self._last_control_payload
        else:
            previous_stamp = self._last_safety_stamp_ns
            previous_payload = self._last_safety_payload

        if previous_stamp is not None:
            if source_stamp_ns < previous_stamp:
                return None
            if source_stamp_ns == previous_stamp and payload != previous_payload:
                return None

        if kind == 'control':
            self._last_control_stamp_ns = source_stamp_ns
            self._last_control_payload = payload
        else:
            self._last_safety_stamp_ns = source_stamp_ns
            self._last_safety_payload = payload
        return source_stamp_ns

    def _next_recovery_observation_locked(self) -> int:
        """서로 다른 STOP·상태 topic callback에 프로세스 내부 단조 순번을 부여한다."""
        self._recovery_observation += 1
        return self._recovery_observation

    def _try_release_stop_latch_locked(self) -> None:
        """
        동일 STOP의 ACK 뒤 DISABLED와 RESET/INIT을 각각 확인한 후 READY와 MANUAL을 확인한다.

        DISABLED와 RESET/INIT 사이에는 순서를 요구하지 않는다. 둘 다 ACK보다 나중에
        관측되어야 하고, READY는 RESET/INIT보다, MANUAL은 DISABLED와 READY 모두보다
        나중이어야 한다. 서로 다른 topic의 전달 순서를 가정하지 않고 local observation
        sequence로 fail-closed 판정한다.
        """
        if not self._stop_latched or self._active_stop_generation <= 0:
            return
        if self._stop_ack_observation <= 0:
            return
        if self._latest_disabled_observation <= self._stop_ack_observation:
            return
        if (
            self._latest_recovery_entry_observation
            <= self._stop_ack_observation
        ):
            return
        if (
            self._latest_ready_observation
            <= self._latest_recovery_entry_observation
        ):
            return
        if self._latest_manual_observation <= max(
            self._latest_disabled_observation,
            self._latest_ready_observation,
        ):
            return
        self._completed_stop_generation = self._active_stop_generation
        self._stop_latched = False

    def _process_prestart_cancels_locked(self) -> bool:
        """수락한 goal의 시작 전 cancel이 middleware에 보일 때까지 출력 없이 기다린다."""
        pending = False
        for key, goal_handle in tuple(self._prestart_cancel_handles.items()):
            if key not in self._early_cancel_goal_keys:
                self._prestart_cancel_handles.pop(key, None)
                continue
            if not goal_handle.is_cancel_requested:
                pending = True
                continue
            self._goal_failures[key] = 'cancel_requested'
            self._prestart_cancel_handles.pop(key, None)
            self._condition.notify_all()
        return pending

    def _process_pending_cancels_locked(self) -> bool:
        """승인된 active cancel의 출력을 즉시 억제하고 CANCELING 확인 뒤 코어를 닫는다."""
        suppress_active_drive = False
        for generation in tuple(self._pending_cancel_generations):
            goal_handle = self._generation_to_goal.get(generation)
            if goal_handle is None:
                self._pending_cancel_generations.discard(generation)
                continue
            if generation == self._core.active_generation:
                # cancel callback이 이 generation을 이미 승인했다. Humble이 callback 반환
                # 전에는 CANCELING을 아직 노출하지 않아도 이 전환 틈에서 명령은 나가면 안 된다.
                suppress_active_drive = True
            if not goal_handle.is_cancel_requested:
                continue
            if generation == self._core.active_generation:
                outcome = self._core.cancel('cancel_requested')
                if outcome is not None:
                    self._finish_outcome_locked(outcome)
            self._pending_cancel_generations.discard(generation)
        return suppress_active_drive

    def _drive_once_locked(self) -> None:
        """코어를 한 tick 진행해 명령·feedback·종료 결과를 ROS 출력으로 번역한다."""
        tick = self._core.tick(now_ns=self._now_ns())
        if tick.command is not None:
            self._publish_command(tick.command)
        if tick.current_step > 0 and self._core.is_sequence_running:
            generation = self._core.active_generation
            # feedback은 pose frame이 아니라 step 진행 안내다. 명령과 같은 20 Hz로 쏘지
            # 않고 step 전환 때 한 번만 보내 Action channel 폭주를 막는다.
            if self._last_feedback_step.get(generation) != tick.current_step:
                self._last_feedback_step[generation] = tick.current_step
                goal_handle = self._generation_to_goal.get(generation)
                if goal_handle is not None:
                    feedback = ExecuteSequence.Feedback()
                    feedback.current_step = tick.current_step
                    feedback.total_steps = tick.total_steps
                    feedback.active_gesture = tick.active_gesture
                    goal_handle.publish_feedback(feedback)
        if tick.outcome is not None:
            self._finish_outcome_locked(tick.outcome)

    def _finish_outcome_locked(self, outcome: MotionOutcome) -> None:
        """motion_active를 내리고 Sequence 종료 결과를 대기 중 Action callback에 전달한다."""
        self._publish_motion_state(False)
        if outcome.kind == 'sequence':
            goal_handle = self._generation_to_goal.get(outcome.generation)
            if goal_handle is not None:
                key = self._goal_key(goal_handle)
                self._goal_outcomes[key] = outcome
        self._condition.notify_all()

    def _publish_command(self, frame: CommandFrame) -> None:
        """코어의 ROS 독립 frame을 source와 uint32 순번이 있는 HandCommand로 바꿔 발행한다."""
        self._wire_sequence = (self._wire_sequence + 1) & 0xFFFFFFFF
        message = HandCommand()
        message.stamp = self._system_clock.now().to_msg()
        message.sequence = self._wire_sequence
        message.source = frame.source
        (
            message.thumb_flex,
            message.thumb_opp,
            message.thumb_abd,
            message.index_flex,
            message.middle_flex,
            message.ring_flex,
            message.little_flex,
        ) = frame.axes
        message.speed_limit = frame.speed_limit
        message.confidence = 1.0
        self._manual_publisher.publish(message)

    def _publish_motion_state(self, active: bool) -> None:
        """Command Manager가 mode 변경을 막는 데 쓰는 실제 동작 여부를 발행한다."""
        message = Bool()
        message.data = bool(active)
        self._motion_publisher.publish(message)

    def _now_ns(self) -> int:
        """wall-clock 보정과 무관한 duration/freshness용 monotonic 시각을 반환한다."""
        return self._steady_clock.now().nanoseconds

    @staticmethod
    def _goal_key(goal_handle) -> bytes:
        """수신한 goal UUID를 dictionary에서 안정적으로 쓸 bytes key로 바꾼다."""
        return bytes(goal_handle.goal_id.uuid)

    def _bounded_positive_int_parameter(
        self,
        name: str,
        default: int,
        *,
        maximum: int,
    ) -> int:
        """시간 관련 ROS 파라미터가 bool이 아닌 양의 정수이며 설계 상한 이내인지 본다."""
        value = self.declare_parameter(name, default).value
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 < value <= maximum
        ):
            raise ValueError(f'{name} must be an integer in [1, {maximum}]')
        return value

    def request_shutdown(self) -> None:
        """새 요청을 닫고 executor spin은 유지한 채 Action 종료 절차를 시작한다."""
        with self._condition:
            if self._shutting_down:
                return
            self._shutting_down = True
            for key, goal_handle in tuple(
                self._prestart_cancel_handles.items()
            ):
                # membership는 cancel callback이 이미 ACCEPT를 반환했다는 뜻이다. Humble은
                # ActionServer transaction 완료 전까지 is_cancel_requested를 늦게 노출할 수
                # 있으므로 이 구간에는 로컬 accepted-cancel 표지가 권위 있는 근거다.
                reason = (
                    'cancel_requested'
                    if key in self._early_cancel_goal_keys
                    or goal_handle.is_cancel_requested
                    else 'shutdown'
                )
                self._goal_failures.setdefault(key, reason)
            # handle accepted 전 승인된 cancel 표지는 execute callback이 middleware terminal
            # 전이까지 마칠 때까지 보존한다. 해당 표지 정리는 _execute_sequence가 소유한다.

            active_generation = self._core.active_generation
            outcome = None
            if active_generation in self._pending_cancel_generations:
                goal_handle = self._generation_to_goal.get(active_generation)
                # cancel callback이 이미 ACCEPT를 반환했다. Humble의 지연된 EXECUTING→
                # CANCELING 전이와 abort를 경쟁시키지 않고, 계속 도는 timer가 상태가 보이는
                # 즉시 cancel을 완료하게 한다.
                if (
                    goal_handle is not None
                    and goal_handle.is_cancel_requested
                ):
                    outcome = self._core.cancel('cancel_requested')
            else:
                outcome = self._core.cancel('shutdown')

            if outcome is not None:
                if rclpy.ok():
                    self._finish_outcome_locked(outcome)
                elif outcome.kind == 'sequence':
                    # rclpy context가 이미 무효면 ROS 상태 전이·발행을 할 수 없다. 대신 로컬
                    # waiter를 깨워 node 파괴 시간이 무한정 늘지 않게 한다.
                    goal_handle = self._generation_to_goal.get(
                        outcome.generation
                    )
                    if goal_handle is not None:
                        self._goal_outcomes[
                            self._goal_key(goal_handle)
                        ] = outcome
            self._condition.notify_all()

    def shutdown_complete(self) -> bool:
        """예약·handle 시작·Action 종료 callback이 모두 비어 안전하게 파괴 가능한지 본다."""
        with self._condition:
            return not (
                self._goal_reservations
                or self._goal_launches_in_progress
                or self._goal_callbacks_outstanding
            )

    def abandon_shutdown_reservations(self) -> None:
        """응답/handle 전송 실패 뒤 남은 예약을 버려 종료 대기 시간을 제한한다."""
        with self._condition:
            self._goal_reservations.clear()
            self._condition.notify_all()

    def destroy_node(self):
        """ActionServer를 먼저 닫은 뒤 기본 Node 자원을 파괴한다."""
        self.request_shutdown()
        self._action_server.destroy()
        return super().destroy_node()


def _drain_shutdown(
    node: ManualExecutorNode,
    executor: MultiThreadedExecutor,
    *,
    timeout_sec: float = 2.0,
) -> bool:
    """제한 시간 동안 executor를 계속 돌려 accepted Action을 terminal 상태까지 배출한다."""
    node.request_shutdown()
    deadline = monotonic() + timeout_sec
    while rclpy.ok() and not node.shutdown_complete():
        remaining = deadline - monotonic()
        if remaining <= 0.0:
            node.abandon_shutdown_reservations()
            node.get_logger().error('Action shutdown drain timed out')
            return False
        executor.spin_once(timeout_sec=min(0.05, remaining))
    return node.shutdown_complete() or not rclpy.ok()


def main(args: Optional[list] = None) -> None:
    """service/action/state/timer와 STOP 중단을 병행 처리하는 4-thread executor로 실행한다."""
    rclpy.init(args=args)
    node = ManualExecutorNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception:
        # Humble은 SIGTERM을 ExternalShutdownException 대신 MultiThreadedExecutor의
        # RCLError("context is not valid")로 올릴 수 있다. 이미 종료된 context만 삼키고
        # 실제 callback 예외는 다시 올려 장애를 숨기지 않는다.
        if rclpy.ok():
            raise
    finally:
        if rclpy.ok():
            _drain_shutdown(node, executor, timeout_sec=2.0)
        if not executor.shutdown(timeout_sec=2.0):
            node.get_logger().error('executor shutdown timed out')
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
