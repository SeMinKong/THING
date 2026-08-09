"""
Manager가 선택한 명령을 fail-closed 방식으로 거르는 ROS 2 어댑터.

간단 매뉴얼
-----------
① 역할
    ``CommandGuardCore``와 ROS 2 topic 사이를 연결하는 command chain의 마지막
    소프트웨어 문이다. core가 수락하고 hardware 전달까지 허용한 ``HandCommand``만
    ``/thing/command``로 발행한다.
② 입력
    ``/thing/command/selected``의 후보 명령, ``/thing/safety_state``의 안전 상태,
    ``/thing/control_state``의 mode/owner 상태, ``/thing/control/stop_requested``의
    STOP 요청, 그리고 node parameter로 주어지는 timeout·축 한계이다.
③ 출력
    통과한 hardware 명령, HOLD 복구 판단용 ``validation_result``, STOP 처리 경계를
    알리는 ``stop_barrier_ack``, 수락/거부 이유를 담은 diagnostics를 발행한다.
④ 주요 실행 흐름
    상태 callback이 core의 최신 상태를 갱신한다. 선택 명령 callback은 ROS message를
    ``GuardCommand``로 변환하고 system clock과 monotonic clock을 함께 넘겨 검증한다.
    검증 결과에 따라 hardware 전달, HOLD activity 통지, 또는 거부 진단 중 하나만
    수행한다. 모든 callback은 같은 재진입 lock 아래에서 순서화된다.
⑤ 사용/실행 방법
    ROS 2 package의 console script/launch에서 이 module의 ``main``을 실행하거나,
    직접 실행 가능한 환경에서는 ``python3 command_guard.py``로 node를 spin한다.
    실제 topic type과 parameter 파일을 제공하는 ROS 2 workspace가 먼저 준비되어야 한다.
⑥ 책임 경계와 하지 않는 일
    이 파일은 ROS 변환·QoS·publish와 callback 순서화를 담당한다. 검증 정책 자체는
    ``command_guard_core.py``가 담당한다. ``SafetyState``는 Safety Manager,
    ``ControlState``는 Command Manager, 실제 motor write·torque-off는
    ``thing_hardware``가 담당한다. 서로 다른 DDS topic 사이의 전역 수신 순서는
    보장되지 않으며, 필요한 인과 경계만 STOP ACK와 message stamp로 확인한다.
"""

from threading import RLock
from time import monotonic_ns
from typing import Optional

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rclpy.clock import Clock, ClockType
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from std_msgs.msg import Bool, UInt64

from thing_control.command_guard_core import (
    AXIS_NAMES,
    CommandGuardCore,
    GuardCommand,
    GuardLimits,
)
from thing_interfaces.msg import ControlState, HandCommand, SafetyState


# command는 최신 표본 하나만 필요하지만 유실을 허용하지 않으므로 KEEP_LAST(1)과
# RELIABLE을 쓴다. 이 QoS는 전달 품질만 정하며 명령 승인 여부는 core가 별도로 판단한다.
_COMMAND_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
)
# 상태는 늦게 참가한 Guard도 마지막 값을 받을 수 있게 transient-local로 구독한다.
# 단, 과거 표본을 무조건 신뢰하지 않고 core가 receive freshness와 재획득 경계를 다시
# 검사한다. 즉 QoS의 latch 기능은 가용성을 돕지만 안전 권한을 부여하지는 않는다.
_STATE_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)
# 진단은 최근 이력을 관찰 도구가 따라잡을 수 있도록 command/state보다 깊은 queue를 둔다.
_DIAGNOSTIC_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
    reliability=ReliabilityPolicy.RELIABLE,
)
# STOP barrier와 validation_result는 node 사이 내부 handshake이므로 RELIABLE로 전달하고,
# 짧은 burst에서 callback 처리 지연이 생겨도 최근 사건을 보존하도록 depth 10을 둔다.
_INTERNAL_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
    reliability=ReliabilityPolicy.RELIABLE,
)


