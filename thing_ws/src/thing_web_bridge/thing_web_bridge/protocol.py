"""WebSocket JSON contract shared by the browser and ROS 2 bridge."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from threading import Lock
from typing import Any, Dict, Mapping


SNAPSHOT_FIELDS = (
    'timestamp',
    'mode',
    'recording_state',
    'landmarks',
    'motor_state',
    'safety_state',
)

CONTROL_MODES = ('DISABLED', 'MIMIC', 'MANUAL', 'TELEOP')
CONTROL_OWNERS = ('NONE', 'WEB', 'LOCAL')
RECORDING_STATES = (
    'IDLE',
    'STARTING',
    'RECORDING',
    'STOPPING',
    'COMPLETED',
    'FAILED',
    'INTERRUPTED',
)
RECORDING_RESULTS = ('UNSET', 'SUCCESS', 'FAILURE')
SAFETY_STATES = (
    'INIT',
    'READY',
    'RUN',
    'HOLD',
    'SAFE',
    'FAULT',
    'ESTOP',
    'RESET',
)
HANDEDNESS = ('UNKNOWN', 'LEFT', 'RIGHT')
HAND_SOURCES = (
    'UNKNOWN',
    'MIMIC',
    'TELEOP',
    'GESTURE',
    'SEQUENCE',
    'SAFETY',
)

GESTURES = ('open', 'fist', 'pinch', 'cylindrical_grasp')
SEQUENCES = ('countdown', 'scissors_rock_paper')
MIMIC_RESULTS = ('SUCCESS', 'FAILURE')
REQUEST_TYPES = (
    'set_control_mode',
    'stop',
    'execute_gesture',
    'execute_sequence',
    'start_recording',
    'stop_recording',
    'set_mimic_result',
    'reset_safety',
)


class ProtocolError(ValueError):
    """Describe a malformed or unsupported browser request."""

    def __init__(self, reason: str) -> None:
        """Initialize the error with a stable Web Bridge reason."""
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class BridgeRequest:
    """Validated client request envelope."""

    request_id: str
    type: str
    timestamp: str
    payload: Dict[str, Any]


def utc_now_z() -> str:
    """Return an RFC 3339 UTC timestamp with a ``Z`` suffix."""
    now = datetime.now(timezone.utc)
    return now.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _symbol(value: Any, table: tuple[str, ...], label: str) -> str:
    """Convert a ROS ordinal or symbolic value to a checked symbol."""
    if isinstance(value, str) and value in table:
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value < len(table):
            return table[value]
    raise ProtocolError(f'invalid_{label}')


def _plain(value: Any) -> Any:
    """Convert a ROS-message-like value to JSON-compatible Python data."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProtocolError('non_finite_state_value')
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    field_reader = getattr(value, 'get_fields_and_field_types', None)
    if callable(field_reader):
        return {
            name: _plain(getattr(value, name))
            for name in field_reader().keys()
        }
    if hasattr(value, 'sec') and hasattr(value, 'nanosec'):
        return {
            'sec': int(value.sec),
            'nanosec': int(value.nanosec),
        }
    if hasattr(value, 'x') and hasattr(value, 'y') and hasattr(value, 'z'):
        return {
            'x': _plain(value.x),
            'y': _plain(value.y),
            'z': _plain(value.z),
        }
    if hasattr(value, '__dict__'):
        return {
            key: _plain(item)
            for key, item in vars(value).items()
            if not key.startswith('_')
        }
    raise ProtocolError('unsupported_state_value')


def control_state_payload(message: Any) -> Dict[str, Any]:
    """Serialize ``ControlState`` while exposing symbolic enums."""
    payload = _plain(message)
    payload['active_mode'] = _symbol(
        payload.get('active_mode'), CONTROL_MODES, 'mode')
    payload['active_owner'] = _symbol(
        payload.get('active_owner'), CONTROL_OWNERS, 'owner')
    return payload


