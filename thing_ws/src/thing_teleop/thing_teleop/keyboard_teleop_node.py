"""ROS 2 state monitor with a curses keyboard teleoperation interface."""

from __future__ import annotations

import curses
from collections import deque
from threading import RLock, Thread
from time import monotonic, sleep

import rclpy
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)

from thing_interfaces.msg import ControlState, HandCommand, MotorStatus, SafetyState
from thing_interfaces.srv import SetControlMode
from thing_teleop.teleop_core import TeleopCore
from thing_teleop.teleop_ui import (
    MotorUiState,
    SystemUiState,
    apply_preview_key,
    build_screen_lines,
    draw_screen,
)


_STATE_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)
_MOTOR_STATUS_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=5,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.VOLATILE,
)
_COMMAND_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.VOLATILE,
)

_CONTROL_NAMES = {
    ControlState.MODE_DISABLED: 'DISABLED',
    ControlState.MODE_MIMIC: 'MIMIC',
    ControlState.MODE_MANUAL: 'MANUAL',
    ControlState.MODE_TELEOP: 'TELEOP',
}
_OWNER_NAMES = {
    ControlState.OWNER_NONE: 'NONE',
    ControlState.OWNER_WEB: 'WEB',
    ControlState.OWNER_LOCAL: 'LOCAL',
}
_SAFETY_NAMES = {
    SafetyState.INIT: 'INIT',
    SafetyState.READY: 'READY',
    SafetyState.RUN: 'RUN',
    SafetyState.HOLD: 'HOLD',
    SafetyState.SAFE: 'SAFE',
    SafetyState.FAULT: 'FAULT',
    SafetyState.ESTOP: 'ESTOP',
    SafetyState.RESET: 'RESET',
}
_ACTUATOR_TO_AXIS = {
    'thumb_opposition': 'thumb_opp',
    'thumb_abduction': 'thumb_abd',
    'thumb_flex': 'thumb_flex',
    'index_flex': 'index_flex',
    'middle_flex': 'middle_flex',
    'ring_flex': 'ring_flex',
    'little_flex': 'little_flex',
}


