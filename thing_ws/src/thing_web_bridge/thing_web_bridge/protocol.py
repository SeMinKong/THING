"""WebSocket JSON contract shared by the browser and ROS 2 bridge."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from threading import Lock
import time
from typing import Any, Dict, Mapping, Optional


# 6.4절이 MVP 계약으로 고정한 top-level 여덟 필드다. 이름·순서를 바꾸지 않는다.
SNAPSHOT_FIELDS = (
    'timestamp',
    'mode',
    'recording_state',
    'landmarks',
    'motor_state',
    'safety_state',
    'control_state',
    'recording',
)

# 6.4절은 두 갈래를 구분한다. control_state·recording은 동결 스키마 원문을
# 그대로 싣고, landmarks·motor_state·safety_state만 파생 표시 객체다. 파생값이
# 필요한 이유는 SafetyState의 reset 가능 여부와 hand-loss latch가 develop 스키마
# 밖 값이기 때문이며, 앞의 두 객체는 원문만으로 FR-19·24·26을 충족한다.
VERBATIM_SECTIONS = ('control_state', 'recording')

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

# 내부 제어 웹의 CONNECTION_STATE·CONNECTION_KEYS와 같은 값·같은 순서다
# (web/frontend/src/config/messageProtocol.js). bool 두 값으로는 "아직 못 받음"과
# "끊김"을 구분할 수 없어 세 번째 값을 둔다 (FR-24).
CONNECTION_UNKNOWN = 'unknown'
CONNECTION_UP = 'up'
CONNECTION_DOWN = 'down'
CONNECTION_KEYS = ('jetson', 'rpi', 'ros2', 'camera', 'motor')

# FR-01 / FR-11의 MIMIC 유효 기준. Web Bridge는 표시 상태만 파생하며 제어
# 판정은 Raspberry Pi가 한다. 값이 YAML과 갈리지 않게 노드 파라미터로 주입한다.
HAND_CONFIDENCE_MIN = 0.70
HAND_LOSS_DEBOUNCE_MS = 150
HAND_REACQUIRE_STABLE_MS = 300

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
        # 읽기 실패 숫자는 JSON null로 보낸다. 모터 통신 실패 시 NaN이 정상적으로
        # 도착하므로 여기서 예외를 던지면 ROS 구독 콜백에서 노드가 죽는다.
        return value if math.isfinite(value) else None
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
    """
    Serialize ``ControlState`` verbatim.

    6.4절이 "control_state는 ControlState.msg 원문을 그대로 싣고"로 정했으므로
    enum을 symbol로 바꾸지 않는다. 정수 상수가 그대로 나간다. 표시용 symbol은
    top-level ``mode`` mirror가 담당한다.
    """
    return _plain(message)


def recording_state_payload(message: Any) -> Dict[str, Any]:
    """
    Serialize ``RecordingState`` verbatim except for uint64 session IDs.

    6.4절이 원문 유지를 요구하면서 Session ID만 예외로 "10진 문자열로
    직렬화하며 세션 없음은 '0'으로 표현한다"고 정했다. JSON 숫자로 보내면
    JavaScript가 63-bit 값의 정밀도를 잃기 때문이다.
    """
    payload = _plain(message)
    for key in ('active_session_id', 'last_session_id'):
        session_id = payload.get(key, 0)
        if isinstance(session_id, bool) or not isinstance(session_id, int):
            raise ProtocolError('invalid_session_id')
        if session_id < 0:
            raise ProtocolError('invalid_session_id')
        payload[key] = str(session_id)
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
    """Serialize ``SafetyState`` and derive the FR-35 recovery path."""
    payload = _plain(message)
    state = _symbol(payload.get('state'), SAFETY_STATES, 'safety_state')
    payload['state'] = state

    # FR-35: /thing/reset_safety는 SAFE·FAULT·ESTOP에서만 복구 요청으로 쓰고
    # HOLD·READY·RUN·RESET에서는 거부한다.
    #
    # 복구 경로 안내와 거부 사유는 여기서 만들지 않는다. 내부 제어 웹이 이미
    # RESET_ALLOWED_STATES로 state에서 직접 파생하고 있어(FR-27은 "웹이 표시"를
    # 요구한다) 브리지가 같은 판단을 중복 발행하면 읽는 곳 없는 필드가 된다.
    payload['reset_allowed'] = state in ('SAFE', 'FAULT', 'ESTOP')
    return payload


def hand_command_payload(message: Any) -> Dict[str, Any]:
    """Serialize the latest validated seven-axis command for display."""
    payload = _plain(message)
    payload['source'] = _symbol(
        payload.get('source'), HAND_SOURCES, 'hand_source')
    return payload


class SnapshotStore:
    """Keep the latest ROS state and build replacement-only snapshots."""

    def __init__(
        self,
        hand_confidence_min: float = HAND_CONFIDENCE_MIN,
        hand_loss_debounce_ms: int = HAND_LOSS_DEBOUNCE_MS,
        hand_reacquire_stable_ms: int = HAND_REACQUIRE_STABLE_MS,
        data_stale_after_ms: int = 1000,
        state_stale_after_ms: int = 5000,
        clock: Any = None,
    ) -> None:
        """Initialize required fields with fail-closed empty values."""
        if hand_confidence_min <= 0.0 or hand_confidence_min > 1.0:
            raise ValueError('hand_confidence_min must be in (0.0, 1.0]')
        for name, value in (
            ('hand_loss_debounce_ms', hand_loss_debounce_ms),
            ('hand_reacquire_stable_ms', hand_reacquire_stable_ms),
            ('data_stale_after_ms', data_stale_after_ms),
            ('state_stale_after_ms', state_stale_after_ms),
        ):
            if int(value) <= 0:
                raise ValueError(f'{name} must be positive')
        self._lock = Lock()
        self._clock = clock if clock is not None else time.monotonic
        self._hand_confidence_min = float(hand_confidence_min)
        self._hand_loss_debounce = int(hand_loss_debounce_ms) / 1000.0
        self._hand_reacquire_stable = int(hand_reacquire_stable_ms) / 1000.0
        self._data_stale_after = int(data_stale_after_ms) / 1000.0
        self._state_stale_after = int(state_stale_after_ms) / 1000.0

        self._mode = 'DISABLED'
        self._recording_state = 'IDLE'
        self._landmarks: Dict[str, Any] = {}
        self._motor_state: Dict[str, Any] = {}
        self._safety_state: Dict[str, Any] = {}
        self._control_state: Dict[str, Any] = {}
        self._recording: Dict[str, Any] = {}
        self._last_hand_command: Dict[str, Any] = {}

        # 토픽별 마지막 수신 monotonic 시각. None은 "아직 받지 못함"이다.
        self._received_at: Dict[str, Optional[float]] = {
            'landmarks': None,
            'motor_state': None,
            'safety_state': None,
            'control_state': None,
            'recording': None,
            'last_hand_command': None,
        }

        # FR-27: hand-loss latch는 ROS 메시지에 필드를 추가하지 않고
        # HandLandmarks·SafetyState를 바탕으로 Web Bridge가 파생한다.
        self._hand_valid_since: Optional[float] = None
        self._hand_invalid_since: Optional[float] = None
        self._hand_loss_latched = False

    def _mark(self, key: str) -> None:
        self._received_at[key] = self._clock()

    def _age(self, key: str) -> Optional[float]:
        received_at = self._received_at.get(key)
        if received_at is None:
            return None
        return max(0.0, self._clock() - received_at)

    def _fresh(self, key: str, limit: float) -> bool:
        age = self._age(key)
        return age is not None and age <= limit

    def update_control_state(self, message: Any) -> None:
        """Store verbatim ControlState and derive the top-level mode mirror."""
        payload = control_state_payload(message)
        # 6.4절: top-level mode는 control_state.active_mode에서 파생한 표시용
        # mirror이며 두 표현은 항상 일치한다. 원문은 정수이므로 여기서 symbol을
        # 만든다.
        mode = _symbol(payload.get('active_mode'), CONTROL_MODES, 'mode')
        with self._lock:
            self._control_state = payload
            self._mode = mode
            self._mark('control_state')

    def update_recording_state(self, message: Any) -> None:
        """Store verbatim RecordingState and derive the state mirror."""
        payload = recording_state_payload(message)
        # 6.4절: recording_state도 recording.state에서 파생한 mirror다.
        state = _symbol(
            payload.get('state'), RECORDING_STATES, 'recording_state')
        with self._lock:
            self._recording = payload
            self._recording_state = state
            self._mark('recording')

    def update_landmarks(self, message: Any) -> None:
        """Store the latest landmarks and advance the hand-loss latch."""
        payload = landmarks_payload(message)
        with self._lock:
            self._landmarks = payload
            self._mark('landmarks')
            self._advance_hand_latch_locked(payload)

    def update_motor_state(self, message: Any) -> None:
        """Store the latest seven-motor status object."""
        payload = motor_state_payload(message)
        with self._lock:
            self._motor_state = payload
            self._mark('motor_state')

    def update_safety_state(self, message: Any) -> None:
        """Store the latest safety display object."""
        payload = safety_state_payload(message)
        with self._lock:
            self._safety_state = payload
            self._mark('safety_state')
            # FR-01: 재검출만으로는 latch를 해제하지 않는다. 제어가 실제로
            # 재개된 RUN을 관측했을 때만 표시 latch를 닫는다.
            if payload['state'] == 'RUN':
                self._hand_loss_latched = False

    def update_hand_command(self, message: Any) -> None:
        """Store the latest validated command for seven-axis display."""
        payload = hand_command_payload(message)
        with self._lock:
            self._last_hand_command = payload
            self._mark('last_hand_command')

    def _advance_hand_latch_locked(self, payload: Mapping[str, Any]) -> None:
        """Track FR-01 debounce and reacquire windows from HandLandmarks."""
        now = self._clock()
        confidence = payload.get('confidence')
        valid = bool(
            payload.get('detected')
            and payload.get('handedness') == 'RIGHT'
            and isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and float(confidence) >= self._hand_confidence_min
        )
        if valid:
            self._hand_invalid_since = None
            if self._hand_valid_since is None:
                self._hand_valid_since = now
            return
        self._hand_valid_since = None
        if self._hand_invalid_since is None:
            self._hand_invalid_since = now
        if now - self._hand_invalid_since >= self._hand_loss_debounce:
            self._hand_loss_latched = True

    def _hand_display_locked(self) -> Dict[str, Any]:
        """
        Build the hand-loss display fields the internal web reads.

        이름과 타입은 내부 제어 웹이 이미 기대하는 것에 맞춘다
        (``landmarks.hand_loss_latched`` / ``reacquire_elapsed_ms`` /
        ``reacquire_stable_ms``). 웹은 "재검출 진행률은 브릿지가 줄 때만
        표시하고 근사로 만들어 내지 않는다"고 되어 있어 경과 ms를 실제 값으로
        보내야 진행 안내가 가능하다 (FR-27).
        """
        if self._hand_valid_since is None:
            elapsed_ms = 0
        else:
            elapsed = self._clock() - self._hand_valid_since
            elapsed_ms = int(max(0.0, elapsed) * 1000.0)
        return {
            'detect_valid': self._hand_valid_since is not None,
            'hand_loss_latched': self._hand_loss_latched,
            'reacquire_elapsed_ms': elapsed_ms,
            'reacquire_stable_ms': int(self._hand_reacquire_stable * 1000.0),
            'confidence_min': self._hand_confidence_min,
        }

    def _connection_status_locked(self) -> Dict[str, str]:
        """Derive the five device groups the internal web shows (FR-24)."""
        landmarks_fresh = self._fresh('landmarks', self._data_stale_after)
        motor_fresh = self._fresh('motor_state', self._data_stale_after)
        rpi_keys = ('control_state', 'safety_state', 'motor_state')
        rpi_seen = any(
            self._received_at[key] is not None for key in rpi_keys)
        rpi_fresh = (
            self._fresh('control_state', self._state_stale_after)
            or self._fresh('safety_state', self._state_stale_after)
            or motor_fresh
        )
        ros_seen = any(
            value is not None for value in self._received_at.values())
        ros_fresh = landmarks_fresh or rpi_fresh

        def state(seen: bool, fresh: bool) -> str:
            if not seen:
                return CONNECTION_UNKNOWN
            return CONNECTION_UP if fresh else CONNECTION_DOWN

        return {
            # snapshot 자체가 Jetson의 Web Bridge에서 생성되므로 도달했다는
            # 사실이 Jetson 생존의 증거다.
            'jetson': CONNECTION_UP,
            'rpi': state(rpi_seen, rpi_fresh),
            'ros2': state(ros_seen, ros_fresh),
            # camera는 image_raw를 구독하지 않고 vision chain의 산출물인
            # landmarks 신선도로 대리 판정한다. 상세 원인은 diagnostics 소관
            # (NFR-09).
            'camera': state(
                self._received_at['landmarks'] is not None, landmarks_fresh),
            'motor': state(
                self._received_at['motor_state'] is not None, motor_fresh),
        }

    def _with_freshness_locked(
        self,
        key: str,
        limit: float,
    ) -> Dict[str, Any]:
        """
        Copy one section and attach stale and age fields.

        stale·age_ms가 있어야 웹이 오래된 데이터와 끊긴 연결을 구분할 수
        있다 (FR-25).
        """
        source = getattr(self, f'_{key}')
        if not source:
            return {}
        payload = deepcopy(source)
        age = self._age(key)
        payload['age_ms'] = None if age is None else int(age * 1000.0)
        payload['stale'] = not self._fresh(key, limit)
        return payload

    def _verbatim_locked(self, key: str) -> Dict[str, Any]:
        """
        Copy one section without adding anything to the frozen schema.

        6.4절이 control_state·recording을 "원문을 그대로" 싣도록 정했으므로
        age_ms·stale을 붙이지 않는다. 신선도가 필요한 곳은 connection_status가
        같은 수신 시각에서 따로 파생한다.
        """
        source = getattr(self, f'_{key}')
        return deepcopy(source) if source else {}

    def snapshot(self) -> Dict[str, Any]:
        """Return one independent snapshot matching the browser contract."""
        with self._lock:
            landmarks = self._with_freshness_locked(
                'landmarks', self._data_stale_after)
            if landmarks:
                landmarks.update(self._hand_display_locked())
            return {
                'timestamp': utc_now_z(),
                'mode': self._mode,
                'recording_state': self._recording_state,
                'landmarks': landmarks,
                'motor_state': self._with_freshness_locked(
                    'motor_state', self._data_stale_after),
                'safety_state': self._with_freshness_locked(
                    'safety_state', self._state_stale_after),
                'control_state': self._verbatim_locked('control_state'),
                'recording': self._verbatim_locked('recording'),
                'last_hand_command': self._with_freshness_locked(
                    'last_hand_command', self._data_stale_after),
                'connection_status': self._connection_status_locked(),
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
    # 계약(docs/interfaces.md ACK reason 표)은 두 사유를 구분한다.
    # web_malformed_request: 표준 10진 문자열 형식이 아님.
    # invalid_session_id: 형식은 맞지만 값이 0이거나 63-bit를 넘음.
    #
    # str.isdigit()는 '²' 같은 유니코드 숫자도 통과시키는데 그런 값은
    # int()에서 미분류 예외가 나 reader task째 죽으므로 ascii로 먼저 막는다.
    # 선행 0('00','007')은 표준 표기가 아니라 형식 오류로 본다. 단 '0'
    # 하나는 0의 표준 표기이므로 형식이 아니라 값 사유로 거부한다.
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ProtocolError('web_malformed_request')
    if len(value) > 1 and value.startswith('0'):
        raise ProtocolError('web_malformed_request')
    number = int(value)
    if number == 0 or number >= 2 ** 63:
        raise ProtocolError('invalid_session_id')


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
