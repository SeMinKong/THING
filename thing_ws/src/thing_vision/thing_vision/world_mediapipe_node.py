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

"""Publish both screen and world hand landmarks from one MediaPipe pass.

기존 ``mediapipe_node``는 MJPEG overlay에 필요한 화면 정규화 좌표만
``/thing/landmarks``로 발행한다. 이 파생 노드는 기존 동작을 보존하면서 같은
추론 결과의 ``multi_hand_world_landmarks``를 ``/thing/world_landmarks``에도
발행한다.

두 토픽 모두 기존 ``HandLandmarks`` 메시지를 사용하지만 좌표 의미가 다르다.

* ``/thing/landmarks``: 화면 표시용 normalized x/y 및 상대 z
* ``/thing/world_landmarks``: 손 중심 기준 미터 단위 x/y/z

launch 파일은 MJPEG에는 첫 번째 토픽을 유지하고, ``hand_target_node``의 입력만
두 번째 토픽으로 remap한다. 따라서 MediaPipe 추론을 두 번 실행하지 않는다.
"""

from collections.abc import Callable
from typing import Any, Optional

from cv_bridge import CvBridge
from geometry_msgs.msg import Point32
import rclpy
from rclpy.executors import ExternalShutdownException
from sensor_msgs.msg import Image
from thing_interfaces.msg import HandLandmarks

from thing_vision.mediapipe_node import LANDMARK_COUNT
from thing_vision.mediapipe_node import MediaPipeNode
from thing_vision.mediapipe_node import _bounded_confidence
from thing_vision.mediapipe_node import _sensor_data_qos


WORLD_LANDMARKS_TOPIC = '/thing/world_landmarks'


class WorldMediaPipeNode(MediaPipeNode):
    """Extend the existing MediaPipe node with a world-coordinate output."""

    def __init__(
        self,
        hands_factory: Optional[Callable[..., Any]] = None,
        bridge: Optional[CvBridge] = None,
    ) -> None:
        """Initialize the original pipeline and the additional publisher."""
        self._pending_world_output: Optional[HandLandmarks] = None
        super().__init__(hands_factory=hands_factory, bridge=bridge)
        self._world_landmarks_publisher = self.create_publisher(
            HandLandmarks,
            WORLD_LANDMARKS_TOPIC,
            _sensor_data_qos(),
        )
        self.get_logger().info(
            'World landmarks enabled: normal coordinates -> '
            '/thing/landmarks, world coordinates -> '
            f'{WORLD_LANDMARKS_TOPIC}',
        )

    def _process_image(self, image_message: Image) -> None:
        """Run one inference and publish its normal and world coordinates."""
        # 부모의 처리 중 손을 못 찾거나 예외가 나더라도 world 구독자에게
        # detected=false를 전달할 수 있도록 기본 메시지를 먼저 준비한다.
        self._pending_world_output = self._empty_landmarks_message(
            image_message,
        )
        super()._process_image(image_message)
        self._world_landmarks_publisher.publish(
            self._pending_world_output,
        )

    def _select_hand(
        self,
        result: Any,
    ) -> Optional[tuple[Any, str, float]]:
        """Select one hand that has both normal and world landmark sets."""
        normal_sets = list(
            getattr(result, 'multi_hand_landmarks', None) or [],
        )
        world_sets = list(
            getattr(result, 'multi_hand_world_landmarks', None) or [],
        )
        handedness_sets = list(
            getattr(result, 'multi_handedness', None) or [],
        )

        candidates: list[tuple[Any, str, float]] = []
        for index, normal_set in enumerate(normal_sets):
            if index >= len(world_sets):
                continue

            normal_points = list(
                getattr(normal_set, 'landmark', []),
            )
            world_points = list(
                getattr(world_sets[index], 'landmark', []),
            )
            if (
                len(normal_points) != LANDMARK_COUNT
                or len(world_points) != LANDMARK_COUNT
            ):
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

            # 같은 인덱스의 두 좌표 세트를 한 후보로 묶어 handedness 점수로
            # 선택한다. 이래야 overlay와 제어가 서로 다른 손을 보지 않는다.
            candidates.append(
                ((normal_points, world_points), label, score),
            )

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
        """Fill synchronized normal and world messages for the selected hand."""
        normal_points, world_points = landmarks

        # 기존 메시지는 그대로 화면 좌표로 채워 MJPEG/Web 계약을 보존한다.
        super()._fill_detected_message(
            output,
            normal_points,
            label,
            score,
        )

        # world 메시지는 동일한 header와 손 판정을 사용하고 landmarks 필드의
        # 좌표만 미터 단위 world 좌표로 채운다.
        world_output = HandLandmarks()
        world_output.header = output.header
        world_output.detected = False
        world_output.confidence = 0.0
        world_output.handedness = HandLandmarks.HANDEDNESS_UNKNOWN
        world_output.handedness_confidence = 0.0
        world_output.image_width = output.image_width
        world_output.image_height = output.image_height
        world_output.landmarks = [
            Point32() for _ in range(LANDMARK_COUNT)
        ]
        super()._fill_detected_message(
            world_output,
            world_points,
            label,
            score,
        )
        self._pending_world_output = world_output


def main(args: Optional[list[str]] = None) -> None:
    """Run the dual-output MediaPipe node."""
    rclpy.init(args=args)
    node: Optional[WorldMediaPipeNode] = None
    try:
        node = WorldMediaPipeNode()
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
