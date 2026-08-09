// ============================================================================
// HandSocketContext — 브라우저 ↔ Jetson Web Bridge WebSocket
// ----------------------------------------------------------------------------
// 계약 출처는 요구사항 명세서 V7.1 + interfaces.md·safety_manager.md·thing_interfaces 다.
//   6.4절  endpoint /ws/robot-state, top-level 6필드 snapshot
//   FR-11  control_renew_period_ms=1000, owner_lease_timeout_ms=3000
//   FR-19  현재 mode·owner 는 control_state 로 확인한다
//   FR-27  손 미검출·연결 단절·거부 사유를 구분해 안내한다
//   FR-28  WS_URL 외부 설정으로 주입한다
//   FR-34  획득·갱신·해제
//   FR-35  복구 절차. 재검출·연결 복구만으로 제어가 재개되지 않는다
//   FR-37  거부 사유 프로젝트 표준
//   NFR-15 재연결은 이전 명령을 재생하지 않는다
//
// ── 브릿지에 요구하는 것 ────────────────────────────────────────────────────
// 6.4절 고정 6필드 + control_state + recording. 전부 .msg 원문 dump 로 받는다.
// enum 정규화, 장치 상태 파생, hand-loss 근사, 잠금 판정은 전부 여기서 한다.
// 브릿지가 connection_status·hand_loss_latched 를 주면 그것을 우선한다.
// 계약 전문은 web/docs/interfaces-bridge.md.
//
// ── 미수신과 관측값을 구분한다 ──────────────────────────────────────────────
// 프런트가 관대해질수록 브릿지 누락이 안 보인다. control_state 가 없으면 화면이
// "비활성화 / 제어권 없음" 으로 보이는데 이건 로봇의 정상 상태처럼 생겼다.
// 그래서 *Known 플래그로 "아직 못 받았다" 를 화면에 드러내고 조작을 막는다.
// ============================================================================

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  CANONICAL_GESTURES,
  CLIENT_MESSAGE,
  CONTROL_MODE,
  CONTROL_MODE_BY_ORDINAL,
  CONTROL_OWNER,
  CONTROL_OWNER_BY_ORDINAL,
  GESTURE_ALIASES,
  HAND_DETECTION,
  HAND_SOURCE_BY_ORDINAL,
  OPERATION_ALLOWED_STATES,
  RECORDING_BUSY_STATES,
  RECORDING_RESULT,
  RECORDING_RESULT_BY_ORDINAL,
  RECORDING_STATE,
  RECORDING_STATE_BY_ORDINAL,
  REJECT_REASON,
  RESET_ALLOWED_STATES,
  SAFETY_STATES,
  SEQUENCE_IDS,
  SERVER_MESSAGE,
  SNAPSHOT_FIXED_FIELDS,
  STOP_MODE,
  STOP_OWNER,
  TIMING,
  WEB_OWNER,
  WEB_SELECTABLE_MODES,
  deriveConnectionStatus,
  describeReason,
  sectionToken,
  emptyConnectionStatus,
  handDetectionState,
  isNumericSessionId,
  isSnapshot,
  isValidSpeedLimit,
  readSessionId,
  toSymbol,
} from "../config/messageProtocol";
import { PENDING, THRESHOLD } from "../config/pending";
import {
  diag, OWNER, announceStartup, observeSnapshotRate,
} from "../config/diagnostics";

const HandSocketContext = createContext(null);

// 재접속 백오프·신선도 임계값·ack timeout 은 근거가 없는 값이라
// config/pending.js 에 모아 두었다. 여기서 사본을 만들지 않는다.

/**
 * FR-28: "`WS_URL`과 `MJPEG_URL` 외부 설정으로 Jetson Web Bridge와 MJPEG에 연결한다."
 * 6.4절: endpoint 는 /ws/robot-state 로 고정한다.
 *
 * VITE_WS_URL 이 있으면 그대로 쓰고, 없으면 현재 호스트를 기준으로 만든다.
 * Laptop 에서 실행하고 Jetson 에 붙는 구성이므로 배포 시에는 VITE_WS_URL 을
 * 반드시 지정해야 한다.
 */
