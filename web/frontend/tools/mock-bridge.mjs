// web/frontend/tools/mock-bridge.mjs
//
// 로봇 없이 화면을 확인하기 위한 개발용 가짜 브릿지.
//
// ─────────────────────────────────────────────────────────────────────────
// 왜 web/ 안에 있는가
// ─────────────────────────────────────────────────────────────────────────
// 원래 시뮬레이터는 저장소 루트의 `tools/bridge_simulator.py` 였고, 실제 브릿지
// 패키지(`thing_ws/src/thing_web_bridge`)를 import 해서 돌았다. 흉내내는 코드가
// 따로 자라 실물과 갈라지는 것을 막으려는 의도였다.
//
// 그런데 이 저장소에서 web 담당이 팀 저장소에 올리는 것은 `web/` 폴더뿐이다.
// `thing_ws` 와 `tools` 는 올라가지 않으므로, 그 구성에서는 시뮬레이터를 돌릴 수
// 없고 결과적으로 로봇 없이 화면을 볼 방법이 사라진다.
//
// 그래서 `web/` 안에서 자립하는 최소 mock 을 둔다. Python·ROS 패키지가 필요 없고
// Node 만 있으면 된다.
//
// ─────────────────────────────────────────────────────────────────────────
// 갈라짐을 막는 방법
// ─────────────────────────────────────────────────────────────────────────
// 동결 상수(7논리축·enum·거부 사유·gesture 이름)는 프런트엔드가 이미 들고 있는
// `src/config/messageProtocol.js` 를 그대로 import 한다. 그 파일이 바뀌면 이
// mock 도 함께 바뀐다. 손으로 베낀 상수 목록을 두지 않는다.
//
// 상태 판정(모드 전환 규칙 등)은 화면을 눌러 볼 수 있을 만큼만 흉내낸다.
// 권위 있는 계약 시험은 브릿지 패키지의 test/ 55건에 그대로 있다. 이 파일은
// 개발용 픽스처이며 계약의 근거가 아니다.
//
// ─────────────────────────────────────────────────────────────────────────
// 실행
//   npm run mock            # ws://localhost:8000/ws/robot-state
//   npm run mock -- --port 9000
//
// 키 입력(Enter)으로 상황을 만든다
//   s 안전상태 순환   r 판정 대기   l 신뢰도 하락   h 손 미검출
//   o LOCAL owner    d 모터 단절   ? 상태 출력    q 종료
// ─────────────────────────────────────────────────────────────────────────

import { createServer } from 'node:http';
import { createHash, randomBytes } from 'node:crypto';
import process from 'node:process';

import {
  CANONICAL_GESTURES,
  CONTROL_MODE,
  CONTROL_OWNER,
  GESTURE_ALIASES,
  HAND_AXIS_KEYS,
  RECORDING_STATE,
  REJECT_REASON,
  RESET_ALLOWED_STATES,
  TIMING,
} from '../src/config/messageProtocol.js';

// messageProtocol 은 SafetyState 를 배열(SAFETY_STATES)로만 내보낸다.
// 여기서는 이름으로 참조하는 편이 읽기 쉬워 객체로 만들어 쓴다.
const SAFETY_STATE = {
  INIT: 'INIT', READY: 'READY', RUN: 'RUN', HOLD: 'HOLD',
  SAFE: 'SAFE', FAULT: 'FAULT', ESTOP: 'ESTOP',
};

/** FR-38 alias 정규화. 미지원 이름이면 null. */
function canonicalGesture(name) {
  if (typeof name !== 'string') return null;
  const resolved = GESTURE_ALIASES[name] ?? name;
  return CANONICAL_GESTURES.includes(resolved) ? resolved : null;
}

// ── 인자 ────────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
const readArg = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
};
const PORT = Number(readArg('port', 8000));
const HOST = readArg('host', '127.0.0.1');
const PATHNAME = '/ws/robot-state';

const log = (tag, message) => console.log(`[${tag}] ${message}`);

// ── 최소 WebSocket 서버 ─────────────────────────────────────────────────
//
// ws 패키지를 새 의존성으로 추가하지 않으려고 RFC 6455 의 필요한 부분만 직접
// 다룬다. 텍스트 프레임만 주고받으면 되므로 짧다. 개발용이라 확장·압축·조각화는
// 지원하지 않는다.

const GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

function acceptKey(key) {
  return createHash('sha1').update(key + GUID).digest('base64');
}

