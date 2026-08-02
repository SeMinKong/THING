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

"""Generate filtered seven-axis MIMIC commands from hand landmarks."""

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


LANDMARK_COUNT = 21
_EPSILON = 1.0e-8
LANDMARKS_TOPIC = '/thing/landmarks'
MIMIC_COMMAND_TOPIC = '/thing/command/mimic'
CONTROL_STATE_TOPIC = '/thing/control_state'
DIAGNOSTICS_TOPIC = '/thing/diagnostics'

# MediaPipe Hands fixed landmark indices.
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
    """Seven dimensionless robot-hand targets in the inclusive 0..1 range."""

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
    """Return the latest-sample-only QoS for landmark input."""
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE,
    )


def _command_qos() -> QoSProfile:
    """Return the reliable latest-command QoS from the V7 contract."""
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
    )


def _state_qos() -> QoSProfile:
    """Return the durable state QoS used by control state."""
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
    points = _validated_points(landmarks)
    palm_width = _distance(points[INDEX_MCP], points[LITTLE_MCP])
    if palm_width <= _EPSILON:
        raise InvalidLandmarks('palm width is zero')

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

    palm_center = _mean_point(
        points,
        (WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, LITTLE_MCP),
    )
    opposition_ratio = (
        _distance(points[THUMB_TIP], palm_center) / palm_width
    )
    # A thumb near the palm centre is opposed; a distant thumb is neutral.
    thumb_opp = _inverse_normalize(opposition_ratio, 0.20, 1.25)

    thumb_spread = _angle(
        _subtract(points[THUMB_MCP], points[WRIST]),
        _subtract(points[INDEX_MCP], points[WRIST]),
    )
    # Approximate neutral-to-wide thumb spread. Calibration can replace these
    # defaults without changing the geometry API.
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
    """Estimate flexion from the two distal joint bends of one digit."""
    base, proximal, distal, tip = (points[index] for index in indices)
    proximal_bend = _joint_bend(base, proximal, distal)
    distal_bend = _joint_bend(proximal, distal, tip)
    return _clamp01(0.65 * proximal_bend + 0.35 * distal_bend)


def _joint_bend(first: Vector3, joint: Vector3, last: Vector3) -> float:
    """Return 0 for a straight joint and 1 near a strongly folded joint."""
    joint_angle = _angle(
        _subtract(first, joint),
        _subtract(last, joint),
    )
    straight_angle = math.pi
    maximum_expected_bend = math.radians(125.0)
    return _clamp01((straight_angle - joint_angle) / maximum_expected_bend)


def _validated_points(landmarks: Sequence[Any]) -> Tuple[Vector3, ...]:
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


def _distance(left: Vector3, right: Vector3) -> float:
    return _norm(_subtract(left, right))


def _norm(vector: Vector3) -> float:
    return math.sqrt(sum(component * component for component in vector))


def _angle(first: Vector3, second: Vector3) -> float:
    first_norm = _norm(first)
    second_norm = _norm(second)
    if first_norm <= _EPSILON or second_norm <= _EPSILON:
        raise InvalidLandmarks('adjacent landmarks overlap')
    cosine = sum(
        left * right for left, right in zip(first, second)
    ) / (first_norm * second_norm)
    return math.acos(max(-1.0, min(1.0, cosine)))


