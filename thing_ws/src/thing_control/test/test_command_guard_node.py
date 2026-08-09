"""ROS 2 integration tests for the command guard node."""

from contextlib import contextmanager
from threading import Event, Thread
from time import monotonic, sleep

import rclpy
from diagnostic_msgs.msg import DiagnosticArray
from rclpy.clock import ClockType
from rclpy.duration import Duration
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from std_msgs.msg import Bool

import thing_control.command_guard as command_guard_module
from thing_control.command_guard import CommandGuardNode
from thing_control.command_guard_core import AXIS_NAMES
from thing_control.command_guard_core import GuardDecision
from thing_interfaces.msg import ControlState, HandCommand, SafetyState


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


def guard_overrides():
    overrides = [
        Parameter('safety_state_timeout_ms', value=1500),
        Parameter('control_state_timeout_ms', value=1500),
    ]
    for axis_name in (
        'thumb_flex',
        'thumb_opp',
        'thumb_abd',
        'index_flex',
        'middle_flex',
        'ring_flex',
        'little_flex',
    ):
        overrides.append(
            Parameter(
                f'axis_limits.{axis_name}.max_delta_per_second',
                value=1000.0,
            )
        )
    return overrides


@contextmanager
def running_nodes(parameter_overrides=None):
    rclpy.init()
    guard = CommandGuardNode(
        parameter_overrides=parameter_overrides or guard_overrides(),
    )
    probe = Node('command_guard_test_probe')
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(guard)
    executor.add_node(probe)
    thread = Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        yield guard, probe
    finally:
        executor.shutdown(timeout_sec=1.0)
        thread.join(timeout=1.0)
        probe.destroy_node()
        guard.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def make_command(node, sequence=1, value=0.5):
    message = HandCommand()
    message.stamp = node.get_clock().now().to_msg()
    message.sequence = sequence
    message.source = HandCommand.SOURCE_MIMIC
    message.thumb_flex = value
    message.thumb_opp = value
    message.thumb_abd = value
    message.index_flex = value
    message.middle_flex = value
    message.ring_flex = value
    message.little_flex = value
    message.speed_limit = 0.5
    message.confidence = 0.9
    return message


def publish_ready_activation(safety_pub, control_pub):
    safety = SafetyState()
    safety.state = SafetyState.READY
    safety.stamp.sec = 1
    safety_pub.publish(safety)

    disabled = ControlState()
    disabled.active_mode = ControlState.MODE_DISABLED
    disabled.active_owner = ControlState.OWNER_NONE
    disabled.owner_alive = False
    control_pub.publish(disabled)
    sleep(0.05)

    active = ControlState()
    active.active_mode = ControlState.MODE_MIMIC
    active.active_owner = ControlState.OWNER_WEB
    active.owner_alive = True
    control_pub.publish(active)


def test_node_defaults_match_safety_watchdog_and_mimic_producer():
    with running_nodes() as (guard, _):
        limits = guard._core._limits
        assert limits.command_hold_ms == 5000
        assert set(limits.mimic_max_axis_delta_per_second) == set(AXIS_NAMES)
        assert all(
            rate == 10.0
            for rate in limits.mimic_max_axis_delta_per_second.values()
        )


def test_guard_subscribes_to_stop_and_publishes_barrier_ack():
    with running_nodes() as (guard, _):
        subscription_topics = {
            subscription.topic_name for subscription in guard.subscriptions
        }
        publisher_topics = {
            publisher.topic_name for publisher in guard.publishers
        }
        assert '/thing/control/stop_requested' in subscription_topics
        assert '/thing/control/stop_barrier_ack' in publisher_topics
        assert '/thing/command/validation_result' in publisher_topics
        assert '/thing/command/validated_activity' not in publisher_topics
        assert '/thing/command/validation_failed' not in publisher_topics


def test_zero_stamp_safety_state_cannot_reopen_hold_barrier():
    with running_nodes() as (guard, _):
        hold = SafetyState()
        hold.state = SafetyState.HOLD
        hold.stamp = guard._system_clock.now().to_msg()
        guard._on_safety_state(hold)
        assert guard._core._safety_state == SafetyState.HOLD

        malformed_run = SafetyState()
        malformed_run.state = SafetyState.RUN
        malformed_run.stamp.sec = 0
        malformed_run.stamp.nanosec = 0
        guard._on_safety_state(malformed_run)

        assert guard._core._safety_state == SafetyState.HOLD


