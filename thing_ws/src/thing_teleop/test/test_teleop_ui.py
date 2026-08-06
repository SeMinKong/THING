"""Tests for keyboard teleoperation screen composition."""

import curses

import pytest

from thing_teleop.teleop_core import TeleopCore
from thing_teleop.teleop_ui import (
    MotorUiState,
    SystemUiState,
    apply_preview_key,
    build_screen_lines,
)


def test_screen_contains_all_axes_and_system_state():
    core = TeleopCore()
    system = SystemUiState(
        control='TELEOP',
        owner='LOCAL',
        owner_alive=True,
        safety='RUN',
        armed=True,
        bus_communication_ok=True,
        publish_rate_hz=20.0,
    )
    motors = {
        'thumb_opp': MotorUiState(
            motor_id=6,
            goal_position_raw=1650,
            present_position_raw=1642,
            current_ampere=0.12,
            temperature_celsius=27.0,
            torque_enabled=True,
            communication_ok=True,
            received=True,
        )
    }

    output = '\n'.join(build_screen_lines(core, 1.0, system, motors))

    assert 'Control: TELEOP / LOCAL / alive' in output
    assert 'Safety: RUN' in output
    assert 'thumb opposition' in output
    assert 'middle flexion' in output
    assert 'Thumb pose: NEUTRAL' in output
    assert '  6    1650     1642' in output
    assert '120mA' in output


def test_preview_keys_update_core_without_ros():
    core = TeleopCore()

    running, _ = apply_preview_key(core, ord('3'))
    assert running
    assert core.selected_name == 'middle_flex'

    apply_preview_key(core, curses.KEY_RIGHT)
    apply_preview_key(core, curses.KEY_UP)
    assert core.selected_value == pytest.approx(0.06)

    apply_preview_key(core, ord('h'))
    assert core.targets == [0.0] * 5

    apply_preview_key(core, ord('f'))
    assert core.thumb_pose == 'folded'

    running, _ = apply_preview_key(core, ord('q'))
    assert not running
