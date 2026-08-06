"""ROS 2 adapter tests for the unified Manual Executor node."""

from contextlib import contextmanager
from threading import Thread
from time import monotonic, sleep

import pytest

rclpy = pytest.importorskip('rclpy')
from action_msgs.msg import GoalStatus  # noqa: E402
from rclpy.action import (  # noqa: E402
    ActionClient,
    CancelResponse,
    GoalResponse,
)
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup  # noqa: E402
from rclpy.executors import MultiThreadedExecutor  # noqa: E402
from rclpy.parameter import Parameter  # noqa: E402
from rclpy.qos import (  # noqa: E402
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from std_msgs.msg import Bool, UInt64  # noqa: E402

from thing_control.manual_executor import (  # noqa: E402
    ManualExecutorNode,
    _drain_shutdown,
)
from thing_interfaces.action import ExecuteSequence  # noqa: E402
from thing_interfaces.msg import (  # noqa: E402
    ControlState,
    HandCommand,
    SafetyState,
)
from thing_interfaces.srv import ExecuteGesture  # noqa: E402


STATE_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)
COMMAND_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
)
INTERNAL_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
    reliability=ReliabilityPolicy.RELIABLE,
)


def wait_until(predicate, timeout=3.0, period=0.01):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(period)
    return predicate()


@contextmanager
def running_manual_executor(overrides=None):
    if not rclpy.ok():
        rclpy.init()
    parameters = [
        Parameter('gestures.open.duration_ms', value=200),
        Parameter('gestures.fist.duration_ms', value=200),
        Parameter('sequences.countdown.steps', value=['open', 'fist']),
        Parameter(
            'sequences.countdown.step_durations_ms',
            value=[250, 250],
        ),
    ]
    parameters.extend(overrides or [])
    node = ManualExecutorNode(parameter_overrides=parameters)
    probe = rclpy.create_node('manual_executor_test_probe')
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    executor.add_node(probe)
    thread = Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        yield node, probe
    finally:
        node.request_shutdown()
        assert executor.shutdown(timeout_sec=2.0)
        thread.join(timeout=1.0)
        node.destroy_node()
        probe.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def publish_manual_ready(node, probe):
    control_publisher = probe.create_publisher(
        ControlState,
        '/thing/control_state',
        STATE_QOS,
    )
    safety_publisher = probe.create_publisher(
        SafetyState,
        '/thing/safety_state',
        STATE_QOS,
    )
    assert wait_until(
        lambda: control_publisher.get_subscription_count() == 1
        and safety_publisher.get_subscription_count() == 1
    )

    def publish_state():
        control = ControlState()
        control.stamp = probe.get_clock().now().to_msg()
        control.active_mode = ControlState.MODE_MANUAL
        control.active_owner = ControlState.OWNER_WEB
        control.owner_alive = True
        safety = SafetyState()
        safety.stamp = probe.get_clock().now().to_msg()
        safety.state = SafetyState.READY
        control_publisher.publish(control)
        safety_publisher.publish(safety)

    publish_state()
    heartbeat_timer = probe.create_timer(0.05, publish_state)
    assert wait_until(
        lambda: node._core._control_received_ns is not None
        and node._core._safety_received_ns is not None
    )
    return control_publisher, safety_publisher, heartbeat_timer