function encodeText(text) {
  const payload = Buffer.from(text, 'utf8');
  const len = payload.length;
  let header;
  if (len < 126) {
    header = Buffer.from([0x81, len]);
  } else if (len < 65536) {
    header = Buffer.alloc(4);
    header.writeUInt8(0x81, 0);
    header.writeUInt8(126, 1);
    header.writeUInt16BE(len, 2);
  } else {
    header = Buffer.alloc(10);
    header.writeUInt8(0x81, 0);
    header.writeUInt8(127, 1);
    header.writeBigUInt64BE(BigInt(len), 2);
  }
  return Buffer.concat([header, payload]);
}

/** 클라이언트 프레임을 해석한다. 마스킹된 텍스트 프레임만 처리한다. */
function decodeFrames(buffer) {
  const messages = [];
  let offset = 0;
  while (offset + 2 <= buffer.length) {
    const first = buffer[offset];
    const second = buffer[offset + 1];
    const opcode = first & 0x0f;
    const masked = (second & 0x80) !== 0;
    let length = second & 0x7f;
    let cursor = offset + 2;

    if (length === 126) {
      if (cursor + 2 > buffer.length) break;
      length = buffer.readUInt16BE(cursor);
      cursor += 2;
    } else if (length === 127) {
      if (cursor + 8 > buffer.length) break;
      length = Number(buffer.readBigUInt64BE(cursor));
      cursor += 8;
    }

    const maskKey = masked ? buffer.subarray(cursor, cursor + 4) : null;
    if (masked) cursor += 4;
    if (cursor + length > buffer.length) break;

    const payload = Buffer.from(buffer.subarray(cursor, cursor + length));
    if (maskKey) {
      for (let i = 0; i < payload.length; i += 1) payload[i] ^= maskKey[i % 4];
    }
    cursor += length;
    offset = cursor;

    if (opcode === 0x8) { messages.push({ close: true }); break; }
    if (opcode === 0x1) messages.push({ text: payload.toString('utf8') });
    // 0x9 ping / 0xA pong / 0x2 binary 는 개발용이라 무시한다
  }
  return { messages, rest: buffer.subarray(offset) };
}

// ── 가짜 로봇 ───────────────────────────────────────────────────────────
//
// Raspberry Pi 의 command_manager + safety_manager + Logger 를 합친 최소 모형.
// 실제 판정 주체는 로봇이다.

const utcNowZ = () => new Date().toISOString().replace(/(\.\d{3})\d*Z$/, '$1Z');

/** FR-18: CSPRNG 로 만든 0이 아닌 63-bit 양의 정수. 10진 문자열로 표현한다. */
function newSessionId() {
  const value = randomBytes(8).readBigUInt64BE() & ((1n << 63n) - 1n);
  return String(value === 0n ? 1n : value);
}

const robot = {
  mode: CONTROL_MODE.DISABLED,
  owner: CONTROL_OWNER.NONE,
  ownerAlive: false,
  sequenceRunning: false,
  reason: '가짜 브릿지 시작',
  // INIT 검사를 통과한 것으로 보고 READY 에서 시작한다
  safety: SAFETY_STATE.READY,
  safetyReason: '준비 완료',
  motorOk: true,
  recState: RECORDING_STATE.IDLE,
  recMessage: '',
  activeSession: '',
  lastSession: '',
  resultPending: false,
  lastResult: 'UNSET',
  handDetected: true,
  handConfidence: 0.93,
  lowConfidence: false,
  handLost: false,
  motionUntil: null,
  settleUntil: null,
  // FR-27 hand-loss latch 파생용
  invalidSince: null,
  validSince: null,
  latched: false,
  sequence: 0,
};

function releaseOwner(reason) {
  robot.mode = CONTROL_MODE.DISABLED;
  robot.owner = CONTROL_OWNER.NONE;
  robot.ownerAlive = false;
  robot.sequenceRunning = false;
  robot.motionUntil = null;
  robot.reason = reason;
}

function interruptRecording(message) {
  if ([RECORDING_STATE.STARTING, RECORDING_STATE.RECORDING].includes(robot.recState)) {
    robot.lastSession = robot.activeSession;
    robot.activeSession = '';
    robot.recState = RECORDING_STATE.INTERRUPTED;
    robot.resultPending = false;
    robot.recMessage = message;
  }
}

