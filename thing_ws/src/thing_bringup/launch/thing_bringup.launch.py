"""
로봇 손 DYNAMIXEL 드라이버를 편 손 자세 기준으로 시작하는 ROS 2 launch 파일.

초보자용 간단 매뉴얼
---------------------
1. 역할
   ``motors.yaml``의 실제 motor driver 파라미터를 읽어 ``motor_driver_node``를 시작한다.
   설정의 ``home_positions_raw``는 편 손 자세이며, 정규화 명령 0.0의 기준점이다.
2. 입력
   기본 입력은 ``thing_bringup/config/motors.yaml``이다. 다른 장비 설정을 시험해야 할 때만
   ``motors_config:=...`` launch 인자로 YAML 경로를 바꾼다.
3. 출력
   드라이버가 ``/thing/motor_status``와 ``/thing/diagnostics``를 발행하고,
   ``/thing/command``와 ``/thing/safety_state``를 구독한다.
4. 시작 흐름
   launch 실행 → 세 DYNAMIXEL bus 연결 → 7개 모터 확인 → 토크 OFF 상태에서
   제어값 설정 → 현재 위치 읽기. 이후 정상 제어권에서 ``open`` 명령을 받으면
   보정된 ID 1·3·4·7만 편 손 자세로 이동한다. 미보정 ID 2·5·6의 -1 endpoint는
   쓰지 않는다. Safety Manager가 ``SAFE``를 발행하면 드라이버는 일반 명령과 별도로
   ``safe_positions_raw``까지 저속 이동한 뒤 토크를 끄는 ``run_safe_cycle()``을 수행한다.
5. 사용 방법
   선행 작업 ``S15P11C103-67`` motor driver와 ``S15P11C103-89`` E-Stop publisher가
   병합된 뒤 사용한다. ``control.launch.py``를 먼저 실행하고 이 launch를 실행한다.
   Safety Manager가 READY이고 MANUAL/WEB 제어권을 얻은 상태에서 ``open`` gesture를
   요청한다.
6. 안전 경계
   launch 자체는 토크를 강제로 켜거나 시작 즉시 움직이지 않는다. 정상 명령은
   ``open → Command Manager → Command Guard → motor_driver_node`` 경로를 사용하고,
   SAFE 이동은 Safety Manager의 ``/thing/safety_state`` 전이에 따라 수행한다.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    """손 모터 드라이버를 version-controlled 파라미터로 시작한다."""
    default_motors_config = PathJoinSubstitution([
        FindPackageShare('thing_bringup'),
        'config',
        'motors.yaml',
    ])
    motors_config = LaunchConfiguration('motors_config')

    return LaunchDescription([
        # 포트나 보정값이 다른 장비에서도 launch 코드를 고치지 않고 YAML만 교체한다.
        DeclareLaunchArgument(
            'motors_config',
            default_value=default_motors_config,
            description='Absolute path to the robot-hand motor parameter YAML.',
        ),
        Node(
            package='thing_hardware',
            executable='motor_driver_node',
            name='motor_driver_node',
            parameters=[motors_config],
            output='screen',
            emulate_tty=True,
        ),
    ])