def test_gesture_service_retains_last_pose_and_accepts_a_replacement():
    with running_manual_executor() as (node, probe):
        commands = []
        motion_states = []
        command_subscription = probe.create_subscription(
            HandCommand,
            '/thing/command/manual',
            commands.append,
            COMMAND_QOS,
        )
        motion_subscription = probe.create_subscription(
            Bool,
            '/thing/control/motion_active',
            lambda message: motion_states.append(message.data),
            INTERNAL_QOS,
        )
        del command_subscription, motion_subscription
        publish_manual_ready(node, probe)
        client = probe.create_client(ExecuteGesture, '/thing/execute_gesture')
        assert wait_until(client.service_is_ready)
        assert wait_until(lambda: node._manual_publisher.get_subscription_count() == 1)

        request = ExecuteGesture.Request()
        request.gesture_name = 'open'
        request.speed_limit = 0.5
        future = client.call_async(request)
        assert wait_until(future.done)

        assert future.result().accepted is True
        assert wait_until(lambda: len(commands) >= 3)
        assert all(message.source == HandCommand.SOURCE_GESTURE for message in commands)
        assert all(message.speed_limit == pytest.approx(0.5) for message in commands)
        assert wait_until(lambda: motion_states and motion_states[-1] is False)
        count_at_completion = len(commands)
        sleep(0.15)
        assert len(commands) >= count_at_completion + 2
        assert all(message.thumb_flex == pytest.approx(0.0) for message in commands)
        assert motion_states[0] is True

        replacement_start = len(commands)
        request.gesture_name = 'fist'
        request.speed_limit = 0.4
        replacement = client.call_async(request)
        assert wait_until(replacement.done)
        assert replacement.result().accepted is True
        assert wait_until(
            lambda: any(
                message.thumb_flex == pytest.approx(1.0)
                and message.speed_limit == pytest.approx(0.4)
                for message in commands[replacement_start:]
            )
        )


def test_sequence_action_blocks_gesture_service_and_publishes_feedback():
    with running_manual_executor() as (node, probe):
        commands = []
        subscription = probe.create_subscription(
            HandCommand,
            '/thing/command/manual',
            commands.append,
            COMMAND_QOS,
        )
        del subscription
        publish_manual_ready(node, probe)
        gesture_client = probe.create_client(
            ExecuteGesture,
            '/thing/execute_gesture',
        )
        action_client = ActionClient(
            probe,
            ExecuteSequence,
            '/thing/execute_sequence',
        )
        assert wait_until(gesture_client.service_is_ready)
        assert wait_until(action_client.server_is_ready)
        assert wait_until(lambda: node._manual_publisher.get_subscription_count() == 1)

        feedback = []
        goal = ExecuteSequence.Goal()
        goal.sequence_name = 'countdown'
        goal.speed_limit = 0.8
        goal_future = action_client.send_goal_async(
            goal,
            feedback_callback=lambda wrapped: feedback.append(wrapped.feedback),
        )
        assert wait_until(goal_future.done)
        goal_handle = goal_future.result()
        assert goal_handle.accepted is True
        assert wait_until(lambda: node._core.is_sequence_running)

        request = ExecuteGesture.Request()
        request.gesture_name = 'open'
        request.speed_limit = 1.0
        blocked_future = gesture_client.call_async(request)
        assert wait_until(blocked_future.done)
        assert blocked_future.result().accepted is False
        assert blocked_future.result().reason == 'motion_active'

        result_future = goal_handle.get_result_async()
        assert wait_until(result_future.done)
        wrapped_result = result_future.result()
        assert wrapped_result.status == GoalStatus.STATUS_SUCCEEDED
        assert wrapped_result.result.success is True
        assert wrapped_result.result.reason == 'completed'
        assert {message.source for message in commands} == {
            HandCommand.SOURCE_SEQUENCE,
        }
        assert feedback
        assert feedback[-1].current_step == 2
        assert feedback[-1].total_steps == 2


