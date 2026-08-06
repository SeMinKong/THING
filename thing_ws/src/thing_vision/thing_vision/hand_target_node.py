#!/usr/bin/env python3
#
# Copyright 2026 C103 Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Generate filtered seven-axis MIMIC commands from hand landmarks.

이 노드는 ``mediapipe_node``가 발행한 21개 손 랜드마크를 받아 힘줄 기반
로봇손이 사용할 일곱 개의 0..1 목표값으로 변환한다. 처리 순서는 다음과 같다.

1. 오른손 여부, confidence, 좌표 개수를 검사한다.
2. 손가락 관절 각도와 엄지의 거리/벌림 각도를 일곱 축으로 변환한다.
3. 사용자의 펼친 손/주먹 측정값으로 네 손가락을 다시 정규화한다.
4. deadband, 저역 통과 필터, 프레임당 변화량 제한을 적용한다.
5. 유효하고 최신인 목표만 ``/thing/command/mimic``으로 주기 발행한다.

여기서는 모터 pulse나 힘줄 길이를 직접 계산하지 않는다. 이 노드의 출력은
``0=펼침``, ``1=굽힘`` 의미의 정규화된 상위 제어 명령이며, 실제 모터 위치
변환과 최종 안전 검사는 뒤쪽 control/hardware 계층이 담당한다.
"""

from dataclasses import dataclass
import math
import time
from typing import Any, Optional, Sequence, Tuple

from diagnostic_msgs.msg import DiagnosticArray
from diagnostic_msgs.msg import DiagnosticStatus
from diagnostic_msgs.msg import KeyValue
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy
from rclpy.qos import HistoryPolicy
from rclpy.qos import QoSProfile
from rclpy.qos import ReliabilityPolicy
from thing_interfaces.msg import ControlState
from thing_interfaces.msg import HandCommand
from thing_interfaces.msg import HandLandmarks


# MediaPipe Hands는 손 하나를 항상 같은 순서의 21개 점으로 표현한다.
LANDMARK_COUNT = 21
_EPSILON = 1.0e-8

# 이 파일의 ROS 데이터 흐름을 한곳에서 확인할 수 있도록 토픽명을 상수화한다.
# /thing/landmarks      : MediaPipe가 찾은 손 좌표 입력
# /thing/control_state  : MIMIC 모드와 제어권 상태 입력
# /thing/command/mimic  : 일곱 축으로 변환한 로봇손 목표 출력
# /thing/diagnostics    : 입력 상태와 계산 오류를 관제 쪽으로 출력
LANDMARKS_TOPIC = '/thing/landmarks'
MIMIC_COMMAND_TOPIC = '/thing/command/mimic'
CONTROL_STATE_TOPIC = '/thing/control_state'
DIAGNOSTICS_TOPIC = '/thing/diagnostics'

# MediaPipe Hands의 고정 인덱스다. 각 손가락은 손바닥 쪽 관절부터 끝점까지
# 연속된 네 점을 사용하므로 아래 번호를 조합해 관절 각도를 계산할 수 있다.
WRIST = 0
THUMB_CMC = 1
THUMB_MCP = 2
THUMB_IP = 3
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_PIP = 6
INDEX_DIP = 7
INDEX_TIP = 8
MIDDLE_MCP = 9
MIDDLE_PIP = 10
MIDDLE_DIP = 11
MIDDLE_TIP = 12
RING_MCP = 13
RING_PIP = 14
RING_DIP = 15
RING_TIP = 16
LITTLE_MCP = 17
LITTLE_PIP = 18
LITTLE_DIP = 19
LITTLE_TIP = 20

Vector3 = Tuple[float, float, float]


class InvalidLandmarks(ValueError):
    """Raised when a landmark set cannot produce a safe target."""


@dataclass(frozen=True)
class HandTargets:
    """Seven dimensionless robot-hand targets in the inclusive 0..1 range.

    엄지는 굽힘(flex), 맞섬(opposition), 벌림(abduction)의 세 축을 사용하고,
    나머지 네 손가락은 각각 하나의 굽힘 축을 사용한다. ``frozen=True``로 두어
    필터 처리 중 이전 목표가 실수로 변경되지 않도록 한다.
    """

    thumb_flex: float
    thumb_opp: float
    thumb_abd: float
    index_flex: float
    middle_flex: float
    ring_flex: float
    little_flex: float

    def as_tuple(self) -> Tuple[float, ...]:
        """Return targets in the same order as ``HandCommand.msg``."""
        return (
            self.thumb_flex,
            self.thumb_opp,
            self.thumb_abd,
            self.index_flex,
            self.middle_flex,
            self.ring_flex,
            self.little_flex,
        )


ZERO_TARGETS = HandTargets(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _landmarks_qos() -> QoSProfile:
    """Return the latest-sample-only QoS for landmark input.

    영상 계열 데이터는 오래된 표본을 순서대로 처리하는 것보다 가장 최신 손
    위치를 빠르게 받는 것이 중요하므로 BEST_EFFORT, depth 1을 사용한다.
    """
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE,
    )


def _command_qos() -> QoSProfile:
    """Return the reliable latest-command QoS from the V7 contract.

    로봇 제어 명령은 영상과 달리 유실 여부가 중요하므로 RELIABLE을 사용하되,
    과거 자세를 쌓아 두지 않도록 최신 명령 하나만 보관한다.
    """
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
    )


def _state_qos() -> QoSProfile:
    """Return the durable state QoS used by control state.

    TRANSIENT_LOCAL을 사용하면 노드가 늦게 시작해도 마지막 ControlState를 즉시
    받아 현재 MIMIC 제어권 상태를 판단할 수 있다.
    """
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
    )


def _diagnostics_qos() -> QoSProfile:
    """Return the reliable QoS used for low-rate diagnostics."""
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
    )


def compute_hand_targets(landmarks: Sequence[Any]) -> HandTargets:
    """
    Convert 21 fixed-order MediaPipe landmarks to seven target values.

    Each item may be a ROS ``Point32``-like object with ``x``, ``y`` and ``z``
    attributes, or a three-element sequence.  Joint angles make finger flexion
    independent of image size.  Thumb distance is divided by palm width for
    the same reason.

    Raises
    ------
    InvalidLandmarks
        If the count, coordinates, or palm scale is invalid.

    """
    # 현재 입력은 HandLandmarks.msg의 일반 ``landmarks`` 좌표다. 향후
    # world_landmarks를 추가하더라도 동일한 21점 순서와 x/y/z 구조라면 이 기하
    # 계산 함수는 그대로 재사용할 수 있다.
    points = _validated_points(landmarks)

    # 엄지 거리값은 카메라 속 손 크기에 따라 달라지므로 검지 MCP와 소지 MCP
    # 사이, 즉 손바닥 폭을 공통 길이 기준으로 사용한다.
    palm_width = _distance(points[INDEX_MCP], points[LITTLE_MCP])
    if palm_width <= _EPSILON:
        raise InvalidLandmarks('palm width is zero')

    # 각 손가락의 네 점에서 가운데 두 관절이 얼마나 꺾였는지 계산한다.
    # 결과는 모두 0(펴짐)..1(강하게 굽힘) 범위다.
    thumb_flex = _finger_flex(
        points,
        (THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP),
    )
    index_flex = _finger_flex(
        points,
        (INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP),
    )
    middle_flex = _finger_flex(
        points,
        (MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP),
    )
    ring_flex = _finger_flex(
        points,
        (RING_MCP, RING_PIP, RING_DIP, RING_TIP),
    )
    little_flex = _finger_flex(
        points,
        (LITTLE_MCP, LITTLE_PIP, LITTLE_DIP, LITTLE_TIP),
    )

    # 엄지 맞섬은 엄지 끝이 손바닥 중심에 가까워지는 동작이다. 절대 거리가
    # 아니라 손바닥 폭으로 나눈 비율을 사용해 사용자 손 크기의 영향을 줄인다.
    palm_center = _mean_point(
        points,
        (WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, LITTLE_MCP),
    )
    opposition_ratio = (
        _distance(points[THUMB_TIP], palm_center) / palm_width
    )
    # 손바닥 중심에 가까울수록 1, 멀수록 0이 되도록 역정규화한다.
    thumb_opp = _inverse_normalize(opposition_ratio, 0.20, 1.25)

    # 엄지 벌림은 CMC를 회전 중심으로 보아 CMC->엄지 끝 방향과 CMC->검지
    # MCP 방향 사이의 각도로 계산한다. 엄지 끝은 MCP보다 이동량이 커서 실제
    # 벌림을 더 잘 반영한다. 두 방향을 손바닥 평면에 먼저 투영해 손바닥 앞뒤로
    # 움직이는 opposition 성분이 abduction 값에 섞이는 것도 줄인다.
    palm_normal = _cross(
        _subtract(points[INDEX_MCP], points[WRIST]),
        _subtract(points[LITTLE_MCP], points[WRIST]),
    )
    thumb_direction = _project_onto_plane(
        _subtract(points[THUMB_TIP], points[THUMB_CMC]),
        palm_normal,
    )
    index_direction = _project_onto_plane(
        _subtract(points[INDEX_MCP], points[THUMB_CMC]),
        palm_normal,
    )
    thumb_spread = _angle(
        thumb_direction,
        index_direction,
    )
    # 투영된 두 방향이 10도 이하면 붙임, 65도 이상이면 최대 벌림으로 본다.
    thumb_abd = _normalize(
        thumb_spread,
        math.radians(10.0),
        math.radians(65.0),
    )

    return HandTargets(
        thumb_flex=thumb_flex,
        thumb_opp=thumb_opp,
        thumb_abd=thumb_abd,
        index_flex=index_flex,
        middle_flex=middle_flex,
        ring_flex=ring_flex,
        little_flex=little_flex,
    )


def _finger_flex(
    points: Sequence[Vector3],
    indices: Tuple[int, int, int, int],
) -> float:
    """Estimate flexion from the two distal joint bends of one digit.

    손바닥에 가까운 관절의 움직임이 전체 손가락 자세에 더 크게 기여하므로
    proximal 65%, distal 35% 가중치를 사용한다.
    """
    base, proximal, distal, tip = (points[index] for index in indices)
    proximal_bend = _joint_bend(base, proximal, distal)
    distal_bend = _joint_bend(proximal, distal, tip)
    return _clamp01(0.65 * proximal_bend + 0.35 * distal_bend)


def _joint_bend(first: Vector3, joint: Vector3, last: Vector3) -> float:
    """Return 0 for a straight joint and 1 near a strongly folded joint.

    ``joint``에서 양옆 점으로 향하는 두 벡터의 내적 각도를 구한다. 직선은
    180도이므로 ``180도 - 측정 각도``가 실제로 꺾인 정도가 된다. 프로젝트가
    강한 굽힘의 기준으로 정한 125도를 1로 보고 범위를 제한한다.
    """
    joint_angle = _angle(
        _subtract(first, joint),
        _subtract(last, joint),
    )
    straight_angle = math.pi
    maximum_expected_bend = math.radians(125.0)
    return _clamp01((straight_angle - joint_angle) / maximum_expected_bend)


def _validated_points(landmarks: Sequence[Any]) -> Tuple[Vector3, ...]:
    """Validate the fixed landmark contract and return plain x/y/z tuples.

    ROS Point32와 단위 테스트용 3원소 sequence를 모두 받을 수 있게 변환하며,
    잘못된 개수나 NaN/Inf가 제어 명령으로 전달되지 않도록 여기서 차단한다.
    """
    if len(landmarks) != LANDMARK_COUNT:
        raise InvalidLandmarks(
            f'expected {LANDMARK_COUNT} landmarks, got {len(landmarks)}',
        )

    points = []
    for index, landmark in enumerate(landmarks):
        try:
            if all(hasattr(landmark, axis) for axis in ('x', 'y', 'z')):
                point = (
                    float(landmark.x),
                    float(landmark.y),
                    float(landmark.z),
                )
            else:
                point = (
                    float(landmark[0]),
                    float(landmark[1]),
                    float(landmark[2]),
                )
        except (IndexError, TypeError, ValueError) as error:
            raise InvalidLandmarks(
                f'landmark {index} is not a three-dimensional point',
            ) from error

        if not all(math.isfinite(value) for value in point):
            raise InvalidLandmarks(
                f'landmark {index} contains a non-finite coordinate',
            )
        points.append(point)

    return tuple(points)


def _mean_point(
    points: Sequence[Vector3],
    indices: Tuple[int, ...],
) -> Vector3:
    count = float(len(indices))
    return tuple(
        sum(points[index][axis] for index in indices) / count
        for axis in range(3)
    )  # type: ignore[return-value]


def _subtract(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[0] - right[0],
        left[1] - right[1],
        left[2] - right[2],
    )


def _dot(left: Vector3, right: Vector3) -> float:
    return sum(a * b for a, b in zip(left, right))


def _cross(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _project_onto_plane(vector: Vector3, normal: Vector3) -> Vector3:
    """Project a vector onto a plane described by its normal vector."""
    normal_squared = _dot(normal, normal)
    if normal_squared <= _EPSILON:
        raise InvalidLandmarks('palm plane is degenerate')

    normal_scale = _dot(vector, normal) / normal_squared
    projected = tuple(
        component - normal_scale * normal_component
        for component, normal_component in zip(vector, normal)
    )
    if _norm(projected) <= _EPSILON:
        raise InvalidLandmarks(
            'thumb direction cannot be projected onto palm plane',
        )
    return projected  # type: ignore[return-value]


def _distance(left: Vector3, right: Vector3) -> float:
    return _norm(_subtract(left, right))


def _norm(vector: Vector3) -> float:
    return math.sqrt(sum(component * component for component in vector))


def _angle(first: Vector3, second: Vector3) -> float:
    """Return the angle between two 3-D vectors using their dot product."""
    first_norm = _norm(first)
    second_norm = _norm(second)
    if first_norm <= _EPSILON or second_norm <= _EPSILON:
        raise InvalidLandmarks('adjacent landmarks overlap')
    cosine = sum(
        left * right for left, right in zip(first, second)
    ) / (first_norm * second_norm)
    return math.acos(max(-1.0, min(1.0, cosine)))


def _normalize(value: float, minimum: float, maximum: float) -> float:
    """Linearly map ``minimum..maximum`` to ``0..1`` and clamp overflow."""
    if maximum <= minimum:
        raise ValueError('normalization maximum must exceed minimum')
    return _clamp01((value - minimum) / (maximum - minimum))


def _inverse_normalize(
    value: float,
    minimum: float,
    maximum: float,
) -> float:
    return 1.0 - _normalize(value, minimum, maximum)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class HandTargetNode(Node):
    """Convert valid right-hand landmarks into filtered MIMIC commands.

    랜드마크 callback은 새 목표를 계산/저장하고, 별도의 publish timer는 저장된
    최신 목표를 일정한 20 Hz 이상으로 발행한다. 즉 MediaPipe 처리 FPS와 제어
    명령 발행 주기를 분리한 구조다.
    """

    def __init__(self) -> None:
        """Initialize parameters, ROS interfaces, and hand-loss state."""
        super().__init__('hand_target_node')

        # 발행/필터 파라미터
        # - deadband: 이 값보다 작은 흔들림은 이전 명령을 유지한다.
        # - low_pass_alpha: 새 측정값을 한 번에 얼마나 반영할지 정한다.
        # - max_axis_delta_per_frame: 랜드마크 한 표본에서 바뀔 수 있는 최대량이다.
        self.declare_parameter('publish_rate_hz', 20.0)
        # true는 디버그/직접 시험용이다. ControlState를 무시하지만 손 유효성 및
        # freshness 검사는 유지한다. false일 때만 정상 MIMIC 제어권을 요구한다.
        self.declare_parameter('publish_without_control_state', False)
        self.declare_parameter('deadband', 0.02)
        self.declare_parameter('low_pass_alpha', 0.25)
        self.declare_parameter('max_axis_delta_per_frame', 0.08)
        self.declare_parameter('hand_confidence_min', 0.70)
        self.declare_parameter('hand_loss_debounce_ms', 150)
        self.declare_parameter('hand_reacquire_stable_ms', 300)
        self.declare_parameter('speed_limit', 0.25)
        self.declare_parameter('diagnostics_rate_hz', 1.0)

        # 사용자별 캘리브레이션 끝점이다. 엄지 굽힘/맞섬은 펼침에서 얻은 raw
        # 값을 min, 최대 동작에서 얻은 raw 값을 max로 사용한다. 엄지 벌림도
        # 붙인 상태를 min, 최대 벌림을 max로 사용해 붙임=0, 벌림=1로 만든다.
        # 나머지 네 손가락도 open..closed 구간을 0..1로 만든다.
        self.declare_parameter('thumb_flex_min', 0.0)
        self.declare_parameter('thumb_flex_max', 1.0)
        self.declare_parameter('thumb_opp_min', 0.0)
        self.declare_parameter('thumb_opp_max', 1.0)
        self.declare_parameter('thumb_abd_min', 0.0)
        self.declare_parameter('thumb_abd_max', 1.0)
        self.declare_parameter('index_flex_open', 0.0)
        self.declare_parameter('index_flex_closed', 1.0)
        self.declare_parameter('middle_flex_open', 0.0)
        self.declare_parameter('middle_flex_closed', 1.0)
        self.declare_parameter('ring_flex_open', 0.0)
        self.declare_parameter('ring_flex_closed', 1.0)
        self.declare_parameter('little_flex_open', 0.0)
        self.declare_parameter('little_flex_closed', 1.0)

        self._publish_rate_hz = float(
            self.get_parameter('publish_rate_hz').value,
        )
        self._publish_without_control_state = self.get_parameter(
            'publish_without_control_state',
        ).value
        if not isinstance(self._publish_without_control_state, bool):
            raise ValueError(
                'publish_without_control_state must be a boolean',
            )
        self._deadband = float(self.get_parameter('deadband').value)
        self._low_pass_alpha = float(
            self.get_parameter('low_pass_alpha').value,
        )
        self._max_axis_delta = float(
            self.get_parameter('max_axis_delta_per_frame').value,
        )
        self._hand_confidence_min = float(
            self.get_parameter('hand_confidence_min').value,
        )
        self._hand_loss_debounce_s = float(
            self.get_parameter('hand_loss_debounce_ms').value,
        ) / 1000.0
        self._hand_reacquire_stable_s = float(
            self.get_parameter('hand_reacquire_stable_ms').value,
        ) / 1000.0
        self._speed_limit = float(
            self.get_parameter('speed_limit').value,
        )
        diagnostics_rate_hz = float(
            self.get_parameter('diagnostics_rate_hz').value,
        )
        # 모든 축의 측정 최소/최대를 한 곳에 묶어 같은 정규화와 검증을 쓴다.
        self._axis_calibration = {
            'thumb_flex': (
                float(self.get_parameter('thumb_flex_min').value),
                float(self.get_parameter('thumb_flex_max').value),
            ),
            'thumb_opp': (
                float(self.get_parameter('thumb_opp_min').value),
                float(self.get_parameter('thumb_opp_max').value),
            ),
            'thumb_abd': (
                float(self.get_parameter('thumb_abd_min').value),
                float(self.get_parameter('thumb_abd_max').value),
            ),
            'index_flex': (
                float(self.get_parameter('index_flex_open').value),
                float(self.get_parameter('index_flex_closed').value),
            ),
            'middle_flex': (
                float(self.get_parameter('middle_flex_open').value),
                float(self.get_parameter('middle_flex_closed').value),
            ),
            'ring_flex': (
                float(self.get_parameter('ring_flex_open').value),
                float(self.get_parameter('ring_flex_closed').value),
            ),
            'little_flex': (
                float(self.get_parameter('little_flex_open').value),
                float(self.get_parameter('little_flex_closed').value),
            ),
        }
        self._validate_parameters(diagnostics_rate_hz)

        # 아래 값들은 hand-loss와 제어권 재획득을 관리하는 작은 상태 머신이다.
        # monotonic 시각은 시스템 시계가 보정되어도 timeout 계산이 역행하지 않는다.
        self._sequence = 0
        self._mimic_active = False
        self._active_owner = ControlState.OWNER_NONE
        self._hand_loss_latched = False
        self._disabled_seen_since_latch = False
        self._reacquired_stable = False
        self._last_input_valid = False
        self._last_landmark_at: Optional[float] = None
        self._valid_since: Optional[float] = None
        self._invalid_since: Optional[float] = None
        self._latest_targets: Optional[HandTargets] = None
        self._latest_target_at: Optional[float] = None
        self._filtered_targets = ZERO_TARGETS
        self._latest_confidence = 0.0
        self._last_invalid_reason = 'no landmark received'
        self._commands_published = 0
        self._calculation_failures = 0

        # ROS 연결 구성: landmarks/control_state를 구독하고, 계산한 명령과
        # 저주기 진단을 발행한다. callback에서 계산하고 timer에서 발행한다.
        self._command_publisher = self.create_publisher(
            HandCommand,
            MIMIC_COMMAND_TOPIC,
            _command_qos(),
        )
        self._diagnostics_publisher = self.create_publisher(
            DiagnosticArray,
            DIAGNOSTICS_TOPIC,
            _diagnostics_qos(),
        )
        self._landmarks_subscription = self.create_subscription(
            HandLandmarks,
            LANDMARKS_TOPIC,
            self._on_landmarks,
            _landmarks_qos(),
        )
        self._control_state_subscription = self.create_subscription(
            ControlState,
            CONTROL_STATE_TOPIC,
            self._on_control_state,
            _state_qos(),
        )
        self._publish_timer = self.create_timer(
            1.0 / self._publish_rate_hz,
            self._on_publish_timer,
        )
        self._diagnostics_timer = self.create_timer(
            1.0 / diagnostics_rate_hz,
            self._publish_diagnostics,
        )
        if self._publish_without_control_state:
            self.get_logger().warning(
                'Control-state-independent MIMIC publication is enabled; '
                'only valid, fresh right-hand targets will be published',
            )

    def _validate_parameters(self, diagnostics_rate_hz: float) -> None:
        if not math.isfinite(self._publish_rate_hz):
            raise ValueError('publish_rate_hz must be finite')
        if self._publish_rate_hz < 20.0:
            raise ValueError('publish_rate_hz must be at least 20.0')
        self._require_range('deadband', self._deadband, 0.0, 1.0)
        self._require_range(
            'low_pass_alpha',
            self._low_pass_alpha,
            _EPSILON,
            1.0,
        )
        self._require_range(
            'max_axis_delta_per_frame',
            self._max_axis_delta,
            _EPSILON,
            1.0,
        )
        self._require_range(
            'hand_confidence_min',
            self._hand_confidence_min,
            0.0,
            1.0,
        )
        self._require_range(
            'speed_limit',
            self._speed_limit,
            _EPSILON,
            1.0,
        )
        if self._hand_loss_debounce_s <= 0.0:
            raise ValueError('hand_loss_debounce_ms must be positive')
        if self._hand_reacquire_stable_s <= 0.0:
            raise ValueError('hand_reacquire_stable_ms must be positive')
        if not math.isfinite(diagnostics_rate_hz):
            raise ValueError('diagnostics_rate_hz must be finite')
        if diagnostics_rate_hz <= 0.0:
            raise ValueError('diagnostics_rate_hz must be positive')
        calibration_items = self._axis_calibration.items()
        for axis_name, (minimum, maximum) in calibration_items:
            self._require_range(
                f'{axis_name}_minimum',
                minimum,
                0.0,
                1.0,
            )
            self._require_range(
                f'{axis_name}_maximum',
                maximum,
                0.0,
                1.0,
            )
            if maximum - minimum <= _EPSILON:
                raise ValueError(
                    f'{axis_name} calibration maximum must exceed minimum',
                )

    @staticmethod
    def _require_range(
        name: str,
        value: float,
        minimum: float,
        maximum: float,
    ) -> None:
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(
                f'{name} must be in the range {minimum}..{maximum}',
            )

    def _on_landmarks(self, message: HandLandmarks) -> None:
        """Validate one MediaPipe result and update the cached hand target.

        이 callback에서는 모터 명령을 바로 publish하지 않는다. 새 표본을 일곱
        축으로 계산해 캐시에 저장하면 ``_on_publish_timer``가 정해진 주기로
        발행한다.
        """
        now = time.monotonic()
        self._last_landmark_at = now

        # 검출 성공, 오른손, confidence 기준을 먼저 확인한다. 실패한 표본은
        # 이전 목표를 새 값으로 덮지 않고 hand-loss 시간만 누적한다.
        invalid_reason = self._invalid_landmark_reason(message)
        if invalid_reason is not None:
            self._mark_input_invalid(now, invalid_reason)
            return

        try:
            # 현재는 화면 기준 일반 landmarks 21개를 사용한다. 좌표에서 먼저
            # 공통 기하학 raw 값을 계산한 뒤, 아래 단계에서 사용자 범위로 보정한다.
            raw_targets = compute_hand_targets(message.landmarks)
        except InvalidLandmarks as error:
            self._calculation_failures += 1
            self._mark_input_invalid(now, str(error))
            return
        calibrated_targets = self._calibrate_targets(raw_targets)

        self._last_input_valid = True
        self._last_invalid_reason = ''
        self._invalid_since = None
        if self._valid_since is None:
            self._valid_since = now

        # 정상 운용에서는 활성 MIMIC 제어권이 있어야 캐시를 갱신한다. 독립 발행
        # 옵션은 통합 전 비전 출력만 시험할 때 이 제어권 조건을 건너뛴다.
        if not self._publish_without_control_state:
            if self._hand_loss_latched:
                if now - self._valid_since >= self._hand_reacquire_stable_s:
                    self._reacquired_stable = True
                return

            self._reacquired_stable = False
            if not self._mimic_active:
                return

        if (
            self._publish_without_control_state
            and self._latest_target_at is None
        ):
            # 손을 다시 찾은 첫 표본은 유실 전의 오래된 자세와 섞지 않고 새로운
            # 필터 시작점으로 사용한다.
            self._filtered_targets = calibrated_targets
            self._latest_targets = calibrated_targets
        else:
            self._latest_targets = self._filter_targets(calibrated_targets)
        self._latest_target_at = now
        self._latest_confidence = _clamp01(float(message.confidence))

    def _calibrate_targets(self, raw: HandTargets) -> HandTargets:
        """Map this user's seven measured axis ranges onto the 0..1 range.

        기본 계산식은 ``(raw - minimum) / (maximum - minimum)``이며 범위를
        벗어나면 0 또는 1로 제한한다. 엄지 벌림도 같은 방향으로 정규화하여
        손바닥에 붙인 상태를 0, 최대한 벌린 상태를 1로 출력한다.
        """
        return HandTargets(
            thumb_flex=_normalize(
                raw.thumb_flex,
                *self._axis_calibration['thumb_flex'],
            ),
            thumb_opp=_normalize(
                raw.thumb_opp,
                *self._axis_calibration['thumb_opp'],
            ),
            thumb_abd=_normalize(
                raw.thumb_abd,
                *self._axis_calibration['thumb_abd'],
            ),
            index_flex=_normalize(
                raw.index_flex,
                *self._axis_calibration['index_flex'],
            ),
            middle_flex=_normalize(
                raw.middle_flex,
                *self._axis_calibration['middle_flex'],
            ),
            ring_flex=_normalize(
                raw.ring_flex,
                *self._axis_calibration['ring_flex'],
            ),
            little_flex=_normalize(
                raw.little_flex,
                *self._axis_calibration['little_flex'],
            ),
        )

    def _invalid_landmark_reason(
        self,
        message: HandLandmarks,
    ) -> Optional[str]:
        # 로봇손 모방 대상은 오른손 하나로 고정한다. confidence는 현재
        # mediapipe_node가 제공하는 handedness score를 사용한다.
        confidence = float(message.confidence)
        if not message.detected:
            return 'hand not detected'
        if message.handedness != HandLandmarks.HANDEDNESS_RIGHT:
            return 'right hand required'
        if not math.isfinite(confidence):
            return 'confidence is not finite'
        if confidence < self._hand_confidence_min:
            return 'confidence below threshold'
        return None

    def _mark_input_invalid(
        self,
        now: float,
        reason: str,
        since: Optional[float] = None,
    ) -> None:
        self._last_input_valid = False
        self._valid_since = None
        self._reacquired_stable = False
        self._last_invalid_reason = reason
        if self._invalid_since is None:
            self._invalid_since = now if since is None else since
        self._evaluate_hand_loss(now)

    def _evaluate_hand_loss(self, now: float) -> None:
        """Apply debounce and invalidate targets after a sustained hand loss.

        순간적인 한두 프레임 검출 실패에는 반응하지 않고 설정된 debounce 시간이
        지난 경우에만 캐시를 폐기한다. 정상 제어 모드에서는 재획득 절차를 강제하는
        latch도 함께 설정한다.
        """
        if self._publish_without_control_state:
            if self._invalid_since is None:
                return
            if now - self._invalid_since < self._hand_loss_debounce_s:
                return
            self._clear_target_cache()
            return

        if not self._mimic_active or self._hand_loss_latched:
            return
        if self._invalid_since is None:
            return
        if now - self._invalid_since < self._hand_loss_debounce_s:
            return

        self._hand_loss_latched = True
        self._disabled_seen_since_latch = False
        self._clear_target_cache(reset_filter=False)
        self.get_logger().error(
            'hand-loss latch set; explicit STOP and new MIMIC acquisition '
            'are required',
        )

    def _on_control_state(self, message: ControlState) -> None:
        """Track whether MIMIC mode has a live owner and handle reacquisition."""
        now = time.monotonic()
        new_mimic_active = (
            message.active_mode == ControlState.MODE_MIMIC
            and message.active_owner != ControlState.OWNER_NONE
            and message.owner_alive
        )
        entering_mimic = (
            new_mimic_active
            and (
                not self._mimic_active
                or message.active_owner != self._active_owner
            )
        )

        if (
            self._hand_loss_latched
            and message.active_mode == ControlState.MODE_DISABLED
            and message.active_owner == ControlState.OWNER_NONE
        ):
            self._disabled_seen_since_latch = True

        self._mimic_active = new_mimic_active
        self._active_owner = int(message.active_owner)

        # Debug publication deliberately ignores ownership changes.  Keep the
        # observed state for diagnostics, but never clear a fresh hand target
        # because the control manager is disabled or unavailable.
        if self._publish_without_control_state:
            return

        if entering_mimic and self._hand_loss_latched:
            if (
                self._disabled_seen_since_latch
                and self._reacquired_stable
            ):
                self._clear_hand_loss_latch(now)
            else:
                self.get_logger().warning(
                    'MIMIC acquired before hand-loss recovery conditions; '
                    'command publication remains blocked',
                )
                self._clear_target_cache(reset_filter=False)
            return

        if entering_mimic:
            self._begin_mimic_session(now)
        elif not new_mimic_active:
            self._clear_target_cache()

    def _clear_hand_loss_latch(self, now: float) -> None:
        self._hand_loss_latched = False
        self._disabled_seen_since_latch = False
        self.get_logger().info(
            'hand-loss latch cleared by new MIMIC acquisition',
        )
        self._begin_mimic_session(now)

    def _begin_mimic_session(self, now: float) -> None:
        # MIMIC 활성화 이후에 도착한 새 랜드마크를 반드시 기다린다. STOP 또는
        # 복구 전에 저장된 오래된 손 자세가 다시 발행되는 것을 막기 위해서다.
        self._clear_target_cache()
        self._last_input_valid = False
        self._valid_since = None
        self._reacquired_stable = False
        self._invalid_since = now
        self._last_invalid_reason = 'waiting for post-activation landmark'

    def _clear_target_cache(self, *, reset_filter: bool = True) -> None:
        """Forget the publishable target so an old pose cannot be replayed."""
        self._latest_targets = None
        self._latest_target_at = None
        self._latest_confidence = 0.0
        if reset_filter:
            self._filtered_targets = ZERO_TARGETS

    def _filter_targets(self, raw: HandTargets) -> HandTargets:
        """Reduce jitter and limit one-landmark-frame changes on all axes.

        각 축마다 (1) deadband, (2) 지수형 저역 통과 필터, (3) 최대 변화량 제한
        순서로 적용한다. 이 함수는 landmark callback마다 한 번 호출되므로 필터
        반응 속도는 MediaPipe 처리 FPS의 영향을 받는다.
        """
        filtered_values = []
        for previous, current in zip(
            self._filtered_targets.as_tuple(),
            raw.as_tuple(),
        ):
            if abs(current - previous) <= self._deadband:
                filtered = previous
            else:
                low_passed = previous + self._low_pass_alpha * (
                    current - previous
                )
                delta = max(
                    -self._max_axis_delta,
                    min(self._max_axis_delta, low_passed - previous),
                )
                filtered = _clamp01(previous + delta)
            filtered_values.append(filtered)

        self._filtered_targets = HandTargets(*filtered_values)
        return self._filtered_targets

    def _on_publish_timer(self) -> None:
        """Publish the latest valid target at the configured command rate."""
        now = time.monotonic()
        self._check_landmark_timeout(now)
        self._evaluate_hand_loss(now)
        if not self._can_publish(now):
            return

        # HandCommand에는 모터 위치가 아니라 일곱 개의 정규화 축과 downstream
        # 속도 제한 힌트를 담는다. sequence는 재전송/순서 검사를 위해 증가시킨다.
        command = HandCommand()
        command.stamp = self.get_clock().now().to_msg()
        self._sequence = (self._sequence + 1) & 0xFFFFFFFF
        command.sequence = self._sequence
        command.source = HandCommand.SOURCE_MIMIC
        command.thumb_flex = self._latest_targets.thumb_flex
        command.thumb_opp = self._latest_targets.thumb_opp
        command.thumb_abd = self._latest_targets.thumb_abd
        command.index_flex = self._latest_targets.index_flex
        command.middle_flex = self._latest_targets.middle_flex
        command.ring_flex = self._latest_targets.ring_flex
        command.little_flex = self._latest_targets.little_flex
        command.speed_limit = self._speed_limit
        command.confidence = self._latest_confidence
        self._command_publisher.publish(command)
        self._commands_published += 1

    def _can_publish(self, now: float) -> bool:
        """Return whether the cached target is fresh and currently allowed.

        캐시가 있더라도 마지막 유효 손 표본이 debounce보다 오래되면 폐기한다.
        독립 발행이 꺼져 있으면 MIMIC 제어권과 hand-loss latch도 함께 검사한다.
        """
        if self._latest_targets is None or self._latest_target_at is None:
            return False
        if now - self._latest_target_at >= self._hand_loss_debounce_s:
            self._clear_target_cache()
            return False
        if self._publish_without_control_state:
            return True
        return self._mimic_active and not self._hand_loss_latched

    def _check_landmark_timeout(self, now: float) -> None:
        if (
            not self._publish_without_control_state
            and not self._mimic_active
        ):
            return
        if self._last_landmark_at is None:
            self._mark_input_invalid(
                now,
                'no landmark received',
                since=self._invalid_since,
            )
            return
        if now - self._last_landmark_at >= self._hand_loss_debounce_s:
            self._mark_input_invalid(
                now,
                'landmark stream timed out',
                since=self._last_landmark_at,
            )

    def _publish_diagnostics(self) -> None:
        """Publish low-rate observability without affecting control output.

        입력/목표 age, MIMIC 활성 여부, 손 유실 latch, 계산 실패 횟수를 제공해
        토픽이 보이지 않거나 명령이 멈춘 원인을 다른 노드에서 확인할 수 있게 한다.
        """
        now = time.monotonic()
        if self._last_landmark_at is None:
            input_age = 'unavailable'
        else:
            input_age = f'{(now - self._last_landmark_at) * 1000.0:.3f}'
        if self._latest_target_at is None:
            target_age = 'unavailable'
        else:
            target_age = f'{(now - self._latest_target_at) * 1000.0:.3f}'

        status = DiagnosticStatus()
        status.name = 'thing_vision/hand_target_node'
        status.hardware_id = 'jetson'
        if self._publish_without_control_state:
            if self._latest_targets is None or not self._last_input_valid:
                status.level = DiagnosticStatus.WARN
                status.message = self._last_invalid_reason
            else:
                status.level = DiagnosticStatus.OK
                status.message = (
                    'publishing MIMIC hand targets without control state'
                )
        elif self._hand_loss_latched:
            status.level = DiagnosticStatus.ERROR
            status.message = 'hand-loss latch active; explicit recovery needed'
        elif self._mimic_active and not self._last_input_valid:
            status.level = DiagnosticStatus.WARN
            status.message = self._last_invalid_reason
        elif self._mimic_active:
            status.level = DiagnosticStatus.OK
            status.message = 'publishing MIMIC hand targets'
        else:
            status.level = DiagnosticStatus.OK
            status.message = 'waiting for active MIMIC mode'

        status.values = [
            KeyValue(key='input_topic', value=LANDMARKS_TOPIC),
            KeyValue(key='output_topic', value=MIMIC_COMMAND_TOPIC),
            KeyValue(
                key='publish_without_control_state',
                value=str(
                    self._publish_without_control_state,
                ).lower(),
            ),
            KeyValue(
                key='mimic_active',
                value=str(self._mimic_active).lower(),
            ),
            KeyValue(
                key='hand_input_valid',
                value=str(self._last_input_valid).lower(),
            ),
            KeyValue(
                key='hand_loss_latched',
                value=str(self._hand_loss_latched).lower(),
            ),
            KeyValue(
                key='reacquired_stable',
                value=str(self._reacquired_stable).lower(),
            ),
            KeyValue(key='input_age_ms', value=input_age),
            KeyValue(key='target_age_ms', value=target_age),
            KeyValue(
                key='commands_published',
                value=str(self._commands_published),
            ),
            KeyValue(
                key='calculation_failures',
                value=str(self._calculation_failures),
            ),
            KeyValue(
                key='last_invalid_reason',
                value=self._last_invalid_reason,
            ),
        ]
        diagnostics = DiagnosticArray()
        diagnostics.header.stamp = self.get_clock().now().to_msg()
        diagnostics.status = [status]
        self._diagnostics_publisher.publish(diagnostics)


def main(args: Optional[list[str]] = None) -> None:
    """Run the ROS 2 hand-target node."""
    rclpy.init(args=args)
    node: Optional[HandTargetNode] = None
    try:
        node = HandTargetNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