def recording_state_payload(message: Any) -> Dict[str, Any]:
    """Serialize ``RecordingState`` without losing uint64 session IDs."""
    payload = _plain(message)
    payload['state'] = _symbol(
        payload.get('state'), RECORDING_STATES, 'recording_state')
    payload['last_mimic_result'] = _symbol(
        payload.get('last_mimic_result'),
        RECORDING_RESULTS,
        'recording_result',
    )
    for key in ('active_session_id', 'last_session_id'):
        session_id = payload.get(key, 0)
        if isinstance(session_id, bool) or not isinstance(session_id, int):
            raise ProtocolError('invalid_session_id')
        if session_id < 0:
            raise ProtocolError('invalid_session_id')
        payload[key] = '' if session_id == 0 else str(session_id)
    return payload


def landmarks_payload(message: Any) -> Dict[str, Any]:
    """Serialize ``HandLandmarks`` with symbolic handedness."""
    payload = _plain(message)
    payload['handedness'] = _symbol(
        payload.get('handedness'), HANDEDNESS, 'handedness')
    return payload


def motor_state_payload(message: Any) -> Dict[str, Any]:
    """Serialize ``MotorStatus`` without changing motor field names."""
    return _plain(message)


def safety_state_payload(message: Any) -> Dict[str, Any]:
    """Serialize ``SafetyState`` and derive whether reset may be requested."""
    payload = _plain(message)
    payload['state'] = _symbol(
        payload.get('state'), SAFETY_STATES, 'safety_state')
    payload['reset_allowed'] = payload['state'] in ('SAFE', 'FAULT', 'ESTOP')
    return payload


def hand_command_payload(message: Any) -> Dict[str, Any]:
    """Serialize the latest validated seven-axis command for display."""
    payload = _plain(message)
    payload['source'] = _symbol(
        payload.get('source'), HAND_SOURCES, 'hand_source')
    return payload


class SnapshotStore:
    """Keep the latest ROS state and build replacement-only snapshots."""

    def __init__(self) -> None:
        """Initialize required fields with fail-closed empty values."""
        self._lock = Lock()
        self._mode = 'DISABLED'
        self._recording_state = 'IDLE'
        self._landmarks: Dict[str, Any] = {}
        self._motor_state: Dict[str, Any] = {}
        self._safety_state: Dict[str, Any] = {}
        self._control_state: Dict[str, Any] = {}
        self._recording: Dict[str, Any] = {}
        self._last_hand_command: Dict[str, Any] = {}

    def update_control_state(self, message: Any) -> None:
        """Store the latest ControlState and top-level mode."""
        payload = control_state_payload(message)
        with self._lock:
            self._control_state = payload
            self._mode = payload['active_mode']

    def update_recording_state(self, message: Any) -> None:
        """Store the latest RecordingState and symbolic top-level state."""
        payload = recording_state_payload(message)
        with self._lock:
            self._recording = payload
            self._recording_state = payload['state']

    def update_landmarks(self, message: Any) -> None:
        """Store the latest landmark display object."""
        payload = landmarks_payload(message)
        with self._lock:
            self._landmarks = payload

    def update_motor_state(self, message: Any) -> None:
        """Store the latest seven-motor status object."""
        payload = motor_state_payload(message)
        with self._lock:
            self._motor_state = payload

    def update_safety_state(self, message: Any) -> None:
        """Store the latest safety display object."""
        payload = safety_state_payload(message)
        with self._lock:
            self._safety_state = payload

    def update_hand_command(self, message: Any) -> None:
        """Store the latest validated command for seven-axis display."""
        payload = hand_command_payload(message)
        with self._lock:
            self._last_hand_command = payload

    def snapshot(self) -> Dict[str, Any]:
        """Return one independent snapshot matching the browser contract."""
        with self._lock:
            return {
                'timestamp': utc_now_z(),
                'mode': self._mode,
                'recording_state': self._recording_state,
                'landmarks': deepcopy(self._landmarks),
                'motor_state': deepcopy(self._motor_state),
                'safety_state': deepcopy(self._safety_state),
                'control_state': deepcopy(self._control_state),
                'recording': deepcopy(self._recording),
                'last_hand_command': deepcopy(self._last_hand_command),
            }