def test_guard_uses_required_command_and_state_qos_profiles():
    with running_nodes() as (guard, _):
        for endpoint in (
            guard._selected_subscription,
            guard._command_publisher,
        ):
            qos = endpoint.qos_profile
            assert qos.history == HistoryPolicy.KEEP_LAST
            assert qos.depth == 1
            assert qos.reliability == ReliabilityPolicy.RELIABLE
            assert qos.durability == DurabilityPolicy.VOLATILE

        for endpoint in (
            guard._safety_subscription,
            guard._control_subscription,
        ):
            qos = endpoint.qos_profile
            assert qos.history == HistoryPolicy.KEEP_LAST
            assert qos.depth == 1
            assert qos.reliability == ReliabilityPolicy.RELIABLE
            assert qos.durability == DurabilityPolicy.TRANSIENT_LOCAL


def test_node_uses_system_time_even_if_ros_sim_time_is_enabled():
    overrides = guard_overrides()
    overrides.append(Parameter('use_sim_time', value=True))
    with running_nodes(overrides) as (guard, probe):
        assert guard._system_clock.clock_type == ClockType.SYSTEM_TIME
        assert guard.get_clock().clock_type == ClockType.ROS_TIME

        commands = []
        command_sub = probe.create_subscription(
            HandCommand,
            '/thing/command',
            commands.append,
            COMMAND_QOS,
        )
        selected_pub = probe.create_publisher(
            HandCommand,
            '/thing/command/selected',
            COMMAND_QOS,
        )
        safety_pub = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        control_pub = probe.create_publisher(
            ControlState,
            '/thing/control_state',
            STATE_QOS,
        )
        assert wait_until(lambda: selected_pub.get_subscription_count() == 1)
        publish_ready_activation(safety_pub, control_pub)
        sleep(0.1)

        selected_pub.publish(make_command(probe))
        assert wait_until(lambda: len(commands) == 1)
        probe.destroy_subscription(command_sub)


def test_node_publishes_periodic_diagnostic_heartbeat_after_acceptance():
    overrides = guard_overrides()
    overrides.append(Parameter('diagnostic_period_ms', value=50))
    with running_nodes(overrides) as (_, probe):
        diagnostics = []
        diagnostic_sub = probe.create_subscription(
            DiagnosticArray,
            '/thing/diagnostics',
            diagnostics.append,
            10,
        )
        selected_pub = probe.create_publisher(
            HandCommand,
            '/thing/command/selected',
            COMMAND_QOS,
        )
        safety_pub = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        control_pub = probe.create_publisher(
            ControlState,
            '/thing/control_state',
            STATE_QOS,
        )
        assert wait_until(lambda: selected_pub.get_subscription_count() == 1)
        publish_ready_activation(safety_pub, control_pub)
        sleep(0.1)
        selected_pub.publish(make_command(probe))

        assert wait_until(
            lambda: any(
                message.status
                and message.status[0].message == 'accepted'
                and any(
                    value.key == 'accepted' and value.value == 'true'
                    for value in message.status[0].values
                )
                for message in diagnostics
            )
        )
        probe.destroy_subscription(diagnostic_sub)


def test_diagnostic_heartbeat_uses_steady_clock_when_ros_time_is_paused():
    overrides = guard_overrides()
    overrides.extend([
        Parameter('use_sim_time', value=True),
        Parameter('diagnostic_period_ms', value=50),
    ])
    with running_nodes(overrides) as (guard, probe):
        assert guard._steady_clock.clock_type == ClockType.STEADY_TIME
        diagnostics = []
        diagnostic_sub = probe.create_subscription(
            DiagnosticArray,
            '/thing/diagnostics',
            diagnostics.append,
            10,
        )
        assert wait_until(
            lambda: any(
                message.status
                and message.status[0].message == 'startup'
                for message in diagnostics
            ),
            timeout=0.5,
        )
        probe.destroy_subscription(diagnostic_sub)


