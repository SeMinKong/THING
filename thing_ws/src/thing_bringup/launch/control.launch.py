"""Launch the safety, arbitration, and final command validation nodes."""

import os
from pathlib import Path

from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


_ENCLAVES = (
    '/thing/control/safety_manager',
    '/thing/control/command_manager',
    '/thing/control/command_guard',
)
_REQUIRED_ENCLAVE_FILES = (
    'cert.pem',
    'governance.p7s',
    'identity_ca.cert.pem',
    'key.pem',
    'permissions.p7s',
    'permissions.xml',
    'permissions_ca.cert.pem',
)


def _enclave_artifacts_complete(enclave_directory: Path) -> bool:
    return all(
        (enclave_directory / name).is_file()
        and (enclave_directory / name).stat().st_size > 0
        for name in _REQUIRED_ENCLAVE_FILES
    )


def _require_enforced_sros2() -> None:
    """Reject production launch unless SROS2 deny-by-default is configured."""
    if os.environ.get('ROS_SECURITY_ENABLE', '').lower() != 'true':
        raise RuntimeError('ROS_SECURITY_ENABLE=true is required')
    if os.environ.get('ROS_SECURITY_STRATEGY') != 'Enforce':
        raise RuntimeError('ROS_SECURITY_STRATEGY=Enforce is required')
    keystore_value = os.environ.get('ROS_SECURITY_KEYSTORE', '')
    if not keystore_value:
        raise RuntimeError('ROS_SECURITY_KEYSTORE is required')
    keystore = Path(keystore_value)
    missing = [
        enclave
        for enclave in _ENCLAVES
        if not _enclave_artifacts_complete(
            keystore / 'enclaves' / enclave.lstrip('/')
        )
    ]
    if missing:
        raise RuntimeError(
            'missing SROS2 enclave artifacts: ' + ', '.join(missing)
        )


def generate_launch_description():
    """Start the control chain with one version-controlled parameter file."""
    _require_enforced_sros2()
    control_config = PathJoinSubstitution(
        [FindPackageShare('thing_bringup'), 'config', 'control.yaml']
    )

    return LaunchDescription([
        Node(
            package='thing_control',
            executable='safety_manager',
            name='safety_manager',
            ros_arguments=['--enclave', _ENCLAVES[0]],
            parameters=[control_config],
            output='screen',
        ),
        Node(
            package='thing_control',
            executable='command_manager',
            name='command_manager',
            ros_arguments=['--enclave', _ENCLAVES[1]],
            parameters=[control_config],
            output='screen',
        ),
        Node(
            package='thing_control',
            executable='command_guard',
            name='command_guard',
            ros_arguments=['--enclave', _ENCLAVES[2]],
            parameters=[control_config],
            output='screen',
        ),
    ])
