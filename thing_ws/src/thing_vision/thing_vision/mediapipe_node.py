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

"""ROS 2 카메라 영상에서 한 손의 MediaPipe 랜드마크를 발행한다.

처리 흐름은 다음과 같다.

1. ``/thing/image_raw``의 ``sensor_msgs/Image``를 구독한다.
2. CvBridge로 ROS 영상을 OpenCV BGR 배열로 변환한다.
3. 추론용 크기로 축소하고 RGB로 바꾼 뒤 MediaPipe Hands에 전달한다.
4. 검출된 손 중 handedness 점수가 가장 높은 한 손을 선택한다.
5. 화면 기준의 일반 랜드마크 21개를 ``/thing/landmarks``로 발행한다.

이 노드는 ``multi_hand_world_landmarks``가 아닌
``multi_hand_landmarks``를 사용한다. 따라서 x, y는 영상 크기에 대해
정규화된 화면 좌표이고 z는 손목을 기준으로 MediaPipe가 추정한 상대 깊이다.
실제 거리 단위의 3차원 world 좌표가 아니다.
"""

from collections.abc import Callable
import math
import time
from typing import Any, Optional

import cv2
from cv_bridge import CvBridge
from diagnostic_msgs.msg import DiagnosticArray
from diagnostic_msgs.msg import DiagnosticStatus
from diagnostic_msgs.msg import KeyValue
from geometry_msgs.msg import Point32
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy
from rclpy.qos import HistoryPolicy
from rclpy.qos import QoSProfile
from rclpy.qos import ReliabilityPolicy
from sensor_msgs.msg import Image
from thing_interfaces.msg import HandLandmarks


IMAGE_TOPIC = '/thing/image_raw'
LANDMARKS_TOPIC = '/thing/landmarks'
DIAGNOSTICS_TOPIC = '/thing/diagnostics'
# MediaPipe Hands가 손목(0)부터 소지 끝(20)까지 제공하는 고정 점 개수다.
LANDMARK_COUNT = 21


def _sensor_data_qos() -> QoSProfile:
    """영상 파이프라인에서 최신 샘플만 전달하는 QoS를 만든다.

    영상은 오래된 프레임을 재전송하는 것보다 최신 프레임을 빠르게 처리하는
    것이 중요하다. 그래서 depth=1과 BEST_EFFORT를 사용해 지연 누적을 막는다.
    카메라 발행자, 이 노드의 구독자, 랜드마크 구독자는 호환되는 sensor-data
    QoS를 사용해야 한다.
    """
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE,
    )


def _diagnostics_qos() -> QoSProfile:
    """낮은 주기로 발행하는 상태 진단용 RELIABLE QoS를 만든다."""
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
    )


def _create_mediapipe_hands(**options: Any) -> Any:
    """고정 버전의 MediaPipe legacy Hands 백엔드를 지연 생성한다.

    모듈을 함수 안에서 import하여 MediaPipe가 없는 환경에서도 파일 자체는
    import할 수 있게 하고, 설치 문제는 노드 생성 시 이해하기 쉬운 오류로
    바꾼다. ``hands_factory``를 주입하는 단위 테스트에서도 이 함수 대신
    가짜 추론기를 사용할 수 있다.
    """
    try:
        import mediapipe as mp
    except ImportError as error:
        raise RuntimeError(
            'MediaPipe is not installed. Activate the project virtual '
            'environment and install mediapipe==0.10.9.',
        ) from error

    try:
        hands_class = mp.solutions.hands.Hands
    except AttributeError as error:
        raise RuntimeError(
            'The installed MediaPipe package does not provide the legacy '
            'Hands API. Install the project-pinned mediapipe==0.10.9.',
        ) from error

    return hands_class(**options)


def _bounded_confidence(value: Any) -> float:
    """외부 추론 결과의 점수를 안전한 유한 실수 [0, 1]로 제한한다."""
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(confidence):
        return 0.0
    return min(max(confidence, 0.0), 1.0)


