"""Launch the Jetson vision pipeline."""

from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    """Start the vision processing chain with one parameter file."""
    vision_config = PathJoinSubstitution(
        [FindPackageShare('thing_bringup'), 'config', 'vision.yaml']
    )

    return LaunchDescription([
        Node(
            package='thing_vision',
            executable='camera_node',
            name='camera_node',
            parameters=[vision_config],
            output='screen',
        ),
        Node(
            package='thing_vision',
            executable='world_mediapipe_node',
            name='mediapipe_node',
            parameters=[vision_config],
            output='screen',
        ),
        Node(
            package='thing_vision',
            executable='hand_target_node',
            name='hand_target_node',
            parameters=[vision_config],
            remappings=[
                ('/thing/landmarks', '/thing/world_landmarks'),
            ],
            output='screen',
        ),
    ])
