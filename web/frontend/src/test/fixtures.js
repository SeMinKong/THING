// frontend/src/test/fixtures.js
//
// 요구사항 명세서 V6.3 6.4절 snapshot v1 과 FR-37 ack 을 그대로 모사한다.
// 필드명은 develop .msg 그대로 쓴다 (FR-30).

import { REJECT_REASON, WEB_REASON } from "../config/messageProtocol";

/** RFC 3339 UTC Z (밀리초 3자리) */
export function utcNowZ() {
  return new Date().toISOString().replace(/(\.\d{3})\d*Z$/, "$1Z");
}

// 3상태다. FR-24 "가짜 값으로 채우지 않는다": bool 로는 "아직 못 받았다" 와
// "끊겼다" 를 구분할 수 없다. 서버(`normalize.py`)가 같은 세 값으로 내려준다.
export const emptyConnection = {
  jetson: "unknown", rpi: "unknown", ros2: "unknown",
  camera: "unknown", motor: "unknown",
};

export const allConnected = {
  jetson: "up", rpi: "up", ros2: "up", camera: "up", motor: "up",
};

export const allDown = {
  jetson: "down", rpi: "down", ros2: "down", camera: "down", motor: "down",
};

export function controlState(over = {}) {
  return {
    active_mode: "DISABLED",
    active_owner: "NONE",
    owner_alive: false,
    sequence_running: false,
    last_transition_reason: "",
    stamp: null,
    ...over,
  };
}

/**
 * 6.4절 safety_state 표시 객체.
 *
 * `reset_allowed` 는 SafetyState.msg 에 없는 파생 필드다. FR-30 이 동결하는 대상은
 * thing_interfaces 의 .msg 파일이고 이 객체는 WebSocket 표시 객체이므로,
 * FR-27 이 hand-loss latch 에 지시한 것과 같은 방식으로 Web Bridge 가 파생한다.
 * FR-35: SAFE·FAULT·ESTOP 에서만 reset_safety 를 쓴다.
 */
export function safetyState(over = {}) {
  const state = over.state ?? "INIT";
  return {
    state,
    command_timeout: false,
    motor_communication_ok: false,
    over_current: false,
    over_temperature: false,
    estop_active: false,
    fault_code: 0,
    reason: "",
    reset_allowed: ["SAFE", "FAULT", "ESTOP"].includes(state),
    stamp: null,
    ...over,
  };
}

export function recordingState(over = {}) {
  return {
    state: "IDLE",
    active_session_id: 0,
    active_bag_path: "",
    active_started_at: null,
    last_session_id: 0,
    last_bag_path: "",
    last_started_at: null,
    last_ended_at: null,
    result_pending: false,
    last_mimic_result: "UNSET",
    message: "",
    ...over,
  };
}

/**
 * 6.4절 snapshot v1.
 * top-level 6필드는 고정이고 나머지는 FR-24 표시를 위한 확장이다.
 */
export function snapshot(over = {}) {
  const control = over.control_state ?? controlState();
  const recording = over.recording_detail ?? recordingState();
  return {
    // ── 고정 6필드 ──
    timestamp: utcNowZ(),
    mode: control.active_mode,
    recording_state: recording.state,
    landmarks: {},
    motor_state: {},
    // 6.4절: 아직 유효 데이터를 받지 못한 객체는 null 대신 {} 를 쓴다.
    // 기본 snapshot 은 브릿지 미연결 상태를 모사하므로 {} 다.
    safety_state: {},
    // ── 확장 ──
    control_state: control,
    recording_detail: recording,
    connection_status: emptyConnection,
    last_hand_command: {},
    pending: { mode: null, owner: null },
    bridge_connected: false,
    ...over,
  };
}

/** 브릿지 연결 + READY 상태 */
export function readySnapshot(over = {}) {
  return snapshot({
    safety_state: safetyState({ state: "READY", motor_communication_ok: true }),
    connection_status: allConnected,
    bridge_connected: true,
    ...over,
  });
}

/** MIMIC 획득 완료 상태 */
export function mimicSnapshot(over = {}) {
  return snapshot({
    control_state: controlState({
      active_mode: "MIMIC", active_owner: "WEB", owner_alive: true,
    }),
    safety_state: safetyState({ state: "RUN", motor_communication_ok: true }),
    connection_status: allConnected,
    bridge_connected: true,
    ...over,
  });
}

/** MANUAL 획득 완료 상태 — gesture 를 수락할 수 있는 조건 */
export function manualSnapshot(over = {}) {
  return snapshot({
    control_state: controlState({
      active_mode: "MANUAL", active_owner: "WEB", owner_alive: true,
    }),
    safety_state: safetyState({ state: "RUN", motor_communication_ok: true }),
    connection_status: allConnected,
    bridge_connected: true,
    ...over,
  });
}

/** 7논리축이 채워진 landmarks 표시 객체 */
export const landmarksPayload = {
  detected: true,
  handedness: "RIGHT",
  confidence: 0.93,
};

/** MotorStatus 표시 객체 — 7모터 */
export const motorStatePayload = {
  motors: Array.from({ length: 7 }, (_, i) => ({
    motor_id: i + 1,
    actuator_name: ["thumb_flex", "thumb_opp", "thumb_abd", "index_flex",
      "middle_flex", "ring_flex", "little_flex"][i],
    goal_position_rad: 0.1 * i,
    present_position_rad: 0.1 * i + 0.01,
    velocity_rad_s: 0,
    current_ampere: 0.05,
    voltage_volt: 11.1,
    temperature_celsius: 35 + i,
    hardware_error: 0,
    communication_result: 0,
    communication_ok: true,
    // failed_read_count 는 MotorState.msg 에 없다. 버스 단위(MotorStatus)에만 있다.
  })),
  bus_communication_ok: true,
  failed_read_count: 0,
  message: "",
};

// ── ack (FR-37) ──

export function ack(requestId, accepted = true, reason = REJECT_REASON.ACCEPTED) {
  return {
    type: "ack",
    request_id: requestId,
    accepted,
    reason,
    timestamp: utcNowZ(),
  };
}

export function reject(requestId, reason) {
  return ack(requestId, false, reason);
}

export const REJECTIONS = [
  REJECT_REASON.INVALID_MODE,
  REJECT_REASON.OWNER_CONFLICT,
  REJECT_REASON.SAFETY_NOT_READY,
  REJECT_REASON.RECORDING_ACTIVE,
  REJECT_REASON.MOTION_ACTIVE,
  WEB_REASON.BRIDGE_OFFLINE,
  WEB_REASON.MALFORMED,
  WEB_REASON.UNKNOWN_TYPE,
  WEB_REASON.STALE,
];

/** 7논리축 유효값 */
export const validAxes = {
  thumb_flex: 0.5, thumb_opp: 0.5, thumb_abd: 0.5,
  index_flex: 0.5, middle_flex: 0.5, ring_flex: 0.5, little_flex: 0.5,
};
