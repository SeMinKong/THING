"""Safety state management and explicit STOP settling."""

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from std_msgs.msg import Empty
from std_srvs.srv import Trigger

from thing_interfaces.msg import SafetyState


class SafetyManager(Node):
    """
    로봇 손의 안전 상태를 관리하는 ROS 2 노드.

    현재 최소 구현 범위:
    1. /thing/safety_state 토픽 발행
    2. /thing/reset_safety 서비스 제공
    3. SAFE, FAULT, ESTOP 상태에서만 Reset 허용
    4. Reset 성공 시 바로 RUN으로 가지 않고 INIT으로 전환

    추후 구현 범위:
    - 명령 timeout에 따른 RUN → HOLD → SAFE 전이
    - MotorStatus 구독
    - 과전류·과온·통신 오류 판정
    - 물리 E-Stop GPIO 입력
    - 안정시간 검사
    - INIT 전체 재검사 후 READY 전환
    - 토크 OFF 요청
    - mode=DISABLED, owner=NONE 처리
    - 이전 명령 및 실행 큐 폐기
    """

    def __init__(self, parameter_overrides=None):
        # ROS 2 노드 이름을 safety_manager로 설정한다.
        super().__init__(
            "safety_manager",
            parameter_overrides=parameter_overrides,
        )

        self.stop_settle_ms = self.declare_parameter(
            "stop_settle_ms",
            500,
        ).value
        if (
            isinstance(self.stop_settle_ms, bool)
            or not isinstance(self.stop_settle_ms, int)
            or self.stop_settle_ms <= 0
        ):
            raise ValueError("stop_settle_ms must be a positive integer")
        self.stop_settle_timer = None

        # 현재 SafetyState 상태.
        # 노드 시작 시에는 이전 상태를 복구하지 않고 항상 INIT부터 시작한다.
        self.current_state = SafetyState.INIT
        # self.current_state = SafetyState.SAFE # 테스트 용도

        # 현재 명령 timeout 발생 여부.
        # 추후 command timeout 감시 로직에서 갱신한다.
        self.command_timeout = False

        # 현재 모터 통신 상태.
        # 아직 MotorStatus를 구독하지 않으므로 초기에는 확인되지 않은 상태로 둔다.
        self.motor_communication_ok = False

        # 개별 안전 이상 상태.
        # 추후 MotorStatus와 하드웨어 입력을 통해 갱신한다.
        self.over_current = False
        self.over_temperature = False
        self.estop_active = False

        # 최소 구현용 통합 fault 상태.
        #
        # 나중에는 다음 조건으로 계산해야 한다.
        # - 과전류
        # - 과온
        # - 지속적인 모터 통신 실패
        # - SAFE 자세 이동 실패
        self.fault_active = False

        # 현재 fault 코드.
        # 0은 fault 없음으로 사용하고,
        # 실제 코드표는 추후 docs/interfaces.md에서 확정한다.
        self.fault_code = 0

        # SafetyState는 상태 정보이므로,
        # 나중에 접속한 구독자도 마지막 상태를 받아야 한다.
        #
        # Reliable:
        #   상태 메시지를 신뢰성 있게 전달한다.
        #
        # Transient Local:
        #   Publisher가 마지막으로 발행한 상태를 저장해 두었다가
        #   늦게 접속한 구독자에게도 전달한다.
        safety_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )

        # 현재 안전 상태를 발행하는 Publisher.
        #
        # 토픽:
        #   /thing/safety_state
        #
        # 타입:
        #   thing_interfaces/msg/SafetyState
        self.safety_state_publisher = self.create_publisher(
            SafetyState,
            "/thing/safety_state",
            safety_qos,
        )

        # 안전 상태 복구를 요청받는 서비스 서버.
        #
        # 서비스 이름:
        #   /thing/reset_safety
        #
        # 서비스 타입:
        #   std_srvs/srv/Trigger
        #
        # 요청이 들어오면:
        #   handle_reset_safety()가 실행된다.
        self.reset_safety_service = self.create_service(
            Trigger,
            "/thing/reset_safety",
            self.handle_reset_safety,
        )
        self.stop_subscription = self.create_subscription(
            Empty,
            "/thing/control/stop_requested",
            self.handle_stop_requested,
            10,
        )

        # 노드 시작 직후 현재 상태인 INIT을 최초 발행한다.
        #
        # Transient Local QoS도 실제로 메시지를 한 번 이상 발행해야
        # 새 구독자에게 마지막 값을 제공할 수 있다.
        self.publish_safety_state("startup")

        self.get_logger().info("Safety manager started.")
        self.get_logger().info(
            "Service ready: /thing/reset_safety"
        )

    def handle_stop_requested(self, message):
        """Apply explicit STOP semantics without taking motor ownership."""
        del message
        self._cancel_stop_settle_timer()

        if self.current_state in (SafetyState.RUN, SafetyState.HOLD):
            self.current_state = SafetyState.HOLD
            self.command_timeout = False
            self.publish_safety_state("stop_settling")
            self.stop_settle_timer = self.create_timer(
                float(self.stop_settle_ms) / 1000.0,
                self._finish_stop_settling,
            )
            return

        if self.current_state == SafetyState.READY:
            self.command_timeout = False
            self.publish_safety_state("stop_ready")
            return

        self.publish_safety_state("stop_control_released")

    def _finish_stop_settling(self):
        self._cancel_stop_settle_timer()

        if self.current_state != SafetyState.HOLD:
            return
        if self.estop_active:
            self.current_state = SafetyState.ESTOP
            self.publish_safety_state("estop_during_stop_settle")
            return
        if self.has_active_fault():
            self.current_state = SafetyState.FAULT
            self.publish_safety_state("fault_during_stop_settle")
            return

        self.current_state = SafetyState.READY
        self.command_timeout = False
        self.publish_safety_state("stop_settled")

    def _cancel_stop_settle_timer(self):
        timer = self.stop_settle_timer
        self.stop_settle_timer = None
        if timer is None:
            return
        timer.cancel()
        self.destroy_timer(timer)

    def handle_reset_safety(self, request, response):
        """
        /thing/reset_safety 요청을 처리한다.

        Reset은 다음 상태에서만 허용한다.
        - SAFE
        - FAULT
        - ESTOP

        Reset이 성공해도 바로 READY 또는 RUN으로 가지 않는다.
        먼저 INIT으로 돌아가 전체 안전검사를 다시 수행해야 한다.
        """
        # std_srvs/srv/Trigger의 요청에는 필드가 없다.
        # 콜백 형식상 request 인자는 필요하지만 실제로 사용하지 않는다.
        del request

        # SAFE, FAULT, ESTOP이 아니면 Reset이 필요하지 않으므로 거부한다.
        #
        # 따라서 INIT, READY, RUN, HOLD에서는 거부된다.
        # 특히 단순 HOLD 상태는 Safety Reset 대상이 아니다.
        if self.current_state not in (
            SafetyState.SAFE,
            SafetyState.FAULT,
            SafetyState.ESTOP,
        ):
            response.success = False
            response.message = "reset_not_allowed"

            self.get_logger().warning(
                "Safety reset rejected: reset_not_allowed"
            )
            return response

        # 물리 E-Stop이 여전히 활성화돼 있으면 Reset을 거부한다.
        #
        # 이 서비스는 물리 E-Stop을 소프트웨어로 해제하는 기능이 아니다.
        # 사용자가 실제 E-Stop 버튼을 먼저 해제해야 한다.
        if self.estop_active:
            response.success = False
            response.message = "estop_still_active"

            self.get_logger().warning(
                "Safety reset rejected: estop_still_active"
            )
            return response

        # 과전류, 과온, 통신 장애 등의 fault 원인이 남아 있으면 거부한다.
        #
        # 현재는 최소 구현이므로 fault_active 하나만 검사한다.
        # 추후 MotorStatus와 실제 하드웨어 상태를 이용해 계산해야 한다.
        if self.has_active_fault():
            response.success = False
            response.message = "fault_still_active"

            self.get_logger().warning(
                "Safety reset rejected: fault_still_active"
            )
            return response

        # 모든 최소 조건을 통과하면 INIT으로 돌아간다.
        #
        # 중요:
        # Reset 성공은 RUN 복귀가 아니다.
        #
        # 실제 최종 흐름:
        # SAFE/FAULT/ESTOP
        # → Reset 요청
        # → INIT 재검사
        # → 정상일 때 READY
        # → 새 mode·owner 획득
        # → 새 명령 수신
        # → RUN
        self.enter_init_for_recheck()

        response.success = True
        response.message = "reset_accepted"

        self.get_logger().info(
            "Safety reset accepted. State changed to INIT."
        )

        return response

    def has_active_fault(self):
        """
        현재 해결되지 않은 fault가 있는지 반환한다.

        현재는 최소 구현이므로 fault_active만 사용한다.

        추후에는 다음 조건을 포함해야 한다.
        - over_current
        - over_temperature
        - 지속적인 motor communication 오류
        - SAFE motion timeout
        """
        return self.fault_active

    def enter_init_for_recheck(self):
        """
        Safety Reset 수락 후 INIT 상태로 전환한다.

        현재 최소 구현에서는 상태만 INIT으로 바꾸고 메시지를 발행한다.

        실제 구현에서는 다음 작업이 함께 필요하다.
        - 이전 command 폐기
        - Gesture·Action·Sequence 실행 취소
        - 명령 queue 폐기
        - 토크 OFF 요청
        - mode=DISABLED 유지
        - owner=NONE 유지
        - 설정 유효성 검사
        - E-Stop 입력 재검사
        - 7개 모터 통신 재검사
        - 모든 검사가 통과한 경우에만 READY 전환
        """
        self._cancel_stop_settle_timer()
        # 바로 READY나 RUN으로 가지 않고 INIT으로 전환한다.
        self.current_state = SafetyState.INIT

        # 이전 timeout 상태와 fault 코드를 초기화한다.
        #
        # 실제 구현에서는 원인이 완전히 해소됐고
        # INIT 검사까지 통과한 뒤 초기화하는 방식으로 더 엄격히 관리해야 한다.
        self.command_timeout = False
        self.fault_code = 0

        # 변경된 INIT 상태를 다른 노드와 웹에 알린다.
        self.publish_safety_state("reset_accepted")

    def publish_safety_state(self, reason):
        """
        현재 내부 안전 상태를 SafetyState 메시지로 만들어 발행한다.

        reason은 상태가 변경된 이유나 현재 상태 설명이다.
        """
        message = SafetyState()

        # SafetyState 메시지를 생성한 ROS 시간.
        message.stamp = self.get_clock().now().to_msg()

        # 현재 안전 상태:
        # INIT, READY, RUN, HOLD, SAFE, FAULT, ESTOP
        message.state = self.current_state

        # 현재 명령 timeout 여부.
        message.command_timeout = self.command_timeout

        # 전체 필수 모터 통신 상태.
        message.motor_communication_ok = (
            self.motor_communication_ok
        )

        # 과전류·과온 상태.
        message.over_current = self.over_current
        message.over_temperature = self.over_temperature

        # 물리 E-Stop 활성 여부.
        message.estop_active = self.estop_active

        # 현재 fault 코드.
        message.fault_code = self.fault_code

        # 상태 변경 또는 오류 원인.
        message.reason = reason

        # /thing/safety_state 토픽으로 메시지를 발행한다.
        self.safety_state_publisher.publish(message)


def main(args=None):
    """Run the SafetyManager node."""
    # ROS 2 Python 통신을 초기화한다.
    rclpy.init(args=args)

    # SafetyManager 노드 객체를 생성한다.
    node = SafetyManager()

    try:
        # 서비스 요청과 추후 토픽 콜백을 계속 처리한다.
        rclpy.spin(node)

    except (KeyboardInterrupt, ExternalShutdownException):
        # Ctrl+C 종료는 정상 종료로 처리한다.
        pass

    finally:
        # 노드 자원을 정리한다.
        node.destroy_node()

        # 아직 ROS 2가 실행 중인 경우에만 종료한다.
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
