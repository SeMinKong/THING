// ============================================================================
// WebSocket <-> ROS 2 프로토콜 정의 — 요구사항 명세서 V7.1 + interfaces.md / thing_interfaces 기준
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
// ── 분담 원칙 ───────────────────────────────────────────────────────────────
// V7.0 은 1.5.2절에서 내부망 웹의 Django 를 삭제했다. 브라우저는 Jetson
// thing_web_bridge 노드의 /ws/robot-state 에 직접 붙는다 (4.1·4.9절).
//
// 브릿지는 ROS 2 제어 경로 위에 있으므로 가공을 요구하지 않는다.
//   브릿지 = .msg 원문 dump 를 snapshot 에 얹는 것까지
//   프런트 = enum 정규화, 장치 상태 파생, hand-loss 근사, 표시·잠금 판정
//
// 브릿지에 요구하는 추가 필드는 control_state·recording 두 개뿐이다.
// 계약 전문은 web/docs/interfaces-bridge.md.
//
// ── 근거 없는 값은 여기 두지 않는다 ─────────────────────────────────────────
// 명세서·.msg·.srv 로 결정되지 않는 값은 전부 config/pending.js 에 모아 두고
// 여기서는 참조만 한다. 이 파일에 남는 숫자는 명세에 근거가 있는 것뿐이다.
// ============================================================================
import { PENDING, SPEC, THRESHOLD } from "./pending.js";
import { diag, OWNER } from "./diagnostics.js";

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

// ---------------------------------------------------------------------------
// enum 정규화 — 브릿지가 .msg 원문을 그대로 dump 하는 경우 대응
// ---------------------------------------------------------------------------
// rosidl_runtime_py.convert.message_to_ordereddict 는 uint8 상수를 정수로 싣는다.
// active_mode 가 "MIMIC" 이 아니라 1 로 온다. 브릿지가 문자열로 바꾸려면 매핑
// 코드를 들고 있어야 하므로 그 부담을 프런트가 가져온다.
//
// 아래 표는 "상수값 = .msg 선언 순서" 를 전제로 한다.
// interfaces-bridge.md 확인 항목 1 이며 브릿지 확인 대상이다.
// 6.4절이 top-level mode·recording_state 는 symbolic string 으로 고정했으므로
// 그 둘은 정수로 오지 않는다.
export const CONTROL_MODE_BY_ORDINAL = ["DISABLED", "MIMIC", "MANUAL", "TELEOP"];
export const CONTROL_OWNER_BY_ORDINAL = ["NONE", "WEB", "LOCAL"];
export const HAND_SOURCE_BY_ORDINAL = [
  "UNKNOWN", "MIMIC", "TELEOP", "GESTURE", "SEQUENCE", "SAFETY",
];

/**
 * uint8 상수 또는 symbolic string 을 symbolic string 으로 통일한다.
 *
 * 표에 없는 값을 fallback 으로 뭉개지 않는다. `SafetyState.RESET=7` 처럼 상수가
 * 추가됐는데 이 표가 안 따라가면, 뭉갤 경우 state=7 이 "INIT" 으로 조용히
 * 오표시된다. `UNKNOWN(7)` 로 드러내면 화면만 봐도 알 수 있고, 어떤 화이트리스트
 * 에도 안 걸리므로 조작은 fail-closed 로 막힌다.
 *
 * 문자열은 표에 없어도 그대로 통과시킨다. 원문이 보이는 편이 통합 중에 빠르다.
 */
