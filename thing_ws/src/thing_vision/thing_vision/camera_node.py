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

"""USB 카메라 영상을 ROS 2 이미지로 전달한다.

이 노드는 OpenCV로 최신 카메라 프레임을 읽고, CvBridge를 이용해
``sensor_msgs/Image``로 변환한 뒤 ``/thing/image_raw``에 발행한다.
카메라 연결 상태와 실제 발행 FPS는 ``/thing/diagnostics``에 함께
발행한다. ROS 구독 토픽은 없으며 입력은 물리 카메라다. 이 파일에는
MediaPipe 추론이나 손가락 제어 로직이 없다.
"""

from collections.abc import Callable
import time
from typing import Any, Optional

import cv2
from cv_bridge import CvBridge
from diagnostic_msgs.msg import DiagnosticArray
from diagnostic_msgs.msg import DiagnosticStatus
from diagnostic_msgs.msg import KeyValue
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy
from rclpy.qos import HistoryPolicy
from rclpy.qos import QoSProfile
from rclpy.qos import ReliabilityPolicy
from sensor_msgs.msg import Image


# 다른 비전 노드가 구독하는 원본 컬러 영상 토픽이다.
IMAGE_TOPIC = '/thing/image_raw'
# 여러 노드가 같은 토픽에 각자의 DiagnosticStatus를 발행한다.
DIAGNOSTICS_TOPIC = '/thing/diagnostics'
# OpenCV의 기본 컬러 채널 순서(B-G-R)를 ROS 메시지에도 명시한다.
IMAGE_ENCODING = 'bgr8'


def _image_qos() -> QoSProfile:
    """지연 누적을 막는 카메라 영상용 QoS를 만든다.

    영상은 과거 프레임을 모두 전달하는 것보다 최신 화면을 빨리
    보여주는 것이 중요하다. 따라서 큐에는 한 장만 남기고(depth=1),
    일부 프레임 유실을 허용하는 BEST_EFFORT를 사용한다.
    """
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE,
    )


def _diagnostics_qos() -> QoSProfile:
    """저주기로 발행되는 상태 정보용 QoS를 만든다.

    진단 정보는 영상보다 빈도가 낮고 유실 없이 전달할 가치가 있어
    RELIABLE과 depth=10을 사용한다.
    """
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
    )