function resolveWsUrl() {
  const configured = import.meta.env?.VITE_WS_URL;
  if (configured) return configured;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/robot-state`;
}

const initialControlState = {
  active_mode: CONTROL_MODE.DISABLED,
  active_owner: CONTROL_OWNER.NONE,
  owner_alive: false,
  sequence_running: false,
  last_transition_reason: "",
  stamp: null,
};

const initialSafetyState = {
  state: "INIT",
  command_timeout: false,
  motor_communication_ok: false,
  over_current: false,
  over_temperature: false,
  estop_active: false,
  fault_code: 0,
  reason: "",
  // reset_allowed 는 SafetyState.msg 에 없는 필드다 (FR-30).
  // reset 가능 여부는 canResetSafety 가 state 에서 파생한다 (FR-35).
  stamp: null,
};

const initialRecordingState = {
  state: RECORDING_STATE.IDLE,
  // 6.5절: Session ID 는 10진 문자열. "" 은 세션 없음.
  active_session_id: "",
  active_bag_path: "",
  active_started_at: null,
  last_session_id: "",
  last_bag_path: "",
  last_started_at: null,
  last_ended_at: null,
  result_pending: false,
  last_mimic_result: RECORDING_RESULT.UNSET,
  message: "",
};

/** RFC 3339 UTC Z (밀리초 3자리). 6.4절 timestamp 형식과 같게 보낸다. */
function utcNowZ() {
  return new Date().toISOString().replace(/(\.\d{3})\d*Z$/, "$1Z");
}

// ── .msg 원문 dump 정규화 ──────────────────────────────────────────────────
//
// 브릿지는 message_to_ordereddict 결과를 그대로 얹는다. 필드명은 .msg 그대로라
// 손댈 것이 없고 uint8 enum 만 문자열로 바꾼다. 없는 필드는 기본값으로 채우지
// 않는다 — FR-24 "가짜 값으로 채우지 않는다".

function normalizeControlState(raw) {
  return {
    ...raw,
    active_mode: toSymbol(raw.active_mode, CONTROL_MODE_BY_ORDINAL, CONTROL_MODE.DISABLED,
      "ControlState.active_mode"),
    active_owner: toSymbol(raw.active_owner, CONTROL_OWNER_BY_ORDINAL, CONTROL_OWNER.NONE,
      "ControlState.active_owner"),
    owner_alive: raw.owner_alive === true,
    sequence_running: raw.sequence_running === true,
  };
}

function normalizeSafetyState(raw) {
  return { ...raw, state: toSymbol(raw.state, SAFETY_STATES, "INIT", "SafetyState.state") };
}

function normalizeRecordingState(raw) {
  return {
    ...raw,
    state: toSymbol(raw.state, RECORDING_STATE_BY_ORDINAL, RECORDING_STATE.IDLE,
      "RecordingState.state"),
    last_mimic_result: toSymbol(
      raw.last_mimic_result, RECORDING_RESULT_BY_ORDINAL, RECORDING_RESULT.UNSET,
      "RecordingState.last_mimic_result",
    ),
    active_session_id: readSessionId(raw.active_session_id),
    last_session_id: readSessionId(raw.last_session_id),
    result_pending: raw.result_pending === true,
  };
}

function normalizeHandCommand(raw) {
  return { ...raw, source: toSymbol(raw.source, HAND_SOURCE_BY_ORDINAL, null, "HandCommand.source") };
}

/** 브릿지가 준 객체인지, 아직 못 받은 {} 인지 (6.4절). */
function received(object) {
  return Boolean(object) && typeof object === "object" && Object.keys(object).length > 0;
}

let requestCounter = 0;
function nextRequestId() {
  requestCounter += 1;
  return `web-${Date.now().toString(36)}-${requestCounter}`;
}

export function HandSocketProvider({ children }) {
  const [connectionState, setConnectionState] = useState("connecting");
  const [controlState, setControlState] = useState(initialControlState);
  const [safetyState, setSafetyState] = useState(initialSafetyState);
  // 6.4절 {} 규칙: 브릿지에서 SafetyState 를 실제로 받았는지.
  // false 면 initialSafetyState 의 값은 관측값이 아니므로 경고를 띄우지 않는다.
  const [safetyStateKnown, setSafetyStateKnown] = useState(false);
  // 6.4절 snapshot 의 top-level timestamp.
  // FR-25 stale 판정은 브라우저 시계가 아니라 이 값과 motor_state.stamp 의
  // 차이로 낸다. 둘 다 서버가 찍은 시각이라 시계 오차의 영향을 받지 않는다.
  const [snapshotAt, setSnapshotAt] = useState(null);
  // 마지막 snapshot 을 받은 브라우저 시각.
  //
  // snapshotAt(서버가 찍은 시각)만으로는 서버가 죽은 것을 알 수 없다. 서버가
  // 멈추면 snapshotAt 과 motor_state.stamp 가 함께 멈춰서 둘의 차이가 0 으로
  // 고정되고, 모터 상태가 "전체 통신 정상" 인 채로 영원히 남는다.
  // "언제 마지막으로 받았는가" 는 브라우저 시계로만 잴 수 있다 (FR-25).
  //
  // 이 값은 경과 시간을 재는 데만 쓴다. 서버 시각과 절대 비교하지 않으므로
  // 브라우저 시계 오차의 영향을 받지 않는다.
  const [snapshotReceivedAt, setSnapshotReceivedAt] = useState(null);
  const [recordingState, setRecordingState] = useState(initialRecordingState);
  const [handCommand, setHandCommand] = useState(null);
  const [landmarks, setLandmarks] = useState(null);
  const [landmarksUpdatedAt, setLandmarksUpdatedAt] = useState(null);
  const [motorStatus, setMotorStatus] = useState(null);
  // 브릿지가 control_state·recording 을 실제로 보냈는지. 누락을 화면에 드러낸다.
  const [controlStateKnown, setControlStateKnown] = useState(false);
  const [recordingStateKnown, setRecordingStateKnown] = useState(false);
  // 6.5절 위반 감지: session_id 가 숫자로 왔다.
  const [sessionIdProtocolError, setSessionIdProtocolError] = useState(false);
  // 웹이 방금 요청한 mode. 확정은 control_state 로만 판단한다 (FR-19).
  const [requestedMode, setRequestedMode] = useState(null);
  // ack 를 기다리는 요청 하나. 버튼 중복 클릭만 막는다 (FR-22 는 로봇이 강제).
  const [inFlight, setInFlight] = useState(null);
  // hand-loss latch 근사. 브릿지가 값을 주면 쓰이지 않는다.
  const [handLossApprox, setHandLossApprox] = useState(false);
  // 조각별 마지막 "변경" 시각(브라우저 시계). stamp 를 파싱하지 않고 지문 비교로 잰다.
  const [sectionUpdatedAt, setSectionUpdatedAt] = useState({});
  // 브릿지가 connection_status 를 직접 주면 그것을 우선한다.
  const [bridgeConnectionStatus, setBridgeConnectionStatus] = useState(null);
  // 시간이 흘러야 up→down 이 바뀐다. 주기적으로 다시 계산한다.
  const [nowTick, setNowTick] = useState(() => Date.now());
  // 화면이 {code, message} 형태를 기대한다 (StatusBar 는 code, SafetyBanner 는 둘 다).
  // 문자열로 바꾸면 "오류:" 와 "[]" 만 표시된다.
  const [lastError, setLastError] = useState(null);
  const [modeRejectedReason, setModeRejectedReason] = useState("");
  const [needsResumeConfirmation, setNeedsResumeConfirmation] = useState(false);

  const wsRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  const reconnectAttemptRef = useRef(0);
  const closedByUnmountRef = useRef(false);

  // FR-34 갱신용. 현재 웹이 보유한 mode 를 기억해 1000ms 마다 재요청한다.
  const heldModeRef = useRef(null);
  const renewTimerRef = useRef(null);

  // 직전에 제어권을 가지고 있었는지. FR-27 안내 판단에 쓴다.
  const hadControlRef = useRef(false);

  // hand-loss 근사용 타이머 기준점, ack 대기 타임아웃 핸들
  const handLostSinceRef = useRef(null);
  const ackTimerRef = useRef(null);
  // { 조각이름: 마지막 지문 } — 변경 감지용
  const sectionTokenRef = useRef({});
  // inFlight 의 ref 사본.
  //
  // setInFlight 의 updater 안에서 매칭 여부를 세우고 그 변수를 바로 읽으면 안 된다.
  // React 는 updater 를 즉시 실행한다고 보장하지 않으므로(지연·재실행 가능)
  // 매칭 판정이 항상 false 로 읽혔다. ack 판정은 렌더와 무관하므로 ref 로 읽는다.
  const inFlightRef = useRef(null);

  // ── 전송 ──

  const clearAckTimer = useCallback(() => {
    if (ackTimerRef.current) {
      clearTimeout(ackTimerRef.current);
      ackTimerRef.current = null;
    }
  }, []);

  /**
   * 요청 하나를 보낸다.
   *
   * track 을 켜면 ack 가 올 때까지 inFlight 에 남겨 버튼을 잠근다. lease 갱신처럼
   * 1초마다 반복되는 요청은 추적하지 않는다 — 추적하면 갱신 ack 를 기다리는 동안
   * 조작이 계속 막힌다.
   */
  const sendRequest = useCallback((type, payload = {}, { track = false } = {}) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setLastError({ code: "NOT_CONNECTED", message: "서버에 연결되어 있지 않습니다." });
      diag.error({
        code: "SEND_WHILE_CLOSED",
        owner: OWNER.CONFIG,
        what: `연결이 없는 상태에서 ${type} 요청을 보내려 했습니다`,
        why: "요청이 전송되지 않았습니다. 화면 조작이 아무 반응 없이 무시됩니다.",
        fix: "WebSocket 연결 상태를 먼저 확인하세요. 상단 상태바의 연결 표시를 보세요.",
        ref: "web/docs/interfaces-bridge.md 1.5",
        detail: { 요청: type, payload },
      });
      return false;
    }
    const requestId = nextRequestId();
    // 요청 envelope: request_id, type, timestamp, payload
    ws.send(JSON.stringify({
      request_id: requestId,
      type,
      timestamp: utcNowZ(),
      payload,
    }));
    if (track) {
      inFlightRef.current = { id: requestId, type };
      setInFlight(inFlightRef.current);
      clearAckTimer();
      // ack 가 유실돼도 잠금이 남지 않게 한다.
      ackTimerRef.current = setTimeout(() => {
        ackTimerRef.current = null;
        inFlightRef.current = null;
        setInFlight(null);
      }, PENDING.ACK_TIMEOUT_MS);
    }
    return requestId;
  }, [clearAckTimer]);

  // ── FR-34 lease 갱신 ──
  //
  // "현재 owner 가 같은 mode·owner 요청을 1000ms 마다 보낸다."
  // 3000ms 동안 갱신이 없으면 owner lease 가 만료되어 안전 전이한다.
  // 갱신 요청은 명령 timeout 을 갱신하지 않는다 (FR-11).

  const stopRenewTimer = useCallback(() => {
    if (renewTimerRef.current) {
      clearInterval(renewTimerRef.current);
      renewTimerRef.current = null;
    }
  }, []);

  const startRenewTimer = useCallback((mode) => {
    stopRenewTimer();
    heldModeRef.current = mode;
    renewTimerRef.current = setInterval(() => {
      const held = heldModeRef.current;
      if (!held) return;
      sendRequest(CLIENT_MESSAGE.SET_CONTROL_MODE, {
        requested_mode: held, requested_owner: WEB_OWNER,
      });
    }, TIMING.CONTROL_RENEW_PERIOD_MS);
  }, [sendRequest, stopRenewTimer]);

  // ── 수신 ──

  const applySnapshot = useCallback((snap) => {
    const now = Date.now();
    observeSnapshotRate(PENDING.BRIDGE_SNAPSHOT_PERIOD_MS);

    // 6.4절 고정 6필드 누락
    const missingFixed = SNAPSHOT_FIXED_FIELDS.filter((k) => !(k in snap));
    if (missingFixed.length > 0) {
      diag.error({
        code: "SNAPSHOT_MISSING_FIXED_FIELDS",
        owner: OWNER.BRIDGE,
        what: `snapshot 에 6.4절 고정 필드가 빠졌습니다: ${missingFixed.join(", ")}`,
        why: "빠진 항목에 해당하는 화면이 '수신 대기' 로 남습니다.",
        fix: "아직 값을 못 받은 객체는 null 이 아니라 {} 로 채워 보내세요.",
        ref: "web/docs/interfaces-bridge.md 1.1",
        detail: { 누락: missingFixed, 받은키: Object.keys(snap) },
      });
    }
    setSnapshotReceivedAt(now);
    const snapshotAt = typeof snap.timestamp === "string" ? snap.timestamp : null;
    if (snapshotAt) setSnapshotAt(snapshotAt);

    // ── ControlState ──────────────────────────────────────────────────────
    // 6.4절이 고정한 top-level mode 는 symbolic string 이지만 owner 가 없다.
    // FR-19 Must 가 "현재 mode·owner 를 /thing/control_state 로 확인해야 한다" 이므로
    // owner_alive 없이는 제어권 판정을 할 수 없다.
    // control_state 가 오면 그것을 쓰고, 없으면 top-level mode 만 반영한 뒤
    // controlStateKnown=false 로 "owner 는 아직 모른다" 를 화면에 드러낸다.
    if (received(snap.control_state)) {
      setControlState(normalizeControlState(snap.control_state));
      setControlStateKnown(true);
    } else {
      setControlStateKnown(false);
      diag.error({
        code: "SNAPSHOT_NO_CONTROL_STATE",
        owner: OWNER.BRIDGE,
        what: "snapshot 에 control_state 가 없습니다",
        why: "owner·owner_alive 를 알 수 없어 웹이 제어권을 인정하지 않습니다(fail-closed). "
          + "모드 획득 버튼이 잠기고 조작이 전혀 안 됩니다.",
        fix: "ControlState.msg 원문을 message_to_ordereddict 로 dump 해 "
          + "control_state 키로 실어 보내세요.",
        ref: "FR-19 / web/docs/interfaces-bridge.md 1.2",
      });
      const topMode = toSymbol(snap.mode, CONTROL_MODE_BY_ORDINAL, null, "snapshot.mode");
      if (topMode) {
        setControlState((prev) => (
          prev.active_mode === topMode && prev.active_owner === CONTROL_OWNER.NONE
            ? prev
            : { ...initialControlState, active_mode: topMode }
        ));
      }
    }

    // ── SafetyState ───────────────────────────────────────────────────────
    // 6.4절 {} 규칙: 빈 객체는 truthy 라 그대로 넣으면 state 가 undefined 가 된다.
    // "아직 모른다" 와 "INIT 을 관측했다" 를 구분한다.
    if (received(snap.safety_state)) {
      setSafetyState(normalizeSafetyState(snap.safety_state));
      setSafetyStateKnown(true);
    } else {
      setSafetyStateKnown(false);
    }

    // ── RecordingState ────────────────────────────────────────────────────
    // top-level recording_state 는 상태 문자열뿐이라 session_id 가 없다.
    // FR-40 의 StopRecording·SetMimicResult 가 session_id 를 요구하므로
    // RecordingState.msg 원문이 필요하다.
    if (received(snap.recording)) {
      setRecordingState(normalizeRecordingState(snap.recording));
      setRecordingStateKnown(true);
      // 6.5절 위반 감지. 한 번 서면 새로고침 전까지 유지한다.
      if (isNumericSessionId(snap.recording.active_session_id)
          || isNumericSessionId(snap.recording.last_session_id)) {
        setSessionIdProtocolError(true);
      }
    } else {
      setRecordingStateKnown(false);
      diag.warn({
        code: "SNAPSHOT_NO_RECORDING",
        owner: OWNER.BRIDGE,
        what: "snapshot 에 recording 이 없습니다",
        why: "session_id 를 알 수 없어 StopRecording·SetMimicResult 를 부를 수 없습니다. "
          + "웹이 녹화 시작 버튼을 막습니다. 조작·모방 제어에는 영향이 없습니다.",
        fix: "RecordingState.msg 원문을 recording 키로 실어 보내세요. "
          + "(recording_state 는 6.4절이 상태 문자열로 이미 쓰고 있어 이름이 다릅니다)",
        ref: "FR-26 / FR-40 / web/docs/interfaces-bridge.md 1.2",
      });
      const topState = toSymbol(
        snap.recording_state, RECORDING_STATE_BY_ORDINAL, null, "snapshot.recording_state",
      );
      if (topState) {
        setRecordingState((prev) => (
          prev.state === topState ? prev : { ...initialRecordingState, state: topState }
        ));
      }
    }

    // ── landmarks / motor_state / command ─────────────────────────────────
    let landmarksNext = null;
    if (received(snap.landmarks)) {
      landmarksNext = snap.landmarks;
      setLandmarks(landmarksNext);
      setLandmarksUpdatedAt(now);
    }
    if (received(snap.motor_state)) setMotorStatus(snap.motor_state);
    if (received(snap.last_hand_command)) {
      const cmd = snap.last_hand_command;
      // FR-30: HandCommand 의 7축은 최상위 고정 필드다. values 래퍼는 없다.
      if (cmd.values !== undefined && typeof cmd.thumb_flex !== "number") {
        diag.error({
          code: "HAND_COMMAND_VALUES_WRAPPER",
          owner: OWNER.BRIDGE,
          what: "last_hand_command 의 7축이 values 래퍼 안에 있습니다",
          why: "FR-30 의 HandCommand 는 7축이 최상위 필드입니다. 웹이 축값을 못 읽어 "
            + "7논리축 표가 비어 보입니다.",
          fix: "HandCommand.msg 원문을 그대로 dump 하세요. 래퍼로 감싸지 마세요.",
          ref: "web/docs/interfaces-bridge.md 1.3.2",
          detail: { 받은키: Object.keys(cmd) },
        });
      }
      setHandCommand(normalizeHandCommand(cmd));
    }

    // ── FR-24 장치 연결 상태 ───────────────────────────────────────────────
    // 브릿지가 직접 주면 그대로 쓰고, 없으면 조각별 마지막 변경 시각에서 파생한다.
    setBridgeConnectionStatus(
      received(snap.connection_status) ? snap.connection_status : null,
    );

    // 조각이 "바뀌었는지" 만 본다. stamp 위치(stamp vs header.stamp)와 타입
    // ({sec,nanosec} vs 문자열)에 의존하지 않는다.
    const sections = {
      landmarks: snap.landmarks,
      motor_state: snap.motor_state,
      safety_state: snap.safety_state,
      control_state: snap.control_state,
      recording: snap.recording,
    };
    let changed = false;
    const nextUpdated = { ...sectionTokenRef.current.at };
    for (const [key, object] of Object.entries(sections)) {
      const token = sectionToken(object);
      if (token === null) continue;
      if (sectionTokenRef.current[key] !== token) {
        sectionTokenRef.current[key] = token;
        nextUpdated[key] = now;
        changed = true;
      } else if (nextUpdated[key] === undefined) {
        nextUpdated[key] = now;
        changed = true;
      }
    }
    if (changed) {
      sectionTokenRef.current.at = nextUpdated;
      setSectionUpdatedAt(nextUpdated);
    }

    // ── FR-27 hand-loss latch 근사 ────────────────────────────────────────
    // 브릿지가 hand_loss_latched 를 주면 그것이 우선한다 (아래 파생 상태).
    // 없을 때만 여기서 근사한다. 8.1절 "150ms 뒤 발행 중단" 을 기준으로 삼는다.
    const detection = handDetectionState(landmarksNext ?? snap.landmarks);
    const lost = detection === HAND_DETECTION.NOT_DETECTED
      || detection === HAND_DETECTION.LOW_CONFIDENCE;
    if (lost) {
      if (handLostSinceRef.current === null) handLostSinceRef.current = now;
      if (now - handLostSinceRef.current >= TIMING.HAND_LOSS_DEBOUNCE_MS) {
        setHandLossApprox(true);
      }
    } else {
      handLostSinceRef.current = null;
      // 재검출만으로 풀지 않는다 (FR-27). 새 mode 획득에서만 푼다.
    }
  }, []);

  const applyAck = useCallback((ack) => {
    if (typeof ack.request_id !== "string" || ack.request_id.length === 0) {
      diag.error({
        code: "ACK_NO_REQUEST_ID",
        owner: OWNER.BRIDGE,
        what: "ack 에 request_id 가 없습니다",
        why: "웹이 어느 요청의 응답인지 몰라 버튼 잠금을 풀지 못합니다. "
          + `${PENDING.ACK_TIMEOUT_MS}ms 뒤 타임아웃으로만 풀립니다.`,
        fix: "요청의 request_id 를 그대로 되돌려 보내세요.",
        ref: "web/docs/interfaces-bridge.md 1.6",
        detail: ack,
      });
    }
    if (typeof ack.accepted !== "boolean") {
      diag.error({
        code: "ACK_NO_ACCEPTED",
        owner: OWNER.BRIDGE,
        what: "ack 의 accepted 가 boolean 이 아닙니다",
        why: "웹이 수락·거부를 판단하지 못해 거부로 처리합니다.",
        fix: "accepted 를 true/false 로 보내세요.",
        ref: "web/docs/interfaces-bridge.md 1.6",
        detail: { accepted: ack.accepted, 타입: typeof ack.accepted },
      });
    }
    if (!ack.accepted && !Object.values(REJECT_REASON).includes(ack.reason)) {
      diag.warn({
        code: `ACK_UNKNOWN_REASON_${ack.reason}`,
        owner: OWNER.BRIDGE,
        what: `FR-37 표준에 없는 거부 사유입니다: ${ack.reason}`,
        why: "웹이 안내 문구를 만들지 못해 원문을 그대로 보여 줍니다.",
        fix: "FR-37 표준 8종 중 하나를 쓰거나, 새 사유라면 웹에 알려 문구를 추가하세요.",
        ref: "FR-37 / messageProtocol.js 의 REJECT_REASON",
        detail: { 받은사유: ack.reason, 표준: Object.values(REJECT_REASON) },
      });
    }
    // FR-22 잠금은 ack 왕복 동안만 건다. 실제 중복 실행 차단은 로봇이 한다
    // (FR-31 "새 일반 동작은 큐에 쌓지 않고 거부", FR-37 motion_active).
    const matched = Boolean(
      inFlightRef.current && ack.request_id === inFlightRef.current.id,
    );
    if (matched) {
      inFlightRef.current = null;
      clearAckTimer();
      setInFlight(null);
    }

    if (ack.accepted) {
      // 추적하지 않은 요청의 ack 로 화면을 지우지 않는다.
      //
      // lease 갱신은 1000ms 마다 set_control_mode 를 보내고 매번 accepted ack 를
      // 받는다. 그 ack 로 lastError 를 지우면 FR-27 이 요구하는 거부 사유가
      // 1초 만에 사라져 사용자가 읽을 수 없다.
      if (matched) {
        setModeRejectedReason("");
        setLastError(null);
      }
      return;
    }
    // 거부된 mode 요청은 대기 표시를 지운다.
    setRequestedMode(null);
    const message = describeReason(ack.reason);
    setModeRejectedReason(message);
    // code 는 FR-37 사유 원문, message 는 사용자 문구
    setLastError({ code: ack.reason, message });

// 갱신이 거부되면 제어권을 잃은 것으로 보고 갱신을 멈춘다.
    // FR-27: 연결 복구만으로 제어가 재개된 것처럼 표시하지 않는다.
    // owner_lease_expired 는 manager 가 DISABLED/NONE 을 발행한 뒤 거부하므로
    // (interfaces.md) 제어권 상실로 처리한다. stop_in_progress 는 일시적 차단이라
    // 여기 포함하지 않는다 — 잠시 뒤 재시도로 풀린다.
    if (ack.reason === REJECT_REASON.OWNER_CONFLICT
        || ack.reason === REJECT_REASON.OWNER_LEASE_EXPIRED
        || ack.reason === REJECT_REASON.SAFETY_NOT_READY) {
      heldModeRef.current = null;
      stopRenewTimer();
      if (hadControlRef.current) setNeedsResumeConfirmation(true);
    }
  }, [clearAckTimer, stopRenewTimer]);

  // ── 연결 ──

  useEffect(() => {
    // 무엇을 가정하고 도는지 먼저 밝힌다. 통합 시험 중 "이 숫자 근거가 뭐냐" 를
    // 콘솔만 보고 답할 수 있어야 한다.
    announceStartup({
      wsUrl: import.meta.env?.VITE_WS_URL ?? "",
      mjpegUrl: import.meta.env?.VITE_MJPEG_STREAM_URL ?? "",
      pending: PENDING,
      thresholds: {
        SECTION_STALE_MS: THRESHOLD.SECTION_STALE_MS,
        MOTOR_STALE_MS: THRESHOLD.MOTOR_STALE_MS,
        CAMERA_STATE_STALE_MS: THRESHOLD.CAMERA_STATE_STALE_MS,
        NO_SNAPSHOT_MS: THRESHOLD.NO_SNAPSHOT_MS,
      },
    });
    closedByUnmountRef.current = false;

    function connect() {
      setConnectionState("connecting");
      let ws;
      try {
        ws = new WebSocket(resolveWsUrl());
      } catch {
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttemptRef.current = 0;
        setConnectionState("open");
        setLastError(null);
        // NFR-15: "재연결은 이전 명령을 재생하지 않는다."
        // 이전에 보유했던 mode 를 자동 재획득하지 않는다. 사용자가 다시 선택해야 한다.
        heldModeRef.current = null;
        stopRenewTimer();
        if (hadControlRef.current) setNeedsResumeConfirmation(true);
      };

      ws.onmessage = (event) => {
        let message;
        try {
          message = JSON.parse(event.data);
        } catch {
          // 조용히 버리면 화면이 "연결됨" 인데 아무것도 안 움직이는 상태가 된다.
          diag.error({
            code: "WS_NOT_JSON",
            owner: OWNER.BRIDGE,
            what: "WebSocket 으로 JSON 이 아닌 데이터가 왔습니다",
            why: "웹이 해석하지 못해 버립니다. 화면은 연결됨으로 보이지만 갱신되지 않습니다.",
            fix: "브릿지가 UTF-8 JSON 텍스트 프레임을 보내는지 확인하세요.",
            ref: "요구사항 명세서 6.4절",
            detail: String(event.data).slice(0, 200),
          });
          return;
        }
        if (isSnapshot(message)) {
          applySnapshot(message);
          return;
        }
        if (message.type === SERVER_MESSAGE.ACK) {
          applyAck(message);
          return;
        }
        // snapshot 도 ack 도 아니다. 왜 아닌지 구체적으로 짚는다.
        // isSnapshot 은 6.4절 고정 6필드 중 mode 가 문자열인지로 판별하므로,
        // 브릿지가 mode 를 정수로 보내면 snapshot 전체가 여기로 떨어진다.
        const missing = SNAPSHOT_FIXED_FIELDS.filter((k) => !(k in message));
        const modeType = typeof message.mode;
        diag.error({
          code: "WS_UNRECOGNIZED_MESSAGE",
          owner: OWNER.BRIDGE,
          what: "서버가 보낸 메시지를 snapshot 으로도 ack 로도 해석하지 못했습니다",
          why: modeType !== "string" && modeType !== "undefined"
            ? `6.4절은 top-level mode 를 symbolic string 으로 고정했는데 `
              + `${modeType} 이 왔습니다. 이 때문에 snapshot 전체가 버려집니다. `
              + "화면은 연결됨인데 모드·상태가 전혀 갱신되지 않습니다."
            : "고정 6필드가 빠졌거나 snapshot 에 type 필드가 붙어 있습니다. "
              + "웹은 type 유무로 snapshot 과 ack 를 구분합니다.",
          fix: modeType !== "string" && modeType !== "undefined"
            ? 'mode 와 recording_state 만은 symbolic string 으로 보내세요. 예) "MIMIC"'
            : "snapshot 에는 type 을 넣지 말고 6.4절 고정 6필드를 모두 채우세요.",
          ref: "web/docs/interfaces-bridge.md 1.1 / 1.6",
          detail: {
            받은키: Object.keys(message),
            누락된고정필드: missing,
            "mode 타입": modeType,
            "mode 값": message.mode,
          },
        });
      };

      ws.onerror = () => {
        setLastError({ code: "SOCKET_ERROR", message: "서버 연결에 문제가 발생했습니다." });
        diag.error({
          code: "WS_ERROR",
          owner: OWNER.CONFIG,
          what: "WebSocket 연결에 실패했습니다",
          why: "주소가 틀렸거나, 브릿지가 안 떠 있거나, 방화벽에 막혔습니다.",
          fix: `주소를 확인하세요: ${resolveWsUrl()}  `
            + "Jetson 에서 web_bridge_node 가 실행 중인지, 포트가 열려 있는지 확인하세요.",
          ref: "FR-28 / web/frontend/.env",
          detail: { 시도한주소: resolveWsUrl() },
        });
      };

      ws.onclose = () => {
        wsRef.current = null;
        heldModeRef.current = null;
        stopRenewTimer();
        clearAckTimer();
        inFlightRef.current = null;
        setConnectionState("closed");
        setInFlight(null);
        setRequestedMode(null);
        // FR-24: 실제 값을 추정하거나 유지하지 않는다.
        setBridgeConnectionStatus(null);
        setSectionUpdatedAt({});
        sectionTokenRef.current = {};
        setControlStateKnown(false);
        setRecordingStateKnown(false);
        setSafetyStateKnown(false);
        if (!closedByUnmountRef.current) scheduleReconnect();
      };
    }

    function scheduleReconnect() {
      const attempt = reconnectAttemptRef.current;
      reconnectAttemptRef.current = attempt + 1;
      const delay = Math.min(
        PENDING.RECONNECT_BASE_DELAY_MS * 2 ** attempt,
        PENDING.RECONNECT_MAX_DELAY_MS,
      );
      reconnectTimerRef.current = setTimeout(connect, delay);
    }

    connect();

    return () => {
      closedByUnmountRef.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      stopRenewTimer();
      clearAckTimer();
      wsRef.current?.close();
    };
  }, [applyAck, applySnapshot, clearAckTimer, stopRenewTimer]);

  // 신선도 판정을 주기적으로 다시 계산한다. snapshot 이 멈춰도 시간은 흐른다.
  useEffect(() => {
    const id = setInterval(() => setNowTick(Date.now()), THRESHOLD.RECHECK_PERIOD_MS);
    return () => clearInterval(id);
  }, []);

  // ── 파생 상태 ──

  // FR-24: 연결이 끊겼으면 추정하지 않는다. 브릿지 값이 있으면 그것이 우선.
  const connectionStatus = useMemo(() => {
    if (connectionState !== "open") return emptyConnectionStatus();
    if (bridgeConnectionStatus) {
      return { ...emptyConnectionStatus(), ...bridgeConnectionStatus };
    }
    const derived = deriveConnectionStatus(
      nowTick, sectionUpdatedAt, motorStatus, THRESHOLD.SECTION_STALE_MS,
    );

    // 어떤 조각의 stamp 가 멈춰서 down 이 됐는지 짚어 준다.
    // "카메라 끊김" 만 보면 카메라를 의심하지만, 실제로는 브릿지가 그 조각을
    // 안 갱신하고 있거나 stamp 가 전진하지 않는 경우가 많다.
    for (const [key, at] of Object.entries(sectionUpdatedAt)) {
      const age = nowTick - at;
      if (age <= THRESHOLD.SECTION_STALE_MS) {
        diag.clear(`SECTION_STALE_${key}`);
        continue;
      }
      diag.warn({
        code: `SECTION_STALE_${key}`,
        owner: OWNER.BRIDGE,
        what: `snapshot 의 ${key} 가 ${Math.round(age / 1000)}초째 바뀌지 않았습니다`,
        why: "웹은 조각의 stamp 가 전진하는지로 발행 주체의 생사를 판정합니다. "
          + "해당 장치가 'down' 으로 표시됩니다.",
        fix: "① 그 토픽을 내는 노드가 살아 있는지 ② 브릿지가 최신 값으로 갱신해 "
          + "보내는지 ③ stamp 가 매 발행마다 전진하는지 순서로 확인하세요. "
          + "발행 주기가 느린 것뿐이라면 pending.js 의 BRIDGE_SNAPSHOT_PERIOD_MS 를 "
          + "실제 값으로 고치세요.",
        ref: "FR-24 / web/docs/interfaces-bridge.md 1.3.1",
        detail: { 조각: key, 경과: `${age}ms`, 임계: `${THRESHOLD.SECTION_STALE_MS}ms` },
      });
    }
    return derived;
  }, [connectionState, bridgeConnectionStatus, nowTick, sectionUpdatedAt, motorStatus]);

  // FR-38: gesture 는 SafetyState=READY|RUN 에서만 수락된다.
  // 안전 상태를 아직 받지 못했으면 조작을 허용하지 않는다 (fail-closed).
  const isSafeToOperate = (
    safetyStateKnown && OPERATION_ALLOWED_STATES.includes(safetyState.state)
  );

  // FR-34: 유효 lease 일 때만 owner_alive 가 true 다.
  // control_state 를 못 받았으면 owner 를 알 수 없으므로 제어권도 없다고 본다
  // (fail-closed). 브릿지가 필드를 빠뜨렸을 때 조작이 열리면 안 된다.
  const webHasControl = (
    controlStateKnown
    && controlState.active_owner === CONTROL_OWNER.WEB
    && controlState.owner_alive === true
  );

  const isRecordingBusy = RECORDING_BUSY_STATES.includes(recordingState.state);

  // FR-35: reset_safety 는 SAFE·FAULT·ESTOP 에서만 쓴다.
  // 안전 상태를 아직 받지 못했으면 허용하지 않는다 (fail-closed).
  const canResetSafety = safetyStateKnown && RESET_ALLOWED_STATES.includes(safetyState.state);

  // FR-20 / FR-27: 8.1절 기준(confidence 0.70 미만도 미검출)으로 판정한다.
  const handDetection = handDetectionState(landmarks);
  // FR-27: hand-loss latch 는 원래 Web Bridge 가 landmark 스트림에서 파생한다.
  // 브릿지가 hand_loss_latched 를 실어 주면 그대로 쓰고, 없으면 프런트가 검출
  // 상태 지속시간으로 근사한다. 어느 쪽이든 재검출만으로 풀리지 않는다.
  const handLossLatched = landmarks?.hand_loss_latched !== undefined
    ? Boolean(landmarks.hand_loss_latched)
    : handLossApprox;
  // 재검출 진행률은 브릿지가 줄 때만 표시한다. 근사로 만들어 내지 않는다.
  const reacquireElapsedMs = landmarks?.reacquire_elapsed_ms ?? 0;
  const reacquireStableMs = landmarks?.reacquire_stable_ms ?? 0;

  // 제어권 보유 여부를 추적해 상실 시 안내한다 (FR-27)
  // control_state 는 로봇이 권위를 갖는 외부 상태다. 그 값이 바뀔 때 갱신 타이머와
  // 안내 플래그를 맞추는 것이 이 effect 의 일이라 setState 가 들어가는 것이 정상이다.
  useEffect(() => {
    if (webHasControl) {
      hadControlRef.current = true;
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setNeedsResumeConfirmation(false);
      setRequestedMode(null);
      // FR-27 / FR-35: latch 는 새 mode·owner 획득으로만 풀린다.
      setHandLossApprox(false);
      handLostSinceRef.current = null;
      // 확정된 mode 로 갱신 타이머를 맞춘다 (FR-19: control_state 가 권위)
      if (WEB_SELECTABLE_MODES.includes(controlState.active_mode)
          && heldModeRef.current !== controlState.active_mode) {
        startRenewTimer(controlState.active_mode);
      }
    } else if (hadControlRef.current) {
      // 제어권을 잃었다. 자동 재개하지 않고 안내만 한다 (FR-35).
      heldModeRef.current = null;
      stopRenewTimer();
      setNeedsResumeConfirmation(true);
    }
  }, [webHasControl, controlState.active_mode, startRenewTimer, stopRenewTimer]);

  // ── 조작 API (기존 시그니처 유지) ──

  const selectMode = useCallback((nextMode) => {
    if (!WEB_SELECTABLE_MODES.includes(nextMode)) {
      setLastError({ code: "INVALID_MODE", message: "웹에서 선택할 수 없는 모드입니다." });
      return false;
    }
    setModeRejectedReason("");
    setRequestedMode(nextMode);
    // SetControlMode.srv 요청 필드는 requested_mode·requested_owner 다.
    // 값은 symbolic string 으로 보내고 브릿지가 uint8 상수로 바꾼다.
    return sendRequest(CLIENT_MESSAGE.SET_CONTROL_MODE, {
      requested_mode: nextMode,
      requested_owner: WEB_OWNER,
    });
  }, [sendRequest]);

  /**
   * Gesture 를 보낸다.
   *
   * commandPresets.js 의 preset 객체를 그대로 받는다. { id, speed_limit, ... }
   * 개별 인수 형태도 허용한다: sendGesture("open", 1.0)
   *
   * 7축 값은 검증하지 않는다. ExecuteGesture.srv 의 요청 필드는 gesture_name 과
   * speed_limit 뿐이고 7축 목표는 FR-41 이 YAML 소관으로 두었다. 웹은 축값을
   * 보내지도 들고 있지도 않으므로 검증할 대상 자체가 없다.
   *
   * (이전 구현은 preset 에서 values 를 제거한 뒤에도 isValidAxisValues(values) 를
   *  남겨 두어, 버튼 4개가 전부 undefined 로 검증에 걸려 아무것도 보내지 않았다.)
   */
  const sendGesture = useCallback((gestureOrId, speedArg) => {
    const isPreset = gestureOrId && typeof gestureOrId === "object";
    const gestureId = isPreset ? gestureOrId.id : gestureOrId;
    const speedLimit = isPreset ? gestureOrId.speed_limit : speedArg;

    // FR-35: 제어 재개 전에는 새 명령을 보내지 않는다.
    if (needsResumeConfirmation) {
      setLastError({
        code: "RESUME_REQUIRED",
        message: "제어가 재개되지 않았습니다. 모드를 다시 선택하세요.",
      });
      return false;
    }
    // FR-38: 이름이 한 글자만 달라도 서비스 호출이 실패한다. alias 를 먼저 편다.
    const canonical = GESTURE_ALIASES[gestureId] ?? gestureId;
    if (!CANONICAL_GESTURES.includes(canonical)) {
      setLastError({
        code: "INVALID_GESTURE",
        message: `지원하지 않는 동작입니다. (${gestureId})`,
      });
      diag.error({
        code: `INVALID_GESTURE_${gestureId}`,
        owner: OWNER.WEB,
        what: `canonical 이 아닌 gesture 를 보내려 했습니다: ${gestureId}`,
        why: "웹이 전송 전에 막았습니다. 서비스 호출은 일어나지 않았습니다.",
        fix: "commandPresets.js 의 id 가 FR-38 canonical 4종 또는 alias 인지 확인하세요.",
        ref: "FR-38",
        detail: { 받은id: gestureId, canonical: CANONICAL_GESTURES,
                  alias: Object.keys(GESTURE_ALIASES) },
      });
      return false;
    }
    // FR-32: speed_limit 은 0.0 초과 1.0 이하
    if (!isValidSpeedLimit(speedLimit)) {
      setLastError({
        code: "INVALID_SPEED_LIMIT",
        message: "speed_limit 은 0.0 초과 1.0 이하여야 합니다.",
      });
      diag.error({
        code: "INVALID_SPEED_LIMIT",
        owner: OWNER.WEB,
        what: `speed_limit 이 허용 범위를 벗어났습니다: ${speedLimit}`,
        why: "웹이 전송 전에 막았습니다. FR-32 는 0.0 초과 1.0 이하만 허용합니다.",
        fix: "src/config/pending.js 의 GESTURE_SPEED_LIMIT / SEQUENCE_SPEED_LIMIT 를 "
          + "확인하세요. 이 값들은 아직 미확정입니다.",
        ref: "FR-32 / web/docs/pending-decisions.md B-1",
        detail: { 받은값: speedLimit },
      });
      return false;
    }
    // 키 이름은 ExecuteGesture.srv 의 요청 필드명과 같아야 한다.
    return Boolean(sendRequest(CLIENT_MESSAGE.EXECUTE_GESTURE, {
      gesture_name: canonical,
      speed_limit: speedLimit,
    }, { track: true }));
  }, [needsResumeConfirmation, sendRequest]);

  // speed_limit 기본값을 두지 않는다. 근거 없는 숫자를 코드에 숨기지 않고
  // 호출부(commandPresets)가 pending.js 의 값을 명시적으로 넘기게 한다.
  const sendSequence = useCallback((sequenceId, speedLimit) => {
    // FR-39: countdown, scissors_rock_paper 만 지원한다.
    if (!SEQUENCE_IDS.includes(sequenceId)) {
      setLastError({
        code: "INVALID_SEQUENCE",
        message: "지원하지 않는 연속 동작입니다. countdown, scissors_rock_paper 만 가능합니다.",
      });
      return false;
    }
    // ExecuteSequence.action 의 goal 필드는 sequence_name 과 speed_limit 이다.
    return sendRequest(CLIENT_MESSAGE.EXECUTE_SEQUENCE, {
      sequence_name: sequenceId,
      speed_limit: speedLimit,
    }, { track: true });
  }, [sendRequest]);

  const sendStop = useCallback(() => {
    // FR-34: 누구나 내부망에서 안전 정지를 요청할 수 있다.
    // FR-31: STOP 과 새 동작이 동시에 발생하면 STOP 이 이긴다.
    // 대기 중인 요청이 있어도 STOP 은 막지 않고 잠금을 먼저 푼다.
    heldModeRef.current = null;
    stopRenewTimer();
    clearAckTimer();
    inFlightRef.current = null;
    setInFlight(null);
    setRequestedMode(null);
    return sendRequest(CLIENT_MESSAGE.STOP, {
      requested_mode: STOP_MODE, requested_owner: STOP_OWNER,
    });
  }, [clearAckTimer, sendRequest, stopRenewTimer]);

  /**
   * FR-27 / FR-35: 재검출이나 연결 복구만으로 제어가 재개된 것처럼 표시하지 않는다.
   * 이 함수는 안내를 닫기만 하고 아무 명령도 보내지 않는다.
   * 제어 재개는 사용자가 mode 를 다시 선택해야 한다.
   */
  const resumeControl = useCallback(() => {
    setNeedsResumeConfirmation(false);
    setModeRejectedReason("");
  }, []);

  // StartRecording.srv 의 요청 필드는 `string label` 이다.
  // 무엇을 담는지 규정이 없어 빈 문자열을 보낸다 (PENDING.START_RECORDING_LABEL).
  const startRecording = useCallback(
    () => sendRequest(
      CLIENT_MESSAGE.START_RECORDING, { label: PENDING.START_RECORDING_LABEL }, { track: true },
    ),
    [sendRequest],
  );

  /**
   * FR-40: StopRecording.srv 의 요청 필드는 uint64 session_id 다.
   *
   * 6.5절이 JSON 에서 10진 문자열로 고정했다. 브릿지가 숫자로 보내면 63-bit 값이
   * JSON.parse 시점에 손상되므로 그 값을 되돌려 보내지 않고 막는다.
   */
  const stopRecording = useCallback(() => {
    if (sessionIdProtocolError) {
      setLastError({
        code: "SESSION_ID_NOT_STRING",
        message: "로봇이 보낸 Session ID 형식이 잘못되어 기록을 종료할 수 없습니다. "
          + "브릿지가 session_id 를 10진 문자열로 보내야 합니다(6.5절).",
      });
      return false;
    }
    const sessionId = recordingState.active_session_id;
    if (!sessionId) {
      setLastError({
        code: "NO_ACTIVE_SESSION",
        message: "활성 기록 세션이 없습니다. 기록 상태를 다시 확인하세요.",
      });
      return false;
    }
    return sendRequest(
      CLIENT_MESSAGE.STOP_RECORDING, { session_id: sessionId }, { track: true },
    );
  }, [recordingState.active_session_id, sendRequest, sessionIdProtocolError]);

  /**
   * FR-40: SetMimicResult.srv 는 uint64 session_id 와 uint8 result 를 받는다.
   * 판정 대상은 정상 Stop 으로 닫힌 직전 세션이다 (FR-18).
   */
  const submitRecordingResult = useCallback((result) => {
    if (result !== RECORDING_RESULT.SUCCESS && result !== RECORDING_RESULT.FAILURE) {
      setLastError({ code: "INVALID_RESULT", message: "판정은 성공 또는 실패여야 합니다." });
      return false;
    }
    if (sessionIdProtocolError) {
      setLastError({
        code: "SESSION_ID_NOT_STRING",
        message: "로봇이 보낸 Session ID 형식이 잘못되어 판정을 보낼 수 없습니다. "
          + "브릿지가 session_id 를 10진 문자열로 보내야 합니다(6.5절).",
      });
      return false;
    }
    const sessionId = recordingState.last_session_id;
    if (!sessionId) {
      setLastError({
        code: "NO_PENDING_SESSION",
        message: "판정할 세션 ID 를 확인할 수 없습니다. 화면을 새로 고치세요.",
      });
      return false;
    }
    return sendRequest(CLIENT_MESSAGE.SET_MIMIC_RESULT, {
      session_id: sessionId,
      result,
    }, { track: true });
  }, [recordingState.last_session_id, sendRequest, sessionIdProtocolError]);

  const resetSafety = useCallback(() => {
    // FR-35: HOLD 에서는 이 서비스를 쓰지 않고 명시적 STOP 절차를 쓴다.
    if (!canResetSafety) {
      setLastError({
        code: "RESET_NOT_ALLOWED",
        message: "안전 초기화는 SAFE·FAULT·비상정지 상태에서만 사용합니다. "
          + "일시정지(HOLD)라면 정지(STOP)로 준비 상태로 돌아가세요.",
      });
      return false;
    }
    return sendRequest(CLIENT_MESSAGE.RESET_SAFETY);
  }, [canResetSafety, sendRequest]);

  // 제거된 API: sendCalibrationCapture (FR-04)
  //
  // FR-30 이 동결한 서비스 5종에 캘리브레이션이 없고 6.3절 서비스 목록에도 없다.
  // 6.4절은 클라이언트→서버 요청을 "기존 ROS 2 서비스·액션에만 매핑" 하도록
  // 제한하므로 매핑 대상이 없는 요청을 웹이 만들어 낼 수 없다.

  const value = useMemo(() => ({
    connectionState,
    controlState,
    controlStateKnown,
    safetyState,
    safetyStateKnown,
    recordingState,
    recordingStateKnown,
    sessionIdProtocolError,
    handCommand,
    landmarks,
    landmarksUpdatedAt,
    snapshotAt,
    snapshotReceivedAt,
    handDetection,
    handLossLatched,
    reacquireElapsedMs,
    reacquireStableMs,
    motorStatus,
    connectionStatus,
    sectionUpdatedAt,
    lastError,
    modeRejectedReason,
    needsResumeConfirmation,
    isSafeToOperate,
    webHasControl,
    isRecordingBusy,
    // 웹이 요청했지만 아직 control_state 로 확정되지 않은 mode
    requestedMode,
    // ack 를 기다리는 요청. 버튼 중복 클릭 방지에만 쓴다.
    commandInFlight: inFlight !== null,
    canResetSafety,
    selectMode,
    sendGesture,
    sendSequence,
    sendStop,
    resumeControl,
    startRecording,
    stopRecording,
    submitRecordingResult,
    resetSafety,
  }), [
    connectionState, controlState, controlStateKnown, safetyState, safetyStateKnown,
    recordingState, recordingStateKnown, sessionIdProtocolError,
    handCommand, handDetection, snapshotAt, snapshotReceivedAt,
    handLossLatched, reacquireElapsedMs, reacquireStableMs,
    landmarks, landmarksUpdatedAt, motorStatus, connectionStatus, sectionUpdatedAt, lastError,
    modeRejectedReason, needsResumeConfirmation, isSafeToOperate, webHasControl,
    isRecordingBusy, requestedMode, inFlight, canResetSafety,
    selectMode, sendGesture, sendSequence, sendStop, resumeControl,
    startRecording, stopRecording, submitRecordingResult,
    resetSafety,
  ]);

  return (
    <HandSocketContext.Provider value={value}>
      {children}
    </HandSocketContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useHandSocket() {
  const context = useContext(HandSocketContext);
  if (!context) {
    throw new Error("useHandSocket 은 HandSocketProvider 안에서만 사용할 수 있습니다.");
  }
  return context;
}
