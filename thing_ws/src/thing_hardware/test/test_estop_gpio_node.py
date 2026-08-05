"""ROS adapter tests for the E-Stop GPIO publisher."""

import time

import rclpy
from rclpy.clock import ClockType
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from std_msgs.msg import Bool

from thing_hardware.estop_gpio_node import EstopGpioNode


_HEARTBEAT_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.VOLATILE,
)


def test_poll_and_heartbeat_timers_use_steady_time():
    """ROS time pause or jump must not suspend safety polling or heartbeat."""
    rclpy.init()
    node = EstopGpioNode(gpiod_loader=lambda: None)
    try:
        assert node._poll_timer.clock.clock_type == ClockType.STEADY_TIME
        assert node._heartbeat_timer.clock.clock_type == ClockType.STEADY_TIME
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_safety_critical_gpio_parameters_are_read_only_after_startup():
    """Runtime parameter services must not widen a validated startup contract."""
    rclpy.init()
    node = EstopGpioNode(gpiod_loader=lambda: None)
    try:
        results = node.set_parameters([
            Parameter('poll_interval_ms', value=6),
        ])

        assert results[0].successful is False
        assert node.get_parameter('poll_interval_ms').value == 5
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_safety_node_disables_runtime_parameter_services():
    """Startup-only safety configuration must not expose mutation endpoints."""
    rclpy.init()
    node = EstopGpioNode(gpiod_loader=lambda: None)
    try:
        own_services = {
            name
            for name, _ in node.get_service_names_and_types()
            if name.startswith('/estop_gpio_node/')
        }
        assert own_services == set()
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_missing_gpiod_keeps_publishing_active_fail_closed_heartbeat():
    """A missing GPIO runtime must not make the E-Stop heartbeat disappear."""
    def missing_gpiod():
        raise ModuleNotFoundError('gpiod unavailable in test')

    rclpy.init()
    node = EstopGpioNode(gpiod_loader=missing_gpiod)
    probe = Node('estop_missing_gpiod_probe')
    received = []
    probe.create_subscription(
        Bool,
        '/thing/estop',
        lambda message: received.append(message.data),
        _HEARTBEAT_QOS,
    )
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(probe)

    try:
        deadline = time.monotonic() + 0.35
        while time.monotonic() < deadline and len(received) < 2:
            executor.spin_once(timeout_sec=0.02)

        assert len(received) >= 2
        assert all(received)
    finally:
        executor.remove_node(probe)
        executor.remove_node(node)
        probe.destroy_node()
        node.destroy_node()
        executor.shutdown()
        rclpy.shutdown()