def test_stop_preempts_action_and_stops_the_shared_output_immediately():
    with running_manual_executor() as (node, probe):
        commands = []
        subscription = probe.create_subscription(
            HandCommand,
            '/thing/command/manual',
            commands.append,
            COMMAND_QOS,
        )
        stop_publisher = probe.create_publisher(
            UInt64,
            '/thing/control/stop_requested',
            INTERNAL_QOS,
        )
        ack_publisher = probe.create_publisher(
            UInt64,
            '/thing/control/stop_barrier_ack',
            INTERNAL_QOS,
        )
        del subscription
        control_publisher, safety_publisher, heartbeat = publish_manual_ready(
            node,
            probe,
        )
        action_client = ActionClient(
            probe,
            ExecuteSequence,
            '/thing/execute_sequence',
        )
        assert wait_until(action_client.server_is_ready)
        assert wait_until(lambda: stop_publisher.get_subscription_count() == 1)
        assert wait_until(lambda: node._manual_publisher.get_subscription_count() == 1)

        goal = ExecuteSequence.Goal()
        goal.sequence_name = 'countdown'
        goal.speed_limit = 1.0
        goal_future = action_client.send_goal_async(goal)
        assert wait_until(goal_future.done)
        goal_handle = goal_future.result()
        assert goal_handle.accepted is True
        assert wait_until(lambda: len(commands) >= 2)

        stop_event = UInt64()
        stop_event.data = 1
        stop_publisher.publish(stop_event)
        result_future = goal_handle.get_result_async()
        assert wait_until(result_future.done)
        wrapped_result = result_future.result()
        assert wrapped_result.status == GoalStatus.STATUS_ABORTED
        assert wrapped_result.result.success is False
        assert wrapped_result.result.reason == 'stop_requested'
        count_after_stop = len(commands)
        sleep(0.15)
        assert len(commands) == count_after_stop

        gesture_client = probe.create_client(
            ExecuteGesture,
            '/thing/execute_gesture',
        )
        request = ExecuteGesture.Request()
        request.gesture_name = 'open'
        request.speed_limit = 1.0
        blocked = gesture_client.call_async(request)
        assert wait_until(blocked.done)
        assert blocked.result().accepted is False
        assert blocked.result().reason == 'stop_latched'

        # Only a causally post-STOP DISABLED state followed by a newer MANUAL
        # acquisition reopens admission.
        heartbeat.cancel()
        sleep(0.01)
        acknowledgement = UInt64()
        acknowledgement.data = 1
        ack_publisher.publish(acknowledgement)
        assert wait_until(lambda: node._stop_ack_observation > 0)
        sleep(0.01)
        disabled = ControlState()
        disabled.stamp = probe.get_clock().now().to_msg()
        disabled.active_mode = ControlState.MODE_DISABLED
        disabled.active_owner = ControlState.OWNER_NONE
        disabled.owner_alive = False
        control_publisher.publish(disabled)
        assert wait_until(
            lambda: node._core._control_mode == ControlState.MODE_DISABLED
        )

        sleep(0.01)
        reset = SafetyState()
        reset.stamp = probe.get_clock().now().to_msg()
        reset.state = SafetyState.RESET
        safety_publisher.publish(reset)
        assert wait_until(lambda: node._core._safety_state == SafetyState.RESET)
        sleep(0.01)
        ready = SafetyState()
        ready.stamp = probe.get_clock().now().to_msg()
        ready.state = SafetyState.READY
        safety_publisher.publish(ready)
        assert wait_until(lambda: node._core._safety_state == SafetyState.READY)

        sleep(0.01)
        manual = ControlState()
        manual.stamp = probe.get_clock().now().to_msg()
        manual.active_mode = ControlState.MODE_MANUAL
        manual.active_owner = ControlState.OWNER_WEB
        manual.owner_alive = True
        control_publisher.publish(manual)
        assert wait_until(lambda: node._stop_latched is False)

        accepted = gesture_client.call_async(request)
        assert wait_until(accepted.done)
        assert accepted.result().accepted is True