def test_ros_node_rejects_stale_and_future_system_timestamps():
    with running_nodes() as (_, probe):
        commands = []
        diagnostics = []
        command_sub = probe.create_subscription(
            HandCommand,
            '/thing/command',
            commands.append,
            COMMAND_QOS,
        )
        diagnostic_sub = probe.create_subscription(
            DiagnosticArray,
            '/thing/diagnostics',
            diagnostics.append,
            10,
        )
        selected_pub = probe.create_publisher(
            HandCommand,
            '/thing/command/selected',
            COMMAND_QOS,
        )
        safety_pub = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        control_pub = probe.create_publisher(
            ControlState,
            '/thing/control_state',
            STATE_QOS,
        )
        assert wait_until(lambda: selected_pub.get_subscription_count() == 1)
        publish_ready_activation(safety_pub, control_pub)
        sleep(0.1)

        stale = make_command(probe, sequence=1)
        stale.stamp = (
            probe.get_clock().now() - Duration(nanoseconds=301_000_000)
        ).to_msg()
        selected_pub.publish(stale)
        assert wait_until(
            lambda: any(
                message.status
                and message.status[0].message == 'command_stale'
                for message in diagnostics
            )
        )

        future = make_command(probe, sequence=2)
        future.stamp = (
            probe.get_clock().now() + Duration(nanoseconds=150_000_000)
        ).to_msg()
        selected_pub.publish(future)
        assert wait_until(
            lambda: any(
                message.status
                and message.status[0].message == 'command_from_future'
                for message in diagnostics
            )
        )

        non_canonical = make_command(probe, sequence=3)
        non_canonical.stamp.nanosec = 1_000_000_000
        selected_pub.publish(non_canonical)
        assert wait_until(
            lambda: any(
                message.status
                and message.status[0].message == 'command_stamp_non_canonical'
                for message in diagnostics
            )
        )
        assert commands == []
        probe.destroy_subscription(command_sub)
        probe.destroy_subscription(diagnostic_sub)


def test_ros_node_rejects_selected_command_after_received_fault_state():
    with running_nodes() as (_, probe):
        commands = []
        diagnostics = []
        command_sub = probe.create_subscription(
            HandCommand,
            '/thing/command',
            commands.append,
            COMMAND_QOS,
        )
        diagnostic_sub = probe.create_subscription(
            DiagnosticArray,
            '/thing/diagnostics',
            diagnostics.append,
            10,
        )
        selected_pub = probe.create_publisher(
            HandCommand,
            '/thing/command/selected',
            COMMAND_QOS,
        )
        safety_pub = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        control_pub = probe.create_publisher(
            ControlState,
            '/thing/control_state',
            STATE_QOS,
        )
        assert wait_until(lambda: selected_pub.get_subscription_count() == 1)
        publish_ready_activation(safety_pub, control_pub)
        sleep(0.1)
        selected_pub.publish(make_command(probe, sequence=1))
        assert wait_until(lambda: len(commands) == 1)

        fault = SafetyState()
        fault.state = SafetyState.FAULT
        fault.stamp.sec = 2
        safety_pub.publish(fault)
        sleep(0.1)
        selected_pub.publish(make_command(probe, sequence=2))
        assert wait_until(
            lambda: any(
                message.status
                and message.status[0].message == 'safety_not_ready'
                for message in diagnostics
            )
        )
        assert len(commands) == 1
        probe.destroy_subscription(command_sub)
        probe.destroy_subscription(diagnostic_sub)


def test_ros_node_rejects_command_after_state_freshness_expires():
    overrides = [
        parameter
        for parameter in guard_overrides()
        if parameter.name not in {
            'safety_state_timeout_ms',
            'control_state_timeout_ms',
        }
    ]
    overrides.extend([
        Parameter('safety_state_timeout_ms', value=50),
        Parameter('control_state_timeout_ms', value=50),
    ])
    with running_nodes(overrides) as (_, probe):
        diagnostics = []
        diagnostic_sub = probe.create_subscription(
            DiagnosticArray,
            '/thing/diagnostics',
            diagnostics.append,
            10,
        )
        selected_pub = probe.create_publisher(
            HandCommand,
            '/thing/command/selected',
            COMMAND_QOS,
        )
        safety_pub = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        control_pub = probe.create_publisher(
            ControlState,
            '/thing/control_state',
            STATE_QOS,
        )
        assert wait_until(lambda: selected_pub.get_subscription_count() == 1)
        publish_ready_activation(safety_pub, control_pub)
        sleep(0.08)
        selected_pub.publish(make_command(probe))
        assert wait_until(
            lambda: any(
                message.status
                and message.status[0].message == 'safety_state_stale'
                for message in diagnostics
            )
        )
        probe.destroy_subscription(diagnostic_sub)