export function toSymbol(value, table, fallback = null, label = "enum") {
  if (typeof value === "string" && value.length > 0) return value;
  if (Number.isInteger(value)) {
    if (value >= 0 && value < table.length) return table[value];
    diag.error({
      code: `UNKNOWN_ENUM_${label}_${value}`,
      owner: OWNER.BRIDGE,
      what: `${label} 에 모르는 정수 ${value} 가 왔습니다`,
      why: `웹은 UNKNOWN(${value}) 로 표시하고 조작을 막습니다(fail-closed). `
        + ".msg 에 상수가 추가됐는데 웹의 매핑표가 안 따라간 경우입니다.",
      fix: "src/config/messageProtocol.js 의 해당 *_BY_ORDINAL / SAFETY_STATES 배열에 "
        + "새 상수를 선언 순서대로 추가하세요.",
      ref: "web/docs/interfaces-bridge.md 1.3",
      detail: { 받은값: value, 아는범위: `0~${table.length - 1}`, 표: table },
    });
    return `UNKNOWN(${value})`;
  }
  if (value !== undefined && value !== null) {
    diag.error({
      code: `ENUM_BAD_TYPE_${label}`,
      owner: OWNER.BRIDGE,
      what: `${label} 의 타입이 숫자도 문자열도 아닙니다`,
      why: "웹이 값을 해석하지 못해 기본값으로 표시합니다.",
      fix: "uint8 정수 또는 symbolic string 으로 보내세요.",
      ref: "web/docs/interfaces-bridge.md 1.3",
      detail: { 받은값: value, 타입: typeof value },
    });
  }
  return fallback;
}

// FR-34 정지·해제 조합
export const STOP_MODE = CONTROL_MODE.DISABLED;
export const STOP_OWNER = CONTROL_OWNER.NONE;

