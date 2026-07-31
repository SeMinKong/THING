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

"""Publish frames from the robot's USB camera as ROS 2 images."""

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


IMAGE_TOPIC = '/thing/image_raw'
DIAGNOSTICS_TOPIC = '/thing/diagnostics'
IMAGE_ENCODING = 'bgr8'


def _image_qos() -> QoSProfile:
    """Return the latest-frame-only QoS used by the camera pipeline."""
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE,
    )


def _diagnostics_qos() -> QoSProfile:
    """Return the QoS used for low-rate camera diagnostics."""
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
    )


class CameraNode(Node):
    """Capture fresh USB-camera frames and publish them without buffering."""

    def __init__(
        self,
        capture_factory: Optional[Callable[[int], Any]] = None,
    ) -> None:
        """Initialize parameters, publishers, timers, and the camera."""
        super().__init__('camera_node')

        self.declare_parameter('device_id', 0)
        self.declare_parameter('image_width', 640)
        self.declare_parameter('image_height', 480)
        self.declare_parameter('fps', 30.0)
        self.declare_parameter(
            'frame_id',
            'camera_color_optical_frame',
        )
        self.declare_parameter('reconnect_interval_ms', 1000)
        self.declare_parameter('diagnostics_rate_hz', 1.0)

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

        self._capture_factory = capture_factory or cv2.VideoCapture
        self._capture: Optional[Any] = None
        self._bridge = CvBridge()
        self._next_reconnect_ns = 0
        self._frames_published = 0
        self._frames_since_diagnostic = 0
        self._read_failures = 0
        self._last_frame_ns: Optional[int] = None
        self._last_diagnostic_ns = time.monotonic_ns()
        self._diagnostic_message = 'camera has not produced a frame'
        self._resolution_warning_reported = False

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
        self._release_camera()

        capture = self._capture_factory(self._device_id)
        if capture is None or not capture.isOpened():
            if capture is not None:
                capture.release()
            self._diagnostic_message = (
                f'failed to open camera device {self._device_id}'
            )
            self.get_logger().error(self._diagnostic_message)
            self._schedule_reconnect()
            return False

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
        delay_ns = self._reconnect_interval_ms * 1_000_000
        self._next_reconnect_ns = time.monotonic_ns() + delay_ns

    def _capture_and_publish(self) -> None:
        if self._capture is None or not self._capture.isOpened():
            if time.monotonic_ns() >= self._next_reconnect_ns:
                self._open_camera()
            return

        success, frame = self._capture.read()
        if not success or frame is None or frame.size == 0:
            self._read_failures += 1
            self._diagnostic_message = 'camera frame read failed'
            self.get_logger().error(
                'Camera frame read failed; reconnecting',
            )
            self._release_camera()
            self._schedule_reconnect()
            return

        frame = self._as_bgr8(frame)
        if frame is None:
            self._read_failures += 1
            self._diagnostic_message = 'unsupported camera frame format'
            return

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
        if frame.ndim == 2:
            return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        if frame.ndim != 3:
            return None

        channel_count = frame.shape[2]
        if channel_count == 3:
            return frame
        if channel_count == 4:
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        return None

    def _warn_if_resolution_differs(
        self,
        actual_width: int,
        actual_height: int,
    ) -> None:
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
        now_ns = time.monotonic_ns()
        elapsed_seconds = max(
            (now_ns - self._last_diagnostic_ns) / 1_000_000_000,
            1e-9,
        )
        measured_fps = (
            self._frames_since_diagnostic / elapsed_seconds
        )

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

        if not connected:
            level = DiagnosticStatus.ERROR
        elif not recent_frame:
            level = DiagnosticStatus.WARN
        else:
            level = DiagnosticStatus.OK

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

        diagnostics = DiagnosticArray()
        diagnostics.header.stamp = self.get_clock().now().to_msg()
        diagnostics.status = [status]
        self._diagnostics_publisher.publish(diagnostics)

        self._frames_since_diagnostic = 0
        self._last_diagnostic_ns = now_ns

    def _release_camera(self) -> None:
        if self._capture is None:
            return
        self._capture.release()
        self._capture = None

    def destroy_node(self) -> None:
        """Release the camera before destroying the ROS node."""
        self._release_camera()
        super().destroy_node()


def main(args: Optional[list[str]] = None) -> None:
    """Run the ROS 2 camera node."""
    rclpy.init(args=args)
    node: Optional[CameraNode] = None
    try:
        node = CameraNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