def test_ros_node_preserves_uint32_sequence_wrap():
    with running_nodes() as (_, probe):
        commands = []
        command_sub = probe.create_subscription(
            HandCommand,
            '/thing/command',
            commands.append,
            COMMAND_QOS,
        )
        selected_pub = probe.create_publisher(
            HandCommand,
            '/thing/command/selected',
            COMMAND_QOS,
        )
        safety_pub = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        control_pub = probe.create_publisher(
            ControlState,
            '/thing/control_state',
            STATE_QOS,
        )
        assert wait_until(lambda: selected_pub.get_subscription_count() == 1)
        publish_ready_activation(safety_pub, control_pub)
        sleep(0.1)

        for sequence in (0xFFFFFFFE, 0xFFFFFFFF, 0):
            selected_pub.publish(make_command(probe, sequence=sequence))
            assert wait_until(lambda: len(commands) == sequence - 0xFFFFFFFD
                              if sequence else len(commands) == 3)
        assert [message.sequence for message in commands] == [
            0xFFFFFFFE,
            0xFFFFFFFF,
            0,
        ]
        probe.destroy_subscription(command_sub)


def test_node_publishes_only_valid_commands_and_reports_rejection_reason():
    with running_nodes() as (_, probe):
        commands = []
        diagnostics = []
        command_sub = probe.create_subscription(
            HandCommand,
            '/thing/command',
            commands.append,
            COMMAND_QOS,
        )
        diagnostic_sub = probe.create_subscription(
            DiagnosticArray,
            '/thing/diagnostics',
            diagnostics.append,
            10,
        )
        selected_pub = probe.create_publisher(
            HandCommand,
            '/thing/command/selected',
            COMMAND_QOS,
        )
        safety_pub = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        control_pub = probe.create_publisher(
            ControlState,
            '/thing/control_state',
            STATE_QOS,
        )

        assert wait_until(lambda: selected_pub.get_subscription_count() == 1)
        assert wait_until(lambda: safety_pub.get_subscription_count() == 1)
        assert wait_until(lambda: control_pub.get_subscription_count() == 1)
        publish_ready_activation(safety_pub, control_pub)
        sleep(0.1)

        selected_pub.publish(make_command(probe, sequence=1))
        assert wait_until(lambda: len(commands) == 1)
        assert commands[0].sequence == 1

        invalid = make_command(probe, sequence=2)
        invalid.thumb_flex = float('nan')
        selected_pub.publish(invalid)
        assert wait_until(
            lambda: diagnostics
            and diagnostics[-1].status
            and diagnostics[-1].status[0].message == 'axis_non_finite'
        )
        sleep(0.1)
        assert [message.sequence for message in commands] == [1]

        probe.destroy_subscription(command_sub)
        probe.destroy_subscription(diagnostic_sub)


def test_active_latched_state_without_disabled_boundary_is_rejected():
    with running_nodes() as (_, probe):
        commands = []
        diagnostics = []
        probe.create_subscription(
            HandCommand,
            '/thing/command',
            commands.append,
            COMMAND_QOS,
        )
        probe.create_subscription(
            DiagnosticArray,
            '/thing/diagnostics',
            diagnostics.append,
            10,
        )
        selected_pub = probe.create_publisher(
            HandCommand,
            '/thing/command/selected',
            COMMAND_QOS,
        )
        safety_pub = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        control_pub = probe.create_publisher(
            ControlState,
            '/thing/control_state',
            STATE_QOS,
        )

        assert wait_until(lambda: selected_pub.get_subscription_count() == 1)
        safety = SafetyState()
        safety.state = SafetyState.READY
        safety.stamp.sec = 1
        safety_pub.publish(safety)
        active = ControlState()
        active.active_mode = ControlState.MODE_MIMIC
        active.active_owner = ControlState.OWNER_WEB
        active.owner_alive = True
        control_pub.publish(active)
        sleep(0.1)

        selected_pub.publish(make_command(probe))
        assert wait_until(
            lambda: diagnostics
            and diagnostics[-1].status[0].message
            == 'control_activation_not_observed'
        )
        assert commands == []