def test_correlated_stop_generation_handles_ack_before_raw_stop_fail_closed():
    with running_manual_executor() as (node, probe):
        _, _, heartbeat = publish_manual_ready(node, probe)
        heartbeat.cancel()
        sleep(0.01)

        stop = UInt64()
        stop.data = 7
        acknowledgement = UInt64()
        acknowledgement.data = 7

        # ACK can overtake the raw STOP on a different DDS topic. Matching
        # generation preserves correlation, but states stamped before the local
        # ACK boundary must not reopen admission.
        stale_disabled = ControlState()
        stale_disabled.stamp = probe.get_clock().now().to_msg()
        stale_disabled.active_mode = ControlState.MODE_DISABLED
        stale_disabled.active_owner = ControlState.OWNER_NONE
        stale_disabled.owner_alive = False
        stale_reset = SafetyState()
        stale_reset.stamp = probe.get_clock().now().to_msg()
        stale_reset.state = SafetyState.RESET
        stale_ready = SafetyState()
        stale_ready.stamp = probe.get_clock().now().to_msg()
        stale_ready.state = SafetyState.READY
        stale_manual = ControlState()
        stale_manual.stamp = probe.get_clock().now().to_msg()
        stale_manual.active_mode = ControlState.MODE_MANUAL
        stale_manual.active_owner = ControlState.OWNER_WEB
        stale_manual.owner_alive = True

        node._on_safety_state(stale_reset)
        node._on_stop_barrier_ack(acknowledgement)
        pending_ack_observation = node._pending_stop_acks[7]
        node._on_stop_barrier_ack(acknowledgement)
        assert node._pending_stop_acks[7] == pending_ack_observation
        node._on_control_state(stale_disabled)
        node._on_safety_state(stale_ready)
        node._on_control_state(stale_manual)
        node._on_stop_requested(stop)
        assert node._stop_latched is True

        # Fresh post-ACK heartbeats complete the partial order and reopen.
        sleep(0.01)
        disabled = ControlState()
        disabled.stamp = probe.get_clock().now().to_msg()
        disabled.active_mode = ControlState.MODE_DISABLED
        disabled.active_owner = ControlState.OWNER_NONE
        disabled.owner_alive = False
        node._on_control_state(disabled)
        sleep(0.01)
        reset = SafetyState()
        reset.stamp = probe.get_clock().now().to_msg()
        reset.state = SafetyState.RESET
        node._on_safety_state(reset)
        sleep(0.01)
        ready = SafetyState()
        ready.stamp = probe.get_clock().now().to_msg()
        ready.state = SafetyState.READY
        node._on_safety_state(ready)
        sleep(0.01)
        manual = ControlState()
        manual.stamp = probe.get_clock().now().to_msg()
        manual.active_mode = ControlState.MODE_MANUAL
        manual.active_owner = ControlState.OWNER_WEB
        manual.owner_alive = True
        node._on_control_state(manual)

        assert node._stop_latched is False
        assert node._completed_stop_generation == 7

        newer = UInt64()
        newer.data = 8
        node._on_stop_requested(newer)
        assert node._stop_latched is True
        node._on_stop_requested(stop)
        assert node._active_stop_generation == 8
        assert node._stop_latched is True


def test_duplicate_matching_ack_does_not_move_recovery_boundary():
    with running_manual_executor() as (node, probe):
        _, _, heartbeat = publish_manual_ready(node, probe)
        heartbeat.cancel()
        sleep(0.01)

        stop = UInt64()
        stop.data = 9
        acknowledgement = UInt64()
        acknowledgement.data = 9
        node._on_stop_requested(stop)
        node._on_stop_barrier_ack(acknowledgement)
        first_ack_observation = node._stop_ack_observation

        sleep(0.01)
        disabled = ControlState()
        disabled.stamp = probe.get_clock().now().to_msg()
        disabled.active_mode = ControlState.MODE_DISABLED
        disabled.active_owner = ControlState.OWNER_NONE
        disabled.owner_alive = False
        node._on_control_state(disabled)
        sleep(0.01)
        reset = SafetyState()
        reset.stamp = probe.get_clock().now().to_msg()
        reset.state = SafetyState.RESET
        node._on_safety_state(reset)
        sleep(0.01)
        ready = SafetyState()
        ready.stamp = probe.get_clock().now().to_msg()
        ready.state = SafetyState.READY
        node._on_safety_state(ready)

        # Guard may ACK a duplicate raw STOP. The first causal ACK is the
        # recovery boundary; replay must not invalidate already observed states.
        node._on_stop_barrier_ack(acknowledgement)
        assert node._stop_ack_observation == first_ack_observation

        sleep(0.01)
        manual = ControlState()
        manual.stamp = probe.get_clock().now().to_msg()
        manual.active_mode = ControlState.MODE_MANUAL
        manual.active_owner = ControlState.OWNER_WEB
        manual.owner_alive = True
        node._on_control_state(manual)

        assert node._stop_latched is False
        assert node._completed_stop_generation == 9


