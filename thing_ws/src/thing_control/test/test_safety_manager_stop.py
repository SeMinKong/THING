"""STOP settling behavior tests for the safety manager node."""

from contextlib import contextmanager
from time import monotonic

import pytest
import rclpy
from std_msgs.msg import Empty

from thing_control.safety_manager import SafetyManager
from thing_interfaces.msg import SafetyState


@contextmanager
def safety_node():
    rclpy.init()
    node = SafetyManager()
    try:
        yield node
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def spin_until(node, predicate, timeout=1.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.02)
        if predicate():
            return True
    return False


@pytest.mark.parametrize('start_state', [SafetyState.RUN, SafetyState.HOLD])
def test_stop_from_run_or_hold_settles_to_ready(start_state):
    with safety_node() as node:
        node.current_state = start_state
        node.command_timeout = True

        node.handle_stop_requested(Empty())

        assert node.current_state == SafetyState.HOLD
        assert node.command_timeout is False
        assert spin_until(
            node,
            lambda: node.current_state == SafetyState.READY,
            timeout=0.8,
        )


@pytest.mark.parametrize(
    ('flag_name', 'expected_state'),
    [
        ('fault_active', SafetyState.FAULT),
        ('estop_active', SafetyState.ESTOP),
    ],
)
def test_fault_or_estop_preempts_stop_settling(flag_name, expected_state):
    with safety_node() as node:
        node.current_state = SafetyState.RUN
        node.handle_stop_requested(Empty())
        setattr(node, flag_name, True)

        assert spin_until(
            node,
            lambda: node.current_state == expected_state,
            timeout=0.8,
        )


@pytest.mark.parametrize(
    'state',
    [SafetyState.SAFE, SafetyState.FAULT, SafetyState.ESTOP],
)
def test_stop_preserves_safe_fault_and_estop_states(state):
    with safety_node() as node:
        node.current_state = state

        node.handle_stop_requested(Empty())

        assert node.current_state == state
        assert node.stop_settle_timer is None


def test_repeated_stop_replaces_timer_without_leaking_ros_timers():
    with safety_node() as node:
        initial_timer_count = len(list(node.timers))
        node.current_state = SafetyState.RUN

        for _ in range(5):
            node.handle_stop_requested(Empty())

        assert len(list(node.timers)) == initial_timer_count + 1
        node._cancel_stop_settle_timer()
        assert len(list(node.timers)) == initial_timer_count


def test_ready_stop_keeps_ready_without_settling_timer():
    with safety_node() as node:
        node.current_state = SafetyState.READY

        node.handle_stop_requested(Empty())

        assert node.current_state == SafetyState.READY
        assert node.stop_settle_timer is None