/** FR-27 / FR-35 hand-loss latch. 브릿지가 landmark 스트림에서 파생한다. */
function updateHandLoss(nowMs) {
  const detected = robot.handDetected && !robot.handLost;
  const confidence = detected ? (robot.lowConfidence ? 0.45 : robot.handConfidence) : 0;
  const valid = detected && confidence >= TIMING.HAND_CONFIDENCE_MIN;

  if (valid) {
    robot.invalidSince = null;
    if (robot.validSince === null) robot.validSince = nowMs;
  } else {
    robot.validSince = null;
    if (robot.invalidSince === null) robot.invalidSince = nowMs;
    else if (nowMs - robot.invalidSince >= 150) robot.latched = true;
  }

  const reacquire = robot.latched && robot.validSince !== null
    ? Math.min(Math.round(nowMs - robot.validSince), 300)
    : 0;

  return {
    detected,
    handedness: detected ? 'RIGHT' : 'UNKNOWN',
    handedness_confidence: detected ? 0.99 : 0,
    confidence: Number(confidence.toFixed(3)),
    image_width: 640,
    image_height: 480,
    valid_detection: valid,
    hand_loss_latched: robot.latched,
    reacquire_elapsed_ms: reacquire,
    reacquire_stable_ms: 300,
    resume_required: robot.latched,
  };
}

function tick() {
  const now = Date.now();
  if (robot.settleUntil && now >= robot.settleUntil) {
    robot.settleUntil = null;
    if (robot.safety === SAFETY_STATE.HOLD) {
      robot.safety = SAFETY_STATE.READY;
      robot.safetyReason = 'STOP 안정화 완료';
    }
  }
  if (robot.motionUntil && now >= robot.motionUntil) {
    robot.motionUntil = null;
    robot.sequenceRunning = false;
  }
  if (robot.recState === RECORDING_STATE.STARTING) {
    robot.recState = RECORDING_STATE.RECORDING;
    robot.recMessage = '기록 중';
  } else if (robot.recState === RECORDING_STATE.STOPPING) {
    // FR-18: 정상 Stop 은 결과 판정을 기다린다
    robot.lastSession = robot.activeSession;
    robot.activeSession = '';
    robot.recState = RECORDING_STATE.COMPLETED;
    robot.resultPending = true;
    robot.recMessage = '판정 대기';
    log('기록', `COMPLETED session_id=${robot.lastSession} → 판정 대기`);
  }
}

// ── 6.4절 snapshot ──────────────────────────────────────────────────────

function buildSnapshot(nowMs) {
  const phase = nowMs / 700;
  const v = Math.abs(Math.sin(phase));
  robot.sequence += 1;

  const landmarks = updateHandLoss(nowMs);
  const safety = {
    state: robot.safety,
    command_timeout: robot.safety === SAFETY_STATE.HOLD,
    motor_communication_ok: robot.motorOk,
    over_current: robot.safety === SAFETY_STATE.FAULT,
    over_temperature: false,
    estop_active: robot.safety === SAFETY_STATE.ESTOP,
    fault_code: robot.safety === SAFETY_STATE.FAULT ? 1 : 0,
    reason: robot.safetyReason,
    stamp: utcNowZ(),
    // FR-35: SAFE·FAULT·ESTOP 에서만 reset_safety 를 쓴다
    reset_allowed: RESET_ALLOWED_STATES.includes(robot.safety),
  };
  const control = {
    active_mode: robot.mode,
    active_owner: robot.owner,
    owner_alive: robot.ownerAlive,
    sequence_running: robot.sequenceRunning,
    last_transition_reason: robot.reason,
    stamp: utcNowZ(),
  };
  const recording = {
    state: robot.recState,
    active_session_id: robot.activeSession,
    active_bag_path: robot.activeSession
      ? `/var/lib/thing-robot-data/bags/${robot.activeSession}` : '',
    last_session_id: robot.lastSession,
    result_pending: robot.resultPending,
    last_mimic_result: robot.lastResult,
    message: robot.recMessage,
  };

  return {
    // 6.4절 고정 6필드
    timestamp: utcNowZ(),
    mode: robot.mode,
    recording_state: robot.recState,
    landmarks,
    motor_state: {
      stamp: utcNowZ(),
      motors: HAND_AXIS_KEYS.map((axis, i) => ({
        motor_id: i + 1,
        actuator_name: axis,
        goal_position_rad: Number(v.toFixed(3)),
        present_position_rad: Number((v * 0.98).toFixed(3)),
        velocity_rad_s: 0,
        current_ampere: robot.safety === SAFETY_STATE.FAULT ? 1.4 : 0.05,
        voltage_volt: 11.1,
        temperature_celsius: 35 + i,
        // FR-25: 모터별 이상 표시는 hardware_error 로 한다
        hardware_error: robot.safety === SAFETY_STATE.FAULT ? 1 : 0,
        communication_result: robot.motorOk ? 0 : -3001,
        communication_ok: robot.motorOk,
      })),
      bus_communication_ok: robot.motorOk,
      failed_read_count: robot.motorOk ? 0 : 12,
      message: robot.motorOk ? '' : '모터 버스 응답 없음',
    },
    safety_state: safety,
    // 확장
    control_state: control,
    recording_detail: recording,
    // 2계층에서는 이 snapshot 이 전달된 것이 곧 브릿지가 살아 있다는 뜻이므로
    // 항상 true 다. 상수이지만 계약에 남긴다 — 프런트엔드가 이 필드로 조작 가능
    // 여부를 판단하고, 빠뜨리면 UI 가 잠긴다.
    bridge_connected: true,
    connection_status: {
      jetson: 'up', rpi: 'up', ros2: 'up',
      camera: 'up', motor: robot.motorOk ? 'up' : 'down',
    },
    last_hand_command: {
      values: Object.fromEntries(HAND_AXIS_KEYS.map((k) => [k, Number(v.toFixed(3))])),
      source: robot.mode === CONTROL_MODE.MIMIC ? 'MIMIC' : 'GESTURE',
      sequence: robot.sequence,
      speed_limit: 1.0,
      confidence: Number(Math.min(v + 0.3, 1).toFixed(3)),
      stamp: utcNowZ(),
    },
    pending: { mode: null, owner: null },
  };
}