def test_replayed_authoritative_states_cannot_reopen_manual_admission():
    with running_manual_executor() as (node, probe):
        control_publisher, safety_publisher, heartbeat = publish_manual_ready(
            node,
            probe,
        )
        heartbeat.cancel()
        sleep(0.01)

        newer_stamp = probe.get_clock().now().to_msg()
        newer_ns = (
            int(newer_stamp.sec) * 1_000_000_000
            + int(newer_stamp.nanosec)
        )
        older_ns = newer_ns - 1_000_000

        hold = SafetyState()
        hold.stamp = newer_stamp
        hold.state = SafetyState.HOLD
        safety_publisher.publish(hold)
        assert wait_until(lambda: node._core._safety_state == SafetyState.HOLD)

        old_ready = SafetyState()
        old_ready.stamp.sec = older_ns // 1_000_000_000
        old_ready.stamp.nanosec = older_ns % 1_000_000_000
        old_ready.state = SafetyState.READY
        safety_publisher.publish(old_ready)
        sleep(0.05)
        assert node._core._safety_state == SafetyState.HOLD

        sleep(0.01)
        disabled = ControlState()
        disabled.stamp = probe.get_clock().now().to_msg()
        disabled_ns = (
            int(disabled.stamp.sec) * 1_000_000_000
            + int(disabled.stamp.nanosec)
        )
        disabled.active_mode = ControlState.MODE_DISABLED
        disabled.active_owner = ControlState.OWNER_NONE
        disabled.owner_alive = False
        control_publisher.publish(disabled)
        assert wait_until(
            lambda: node._core._control_mode == ControlState.MODE_DISABLED
        )

        old_manual = ControlState()
        old_manual_ns = disabled_ns - 1_000_000
        old_manual.stamp.sec = old_manual_ns // 1_000_000_000
        old_manual.stamp.nanosec = old_manual_ns % 1_000_000_000
        old_manual.active_mode = ControlState.MODE_MANUAL
        old_manual.active_owner = ControlState.OWNER_WEB
        old_manual.owner_alive = True
        control_publisher.publish(old_manual)
        sleep(0.05)
        assert node._core._control_mode == ControlState.MODE_DISABLED


def test_action_cancel_reaches_canceled_terminal_state_without_race():
    with running_manual_executor() as (node, probe):
        publish_manual_ready(node, probe)
        action_client = ActionClient(
            probe,
            ExecuteSequence,
            '/thing/execute_sequence',
        )
        assert wait_until(action_client.server_is_ready)
        goal = ExecuteSequence.Goal()
        goal.sequence_name = 'countdown'
        goal.speed_limit = 1.0
        goal_future = action_client.send_goal_async(goal)
        assert wait_until(goal_future.done)
        goal_handle = goal_future.result()
        assert goal_handle.accepted is True
        assert wait_until(lambda: node._core.is_sequence_running)

        cancel_future = goal_handle.cancel_goal_async()
        assert wait_until(cancel_future.done)
        assert cancel_future.result().goals_canceling
        result_future = goal_handle.get_result_async()
        assert wait_until(result_future.done)
        wrapped = result_future.result()
        assert wrapped.status == GoalStatus.STATUS_CANCELED
        assert wrapped.result.reason == 'cancel_requested'


