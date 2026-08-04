"""ROS adapter tests for the timestamp-based Safety Manager."""

from contextlib import contextmanager
import math
from time import monotonic, sleep

import pytest
import rclpy
from rclpy.clock import ClockType
from rclpy.duration import Duration
from rclpy.parameter import Parameter
from rclpy.qos import DurabilityPolicy, HistoryPolicy, ReliabilityPolicy
from std_msgs.msg import Bool, UInt64
from std_srvs.srv import Trigger

from thing_control.safety_manager import SafetyManager
from thing_interfaces.msg import (
    ControlState,
    HandCommand,
    MotorState,
    MotorStatus,
    SafetyState,
)


def make_stop_event(_node, generation=1):
    message = UInt64()
    message.data = generation
    return message


@contextmanager
def safety_node(*additional_overrides):
    rclpy.init()
    node = SafetyManager(parameter_overrides=[
        Parameter('trip_limits_validated', value=True),
        *additional_overrides,
    ])
    try:
        yield node
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def motor_status(node, *, torque_enabled=False, communication_ok=True):
    message = MotorStatus()
    message.header.stamp = node._system_clock.now().to_msg()
    message.bus_communication_ok = communication_ok
    for motor_id in range(1, 8):
        motor = MotorState()
        motor.motor_id = motor_id
        motor.communication_ok = communication_ok
        motor.torque_enabled = torque_enabled
        motor.current_ampere = 0.1
        motor.temperature_celsius = 30.0
        message.motors.append(motor)
    return message


def maintain_heartbeats(node, duration_s, *, torque_enabled=True):
    deadline = monotonic() + duration_s
    while monotonic() < deadline:
        sleep(min(0.05, max(0.0, deadline - monotonic())))
        inactive = Bool()
        inactive.data = False
        node._on_estop(inactive)
        node._on_motor_status(motor_status(
            node,
            torque_enabled=torque_enabled,
        ))


def make_ready(node):
    inactive = Bool()
    inactive.data = False
    node._on_estop(inactive)
    node._on_motor_status(motor_status(node))
    node._on_tick()
    assert node.current_state == SafetyState.READY


def make_run(node):
    make_ready(node)
    command = HandCommand()
    node._on_validated_command(command)
    assert node.current_state == SafetyState.RUN


def test_node_uses_only_existing_topics_and_trigger_service_for_reset():
    with safety_node() as node:
        assert node._steady_clock.clock_type == ClockType.STEADY_TIME
        assert node._system_clock.clock_type == ClockType.SYSTEM_TIME
        subscription_topics = {
            subscription.topic_name for subscription in node.subscriptions
        }
        assert '/thing/motor_status' in subscription_topics
        assert '/thing/estop' in subscription_topics
        assert '/thing/command' in subscription_topics
        assert '/thing/command/validation_result' in subscription_topics
        assert '/thing/command/validated_activity' not in subscription_topics
        assert '/thing/command/validation_failed' not in subscription_topics
        assert '/thing/control/stop_barrier_ack' in subscription_topics
        assert '/thing/control_state' in subscription_topics
        assert '/thing/control/stop_requested' not in subscription_topics
        assert node.reset_safety_service.srv_name == '/thing/reset_safety'
        assert (
            node.motor_status_subscription.qos_profile.durability
            == DurabilityPolicy.VOLATILE
        )
        assert node.motor_status_subscription.qos_profile.depth == 5
        assert (
            node.motor_status_subscription.qos_profile.reliability
            == ReliabilityPolicy.RELIABLE
        )
        assert (
            node.estop_subscription.qos_profile.durability
            == DurabilityPolicy.VOLATILE
        )
        state_qos = node.safety_state_publisher.qos_profile
        assert state_qos.history == HistoryPolicy.KEEP_LAST
        assert state_qos.depth == 1
        assert state_qos.reliability == ReliabilityPolicy.RELIABLE
        assert state_qos.durability == DurabilityPolicy.TRANSIENT_LOCAL

        command_qos = node.command_subscription.qos_profile
        assert command_qos.history == HistoryPolicy.KEEP_LAST
        assert command_qos.depth == 1
        assert command_qos.reliability == ReliabilityPolicy.RELIABLE
        assert command_qos.durability == DurabilityPolicy.VOLATILE


def test_node_wires_safe_action_timeout_parameter_into_core():
    with safety_node(
        Parameter('safe_action_timeout_ms', value=2500),
    ) as node:
        assert node._limits.safe_action_timeout_ms == 2500


def test_node_uses_five_and_ten_second_command_watchdog_defaults():
    with safety_node() as node:
        assert node._limits.command_hold_ms == 5000
        assert node._limits.command_safe_ms == 10000


