"""Static wiring contract for the unified Manual Executor."""

import ast
from pathlib import Path
from xml.etree import ElementTree

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


def test_control_launch_starts_manual_executor_in_a_dedicated_enclave():
    launch = (BRINGUP_ROOT / 'launch' / 'control.launch.py').read_text()

    assert "'/thing/control/manual_executor'" in launch
    assert "executable='manual_executor'" in launch
    assert "name='manual_executor'" in launch


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


def test_security_policy_grants_manual_executor_least_privilege_contract():
    policy = ElementTree.parse(
        PACKAGE_ROOT / 'security' / 'thing_control.policy.xml'
    ).getroot()
    enclave = policy.find(
        ".//enclave[@path='/thing/control/manual_executor']"
    )
    assert enclave is not None
    profile = enclave.find(".//profile[@node='manual_executor']")
    assert profile is not None

    published = {
        topic.text
        for topic in profile.findall("topics[@publish='ALLOW']/topic")
    }
    subscribed = {
        topic.text
        for topic in profile.findall("topics[@subscribe='ALLOW']/topic")
    }
    replied_services = {
        service.text
        for service in profile.findall("services[@reply='ALLOW']/service")
    }

    assert published == {
        'ros_discovery_info',
        '/thing/command/manual',
        '/thing/control/motion_active',
        '/thing/execute_sequence/_action/feedback',
        '/thing/execute_sequence/_action/status',
        'parameter_events',
        'rosout',
    }
    assert subscribed == {
        'ros_discovery_info',
        '/thing/control/stop_barrier_ack',
        '/thing/control/stop_requested',
        '/thing/control_state',
        '/thing/safety_state',
    }
    assert replied_services == {
        '/thing/execute_gesture',
        '/thing/execute_sequence/_action/send_goal',
        '/thing/execute_sequence/_action/get_result',
        '/thing/execute_sequence/_action/cancel_goal',
        '~/describe_parameters',
        '~/get_parameter_types',
        '~/get_parameters',
        '~/list_parameters',
        '~/set_parameters',
        '~/set_parameters_atomically',
    }


def test_manual_command_topic_has_exactly_one_policy_publisher():
    policy = ElementTree.parse(
        PACKAGE_ROOT / 'security' / 'thing_control.policy.xml'
    ).getroot()
    publishers = {
        profile.attrib['node']
        for profile in policy.findall('.//profile')
        if any(
            topic.text == '/thing/command/manual'
            for topic in profile.findall("topics[@publish='ALLOW']/topic")
        )
    }
    assert publishers == {'manual_executor'}
