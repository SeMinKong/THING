// ============================================================================
// WebSocket <-> ROS 2 프로토콜 정의 — 요구사항 명세서 V6.3 단독 기준
// ----------------------------------------------------------------------------
// 계약 출처
//   6.4절  서버→클라이언트 snapshot v1 (endpoint·top-level 6필드 고정)
//   6.3절  ROS 2 토픽·서비스 런타임 이름 (단일 기준)
//   FR-11  타이밍 상수
//   FR-30  thing_interfaces 동결 (필드명을 축약·변형하지 않는다)
//   FR-34  MODE / OWNER 상수
//   FR-35  SafetyState
//   FR-37  거부 사유 프로젝트 표준
//   FR-38  canonical gesture 와 alias
//
// 이 파일은 backend/thing_bridge/consumers.py 와 쌍으로 유지한다.
// 필드를 바꿀 때는 두 파일과 docs/interfaces.md 를 함께 갱신한다.
// ============================================================================

// ---------------------------------------------------------------------------
// HandCommand.msg
// ---------------------------------------------------------------------------
// uint8 SOURCE_UNKNOWN=0 / SOURCE_MIMIC=1 / SOURCE_TELEOP=2 /
//       SOURCE_GESTURE=3 / SOURCE_SEQUENCE=4 / SOURCE_SAFETY=5
export const HAND_SOURCE = {
  UNKNOWN: "UNKNOWN",
  MIMIC: "MIMIC",
  TELEOP: "TELEOP",
  GESTURE: "GESTURE",
  SEQUENCE: "SEQUENCE",
  SAFETY: "SAFETY",
};

// FR-30: 7논리축은 배열이 아니라 고정 필드다.
// thumb_opp = 엄지 가로 방향 대립축, thumb_abd = 엄지 세로 방향 벌림축.
// 아래 배열은 화면 표시·순회용 메타데이터이며 실제 payload 는
// { thumb_flex, thumb_opp, ... } 형태의 고정 필드 객체다.
export const HAND_AXES = [
  { key: "thumb_flex", label: "엄지 굴곡" },
  { key: "thumb_opp", label: "엄지 대립" },
  { key: "thumb_abd", label: "엄지 벌림" },
  { key: "index_flex", label: "검지 굴곡" },
  { key: "middle_flex", label: "중지 굴곡" },
  { key: "ring_flex", label: "약지 굴곡" },
  { key: "little_flex", label: "소지 굴곡" },
];

export const HAND_AXIS_KEYS = HAND_AXES.map((axis) => axis.key);

// FR-23 / FR-32: 유한값, 0.0~1.0 밖의 값과 누락 축을 전달하지 않는다.
// 서버(consumers.py)와 command_guard 가 다시 검증한다.
export function isValidAxisValues(values) {
  if (!values || typeof values !== "object") return false;
  return HAND_AXIS_KEYS.every((key) => {
    const v = values[key];
    return typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1;
  });
}

export function zeroAxisValues() {
  return Object.fromEntries(HAND_AXIS_KEYS.map((key) => [key, 0]));
}

// FR-32: speed_limit 은 0.0 초과 1.0 이하
export function isValidSpeedLimit(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 0 && value <= 1;
}

// ---------------------------------------------------------------------------
// ControlState.msg — FR-34
// ---------------------------------------------------------------------------
// 필드명은 축약하지 않고 .msg 그대로 사용한다:
//   { active_mode, active_owner, owner_alive, sequence_running,
//     last_transition_reason, stamp }
export const CONTROL_MODE = {
  DISABLED: "DISABLED",
  MIMIC: "MIMIC",
  MANUAL: "MANUAL",
  TELEOP: "TELEOP",
};

export const CONTROL_OWNER = {
  NONE: "NONE",
  WEB: "WEB",
  LOCAL: "LOCAL",
};

// FR-34: 웹은 WEB owner 로 MIMIC·MANUAL 만 획득한다. TELEOP 은 LOCAL 전용이다.
export const WEB_SELECTABLE_MODES = [CONTROL_MODE.MIMIC, CONTROL_MODE.MANUAL];
export const WEB_OWNER = CONTROL_OWNER.WEB;

// FR-34 정지·해제 조합
export const STOP_MODE = CONTROL_MODE.DISABLED;
export const STOP_OWNER = CONTROL_OWNER.NONE;

