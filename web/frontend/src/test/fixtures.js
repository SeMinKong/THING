// frontend/src/test/fixtures.js
//
// 요구사항 명세서 V7.1 6.4절 snapshot v1 과 거부 사유(FR-37) ack 을 그대로 모사한다.
//
// 구조는 thing_interfaces 의 .msg 실물을 따른다 (FR-30).
//   시각 필드 위치: ControlState·SafetyState·HandCommand 는 `stamp`,
//                  HandLandmarks·MotorStatus·RecordingState 는 `header.stamp`
//   시각 타입: builtin_interfaces/Time = { sec, nanosec }. 문자열이 아니다.
//   enum: uint8 상수. 브릿지가 .msg 원문을 dump 하면 정수로 온다.
// 픽스처가 프런트 편한 모양을 쓰면 실물과 갈라져 시험이 결함을 못 잡는다.

import { REJECT_REASON, WEB_REASON } from "../config/messageProtocol";

/** RFC 3339 UTC Z (밀리초 3자리). 6.4절 top-level timestamp 형식. */
export function utcNowZ() {
  return new Date().toISOString().replace(/(\.\d{3})\d*Z$/, "$1Z");
}

/** builtin_interfaces/Time. .msg 의 시각 필드는 전부 이 형태다. */
export function rosTime(offsetMs = 0) {
  const ms = Date.now() + offsetMs;
  return { sec: Math.floor(ms / 1000), nanosec: (ms % 1000) * 1e6 };
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
    stamp: rosTime(),
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
    stamp: rosTime(),
    ...over,
  };
}

export function recordingState(over = {}) {
  return {
    header: { stamp: rosTime(), frame_id: "" },
    state: "IDLE",
    active_session_id: "",
    active_bag_path: "",
    active_started_at: rosTime(),
    last_session_id: "",
    last_bag_path: "",
    last_started_at: rosTime(),
    last_ended_at: rosTime(),
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
  const recording = over.recording ?? recordingState();
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
    // ── 브릿지가 추가로 얹는 두 개 (.msg 원문 dump) ──
    control_state: control,
    recording,
    // ── 선택 필드. 없으면 프런트가 파생한다 ──
    connection_status: emptyConnection,
    last_hand_command: {},
    ...over,
  };
}

/** 브릿지 연결 + READY 상태 */
export function readySnapshot(over = {}) {
  return snapshot({
    safety_state: safetyState({ state: "READY", motor_communication_ok: true }),
    connection_status: allConnected,
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
    ...over,
  });
}

/** 7논리축이 채워진 landmarks 표시 객체 */
export const landmarksPayload = {
  header: { stamp: rosTime(), frame_id: "camera" },
  detected: true,
  handedness: "RIGHT",
  confidence: 0.93,
};

/** MotorStatus 표시 객체 — 7모터 */
export const motorStatePayload = {
  header: { stamp: rosTime(), frame_id: "" },
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
    // V7 FR-30 승인 변경. .msg 반영 전이라 브릿지가 안 줄 수도 있다.
    torque_enabled: true,
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
