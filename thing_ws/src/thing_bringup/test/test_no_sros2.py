"""Regression contract for running the project without mandatory SROS2."""

import importlib.util
from pathlib import Path
from runpy import run_path

from launch_ros.actions import Node


BRINGUP_ROOT = Path(__file__).resolve().parents[1]
CONTROL_ROOT = BRINGUP_ROOT.parent / 'thing_control'
SECURITY_ENVIRONMENT = (
    'ROS_SECURITY_ENABLE',
    'ROS_SECURITY_STRATEGY',
    'ROS_SECURITY_KEYSTORE',
    'ROS_SECURITY_ENCLAVE_OVERRIDE',
)


def load_launch(path, module_name):
    """Load a launch file as a normal Python module."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launches_start_without_sros2_and_keep_node_wiring(monkeypatch):
    """Removing SROS2 must not remove or rename any production node."""
    for name in SECURITY_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)

    control = load_launch(
        BRINGUP_ROOT / 'launch' / 'control.launch.py',
        'control_launch_without_sros2',
    ).generate_launch_description()
    hardware = load_launch(
        BRINGUP_ROOT / 'launch' / 'thing_bringup.launch.py',
        'hardware_launch_without_sros2',
    ).generate_launch_description()

    control_nodes = [entity for entity in control.entities if isinstance(entity, Node)]
    hardware_nodes = [entity for entity in hardware.entities if isinstance(entity, Node)]

    assert [node.node_executable for node in control_nodes] == [
        'estop_gpio_node',
        'safety_manager',
        'command_manager',
        'manual_executor',
        'command_guard',
    ]
    assert [node.node_executable for node in hardware_nodes] == [
        'motor_driver_node',
    ]
    assert all(not node._Node__ros_arguments for node in control_nodes)
    assert not hardware_nodes[0]._Node__ros_arguments
    assert all(node._Node__parameters for node in control_nodes + hardware_nodes)


def test_control_package_does_not_install_an_sros2_policy(monkeypatch):
    """The package must not ship a dormant policy after SROS2 removal."""
    captured = {}

    monkeypatch.setattr('setuptools.setup', lambda **kwargs: captured.update(kwargs))
    run_path(str(CONTROL_ROOT / 'setup.py'), run_name='__main__')

    installed_paths = [destination for destination, _ in captured['data_files']]
    assert not (CONTROL_ROOT / 'security' / 'thing_control.policy.xml').exists()
    assert all('security' not in destination for destination in installed_paths)