// ── 요청 처리 ───────────────────────────────────────────────────────────

const ACCEPT = [true, REJECT_REASON.ACCEPTED];

function setControlMode(payload) {
  const mode = payload.requested_mode ?? payload.mode;
  const owner = payload.requested_owner ?? payload.owner;

  if (mode === CONTROL_MODE.DISABLED && owner === CONTROL_OWNER.NONE) {
    releaseOwner('명시적 STOP');
    if ([SAFETY_STATE.SAFE, SAFETY_STATE.FAULT, SAFETY_STATE.ESTOP].includes(robot.safety)) {
      // FR-35: STOP 은 mode·owner 만 해제하고 안전 상태는 유지한다
      robot.safetyReason = `${robot.safety} 유지 (STOP 은 mode·owner 만 해제)`;
    } else {
      robot.safety = SAFETY_STATE.HOLD;
      robot.safetyReason = 'STOP 수락, 안정화 중';
      robot.settleUntil = Date.now() + 500;
    }
    interruptRecording('STOP 으로 기록 중단');
    log('모드', 'STOP 수락');
    return ACCEPT;
  }

  if (![CONTROL_OWNER.NONE, owner].includes(robot.owner)) {
    return [false, REJECT_REASON.OWNER_CONFLICT];
  }
  if (mode === robot.mode && owner === robot.owner) {
    // FR-34 갱신
    if (![SAFETY_STATE.READY, SAFETY_STATE.RUN].includes(robot.safety)) {
      return [false, REJECT_REASON.SAFETY_NOT_READY];
    }
    robot.ownerAlive = true;
    return ACCEPT;
  }
  // FR-19: MIMIC↔MANUAL 직접 전환 금지
  if (robot.mode !== CONTROL_MODE.DISABLED) return [false, REJECT_REASON.INVALID_MODE];
  if (robot.safety !== SAFETY_STATE.READY) return [false, REJECT_REASON.SAFETY_NOT_READY];

  robot.mode = mode;
  robot.owner = owner;
  robot.ownerAlive = true;
  robot.reason = `${mode} 획득 (${owner})`;
  log('모드', `${mode}/${owner} 획득`);
  return ACCEPT;
}

function startMotion(label, holdMs, reason) {
  if (robot.sequenceRunning) return [false, REJECT_REASON.MOTION_ACTIVE];
  // FR-38: READY 에서 첫 유효 gesture 가 RUN 전이를 일으킨다
  robot.safety = SAFETY_STATE.RUN;
  robot.safetyReason = reason;
  robot.sequenceRunning = true;
  robot.motionUntil = Date.now() + holdMs;
  robot.reason = label;
  log('동작', `${label} (유지 ${holdMs}ms)`);
  return ACCEPT;
}