class KeyboardTeleopNode(Node):
    """Collect ROS state for the keyboard teleoperation screen."""

    def __init__(self) -> None:
        super().__init__('keyboard_teleop_node')
        self._state_lock = RLock()
        step_size = float(self.declare_parameter('step_size', 0.01).value)
        self.speed_limit = float(
            self.declare_parameter('speed_limit', 1.0).value
        )
        if not 0.0 < self.speed_limit <= 1.0:
            raise ValueError('speed_limit must be in the range (0.0, 1.0]')
        publish_rate_hz = float(
            self.declare_parameter('publish_rate_hz', 20.0).value
        )
        state_timeout_ms = int(
            self.declare_parameter('state_timeout_ms', 1500).value
        )
        if publish_rate_hz <= 0.0:
            raise ValueError('publish_rate_hz must be positive')
        if state_timeout_ms <= 0:
            raise ValueError('state_timeout_ms must be positive')
        self._state_timeout = state_timeout_ms / 1000.0

        self.core = TeleopCore(step_size=step_size)
        self.armed = False
        self._sequence = 0
        self.control_name = 'UNKNOWN'
        self.owner_name = 'NONE'
        self.owner_alive = False
        self.safety_name = 'UNKNOWN'
        self._control_mode: int | None = None
        self._control_owner: int | None = None
        self._safety_state: int | None = None
        self._last_control_state_time: float | None = None
        self._last_safety_state_time: float | None = None
        self.bus_communication_ok = False
        self.motors: dict[str, MotorUiState] = {}
        self.status = 'Monitoring ROS state; commands are not being published'
        self._motor_receive_times: deque[float] = deque(maxlen=100)
        self._last_motor_status_time: float | None = None
        self._mode_request = None
        self._mode_request_action = ''
        self._mode_request_deadline = 0.0
        self._lease_heartbeat_period = float(
            self.declare_parameter('lease_heartbeat_ms', 1000).value
        ) / 1000.0
        self._service_timeout = float(
            self.declare_parameter('service_timeout_ms', 1500).value
        ) / 1000.0
        if self._lease_heartbeat_period <= 0.0:
            raise ValueError('lease_heartbeat_ms must be positive')
        if self._service_timeout <= 0.0:
            raise ValueError('service_timeout_ms must be positive')
        self._next_lease_heartbeat = 0.0
        self._lease_enabled = False
        self._stop_queued = False
        self._stop_complete = False
        self._exit_after_stop = False
        self.exit_ready = False

        self.create_subscription(
            MotorStatus,
            '/thing/motor_status',
            self._on_motor_status,
            _MOTOR_STATUS_QOS,
        )
        self.create_subscription(
            ControlState,
            '/thing/control_state',
            self._on_control_state,
            _STATE_QOS,
        )
        self._command_publisher = self.create_publisher(
            HandCommand,
            '/thing/command/teleop',
            _COMMAND_QOS,
        )
        self._command_timer = self.create_timer(
            1.0 / publish_rate_hz,
            self._publish_command,
        )
        self._lifecycle_timer = self.create_timer(
            0.05,
            self.update_control_lifecycle,
        )
        self._mode_client = self.create_client(
            SetControlMode,
            '/thing/set_control_mode',
        )
        self.create_subscription(
            SafetyState,
            '/thing/safety_state',
            self._on_safety_state,
            _STATE_QOS,
        )

    def _on_motor_status(self, message: MotorStatus) -> None:
        with self._state_lock:
            now = monotonic()
            self._last_motor_status_time = now
            self._motor_receive_times.append(now)
            self.bus_communication_ok = message.bus_communication_ok
            for motor in message.motors:
                axis_name = _ACTUATOR_TO_AXIS.get(motor.actuator_name)
                if axis_name is None:
                    continue
                self.motors[axis_name] = MotorUiState(
                    motor_id=motor.motor_id,
                    goal_position_raw=motor.goal_position_raw,
                    present_position_raw=motor.present_position_raw,
                    current_ampere=motor.current_ampere,
                    temperature_celsius=motor.temperature_celsius,
                    torque_enabled=motor.torque_enabled,
                    communication_ok=motor.communication_ok,
                    received=True,
                )

    def _on_control_state(self, message: ControlState) -> None:
        with self._state_lock:
            self._last_control_state_time = monotonic()
            self._control_mode = message.active_mode
            self._control_owner = message.active_owner
            self.control_name = _CONTROL_NAMES.get(
                message.active_mode,
                f'UNKNOWN({message.active_mode})',
            )
            self.owner_name = _OWNER_NAMES.get(
                message.active_owner,
                f'UNKNOWN({message.active_owner})',
            )
            self.owner_alive = message.owner_alive
            if (
                message.active_mode != ControlState.MODE_TELEOP
                or message.active_owner != ControlState.OWNER_LOCAL
            ):
                self._lease_enabled = False
                self._disarm('Control mode or owner changed')

    def _on_safety_state(self, message: SafetyState) -> None:
        with self._state_lock:
            self._last_safety_state_time = monotonic()
            self._safety_state = message.state
            self.safety_name = _SAFETY_NAMES.get(
                message.state,
                f'UNKNOWN({message.state})',
            )
            if message.state not in (SafetyState.READY, SafetyState.RUN):
                self._disarm(f'Safety state changed to {self.safety_name}')

    def motor_receive_rate_hz(self) -> float:
        """Estimate MotorStatus receive rate from the local steady clock."""
        with self._state_lock:
            if len(self._motor_receive_times) < 2:
                return 0.0
            elapsed = self._motor_receive_times[-1] - self._motor_receive_times[0]
            if elapsed <= 0.0:
                return 0.0
            return (len(self._motor_receive_times) - 1) / elapsed

    def motor_states_for_display(self) -> dict[str, MotorUiState]:
        """Return feedback, marking all motors stale after one second."""
        with self._state_lock:
            if (
                self._last_motor_status_time is None
                or monotonic() - self._last_motor_status_time > 1.0
            ):
                return {
                    name: MotorUiState(
                        motor_id=motor.motor_id,
                        received=False,
                    )
                    for name, motor in self.motors.items()
                }
            return dict(self.motors)

    def system_state_for_display(self) -> SystemUiState:
        """Build the system summary displayed by curses."""
        with self._state_lock:
            return SystemUiState(
                control=self.control_name,
                owner=self.owner_name,
                owner_alive=self.owner_alive,
                safety=self.safety_name,
                armed=self.armed,
                bus_communication_ok=self.bus_communication_ok,
                publish_rate_hz=self.motor_receive_rate_hz(),
            )

    def ui_snapshot(
        self,
    ) -> tuple[TeleopCore, SystemUiState, dict[str, MotorUiState], str]:
        """Copy all mutable state needed for one consistent UI frame."""
        with self._state_lock:
            core = TeleopCore(
                step_size=self.core.step_size,
                selected_index=self.core.selected_index,
                thumb_pose=self.core.thumb_pose,
                targets=list(self.core.targets),
            )
            return (
                core,
                self.system_state_for_display(),
                self.motor_states_for_display(),
                self.status,
            )

    def request_teleop_control(self) -> None:
        """Request TELEOP/LOCAL control without arming command output."""
        with self._state_lock:
            self._disarm('Requesting TELEOP/LOCAL control')
            if self._mode_request is not None:
                self.status = 'A control-mode request is already in progress'
                return
            self._start_mode_request(
                ControlState.MODE_TELEOP,
                ControlState.OWNER_LOCAL,
                'acquire',
            )

    def request_stop(self, exit_after: bool = False) -> None:
        """Stop lease renewal and request explicit DISABLED/NONE control."""
        with self._state_lock:
            self._disarm('STOP requested; command publishing stopped')
            self._lease_enabled = False
            self._stop_complete = False
            self._exit_after_stop = self._exit_after_stop or exit_after
            if self._mode_request is not None:
                if self._mode_request_action == 'stop':
                    return
                self._stop_queued = True
                self.status = 'STOP queued behind the current control request...'
                return
            self._start_stop_request()

    def _start_stop_request(self) -> None:
        self._stop_queued = False
        self._start_mode_request(
            ControlState.MODE_DISABLED,
            ControlState.OWNER_NONE,
            'stop',
        )

    def _start_mode_request(
        self,
        requested_mode: int,
        requested_owner: int,
        action: str,
    ) -> None:
        if not self._mode_client.service_is_ready():
            self.status = (
                'STOP service unavailable; command output remains disarmed'
                if action == 'stop'
                else 'SetControlMode service is not available'
            )
            if action == 'stop':
                self._stop_complete = True
                if self._exit_after_stop:
                    self.exit_ready = True
            return
        request = SetControlMode.Request()
        request.requested_mode = requested_mode
        request.requested_owner = requested_owner
        self._mode_request = self._mode_client.call_async(request)
        self._mode_request_action = action
        self._mode_request_deadline = monotonic() + self._service_timeout
        if action == 'stop':
            self.status = 'STOP requested; waiting for command manager...'
        elif action == 'acquire':
            self.status = 'TELEOP/LOCAL control requested...'

    def update_control_lifecycle(self) -> None:
        """Poll service completion and send owner-lease heartbeats."""
        with self._state_lock:
            now = monotonic()
            if self._mode_request is not None:
                if self._mode_request.done():
                    self._finish_mode_request(now)
                elif now >= self._mode_request_deadline:
                    action = self._mode_request_action
                    self._mode_request.cancel()
                    self._mode_request = None
                    self._mode_request_action = ''
                    self._lease_enabled = False
                    self.status = (
                        f'{action.upper()} request timed out; '
                        'command output is disarmed'
                    )
                    if self._stop_queued and action != 'stop':
                        self._start_stop_request()
                        return
                    if action == 'stop':
                        self._stop_complete = True
                        if self._exit_after_stop:
                            self.exit_ready = True
                return

            if self._lease_enabled and now >= self._next_lease_heartbeat:
                self._start_mode_request(
                    ControlState.MODE_TELEOP,
                    ControlState.OWNER_LOCAL,
                    'heartbeat',
                )

    def _finish_mode_request(self, now: float) -> None:
        action = self._mode_request_action
        future = self._mode_request
        self._mode_request = None
        self._mode_request_action = ''
        try:
            response = future.result()
        except Exception as error:  # noqa: B902 - ROS future exceptions vary
            self._lease_enabled = False
            self.status = f'{action.upper()} request failed: {error}'
            if self._stop_queued and action != 'stop':
                self._start_stop_request()
                return
            if action == 'stop':
                self._stop_complete = True
                if self._exit_after_stop:
                    self.exit_ready = True
            return

        if not response.accepted:
            self._lease_enabled = False
            self.status = f'{action.upper()} rejected: {response.reason}'
        elif action == 'stop':
            self._lease_enabled = False
            self._stop_complete = True
            self.status = 'Control released: DISABLED / NONE'
        else:
            self._lease_enabled = True
            self._next_lease_heartbeat = now + self._lease_heartbeat_period
            if action == 'acquire':
                self.status = 'TELEOP/LOCAL acquired; command output remains disarmed'

        if self._stop_queued and action != 'stop':
            self._start_stop_request()
            return
        if action == 'stop' and self._exit_after_stop:
            self.exit_ready = True

    def best_effort_stop(self) -> None:
        """Try to release control during abnormal or external shutdown."""
        if not rclpy.ok():
            return
        self.request_stop()
        deadline = monotonic() + self._service_timeout + 0.1
        while monotonic() < deadline:
            with self._state_lock:
                if self._stop_complete:
                    return
            sleep(0.01)

    def arm_home(self) -> None:
        """Set a known home command and arm output when every gate is valid."""
        with self._state_lock:
            self.core.set_home()
            invalid_reason = self._command_gate_reason()
            if invalid_reason:
                self.armed = False
                self.status = f'Cannot arm: {invalid_reason}'
                return
            self.armed = True
            self.status = 'ARMED at home; publishing TELEOP command at 20 Hz'

    def _command_gate_reason(self) -> str:
        now = monotonic()
        if self._last_control_state_time is None:
            return 'control_state unavailable'
        if now - self._last_control_state_time > self._state_timeout:
            return 'control_state stale'
        if self._control_mode != ControlState.MODE_TELEOP:
            return 'control mode is not TELEOP'
        if self._control_owner != ControlState.OWNER_LOCAL:
            return 'control owner is not LOCAL'
        if not self.owner_alive:
            return 'control owner lease is not alive'
        if self._last_safety_state_time is None:
            return 'safety_state unavailable'
        if now - self._last_safety_state_time > self._state_timeout:
            return 'safety_state stale'
        if self._safety_state not in (SafetyState.READY, SafetyState.RUN):
            return f'safety state is {self.safety_name}'
        return ''

    def _disarm(self, reason: str) -> None:
        if not self.armed:
            return
        self.armed = False
        self.status = f'DISARMED: {reason}; press h to arm again'

    def _publish_command(self) -> None:
        with self._state_lock:
            if not self.armed:
                return
            invalid_reason = self._command_gate_reason()
            if invalid_reason:
                self._disarm(invalid_reason)
                return

            values = self.core.command_values()
            self._sequence = (self._sequence + 1) & 0xFFFFFFFF
            message = HandCommand()
            message.stamp = self.get_clock().now().to_msg()
            message.sequence = self._sequence
            message.source = HandCommand.SOURCE_TELEOP
            message.thumb_flex = values['thumb_flex']
            message.thumb_opp = values['thumb_opp']
            message.thumb_abd = values['thumb_abd']
            message.index_flex = values['index_flex']
            message.middle_flex = values['middle_flex']
            message.ring_flex = values['ring_flex']
            message.little_flex = values['little_flex']
            message.speed_limit = self.speed_limit
            message.confidence = 1.0
        self._command_publisher.publish(message)

    def apply_motion_key(self, key: int) -> None:
        """Apply a keyboard target change while protecting publisher state."""
        with self._state_lock:
            _, status = apply_preview_key(self.core, key)
            if status:
                self.status = status

    def should_exit(self) -> bool:
        """Return whether graceful STOP processing permits UI exit."""
        with self._state_lock:
            return self.exit_ready


