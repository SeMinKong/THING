"""Static wiring contract for the unified Manual Executor."""

import ast
from pathlib import Path

import yaml


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PACKAGE_ROOT.parent
BRINGUP_ROOT = SRC_ROOT / 'thing_bringup'


def test_setup_exposes_only_the_unified_manual_executor_entrypoint():
    setup = (PACKAGE_ROOT / 'setup.py').read_text()

    assert "manual_executor = thing_control.manual_executor:main" in setup
    assert 'gesture_executor =' not in setup
    assert 'sequence_executor =' not in setup


def test_manual_executor_has_both_request_interfaces_and_one_output_topic():
    source = (
        PACKAGE_ROOT / 'thing_control' / 'manual_executor.py'
    ).read_text()

    for contract in (
        "'manual_executor'",
        "'/thing/execute_gesture'",
        "'/thing/execute_sequence'",
        "'/thing/command/manual'",
        "'/thing/control/motion_active'",
        "'/thing/control/stop_requested'",
        "'/thing/control_state'",
        "'/thing/safety_state'",
    ):
        assert contract in source

    assert source.count("'/thing/command/manual'") == 1


def test_no_second_python_node_publishes_the_manual_command_topic():
    publishers = []
    for path in (PACKAGE_ROOT / 'thing_control').glob('*.py'):
        tree = ast.parse(path.read_text())
        for call in (
            node for node in ast.walk(tree) if isinstance(node, ast.Call)
        ):
            if not (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == 'create_publisher'
            ):
                continue
            topics = {
                argument.value
                for argument in call.args
                if isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
            }
            if '/thing/command/manual' in topics:
                publishers.append(path.name)

    assert publishers == ['manual_executor.py']


def test_control_launch_starts_manual_executor():
    launch = (BRINGUP_ROOT / 'launch' / 'control.launch.py').read_text()

    assert "executable='manual_executor'" in launch
    assert "name='manual_executor'" in launch
    assert '--enclave' not in launch


def test_control_yaml_has_seven_axis_presets_and_bounded_20hz_period():
    config = yaml.safe_load(
        (BRINGUP_ROOT / 'config' / 'control.yaml').read_text()
    )
    parameters = config['manual_executor']['ros__parameters']

    assert 0 < parameters['publish_period_ms'] <= 50
    for name in ('open', 'fist', 'pinch', 'cylindrical_grasp'):
        axes = parameters[f'gestures.{name}.axes']
        assert len(axes) == 7
        assert all(0.0 <= float(value) <= 1.0 for value in axes)
        assert parameters[f'gestures.{name}.duration_ms'] > 0
    for name in ('countdown', 'scissors_rock_paper'):
        steps = parameters[f'sequences.{name}.steps']
        durations = parameters[f'sequences.{name}.step_durations_ms']
        assert steps
        assert len(steps) == len(durations)
        assert all(duration > 0 for duration in durations)