const HANDLERS = {
  set_control_mode: setControlMode,
  stop: () => setControlMode({
    requested_mode: CONTROL_MODE.DISABLED, requested_owner: CONTROL_OWNER.NONE,
  }),
  execute_gesture: (payload) => {
    const name = canonicalGesture(payload.gesture_name ?? payload.gesture_id);
    if (!name) return [false, REJECT_REASON.INVALID_MODE];
    // FR-22 초기 유지시간: open·fist 1000ms, 그 외 3000ms
    return startMotion(`gesture ${name}`, ['open', 'fist'].includes(name) ? 1000 : 3000,
                       '동작 실행 중');
  },
  execute_sequence: (payload) => startMotion(
    `sequence ${payload.sequence_name ?? payload.sequence_id}`, 4000, 'sequence 실행 중',
  ),
  start_recording: () => {
    robot.activeSession = newSessionId();
    robot.recState = RECORDING_STATE.STARTING;
    robot.recMessage = '기록 시작';
    log('기록', `START session_id=${robot.activeSession}`);
    return ACCEPT;
  },
  stop_recording: (payload) => {
    if (String(payload.session_id) !== robot.activeSession) {
      log('기록', `STOP 거부: ${payload.session_id} ≠ ${robot.activeSession}`);
      return [false, 'web_session_mismatch'];
    }
    robot.recState = RECORDING_STATE.STOPPING;
    robot.recMessage = '기록 종료 중';
    log('기록', `STOP session_id=${payload.session_id}`);
    return ACCEPT;
  },
  set_mimic_result: (payload) => {
    if (String(payload.session_id) !== robot.lastSession) {
      return [false, 'web_session_mismatch'];
    }
    robot.resultPending = false;
    robot.lastResult = payload.result;
    robot.recState = RECORDING_STATE.IDLE;
    robot.recMessage = `판정 ${payload.result} → exporter 시작`;
    log('기록', `판정 ${payload.result}`);
    return ACCEPT;
  },
  reset_safety: () => {
    robot.safety = SAFETY_STATE.READY;
    robot.safetyReason = 'reset 후 INIT 재검사 통과';
    robot.motorOk = true;
    releaseOwner('Safety Reset');
    log('안전', 'reset 수락 → READY');
    return ACCEPT;
  },
};

// ── 서버 ────────────────────────────────────────────────────────────────

const clients = new Set();

function broadcast(text) {
  const frame = encodeText(text);
  for (const socket of clients) {
    if (!socket.destroyed) socket.write(frame);
  }
}

const server = createServer((req, res) => {
  res.writeHead(426, { 'Content-Type': 'text/plain; charset=utf-8' });
  res.end('WebSocket 전용입니다. ws://…/ws/robot-state 로 접속하세요.\n');
});

server.on('upgrade', (req, socket) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const key = req.headers['sec-websocket-key'];
  if (url.pathname.replace(/\/$/, '') !== PATHNAME || !key) {
    socket.end('HTTP/1.1 404 Not Found\r\n\r\n');
    return;
  }

  socket.write([
    'HTTP/1.1 101 Switching Protocols',
    'Upgrade: websocket',
    'Connection: Upgrade',
    `Sec-WebSocket-Accept: ${acceptKey(key)}`,
    '', '',
  ].join('\r\n'));

  clients.add(socket);
  log('연결', `브라우저 접속 (${clients.size})`);
  socket.write(encodeText(JSON.stringify(buildSnapshot(Date.now()))));

  let buffer = Buffer.alloc(0);
  socket.on('data', (chunk) => {
    buffer = Buffer.concat([buffer, chunk]);
    const { messages, rest } = decodeFrames(buffer);
    buffer = rest;
    for (const message of messages) {
      if (message.close) { socket.end(); return; }
      handleRequest(socket, message.text);
    }
  });

  const drop = () => {
    clients.delete(socket);
    log('연결', `브라우저 해제 (${clients.size})`);
    // NFR-15: 브라우저가 떠나면 갱신이 멈추고 FR-11 의 lease 만료가 안전 전이를
    // 수행한다. 별도 통보 메시지를 보내지 않는다.
  };
  socket.on('close', drop);
  socket.on('error', drop);
});