// ---------------------------------------------------------------------------
// SafetyState.msg — FR-35
// ---------------------------------------------------------------------------
export const SAFETY_STATES = [
  "INIT", "READY", "RUN", "HOLD", "SAFE", "FAULT", "ESTOP",
];

// FR-27: 위험 상태에서 일반 조작을 비활성화한다
export const UNSAFE_TO_OPERATE_STATES = ["SAFE", "FAULT", "ESTOP"];

// FR-34: "획득: READY 에서 SetControlMode(활성 mode, WEB|LOCAL)"
export const ACQUIRE_ALLOWED_STATES = ["READY"];

// FR-38: "active mode=MANUAL, 유효 WEB owner, SafetyState=READY|RUN"
export const OPERATION_ALLOWED_STATES = ["READY", "RUN"];

// FR-35: reset_safety 는 SAFE·FAULT·ESTOP 에서만 사용한다.
// HOLD 에서는 명시적 STOP 절차를 쓴다.
export const RESET_ALLOWED_STATES = ["SAFE", "FAULT", "ESTOP"];

// ---------------------------------------------------------------------------
// RecordingState.msg — FR-18 / FR-26
// ---------------------------------------------------------------------------
export const RECORDING_STATE = {
  IDLE: "IDLE",
  STARTING: "STARTING",
  RECORDING: "RECORDING",
  STOPPING: "STOPPING",
  COMPLETED: "COMPLETED",
  FAILED: "FAILED",
  INTERRUPTED: "INTERRUPTED",
};

export const RECORDING_RESULT = {
  UNSET: "UNSET",
  SUCCESS: "SUCCESS",
  FAILURE: "FAILURE",
};

// FR-19: 기록 중 새 일반 mode 요청은 거부된다
export const RECORDING_BUSY_STATES = [
  RECORDING_STATE.STARTING,
  RECORDING_STATE.RECORDING,
  RECORDING_STATE.STOPPING,
];

// ---------------------------------------------------------------------------
// HandLandmarks.msg
// ---------------------------------------------------------------------------
export const HANDEDNESS = {
  UNKNOWN: "UNKNOWN",
  LEFT: "LEFT",
  RIGHT: "RIGHT",
};

/**
 * 손 검출 표시 상태.
 *
 * 8.1절이 미검출을 "confidence 0.70 미만 **또는** 미검출" 로 정의한다.
 * detected 만 보면 confidence 0.4 인 프레임을 "검출됨" 으로 표시하게 되는데,
 * 그 사이 ROS 쪽은 이미 hand-loss 로 판정해 HandCommand 발행을 멈춘 상태다
 * (FR-06, FR-35). 화면과 실제 제어 상태가 어긋나므로 임계값을 함께 본다.
 *
 * 반환값
 *   "unknown"        landmarks 를 아직 받지 못했다 (6.4절의 {})
 *   "detected"       유효 검출
 *   "low_confidence" 검출됐지만 confidence 가 임계 미달 → ROS 기준 미검출
 *   "not_detected"   미검출
 */
export const HAND_DETECTION = {
  UNKNOWN: "unknown",
  DETECTED: "detected",
  LOW_CONFIDENCE: "low_confidence",
  NOT_DETECTED: "not_detected",
};

export function handDetectionState(landmarks) {
  if (!landmarks || typeof landmarks !== "object" || Object.keys(landmarks).length === 0) {
    return HAND_DETECTION.UNKNOWN;
  }
  if (!landmarks.detected) return HAND_DETECTION.NOT_DETECTED;
  const confidence = landmarks.confidence;
  if (typeof confidence === "number" && confidence < TIMING.HAND_CONFIDENCE_MIN) {
    return HAND_DETECTION.LOW_CONFIDENCE;
  }
  return HAND_DETECTION.DETECTED;
}

// ---------------------------------------------------------------------------
// FR-38 canonical gesture 와 alias
// ---------------------------------------------------------------------------
// 이름이 한 글자만 달라도 /thing/execute_gesture 호출이 실패한다.
export const CANONICAL_GESTURES = ["open", "fist", "pinch", "cylindrical_grasp"];

// "home|paper→open, rock→fist alias 는 중복 7축 값을 만들지 않는다"
export const GESTURE_ALIASES = { home: "open", paper: "open", rock: "fist" };

// FR-39 (Could): countdown, scissors_rock_paper 만 지원한다
export const SEQUENCE_IDS = ["countdown", "scissors_rock_paper"];

