"""Launch the MIMIC logger and its isolated uploader process."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.actions import ExecuteProcess
from launch.substitutions import LaunchConfiguration
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackagePrefix
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    """
    Start the logger and uploader with one command.

    uploader는 ROS 노드는 아니지만 별도 프로세스 격리를 유지한 채 이 launch가
    함께 관리한다. 기본적으로 /etc/thing-uploader.env를 읽으며 launch 인자로
    다른 파일을 지정할 수 있다.
    """
    logger_config = PathJoinSubstitution(
        [FindPackageShare('thing_bringup'), 'config', 'logger.yaml']
    )
    uploader_env_file = LaunchConfiguration('uploader_env_file')
    uploader_executable = PathJoinSubstitution([
        FindPackagePrefix('thing_logger'),
        'lib',
        'thing_logger',
        'uploader',
    ])

    return LaunchDescription([
        DeclareLaunchArgument(
            'uploader_env_file',
            default_value='/etc/thing-uploader.env',
            description='Uploader KEY=VALUE configuration file',
        ),
        ExecuteProcess(
            cmd=[uploader_executable],
            name='uploader',
            output='screen',
            additional_env={
                'THING_UPLOADER_ENV_FILE': uploader_env_file,
            },
        ),
        Node(
            package='thing_logger',
            executable='logger',
            name='logger',
            parameters=[logger_config],
            output='screen',
        ),
    ])
