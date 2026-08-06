"""Launch the MIMIC session recording logger node."""

from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    """
    Start the logger with one version-controlled parameter file.

    격리 uploader 데몬은 ROS 노드가 아니므로 이 launch가 시작하지 않는다.
    별도 프로세스로 THING_UPLOADER_*/THING_EC2_UPLOAD_URL 환경변수와 함께
    기동한다(docs/decisions/2026-08-05-logger-uploader-jetson-배치.md).
    """
    logger_config = PathJoinSubstitution(
        [FindPackageShare('thing_bringup'), 'config', 'logger.yaml']
    )

    return LaunchDescription([
        Node(
            package='thing_logger',
            executable='logger',
            name='logger',
            parameters=[logger_config],
            output='screen',
        ),
    ])