// ---------------------------------------------------------------------------
// 클라이언트 → 서버 요청 type
// ---------------------------------------------------------------------------
// 6.3절 서비스 이름과 1:1 대응한다.
export const CLIENT_MESSAGE = {
  SET_CONTROL_MODE: "set_control_mode",   // /thing/set_control_mode
  STOP: "stop",                           // SetControlMode(DISABLED, NONE)
  EXECUTE_GESTURE: "execute_gesture",     // /thing/execute_gesture
  EXECUTE_SEQUENCE: "execute_sequence",   // /thing/execute_sequence
  START_RECORDING: "start_recording",     // /thing/start_recording
  STOP_RECORDING: "stop_recording",       // /thing/stop_recording
  SET_MIMIC_RESULT: "set_mimic_result",   // /thing/set_mimic_result
  RESET_SAFETY: "reset_safety",           // /thing/reset_safety
};

// ---------------------------------------------------------------------------
// 서버 → 클라이언트
// ---------------------------------------------------------------------------
// 6.4절 snapshot 은 type 필드가 없는 top-level 객체다.
// 요청 응답만 type="ack" 를 가진다.
export const SERVER_MESSAGE = { ACK: "ack" };

// 6.4절이 고정한 top-level 6필드
export const SNAPSHOT_FIXED_FIELDS = [
  "timestamp", "mode", "recording_state", "landmarks", "motor_state", "safety_state",
];

/**
 * 받은 메시지가 snapshot 인지 판별한다.
 *
 * snapshot 은 type 필드가 없고 timestamp·mode 가 최상위에 있다 (6.4절).
 * 반드시 boolean 을 돌려준다. 단축 평가 결과를 그대로 반환하면
 * isSnapshot(null) 이 null 이 되어 호출부에서 혼란이 생긴다.
 */
export function isSnapshot(message) {
  return Boolean(
    message
    && typeof message === "object"
    && message.type === undefined
    && typeof message.timestamp === "string"
    && typeof message.mode === "string",
  );
}

// ---------------------------------------------------------------------------
// FR-37 거부 사유 — 프로젝트 표준
// ---------------------------------------------------------------------------
// "MVP 구현은 accepted, invalid_mode, owner_conflict, safety_not_ready,
//  recording_active, motion_active 를 프로젝트 표준으로 문서화한다."
export const REJECT_REASON = {
  ACCEPTED: "accepted",
  INVALID_MODE: "invalid_mode",
  OWNER_CONFLICT: "owner_conflict",
  SAFETY_NOT_READY: "safety_not_ready",
  RECORDING_ACTIVE: "recording_active",
  MOTION_ACTIVE: "motion_active",
};

// 웹 전송 계층 전용 사유 (web_ 접두사)
export const WEB_REASON = {
  BRIDGE_OFFLINE: "web_bridge_offline",
  MALFORMED: "web_malformed_request",
  UNKNOWN_TYPE: "web_unknown_type",
  STALE: "web_stale_request",
  // FR-18 / FR-40: 요청한 Session ID 가 RecordingState 의 세션과 다르다.
  SESSION_MISMATCH: "web_session_mismatch",
};

// FR-27: "reset 가능 여부와 거부 사유를 구분하고"
// 사용자가 다음에 무엇을 해야 하는지까지 알려준다.
const REASON_MESSAGES = {
  [REJECT_REASON.INVALID_MODE]:
    "지금 이 모드로 바꿀 수 없습니다. 모방↔조작은 직접 전환할 수 없으니 먼저 정지(STOP)로 비활성화한 뒤 다시 선택하세요.",
  [REJECT_REASON.OWNER_CONFLICT]:
    "다른 조작 주체가 제어권을 가지고 있습니다. 해당 주체가 해제한 뒤 다시 시도하세요.",
  [REJECT_REASON.SAFETY_NOT_READY]:
    "안전 상태가 준비(READY)가 아닙니다. 정지 후 안정화를 기다리거나, 위험 상태라면 원인을 해소하고 안전 초기화를 수행하세요.",
  [REJECT_REASON.RECORDING_ACTIVE]:
    "기록이 진행 중이거나 판정이 끝나지 않았습니다. 기록을 종료하고 성공·실패를 판정한 뒤 다시 시도하세요.",
  [REJECT_REASON.MOTION_ACTIVE]:
    "동작이 실행 중입니다. 새 동작은 대기열에 쌓이지 않습니다. 끝나기를 기다리거나 정지(STOP)를 누르세요.",
  [WEB_REASON.BRIDGE_OFFLINE]:
    "ROS 2 브릿지에 연결되어 있지 않아 요청을 전달할 수 없습니다.",
  [WEB_REASON.MALFORMED]:
    "요청 형식이 올바르지 않습니다. 화면을 새로 고친 뒤 다시 시도하세요.",
  [WEB_REASON.UNKNOWN_TYPE]:
    "서버가 알 수 없는 요청입니다. 웹과 서버 버전이 다를 수 있습니다.",
  [WEB_REASON.STALE]:
    "요청 시각이 서버 시각과 크게 어긋났습니다. 기기 시간을 확인하세요.",
  [WEB_REASON.SESSION_MISMATCH]:
    "화면에 표시된 세션과 로봇의 현재 세션이 다릅니다. 화면을 새로 고쳐 최신 기록 상태를 확인하세요.",
};

