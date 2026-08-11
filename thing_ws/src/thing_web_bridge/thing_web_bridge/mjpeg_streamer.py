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

"""Serve timestamp-matched ROS images and hand landmarks as HTTP MJPEG."""

from collections import OrderedDict
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import math
import socket
from threading import Condition
from threading import Lock
from threading import Thread
import time
from typing import Optional, Tuple

import cv2
from cv_bridge import CvBridge
from cv_bridge import CvBridgeError
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
from sensor_msgs.msg import Image
from thing_interfaces.msg import HandLandmarks


IMAGE_TOPIC = '/thing/image_raw'
LANDMARKS_TOPIC = '/thing/landmarks'
DIAGNOSTICS_TOPIC = '/thing/diagnostics'

BOUNDARY = 'frame'
LANDMARK_COUNT = 21

# MediaPipe Hands의 손가락 뼈와 손바닥 연결선이다.
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
)

StampKey = Tuple[str, int, int]
MatchedPair = Tuple[Image, HandLandmarks]


def _sensor_data_qos() -> QoSProfile:
    """Return the latest-sample QoS used by image and landmark topics."""
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE,
    )


def _diagnostics_qos() -> QoSProfile:
    """Return reliable QoS for low-rate diagnostics."""
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
    )


def _stamp_key(message: object) -> StampKey:
    """Return an exact key from a ROS message frame and timestamp."""
    header = message.header
    stamp = header.stamp
    return (
        str(header.frame_id),
        int(stamp.sec),
        int(stamp.nanosec),
    )