def test_adapter_passes_system_stamp_to_safe_entry_paths():
    with safety_node() as node:
        captured = {}
        node._publish_if_changed = lambda: None

        def record_tick(now_ns, *, state_stamp_ns=None):
            captured['tick'] = (now_ns, state_stamp_ns)

        def record_activity(now_ns, *, state_stamp_ns=None):
            captured['activity'] = (now_ns, state_stamp_ns)

        node._core.tick = record_tick
        node._core.on_validated_activity = record_activity
        system_before_ns = node._system_clock.now().nanoseconds
        node._on_tick()
        validation_result = Bool()
        validation_result.data = True
        node._on_validation_result(validation_result)
        system_after_ns = node._system_clock.now().nanoseconds

        for steady_ns, state_stamp_ns in captured.values():
            assert steady_ns >= 0
            assert system_before_ns <= state_stamp_ns <= system_after_ns


def test_late_safety_state_subscriber_receives_current_latched_state():
    with safety_node() as node:
        make_ready(node)
        probe = rclpy.create_node('late_safety_state_probe')
        received = []
        subscription = probe.create_subscription(
            SafetyState,
            '/thing/safety_state',
            received.append,
            node.safety_state_publisher.qos_profile,
        )
        try:
            deadline = monotonic() + 2.0
            while not received and monotonic() < deadline:
                rclpy.spin_once(probe, timeout_sec=0.1)
            assert received
            assert received[-1].state == SafetyState.READY
        finally:
            probe.destroy_subscription(subscription)
            probe.destroy_node()


def test_safety_state_stamp_is_stable_per_transition_and_increases_on_change():
    class RecordingPublisher:
        def __init__(self):
            self.messages = []

        def publish(self, message):
            self.messages.append(message)

    with safety_node() as node:
        recorder = RecordingPublisher()
        node.safety_state_publisher = recorder
        node.publish_safety_state()
        node.publish_safety_state()
        first, duplicate = recorder.messages
        first_stamp = (first.stamp.sec, first.stamp.nanosec)
        assert (duplicate.stamp.sec, duplicate.stamp.nanosec) == first_stamp

        make_ready(node)
        transitioned = recorder.messages[-1]
        transitioned_stamp = (
            transitioned.stamp.sec,
            transitioned.stamp.nanosec,
        )
        assert transitioned_stamp > first_stamp


def test_hardware_fault_publication_waits_for_estop_priority_window():
    with safety_node() as node:
        make_ready(node)
        published_epoch = node._last_published_epoch

        for _ in range(3):
            node._on_motor_status(motor_status(node, communication_ok=False))
        fault_snapshot = node._core.snapshot()
        assert fault_snapshot.state == SafetyState.FAULT
        assert node._last_published_epoch == published_epoch

        active = Bool()
        active.data = True
        node._on_estop(active)
        estop_snapshot = node._core.snapshot()
        assert estop_snapshot.state == SafetyState.ESTOP
        assert node._last_published_epoch == estop_snapshot.transition_epoch


def test_hardware_fault_is_published_after_bounded_priority_window():
    with safety_node() as node:
        make_ready(node)
        for _ in range(3):
            node._on_motor_status(motor_status(node, communication_ok=False))
        fault_snapshot = node._core.snapshot()
        assert node._last_published_epoch != fault_snapshot.transition_epoch

        node._fault_publish_not_before_ns = 0
        node._on_tick()
        assert node._last_published_epoch == fault_snapshot.transition_epoch


def test_tick_fault_publication_also_waits_for_estop_priority_window():
    """A stale-motor timer and a ready E-Stop callback must prefer ESTOP."""
    with safety_node() as node:
        make_ready(node)
        published_epoch = node._last_published_epoch
        last_motor_ns = node._core._hardware.received_ns

        # Keep E-Stop fresh, but let MotorStatus reach its 300 ms deadline.
        node._core.update_estop(False, last_motor_ns + 200_000_000)
        node._core.tick(last_motor_ns + 300_000_000)
        node._publish_if_changed()

        assert node.current_state == SafetyState.FAULT
        assert node._last_published_epoch == published_epoch
        assert node._fault_publish_not_before_ns is not None

        active = Bool()
        active.data = True
        node._on_estop(active)
        assert node.current_state == SafetyState.ESTOP
        assert node._last_published_epoch == node._core.snapshot().transition_epoch


def test_owner_lease_expiry_control_state_enters_hold_without_watchdog_delay():
    with safety_node() as node:
        make_run(node)
        expired = ControlState()
        expired.active_mode = ControlState.MODE_DISABLED
        expired.active_owner = ControlState.OWNER_NONE
        expired.owner_alive = False
        expired.last_transition_reason = 'owner_lease_expired'

        node._on_control_state(expired)

        assert node.current_state == SafetyState.HOLD
        assert node.command_timeout is True


