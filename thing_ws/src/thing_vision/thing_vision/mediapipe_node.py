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

"""Detect one hand in ROS 2 images and publish MediaPipe landmarks."""

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
LANDMARK_COUNT = 21


def _sensor_data_qos() -> QoSProfile:
    """Return the latest-sample-only QoS for the vision pipeline."""
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE,
    )


def _diagnostics_qos() -> QoSProfile:
    """Return the reliable QoS used for low-rate diagnostics."""
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
    )


def _create_mediapipe_hands(**options: Any) -> Any:
    """Create the pinned legacy MediaPipe Hands backend lazily."""
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
    """Convert a confidence-like value to a finite value in [0, 1]."""
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(confidence):
        return 0.0
    return min(max(confidence, 0.0), 1.0)


class MediaPipeNode(Node):
    """Convert camera images into one fixed-order set of hand landmarks."""

    def __init__(
        self,
        hands_factory: Optional[Callable[..., Any]] = None,
        bridge: Optional[CvBridge] = None,
    ) -> None:
        """Initialize parameters, MediaPipe, ROS interfaces, and metrics."""
        super().__init__('mediapipe_node')

        self.declare_parameter('max_num_hands', 1)
        self.declare_parameter('model_complexity', 0)
        self.declare_parameter('min_detection_confidence', 0.6)
        self.declare_parameter('min_tracking_confidence', 0.6)
        self.declare_parameter('input_is_mirrored', False)
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
        self._input_timeout_ms = int(
            self.get_parameter('input_timeout_ms').value,
        )
        self._diagnostics_rate_hz = float(
            self.get_parameter('diagnostics_rate_hz').value,
        )
        self._validate_parameters()

        self._bridge = bridge or CvBridge()
        factory = hands_factory or _create_mediapipe_hands
        self._hands = factory(
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

        self._landmarks_publisher = self.create_publisher(
            HandLandmarks,
            LANDMARKS_TOPIC,
            _sensor_data_qos(),
        )
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

    def _process_image(self, image_message: Image) -> None:
        now_ns = time.monotonic_ns()
        self._frames_received += 1
        self._last_image_ns = now_ns

        output = self._empty_landmarks_message(image_message)
        try:
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

            output.image_height = int(bgr_image.shape[0])
            output.image_width = int(bgr_image.shape[1])
            rgb_image = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)
            rgb_image.flags.writeable = False

            inference_start_ns = time.monotonic_ns()
            result = self._hands.process(rgb_image)
            inference_end_ns = time.monotonic_ns()
            self._last_inference_ms = (
                inference_end_ns - inference_start_ns
            ) / 1_000_000

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
            self._processing_failures += 1
            self._diagnostic_message = (
                f'image processing failed: {type(error).__name__}'
            )
            self._log_processing_error(error, now_ns)

        self._landmarks_publisher.publish(output)

    def _empty_landmarks_message(
        self,
        image_message: Image,
    ) -> HandLandmarks:
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
        landmark_sets = list(
            getattr(result, 'multi_hand_landmarks', None) or [],
        )
        handedness_sets = list(
            getattr(result, 'multi_handedness', None) or [],
        )

        candidates: list[tuple[Any, str, float]] = []
        for index, landmark_set in enumerate(landmark_sets):
            points = list(getattr(landmark_set, 'landmark', []))
            if len(points) != LANDMARK_COUNT:
                continue

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
        return max(candidates, key=lambda candidate: candidate[2])

    def _fill_detected_message(
        self,
        output: HandLandmarks,
        landmarks: Any,
        label: str,
        score: float,
    ) -> None:
        points = []
        for landmark in landmarks:
            x = float(landmark.x)
            y = float(landmark.y)
            z = float(landmark.z)
            if not all(math.isfinite(value) for value in (x, y, z)):
                raise ValueError('MediaPipe returned a non-finite landmark')
            points.append(Point32(x=x, y=y, z=z))

        output.detected = True
        output.confidence = score
        output.handedness = self._handedness_value(label)
        output.handedness_confidence = score
        output.landmarks = points

    def _handedness_value(self, label: str) -> int:
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
        if now_ns - self._last_error_log_ns < 1_000_000_000:
            return
        self._last_error_log_ns = now_ns
        self.get_logger().error(
            'Failed to process camera image; publishing detected=false: '
            f'{error}',
        )

    def _publish_diagnostics(self) -> None:
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
        """Close MediaPipe resources before destroying the ROS node."""
        if self._hands is not None:
            self._hands.close()
            self._hands = None
        super().destroy_node()


def main(args: Optional[list[str]] = None) -> None:
    """Run the ROS 2 MediaPipe node."""
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