class CommandGuardNode(Node):
    """
    ROS 입력을 직렬화해 core 정책을 적용하고 승인된 출력만 발행하는 node.

    이 클래스는 하나의 lock으로 상태 갱신, STOP latch, 명령 승인과 publish를 묶는다.
    따라서 같은 프로세스 안에서는 검증 직후 STOP이 끼어들어 예전 명령이 발행되는
    틈을 만들지 않는다. 실제 허용/거부 규칙과 상태 기억은 ``CommandGuardCore``에
    위임하고, 여기서는 message 변환과 ROS I/O만 담당한다.
    """

    def __init__(self, parameter_overrides=None) -> None:
        super().__init__(
            'command_guard',
            parameter_overrides=parameter_overrides,
        )
        # 승인 확인과 publish 사이까지 같은 lock을 유지해야 STOP/state callback이
        # 중간에 끼어드는 TOCTOU(time-of-check/time-of-use) 경로가 닫힌다.
        self._transaction_lock = RLock()
        # 경과 시간에는 시스템 시각 보정의 영향을 받지 않는 steady/monotonic 계열을,
        # wire stamp와 diagnostics에는 producer와 같은 시간축인 system clock을 쓴다.
        # use_sim_time 설정과 무관하게 이 계약을 유지하려고 clock type을 명시한다.
        self._steady_clock = Clock(clock_type=ClockType.STEADY_TIME)
        self._system_clock = Clock(clock_type=ClockType.SYSTEM_TIME)
        self._core = CommandGuardCore(self._declare_limits())

        self._command_publisher = self.create_publisher(
            HandCommand,
            '/thing/command',
            _COMMAND_QOS,
        )
        self._validation_result_publisher = self.create_publisher(
            Bool,
            '/thing/command/validation_result',
            _INTERNAL_QOS,
        )
        self._stop_ack_publisher = self.create_publisher(
            UInt64,
            '/thing/control/stop_barrier_ack',
            _INTERNAL_QOS,
        )
        self._diagnostic_publisher = self.create_publisher(
            DiagnosticArray,
            '/thing/diagnostics',
            _DIAGNOSTIC_QOS,
        )
        self._last_diagnostic_reason = 'startup'
        self._last_stop_generation = 0
        self._last_diagnostic_accepted = False
        self._last_diagnostic_source = -1
        self._last_diagnostic_sequence = -1
        diagnostic_period_ms = self._declare_diagnostic_period_ms()
        # 진단 timer도 steady clock을 써서 system clock 조정 때문에 발행 간격이 왜곡되거나
        # 멈추지 않게 한다. 안전 판정 자체와 별개인 관측 경로지만 같은 lock으로 snapshot한다.
        self._diagnostic_timer = self.create_timer(
            diagnostic_period_ms / 1000.0,
            self._publish_periodic_diagnostic,
            clock=self._steady_clock,
        )
        self._selected_subscription = self.create_subscription(
            HandCommand,
            '/thing/command/selected',
            self._on_selected_command,
            _COMMAND_QOS,
        )
        self._safety_subscription = self.create_subscription(
            SafetyState,
            '/thing/safety_state',
            self._on_safety_state,
            _STATE_QOS,
        )
        self._control_subscription = self.create_subscription(
            ControlState,
            '/thing/control_state',
            self._on_control_state,
            _STATE_QOS,
        )
        self._stop_subscription = self.create_subscription(
            UInt64,
            '/thing/control/stop_requested',
            self._on_stop_requested,
            _INTERNAL_QOS,
        )

    def _declare_diagnostic_period_ms(self) -> int:
        """진단 주기를 선언하고 과도하게 느리거나 잘못된 설정을 시작 시 차단한다."""
        period_ms = self.declare_parameter(
            'diagnostic_period_ms',
            1000,
        ).value
        if (
            isinstance(period_ms, bool)
            or not isinstance(period_ms, int)
            or not 0 < period_ms <= 1000
        ):
            raise ValueError(
                'diagnostic_period_ms must be an integer in [1, 1000]'
            )
        return period_ms

    def _declare_limits(self) -> GuardLimits:
        """
        ROS parameter를 읽어 core가 검증하는 하나의 ``GuardLimits``로 묶는다.

        최종 범위와 hard maximum 검사는 ``GuardLimits`` 생성 시 수행된다. 잘못되거나
        안전 한계를 넓히는 설정은 node가 열린 상태로 시작하지 않고 예외로 실패한다.
        """
        command_timeout_ms = self.declare_parameter(
            'command_timeout_ms',
            300,
        ).value
        future_tolerance_ms = self.declare_parameter(
            'command_future_tolerance_ms',
            100,
        ).value
        safety_timeout_ms = self.declare_parameter(
            'safety_state_timeout_ms',
            1500,
        ).value
        control_timeout_ms = self.declare_parameter(
            'control_state_timeout_ms',
            1500,
        ).value
        command_hold_ms = self.declare_parameter(
            'command_hold_ms',
            5000,
        ).value

        axis_min = {}
        axis_max = {}
        max_delta = {}
        mimic_max_delta = {}
        for axis_name in AXIS_NAMES:
            prefix = f'axis_limits.{axis_name}'
            axis_min[axis_name] = self.declare_parameter(
                f'{prefix}.min',
                0.0,
            ).value
            axis_max[axis_name] = self.declare_parameter(
                f'{prefix}.max',
                1.0,
            ).value
            max_delta[axis_name] = self.declare_parameter(
                f'{prefix}.max_delta_per_second',
                1.5,
            ).value
            mimic_max_delta[axis_name] = self.declare_parameter(
                f'mimic_axis_limits.{axis_name}.max_delta_per_second',
                10.0,
            ).value

        return GuardLimits(
            command_stale_timeout_ms=command_timeout_ms,
            command_future_tolerance_ms=future_tolerance_ms,
            safety_state_timeout_ms=safety_timeout_ms,
            control_state_timeout_ms=control_timeout_ms,
            command_hold_ms=command_hold_ms,
            axis_min=axis_min,
            axis_max=axis_max,
            max_axis_delta_per_second=max_delta,
            mimic_max_axis_delta_per_second=mimic_max_delta,
        )

    def _on_safety_state(self, message: SafetyState) -> None:
        """정규 stamp를 가진 SafetyState만 monotonic 수신 시각과 함께 core에 전달한다."""
        with self._transaction_lock:
            # sec/nanosec 조합이 canonical하지 않으면 서로 다른 표현이 같은 시각으로
            # 해석될 수 있으므로, 상태 freshness의 기준 자체를 갱신하지 않는다.
            nanosec = int(message.stamp.nanosec)
            if not 0 <= nanosec < 1_000_000_000:
                return
            source_stamp_ns = (
                int(message.stamp.sec) * 1_000_000_000 + nanosec
            )
            if source_stamp_ns <= 0:
                return
            self._core.update_safety_state(
                message.state,
                monotonic_ns(),
                source_stamp_ns=source_stamp_ns,
                reason=str(message.reason),
            )

    def _on_control_state(self, message: ControlState) -> None:
        """mode·owner·생존 상태와 로컬 수신 시각을 원자적으로 core에 반영한다."""
        with self._transaction_lock:
            self._core.update_control_state(
                message.active_mode,
                message.active_owner,
                message.owner_alive,
                monotonic_ns(),
            )

    def _on_stop_requested(self, message: UInt64) -> None:
        """core의 STOP latch를 먼저 닫고 같은 transaction 안에서 ACK를 발행한다."""
        with self._transaction_lock:
            generation = int(message.data)
            if generation <= 0 or generation < self._last_stop_generation:
                return
            # ACK는 단순 수신 확인이 아니다. on_stop_requested가 latch와 activation trust를
            # 먼저 닫은 뒤에만 발행하므로 Manager가 service 완료 경계로 사용할 수 있다.
            if generation > self._last_stop_generation:
                self._core.on_stop_requested()
                self._last_stop_generation = generation
            acknowledgement = UInt64()
            acknowledgement.data = generation
            self._stop_ack_publisher.publish(acknowledgement)

    def _on_selected_command(self, message: HandCommand) -> None:
        """
        선택 명령을 변환·검증하고 결정에 맞는 ROS 출력 하나를 수행한다.

        callback 전체가 transaction lock 안에 있으므로 core가 승인한 결과와 실제
        publish 사이에 다른 상태 callback이 들어오지 않는다. 거부 시 hardware topic은
        건드리지 않고 validation 실패와 진단만 발행한다.
        """
        with self._transaction_lock:
            # stamp의 nanosec 범위는 ROS message 생성기가 항상 보장한다고 가정하지 않는다.
            # malformed input을 정규화하거나 예외로 죽지 않고 진단과 함께 거부한다.
            nanosec = int(message.stamp.nanosec)
            if not 0 <= nanosec < 1_000_000_000:
                self._publish_rejection(message, 'command_stamp_non_canonical')
                return
            command = GuardCommand(
                stamp_ns=(
                    int(message.stamp.sec) * 1_000_000_000
                    + nanosec
                ),
                sequence=int(message.sequence),
                source=int(message.source),
                axes={
                    axis_name: float(getattr(message, axis_name))
                    for axis_name in AXIS_NAMES
                },
                speed_limit=float(message.speed_limit),
                confidence=float(message.confidence),
            )
            decision = self._core.validate(
                command,
                # command stamp freshness는 system time, 상태 수신 age와 축 변화율은
                # monotonic time으로 계산해 서로 다른 clock의 목적을 섞지 않는다.
                now_ros_ns=self._system_clock.now().nanoseconds,
                now_monotonic_ns=monotonic_ns(),
            )
            if decision.accepted:
                self._last_diagnostic_reason = decision.reason
                self._last_diagnostic_accepted = True
                self._last_diagnostic_source = int(message.source)
                self._last_diagnostic_sequence = int(message.sequence)
                if not decision.forward_to_hardware:
                    # HOLD 복구 activity는 safety 판단에만 쓰며 motor에는 전달하지 않는다.
                    validation_result = Bool()
                    validation_result.data = True
                    self._validation_result_publisher.publish(validation_result)
                    return
                self._command_publisher.publish(message)
                return
            self._publish_rejection(message, decision.reason)

    def _publish_periodic_diagnostic(self) -> None:
        """마지막 판단 결과를 주기적으로 재발행해 조용한 구간도 관측 가능하게 한다."""
        with self._transaction_lock:
            self._publish_diagnostic(
                reason=self._last_diagnostic_reason,
                accepted=self._last_diagnostic_accepted,
                source=self._last_diagnostic_source,
                sequence=self._last_diagnostic_sequence,
            )

    def _publish_rejection(self, message: HandCommand, reason: str) -> None:
        """거부를 recovery 입력·diagnostics·throttled log에 일관되게 기록한다."""
        # valid-invalid-valid를 하나의 HOLD recovery window로 세지 않게 실패도 알린다.
        validation_result = Bool()
        validation_result.data = False
        self._validation_result_publisher.publish(validation_result)
        self._last_diagnostic_reason = reason
        self._last_diagnostic_accepted = False
        self._last_diagnostic_source = int(message.source)
        self._last_diagnostic_sequence = int(message.sequence)
        self._publish_diagnostic(
            reason=reason,
            accepted=False,
            source=int(message.source),
            sequence=int(message.sequence),
        )
        self.get_logger().warning(
            f'HandCommand rejected: {reason}',
            throttle_duration_sec=1.0,
        )

    def _publish_diagnostic(
        self,
        *,
        reason: str,
        accepted: bool,
        source: int,
        sequence: int,
    ) -> None:
        """현재 판단을 표준 ROS diagnostic message로 만들어 발행한다."""
        diagnostic = DiagnosticArray()
        diagnostic.header.stamp = self._system_clock.now().to_msg()
        status = DiagnosticStatus()
        status.level = (
            DiagnosticStatus.OK if accepted else DiagnosticStatus.WARN
        )
        status.name = 'thing_control/command_guard'
        status.hardware_id = 'thing-control'
        status.message = reason
        status.values = [
            KeyValue(key='source', value=str(source)),
            KeyValue(key='sequence', value=str(sequence)),
            KeyValue(key='accepted', value=str(accepted).lower()),
        ]
        diagnostic.status = [status]
        self._diagnostic_publisher.publish(diagnostic)


def main(args: Optional[list] = None) -> None:
    """ROS client를 초기화해 CommandGuardNode를 spin하고 종료 자원을 정리한다."""
    rclpy.init(args=args)
    node = CommandGuardNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