def _normalize(value: float, minimum: float, maximum: float) -> float:
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
    """Convert valid right-hand landmarks into safe MIMIC commands."""

    def __init__(self) -> None:
        """Initialize parameters, ROS interfaces, and hand-loss state."""
        super().__init__('hand_target_node')

        self.declare_parameter('publish_rate_hz', 20.0)
        self.declare_parameter('deadband', 0.02)
        self.declare_parameter('low_pass_alpha', 0.25)
        self.declare_parameter('max_axis_delta_per_frame', 0.08)
        self.declare_parameter('hand_confidence_min', 0.70)
        self.declare_parameter('hand_loss_debounce_ms', 150)
        self.declare_parameter('hand_reacquire_stable_ms', 300)
        self.declare_parameter('speed_limit', 0.25)
        self.declare_parameter('diagnostics_rate_hz', 1.0)

        self._publish_rate_hz = float(
            self.get_parameter('publish_rate_hz').value,
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
        self._validate_parameters(diagnostics_rate_hz)

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
        self._filtered_targets = ZERO_TARGETS
        self._latest_confidence = 0.0
        self._last_invalid_reason = 'no landmark received'
        self._commands_published = 0
        self._calculation_failures = 0

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
        now = time.monotonic()
        self._last_landmark_at = now
        invalid_reason = self._invalid_landmark_reason(message)
        if invalid_reason is not None:
            self._mark_input_invalid(now, invalid_reason)
            return

        try:
            raw_targets = compute_hand_targets(message.landmarks)
        except InvalidLandmarks as error:
            self._calculation_failures += 1
            self._mark_input_invalid(now, str(error))
            return

        self._last_input_valid = True
        self._last_invalid_reason = ''
        self._invalid_since = None
        if self._valid_since is None:
            self._valid_since = now

        if self._hand_loss_latched:
            if now - self._valid_since >= self._hand_reacquire_stable_s:
                self._reacquired_stable = True
            return

        self._reacquired_stable = False
        if not self._mimic_active:
            return

        self._latest_targets = self._filter_targets(raw_targets)
        self._latest_confidence = _clamp01(float(message.confidence))

    def _invalid_landmark_reason(
        self,
        message: HandLandmarks,
    ) -> Optional[str]:
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
        if not self._mimic_active or self._hand_loss_latched:
            return
        if self._invalid_since is None:
            return
        if now - self._invalid_since < self._hand_loss_debounce_s:
            return

        self._hand_loss_latched = True
        self._disabled_seen_since_latch = False
        self._latest_targets = None
        self._latest_confidence = 0.0
        self.get_logger().error(
            'hand-loss latch set; explicit STOP and new MIMIC acquisition '
            'are required',
        )

    def _on_control_state(self, message: ControlState) -> None:
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
                self._latest_targets = None
            return

        if entering_mimic:
            self._begin_mimic_session(now)
        elif not new_mimic_active:
            self._latest_targets = None
            self._latest_confidence = 0.0
            self._filtered_targets = ZERO_TARGETS

    def _clear_hand_loss_latch(self, now: float) -> None:
        self._hand_loss_latched = False
        self._disabled_seen_since_latch = False
        self.get_logger().info(
            'hand-loss latch cleared by new MIMIC acquisition',
        )
        self._begin_mimic_session(now)

    def _begin_mimic_session(self, now: float) -> None:
        # Require a landmark received after this activation. This prevents a
        # target cached before STOP or recovery from being replayed.
        self._latest_targets = None
        self._latest_confidence = 0.0
        self._filtered_targets = ZERO_TARGETS
        self._last_input_valid = False
        self._valid_since = None
        self._reacquired_stable = False
        self._invalid_since = now
        self._last_invalid_reason = 'waiting for post-activation landmark'

    def _filter_targets(self, raw: HandTargets) -> HandTargets:
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
        now = time.monotonic()
        self._check_landmark_timeout(now)
        self._evaluate_hand_loss(now)
        if (
            not self._mimic_active
            or self._hand_loss_latched
            or self._latest_targets is None
        ):
            return

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

    def _check_landmark_timeout(self, now: float) -> None:
        if not self._mimic_active:
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
        now = time.monotonic()
        if self._last_landmark_at is None:
            input_age = 'unavailable'
        else:
            input_age = f'{(now - self._last_landmark_at) * 1000.0:.3f}'

        status = DiagnosticStatus()
        status.name = 'thing_vision/hand_target_node'
        status.hardware_id = 'jetson'
        if self._hand_loss_latched:
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
