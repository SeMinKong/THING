"""End-to-end normal control handoff across all control safety nodes."""

from threading import Thread
from time import monotonic, sleep

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool

from thing_control.command_guard import CommandGuardNode
from thing_control.command_manager import CommandManagerNode
from thing_control.safety_manager import SafetyManager
from thing_interfaces.msg import (
    ControlState,
    HandCommand,
    MotorState,
    MotorStatus,
    SafetyState,
)
from thing_interfaces.srv import SetControlMode


STATE_QOS = QoSProfile(
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)
COMMAND_QOS = QoSProfile(
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
)


def wait_until(predicate, timeout=3.0):
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


def make_motor_status(node):
    message = MotorStatus()
    message.header.stamp = node.get_clock().now().to_msg()
    message.bus_communication_ok = True
    for motor_id in range(1, 8):
        motor = MotorState()
        motor.motor_id = motor_id
        motor.communication_ok = True
        motor.torque_enabled = False
        motor.current_ampere = 0.1
        motor.temperature_celsius = 30.0
        message.motors.append(motor)
    return message


def make_mimic_command(node, sequence=1):
    message = HandCommand()
    message.stamp = node.get_clock().now().to_msg()
    message.sequence = sequence
    message.source = HandCommand.SOURCE_MIMIC
    message.speed_limit = 0.5
    message.confidence = 0.9
    return message


def test_control_stop_runs_reset_and_allows_reacquisition_only_after_ready():
    rclpy.init()
    manager = CommandManagerNode()
    guard = CommandGuardNode()
    safety = SafetyManager(parameter_overrides=[
        Parameter('trip_limits_validated', value=True),
    ])
    probe = Node('control_reset_handoff_probe')
    executor = MultiThreadedExecutor(num_threads=5)
    for node in (manager, guard, safety, probe):
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
    motor_publisher = probe.create_publisher(
        MotorStatus,
        '/thing/motor_status',
        STATE_QOS,
    )
    estop_publisher = probe.create_publisher(
        Bool,
        '/thing/estop',
        STATE_QOS,
    )
    mimic_publisher = probe.create_publisher(
        HandCommand,
        '/thing/command/mimic',
        COMMAND_QOS,
    )
    selected_publisher = probe.create_publisher(
        HandCommand,
        '/thing/command/selected',
        COMMAND_QOS,
    )
    forwarded_commands = []
    forwarded_subscription = probe.create_subscription(
        HandCommand,
        '/thing/command',
        forwarded_commands.append,
        COMMAND_QOS,
    )
    client = probe.create_client(SetControlMode, '/thing/set_control_mode')

    inactive = Bool()
    inactive.data = False

    def publish_hardware_heartbeat():
        motor_publisher.publish(make_motor_status(probe))
        estop_publisher.publish(inactive)

    heartbeat_timer = probe.create_timer(0.05, publish_hardware_heartbeat)

    try:
        assert wait_until(client.service_is_ready)
        assert wait_until(
            lambda: safety_states
            and safety_states[-1].state == SafetyState.READY
        )
        # Service discovery proves only that Manager is alive. Guard must also have
        # consumed the startup DISABLED state and Safety READY before the first
        # activation; otherwise its fail-closed startup contract rejects commands.
        assert wait_until(
            lambda: guard._core._saw_disabled
            and guard._core._safety_state == SafetyState.READY
        )

        acquired = call_mode(
            client,
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert acquired.accepted is True

        def publish_until_run():
            mimic_publisher.publish(make_mimic_command(probe))
            return bool(
                safety_states
                and safety_states[-1].state == SafetyState.RUN
            )

        assert wait_until(publish_until_run)

        stopped = call_mode(
            client,
            ControlState.MODE_DISABLED,
            ControlState.OWNER_NONE,
        )
        assert stopped.accepted is True
        assert wait_until(
            lambda: any(
                state.state == SafetyState.RESET for state in safety_states
            )
        )
        forwarded_before_late_command = len(forwarded_commands)
        selected_publisher.publish(make_mimic_command(probe, sequence=2))
        sleep(0.1)
        assert len(forwarded_commands) == forwarded_before_late_command

        blocked = call_mode(
            client,
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert blocked.accepted is False
        assert blocked.reason in {'stop_in_progress', 'safety_not_ready'}

        assert wait_until(
            lambda: safety_states
            and safety_states[-1].state == SafetyState.READY,
            timeout=2.0,
        )
        reset_index = next(
            index for index, state in enumerate(safety_states)
            if state.state == SafetyState.RESET
        )
        assert all(
            state.state != SafetyState.HOLD
            for state in safety_states[reset_index:]
        )

        reacquired = call_mode(
            client,
            ControlState.MODE_MIMIC,
            ControlState.OWNER_WEB,
        )
        assert reacquired.accepted is True
    finally:
        heartbeat_timer.cancel()
        probe.destroy_timer(heartbeat_timer)
        probe.destroy_subscription(subscription)
        probe.destroy_subscription(forwarded_subscription)
        executor.shutdown(timeout_sec=1.0)
        thread.join(timeout=1.0)
        for node in (probe, safety, guard, manager):
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