/** 거부 사유를 사용자 문구로 바꾼다. 알 수 없는 사유는 원문을 함께 보여준다. */
export function describeReason(reason) {
  if (!reason || reason === REJECT_REASON.ACCEPTED) return "";
  return REASON_MESSAGES[reason] || `요청이 거부되었습니다. (${reason})`;
}

/** 이 사유가 안전 초기화(reset_safety)로 해결되는 종류인지. */
export function reasonNeedsSafetyReset(reason) {
  return reason === REJECT_REASON.SAFETY_NOT_READY;
}

// ---------------------------------------------------------------------------
// FR-24 장치 연결 상태
// ---------------------------------------------------------------------------
export const CONNECTION_KEYS = ["jetson", "rpi", "ros2", "camera", "motor"];

/**
 * 장치 연결 표시 상태.
 *
 * bool 두 값으로는 "브릿지에서 아직 못 받았다" 와 "끊겼다" 를 구분할 수 없다.
 * 특히 camera 를 false 로 단정하면 MJPEG 스트림이 정상인데도 영상이 가려진다.
 * FR-24 "가짜 값으로 채우지 않는다" 를 지키려면 세 번째 값이 필요하다.
 * 서버(`normalize.py`)가 같은 세 값으로 정규화해 내려준다.
 */
export const CONNECTION_STATE = {
  UNKNOWN: "unknown",
  UP: "up",
  DOWN: "down",
};

export function emptyConnectionStatus() {
  return Object.fromEntries(
    CONNECTION_KEYS.map((key) => [key, CONNECTION_STATE.UNKNOWN]),
  );
}

/** 관측된 단절만 true. "아직 모름" 은 단절로 취급하지 않는다. */
export function isDeviceDown(value) {
  return value === CONNECTION_STATE.DOWN;
}

/** 표시·조작을 허용할 수 있는 상태. 미확인은 막지 않는다. */
export function isDeviceUsable(value) {
  return value !== CONNECTION_STATE.DOWN;
}

// ---------------------------------------------------------------------------
// FR-07: 7채널 서보
// ---------------------------------------------------------------------------
export const MOTOR_COUNT = 7;

// ---------------------------------------------------------------------------
// FR-11 타이밍 상수 (판정 주체는 Raspberry Pi. 웹은 갱신 주기에만 사용한다)
// ---------------------------------------------------------------------------
/**
 * 웹이 실제로 쓰는 타이밍만 남긴다.
 *
 * FR-41 은 FR-11 timeout 을 YAML 로 관리하라고 한다. 웹이 복제본을 들고 있으면
 * YAML 이 바뀔 때 두 곳이 갈린다. 나머지 8개(COMMAND_HOLD/SAFE_TIMEOUT,
 * OWNER_LEASE_TIMEOUT, HAND_LOSS_DEBOUNCE, HAND_REACQUIRE_STABLE, STOP_SETTLE,
 * FAULT_CLEAR_STABLE, ESTOP_RELEASE_STABLE, SAFE_MOTION_TIMEOUT)는 판정 주체인
 * Raspberry Pi 와 hand-loss latch 를 파생하는 서버(`normalize.py`)만 쓴다.
 */
export const TIMING = {
  //: FR-34 갱신 주기. 웹이 직접 타이머를 돌린다.
  CONTROL_RENEW_PERIOD_MS: 1000,
  //: 8.1절 "confidence 0.70 미만 또는 미검출". 검출 표시 판정에 쓴다.
  HAND_CONFIDENCE_MIN: 0.7,
};