def test_init_requires_fresh_estop_and_seven_torque_off_motors():
    with safety_node() as node:
        node._on_motor_status(motor_status(node))
        node._on_tick()
        assert node.current_state == SafetyState.INIT

        inactive = Bool()
        inactive.data = False
        node._on_estop(inactive)
        node._on_tick()
        assert node.current_state == SafetyState.READY


def test_stop_from_ready_or_run_enters_reset_then_fresh_torque_off_ready():
    for start_in_run in (False, True):
        with safety_node() as node:
            if start_in_run:
                make_run(node)
            else:
                make_ready(node)

            node.handle_stop_requested(make_stop_event(node))
            assert node.current_state == SafetyState.RESET

            maintain_heartbeats(
                node,
                0.51,
                torque_enabled=False,
            )
            node._on_tick()
            assert node.current_state == SafetyState.READY


def test_stop_during_hold_enters_reset():
    with safety_node(
        Parameter('command_hold_ms', value=300),
        Parameter('command_safe_ms', value=1000),
    ) as node:
        make_run(node)
        maintain_heartbeats(node, 0.31)
        node._on_tick()
        assert node.current_state == SafetyState.HOLD

        node.handle_stop_requested(make_stop_event(node))
        assert node.current_state == SafetyState.RESET


def test_estop_and_motor_fault_preempt_reset():
    with safety_node() as node:
        make_ready(node)
        node.handle_stop_requested(make_stop_event(node))
        active = Bool()
        active.data = True
        node._on_estop(active)
        assert node.current_state == SafetyState.ESTOP

    with safety_node() as node:
        make_ready(node)
        node.handle_stop_requested(make_stop_event(node))
        for _ in range(3):
            node._on_motor_status(motor_status(node, communication_ok=False))
        assert node.current_state == SafetyState.FAULT


def test_reset_safety_is_rejected_during_reset():
    with safety_node() as node:
        make_ready(node)
        node.handle_stop_requested(make_stop_event(node))
        response = node.handle_reset_safety(
            Trigger.Request(),
            Trigger.Response(),
        )
        assert response.success is False
        assert response.message == 'safety_reset_not_allowed'


def test_duplicate_motor_ids_fail_closed():
    with safety_node() as node:
        make_ready(node)
        duplicate = motor_status(node)
        for motor in duplicate.motors:
            motor.motor_id = 1
        node._on_motor_status(duplicate)
        assert node.current_state == SafetyState.FAULT


@pytest.mark.parametrize('field_name', [
    'goal_position_rad',
    'present_position_rad',
    'velocity_rad_s',
    'current_ampere',
    'voltage_volt',
    'temperature_celsius',
])
@pytest.mark.parametrize('invalid_value', [math.nan, math.inf, -math.inf])
def test_every_non_finite_motor_measurement_fails_closed(
    field_name,
    invalid_value,
):
    with safety_node() as node:
        make_ready(node)
        invalid = motor_status(node)
        setattr(invalid.motors[0], field_name, invalid_value)
        node._on_motor_status(invalid)
        assert node.current_state == SafetyState.FAULT


def test_stale_and_future_motor_status_stamps_fail_closed():
    with safety_node() as node:
        make_ready(node)
        stale = motor_status(node)
        stale.header.stamp = (
            node._system_clock.now() - Duration(nanoseconds=301_000_000)
        ).to_msg()
        node._on_motor_status(stale)
        assert node.current_state == SafetyState.FAULT

    with safety_node() as node:
        make_ready(node)
        future = motor_status(node)
        future.header.stamp = (
            node._system_clock.now() + Duration(nanoseconds=101_000_000)
        ).to_msg()
        node._on_motor_status(future)
        assert node.current_state == SafetyState.FAULT


def test_non_canonical_motor_status_stamp_fails_closed():
    with safety_node() as node:
        make_ready(node)
        invalid = motor_status(node)
        invalid.header.stamp.nanosec = 1_000_000_000
        node._on_motor_status(invalid)
        assert node.current_state == SafetyState.FAULT


@pytest.mark.parametrize('name,value', [
    ('tick_period_ms', 21),
    ('state_publish_period_ms', 101),
])
def test_timer_parameters_cannot_weaken_default_safety_envelope(name, value):
    rclpy.init()
    try:
        with pytest.raises(ValueError):
            SafetyManager(parameter_overrides=[
                Parameter('trip_limits_validated', value=True),
                Parameter(name, value=value),
            ])
    finally:
        if rclpy.ok():
            rclpy.shutdown()


@pytest.mark.parametrize('name', ['max_current_ampere', 'max_temperature_celsius'])
@pytest.mark.parametrize('value', [math.nan, math.inf])
def test_non_finite_trip_parameters_are_rejected(name, value):
    rclpy.init()
    try:
        with pytest.raises(ValueError):
            SafetyManager(parameter_overrides=[
                Parameter('trip_limits_validated', value=True),
                Parameter(name, value=value),
            ])
    finally:
        if rclpy.ok():
            rclpy.shutdown()
