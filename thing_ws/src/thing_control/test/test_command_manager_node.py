"""ROS integration tests for the command manager node."""

from contextlib import contextmanager
from threading import Event, Thread
from time import monotonic, sleep

import rclpy
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from std_msgs.msg import Bool, Empty

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


def test_main_treats_external_shutdown_as_clean_exit(monkeypatch):
    events = []

    class FakeNode:
        def destroy_node(self):
            events.append('destroy_node')

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

    def raise_external_shutdown(_node):
        raise ExternalShutdownException()

    monkeypatch.setattr(
        command_manager_module.rclpy,
        'spin',
        raise_external_shutdown,
    )
    monkeypatch.setattr(command_manager_module.rclpy, 'ok', lambda: False)

    command_manager_module.main()

    assert events == [('init', None), 'destroy_node']


def publish_safety_ready(publisher):
    message = SafetyState()
    message.state = SafetyState.READY
    publisher.publish(message)


def test_node_routes_only_active_source_and_stop_preempts_commands():
    with running_nodes() as (_, probe):
        selected = []
        stop_events = []
        control_states = []
        selected_sub = probe.create_subscription(
            HandCommand,
            '/thing/command/selected',
            selected.append,
            COMMAND_QOS,
        )
        stop_sub = probe.create_subscription(
            Empty,
            '/thing/control/stop_requested',
            stop_events.append,
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
        assert wait_until(lambda: safety_pub.get_subscription_count() == 1)
        assert wait_until(lambda: mimic_pub.get_subscription_count() == 1)
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
        sleep(0.1)
        assert selected == []

        valid = HandCommand()
        valid.sequence = 11
        valid.source = HandCommand.SOURCE_MIMIC
        mimic_pub.publish(valid)
        assert wait_until(lambda: len(selected) == 1)
        assert selected[0].sequence == 11

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


def test_stop_cannot_complete_between_authorization_and_publish():
    with running_nodes() as (manager, _):
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
