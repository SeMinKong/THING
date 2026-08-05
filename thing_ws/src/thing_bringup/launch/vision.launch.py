"""Launch the Jetson vision pipeline and browser-facing transports."""

from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    """Start vision processing, MJPEG streaming, and the Web Bridge."""
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
            executable='mediapipe_node',
            name='mediapipe_node',
            parameters=[vision_config],
            output='screen',
        ),
        Node(
            package='thing_vision',
            executable='hand_target_node',
            name='hand_target_node',
            parameters=[vision_config],
            output='screen',
        ),
        Node(
            package='thing_web_bridge',
            executable='mjpeg_streamer',
            name='mjpeg_streamer',
            parameters=[vision_config],
            output='screen',
        ),
        Node(
            package='thing_web_bridge',
            executable='web_bridge_node',
            name='web_bridge_node',
            parameters=[vision_config],
            output='screen',
        ),
    ])
