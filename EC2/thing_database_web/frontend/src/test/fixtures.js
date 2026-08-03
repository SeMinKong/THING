// frontend/src/test/fixtures.js
//
// 오류 fixture 와 정상 응답 fixture.
// 서버 응답 형식은 명세서 6.5절 "공개 GET 응답 JSON schema v1" 을 따른다.

export const SESSION_ID = '123456789012345678';

export const listResponse = {
  items: [
    {
      session_id: SESSION_ID,
      robot_id: 'THING-001',
      started_at: '2026-07-29T00:00:00.000Z',
      ended_at: '2026-07-29T00:01:00.000Z',
      uploaded_at: '2026-07-29T00:01:06.000Z',
      result: 'SUCCESS',
      duration_ms: 60000,
      row_counts: { hand_command: 3, motor_status: 4 },
      file_sizes: { metadata: 913, hand_command: 421, motor_status: 758 },
    },
    {
      session_id: '999999999999999999',
      robot_id: 'THING-001',
      started_at: '2026-07-28T00:00:00.000Z',
      ended_at: '2026-07-28T00:00:30.000Z',
      uploaded_at: '2026-07-28T00:00:35.000Z',
      result: 'FAILURE',
      duration_ms: 30000,
      row_counts: { hand_command: 1, motor_status: 1 },
      file_sizes: { metadata: 800, hand_command: 100, motor_status: 200 },
    },
  ],
  next_cursor: null,
};

export const emptyListResponse = { items: [], next_cursor: null };

export const detailResponse = {
  session_id: SESSION_ID,
  robot_id: 'THING-001',
  schema_version: 1,
  data_version: 1,
  started_at: '2026-07-29T00:00:00.000Z',
  ended_at: '2026-07-29T00:01:00.000Z',
  uploaded_at: '2026-07-29T00:01:06.000Z',
  result: 'SUCCESS',
  duration_ms: 60000,
  interface_commit: '70dfdab8d555dfbfdd471c5acca4f30a8a8fc3ec',
  time_sync: true,
  content_digest: 'sha256:d7062c52dcc47185d140ba09e374a5de6188b4dca59a556d96b40bca281320b9',
  row_counts: { hand_command: 3, motor_status: 4 },
  file_sizes: { metadata: 913, hand_command: 421, motor_status: 758 },
  downloads: {
    metadata: `/api/v1/sessions/${SESSION_ID}/download/metadata`,
    hand_command: `/api/v1/sessions/${SESSION_ID}/download/hand_command`,
    motor_status: `/api/v1/sessions/${SESSION_ID}/download/motor_status`,
  },
};

/** 마지막 행의 confidence 가 결측(null) — 0 과 구분되어야 한다 */
export const handCommandData = {
  columns: ['stamp_sec', 'stamp_nanosec', 'elapsed_ms', 'sequence', 'source',
            'thumb_flex', 'thumb_opp', 'thumb_abd', 'index_flex', 'middle_flex',
            'ring_flex', 'little_flex', 'speed_limit', 'confidence'],
  rows: [
    { stamp_sec: 1785283200, stamp_nanosec: 0, elapsed_ms: 0, sequence: 1,
      source: 'VISION', thumb_flex: 0.1, thumb_opp: 0.2, thumb_abd: 0.0,
      index_flex: 0.3, middle_flex: 0.3, ring_flex: 0.2, little_flex: 0.2,
      speed_limit: 0.5, confidence: 0.92 },
    { stamp_sec: 1785283200, stamp_nanosec: 20000000, elapsed_ms: 20, sequence: 2,
      source: 'VISION', thumb_flex: 0.15, thumb_opp: 0.2, thumb_abd: 0.0,
      index_flex: 0.35, middle_flex: 0.32, ring_flex: 0.21, little_flex: 0.2,
      speed_limit: 0.5, confidence: 0.88 },
    { stamp_sec: 1785283200, stamp_nanosec: 40000000, elapsed_ms: 40, sequence: 3,
      source: 'MANUAL', thumb_flex: 0.15, thumb_opp: 0.2, thumb_abd: 0.0,
      index_flex: 0.35, middle_flex: 0.32, ring_flex: 0.21, little_flex: 0.2,
      speed_limit: 0.5, confidence: null },
  ],
  truncated: false,
};

/** motor_id 12 의 마지막 샘플이 통신 실패 — 숫자 결측 + communication_ok false */
export const motorStatusData = {
  columns: ['stamp_sec', 'stamp_nanosec', 'elapsed_ms', 'frame_id', 'motor_id',
            'actuator_name', 'goal_position_raw', 'present_position_raw',
            'goal_position_rad', 'present_position_rad', 'velocity_rad_s',
            'current_ampere', 'voltage_volt', 'temperature_celsius',
            'hardware_error', 'communication_result', 'communication_ok',
            'bus_communication_ok', 'failed_read_count'],
  rows: [
    { stamp_sec: 1785283200, stamp_nanosec: 0, elapsed_ms: 0, frame_id: 'thing_hand',
      motor_id: 11, actuator_name: 'thumb_flex', goal_position_raw: 2048,
      present_position_raw: 2050, goal_position_rad: 0.0, present_position_rad: 0.003,
      velocity_rad_s: 0.0, current_ampere: 0.05, voltage_volt: 11.9,
      temperature_celsius: 32, hardware_error: 0, communication_result: 0,
      communication_ok: true, bus_communication_ok: true, failed_read_count: 0 },
    { stamp_sec: 1785283200, stamp_nanosec: 0, elapsed_ms: 0, frame_id: 'thing_hand',
      motor_id: 12, actuator_name: 'thumb_opp', goal_position_raw: 2048,
      present_position_raw: 2047, goal_position_rad: 0.0, present_position_rad: -0.002,
      velocity_rad_s: 0.0, current_ampere: 0.048, voltage_volt: 11.9,
      temperature_celsius: 33, hardware_error: 0, communication_result: 0,
      communication_ok: true, bus_communication_ok: true, failed_read_count: 0 },
    { stamp_sec: 1785283200, stamp_nanosec: 20000000, elapsed_ms: 20, frame_id: 'thing_hand',
      motor_id: 11, actuator_name: 'thumb_flex', goal_position_raw: 2150,
      present_position_raw: 2100, goal_position_rad: 0.157, present_position_rad: 0.08,
      velocity_rad_s: 0.52, current_ampere: 0.062, voltage_volt: 11.88,
      temperature_celsius: 33, hardware_error: 0, communication_result: 0,
      communication_ok: true, bus_communication_ok: true, failed_read_count: 0 },
    { stamp_sec: 1785283200, stamp_nanosec: 20000000, elapsed_ms: 20, frame_id: 'thing_hand',
      motor_id: 12, actuator_name: 'thumb_opp', goal_position_raw: 2048,
      present_position_raw: null, goal_position_rad: null, present_position_rad: null,
      velocity_rad_s: null, current_ampere: null, voltage_volt: null,
      temperature_celsius: null, hardware_error: null, communication_result: -3001,
      communication_ok: false, bus_communication_ok: true, failed_read_count: 1 },
  ],
  truncated: false,
};

/** 오류 fixture — axios 가 던지는 형태를 모사한다 */
export function apiError(status, code, message, details = []) {
  const err = new Error(message);
  err.response = { status, data: { error: { code, message, details }, request_id: 'test-req' } };
  return err;
}

export const notFoundError = apiError(404, 'NOT_FOUND', 'The requested resource does not exist.');
export const serverError = apiError(500, 'INTERNAL_ERROR', 'An internal error occurred.');
export const networkError = new Error('Network Error');   // response 없음