class MediaPipeNode(Node):
    """카메라 영상을 순서가 고정된 한 손의 랜드마크로 변환한다.

    MediaPipe의 21개 점 순서는 항상 동일하므로 이후 노드가 번호만으로 손목,
    엄지, 검지 등의 관절을 찾을 수 있다. 이 노드는 관절 각도나 로봇 명령을
    계산하지 않고, 추론 결과를 ROS 메시지 형태로 전달하는 역할만 맡는다.
    """

    def __init__(
        self,
        hands_factory: Optional[Callable[..., Any]] = None,
        bridge: Optional[CvBridge] = None,
    ) -> None:
        """파라미터, MediaPipe, ROS 입출력과 진단 지표를 초기화한다."""
        super().__init__('mediapipe_node')

        # Hands 모델 동작과 정확도/속도 균형을 조절하는 파라미터다.
        # model_complexity=0은 Jetson CPU에서 지연을 줄이는 경량 모델이다.
        self.declare_parameter('max_num_hands', 1)
        self.declare_parameter('model_complexity', 0)
        self.declare_parameter('min_detection_confidence', 0.6)
        self.declare_parameter('min_tracking_confidence', 0.6)
        # MediaPipe handedness는 기본적으로 거울처럼 반전된 셀피 영상을
        # 가정하므로, 원본 카메라 입력이면 아래에서 좌/우 판정을 뒤집는다.
        self.declare_parameter('input_is_mirrored', False)
        # 원본 발행 해상도와 별개로 MediaPipe 추론에만 사용할 축소 크기다.
        self.declare_parameter('inference_width', 320)
        self.declare_parameter('inference_height', 240)
        self.declare_parameter('input_timeout_ms', 1000)
        self.declare_parameter('diagnostics_rate_hz', 1.0)

        self._max_num_hands = int(
            self.get_parameter('max_num_hands').value,
        )
        self._model_complexity = int(
            self.get_parameter('model_complexity').value,
        )
        self._min_detection_confidence = float(
            self.get_parameter('min_detection_confidence').value,
        )
        self._min_tracking_confidence = float(
            self.get_parameter('min_tracking_confidence').value,
        )
        self._input_is_mirrored = bool(
            self.get_parameter('input_is_mirrored').value,
        )

        self._inference_width = int(
            self.get_parameter('inference_width').value,
        )
        self._inference_height = int(
            self.get_parameter('inference_height').value,
        )

        self._input_timeout_ms = int(
            self.get_parameter('input_timeout_ms').value,
        )
        self._diagnostics_rate_hz = float(
            self.get_parameter('diagnostics_rate_hz').value,
        )
        self._validate_parameters()

        # CvBridge는 sensor_msgs/Image와 OpenCV ndarray 사이를 변환한다.
        self._bridge = bridge or CvBridge()
        factory = hands_factory or _create_mediapipe_hands
        self._hands = factory(
            # 비디오 모드에서는 매 프레임 손바닥을 새로 검출하지 않는다.
            # 첫 검출 뒤에는 이전 랜드마크를 추적하고, 추적이 실패하면 다시
            # 검출하므로 static_image_mode=True보다 연속 영상 처리에 빠르다.
            static_image_mode=False,
            max_num_hands=self._max_num_hands,
            model_complexity=self._model_complexity,
            min_detection_confidence=(
                self._min_detection_confidence
            ),
            min_tracking_confidence=(
                self._min_tracking_confidence
            ),
        )

        # ROS 시간은 시뮬레이션/동기화 용도로 바뀔 수 있으므로 처리 시간과
        # timeout 측정에는 역행하지 않는 monotonic clock을 사용한다.
        self._frames_received = 0
        self._frames_processed = 0
        self._frames_since_diagnostic = 0
        self._hands_detected = 0
        self._processing_failures = 0
        self._last_image_ns: Optional[int] = None
        self._last_success_ns: Optional[int] = None
        self._last_inference_ms = 0.0
        self._last_error_log_ns = 0
        self._last_diagnostic_ns = time.monotonic_ns()
        self._diagnostic_message = 'waiting for camera images'

        # 영상과 랜드마크에는 같은 최신 샘플 우선 QoS를 사용한다. 처리 속도가
        # 카메라 FPS보다 느려져도 큐에 과거 영상이 쌓이지 않는다.
        self._landmarks_publisher = self.create_publisher(
            HandLandmarks,
            LANDMARKS_TOPIC,
            _sensor_data_qos(),
        )
        # 진단은 저주기 정보이므로 유실을 줄이기 위해 RELIABLE로 발행한다.
        self._diagnostics_publisher = self.create_publisher(
            DiagnosticArray,
            DIAGNOSTICS_TOPIC,
            _diagnostics_qos(),
        )
        self._image_subscription = self.create_subscription(
            Image,
            IMAGE_TOPIC,
            self._process_image,
            _sensor_data_qos(),
        )
        self._diagnostics_timer = self.create_timer(
            1.0 / self._diagnostics_rate_hz,
            self._publish_diagnostics,
        )

        mirror_policy = (
            'using MediaPipe handedness as-is'
            if self._input_is_mirrored
            else 'swapping MediaPipe handedness for raw camera input'
        )
        self.get_logger().info(
            'MediaPipe Hands ready: '
            f'model_complexity={self._model_complexity}, '
            f'max_num_hands={self._max_num_hands}; '
            f'{mirror_policy}',
        )
        self.get_logger().warning(
            'HandLandmarks.confidence uses the MediaPipe handedness score '
            'because the legacy Hands API does not expose a per-result '
            'detection score.',
        )

    def _validate_parameters(self) -> None:
        """잘못된 설정을 추론 시작 전에 발견해 명확한 오류로 중단한다."""
        if self._max_num_hands <= 0:
            raise ValueError('max_num_hands must be greater than zero')
        if self._model_complexity not in (0, 1):
            raise ValueError('model_complexity must be 0 or 1')
        for name, value in (
            (
                'min_detection_confidence',
                self._min_detection_confidence,
            ),
            (
                'min_tracking_confidence',
                self._min_tracking_confidence,
            ),
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f'{name} must be between 0.0 and 1.0')
        if self._input_timeout_ms < 100:
            raise ValueError('input_timeout_ms must be at least 100')
        if (
            not math.isfinite(self._diagnostics_rate_hz)
            or self._diagnostics_rate_hz <= 0.0
        ):
            raise ValueError(
                'diagnostics_rate_hz must be greater than zero',
            )
        if (
            self._inference_width <= 0
            or self._inference_height <= 0
        ):
            raise ValueError(
                'inference_width and inference_height '
                'must be greater than zero',
            )

    def _process_image(self, image_message: Image) -> None:
        """카메라 프레임 하나를 추론하고 결과 메시지를 항상 한 번 발행한다."""
        now_ns = time.monotonic_ns()
        self._frames_received += 1
        self._last_image_ns = now_ns

        # 손 미검출이나 처리 오류도 downstream 노드에 즉시 알려야 한다.
        # 먼저 detected=false인 고정 크기 메시지를 만들고 성공 시 덮어쓴다.
        output = self._empty_landmarks_message(image_message)
        try:
            # ROS Image의 encoding/stride 처리는 CvBridge에 맡기고, OpenCV가
            # 일반적으로 사용하는 BGR 8-bit 3채널 배열로 통일한다.
            bgr_image = self._bridge.imgmsg_to_cv2(
                image_message,
                desired_encoding='bgr8',
            )
            if (
                bgr_image is None
                or bgr_image.ndim != 3
                or bgr_image.shape[2] != 3
                or bgr_image.size == 0
            ):
                raise ValueError('converted image is not a non-empty BGR8')

            # 출력 메시지에는 추론용 축소 크기가 아니라 원본 영상 크기를
            # 기록한다. 화면 overlay가 원본 픽셀 좌표로 복원할 때 사용된다.
            output.image_height = int(bgr_image.shape[0])
            output.image_width = int(bgr_image.shape[1])
            if (
                bgr_image.shape[1] != self._inference_width
                or bgr_image.shape[0] != self._inference_height
            ):
                inference_image = cv2.resize(
                    bgr_image,
                    (
                        self._inference_width,
                        self._inference_height,
                    ),
                    interpolation=cv2.INTER_AREA,
                )
            else:
                inference_image = bgr_image

            # OpenCV/CvBridge는 BGR이지만 MediaPipe Hands는 RGB를 입력받는다.
            rgb_image = cv2.cvtColor(
                inference_image,
                cv2.COLOR_BGR2RGB,
            )
            # MediaPipe가 입력을 수정할 필요가 없다고 알려 불필요한 복사를
            # 줄인다. process()가 끝난 뒤 이 배열은 다시 사용하지 않는다.
            rgb_image.flags.writeable = False

            inference_start_ns = time.monotonic_ns()
            # 내부적으로 초기 palm detector가 손 영역을 찾고, 이어서 21개
            # landmark 모델과 프레임 간 tracker가 관절 위치를 갱신한다.
            result = self._hands.process(rgb_image)
            inference_end_ns = time.monotonic_ns()
            self._last_inference_ms = (
                inference_end_ns - inference_start_ns
            ) / 1_000_000

            # 여러 손이 반환될 수 있어도 현재 인터페이스는 한 손만 전달한다.
            # 아래 함수가 handedness 점수가 가장 높은 유효 후보를 선택한다.
            candidate = self._select_hand(result)
            if candidate is not None:
                landmarks, label, score = candidate
                self._fill_detected_message(
                    output,
                    landmarks,
                    label,
                    score,
                )
                self._hands_detected += 1
                self._diagnostic_message = 'hand detected'
            else:
                self._diagnostic_message = 'no hand detected'

            self._frames_processed += 1
            self._frames_since_diagnostic += 1
            self._last_success_ns = inference_end_ns
        except Exception as error:  # Keep one bad frame from killing safety.
            # 한 프레임의 변환/추론 오류로 노드를 종료하지 않는다. 미검출
            # 메시지를 발행해 제어 노드가 이전 손 명령을 계속 쓰지 않게 한다.
            self._processing_failures += 1
            self._diagnostic_message = (
                f'image processing failed: {type(error).__name__}'
            )
            self._log_processing_error(error, now_ns)

        # 성공, 미검출, 오류 모두 입력 프레임당 결과 한 개를 발행한다.
        self._landmarks_publisher.publish(output)

    def _empty_landmarks_message(
        self,
        image_message: Image,
    ) -> HandLandmarks:
        """입력 header를 유지한 detected=false 기본 메시지를 만든다.

        랜드마크 배열은 손을 못 찾은 경우에도 항상 21개를 채운다. 구독자는
        배열 길이보다 ``detected``를 먼저 확인하면 고정된 메시지 구조로
        안전하게 처리할 수 있다.
        """
        output = HandLandmarks()
        output.header = image_message.header
        output.detected = False
        output.confidence = 0.0
        output.handedness = HandLandmarks.HANDEDNESS_UNKNOWN
        output.handedness_confidence = 0.0
        output.image_width = int(image_message.width)
        output.image_height = int(image_message.height)
        output.landmarks = [Point32() for _ in range(LANDMARK_COUNT)]
        return output

    def _select_hand(
        self,
        result: Any,
    ) -> Optional[tuple[Any, str, float]]:
        """21개 점이 있는 후보 중 handedness 점수가 가장 높은 손을 고른다.

        ``multi_hand_landmarks``와 ``multi_handedness``는 같은 인덱스끼리 같은
        손을 나타낸다. 여기서는 화면 좌표인 ``multi_hand_landmarks``만 꺼내며
        ``multi_hand_world_landmarks``는 사용하지 않는다.
        """
        landmark_sets = list(
            getattr(result, 'multi_hand_landmarks', None) or [],
        )
        handedness_sets = list(
            getattr(result, 'multi_handedness', None) or [],
        )

        candidates: list[tuple[Any, str, float]] = []
        for index, landmark_set in enumerate(landmark_sets):
            points = list(getattr(landmark_set, 'landmark', []))
            # 부분 결과는 인덱스 의미가 깨지므로 후보에서 제외한다.
            if len(points) != LANDMARK_COUNT:
                continue

            # legacy Hands API는 결과별 detection confidence를 노출하지 않는다.
            # 여기의 score는 Left/Right 분류(handedness)의 확신도다.
            label = ''
            score = 0.0
            if index < len(handedness_sets):
                classifications = list(
                    getattr(
                        handedness_sets[index],
                        'classification',
                        [],
                    ),
                )
                if classifications:
                    label = str(
                        getattr(classifications[0], 'label', ''),
                    )
                    score = _bounded_confidence(
                        getattr(classifications[0], 'score', 0.0),
                    )
            candidates.append((points, label, score))

        if not candidates:
            return None
        # max_num_hands가 1보다 커도 출력 인터페이스는 최고 점수 한 손만 쓴다.
        return max(candidates, key=lambda candidate: candidate[2])

    def _fill_detected_message(
        self,
        output: HandLandmarks,
        landmarks: Any,
        label: str,
        score: float,
    ) -> None:
        """선택한 일반 랜드마크와 좌/우 정보를 ROS 메시지에 채운다.

        일반 랜드마크의 x, y는 대체로 [0, 1]인 영상 정규화 좌표이며 z는
        손목을 원점으로 한 상대 깊이 추정값이다. 이 좌표는 미터 단위의
        world landmark가 아니므로 화면 표시에는 적합하지만 손 방향에 따른
        3차원 각도 오차가 생길 수 있다.
        """
        points = []
        for landmark in landmarks:
            x = float(landmark.x)
            y = float(landmark.y)
            z = float(landmark.z)
            if not all(math.isfinite(value) for value in (x, y, z)):
                raise ValueError('MediaPipe returned a non-finite landmark')
            points.append(Point32(x=x, y=y, z=z))

        output.detected = True
        # 호환성을 위해 confidence에도 handedness score를 넣는다. 실제 손
        # 검출 confidence와 혼동하지 않도록 diagnostics에도 출처를 표시한다.
        output.confidence = score
        output.handedness = self._handedness_value(label)
        output.handedness_confidence = score
        output.landmarks = points

    def _handedness_value(self, label: str) -> int:
        """MediaPipe의 문자열 좌/우 판정을 프로젝트 메시지 상수로 바꾼다.

        MediaPipe handedness는 셀피처럼 좌우 반전된 입력을 가정한다. 실제
        카메라 원본이 반전되지 않았다면 Left/Right를 서로 바꿔야 사용자의
        실제 손과 일치한다.
        """
        normalized = label.strip().lower()
        if not self._input_is_mirrored:
            if normalized == 'left':
                normalized = 'right'
            elif normalized == 'right':
                normalized = 'left'

        if normalized == 'left':
            return HandLandmarks.HANDEDNESS_LEFT
        if normalized == 'right':
            return HandLandmarks.HANDEDNESS_RIGHT
        return HandLandmarks.HANDEDNESS_UNKNOWN

    def _log_processing_error(
        self,
        error: Exception,
        now_ns: int,
    ) -> None:
        """연속 오류가 로그를 잠식하지 않도록 최대 1 Hz로 기록한다."""
        if now_ns - self._last_error_log_ns < 1_000_000_000:
            return
        self._last_error_log_ns = now_ns
        self.get_logger().error(
            'Failed to process camera image; publishing detected=false: '
            f'{error}',
        )

    def _publish_diagnostics(self) -> None:
        """입력 timeout, 추론 속도, 검출률과 오류 횟수를 진단 토픽에 낸다."""
        now_ns = time.monotonic_ns()
        elapsed_seconds = max(
            (now_ns - self._last_diagnostic_ns) / 1_000_000_000,
            1e-9,
        )
        measured_fps = (
            self._frames_since_diagnostic / elapsed_seconds
        )
        input_age_ms = (
            -1.0
            if self._last_image_ns is None
            else (now_ns - self._last_image_ns) / 1_000_000
        )

        # 카메라 입력이 없거나 마지막 입력을 정상 처리하지 못했다면
        # diagnostics level을 올려 운영자가 원인을 구분할 수 있게 한다.
        if self._last_image_ns is None:
            level = DiagnosticStatus.WARN
        elif input_age_ms > self._input_timeout_ms:
            level = DiagnosticStatus.ERROR
            self._diagnostic_message = 'camera image input timed out'
        elif (
            self._last_success_ns is None
            or self._last_success_ns < self._last_image_ns
        ):
            level = DiagnosticStatus.ERROR
        else:
            level = DiagnosticStatus.OK

        detection_rate = (
            self._hands_detected / self._frames_processed
            if self._frames_processed
            else 0.0
        )
        status = DiagnosticStatus()
        status.level = level
        status.name = 'thing_vision/mediapipe_node'
        status.hardware_id = 'mediapipe:cpu'
        status.message = self._diagnostic_message
        # confidence_source를 명시해 구독자가 confidence를 검출 확률로
        # 잘못 해석하지 않도록 한다.
        status.values = [
            KeyValue(
                key='input_topic',
                value=IMAGE_TOPIC,
            ),
            KeyValue(
                key='output_topic',
                value=LANDMARKS_TOPIC,
            ),
            KeyValue(
                key='model_complexity',
                value=str(self._model_complexity),
            ),
            KeyValue(
                key='input_is_mirrored',
                value=str(self._input_is_mirrored).lower(),
            ),
            KeyValue(
                key='confidence_source',
                value='mediapipe_handedness_score',
            ),
            KeyValue(
                key='measured_processing_fps',
                value=f'{measured_fps:.3f}',
            ),
            KeyValue(
                key='last_inference_ms',
                value=f'{self._last_inference_ms:.3f}',
            ),
            KeyValue(
                key='input_age_ms',
                value=f'{input_age_ms:.3f}',
            ),
            KeyValue(
                key='frames_received',
                value=str(self._frames_received),
            ),
            KeyValue(
                key='frames_processed',
                value=str(self._frames_processed),
            ),
            KeyValue(
                key='hands_detected',
                value=str(self._hands_detected),
            ),
            KeyValue(
                key='detection_rate',
                value=f'{detection_rate:.3f}',
            ),
            KeyValue(
                key='processing_failures',
                value=str(self._processing_failures),
            ),
        ]

        diagnostics = DiagnosticArray()
        diagnostics.header.stamp = self.get_clock().now().to_msg()
        diagnostics.status = [status]
        self._diagnostics_publisher.publish(diagnostics)

        self._frames_since_diagnostic = 0
        self._last_diagnostic_ns = now_ns

    def destroy_node(self) -> None:
        """ROS 노드를 제거하기 전에 MediaPipe 네이티브 자원을 닫는다."""
        if self._hands is not None:
            self._hands.close()
            self._hands = None
        super().destroy_node()


def main(args: Optional[list[str]] = None) -> None:
    """ROS 2를 초기화하고 종료 요청까지 MediaPipe 노드를 실행한다."""
    rclpy.init(args=args)
    node: Optional[MediaPipeNode] = None
    try:
        node = MediaPipeNode()
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