// ---------------------------------------------------------------------------
// SafetyState.msg — FR-35
// ---------------------------------------------------------------------------
// V7: "SafetyState.msg: 기존 상수 뒤 uint8 RESET=7 추가" (FR-30 승인된 변경 사항).
// RESET 은 명시적 정상 STOP 뒤 모터를 움직이지 않고 torque OFF 를 재확인하는 상태이며
// /thing/reset_safety 와는 다른 정상 제어 상태다.
// 배열 순서가 곧 uint8 상수값이다.
export const SAFETY_STATES = [
  "INIT", "READY", "RUN", "HOLD", "SAFE", "FAULT", "ESTOP", "RESET",
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

export const RECORDING_STATE_BY_ORDINAL = [
  "IDLE", "STARTING", "RECORDING", "STOPPING", "COMPLETED", "FAILED", "INTERRUPTED",
];
export const RECORDING_RESULT_BY_ORDINAL = ["UNSET", "SUCCESS", "FAILURE"];

// ---------------------------------------------------------------------------
// Session ID — 6.5절
// ---------------------------------------------------------------------------
// "CSPRNG로 만든 0이 아닌 63-bit 양의 정수 ... JSON·API·EC2에서는 10진 문자열로
//  표현한다."
//
// 숫자로 오면 JSON.parse 시점에 이미 손상된다. 받은 뒤에는 복구할 수 없다.
//   브릿지 8531234567890123456 → JS 8531234567890124000
// 그 값을 StopRecording 으로 되돌려 보내면 세션이 닫히지 않는다.

/** 0 이 아닌 숫자 세션 ID = 계약 위반. 값이 이미 손상됐을 수 있다. */
export function isNumericSessionId(value) {
  return typeof value === "number" && value !== 0;
}

/**
 * 표시·전송용 세션 ID. 세션이 없으면 빈 문자열.
 *
 * 6.5절이 "0이 아닌 63-bit 양의 정수" 로 정했으므로 0 은 "세션 없음" 이다.
 * `uint64 active_session_id` 는 세션이 없을 때 0 이고, 이를 그대로 문자열로
 * 바꾸면 "0" 이 되어 truthy 가 된다. 그러면 활성 세션이 없는데도
 * StopRecording(session_id="0") 을 보내게 된다.
 */
export function readSessionId(value) {
  if (value === 0 || value === "0") return "";
  if (isNumericSessionId(value)) {
    diag.error({
      code: "SESSION_ID_NUMERIC",
      owner: OWNER.BRIDGE,
      what: "session_id 가 JSON 숫자로 왔습니다",
      why: "63-bit 값은 JSON.parse 시점에 이미 손상됩니다. 받은 뒤에는 복구할 수 "
        + "없어서, 웹은 기록 종료·판정 전송을 차단합니다. 세션이 안 닫히고 EC2 "
        + "업로드까지 멈춥니다.",
      fix: "브릿지가 uint64 를 10진 문자열로 직렬화해야 합니다. 예) str(session_id)",
      ref: "요구사항 명세서 6.5절 / interfaces-bridge.md 1.4",
      detail: { 받은값: value, 손상가능: value > Number.MAX_SAFE_INTEGER },
    });
  }
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "";
}

// FR-19: 기록 중 새 일반 mode 요청은 거부된다
export const RECORDING_BUSY_STATES = [
  RECORDING_STATE.STARTING,
  RECORDING_STATE.RECORDING,
  RECORDING_STATE.STOPPING,
];

// ---------------------------------------------------------------------------
// HandLandmarks.msg
// ---------------------------------------------------------------------------
// HandLandmarks.handedness 는 uint8(HANDEDNESS_UNKNOWN=0/LEFT=1/RIGHT=2) 이다.
// 화면이 쓰지 않으므로 상수를 두지 않는다. 표시가 필요해지면 그때 ordinal 표를
// 추가한다 — 문자열 상수를 만들면 .msg 와 어긋난다.

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

/** 6.4절이 고정한 top-level 6필드. 진단에서 누락 여부를 짚는 데 쓴다. */
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
// 거부 사유 — docs/interfaces.md / safety_manager.md / FR-18 기준
// ---------------------------------------------------------------------------
// 모든 service·action 응답의 `string reason` 로 온다. command_guard 의 진단
// (diagnostics) reason 은 ack 가 아니라 /thing/diagnostics 채널이므로 넣지 않는다.
// describeReason 은 표에 없는 값을 원문과 함께 fallback 하므로, 로봇이 미확정
// 문자열을 보내도 조용히 깨지지 않는다.
//
// 옛 stop_barrier_pending·stop_barrier_timeout 은 제거했다. interfaces.md 에서
// STOP 배리어는 내부 ROS 토픽(stop_requested/stop_barrier_ack) 메커니즘이고,
// 재획득 차단의 mode 서비스 거부 사유는 stop_in_progress 다.
export const REJECT_REASON = {
  ACCEPTED: "accepted",

  // 모드 서비스 /thing/set_control_mode (interfaces.md)
  INVALID_MODE: "invalid_mode",
  MOTION_ACTIVE: "motion_active",
  STOP_IN_PROGRESS: "stop_in_progress",        // 명시적 STOP 후 500ms 재획득 차단 구간
  OWNER_LEASE_EXPIRED: "owner_lease_expired",  // lease 만료 → DISABLED/NONE 발행 후 거부
  SAFETY_NOT_READY: "safety_not_ready",        // INIT·SAFE·FAULT·ESTOP·RESET (HOLD 는 검증만)
  // ↓ interfaces.md 모드 4종엔 이름이 없으나 서비스가 "녹화 중·타 owner"에서 거부한다.
  //   실제 reason 문자열 미확정 — 회신 전까지 유지(불일치 시 fallback).
  OWNER_CONFLICT: "owner_conflict",
  RECORDING_ACTIVE: "recording_active",

  // Manual Executor /thing/execute_gesture · /thing/execute_sequence (interfaces.md)
  INVALID_GESTURE: "invalid_gesture",
  INVALID_SEQUENCE: "invalid_sequence",
  INVALID_SPEED_LIMIT: "invalid_speed_limit",
  NOT_MANUAL_MODE: "not_manual_mode",
  CONTROL_STATE_UNAVAILABLE: "control_state_unavailable",
  CONTROL_STATE_STALE: "control_state_stale",
  SAFETY_STATE_UNAVAILABLE: "safety_state_unavailable",
  SAFETY_STATE_STALE: "safety_state_stale",
  STOP_LATCHED: "stop_latched",

  // 기록 서비스 /thing/start_recording · /thing/stop_recording (FR-18)
  NOT_MIMIC_MODE: "not_mimic_mode",
  START_FAILED: "start_failed",
  ALREADY_RECORDING: "already_recording",
  RESULT_PENDING: "result_pending",
  NOT_RECORDING: "not_recording",
  SESSION_MISMATCH: "session_mismatch",   // 로봇 StopRecording 사유 — WEB_REASON.SESSION_MISMATCH 와 별개
  STOP_FAILED: "stop_failed",
  PREEMPTED_BY_STOP: "preempted_by_stop",  // 정지 명령으로 대기 중이던 요청이 취소됨
  SUPERSEDED: "superseded",                // 제어권 유지 신호가 새 요청으로 교체됨
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
  // 모드 서비스
  [REJECT_REASON.INVALID_MODE]:
    "지금 이 모드로 바꿀 수 없습니다. 모방↔조작은 직접 전환할 수 없으니 먼저 정지(STOP)로 비활성화한 뒤 다시 선택하세요.",
  [REJECT_REASON.MOTION_ACTIVE]:
    "동작이 실행 중입니다. 새 동작은 대기열에 쌓이지 않습니다. 끝나기를 기다리거나 정지(STOP)를 누르세요.",
  [REJECT_REASON.STOP_IN_PROGRESS]:
    "정지 직후 잠시 재획득이 차단되는 구간입니다. 잠시 후 다시 시도하세요.",
  [REJECT_REASON.OWNER_LEASE_EXPIRED]:
    "제어권 유지 신호가 끊겨 제어권이 해제됐습니다. 안전 상태를 확인한 뒤 모드를 다시 획득하세요.",
  [REJECT_REASON.SAFETY_NOT_READY]:
    "안전 상태가 준비(READY)가 아닙니다. 정지 후 안정화를 기다리거나, 위험 상태라면 원인을 해소하고 안전 초기화를 수행하세요.",
  [REJECT_REASON.OWNER_CONFLICT]:
    "다른 조작 주체가 제어권을 가지고 있습니다. 해당 주체가 해제한 뒤 다시 시도하세요.",
  [REJECT_REASON.RECORDING_ACTIVE]:
    "기록이 진행 중이거나 판정이 끝나지 않았습니다. 기록을 종료하고 성공·실패를 판정한 뒤 다시 시도하세요.",

  // Manual Executor (Gesture·Sequence)
  [REJECT_REASON.INVALID_GESTURE]:
    "지원하지 않는 동작입니다. 열기·주먹·집기·원통 파지만 가능합니다.",
  [REJECT_REASON.INVALID_SEQUENCE]:
    "지원하지 않는 연속 동작입니다. 카운트다운·가위바위보만 가능합니다.",
  [REJECT_REASON.INVALID_SPEED_LIMIT]:
    "속도 값이 허용 범위를 벗어났습니다. (0.0 초과 1.0 이하)",
  [REJECT_REASON.NOT_MANUAL_MODE]:
    "조작 모드가 아닙니다. 조작 모드와 제어권을 먼저 획득하세요.",
  [REJECT_REASON.CONTROL_STATE_UNAVAILABLE]:
    "제어 상태를 확인할 수 없어 명령을 보낼 수 없습니다. 잠시 후 다시 시도하세요.",
  [REJECT_REASON.CONTROL_STATE_STALE]:
    "제어 상태 갱신이 지연되고 있어 명령을 보낼 수 없습니다. 연결을 확인하세요.",
  [REJECT_REASON.SAFETY_STATE_UNAVAILABLE]:
    "안전 상태를 확인할 수 없어 명령을 보낼 수 없습니다. 잠시 후 다시 시도하세요.",
  [REJECT_REASON.SAFETY_STATE_STALE]:
    "안전 상태 갱신이 지연되고 있어 명령을 보낼 수 없습니다. 연결을 확인하세요.",
  [REJECT_REASON.STOP_LATCHED]:
    "정지 이후 아직 새 제어권을 획득하지 않았습니다. 모드를 다시 획득한 뒤 시도하세요.",

  // 기록 서비스
  [REJECT_REASON.NOT_MIMIC_MODE]:
    "기록은 모방 모드에서만 시작할 수 있습니다.",
  [REJECT_REASON.START_FAILED]:
    "기록을 시작하지 못했습니다. 잠시 후 다시 시도하세요.",
  [REJECT_REASON.ALREADY_RECORDING]:
    "이미 기록이 진행 중입니다.",
  [REJECT_REASON.RESULT_PENDING]:
    "직전 세션의 성공·실패 판정이 끝나지 않았습니다. 먼저 판정한 뒤 새 기록을 시작하세요.",
  [REJECT_REASON.NOT_RECORDING]:
    "진행 중인 기록이 없습니다.",
  [REJECT_REASON.SESSION_MISMATCH]:
    "화면의 세션과 로봇의 현재 세션이 다릅니다. 화면을 새로 고쳐 최신 기록 상태를 확인하세요.",
  [REJECT_REASON.STOP_FAILED]:
    "기록 종료에 실패했습니다. 로봇 상태를 확인한 뒤 다시 시도하세요.",

  // 웹 전송 계층 (web_ 접두사)
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
  [REJECT_REASON.PREEMPTED_BY_STOP]:
    "정지 명령으로 대기 중이던 요청이 취소되었습니다.",
  [REJECT_REASON.SUPERSEDED]:
    "제어권 유지 신호가 새 요청으로 교체되었습니다.",
};

/** 거부 사유를 사용자 문구로 바꾼다. 알 수 없는 사유는 원문을 함께 보여준다. */
export function describeReason(reason) {
  if (!reason || reason === REJECT_REASON.ACCEPTED) return "";
  return REASON_MESSAGES[reason] || `요청이 거부되었습니다. (${reason})`;
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

/**
 * snapshot 조각의 "변경 지문".
 *
 * ── stamp 를 파싱하지 않는 이유 ──
 * thing_interfaces 는 메시지마다 시각 필드 위치가 다르다.
 *   stamp        ControlState, SafetyState, HandCommand   (builtin_interfaces/Time)
 *   header.stamp HandLandmarks, MotorStatus, RecordingState (std_msgs/Header)
 * 그리고 둘 다 `{sec, nanosec}` 이지 문자열이 아니다. 브릿지가 .msg 원문을
 * dump 하므로 이 형태로 온다.
 *
 * 이전 구현은 `snapshotAt − stamp` 뺄셈으로 신선도를 쟀는데 두 가지가 걸렸다.
 *   1. 문자열 파싱을 전제해서 {sec,nanosec} 을 못 읽고 항상 "최신" 으로 판정
 *   2. snapshotAt 은 Jetson, motor_state.stamp 는 Raspberry Pi 가 찍는다.
 *      두 장비 시계가 어긋나면 정상인데 단절로 뜨거나 그 반대가 된다.
 *
 * 그래서 값을 해석하지 않고 **바뀌었는지만** 본다. 위치도 타입도 상관없고
 * 장비 간 시계 동기화에도 의존하지 않는다. 경과 시간은 브라우저 시계로 재되
 * 절대 비교가 아니라 스톱워치로만 쓰므로 시계 오차의 영향을 받지 않는다.
 *
 * 반환값이 같으면 "그 사이 새 메시지가 없었다" 는 뜻이다.
 * 시각 필드가 아예 없으면 객체 전체를 지문으로 쓴다.
 */
export function sectionToken(object) {
  if (!object || typeof object !== "object" || Object.keys(object).length === 0) {
    return null;
  }
  const stamp = object.stamp ?? object.header?.stamp;
  if (stamp !== undefined) return JSON.stringify(stamp);
  return JSON.stringify(object);
}

/** 여러 근거 중 하나라도 살아 있으면 up. 전부 모르면 unknown. */
function anyUp(...states) {
  if (states.includes(CONNECTION_STATE.UP)) return CONNECTION_STATE.UP;
  if (states.includes(CONNECTION_STATE.DOWN)) return CONNECTION_STATE.DOWN;
  return CONNECTION_STATE.UNKNOWN;
}

/**
 * 마지막 변경 시각으로 조각 하나의 생사를 판정한다.
 *
 *   null      아직 못 받았다   → unknown
 *   최근 변경  살아 있다        → up
 *   오래 정지  발행이 멈췄다     → down
 *
 * 임계값 staleMs 는 근거가 없는 값이다 (pending.js 의 THRESHOLD.SECTION_STALE_MS).
 */
export function sectionLiveness(updatedAt, now, staleMs) {
  if (typeof updatedAt !== "number") return CONNECTION_STATE.UNKNOWN;
  return now - updatedAt > staleMs ? CONNECTION_STATE.DOWN : CONNECTION_STATE.UP;
}

/**
 * FR-24 장치별 연결 상태를 조각별 마지막 변경 시각에서 파생한다.
 *
 * 브릿지가 `connection_status` 를 직접 주면 그것을 쓴다. 그러려면 브릿지가
 * /thing/diagnostics 를 파싱하고 진단 항목↔장치 대응 규칙을 세워야 하는데 그
 * 규칙이 아직 없다. 정해질 때까지 프런트가 근사한다.
 *
 * 근거는 토픽 발행 주체다 (6.3절 발행 노드 표).
 *   landmarks     ← Jetson mediapipe_node   → jetson, camera
 *   motor_state   ← Raspberry Pi            → rpi, motor
 *   safety_state  ← Raspberry Pi            → rpi
 *   control_state ← Raspberry Pi            → ros2
 *
 * 근사의 한계
 *   - Jetson 자체 장애와 카메라 장애를 구분하지 못한다. 실제 스트림 실패는
 *     CameraStream 의 <img> onError 가 따로 잡는다.
 *   - FR-24 가 추가로 요구하는 MediaPipe·hand_target·MJPEG 개별 상태는 이
 *     근사로 만들 수 없다. 브릿지가 보내 줘야 한다 — 협의 항목.
 * 안전 판단 주체는 Raspberry Pi 이므로(NFR-16) 이 표시가 틀려도 제어에는
 * 영향이 없다.
 */
export function deriveConnectionStatus(
  now, updatedAt = {}, motorState = null, staleMs = THRESHOLD.SECTION_STALE_MS,
) {
  const live = (key) => sectionLiveness(updatedAt[key], now, staleMs);
  const lm = live("landmarks");
  const mt = live("motor_state");
  const sf = live("safety_state");
  const ct = live("control_state");
  const rc = live("recording");

  let motor = mt;
  if (motorState && motorState.bus_communication_ok === false) {
    motor = CONNECTION_STATE.DOWN;
  }

  return {
    jetson: lm,
    camera: lm,
    rpi: anyUp(mt, sf),
    ros2: anyUp(ct, sf, rc, mt, lm),
    motor,
  };
}

/** 관측된 단절만 true. "아직 모름" 은 단절로 취급하지 않는다. */
export function isDeviceDown(value) {
  return value === CONNECTION_STATE.DOWN;
}

/** 표시·조작을 허용할 수 있는 상태. 미확인은 막지 않는다. */
export function isDeviceUsable(value) {
  return value !== CONNECTION_STATE.DOWN;
}

/**
 * `builtin_interfaces/Time` 또는 RFC 3339 문자열을 표시용 문자열로.
 *
 * `{sec, nanosec}` 을 Unix epoch 로 해석한다. ROS 2 노드가 system clock 을 쓰면
 * 맞고 sim time 을 쓰면 틀린다. 이 프로젝트는 sim 을 쓰지 않으므로(1.5.2절
 * Isaac Sim 은 MVP 제외) epoch 로 본다. 표시 전용이며 판정에 쓰지 않는다.
 */
export function formatStamp(stamp) {
  if (typeof stamp === "string") return stamp.replace("T", " ").replace("Z", "");
  if (stamp && typeof stamp.sec === "number") {
    const ms = stamp.sec * 1000 + Math.floor((stamp.nanosec ?? 0) / 1e6);
    return new Date(ms).toISOString().replace("T", " ").replace("Z", "").slice(0, 23);
  }
  return "-";
}

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
  //: FR-34 "현재 owner가 같은 mode·owner 요청을 1000ms마다 보낸다."
  //  웹이 직접 타이머를 돌리므로 사본이 필요하다. FR-41 YAML 과 동기 대상.
  CONTROL_RENEW_PERIOD_MS: SPEC.CONTROL_RENEW_PERIOD_MS,
  //: 8.1절 "confidence 0.70 미만 또는 미검출". 검출 표시 판정에 쓴다.
  HAND_CONFIDENCE_MIN: SPEC.HAND_CONFIDENCE_MIN,
  //: 8.1절 "confidence 0.70 미만 또는 미검출 150ms 뒤 발행 중단".
  //  브릿지가 hand_loss_latched 를 주지 않을 때 latch 를 근사하는 데 쓴다.
  //  판정 주체는 Raspberry Pi 이고 이 값은 표시용이다.
  HAND_LOSS_DEBOUNCE_MS: SPEC.HAND_LOSS_DEBOUNCE_MS,
  //: 미확정. ack 유실 시 버튼 잠금을 푸는 상한. pending.js 참조.
  ACK_TIMEOUT_MS: PENDING.ACK_TIMEOUT_MS,
};
