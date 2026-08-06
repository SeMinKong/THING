"""Wiring tests for the robot-hand hardware bringup launch."""

import importlib.util
from pathlib import Path

import yaml
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LAUNCH_PATH = PACKAGE_ROOT / 'launch' / 'thing_bringup.launch.py'
MOTORS_PATH = PACKAGE_ROOT / 'config' / 'motors.yaml'
CONTROL_PATH = PACKAGE_ROOT / 'config' / 'control.yaml'


def load_launch_module():
    """Load the launch file as a normal Python module."""
    spec = importlib.util.spec_from_file_location(
        'thing_bringup_launch_test',
        LAUNCH_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_thing_bringup_starts_motor_driver_with_config_argument():
    """The launch keeps the motor executable and parameter wiring unchanged."""
    description = load_launch_module().generate_launch_description()

    arguments = [
        entity
        for entity in description.entities
        if isinstance(entity, DeclareLaunchArgument)
    ]
    nodes = [
        entity
        for entity in description.entities
        if isinstance(entity, Node)
    ]
    assert [argument.name for argument in arguments] == ['motors_config']
    assert len(nodes) == 1
    assert nodes[0].node_package == 'thing_hardware'
    assert nodes[0].node_executable == 'motor_driver_node'
    assert nodes[0]._Node__parameters
    assert not nodes[0]._Node__ros_arguments


def test_integrated_motors_use_calibrated_safe_start_references():
    """All seven calibrated axes use measured endpoints and safe targets."""
    parameters = yaml.safe_load(MOTORS_PATH.read_text())[
        'motor_driver_node'
    ]['ros__parameters']
    motor_ids = parameters['motor_ids']
    controlled_ids = set(parameters['controlled_motor_ids'])
    axis_fields = (
        'actuator_names',
        'bus_indices',
        'home_positions_raw',
        'closed_positions_raw',
        'safe_positions_raw',
        'position_tolerances_raw',
        'position_p_gains',
        'position_i_gains',
        'position_d_gains',
        'goal_currents_ma',
        'profile_accelerations_raw',
        'profile_velocities_raw',
    )
    assert motor_ids == sorted(set(motor_ids))
    assert len(motor_ids) == 7
    assert controlled_ids == set(motor_ids)
    assert all(len(parameters[field]) == len(motor_ids) for field in axis_fields)
    assert parameters['operating_mode'] == 5
    assert parameters['integration_test_mode'] is False
    assert 0.0 < parameters['safe_velocity_limit'] <= 1.0
    assert 0.0 < parameters['safe_motion_timeout_seconds'] <= 3.0

    rows = zip(
        motor_ids,
        parameters['home_positions_raw'],
        parameters['closed_positions_raw'],
        parameters['safe_positions_raw'],
        parameters['position_tolerances_raw'],
    )
    for motor_id, home, closed, safe, tolerance in rows:
        assert motor_id in controlled_ids
        assert home >= 0
        assert closed >= 0
        assert safe >= 0
        assert safe == home
        assert tolerance >= 0


def test_thumb_functional_pose_arrays_share_one_valid_shape():
    """Thumb pose maps and reversal matrices stay index-compatible."""
    parameters = yaml.safe_load(MOTORS_PATH.read_text())[
        'motor_driver_node'
    ]['ros__parameters']
    pose_count = len(parameters['thumb_functional_pose_names'])
    pose_fields = (
        'thumb_functional_opposition',
        'thumb_functional_abduction',
        'thumb_opposition_positions_raw',
        'thumb_opposition_approach_directions',
        'thumb_opposition_approach_start_positions_raw',
        'thumb_abduction_positions_raw',
        'thumb_abduction_approach_directions',
        'thumb_abduction_approach_start_positions_raw',
        'thumb_flex_home_positions_raw',
        'thumb_flex_closed_positions_raw',
    )

    assert parameters['thumb_functional_pose_names'] == [
        'neutral', 'open', 'grasp', 'folded'
    ]
    assert all(len(parameters[field]) == pose_count for field in pose_fields)
    assert len(parameters['thumb_opposition_reversal_positions_raw']) == (
        pose_count * pose_count
    )
    assert len(parameters['thumb_abduction_reversal_positions_raw']) == (
        pose_count * pose_count
    )


def test_open_gesture_is_the_normalized_home_pose():
    """The normal command path must map the named open pose to every home endpoint."""
    parameters = yaml.safe_load(CONTROL_PATH.read_text())[
        'manual_executor'
    ]['ros__parameters']

    assert parameters['gestures.open.axes'] == [0.0] * 7
