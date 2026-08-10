"""End-to-end ROS command_manager to command_guard contract test."""

from contextlib import contextmanager
from threading import Thread
from time import monotonic, sleep

import rclpy
from diagnostic_msgs.msg import DiagnosticArray
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)

from thing_control.command_guard import CommandGuardNode
from thing_control.command_manager import CommandManagerNode
from thing_interfaces.msg import ControlState, HandCommand, SafetyState
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
def running_pipeline():
    rclpy.init()
    manager = CommandManagerNode(
        parameter_overrides=[
            Parameter('owner_lease_timeout_ms', value=3000),
            Parameter('state_publish_period_ms', value=100),
        ]
    )
    guard = CommandGuardNode(parameter_overrides=guard_overrides())
    probe = Node('command_pipeline_test_probe')
    executor = MultiThreadedExecutor(num_threads=4)
    for node in (manager, guard, probe):
        executor.add_node(node)
    thread = Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        yield probe
    finally:
        executor.shutdown(timeout_sec=1.0)
        thread.join(timeout=1.0)
        for node in (probe, guard, manager):
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def make_command(node, sequence, value=0.5):
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


def call_mode(client, mode, owner):
    request = SetControlMode.Request()
    request.requested_mode = mode
    request.requested_owner = owner
    future = client.call_async(request)
    assert wait_until(future.done)
    return future.result()


def test_manager_selected_command_reaches_output_only_after_guard_acceptance():
    with running_pipeline() as probe:
        guarded = []
        selected = []
        diagnostics = []
        probe.create_subscription(
            HandCommand,
            '/thing/command',
            guarded.append,
            COMMAND_QOS,
        )
        probe.create_subscription(
            HandCommand,
            '/thing/command/selected',
            selected.append,
            COMMAND_QOS,
        )
        probe.create_subscription(
            DiagnosticArray,
            '/thing/diagnostics',
            diagnostics.append,
            10,
        )
        mimic_pub = probe.create_publisher(
            HandCommand,
            '/thing/command/mimic',
            COMMAND_QOS,
        )
        safety_pub = probe.create_publisher(
            SafetyState,
            '/thing/safety_state',
            STATE_QOS,
        )
        mode_client = probe.create_client(
            SetControlMode,
            '/thing/set_control_mode',
        )

        assert wait_until(mode_client.service_is_ready)
        assert wait_until(lambda: mimic_pub.get_subscription_count() == 1)
        assert wait_until(lambda: safety_pub.get_subscription_count() == 2)

        ready = SafetyState()
        ready.state = SafetyState.READY
        ready.stamp.sec = 1
        safety_pub.publish(ready)
        sleep(0.1)
        response = call_mode(
            mode_client,
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert response.accepted is True
        sleep(0.1)

        mimic_pub.publish(make_command(probe, sequence=1))
        assert wait_until(lambda: len(selected) == 1)
        assert wait_until(lambda: len(guarded) == 1)

        invalid = make_command(probe, sequence=2)
        invalid.index_flex = float('nan')
        mimic_pub.publish(invalid)
        assert wait_until(lambda: len(selected) == 2)
        assert wait_until(
            lambda: diagnostics
            and diagnostics[-1].status[0].message == 'axis_non_finite'
        )
        sleep(0.1)
        assert [message.sequence for message in guarded] == [1]

        fault = SafetyState()
        fault.state = SafetyState.FAULT
        fault.stamp.sec = 2
        safety_pub.publish(fault)
        sleep(0.1)
        mimic_pub.publish(make_command(probe, sequence=3))
        sleep(0.1)
        assert [message.sequence for message in guarded] == [1]
