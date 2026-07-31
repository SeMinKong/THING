import rclpy #python에서 ros 기능 사용 가능케 import
from rclpy.node import Node
# 토픽 QoS 통신 규칙을 설정하기 위한 기능들
from rclpy.qos import(
    QoSProfile,
    ReliabilityPolicy,
    DurabilityPolicy # 늦게 구독한 노드가 마지막 메시지를 받을지 설정
)
from thing_interfaces.msg import RecordingState # ros2 msg import
# 메시지 형식 가져오기


class Logger(Node):
    def __init__(self):
        super().__init__('logger') # 초기화
        #thing/recording_state 토픽 통신 규칙
        recording_state_qos = QoSProfile(
            depth=1,
            reliability = ReliabilityPolicy.RELIABLE,
            durability = DurabilityPolicy.TRANSIENT_LOCAL
        )

        # recoringState 메시지를 thing/recording_state로 토픽으로 발행할 publisher 생성
        self.recording_state_publisher = self.create_publisher(
            RecordingState,
            'thing/recording_state',
            recording_state_qos,
        )
        self.get_logger().info('Logger started.')

def main(args=None):
    # ROS2 Python 통신 기능 초기화
    rclpy.init(args=args)
    # Logger 노드 객체 생성
    logger = Logger() # logger 노드 객체 생성
           

    try:
        # 서비스 요청이나 토픽 메세지를 처리하며 계속 실행
        rclpy.spin(logger)

    except KeyboardInterrupt:
        pass
    finally:
        logger.destroy_node() # 노드 종료 (노드 제거)
        rclpy.shutdown() # Ros2 python 기능 종료

if __name__ == '__main__':
    main() # main 함수 실행