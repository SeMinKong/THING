"""Packaging and YAML wiring tests for command manager."""

from pathlib import Path
from runpy import run_path
from xml.etree import ElementTree

import yaml


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_SRC = PACKAGE_ROOT.parent


def test_package_declares_python_yaml_test_dependency():
    package = ElementTree.parse(PACKAGE_ROOT / "package.xml").getroot()

    assert any(
        dependency.text == "python3-yaml"
        for dependency in package.findall("test_depend")
    )


def test_package_declares_std_msgs_runtime_dependency():
    package = ElementTree.parse(PACKAGE_ROOT / 'package.xml').getroot()
    dependencies = package.findall('depend') + package.findall('exec_depend')

    assert any(dependency.text == 'std_msgs' for dependency in dependencies)


def test_setup_registers_command_manager_console_script(monkeypatch):
    captured = {}

    def capture_setup(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr('setuptools.setup', capture_setup)
    run_path(str(PACKAGE_ROOT / 'setup.py'), run_name='__main__')

    console_scripts = captured['entry_points']['console_scripts']
    assert 'command_manager = thing_control.command_manager:main' in console_scripts
    assert 'safety_manager = thing_control.safety_manager:main' in console_scripts


def test_control_yaml_uses_v6_3_owner_lease_defaults():
    config_path = (
        WORKSPACE_SRC
        / 'thing_bringup'
        / 'config'
        / 'control.yaml'
    )
    config = yaml.safe_load(config_path.read_text())
    parameters = config['command_manager']['ros__parameters']

    assert parameters == {
        'owner_lease_timeout_ms': 3000,
        'stop_reacquire_delay_ms': 500,
        'lease_check_period_ms': 100,
        'state_publish_period_ms': 1000,
    }

    safety_parameters = config['safety_manager']['ros__parameters']
    assert safety_parameters['safe_action_timeout_ms'] == 3000
    assert safety_parameters['reset_min_ms'] == 500
    assert safety_parameters['reset_timeout_ms'] == 3000


def test_security_policy_grants_v6_5_stop_and_lease_paths():
    policy = ElementTree.parse(
        PACKAGE_ROOT / 'security' / 'thing_control.policy.xml'
    ).getroot()
    profiles = {
        profile.attrib['node']: profile
        for profile in policy.findall('.//profile')
    }

    def topic_names(node_name, direction):
        topics = profiles[node_name].find(f"topics[@{direction}='ALLOW']")
        assert topics is not None
        return {topic.text for topic in topics.findall('topic')}

    manager_subscriptions = topic_names('command_manager', 'subscribe')
    guard_publications = topic_names('command_guard', 'publish')
    safety_subscriptions = topic_names('safety_manager', 'subscribe')

    assert '/thing/control/stop_barrier_ack' in manager_subscriptions
    assert '/thing/control/stop_barrier_ack' in guard_publications
    assert '/thing/control/stop_barrier_ack' in safety_subscriptions
    assert '/thing/control_state' in safety_subscriptions


def test_security_policy_protected_topics_have_exact_authoritative_publishers():
    policy = ElementTree.parse(
        PACKAGE_ROOT / 'security' / 'thing_control.policy.xml'
    ).getroot()
    protected_publishers = {
        '/thing/command/selected': {'command_manager'},
        '/thing/command': {'command_guard'},
        '/thing/command/validation_result': {'command_guard'},
        '/thing/control/stop_barrier_ack': {'command_guard'},
    }
    publishers = {topic: set() for topic in protected_publishers}

    for profile in policy.findall('.//profile'):
        topics = profile.find("topics[@publish='ALLOW']")
        if topics is None:
            continue
        granted = {
            topic.text
            for topic in topics.findall('topic')
            if topic.text is not None
        }
        assert all('*' not in topic for topic in granted)
        for protected_topic in protected_publishers.keys() & granted:
            publishers[protected_topic].add(profile.attrib['node'])

    assert publishers == protected_publishers


def test_interfaces_document_control_arbitration_contract():
    repository_root = PACKAGE_ROOT.parents[2]
    interfaces = (repository_root / 'docs' / 'interfaces.md').read_text()

    for contract in (
        '`MODE_MIMIC` + `OWNER_WEB`',
        '`MODE_MANUAL` + `OWNER_WEB`',
        '`MODE_TELEOP` + `OWNER_LOCAL`',
        '`/thing/control/stop_requested`',
        '`/thing/control/motion_active`',
        '`invalid_mode`',
        '`motion_active`',
        '`stop_in_progress`',
    ):
        assert contract in interfaces
