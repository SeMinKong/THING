"""Packaging, configuration, and contract tests for command_guard."""

import runpy
from pathlib import Path

import yaml


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_SRC = PACKAGE_ROOT.parent
REPOSITORY_ROOT = WORKSPACE_SRC.parents[1]
CONTROL_CONFIG = WORKSPACE_SRC / 'thing_bringup' / 'config' / 'control.yaml'
BRINGUP_ROOT = WORKSPACE_SRC / 'thing_bringup'
CONTROL_LAUNCH = BRINGUP_ROOT / 'launch' / 'control.launch.py'
INTERFACE_DOC = REPOSITORY_ROOT / 'docs' / 'interfaces.md'


def test_setup_registers_all_control_console_scripts(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        'setuptools.setup',
        lambda **kwargs: captured.update(kwargs),
    )

    runpy.run_path(str(PACKAGE_ROOT / 'setup.py'), run_name='__main__')

    scripts = captured['entry_points']['console_scripts']
    assert 'command_manager = thing_control.command_manager:main' in scripts
    assert 'command_guard = thing_control.command_guard:main' in scripts
    assert 'safety_manager = thing_control.safety_manager:main' in scripts


def test_control_yaml_defines_v6_3_guard_and_state_freshness_limits():
    config = yaml.safe_load(CONTROL_CONFIG.read_text())
    guard = config['command_guard']['ros__parameters']

    assert guard['command_timeout_ms'] == 300
    assert guard['command_hold_ms'] == 5000
    assert guard['command_future_tolerance_ms'] == 100
    assert guard['safety_state_timeout_ms'] == 1500
    assert guard['control_state_timeout_ms'] == 1500
    assert guard['diagnostic_period_ms'] == 1000
    assert 'default_speed_limit' not in guard

    expected_axes = {
        'thumb_flex',
        'thumb_opp',
        'thumb_abd',
        'index_flex',
        'middle_flex',
        'ring_flex',
        'little_flex',
    }
    assert set(guard['axis_limits']) == expected_axes
    for limits in guard['axis_limits'].values():
        assert limits['min'] == 0.0
        assert limits['max'] == 1.0
        assert limits['max_delta_per_second'] > 0.0

    assert set(guard['mimic_axis_limits']) == expected_axes
    for limits in guard['mimic_axis_limits'].values():
        assert limits['max_delta_per_second'] == 10.0

    manager = config['command_manager']['ros__parameters']
    assert manager['state_publish_period_ms'] < guard['control_state_timeout_ms']

    safety = config['safety_manager']['ros__parameters']
    assert safety == {
        'command_hold_ms': 5000,
        'command_safe_ms': 10000,
        'safe_action_timeout_ms': 3000,
        'recovery_stable_ms': 300,
        'recovery_max_gap_ms': 100,
        'reset_min_ms': 500,
        'reset_timeout_ms': 3000,
        'estop_release_ms': 500,
        'fault_clear_stable_ms': 1000,
        'hardware_status_timeout_ms': 300,
        'estop_input_timeout_ms': 300,
        'tick_period_ms': 20,
        'state_publish_period_ms': 100,
        'trip_limits_validated': True,
        'max_current_ampere': 1.47,
        'max_temperature_celsius': 70.0,
    }


def test_bringup_installs_control_launch(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        'setuptools.setup',
        lambda **kwargs: captured.update(kwargs),
    )
    monkeypatch.chdir(BRINGUP_ROOT)
    runpy.run_path(str(BRINGUP_ROOT / 'setup.py'), run_name='__main__')

    launch_entries = [
        (destination, files)
        for destination, files in captured['data_files']
        if destination.endswith('/launch')
    ]
    assert launch_entries
    assert any(
        str(file_path).endswith('control.launch.py')
        for _, files in launch_entries
        for file_path in files
    )


def test_control_launch_starts_all_control_nodes_with_shared_config():
    launch_source = CONTROL_LAUNCH.read_text()
    assert "FindPackageShare('thing_bringup')" in launch_source
    assert launch_source.count('parameters=[control_config]') == 5
    assert "package='thing_hardware'" in launch_source
    assert "executable='estop_gpio_node'" in launch_source
    for executable in (
        'safety_manager',
        'command_manager',
        'manual_executor',
        'command_guard',
    ):
        assert "package='thing_control'" in launch_source
        assert f"executable='{executable}'" in launch_source


def test_package_declares_runtime_diagnostic_dependency():
    package_xml = (PACKAGE_ROOT / 'package.xml').read_text()
    assert '<exec_depend>diagnostic_msgs</exec_depend>' in package_xml


def test_interface_document_lists_guard_rejections_and_hardware_boundary():
    document = INTERFACE_DOC.read_text()
    for reason in (
        'safety_state_missing',
        'safety_state_stale',
        'control_state_missing',
        'control_state_stale',
        'control_activation_not_observed',
        'source_mode_mismatch',
        'command_stale',
        'command_from_future',
        'axis_non_finite',
        'axis_out_of_range',
        'speed_limit_out_of_range',
        'confidence_out_of_range',
        'sequence_duplicate',
        'sequence_out_of_order',
        'axis_rate_exceeded',
    ):
        assert f'`{reason}`' in document

    assert 'command_guard는 HandCommand에 없는 전류' in document
    assert 'thing_hardware와 safety_manager' in document
    assert 'ros2 launch thing_bringup control.launch.py' in document
    assert 'diagnostic_period_ms' in document
    assert '1Hz보다 느려지도록 설정할 수 없습니다' in document
    assert '분산 STOP/FAULT race' in document
