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