class FakeGoalHandle:
    def __init__(self, request, key_byte):
        self.request = request
        self.goal_id = type('GoalId', (), {'uuid': bytes([key_byte]) * 16})()
        self.is_cancel_requested = False
        self.execute_count = 0
        self.canceled_count = 0
        self.aborted_count = 0
        self.execute_callback = None
        self.execute_executor = None
        self.execute_error = None
        self.execute_thread = None
        self.execute_result = None

    def execute(self):
        self.execute_count += 1
        if self.execute_error is not None:
            raise self.execute_error
        if self.execute_callback is not None:
            if self.execute_executor is not None:
                self.execute_executor.create_task(
                    self.execute_callback,
                    self,
                )
                return

            def run_execute_callback():
                self.execute_result = self.execute_callback(self)

            self.execute_thread = Thread(target=run_execute_callback)
            self.execute_thread.start()

    def publish_feedback(self, _feedback):
        pass

    def canceled(self):
        self.canceled_count += 1

    def abort(self):
        self.aborted_count += 1

    def succeed(self):
        raise AssertionError('cancel test must not succeed')


def test_action_and_gesture_share_one_mutually_exclusive_admission_lane():
    with running_manual_executor() as (node, probe):
        publish_manual_ready(node, probe)
        goal = ExecuteSequence.Goal()
        goal.sequence_name = 'countdown'
        goal.speed_limit = 1.0

        assert isinstance(
            node._admission_callback_group,
            MutuallyExclusiveCallbackGroup,
        )
        assert node._gesture_service.callback_group is (
            node._action_server.callback_group
        )
        assert node._on_sequence_goal(goal) == GoalResponse.ACCEPT
        handle = FakeGoalHandle(goal, 1)
        node._on_sequence_accepted(handle)
        assert handle.execute_count == 1
        assert node._core.is_sequence_running is True

        gesture_request = ExecuteGesture.Request()
        gesture_request.gesture_name = 'open'
        gesture_request.speed_limit = 1.0
        blocked = node._on_execute_gesture(
            gesture_request,
            ExecuteGesture.Response(),
        )
        assert blocked.accepted is False
        assert blocked.reason == 'motion_active'

        assert node._on_sequence_cancel(handle) == CancelResponse.ACCEPT
        handle.is_cancel_requested = True
        node._on_publish_timer()
        result = node._execute_sequence(handle)
        assert result.success is False
        assert result.reason == 'cancel_requested'
        assert handle.canceled_count == 1


def test_accepted_cancel_suppresses_timer_drive_before_canceling_state_visible():
    with running_manual_executor() as (node, probe):
        publish_manual_ready(node, probe)
        goal = ExecuteSequence.Goal()
        goal.sequence_name = 'countdown'
        goal.speed_limit = 1.0
        assert node._on_sequence_goal(goal) == GoalResponse.ACCEPT
        handle = FakeGoalHandle(goal, 2)
        node._on_sequence_accepted(handle)
        assert node._core.is_sequence_running is True

        assert node._on_sequence_cancel(handle) == CancelResponse.ACCEPT
        sequence_before_gap = node._wire_sequence
        node._on_publish_timer()
        assert node._wire_sequence == sequence_before_gap
        assert node._core.is_sequence_running is True

        handle.is_cancel_requested = True
        node._on_publish_timer()
        result = node._execute_sequence(handle)
        assert result.reason == 'cancel_requested'
        assert handle.canceled_count == 1


def test_execute_scheduling_failure_cancels_core_before_aborting_goal():
    with running_manual_executor() as (node, probe):
        publish_manual_ready(node, probe)
        goal = ExecuteSequence.Goal()
        goal.sequence_name = 'countdown'
        goal.speed_limit = 1.0
        assert node._on_sequence_goal(goal) == GoalResponse.ACCEPT
        handle = FakeGoalHandle(goal, 4)
        handle.execute_error = RuntimeError('injected scheduling failure')

        node._on_sequence_accepted(handle)
        sequence_after_failure = node._wire_sequence
        node._on_publish_timer()
        assert node._wire_sequence == sequence_after_failure
        assert node._core.is_sequence_running is False
        assert handle.aborted_count == 1
        assert node._goal_callbacks_outstanding == set()


