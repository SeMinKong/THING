"""Wiring tests for the robot-hand hardware bringup launch."""

import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
import yaml
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LAUNCH_PATH = PACKAGE_ROOT / 'launch' / 'thing_bringup.launch.py'
MOTORS_PATH = PACKAGE_ROOT / 'config' / 'motors.yaml'
CONTROL_PATH = PACKAGE_ROOT / 'config' / 'control.yaml'
POLICY_PATH = (
    PACKAGE_ROOT.parent
    / 'thing_control'
    / 'security'
    / 'thing_control.policy.xml'
)


def load_launch_module():
    """Load the launch file as a normal Python module."""
    spec = importlib.util.spec_from_file_location(
        'thing_bringup_launch_test',
        LAUNCH_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configure_complete_hardware_enclave(module, monkeypatch, tmp_path):
    """Create non-empty placeholder artifacts for launch wiring tests."""
    monkeypatch.setenv('ROS_SECURITY_ENABLE', 'true')
    monkeypatch.setenv('ROS_SECURITY_STRATEGY', 'Enforce')
    monkeypatch.setenv('ROS_SECURITY_KEYSTORE', str(tmp_path))
    monkeypatch.delenv('ROS_SECURITY_ENCLAVE_OVERRIDE', raising=False)
    enclave_directory = (
        tmp_path
        / 'enclaves'
        / module._HARDWARE_ENCLAVE.lstrip('/')
    )
    enclave_directory.mkdir(parents=True)
    for name in module._REQUIRED_ENCLAVE_FILES:
        (enclave_directory / name).write_text('test-artifact')


def test_thing_bringup_rejects_insecure_environment(monkeypatch):
    """The hardware launch must not silently join an insecure DDS domain."""
    module = load_launch_module()
    monkeypatch.delenv('ROS_SECURITY_ENABLE', raising=False)
    monkeypatch.delenv('ROS_SECURITY_STRATEGY', raising=False)
    monkeypatch.delenv('ROS_SECURITY_KEYSTORE', raising=False)

    with pytest.raises(RuntimeError, match='ROS_SECURITY_ENABLE'):
        module.generate_launch_description()


def test_thing_bringup_requires_exact_lowercase_security_enable(
    monkeypatch,
    tmp_path,
):
    """Values rejected by Humble rcl must also be rejected by preflight."""
    module = load_launch_module()
    configure_complete_hardware_enclave(module, monkeypatch, tmp_path)
    for value in ('TRUE', 'True', '1'):
        monkeypatch.setenv('ROS_SECURITY_ENABLE', value)
        with pytest.raises(RuntimeError, match='ROS_SECURITY_ENABLE'):
            module.generate_launch_description()


def test_thing_bringup_rejects_mismatched_enclave_override(
    monkeypatch,
    tmp_path,
):
    """An environment override must not replace the reviewed hardware identity."""
    module = load_launch_module()
    configure_complete_hardware_enclave(module, monkeypatch, tmp_path)
    monkeypatch.setenv(
        'ROS_SECURITY_ENCLAVE_OVERRIDE',
        '/thing/hardware/unreviewed_driver',
    )

    with pytest.raises(RuntimeError, match='ROS_SECURITY_ENCLAVE_OVERRIDE'):
        module.generate_launch_description()


def test_thing_bringup_requires_complete_hardware_enclave(
    monkeypatch,
    tmp_path,
):
    """An empty hardware enclave directory must fail before starting a motor."""
    module = load_launch_module()
    monkeypatch.setenv('ROS_SECURITY_ENABLE', 'true')
    monkeypatch.setenv('ROS_SECURITY_STRATEGY', 'Enforce')
    monkeypatch.setenv('ROS_SECURITY_KEYSTORE', str(tmp_path))
    (
        tmp_path
        / 'enclaves'
        / module._HARDWARE_ENCLAVE.lstrip('/')
    ).mkdir(parents=True)

    with pytest.raises(RuntimeError, match='missing SROS2 enclave artifacts'):
        module.generate_launch_description()


def test_thing_bringup_starts_motor_driver_with_config_argument(
    monkeypatch,
    tmp_path,
):
    """A complete enclave starts one driver with config and enclave wiring."""
    module = load_launch_module()
    configure_complete_hardware_enclave(module, monkeypatch, tmp_path)

    description = module.generate_launch_description()

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
    assert nodes[0]._Node__ros_arguments == [
        '--enclave',
        module._HARDWARE_ENCLAVE,
    ]


def test_hardware_policy_is_least_privilege_for_motor_driver():
    """The motor enclave may use only its documented ROS graph contract."""
    root = ET.parse(POLICY_PATH).getroot()
    enclave = root.find(
        ".//enclave[@path='/thing/hardware/motor_driver']"
    )
    assert enclave is not None
    profile = enclave.find("./profiles/profile[@node='motor_driver_node']")
    assert profile is not None

    subscribed = {
        topic.text
        for topic in profile.findall("./topics[@subscribe='ALLOW']/topic")
    }
    published = {
        topic.text
        for topic in profile.findall("./topics[@publish='ALLOW']/topic")
    }
    assert subscribed == {
        'ros_discovery_info',
        '/thing/command',
        '/thing/safety_state',
    }
    assert published == {
        'ros_discovery_info',
        '/thing/motor_status',
        '/thing/diagnostics',
        'parameter_events',
        'rosout',
    }


def test_controlled_motors_use_open_home_as_safe_start_reference():
    """Only calibrated IDs may use the measured open-hand home targets."""
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
    unsupported_parameters = {
        'model',
        'encoder_resolution_raw',
        'current_limit_ma',
        'max_consecutive_read_failures',
        'bus_failure_timeout_ms',
    }

    assert motor_ids == sorted(set(motor_ids))
    assert len(motor_ids) == 7
    assert controlled_ids == {1, 3, 4, 7}
    assert all(len(parameters[field]) == len(motor_ids) for field in axis_fields)
    assert unsupported_parameters.isdisjoint(parameters)
    assert parameters['operating_mode'] == 5
    assert parameters['integration_test_mode'] is True
    assert 0.0 < parameters['safe_velocity_limit'] <= 1.0

    rows = zip(
        motor_ids,
        parameters['home_positions_raw'],
        parameters['closed_positions_raw'],
        parameters['safe_positions_raw'],
        parameters['position_tolerances_raw'],
    )
    for motor_id, home, closed, safe, tolerance in rows:
        if motor_id in controlled_ids:
            assert 0 <= home <= 4095
            assert 0 <= closed <= 4095
            assert 0 <= safe <= 4095
            assert safe == home
            assert tolerance >= 0
        else:
            assert home == -1
            assert closed == -1
            assert safe == -1
            assert tolerance == -1


def test_open_gesture_is_the_normalized_home_pose():
    """The normal command path must map the named open pose to every home endpoint."""
    parameters = yaml.safe_load(CONTROL_PATH.read_text())[
        'manual_executor'
    ]['ros__parameters']

    assert parameters['gestures.open.axes'] == [0.0] * 7
