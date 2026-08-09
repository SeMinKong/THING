"""Launch the browser-facing transports: MJPEG streaming and the Web Bridge."""

from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    """
    Start mjpeg_streamer and web_bridge_node.

    파라미터는 기존 vision.yaml의 mjpeg_streamer·web_bridge_node 절을
    그대로 사용한다(노드 이름 키가 달라 vision 노드 설정과 충돌하지 않음).
    """
    vision_config = PathJoinSubstitution(
        [FindPackageShare('thing_bringup'), 'config', 'vision.yaml']
    )

    return LaunchDescription([
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