def test_hold_emits_validated_activity_without_forwarding_motor_command():
    with running_nodes() as (_, probe):
        commands = []
        validation_results = []
        command_sub = probe.create_subscription(
            HandCommand,
            '/thing/command',
            commands.append,
            COMMAND_QOS,
        )
        activity_sub = probe.create_subscription(
            Bool,
            '/thing/command/validation_result',
            validation_results.append,
            10,
        )
        selected_pub = probe.create_publisher(
            HandCommand,
            '/thing/command/selected',
            COMMAND_QOS,
        )
        safety_pub = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        control_pub = probe.create_publisher(
            ControlState,
            '/thing/control_state',
            STATE_QOS,
        )
        assert wait_until(lambda: selected_pub.get_subscription_count() == 1)
        publish_ready_activation(safety_pub, control_pub)
        sleep(0.1)

        selected_pub.publish(make_command(probe, sequence=1))
        assert wait_until(lambda: len(commands) == 1)

        hold = SafetyState()
        hold.state = SafetyState.HOLD
        hold.stamp.sec = 2
        safety_pub.publish(hold)
        sleep(0.05)
        selected_pub.publish(make_command(probe, sequence=2))
        assert wait_until(lambda: len(validation_results) == 1)
        assert validation_results[0].data is True
        assert len(commands) == 1

        run = SafetyState()
        run.state = SafetyState.RUN
        run.stamp.sec = 3
        safety_pub.publish(run)
        sleep(0.05)
        selected_pub.publish(make_command(probe, sequence=3))
        assert wait_until(lambda: len(commands) == 2)

        probe.destroy_subscription(command_sub)
        probe.destroy_subscription(activity_sub)


def test_safety_transition_cannot_complete_between_validation_and_publish():
    with running_nodes() as (guard, _):
        validation_checked = Event()
        allow_publish = Event()
        safety_completed = Event()
        events = []

        def blocking_validate(*_args, **_kwargs):
            validation_checked.set()
            assert allow_publish.wait(timeout=1.0)
            return GuardDecision(True, 'accepted')

        class RecordingPublisher:
            def publish(self, _message):
                events.append('published')

        guard._core.validate = blocking_validate
        guard._command_publisher = RecordingPublisher()

        command_thread = Thread(
            target=guard._on_selected_command,
            args=(make_command(guard),),
        )
        command_thread.start()
        assert validation_checked.wait(timeout=1.0)

        safety = SafetyState()
        safety.state = SafetyState.FAULT
        safety.stamp.sec = 1

        def apply_safety():
            guard._on_safety_state(safety)
            events.append('safety_completed')
            safety_completed.set()

        safety_thread = Thread(target=apply_safety)
        safety_thread.start()
        completed_before_publish = safety_completed.wait(timeout=0.05)
        allow_publish.set()
        command_thread.join(timeout=1.0)
        safety_thread.join(timeout=1.0)

        assert completed_before_publish is False
        assert events == ['published', 'safety_completed']


def test_main_treats_external_shutdown_as_clean_exit(monkeypatch):
    events = []

    class FakeNode:
        def destroy_node(self):
            events.append('destroy_node')

    monkeypatch.setattr(
        command_guard_module.rclpy,
        'init',
        lambda args=None: events.append(('init', args)),
    )
    monkeypatch.setattr(command_guard_module, 'CommandGuardNode', FakeNode)

    def raise_external_shutdown(_node):
        raise ExternalShutdownException()

    monkeypatch.setattr(
        command_guard_module.rclpy,
        'spin',
        raise_external_shutdown,
    )
    monkeypatch.setattr(command_guard_module.rclpy, 'ok', lambda: False)

    command_guard_module.main()

    assert events == [('init', None), 'destroy_node']