class CameraNode(Node):
    """USB 카메라를 관리하며 최신 프레임과 상태를 발행하는 노드."""

    def __init__(
        self,
        capture_factory: Optional[Callable[[int], Any]] = None,
    ) -> None:
        """파라미터, Publisher, Timer와 카메라를 준비한다."""
        super().__init__('camera_node')

        # 실행 시 YAML 또는 --ros-args -p로 덮어쓸 수 있는 ROS 파라미터다.
        # device_id=0은 보통 /dev/video0에 해당한다.
        self.declare_parameter('device_id', 0)
        # 아래 해상도와 FPS는 카메라 드라이버에 보내는 '요청값'이다.
        # 실제 적용값은 장치가 지원하는 모드에 따라 달라질 수 있다.
        self.declare_parameter('image_width', 640)
        self.declare_parameter('image_height', 480)
        self.declare_parameter('fps', 30.0)
        # frame_id는 이 영상이 어느 카메라 좌표계에서 왔는지 표시한다.
        self.declare_parameter(
            'frame_id',
            'camera_color_optical_frame',
        )
        # 연결 실패 후 재시도 간격과 진단 메시지 발행 주기를 설정한다.
        self.declare_parameter('reconnect_interval_ms', 1000)
        self.declare_parameter('diagnostics_rate_hz', 1.0)

        # 선언한 ROS 파라미터를 실제 Python 타입으로 읽어 캐시한다.
        self._device_id = int(
            self.get_parameter('device_id').value,
        )
        self._image_width = int(
            self.get_parameter('image_width').value,
        )
        self._image_height = int(
            self.get_parameter('image_height').value,
        )
        self._fps = float(self.get_parameter('fps').value)
        self._frame_id = str(
            self.get_parameter('frame_id').value,
        )
        self._reconnect_interval_ms = int(
            self.get_parameter('reconnect_interval_ms').value,
        )
        self._diagnostics_rate_hz = float(
            self.get_parameter('diagnostics_rate_hz').value,
        )
        self._validate_parameters()

        # 기본값은 cv2.VideoCapture다. capture_factory는 테스트에서 실제
        # 카메라 대신 가짜 캡처 객체를 주입할 수 있게 만든 연결 지점이다.
        # VideoCapture(device_id)는 백엔드를 지정하지 않으므로 OpenCV가
        # V4L2/GStreamer 등 사용 가능한 백엔드를 자동 선택한다. 따라서
        # Jetson에서 GStreamer/Argus 경고가 나도 다른 백엔드로 USB 카메라가
        # 열리면 노드는 동작한다. 명시적 GStreamer fallback은 이 코드에 없다.
        self._capture_factory = capture_factory or cv2.VideoCapture
        self._capture: Optional[Any] = None
        # CvBridge가 OpenCV ndarray와 sensor_msgs/Image 사이를 변환한다.
        self._bridge = CvBridge()

        # 재연결 시각, FPS 계산, 진단 표시를 위한 내부 상태다.
        # 경과 시간 계산에는 시스템 시각 변경의 영향을 받지 않는
        # monotonic clock을 사용하고, ROS 메시지 stamp에는 ROS clock을 쓴다.
        self._next_reconnect_ns = 0
        self._frames_published = 0
        self._frames_since_diagnostic = 0
        self._read_failures = 0
        self._last_frame_ns: Optional[int] = None
        self._last_diagnostic_ns = time.monotonic_ns()
        self._diagnostic_message = 'camera has not produced a frame'
        self._resolution_warning_reported = False

        # 원본 영상과 카메라 상태는 용도에 맞는 서로 다른 QoS로 발행한다.
        self._image_publisher = self.create_publisher(
            Image,
            IMAGE_TOPIC,
            _image_qos(),
        )
        self._diagnostics_publisher = self.create_publisher(
            DiagnosticArray,
            DIAGNOSTICS_TOPIC,
            _diagnostics_qos(),
        )

        # 첫 Timer는 목표 FPS 간격마다 한 프레임을 읽는다. 두 번째 Timer는
        # 저주기로 연결 상태와 실제 발행 FPS를 진단 토픽에 보낸다.
        self._capture_timer = self.create_timer(
            1.0 / self._fps,
            self._capture_and_publish,
        )
        self._diagnostics_timer = self.create_timer(
            1.0 / self._diagnostics_rate_hz,
            self._publish_diagnostics,
        )

        self._open_camera()

    def _validate_parameters(self) -> None:
        """카메라를 열기 전에 잘못된 설정을 빠르게 거부한다."""
        if self._device_id < 0:
            raise ValueError('device_id must be zero or greater')
        if self._image_width <= 0 or self._image_height <= 0:
            raise ValueError('image dimensions must be greater than zero')
        if self._fps <= 0.0:
            raise ValueError('fps must be greater than zero')
        if not self._frame_id:
            raise ValueError('frame_id must not be empty')
        if self._reconnect_interval_ms < 100:
            raise ValueError(
                'reconnect_interval_ms must be at least 100',
            )
        if self._diagnostics_rate_hz <= 0.0:
            raise ValueError(
                'diagnostics_rate_hz must be greater than zero',
            )

    def _open_camera(self) -> bool:
        """OpenCV로 카메라를 열고 목표 캡처 속성을 요청한다.

        ``set`` 호출은 장치에 값을 요청할 뿐 성공을 보장하지 않는다.
        실제 프레임 크기는 발행 후 ``_warn_if_resolution_differs``에서
        확인하고, 실제 FPS는 진단 토픽에서 측정한다.
        """
        # 재연결 시 이전 핸들을 먼저 닫아 장치 점유가 남지 않게 한다.
        self._release_camera()

        capture = self._capture_factory(self._device_id)
        if capture is None or not capture.isOpened():
            # 열기 실패 시 Timer를 막지 않고, 예약된 시각에 다시 시도한다.
            if capture is not None:
                capture.release()
            self._diagnostic_message = (
                f'failed to open camera device {self._device_id}'
            )
            self.get_logger().error(self._diagnostic_message)
            self._schedule_reconnect()
            return False

        # 카메라가 지원하지 않는 값은 무시될 수 있다. 버퍼 크기 1은
        # 지원하는 백엔드에서 오래된 프레임이 쌓여 지연되는 것을 줄인다.
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._image_width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._image_height)
        capture.set(cv2.CAP_PROP_FPS, self._fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self._capture = capture
        self._next_reconnect_ns = 0
        self._diagnostic_message = 'camera connected; awaiting frame'
        self.get_logger().info(
            'Opened camera device '
            f'{self._device_id} at target '
            f'{self._image_width}x{self._image_height} '
            f'@ {self._fps:.1f} FPS',
        )
        return True

    def _schedule_reconnect(self) -> None:
        """다음 카메라 연결 재시도 시각을 monotonic clock으로 정한다."""
        delay_ns = self._reconnect_interval_ms * 1_000_000
        self._next_reconnect_ns = time.monotonic_ns() + delay_ns

    def _capture_and_publish(self) -> None:
        """Timer마다 최신 프레임 한 장을 읽어 ROS 이미지로 발행한다."""
        # 카메라가 끊겼다면 reconnect_interval_ms마다 다시 연다.
        if self._capture is None or not self._capture.isOpened():
            if time.monotonic_ns() >= self._next_reconnect_ns:
                self._open_camera()
            return

        # read()는 성공 여부와 OpenCV ndarray 프레임을 함께 반환한다.
        success, frame = self._capture.read()
        if not success or frame is None or frame.size == 0:
            # 읽기 오류가 난 핸들은 버리고 다음 주기에 새로 연결한다.
            self._read_failures += 1
            self._diagnostic_message = 'camera frame read failed'
            self.get_logger().error(
                'Camera frame read failed; reconnecting',
            )
            self._release_camera()
            self._schedule_reconnect()
            return

        # 카메라마다 회색/3채널/4채널 형식이 다를 수 있어 bgr8로 통일한다.
        frame = self._as_bgr8(frame)
        if frame is None:
            self._read_failures += 1
            self._diagnostic_message = 'unsupported camera frame format'
            return

        # CvBridge가 ndarray의 크기, step, byte data를 Image로 변환한다.
        # header.stamp는 촬영 직후의 ROS 시각, frame_id는 카메라 좌표계다.
        # 이후 MediaPipe 노드는 이 header를 결과 메시지에도 이어서 사용한다.
        stamp = self.get_clock().now().to_msg()
        message = self._bridge.cv2_to_imgmsg(
            frame,
            encoding=IMAGE_ENCODING,
        )
        message.header.stamp = stamp
        message.header.frame_id = self._frame_id
        self._image_publisher.publish(message)

        now_ns = time.monotonic_ns()
        self._last_frame_ns = now_ns
        self._frames_published += 1
        self._frames_since_diagnostic += 1
        self._diagnostic_message = 'camera streaming'
        self._warn_if_resolution_differs(message.width, message.height)

    def _as_bgr8(self, frame: Any) -> Optional[Any]:
        """카메라 프레임을 ROS에 발행할 BGR 3채널 형식으로 맞춘다."""
        if frame.ndim == 2:
            # 단일 채널 흑백 영상은 동일 값을 B/G/R 세 채널로 확장한다.
            return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        if frame.ndim != 3:
            return None

        channel_count = frame.shape[2]
        if channel_count == 3:
            # OpenCV VideoCapture의 일반적인 BGR 프레임은 그대로 사용한다.
            return frame
        if channel_count == 4:
            # BGRA 영상은 사용하지 않는 알파 채널을 제거한다.
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        return None

    def _warn_if_resolution_differs(
        self,
        actual_width: int,
        actual_height: int,
    ) -> None:
        """요청값과 실제 해상도가 다르면 실행 중 한 번만 경고한다."""
        if self._resolution_warning_reported:
            return
        if (
            actual_width == self._image_width
            and actual_height == self._image_height
        ):
            return

        self._resolution_warning_reported = True
        self.get_logger().warning(
            'Camera returned '
            f'{actual_width}x{actual_height}; requested '
            f'{self._image_width}x{self._image_height}',
        )

    def _publish_diagnostics(self) -> None:
        """카메라 연결 상태와 구간별 실측 FPS를 진단 토픽에 발행한다."""
        now_ns = time.monotonic_ns()
        # 직전 진단 이후 발행한 프레임 수를 실제 경과 시간으로 나눈다.
        elapsed_seconds = max(
            (now_ns - self._last_diagnostic_ns) / 1_000_000_000,
            1e-9,
        )
        measured_fps = (
            self._frames_since_diagnostic / elapsed_seconds
        )

        # isOpened()는 장치 핸들 상태를, recent_frame은 실제 데이터가
        # 최근에 도착했는지를 본다. 영상이 멎은 상황도 구분할 수 있다.
        connected = (
            self._capture is not None
            and self._capture.isOpened()
        )
        recent_frame = (
            self._last_frame_ns is not None
            and now_ns - self._last_frame_ns
            <= max(
                self._reconnect_interval_ms * 2_000_000,
                1_000_000_000,
            )
        )

        # 연결 실패는 ERROR, 연결됐지만 최근 영상이 없으면 WARN,
        # 정상 스트리밍 중이면 OK로 보고한다.
        if not connected:
            level = DiagnosticStatus.ERROR
        elif not recent_frame:
            level = DiagnosticStatus.WARN
        else:
            level = DiagnosticStatus.OK

        # DiagnosticStatus에 사람이 읽는 상태와 모니터링 수치를 넣는다.
        status = DiagnosticStatus()
        status.level = level
        status.name = 'thing_vision/camera_node'
        status.hardware_id = f'camera:{self._device_id}'
        status.message = self._diagnostic_message
        status.values = [
            KeyValue(
                key='connected',
                value=str(connected).lower(),
            ),
            KeyValue(
                key='target_resolution',
                value=f'{self._image_width}x{self._image_height}',
            ),
            KeyValue(
                key='target_fps',
                value=f'{self._fps:.3f}',
            ),
            KeyValue(
                key='measured_fps',
                value=f'{measured_fps:.3f}',
            ),
            KeyValue(
                key='frames_published',
                value=str(self._frames_published),
            ),
            KeyValue(
                key='read_failures',
                value=str(self._read_failures),
            ),
        ]

        # ROS 시각으로 여러 노드의 진단 결과를 시간 순서로 비교할 수 있다.
        diagnostics = DiagnosticArray()
        diagnostics.header.stamp = self.get_clock().now().to_msg()
        diagnostics.status = [status]
        self._diagnostics_publisher.publish(diagnostics)

        self._frames_since_diagnostic = 0
        self._last_diagnostic_ns = now_ns

    def _release_camera(self) -> None:
        """열린 VideoCapture를 해제해 카메라 장치 점유를 반환한다."""
        if self._capture is None:
            return
        self._capture.release()
        self._capture = None

    def destroy_node(self) -> None:
        """ROS 노드를 제거하기 전에 카메라 핸들을 안전하게 해제한다."""
        self._release_camera()
        super().destroy_node()


def main(args: Optional[list[str]] = None) -> None:
    """ROS 2를 초기화하고 종료 요청까지 CameraNode를 실행한다."""
    rclpy.init(args=args)
    node: Optional[CameraNode] = None
    try:
        node = CameraNode()
        # spin이 Timer callback을 실행하며 Ctrl+C 또는 종료 요청을 기다린다.
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        # 예외나 Ctrl+C에서도 카메라와 ROS 리소스를 반드시 정리한다.
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