def _require_payload_keys(
    payload: Mapping[str, Any],
    required: set[str],
) -> None:
    if set(payload) != required:
        raise ProtocolError('web_malformed_request')


def _validate_speed(value: Any) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 < float(value) <= 1.0
    ):
        raise ProtocolError('web_malformed_request')


def _validate_session_id(value: Any) -> None:
    if not isinstance(value, str) or not value.isdigit() or value == '0':
        raise ProtocolError('web_malformed_request')
    if int(value) >= 2 ** 63:
        raise ProtocolError('web_malformed_request')


def _validate_timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value.endswith('Z'):
        raise ProtocolError('web_malformed_request')
    try:
        datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError as error:
        raise ProtocolError('web_malformed_request') from error


def parse_request(message: Any) -> BridgeRequest:
    """Validate a browser request without allowing arbitrary ROS access."""
    if not isinstance(message, Mapping):
        raise ProtocolError('web_malformed_request')
    if set(message) != {'request_id', 'type', 'timestamp', 'payload'}:
        raise ProtocolError('web_malformed_request')
    request_id = message['request_id']
    request_type = message['type']
    timestamp = message['timestamp']
    payload = message['payload']
    if not isinstance(request_id, str) or not request_id.strip():
        raise ProtocolError('web_malformed_request')
    if request_type not in REQUEST_TYPES:
        raise ProtocolError('web_unknown_type')
    _validate_timestamp(timestamp)
    if not isinstance(payload, Mapping):
        raise ProtocolError('web_malformed_request')

    clean_payload = dict(payload)
    if request_type == 'set_control_mode':
        _require_payload_keys(
            clean_payload, {'requested_mode', 'requested_owner'})
        mode = clean_payload['requested_mode']
        owner = clean_payload['requested_owner']
        if mode not in ('MIMIC', 'MANUAL') or owner != 'WEB':
            raise ProtocolError('invalid_mode')
    elif request_type == 'stop':
        _require_payload_keys(
            clean_payload, {'requested_mode', 'requested_owner'})
        if clean_payload != {
            'requested_mode': 'DISABLED',
            'requested_owner': 'NONE',
        }:
            raise ProtocolError('invalid_mode')
    elif request_type == 'execute_gesture':
        _require_payload_keys(clean_payload, {'gesture_name', 'speed_limit'})
        if clean_payload['gesture_name'] not in GESTURES:
            raise ProtocolError('web_malformed_request')
        _validate_speed(clean_payload['speed_limit'])
    elif request_type == 'execute_sequence':
        _require_payload_keys(clean_payload, {'sequence_name', 'speed_limit'})
        if clean_payload['sequence_name'] not in SEQUENCES:
            raise ProtocolError('web_malformed_request')
        _validate_speed(clean_payload['speed_limit'])
    elif request_type == 'start_recording':
        _require_payload_keys(clean_payload, {'label'})
        if not isinstance(clean_payload['label'], str):
            raise ProtocolError('web_malformed_request')
    elif request_type == 'stop_recording':
        _require_payload_keys(clean_payload, {'session_id'})
        _validate_session_id(clean_payload['session_id'])
    elif request_type == 'set_mimic_result':
        _require_payload_keys(clean_payload, {'session_id', 'result'})
        _validate_session_id(clean_payload['session_id'])
        if clean_payload['result'] not in MIMIC_RESULTS:
            raise ProtocolError('web_malformed_request')
    elif request_type == 'reset_safety':
        _require_payload_keys(clean_payload, set())

    return BridgeRequest(
        request_id=request_id,
        type=request_type,
        timestamp=timestamp,
        payload=clean_payload,
    )


def make_ack(
    request_id: str,
    accepted: bool,
    reason: str,
    **fields: Any,
) -> Dict[str, Any]:
    """Build the acknowledgement shape consumed by the current frontend."""
    ack = {
        'type': 'ack',
        'request_id': request_id,
        'accepted': bool(accepted),
        'reason': str(reason),
        'timestamp': utc_now_z(),
    }
    ack.update(fields)
    return ack
