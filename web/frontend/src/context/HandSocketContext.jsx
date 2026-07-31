// ============================================================================
// HandSocketContext — 브라우저 ↔ Jetson Web Bridge WebSocket
// ----------------------------------------------------------------------------
// 계약 출처는 요구사항 명세서 V6.3 단독이다.
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
// 이 파일은 backend/thing_bridge/consumers.py 와 쌍으로 유지한다.
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
  CLIENT_MESSAGE,
  CONTROL_MODE,
  CONTROL_OWNER,
  OPERATION_ALLOWED_STATES,
  RECORDING_BUSY_STATES,
  RECORDING_RESULT,
  RECORDING_STATE,
  REJECT_REASON,
  RESET_ALLOWED_STATES,
  SEQUENCE_IDS,
  SERVER_MESSAGE,
  STOP_MODE,
  STOP_OWNER,
  TIMING,
  WEB_OWNER,
  WEB_SELECTABLE_MODES,
  describeReason,
  emptyConnectionStatus,
  handDetectionState,
  isSnapshot,
  isValidAxisValues,
  isValidSpeedLimit,
} from "../config/messageProtocol";

const HandSocketContext = createContext(null);

const RECONNECT_BASE_DELAY_MS = 1000;
const RECONNECT_MAX_DELAY_MS = 10000;

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
  const [connectionStatus, setConnectionStatus] = useState(emptyConnectionStatus);
  const [handCommand, setHandCommand] = useState(null);
  const [landmarks, setLandmarks] = useState(null);
  const [landmarksUpdatedAt, setLandmarksUpdatedAt] = useState(null);
  const [motorStatus, setMotorStatus] = useState(null);
  const [bridgeConnected, setBridgeConnected] = useState(false);
  const [pendingMode, setPendingMode] = useState(null);
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

  // ── 전송 ──

  const sendRequest = useCallback((type, payload = {}) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setLastError({ code: "NOT_CONNECTED", message: "서버에 연결되어 있지 않습니다." });
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
    return requestId;
  }, []);

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
      sendRequest(CLIENT_MESSAGE.SET_CONTROL_MODE, { mode: held, owner: WEB_OWNER });
    }, TIMING.CONTROL_RENEW_PERIOD_MS);
  }, [sendRequest, stopRenewTimer]);

  // ── 수신 ──

  const applySnapshot = useCallback((snap) => {
    setSnapshotReceivedAt(Date.now());
    if (typeof snap.timestamp === "string") setSnapshotAt(snap.timestamp);
    // 6.4절: 새 snapshot 은 이전 snapshot 을 대체한다.
    if (snap.control_state) setControlState(snap.control_state);
    // 6.4절: 아직 유효 데이터를 받지 못한 객체는 {} 다. {} 는 truthy 이므로
    // 그대로 넣으면 safetyState.state 가 undefined 가 되어 상태 표시가 비어버린다.
    // "아직 모른다" 와 "INIT 을 관측했다" 를 구분해서 다룬다.
    if (snap.safety_state && Object.keys(snap.safety_state).length > 0) {
      setSafetyState(snap.safety_state);
      setSafetyStateKnown(true);
    } else {
      setSafetyStateKnown(false);
    }
    if (snap.recording_detail) setRecordingState(snap.recording_detail);
    if (snap.connection_status) setConnectionStatus(snap.connection_status);
    // snapshot 에 bridge_connected 가 없어도 잠기지 않는다.
    //
    // 3계층(브라우저 ↔ Django ↔ 브릿지 노드) 시절에는 "Django 는 살아 있지만
    // 브릿지 노드가 죽었다" 는 상태가 실재해 이 플래그가 필요했다. 2계층에서는
    // /ws/robot-state 로 snapshot 을 받았다는 것 자체가 브릿지가 살아 있다는
    // 뜻이다. Boolean(undefined) === false 로 두면 필드를 잊은 브릿지 구현이
    // 조작 UI 를 전부 잠근다("ROS 2 브릿지에 연결되어 있지 않아 요청을 전달할 수
    // 없습니다" 가 영구히 표시됨).
    //
    // "ROS 서비스가 아직 안 떴다" 는 connection_status.ros2·rpi 가 표현하므로
    // 역할이 겹치지 않는다.
    setBridgeConnected(snap.bridge_connected ?? true);
    setPendingMode(snap.pending?.mode ?? null);

    // 6.4절: 유효 데이터를 받지 못한 객체는 {} 다. 빈 객체는 표시하지 않는다.
    if (snap.landmarks && Object.keys(snap.landmarks).length > 0) {
      setLandmarks(snap.landmarks);
      setLandmarksUpdatedAt(Date.now());
    }
    if (snap.motor_state && Object.keys(snap.motor_state).length > 0) {
      setMotorStatus(snap.motor_state);
    }
    if (snap.last_hand_command && Object.keys(snap.last_hand_command).length > 0) {
      setHandCommand(snap.last_hand_command);
    }
  }, []);

  const applyAck = useCallback((ack) => {
    if (ack.accepted) {
      setModeRejectedReason("");
      setLastError(null);
      return;
    }
    const message = describeReason(ack.reason);
    setModeRejectedReason(message);
    // code 는 FR-37 사유 원문, message 는 사용자 문구
    setLastError({ code: ack.reason, message });

    // 갱신이 거부되면 제어권을 잃은 것으로 보고 갱신을 멈춘다.
    // FR-27: 연결 복구만으로 제어가 재개된 것처럼 표시하지 않는다.
    if (ack.reason === REJECT_REASON.OWNER_CONFLICT
        || ack.reason === REJECT_REASON.SAFETY_NOT_READY) {
      heldModeRef.current = null;
      stopRenewTimer();
      if (hadControlRef.current) setNeedsResumeConfirmation(true);
    }
  }, [stopRenewTimer]);

  // ── 연결 ──

  useEffect(() => {
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
          return;
        }
        if (isSnapshot(message)) {
          applySnapshot(message);
        } else if (message.type === SERVER_MESSAGE.ACK) {
          applyAck(message);
        }
      };

      ws.onerror = () => {
        setLastError({ code: "SOCKET_ERROR", message: "서버 연결에 문제가 발생했습니다." });
      };

      ws.onclose = () => {
        wsRef.current = null;
        heldModeRef.current = null;
        stopRenewTimer();
        setConnectionState("closed");
        setBridgeConnected(false);
        // FR-24: 실제 값을 추정하거나 유지하지 않는다.
        setConnectionStatus(emptyConnectionStatus());
        if (!closedByUnmountRef.current) scheduleReconnect();
      };
    }

    function scheduleReconnect() {
      const attempt = reconnectAttemptRef.current;
      reconnectAttemptRef.current = attempt + 1;
      const delay = Math.min(
        RECONNECT_BASE_DELAY_MS * 2 ** attempt,
        RECONNECT_MAX_DELAY_MS,
      );
      reconnectTimerRef.current = setTimeout(connect, delay);
    }

    connect();

    return () => {
      closedByUnmountRef.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      stopRenewTimer();
      wsRef.current?.close();
    };
  }, [applyAck, applySnapshot, stopRenewTimer]);

  // ── 파생 상태 ──

  // FR-38: gesture 는 SafetyState=READY|RUN 에서만 수락된다.
  // 안전 상태를 아직 받지 못했으면 조작을 허용하지 않는다 (fail-closed).
  const isSafeToOperate = (
    safetyStateKnown && OPERATION_ALLOWED_STATES.includes(safetyState.state)
  );

  // FR-34: 유효 lease 일 때만 owner_alive 가 true 다.
  const webHasControl = (
    controlState.active_owner === CONTROL_OWNER.WEB && controlState.owner_alive === true
  );

  const isRecordingBusy = RECORDING_BUSY_STATES.includes(recordingState.state);

  // FR-35: reset_safety 는 SAFE·FAULT·ESTOP 에서만 쓴다.
  // 안전 상태를 아직 받지 못했으면 허용하지 않는다 (fail-closed).
  const canResetSafety = safetyStateKnown && RESET_ALLOWED_STATES.includes(safetyState.state);

  // FR-20 / FR-27: 8.1절 기준(confidence 0.70 미만도 미검출)으로 판정한다.
  const handDetection = handDetectionState(landmarks);
  // FR-27: hand-loss latch 는 서버(`normalize.py`)가 landmark 스트림에서 파생한다.
  // 웹은 그 값을 표시만 한다. 재검출만으로 제어가 재개된 것처럼 보이면 안 된다.
  const handLossLatched = Boolean(landmarks?.hand_loss_latched);
  const reacquireElapsedMs = landmarks?.reacquire_elapsed_ms ?? 0;
  const reacquireStableMs = landmarks?.reacquire_stable_ms ?? 0;

  // 제어권 보유 여부를 추적해 상실 시 안내한다 (FR-27)
  useEffect(() => {
    if (webHasControl) {
      hadControlRef.current = true;
      setNeedsResumeConfirmation(false);
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
    return sendRequest(CLIENT_MESSAGE.SET_CONTROL_MODE, {
      mode: nextMode,
      owner: WEB_OWNER,
    });
  }, [sendRequest]);

  /**
   * Gesture 를 보낸다.
   *
   * commandPresets.js 의 preset 객체를 그대로 받는다.
   *   { id, values, speed_limit, source, ... }
   * 화면이 이 형태로 호출하므로 시그니처를 바꾸지 않는다.
   * 개별 인수로 넘기는 형태도 허용해 호출부 선택지를 남긴다.
   */
  const sendGesture = useCallback((gestureOrId, valuesArg, speedArg) => {
    const isPreset = gestureOrId && typeof gestureOrId === "object";
    const gestureId = isPreset ? gestureOrId.id : gestureOrId;
    const values = isPreset ? gestureOrId.values : valuesArg;
    const speedLimit = isPreset ? gestureOrId.speed_limit : speedArg;

    // FR-35: 제어 재개 전에는 새 명령을 보내지 않는다.
    if (needsResumeConfirmation) {
      setLastError({
        code: "RESUME_REQUIRED",
        message: "제어가 재개되지 않았습니다. 모드를 다시 선택하세요.",
      });
      return false;
    }
    // FR-23: 서버·command_guard 가 다시 검증하지만 웹도 잘못된 값을 보내지 않는다.
    if (!isValidAxisValues(values)) {
      setLastError({
        code: "INVALID_COMMAND",
        message: "명령값이 허용 범위(0.0~1.0)를 벗어났습니다.",
      });
      return false;
    }
    if (!isValidSpeedLimit(speedLimit)) {
      setLastError({
        code: "INVALID_SPEED_LIMIT",
        message: "speed_limit 은 0.0 초과 1.0 이하여야 합니다.",
      });
      return false;
    }
    // ExecuteGesture.srv 의 요청 필드는 gesture_name 과 speed_limit 뿐이다.
    // 7축 목표는 FR-41 이 YAML 소관으로 두었으므로 웹이 보내지 않는다.
    return Boolean(sendRequest(CLIENT_MESSAGE.EXECUTE_GESTURE, {
      gesture_id: gestureId,
      speed_limit: speedLimit,
    }));
  }, [needsResumeConfirmation, sendRequest]);

  const sendSequence = useCallback((sequenceId, speedLimit = 0.5) => {
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
    });
  }, [sendRequest]);

  const sendStop = useCallback(() => {
    // FR-34: 누구나 내부망에서 안전 정지를 요청할 수 있다.
    // FR-31: STOP 과 새 동작이 동시에 발생하면 STOP 이 이긴다.
    heldModeRef.current = null;
    stopRenewTimer();
    return sendRequest(CLIENT_MESSAGE.STOP, { mode: STOP_MODE, owner: STOP_OWNER });
  }, [sendRequest, stopRenewTimer]);

  /**
   * FR-27 / FR-35: 재검출이나 연결 복구만으로 제어가 재개된 것처럼 표시하지 않는다.
   * 이 함수는 안내를 닫기만 하고 아무 명령도 보내지 않는다.
   * 제어 재개는 사용자가 mode 를 다시 선택해야 한다.
   */
  const resumeControl = useCallback(() => {
    setNeedsResumeConfirmation(false);
    setModeRejectedReason("");
  }, []);

  const startRecording = useCallback(
    () => sendRequest(CLIENT_MESSAGE.START_RECORDING),
    [sendRequest],
  );

  /**
   * FR-40: `StopRecording.srv` 의 요청 필드는 `uint64 session_id` 다.
   * 활성 세션 ID 를 명시하지 않으면 어떤 세션을 닫는지 확인할 수 없다.
   */
  const stopRecording = useCallback(() => {
    const sessionId = recordingState.active_session_id;
    if (!sessionId) {
      setLastError({
        code: "NO_ACTIVE_SESSION",
        message: "활성 기록 세션이 없습니다. 기록 상태를 다시 확인하세요.",
      });
      return false;
    }
    return sendRequest(CLIENT_MESSAGE.STOP_RECORDING, { session_id: sessionId });
  }, [recordingState.active_session_id, sendRequest]);

  /**
   * FR-40: `SetMimicResult.srv` 는 `uint64 session_id` 와 `uint8 result` 를 받는다.
   * 판정 대상은 정상 Stop 으로 닫힌 직전 세션이다 (FR-18).
   */
  const submitRecordingResult = useCallback((result) => {
    if (result !== RECORDING_RESULT.SUCCESS && result !== RECORDING_RESULT.FAILURE) {
      setLastError({ code: "INVALID_RESULT", message: "판정은 성공 또는 실패여야 합니다." });
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
    });
  }, [recordingState.last_session_id, sendRequest]);

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
    safetyState,
    safetyStateKnown,
    recordingState,
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
    lastError,
    modeRejectedReason,
    needsResumeConfirmation,
    isSafeToOperate,
    webHasControl,
    isRecordingBusy,
    // 아래 셋은 새로 노출한다. 기존 값은 그대로 유지된다.
    bridgeConnected,
    pendingMode,
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
    connectionState, controlState, safetyState, safetyStateKnown, recordingState,
    handCommand, handDetection, snapshotAt, snapshotReceivedAt, handLossLatched, reacquireElapsedMs, reacquireStableMs,
    landmarks, landmarksUpdatedAt, motorStatus, connectionStatus, lastError,
    modeRejectedReason, needsResumeConfirmation, isSafeToOperate, webHasControl,
    isRecordingBusy, bridgeConnected, pendingMode, canResetSafety,
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

export function useHandSocket() {
  const context = useContext(HandSocketContext);
  if (!context) {
    throw new Error("useHandSocket 은 HandSocketProvider 안에서만 사용할 수 있습니다.");
  }
  return context;
}
