"""Tests for keyboard teleoperation state management."""

import pytest

from thing_teleop.teleop_core import AXIS_NAMES, THUMB_POSES, TeleopCore, clamp


def test_axis_order_matches_keyboard_layout():
    assert AXIS_NAMES == (
        'thumb_flex',
        'index_flex',
        'middle_flex',
        'ring_flex',
        'little_flex',
    )


def test_select_key_maps_one_through_five():
    core = TeleopCore()

    for key, name in zip('12345', AXIS_NAMES):
        assert core.select_key(key)
        assert core.selected_name == name

    assert not core.select_key('8')
    assert core.selected_name == 'little_flex'


def test_adjust_selected_uses_step_and_clamps():
    core = TeleopCore(step_size=0.05)
    core.select_key('2')

    assert core.adjust_selected() == pytest.approx(0.05)
    assert core.adjust_selected(4.0) == pytest.approx(0.25)
    assert core.adjust_selected(-10.0) == pytest.approx(0.0)
    assert core.adjust_selected(100.0) == pytest.approx(1.0)


def test_home_resets_every_axis_and_selects_neutral_thumb():
    core = TeleopCore(
        thumb_pose='folded',
        targets=[0.1, 0.2, 0.3, 0.4, 0.5],
    )

    core.set_home()

    assert core.targets == [0.0] * 5
    assert core.thumb_pose == 'neutral'


def test_command_values_contains_thumb_pose_and_flexion_axes():
    core = TeleopCore(thumb_pose='grasp', targets=[0.1] * 5)

    values = core.command_values()
    assert values['thumb_opp'] == THUMB_POSES['grasp'][0]
    assert values['thumb_abd'] == THUMB_POSES['grasp'][1]
    assert values['thumb_flex'] == pytest.approx(0.1)


@pytest.mark.parametrize(
    'key, pose',
    [('a', 'open'), ('s', 'neutral'), ('d', 'grasp'), ('f', 'folded')],
)
def test_thumb_pose_shortcuts_set_validated_pairs(key, pose):
    core = TeleopCore()

    assert core.set_thumb_pose_key(key)
    assert core.thumb_pose == pose
    values = core.command_values()
    assert (values['thumb_opp'], values['thumb_abd']) == THUMB_POSES[pose]


@pytest.mark.parametrize('value, expected', [(-1.0, 0.0), (0.4, 0.4), (2.0, 1.0)])
def test_clamp(value, expected):
    assert clamp(value) == pytest.approx(expected)


@pytest.mark.parametrize('step_size', [0.0, -0.1, 1.1])
def test_invalid_step_size_is_rejected(step_size):
    with pytest.raises(ValueError):
        TeleopCore(step_size=step_size)
