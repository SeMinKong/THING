"""End-to-end STOP handoff between command and safety managers."""

from threading import Thread
from time import monotonic, sleep

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

from thing_control.command_manager import CommandManagerNode
from thing_control.safety_manager import SafetyManager
from thing_interfaces.msg import ControlState, SafetyState
from thing_interfaces.srv import SetControlMode


STATE_QOS = QoSProfile(
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)


def wait_until(predicate, timeout=2.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(0.01)
    return False


def call_mode(client, mode, owner):
    request = SetControlMode.Request()
    request.requested_mode = mode
    request.requested_owner = owner
    future = client.call_async(request)
    assert wait_until(future.done)
    return future.result()


def test_stop_handoff_settles_and_allows_only_fresh_reacquisition():
    rclpy.init()
    manager = CommandManagerNode(
        parameter_overrides=[
            Parameter('stop_reacquire_delay_ms', value=100),
        ]
    )
    safety = SafetyManager(
        parameter_overrides=[
            Parameter('stop_settle_ms', value=100),
        ]
    )
    probe = Node('stop_handoff_test_probe')
    executor = MultiThreadedExecutor(num_threads=3)
    for node in (manager, safety, probe):
        executor.add_node(node)
    thread = Thread(target=executor.spin, daemon=True)
    thread.start()

    safety_states = []
    subscription = probe.create_subscription(
        SafetyState,
        '/thing/safety_state',
        safety_states.append,
        STATE_QOS,
    )
    client = probe.create_client(
        SetControlMode,
        '/thing/set_control_mode',
    )

    try:
        assert wait_until(client.service_is_ready)
        assert wait_until(
            lambda: manager._stop_event_publisher.get_subscription_count() >= 1
        )

        safety.current_state = SafetyState.READY
        safety.publish_safety_state('test_ready')
        assert wait_until(
            lambda: manager._core._safety_state == SafetyState.READY
        )

        acquired = call_mode(
            client,
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert acquired.accepted is True

        safety.current_state = SafetyState.RUN
        safety.publish_safety_state('test_run')
        assert wait_until(
            lambda: manager._core._safety_state == SafetyState.RUN
        )

        stopped = call_mode(
            client,
            ControlState.MODE_DISABLED,
            ControlState.OWNER_NONE,
        )
        assert stopped.accepted is True
        assert manager._core.snapshot().active_mode == ControlState.MODE_DISABLED

        blocked = call_mode(
            client,
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert blocked.accepted is False
        assert blocked.reason == 'stop_in_progress'

        assert wait_until(
            lambda: any(
                state.state == SafetyState.HOLD for state in safety_states
            )
        )
        assert wait_until(
            lambda: safety_states
            and safety_states[-1].state == SafetyState.READY,
            timeout=1.0,
        )

        reacquired = call_mode(
            client,
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert reacquired.accepted is True
    finally:
        probe.destroy_subscription(subscription)
        executor.shutdown(timeout_sec=1.0)
        thread.join(timeout=1.0)
        for node in (probe, safety, manager):
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
