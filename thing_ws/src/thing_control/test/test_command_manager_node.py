"""ROS integration tests for the command manager node."""

from contextlib import contextmanager
from threading import Event, Thread
from time import monotonic, sleep, time_ns

import pytest
import rclpy
from rclpy.clock import ClockType
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from rclpy.serialization import deserialize_message, serialize_message
from std_msgs.msg import Bool, UInt64

import thing_control.command_manager as command_manager_module
from thing_control.command_manager import CommandManagerNode
from thing_interfaces.msg import (
    ControlState,
    HandCommand,
    SafetyState,
)
from thing_interfaces.srv import SetControlMode


COMMAND_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
)
STATE_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)


def wait_until(predicate, timeout=3.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(0.01)
    return False


@contextmanager
def running_nodes(parameter_overrides=None):
    rclpy.init()
    manager = CommandManagerNode(
        parameter_overrides=parameter_overrides or [],
    )
    probe = Node('command_manager_test_probe')
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(manager)
    executor.add_node(probe)
    thread = Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        yield manager, probe
    finally:
        executor.shutdown(timeout_sec=1.0)
        thread.join(timeout=1.0)
        probe.destroy_node()
        manager.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def call_mode(client, mode, owner):
    request = SetControlMode.Request()
    request.requested_mode = mode
    request.requested_owner = owner
    future = client.call_async(request)
    assert wait_until(future.done)
    return future.result()


def test_main_uses_two_executor_threads_and_treats_shutdown_as_clean(monkeypatch):
    events = []

    class FakeNode:
        def destroy_node(self):
            events.append('destroy_node')

    class FakeExecutor:
        def __init__(self, num_threads):
            events.append(('executor', num_threads))

        def add_node(self, _node):
            events.append('add_node')

        def spin(self):
            raise ExternalShutdownException()

        def shutdown(self, timeout_sec=None):
            events.append(('shutdown', timeout_sec))

    monkeypatch.setattr(
        command_manager_module.rclpy,
        'init',
        lambda args=None: events.append(('init', args)),
    )
    monkeypatch.setattr(
        command_manager_module,
        'CommandManagerNode',
        FakeNode,
    )
    monkeypatch.setattr(
        command_manager_module,
        'MultiThreadedExecutor',
        FakeExecutor,
        raising=False,
    )
    monkeypatch.setattr(command_manager_module.rclpy, 'ok', lambda: False)

    command_manager_module.main()

    assert events == [
        ('init', None),
        ('executor', 2),
        'add_node',
        ('shutdown', 1.0),
        'destroy_node',
    ]


def test_stop_service_response_waits_until_guard_barrier_ack():
    with running_nodes() as (_, probe):
        stop_observed = Event()
        allow_ack = Event()
        ack_publisher = probe.create_publisher(
            UInt64,
            '/thing/control/stop_barrier_ack',
            10,
        )

        def acknowledge_after_test_allows(message):
            stop_observed.set()
            assert allow_ack.wait(timeout=1.0)
            acknowledgement = UInt64()
            acknowledgement.data = message.data
            ack_publisher.publish(acknowledgement)

        stop_subscription = probe.create_subscription(
            UInt64,
            '/thing/control/stop_requested',
            acknowledge_after_test_allows,
            10,
        )
        safety_pub = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        client = probe.create_client(
            SetControlMode,
            '/thing/set_control_mode',
        )

        assert wait_until(client.service_is_ready)
        assert wait_until(lambda: safety_pub.get_subscription_count() == 1)
        assert wait_until(lambda: ack_publisher.get_subscription_count() == 1)
        publish_safety_ready(safety_pub)

        request = SetControlMode.Request()
        request.requested_mode = ControlState.MODE_DISABLED
        request.requested_owner = ControlState.OWNER_NONE
        future = client.call_async(request)

        assert stop_observed.wait(timeout=1.0)
        sleep(0.05)
        assert future.done() is False

        allow_ack.set()
        assert wait_until(future.done)
        assert future.result().accepted is True

        probe.destroy_subscription(stop_subscription)


def test_mode_service_publishes_expiry_and_requires_retry():
    with running_nodes() as (manager, _):
        publications = []
        manager._core.check_lease = lambda: True
        manager._core.request_mode = lambda *_: pytest.fail(
            'expired request must not reacquire in the same callback'
        )
        manager._publish_control_state = lambda: publications.append(True)

        request = SetControlMode.Request()
        request.requested_mode = ControlState.MODE_MIMIC
        request.requested_owner = ControlState.OWNER_WEB
        response = manager._on_set_control_mode(
            request,
            SetControlMode.Response(),
        )

        assert response.accepted is False
        assert response.active_mode == ControlState.MODE_DISABLED
        assert response.active_owner == ControlState.OWNER_NONE
        assert response.reason == 'owner_lease_expired'
        assert publications == [True]


def test_manager_adapter_ignores_malformed_and_replayed_safety_stamps():
    with running_nodes() as (manager, _):
        malformed = SafetyState()
        malformed.state = SafetyState.READY
        manager._on_safety_state(malformed)
        assert manager._core._safety_state != SafetyState.READY

        valid = SafetyState()
        valid.state = SafetyState.READY
        valid.stamp.sec = 10
        manager._on_safety_state(valid)
        assert manager._core._safety_state == SafetyState.READY

        stale = SafetyState()
        stale.state = SafetyState.FAULT
        stale.stamp.sec = 9
        manager._on_safety_state(stale)

        same_stamp_change = SafetyState()
        same_stamp_change.state = SafetyState.RESET
        same_stamp_change.stamp.sec = 10
        manager._on_safety_state(same_stamp_change)
        assert manager._core._safety_state == SafetyState.READY


def make_stamped_safety_state(state):
    message = SafetyState()
    message.state = state
    previous_ns = getattr(make_stamped_safety_state, '_last_ns', 0)
    stamp_ns = max(time_ns(), previous_ns + 1)
    make_stamped_safety_state._last_ns = stamp_ns
    message.stamp.sec, message.stamp.nanosec = divmod(
        stamp_ns,
        1_000_000_000,
    )
    return message


def publish_safety_ready(publisher):
    publisher.publish(make_stamped_safety_state(SafetyState.READY))


def test_stop_timeout_stays_disabled_and_blocks_reacquisition_until_late_ack():
    overrides = [Parameter('stop_barrier_timeout_ms', value=10)]
    with running_nodes(overrides) as (manager, probe):
        safety_publisher = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        client = probe.create_client(SetControlMode, '/thing/set_control_mode')
        assert client.wait_for_service(timeout_sec=1.0)
        publish_safety_ready(safety_publisher)
        assert wait_until(
            lambda: manager._core._safety_state == SafetyState.READY
        )

        acquired = call_mode(
            client,
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert acquired.accepted is True

        stopped = call_mode(
            client,
            ControlState.MODE_DISABLED,
            ControlState.OWNER_NONE,
        )
        assert stopped.accepted is False
        assert stopped.reason == 'stop_barrier_timeout'
        assert stopped.active_mode == ControlState.MODE_DISABLED
        assert stopped.active_owner == ControlState.OWNER_NONE

        blocked = call_mode(
            client,
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert blocked.accepted is False
        assert blocked.reason == 'stop_barrier_pending'

        # A genuinely late Guard ACK closes only the Guard barrier. Cached READY and a
        # fixed delay are not proof that Safety observed RESET and completed a new cycle.
        late_ack = UInt64()
        late_ack.data = manager._pending_stop_generation
        manager._on_stop_barrier_ack(late_ack)
        sleep(0.51)
        stale_ready_reacquire = call_mode(
            client,
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert stale_ready_reacquire.accepted is False
        assert stale_ready_reacquire.reason == 'stop_in_progress'

        safety_publisher.publish(make_stamped_safety_state(SafetyState.RESET))
        sleep(0.05)
        publish_safety_ready(safety_publisher)
        sleep(0.05)

        reacquired = call_mode(
            client,
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert reacquired.accepted is True

        probe.destroy_publisher(safety_publisher)


def test_manager_rejects_unbounded_timer_parameters():
    for name, value, maximum in (
        ('lease_check_period_ms', 101, 100),
        ('state_publish_period_ms', 1001, 1000),
        ('stop_barrier_timeout_ms', 501, 500),
    ):
        with pytest.raises(ValueError):
            CommandManagerNode._validate_bounded_parameter(
                name,
                value,
                maximum,
            )


def test_manager_timers_use_steady_clock():
    with running_nodes() as (manager, _):
        assert manager._lease_timer.clock.clock_type == ClockType.STEADY_TIME
        assert manager._state_timer.clock.clock_type == ClockType.STEADY_TIME


def test_manager_uses_required_command_and_state_qos_profiles():
    with running_nodes() as (manager, _):
        command_endpoints = [
            manager._selected_publisher,
            *manager._command_subscriptions,
        ]
        for endpoint in command_endpoints:
            qos = endpoint.qos_profile
            assert qos.history == HistoryPolicy.KEEP_LAST
            assert qos.depth == 1
            assert qos.reliability == ReliabilityPolicy.RELIABLE
            assert qos.durability == DurabilityPolicy.VOLATILE

        state_endpoints = [
            manager._control_state_publisher,
            manager._safety_subscription,
            manager._recording_subscription,
        ]
        for endpoint in state_endpoints:
            qos = endpoint.qos_profile
            assert qos.history == HistoryPolicy.KEEP_LAST
            assert qos.depth == 1
            assert qos.reliability == ReliabilityPolicy.RELIABLE
            assert qos.durability == DurabilityPolicy.TRANSIENT_LOCAL


def test_node_routes_only_active_source_and_stop_preempts_commands():
    with running_nodes() as (_, probe):
        selected = []
        stop_events = []
        control_states = []
        ack_publisher = probe.create_publisher(
            UInt64,
            '/thing/control/stop_barrier_ack',
            10,
        )

        def record_stop_and_ack(message):
            stop_events.append(message)
            acknowledgement = UInt64()
            acknowledgement.data = message.data
            ack_publisher.publish(acknowledgement)

        selected_sub = probe.create_subscription(
            HandCommand,
            '/thing/command/selected',
            selected.append,
            COMMAND_QOS,
        )
        stop_sub = probe.create_subscription(
            UInt64,
            '/thing/control/stop_requested',
            record_stop_and_ack,
            10,
        )
        control_sub = probe.create_subscription(
            ControlState,
            '/thing/control_state',
            control_states.append,
            STATE_QOS,
        )
        mimic_pub = probe.create_publisher(
            HandCommand,
            '/thing/command/mimic',
            COMMAND_QOS,
        )
        manual_pub = probe.create_publisher(
            HandCommand,
            '/thing/command/manual',
            COMMAND_QOS,
        )
        teleop_pub = probe.create_publisher(
            HandCommand,
            '/thing/command/teleop',
            COMMAND_QOS,
        )
        safety_pub = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        client = probe.create_client(
            SetControlMode,
            '/thing/set_control_mode',
        )

        assert wait_until(client.service_is_ready)
        assert wait_until(
            lambda: probe.count_publishers(
                '/thing/control/stop_requested'
            ) == 1
        )
        assert wait_until(lambda: ack_publisher.get_subscription_count() == 1)
        assert wait_until(lambda: safety_pub.get_subscription_count() == 1)
        assert wait_until(lambda: mimic_pub.get_subscription_count() == 1)
        assert wait_until(lambda: manual_pub.get_subscription_count() == 1)
        assert wait_until(lambda: teleop_pub.get_subscription_count() == 1)
        publish_safety_ready(safety_pub)
        assert wait_until(lambda: len(control_states) > 0)

        response = call_mode(
            client,
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert response.accepted is True

        wrong = HandCommand()
        wrong.sequence = 10
        wrong.source = HandCommand.SOURCE_TELEOP
        teleop_pub.publish(wrong)
        wrong_manual = HandCommand()
        wrong_manual.sequence = 10
        wrong_manual.source = HandCommand.SOURCE_GESTURE
        manual_pub.publish(wrong_manual)
        sleep(0.1)
        assert selected == []

        valid = HandCommand()
        valid.stamp = probe.get_clock().now().to_msg()
        valid.sequence = 11
        valid.source = HandCommand.SOURCE_MIMIC
        valid.thumb_flex = 0.11
        valid.thumb_opp = 0.22
        valid.thumb_abd = 0.33
        valid.index_flex = 0.44
        valid.middle_flex = 0.55
        valid.ring_flex = 0.66
        valid.little_flex = 0.77
        valid.speed_limit = 0.73
        valid.confidence = 0.99
        expected = deserialize_message(
            serialize_message(valid),
            HandCommand,
        )
        mimic_pub.publish(valid)
        assert wait_until(lambda: len(selected) == 1)
        # Compare normalized fields, not CDR padding bytes or Python float64
        # literals against the DDS float32 round trip.
        assert selected[0] == expected

        stop = call_mode(
            client,
            ControlState.MODE_DISABLED,
            ControlState.OWNER_NONE,
        )
        assert stop.accepted is True
        assert wait_until(lambda: len(stop_events) == 1)
        assert wait_until(
            lambda: control_states
            and control_states[-1].active_mode
            == ControlState.MODE_DISABLED
            and control_states[-1].active_owner
            == ControlState.OWNER_NONE
        )

        after_stop = HandCommand()
        after_stop.sequence = 12
        after_stop.source = HandCommand.SOURCE_MIMIC
        mimic_pub.publish(after_stop)
        sleep(0.1)
        assert [message.sequence for message in selected] == [11]

        probe.destroy_subscription(selected_sub)
        probe.destroy_subscription(stop_sub)
        probe.destroy_subscription(control_sub)


def test_node_publishes_disabled_state_when_owner_lease_expires():
    overrides = [
        Parameter('owner_lease_timeout_ms', value=80),
        Parameter('lease_check_period_ms', value=10),
        Parameter('state_publish_period_ms', value=20),
    ]
    with running_nodes(overrides) as (_, probe):
        control_states = []
        control_sub = probe.create_subscription(
            ControlState,
            '/thing/control_state',
            control_states.append,
            STATE_QOS,
        )
        safety_pub = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        client = probe.create_client(
            SetControlMode,
            '/thing/set_control_mode',
        )

        assert wait_until(client.service_is_ready)
        assert wait_until(lambda: safety_pub.get_subscription_count() == 1)
        publish_safety_ready(safety_pub)
        assert wait_until(lambda: len(control_states) > 0)

        response = call_mode(
            client,
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert response.accepted is True
        assert wait_until(
            lambda: any(
                state.active_mode == ControlState.MODE_MIMIC
                for state in control_states
            )
        )
        assert wait_until(
            lambda: control_states
            and control_states[-1].active_mode
            == ControlState.MODE_DISABLED
            and control_states[-1].active_owner
            == ControlState.OWNER_NONE
            and not control_states[-1].owner_alive
            and control_states[-1].last_transition_reason
            == 'owner_lease_expired'
        )

        probe.destroy_subscription(control_sub)


def test_command_callback_publishes_lease_expiry_before_rejecting_source():
    with running_nodes() as (manager, _):
        now_ns = [0]
        manager._core = command_manager_module.CommandManagerCore(
            owner_lease_timeout_ms=3000,
            monotonic_ns=lambda: now_ns[0],
        )
        manager._core.update_safety_state(SafetyState.READY, 1)
        acquired = manager._core.request_mode(
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert acquired.accepted is True

        control_states = []

        class RecordingPublisher:
            def publish(self, message):
                control_states.append(message)

        manager._control_state_publisher = RecordingPublisher()
        manager._selected_publisher = RecordingPublisher()
        now_ns[0] = 3_000_000_000
        command = HandCommand()
        command.source = HandCommand.SOURCE_MIMIC

        manager._on_command(command, (HandCommand.SOURCE_MIMIC,))

        assert len(control_states) == 1
        assert control_states[0].active_mode == ControlState.MODE_DISABLED
        assert control_states[0].active_owner == ControlState.OWNER_NONE
        assert control_states[0].last_transition_reason == 'owner_lease_expired'


def test_command_callback_publishes_expiry_when_deadline_crosses_between_checks():
    with running_nodes() as (manager, _):
        now_ns = [0]
        manager._core = command_manager_module.CommandManagerCore(
            owner_lease_timeout_ms=3000,
            monotonic_ns=lambda: now_ns[0],
        )
        manager._core.update_safety_state(SafetyState.READY, 1)
        acquired = manager._core.request_mode(
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert acquired.accepted is True

        control_states = []

        class RecordingPublisher:
            def publish(self, message):
                control_states.append(message)

        manager._control_state_publisher = RecordingPublisher()
        manager._selected_publisher = RecordingPublisher()
        now_ns[0] = 2_999_999_999
        original_check_lease = manager._core.check_lease

        def cross_deadline_after_check():
            changed = original_check_lease()
            now_ns[0] = 3_000_000_000
            return changed

        manager._core.check_lease = cross_deadline_after_check
        command = HandCommand()
        command.source = HandCommand.SOURCE_MIMIC

        manager._on_command(command, (HandCommand.SOURCE_MIMIC,))

        assert len(control_states) == 1
        assert control_states[0].active_mode == ControlState.MODE_DISABLED
        assert control_states[0].active_owner == ControlState.OWNER_NONE
        assert control_states[0].last_transition_reason == 'owner_lease_expired'


def test_stop_service_accepts_hold_after_guard_barrier_ack():
    with running_nodes() as (manager, _):
        manager._core.update_safety_state(SafetyState.READY, 1)
        acquired = manager._core.request_mode(
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert acquired.accepted is True
        manager._core.update_safety_state(SafetyState.HOLD, 2)
        stop_events = []

        class AckingPublisher:
            def publish(self, message):
                stop_events.append(message)
                acknowledgement = UInt64()
                acknowledgement.data = message.data
                manager._on_stop_barrier_ack(acknowledgement)

        manager._stop_event_publisher = AckingPublisher()
        request = SetControlMode.Request()
        request.requested_mode = ControlState.MODE_DISABLED
        request.requested_owner = ControlState.OWNER_NONE

        response = manager._on_set_control_mode(
            request,
            SetControlMode.Response(),
        )

        assert response.accepted is True
        assert response.reason == 'accepted'
        assert response.active_mode == ControlState.MODE_DISABLED
        assert response.active_owner == ControlState.OWNER_NONE
        assert len(stop_events) == 1


def test_stop_cannot_complete_between_authorization_and_publish():
    with running_nodes() as (manager, _):
        manager._core.update_safety_state(SafetyState.READY, 1)
        authorization_checked = Event()
        allow_publish = Event()
        stop_completed = Event()
        events = []

        def blocking_accepts_source(_source):
            authorization_checked.set()
            assert allow_publish.wait(timeout=1.0)
            return True

        class RecordingPublisher:
            def publish(self, _message):
                events.append('published')

        manager._core.accepts_source = blocking_accepts_source
        manager._selected_publisher = RecordingPublisher()

        command = HandCommand()
        command.source = HandCommand.SOURCE_MIMIC
        command_thread = Thread(
            target=manager._on_command,
            args=(command, (HandCommand.SOURCE_MIMIC,)),
        )
        command_thread.start()
        assert authorization_checked.wait(timeout=1.0)

        request = SetControlMode.Request()
        request.requested_mode = ControlState.MODE_DISABLED
        request.requested_owner = ControlState.OWNER_NONE
        response = SetControlMode.Response()

        def stop_control():
            manager._on_set_control_mode(request, response)
            events.append('stop_completed')
            stop_completed.set()

        stop_thread = Thread(target=stop_control)
        stop_thread.start()
        completed_before_publish = stop_completed.wait(timeout=0.05)
        allow_publish.set()
        command_thread.join(timeout=1.0)
        assert wait_until(lambda: manager._stop_barrier_pending)
        acknowledgement = UInt64()
        acknowledgement.data = manager._pending_stop_generation
        manager._on_stop_barrier_ack(acknowledgement)
        stop_thread.join(timeout=1.0)

        assert completed_before_publish is False
        assert events == ['published', 'stop_completed']


def test_motion_activity_topic_rejects_valid_mode_change():
    with running_nodes() as (manager, probe):
        safety_pub = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        motion_pub = probe.create_publisher(
            Bool,
            '/thing/control/motion_active',
            10,
        )
        client = probe.create_client(
            SetControlMode,
            '/thing/set_control_mode',
        )

        assert wait_until(client.service_is_ready)
        assert wait_until(lambda: safety_pub.get_subscription_count() == 1)
        assert wait_until(lambda: motion_pub.get_subscription_count() == 1)
        publish_safety_ready(safety_pub)

        acquired = call_mode(
            client,
            ControlState.MODE_MANUAL,
            ControlState.OWNER_WEB,
        )
        assert acquired.accepted is True

        active = Bool()
        active.data = True
        motion_pub.publish(active)
        assert wait_until(
            lambda: manager._core.snapshot().sequence_running
        )

        changed = call_mode(
            client,
            ControlState.MODE_TELEOP,
            ControlState.OWNER_LOCAL,
        )
        assert changed.accepted is False
        assert changed.reason == 'motion_active'