function handleRequest(socket, raw) {
  let content;
  try {
    content = JSON.parse(raw);
  } catch {
    return;
  }
  const { request_id: requestId, type, payload = {} } = content;
  const handler = HANDLERS[type];
  const [accepted, reason] = handler
    ? handler(payload)
    : [false, 'web_unknown_type'];

  log('요청', `${type} → ${accepted ? '수락' : '거부'} (${reason})`);
  socket.write(encodeText(JSON.stringify({
    type: 'ack', request_id: requestId, accepted, reason, timestamp: utcNowZ(),
  })));
  broadcast(JSON.stringify(buildSnapshot(Date.now())));
}

// ── 키보드 시나리오 ─────────────────────────────────────────────────────

const SAFETY_CYCLE = [
  SAFETY_STATE.READY, SAFETY_STATE.RUN, SAFETY_STATE.HOLD,
  SAFETY_STATE.SAFE, SAFETY_STATE.FAULT, SAFETY_STATE.ESTOP,
];

const KEYS = {
  s() {
    const i = SAFETY_CYCLE.indexOf(robot.safety);
    robot.safety = SAFETY_CYCLE[(i + 1) % SAFETY_CYCLE.length];
    robot.safetyReason = `수동 전환: ${robot.safety}`;
    robot.motorOk = robot.safety !== SAFETY_STATE.FAULT;
    // FR-35: 안전 전이 시 mode·owner·큐를 해제하고 활성 recording 을 중단한다
    if (robot.safety !== SAFETY_STATE.READY && robot.safety !== SAFETY_STATE.RUN) {
      releaseOwner(`안전 전이 ${robot.safety}`);
      interruptRecording(`${robot.safety} 전이로 기록 중단`);
    }
    log('안전', `→ ${robot.safety}`);
  },
  r() {
    robot.lastSession = newSessionId();
    robot.recState = RECORDING_STATE.COMPLETED;
    robot.activeSession = '';
    robot.resultPending = true;
    robot.recMessage = '판정 대기 (수동 생성)';
    log('기록', `판정 대기 생성 session_id=${robot.lastSession}`);
  },
  l() {
    robot.lowConfidence = !robot.lowConfidence;
    log('손', `신뢰도 강제 하락 ${robot.lowConfidence ? 'ON' : 'OFF'} `
      + `(임계 ${TIMING.HAND_CONFIDENCE_MIN})`);
  },
  h() {
    robot.handLost = !robot.handLost;
    log('손', `미검출 강제 ${robot.handLost ? 'ON' : 'OFF'}`);
  },
  o() {
    if (robot.owner === CONTROL_OWNER.LOCAL) {
      releaseOwner('LOCAL owner 해제');
      log('모드', 'LOCAL owner 해제');
    } else {
      robot.mode = CONTROL_MODE.TELEOP;
      robot.owner = CONTROL_OWNER.LOCAL;
      robot.ownerAlive = true;
      robot.reason = '로컬 teleop 획득';
      log('모드', 'LOCAL owner 가 TELEOP 획득 (웹은 owner_conflict 를 받는다)');
    }
  },
  d() {
    robot.motorOk = !robot.motorOk;
    log('장치', `모터 연결 ${robot.motorOk ? '정상' : '끊김'}`);
  },
  '?'() {
    log('상태', `mode=${robot.mode}/${robot.owner} alive=${robot.ownerAlive} `
      + `safety=${robot.safety} rec=${robot.recState} `
      + `active=${robot.activeSession || '-'} pending=${robot.resultPending} `
      + `latch=${robot.latched}`);
  },
};

if (process.stdin.isTTY) {
  console.log('\n키: s=안전순환  r=판정대기  l=신뢰도하락  h=손미검출  '
    + 'o=LOCAL owner  d=모터단절  ?=상태  q=종료\n');
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => {
    const key = chunk.trim().toLowerCase();
    if (key === 'q') { log('종료', '사용자 요청'); process.exit(0); }
    if (KEYS[key]) {
      KEYS[key]();
      broadcast(JSON.stringify(buildSnapshot(Date.now())));
    } else if (key) {
      log('입력', `알 수 없는 키: ${key}`);
    }
  });
}

// NFR-13: 손·7축 10Hz. 상태 전이는 요청·키 입력에서 즉시 보낸다.
setInterval(() => {
  tick();
  if (clients.size) broadcast(JSON.stringify(buildSnapshot(Date.now())));
}, 100);

server.listen(PORT, HOST, () => {
  log('연결', `ws://${HOST}:${PORT}${PATHNAME} 에서 대기`);
  log('연결', '웹에서 ①정지 → ②모드 획득 순서로 제어권을 잡는다');
});