def test_shutdown_preserves_accepted_cancel_until_canceling_is_visible():
    with running_manual_executor() as (node, probe):
        publish_manual_ready(node, probe)
        goal = ExecuteSequence.Goal()
        goal.sequence_name = 'countdown'
        goal.speed_limit = 1.0
        assert node._on_sequence_goal(goal) == GoalResponse.ACCEPT
        handle = FakeGoalHandle(goal, 5)
        node._on_sequence_accepted(handle)
        assert node._on_sequence_cancel(handle) == CancelResponse.ACCEPT

        node.request_shutdown()
        assert node._core.is_sequence_running is True
        handle.is_cancel_requested = True
        node._on_publish_timer()
        result = node._execute_sequence(handle)
        assert result.reason == 'cancel_requested'
        assert handle.canceled_count == 1
        assert handle.aborted_count == 0


def test_shutdown_drain_preserves_accepted_prehandle_cancel():
    rclpy.init()
    node = ManualExecutorNode()
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    try:
        goal = ExecuteSequence.Goal()
        goal.sequence_name = 'countdown'
        goal.speed_limit = 1.0
        with node._condition:
            node._goal_reservations[id(goal)] = node._now_ns()
        handle = FakeGoalHandle(goal, 6)
        handle.execute_callback = node._execute_sequence
        handle.execute_executor = executor
        assert node._on_sequence_cancel(handle) == CancelResponse.ACCEPT
        assert handle.is_cancel_requested is False

        node.request_shutdown()
        node._on_sequence_accepted(handle)
        assert node._wire_sequence == 0
        assert handle.aborted_count == 0
        # Models Humble applying EXECUTING -> CANCELING only after the user
        # cancel callback has returned ACCEPT.
        handle.is_cancel_requested = True
        assert _drain_shutdown(node, executor, timeout_sec=1.0) is True
        assert handle.canceled_count == 1
        assert handle.aborted_count == 0
        assert node._goal_reservations == {}
        assert node._goal_callbacks_outstanding == set()
    finally:
        executor.shutdown(timeout_sec=1.0)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def test_shutdown_drain_keeps_real_executor_scheduling_prehandle_goal():
    rclpy.init()
    node = ManualExecutorNode()
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    try:
        goal = ExecuteSequence.Goal()
        goal.sequence_name = 'countdown'
        goal.speed_limit = 1.0
        with node._condition:
            node._goal_reservations[id(goal)] = node._now_ns()
        node.request_shutdown()

        handle = FakeGoalHandle(goal, 3)
        handle.execute_callback = node._execute_sequence
        handle.execute_executor = executor
        node._on_sequence_accepted(handle)
        assert node.shutdown_complete() is False
        assert handle.aborted_count == 0
        assert _drain_shutdown(node, executor, timeout_sec=1.0) is True
        assert handle.aborted_count == 1
        assert node._goal_reservations == {}
        assert node._goal_callbacks_outstanding == set()
    finally:
        executor.shutdown(timeout_sec=1.0)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def test_shutdown_aborts_active_action_before_executor_teardown():
    with running_manual_executor() as (node, probe):
        publish_manual_ready(node, probe)
        action_client = ActionClient(
            probe,
            ExecuteSequence,
            '/thing/execute_sequence',
        )
        assert wait_until(action_client.server_is_ready)
        goal = ExecuteSequence.Goal()
        goal.sequence_name = 'countdown'
        goal.speed_limit = 1.0
        goal_future = action_client.send_goal_async(goal)
        assert wait_until(goal_future.done)
        goal_handle = goal_future.result()
        assert goal_handle.accepted is True
        assert wait_until(lambda: node._core.is_sequence_running)

        result_future = goal_handle.get_result_async()
        node.request_shutdown()
        assert wait_until(result_future.done)
        wrapped = result_future.result()
        assert wrapped.status == GoalStatus.STATUS_ABORTED
        assert wrapped.result.success is False
        assert wrapped.result.reason == 'shutdown'