def run_ui(screen: curses.window, node: KeyboardTeleopNode) -> None:
    """Render snapshots and process keys while ROS runs independently."""
    curses.curs_set(0)
    screen.keypad(True)
    screen.timeout(50)
    running = True
    while running and rclpy.ok():
        if node.should_exit():
            break
        core, system, motors, status = node.ui_snapshot()
        lines = build_screen_lines(
            core,
            node.speed_limit,
            system,
            motors,
            status,
        )
        draw_screen(screen, lines)
        key = screen.getch()
        if key == -1:
            continue
        if key in (ord('m'), ord('M')):
            node.request_teleop_control()
            continue
        if key == ord(' '):
            node.request_stop()
            continue
        if key in (ord('q'), ord('Q')):
            node.request_stop(exit_after=True)
            continue
        if key in (ord('h'), ord('H')):
            node.arm_home()
            continue
        node.apply_motion_key(key)


def main(args=None) -> None:
    """Run the ROS state monitor and curses user interface."""
    rclpy.init(args=args)
    node = KeyboardTeleopNode()
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor_thread = Thread(target=executor.spin, daemon=True)
    executor_thread.start()
    try:
        curses.wrapper(run_ui, node)
    except (ExternalShutdownException, KeyboardInterrupt):
        pass
    finally:
        if not node.should_exit():
            node.best_effort_stop()
        executor.shutdown(timeout_sec=2.0)
        executor_thread.join(timeout=2.0)
        executor.remove_node(node)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
