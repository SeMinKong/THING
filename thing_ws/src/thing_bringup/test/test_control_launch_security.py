"""Tests for the fail-closed SROS2 control launch gate."""

import importlib.util
from pathlib import Path

import pytest


LAUNCH_PATH = (
    Path(__file__).resolve().parents[1]
    / 'launch'
    / 'control.launch.py'
)


def load_launch_module():
    """Load the launch file as a normal Python module."""
    spec = importlib.util.spec_from_file_location(
        'thing_control_launch_security_test',
        LAUNCH_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_control_launch_rejects_insecure_environment(monkeypatch):
    """Production control launch must not silently join an insecure domain."""
    module = load_launch_module()
    monkeypatch.delenv('ROS_SECURITY_ENABLE', raising=False)
    monkeypatch.delenv('ROS_SECURITY_STRATEGY', raising=False)
    monkeypatch.delenv('ROS_SECURITY_KEYSTORE', raising=False)

    with pytest.raises(RuntimeError, match='ROS_SECURITY_ENABLE'):
        module.generate_launch_description()


def test_control_launch_requires_every_enclave(monkeypatch, tmp_path):
    """A partial keystore must fail before any control node starts."""
    module = load_launch_module()
    monkeypatch.setenv('ROS_SECURITY_ENABLE', 'true')
    monkeypatch.setenv('ROS_SECURITY_STRATEGY', 'Enforce')
    monkeypatch.setenv('ROS_SECURITY_KEYSTORE', str(tmp_path))
    first = module._ENCLAVES[0]
    (tmp_path / 'enclaves' / first.lstrip('/')).mkdir(parents=True)

    with pytest.raises(RuntimeError, match='missing SROS2 enclave artifacts'):
        module.generate_launch_description()


def test_control_launch_accepts_complete_enclave_set(monkeypatch, tmp_path):
    """A complete enforced keystore produces all four control actions."""
    module = load_launch_module()
    monkeypatch.setenv('ROS_SECURITY_ENABLE', 'true')
    monkeypatch.setenv('ROS_SECURITY_STRATEGY', 'Enforce')
    monkeypatch.setenv('ROS_SECURITY_KEYSTORE', str(tmp_path))
    for enclave in module._ENCLAVES:
        enclave_directory = tmp_path / 'enclaves' / enclave.lstrip('/')
        enclave_directory.mkdir(parents=True)
        for name in module._REQUIRED_ENCLAVE_FILES:
            (enclave_directory / name).write_text('test-artifact')

    description = module.generate_launch_description()

    assert len(description.entities) == 4


def test_control_launch_rejects_empty_enclave_directories(
    monkeypatch,
    tmp_path,
):
    module = load_launch_module()
    monkeypatch.setenv('ROS_SECURITY_ENABLE', 'true')
    monkeypatch.setenv('ROS_SECURITY_STRATEGY', 'Enforce')
    monkeypatch.setenv('ROS_SECURITY_KEYSTORE', str(tmp_path))
    for enclave in module._ENCLAVES:
        (tmp_path / 'enclaves' / enclave.lstrip('/')).mkdir(parents=True)

    with pytest.raises(RuntimeError, match='missing SROS2 enclave artifacts'):
        module.generate_launch_description()