class LatestJpeg:
    """Share the newest JPEG safely between ROS and HTTP threads."""

    def __init__(self) -> None:
        """Initialize an empty frame store and connection counters."""
        self._condition = Condition()
        self._jpeg: Optional[bytes] = None
        self._sequence = 0
        self._closed = False
        self._active_clients = 0

    @property
    def active_clients(self) -> int:
        """Return the number of connected MJPEG clients."""
        with self._condition:
            return self._active_clients

    def client_connected(self) -> None:
        """Record one HTTP stream connection."""
        with self._condition:
            self._active_clients += 1

    def client_disconnected(self) -> None:
        """Record one HTTP stream disconnection."""
        with self._condition:
            self._active_clients = max(
                0,
                self._active_clients - 1,
            )

    def update(self, jpeg: bytes) -> None:
        """Replace the current frame and wake every waiting client."""
        with self._condition:
            if self._closed:
                return

            self._jpeg = jpeg
            self._sequence += 1
            self._condition.notify_all()

    def wait_for_next(
        self,
        previous_sequence: int,
        timeout: float,
    ) -> Tuple[int, Optional[bytes], bool]:
        """Wait until a newer JPEG is available or the store closes."""
        with self._condition:
            self._condition.wait_for(
                lambda: (
                    self._closed
                    or self._sequence != previous_sequence
                ),
                timeout=timeout,
            )

            return self._sequence, self._jpeg, self._closed

    def close(self) -> None:
        """Stop all waiting HTTP stream loops."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()


class LatestMatchedPair:
    """Keep only the newest timestamp-matched pair for JPEG encoding."""

    def __init__(self) -> None:
        """Initialize an empty latest-only work slot."""
        self._condition = Condition()
        self._pair: Optional[MatchedPair] = None
        self._closed = False
        self._superseded_pairs = 0

    @property
    def superseded_pairs(self) -> int:
        """Return how many pending pairs newer work replaced."""
        with self._condition:
            return self._superseded_pairs

    def update(self, pair: MatchedPair) -> bool:
        """Replace pending work with the newest matched pair."""
        with self._condition:
            if self._closed:
                return False

            if self._pair is not None:
                self._superseded_pairs += 1

            self._pair = pair
            self._condition.notify_all()
            return True

    def wait_until_ready(
        self,
        not_before: float,
    ) -> Optional[MatchedPair]:
        """Take the newest pair once the rate-limit deadline passes."""
        with self._condition:
            while not self._closed:
                delay = not_before - time.monotonic()

                if self._pair is not None and delay <= 0.0:
                    pair = self._pair
                    self._pair = None
                    return pair

                timeout = (
                    max(delay, 0.0)
                    if self._pair is not None
                    else None
                )
                self._condition.wait(timeout=timeout)

            return None

    def close(self) -> None:
        """Discard pending work and wake the encoder thread."""
        with self._condition:
            self._closed = True
            self._pair = None
            self._condition.notify_all()


def _make_handler(
    frame_store: LatestJpeg,
    stream_path: str,
) -> type[BaseHTTPRequestHandler]:
    """Create an HTTP handler bound to one frame store and URL path."""

    class MjpegHandler(BaseHTTPRequestHandler):

        protocol_version = 'HTTP/1.1'

        def do_GET(self) -> None:
            """Serve the configured MJPEG endpoint."""
            request_path = self.path.split('?', maxsplit=1)[0]

            if request_path != stream_path:
                self.send_error(404, 'MJPEG stream not found')
                return

            try:
                self.connection.setsockopt(
                    socket.IPPROTO_TCP,
                    socket.TCP_NODELAY,
                    1,
                )
            except OSError:
                pass

            self.send_response(200)
            self.send_header(
                'Content-Type',
                f'multipart/x-mixed-replace; boundary={BOUNDARY}',
            )
            self.send_header(
                'Cache-Control',
                'no-store, no-cache',
            )
            self.send_header('Pragma', 'no-cache')
            self.send_header('X-Accel-Buffering', 'no')
            self.send_header('Connection', 'close')
            self.end_headers()

            frame_store.client_connected()
            sequence = -1

            try:
                while True:
                    next_sequence, jpeg, closed = (
                        frame_store.wait_for_next(
                            sequence,
                            timeout=1.0,
                        )
                    )

                    if closed:
                        break

                    if next_sequence == sequence:
                        continue

                    sequence = next_sequence

                    if jpeg is None:
                        continue

                    self.wfile.write(
                        f'--{BOUNDARY}\r\n'.encode(),
                    )
                    self.wfile.write(
                        b'Content-Type: image/jpeg\r\n',
                    )
                    self.wfile.write(
                        (
                            f'Content-Length: '
                            f'{len(jpeg)}\r\n\r\n'
                        ).encode(),
                    )
                    self.wfile.write(jpeg)
                    self.wfile.write(b'\r\n')
                    self.wfile.flush()

            except (
                BrokenPipeError,
                ConnectionAbortedError,
                ConnectionResetError,
                TimeoutError,
            ):
                pass

            finally:
                frame_store.client_disconnected()

        def log_message(
            self,
            format_string: str,
            *args: object,
        ) -> None:
            """Suppress request logs; diagnostics report status."""

    return MjpegHandler


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    """Allow quick restart and isolate every browser connection."""

    allow_reuse_address = True
    daemon_threads = True


class MjpegStreamer(Node):
    """Match ROS images and landmarks, overlay them, and serve MJPEG."""

    def __init__(self) -> None:
        """Initialize ROS subscriptions and the HTTP server."""
        super().__init__('mjpeg_streamer')

        self.declare_parameter('http_host', '0.0.0.0')
        self.declare_parameter('http_port', 8080)
        self.declare_parameter(
            'stream_path',
            '/stream.mjpg',
        )
        self.declare_parameter('target_fps', 15.0)
        self.declare_parameter('jpeg_quality', 60)
        self.declare_parameter('sync_cache_size', 10)
        self.declare_parameter(
            'diagnostics_rate_hz',
            1.0,
        )

        self._http_host = str(
            self.get_parameter('http_host').value,
        )
        self._http_port = int(
            self.get_parameter('http_port').value,
        )
        self._stream_path = str(
            self.get_parameter('stream_path').value,
        )
        self._target_fps = float(
            self.get_parameter('target_fps').value,
        )
        self._jpeg_quality = int(
            self.get_parameter('jpeg_quality').value,
        )
        self._sync_cache_size = int(
            self.get_parameter('sync_cache_size').value,
        )
        diagnostics_rate_hz = float(
            self.get_parameter(
                'diagnostics_rate_hz',
            ).value,
        )

        self._validate_parameters(
            diagnostics_rate_hz,
        )

        self._bridge = CvBridge()

        # image와 landmarks가 서로 다른 callback에서 오므로
        # timestamp가 정확히 같은 메시지만 합친다.
        self._cache_lock = Lock()
        self._metrics_lock = Lock()
        self._shutdown_lock = Lock()
        self._shutdown_started = False

        self._images: OrderedDict[
            StampKey,
            Image,
        ] = OrderedDict()

        self._landmarks: OrderedDict[
            StampKey,
            HandLandmarks,
        ] = OrderedDict()

        self._last_frame_at: Optional[float] = None
        self._last_encode_ms = 0.0
        self._last_source_age_ms = -1.0

        self._images_received = 0
        self._landmarks_received = 0
        self._matched_frames = 0
        self._encoded_frames = 0
        self._encode_failures = 0

        self._frames_since_diagnostic = 0
        self._last_diagnostic_at = time.monotonic()

        self._frame_store = LatestJpeg()
        self._matched_pair_store = LatestMatchedPair()

        self._diagnostics_publisher = (
            self.create_publisher(
                DiagnosticArray,
                DIAGNOSTICS_TOPIC,
                _diagnostics_qos(),
            )
        )

        self._image_subscription = (
            self.create_subscription(
                Image,
                IMAGE_TOPIC,
                self._on_image,
                _sensor_data_qos(),
            )
        )

        self._landmarks_subscription = (
            self.create_subscription(
                HandLandmarks,
                LANDMARKS_TOPIC,
                self._on_landmarks,
                _sensor_data_qos(),
            )
        )

        self._diagnostics_timer = self.create_timer(
            1.0 / diagnostics_rate_hz,
            self._publish_diagnostics,
        )

        handler = _make_handler(
            self._frame_store,
            self._stream_path,
        )

        self._http_server = (
            ReusableThreadingHTTPServer(
                (
                    self._http_host,
                    self._http_port,
                ),
                handler,
            )
        )

        self._http_thread = Thread(
            target=self._http_server.serve_forever,
            name='mjpeg-http-server',
            daemon=True,
        )
        self._encoder_thread = Thread(
            target=self._encode_loop,
            name='mjpeg-encoder',
            daemon=True,
        )

        self._encoder_thread.start()
        self._http_thread.start()

        self.get_logger().info(
            'MJPEG stream ready at '
            f'http://{self._http_host}:'
            f'{self._http_port}'
            f'{self._stream_path}; exact-frame sync, '
            'latest-only JPEG encoding',
        )

        if self._sync_cache_size < 5:
            self.get_logger().warning(
                'sync_cache_size below 5 may evict images before '
                'MediaPipe returns matching landmarks',
            )

    def _validate_parameters(
        self,
        diagnostics_rate_hz: float,
    ) -> None:
        if not self._http_host:
            raise ValueError(
                'http_host must not be empty',
            )

        if not 1 <= self._http_port <= 65535:
            raise ValueError(
                'http_port must be in the range 1..65535',
            )

        if not self._stream_path.startswith('/'):
            raise ValueError(
                'stream_path must start with /',
            )

        if (
            not math.isfinite(self._target_fps)
            or self._target_fps <= 0.0
        ):
            raise ValueError(
                'target_fps must be a finite positive number',
            )

        if not 1 <= self._jpeg_quality <= 100:
            raise ValueError(
                'jpeg_quality must be in the range 1..100',
            )

        if self._sync_cache_size < 2:
            raise ValueError(
                'sync_cache_size must be at least 2',
            )

        if (
            not math.isfinite(diagnostics_rate_hz)
            or diagnostics_rate_hz <= 0.0
        ):
            raise ValueError(
                'diagnostics_rate_hz must be '
                'a finite positive number',
            )

    def _on_image(self, message: Image) -> None:
        """Store an image and queue it if matching landmarks exist."""
        with self._metrics_lock:
            self._images_received += 1
        key = _stamp_key(message)

        pair = self._cache_and_take_pair(
            key,
            image=message,
            landmarks=None,
        )

        self._queue_matched_pair(pair)

    def _on_landmarks(
        self,
        message: HandLandmarks,
    ) -> None:
        """Store landmarks and queue them if the image exists."""
        with self._metrics_lock:
            self._landmarks_received += 1
        key = _stamp_key(message)

        pair = self._cache_and_take_pair(
            key,
            image=None,
            landmarks=message,
        )

        self._queue_matched_pair(pair)

    def _queue_matched_pair(
        self,
        pair: Optional[MatchedPair],
    ) -> None:
        """Queue only exact-stamp pairs for latest-only encoding."""
        if pair is None:
            return

        with self._metrics_lock:
            self._matched_frames += 1

        self._matched_pair_store.update(pair)

    def _cache_and_take_pair(
        self,
        key: StampKey,
        image: Optional[Image],
        landmarks: Optional[HandLandmarks],
    ) -> Optional[Tuple[Image, HandLandmarks]]:
        """Return only an image-landmark pair with the same stamp."""
        with self._cache_lock:
            if image is not None:
                self._images[key] = image
                self._images.move_to_end(key)

            if landmarks is not None:
                self._landmarks[key] = landmarks
                self._landmarks.move_to_end(key)

            matched_image = self._images.pop(
                key,
                None,
            )
            matched_landmarks = self._landmarks.pop(
                key,
                None,
            )

            if (
                matched_image is None
                or matched_landmarks is None
            ):
                if matched_image is not None:
                    self._images[key] = matched_image

                if matched_landmarks is not None:
                    self._landmarks[key] = (
                        matched_landmarks
                    )

                self._trim_cache(self._images)
                self._trim_cache(self._landmarks)

                return None

            return matched_image, matched_landmarks

    def _trim_cache(
        self,
        cache: OrderedDict,
    ) -> None:
        """Remove old unmatched messages."""
        while len(cache) > self._sync_cache_size:
            cache.popitem(last=False)

    def _encode_loop(self) -> None:
        """Encode newest matched pairs without blocking ROS callbacks."""
        period = 1.0 / self._target_fps
        next_encode_at = 0.0

        while True:
            pair = self._matched_pair_store.wait_until_ready(
                next_encode_at,
            )
            if pair is None:
                return

            encode_started = time.monotonic()

            try:
                self._render_pair(*pair)
            except Exception as error:  # Keep the worker alive.
                with self._metrics_lock:
                    self._encode_failures += 1
                self.get_logger().error(
                    'Unexpected MJPEG encoder error: '
                    f'{type(error).__name__}: {error}',
                )

            next_encode_at = encode_started + period

    def _render_pair(
        self,
        image_message: Image,
        landmark_message: HandLandmarks,
    ) -> None:
        """Draw landmarks and publish one encoded JPEG."""
        encode_started = time.monotonic()

        try:
            frame = self._bridge.imgmsg_to_cv2(
                image_message,
                desired_encoding='bgr8',
            )
            frame = frame.copy()

            self._draw_landmarks(
                frame,
                landmark_message,
            )

            success, encoded = cv2.imencode(
                '.jpg',
                frame,
                [
                    cv2.IMWRITE_JPEG_QUALITY,
                    self._jpeg_quality,
                ],
            )

            if not success:
                raise RuntimeError(
                    'OpenCV JPEG encoding failed',
                )

        except (
            CvBridgeError,
            cv2.error,
            RuntimeError,
            ValueError,
        ) as error:
            with self._metrics_lock:
                self._encode_failures += 1
            self.get_logger().warning(
                f'MJPEG frame skipped: {error}',
            )
            return

        self._frame_store.update(
            encoded.tobytes(),
        )

        completed_at = time.monotonic()
        source_age_ms = self._source_age_ms(
            image_message,
        )

        with self._metrics_lock:
            self._encoded_frames += 1
            self._frames_since_diagnostic += 1
            self._last_frame_at = completed_at
            self._last_encode_ms = (
                completed_at - encode_started
            ) * 1000.0
            self._last_source_age_ms = source_age_ms

    def _source_age_ms(self, message: Image) -> float:
        """Return camera-stamp-to-encode age using the ROS clock."""
        stamp = message.header.stamp
        stamp_ns = (
            int(stamp.sec) * 1_000_000_000
            + int(stamp.nanosec)
        )
        now_ns = self.get_clock().now().nanoseconds

        if stamp_ns <= 0 or now_ns < stamp_ns:
            return -1.0

        return (now_ns - stamp_ns) / 1_000_000.0

    def _draw_landmarks(
        self,
        frame: object,
        message: HandLandmarks,
    ) -> None:
        """Draw detected landmarks on one OpenCV frame."""
        if (
            not message.detected
            or len(message.landmarks)
            != LANDMARK_COUNT
        ):
            return

        height, width = frame.shape[:2]
        points = []

        for landmark in message.landmarks:
            x = float(landmark.x)
            y = float(landmark.y)

            if (
                not math.isfinite(x)
                or not math.isfinite(y)
            ):
                raise ValueError(
                    'landmark contains a '
                    'non-finite coordinate',
                )

            pixel_x = max(
                0,
                min(
                    width - 1,
                    int(round(x * width)),
                ),
            )
            pixel_y = max(
                0,
                min(
                    height - 1,
                    int(round(y * height)),
                ),
            )

            points.append(
                (pixel_x, pixel_y),
            )

        for start, end in HAND_CONNECTIONS:
            cv2.line(
                frame,
                points[start],
                points[end],
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

        for point in points:
            cv2.circle(
                frame,
                point,
                3,
                (0, 165, 255),
                -1,
                cv2.LINE_AA,
            )

    def _publish_diagnostics(self) -> None:
        """Publish HTTP and encoding status at low rate."""
        now = time.monotonic()
        with self._metrics_lock:
            elapsed = max(
                now - self._last_diagnostic_at,
                1.0e-9,
            )
            measured_fps = (
                self._frames_since_diagnostic
                / elapsed
            )
            last_frame_at = self._last_frame_at
            last_encode_ms = self._last_encode_ms
            last_source_age_ms = (
                self._last_source_age_ms
            )
            images_received = self._images_received
            landmarks_received = (
                self._landmarks_received
            )
            matched_frames = self._matched_frames
            encoded_frames = self._encoded_frames
            encode_failures = self._encode_failures
            self._frames_since_diagnostic = 0
            self._last_diagnostic_at = now

        if last_frame_at is None:
            frame_age_ms = float('inf')
        else:
            frame_age_ms = (
                now - last_frame_at
            ) * 1000.0

        status = DiagnosticStatus()
        status.name = (
            'thing_web_bridge/mjpeg_streamer'
        )
        status.hardware_id = 'jetson'

        if not self._http_thread.is_alive():
            status.level = DiagnosticStatus.ERROR
            status.message = (
                'HTTP server thread stopped'
            )

        elif not self._encoder_thread.is_alive():
            status.level = DiagnosticStatus.ERROR
            status.message = (
                'JPEG encoder thread stopped'
            )

        elif frame_age_ms > 1000.0:
            status.level = DiagnosticStatus.WARN
            status.message = (
                'no recent timestamp-matched frame'
            )

        else:
            status.level = DiagnosticStatus.OK
            status.message = 'MJPEG stream ready'

        endpoint = (
            f'http://{self._http_host}:'
            f'{self._http_port}'
            f'{self._stream_path}'
        )

        status.values = [
            KeyValue(
                key='image_topic',
                value=IMAGE_TOPIC,
            ),
            KeyValue(
                key='landmarks_topic',
                value=LANDMARKS_TOPIC,
            ),
            KeyValue(
                key='endpoint',
                value=endpoint,
            ),
            KeyValue(
                key='target_fps',
                value=f'{self._target_fps:.3f}',
            ),
            KeyValue(
                key='jpeg_quality',
                value=str(self._jpeg_quality),
            ),
            KeyValue(
                key='sync_cache_size',
                value=str(self._sync_cache_size),
            ),
            KeyValue(
                key='measured_fps',
                value=f'{measured_fps:.3f}',
            ),
            KeyValue(
                key='frame_age_ms',
                value=f'{frame_age_ms:.3f}',
            ),
            KeyValue(
                key='source_age_ms',
                value=f'{last_source_age_ms:.3f}',
            ),
            KeyValue(
                key='encode_ms',
                value=f'{last_encode_ms:.3f}',
            ),
            KeyValue(
                key='active_clients',
                value=str(
                    self._frame_store.active_clients,
                ),
            ),
            KeyValue(
                key='images_received',
                value=str(images_received),
            ),
            KeyValue(
                key='landmarks_received',
                value=str(landmarks_received),
            ),
            KeyValue(
                key='matched_frames',
                value=str(matched_frames),
            ),
            KeyValue(
                key='encoded_frames',
                value=str(encoded_frames),
            ),
            KeyValue(
                key='encode_failures',
                value=str(encode_failures),
            ),
            KeyValue(
                key='superseded_pairs',
                value=str(
                    self._matched_pair_store.superseded_pairs,
                ),
            ),
        ]

        diagnostics = DiagnosticArray()
        diagnostics.header.stamp = (
            self.get_clock().now().to_msg()
        )
        diagnostics.status = [status]

        self._diagnostics_publisher.publish(
            diagnostics,
        )

    def destroy_node(self) -> None:
        """Stop HTTP clients and release server resources."""
        with self._shutdown_lock:
            if self._shutdown_started:
                return
            self._shutdown_started = True

        self._matched_pair_store.close()
        self._encoder_thread.join()
        self._frame_store.close()
        self._http_server.shutdown()
        self._http_server.server_close()
        self._http_thread.join()
        super().destroy_node()


def main(
    args: Optional[list[str]] = None,
) -> None:
    """Run the ROS 2 MJPEG streamer node."""
    rclpy.init(args=args)

    node: Optional[MjpegStreamer] = None

    try:
        node = MjpegStreamer()
        rclpy.spin(node)

    except (
        KeyboardInterrupt,
        ExternalShutdownException,
    ):
        pass

    finally:
        if node is not None:
            node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
