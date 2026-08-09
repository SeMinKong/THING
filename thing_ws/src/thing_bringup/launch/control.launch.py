"""Launch the safety, arbitration, and final command validation nodes."""

from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    """Start the control chain with one version-controlled parameter file."""
    control_config = PathJoinSubstitution(
        [FindPackageShare('thing_bringup'), 'config', 'control.yaml']
    )

    return LaunchDescription([
        Node(
            package='thing_hardware',
            executable='estop_gpio_node',
            name='estop_gpio_node',
            parameters=[control_config],
            output='screen',
        ),
        Node(
            package='thing_control',
            executable='safety_manager',
            name='safety_manager',
            parameters=[control_config],
            output='screen',
        ),
        Node(
            package='thing_control',
            executable='command_manager',
            name='command_manager',
            parameters=[control_config],
            output='screen',
        ),
        Node(
            package='thing_control',
            executable='manual_executor',
            name='manual_executor',
            parameters=[control_config],
            output='screen',
        ),
        Node(
            package='thing_control',
            executable='command_guard',
            name='command_guard',
            parameters=[control_config],
            output='screen',
        ),
    ])
