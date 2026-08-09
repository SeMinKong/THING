"""Curses user interface for seven-axis keyboard teleoperation."""

from __future__ import annotations

import curses
from dataclasses import dataclass

from thing_teleop.teleop_core import AXIS_NAMES, TeleopCore


DISPLAY_AXIS_NAMES = ('thumb_opp', 'thumb_abd', *AXIS_NAMES)


AXIS_LABELS = {
    'thumb_opp': 'thumb opposition',
    'thumb_abd': 'thumb abduction',
    'thumb_flex': 'thumb flexion',
    'index_flex': 'index flexion',
    'middle_flex': 'middle flexion',
    'ring_flex': 'ring flexion',
    'little_flex': 'little flexion',
}


@dataclass(frozen=True)
class MotorUiState:
    """Motor feedback displayed for one logical axis."""

    motor_id: int
    goal_position_raw: int = 0
    present_position_raw: int = 0
    current_ampere: float = 0.0
    temperature_celsius: float = 0.0
    torque_enabled: bool = False
    communication_ok: bool = False
    received: bool = False


@dataclass(frozen=True)
class SystemUiState:
    """Control and safety state displayed above the axis table."""

    control: str = 'UNKNOWN'
    owner: str = 'NONE'
    owner_alive: bool = False
    safety: str = 'UNKNOWN'
    armed: bool = False
    bus_communication_ok: bool = False
    publish_rate_hz: float = 0.0


def build_screen_lines(
    core: TeleopCore,
    speed_limit: float,
    system: SystemUiState,
    motors: dict[str, MotorUiState] | None = None,
    status: str = 'UI preview: commands are not being published',
) -> list[str]:
    """Build the complete terminal screen as text lines."""
    motor_states = motors or {}
    alive = 'alive' if system.owner_alive else 'stale'
    armed = 'YES' if system.armed else 'NO'
    bus = 'OK' if system.bus_communication_ok else 'WAIT'
    lines = [
        'THING 7-Axis Keyboard Teleop',
        (
            f'Control: {system.control} / {system.owner} / {alive}    '
            f'Safety: {system.safety}    Armed: {armed}'
        ),
        (
            f'Speed limit: {speed_limit:.2f}    Step: {core.step_size:.2f}    '
            f'Motor RX: {system.publish_rate_hz:.1f} Hz    Bus: {bus}'
        ),
        '',
        (
            f'Thumb pose: {core.thumb_pose.upper():7s}  '
            '[a OPEN | s NEUTRAL | d GRASP | f FOLDED]'
        ),
        '   KEY  AXIS                 CMD   ID    GOAL  PRESENT  CURRENT  TEMP  TORQUE COMM',
    ]

    command_values = core.command_values()
    for axis_name in DISPLAY_AXIS_NAMES:
        flex_index = (
            AXIS_NAMES.index(axis_name) if axis_name in AXIS_NAMES else None
        )
        marker = '>' if flex_index == core.selected_index else ' '
        key_label = str(flex_index + 1) if flex_index is not None else '-'
        motor = motor_states.get(axis_name)
        if motor is None or not motor.received:
            feedback = '  -       -        -        -       -     -      WAIT'
        else:
            torque = 'ON' if motor.torque_enabled else 'OFF'
            communication = 'OK' if motor.communication_ok else 'ERROR'
            feedback = (
                f'{motor.motor_id:3d}  {motor.goal_position_raw:6d}  '
                f'{motor.present_position_raw:7d}  '
                f'{motor.current_ampere * 1000.0:7.0f}mA '
                f'{motor.temperature_celsius:4.0f}C  '
                f'{torque:6s} {communication}'
            )
        lines.append(
            f'{marker}   {key_label}  {AXIS_LABELS[axis_name]:18s} '
            f'{command_values[axis_name]:.2f}  {feedback}'
        )

    lines.extend(
        [
            '',
            '1-5 Select flexion | Left/Right +/-0.01 | Up/Down +/-0.05 | h Home',
            'm TELEOP | Space STOP | q Quit',
            '',
            f'Status: {status}',
        ]
    )
    return lines


def draw_screen(screen: curses.window, lines: list[str]) -> None:
    """Draw text lines without writing beyond the terminal boundary."""
    screen.erase()
    height, width = screen.getmaxyx()
    required_width = max(len(line) for line in lines)
    if height < len(lines) or width < required_width:
        message = (
            f'Terminal too small: need at least {required_width}x{len(lines)}, '
            f'current {width}x{height}'
        )
        screen.addnstr(0, 0, message, max(1, width - 1))
    else:
        for row, line in enumerate(lines):
            screen.addnstr(row, 0, line, max(1, width - 1))
    screen.refresh()


def apply_preview_key(core: TeleopCore, key: int) -> tuple[bool, str]:
    """Apply a key in UI preview mode and return whether to continue."""
    if key in (ord('q'), ord('Q')):
        return False, 'Quit requested'
    if ord('1') <= key <= ord('5'):
        core.select_key(chr(key))
        return True, f'Selected {core.selected_name}'
    if 0 <= key <= 255 and chr(key).lower() in 'asdf':
        core.set_thumb_pose_key(chr(key))
        return True, f'Thumb pose={core.thumb_pose}'
    if key == curses.KEY_LEFT:
        value = core.adjust_selected(-1.0)
        return True, f'{core.selected_name} target={value:.2f}'
    if key == curses.KEY_RIGHT:
        value = core.adjust_selected(1.0)
        return True, f'{core.selected_name} target={value:.2f}'
    if key == curses.KEY_DOWN:
        value = core.adjust_selected(-0.05 / core.step_size)
        return True, f'{core.selected_name} target={value:.2f}'
    if key == curses.KEY_UP:
        value = core.adjust_selected(0.05 / core.step_size)
        return True, f'{core.selected_name} target={value:.2f}'
    if key in (ord('h'), ord('H')):
        core.set_home()
        return True, 'All logical axes returned to home'
    if key in (ord('m'), ord('M'), ord(' ')):
        return True, 'ROS control is not connected in UI preview mode'
    return True, ''


def run_preview(screen: curses.window) -> None:
    """Run the keyboard UI without publishing ROS commands."""
    curses.curs_set(0)
    screen.keypad(True)
    screen.timeout(50)
    core = TeleopCore()
    system = SystemUiState()
    status = 'UI preview: commands are not being published'
    running = True
    while running:
        lines = build_screen_lines(core, 1.0, system, status=status)
        draw_screen(screen, lines)
        key = screen.getch()
        if key != -1:
            running, new_status = apply_preview_key(core, key)
            if new_status:
                status = new_status


def main() -> None:
    """Run the curses UI preview."""
    curses.wrapper(run_preview)
