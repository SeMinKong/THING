// FR-27: 웹 오류 및 안전 안내
// 손 검출 실패/카메라 연결 실패/ROS2 통신 단절/RPi 연결 실패/모터 통신 오류/
// timeout/과전류/과온/비상정지 등을 사용자가 이해할 수 있는 문구로 안내한다.
import { useHandSocket } from "../context/HandSocketContext";
import { HAND_DETECTION, RESET_ALLOWED_STATES } from "../config/messageProtocol";

function buildMessages({
  connectionState, connectionStatus, safetyState, safetyStateKnown, handDetection, mode,
}) {
  const messages = [];

  if (connectionState !== "open") {
    messages.push({ level: "danger", text: "웹 서버(ROS2 브릿지) 연결이 끊어졌습니다." });
  }
  if (connectionState === "open" && !connectionStatus.ros2) {
    messages.push({ level: "danger", text: "ROS 2 통신이 단절되었습니다." });
  }
  if (connectionState === "open" && !connectionStatus.jetson) {
    messages.push({ level: "warning", text: "Jetson과의 연결 상태를 확인할 수 없습니다." });
  }
  if (connectionState === "open" && !connectionStatus.rpi) {
    messages.push({ level: "warning", text: "Raspberry Pi와의 연결 상태를 확인할 수 없습니다." });
  }
  if (connectionState === "open" && !connectionStatus.camera) {
    messages.push({ level: "warning", text: "카메라 연결이 끊어졌습니다." });
  }
  if (connectionState === "open" && !connectionStatus.motor) {
    messages.push({ level: "warning", text: "모터 연결 상태를 확인할 수 없습니다." });
  }
  if (mode === "MIMIC" && connectionStatus.camera
      && (handDetection === HAND_DETECTION.NOT_DETECTED
          || handDetection === HAND_DETECTION.LOW_CONFIDENCE)) {
    // FR-27: 재검출만으로 제어가 재개된 것처럼 표시하지 않는다.
    // hand-loss latch 와 300ms 재검출 진행 상태는 Web Bridge 가 파생해 내려주기로
    // 되어 있으나(FR-27) 아직 계약이 확정되지 않았다. 확정 전까지는 "제어가
    // 자동으로 재개되지 않는다" 는 사실만 명시한다.
    messages.push({
      level: "warning",
      text: handDetection === HAND_DETECTION.LOW_CONFIDENCE
        ? "손 인식 신뢰도가 기준(70%)에 미달합니다. 미검출로 처리되어 명령 발행이 중단됩니다."
        : "손이 검출되지 않았습니다. 카메라 앞에 손을 위치시켜 주세요.",
    });
    messages.push({
      level: "warning",
      text: "손을 다시 인식해도 제어는 자동으로 재개되지 않습니다. "
        + "정지(STOP) 후 모방 모드와 제어권을 다시 획득해야 합니다.",
    });
  }

  // 6.4절 {} 규칙: SafetyState 를 아직 받지 못했으면 기본값은 관측값이 아니다.
  // 이 구간에서 개별 플래그를 읽으면 motor_communication_ok=false 때문에
  // "모터 통신 오류" 같은 허위 경고가 접속 직후 항상 떴다.
  if (!safetyStateKnown) {
    messages.push({
      level: "warning",
      text: "안전 상태(SafetyState)를 아직 수신하지 못했습니다. 로봇 상태를 판단할 수 없습니다.",
    });
    return messages;
  }

  if (safetyState.command_timeout) {
    messages.push({ level: "warning", text: "명령 수신 timeout이 발생했습니다." });
  }
  if (safetyState.over_current) {
    messages.push({ level: "danger", text: "모터 과전류가 감지되었습니다." });
  }
  if (safetyState.over_temperature) {
    messages.push({ level: "danger", text: "모터 과온이 감지되었습니다." });
  }
  if (safetyState.motor_communication_ok === false) {
    messages.push({ level: "danger", text: "모터 통신 오류가 발생했습니다." });
  }
  
  if (safetyState.estop_active) {
    messages.push({
      level: "danger",
      text: "비상정지가 작동했습니다. 물리 E-Stop 을 해제하고 "
        + "안정 시간(500ms) 뒤 Safety Reset 이 필요합니다.",
    });
  }

  // FR-27, FR-35 복구 절차 상세 안내
  if (safetyState.state === "HOLD") {
    messages.push({
      level: "warning",
      text: "HOLD 상태입니다. 제어를 재개하려면 명시적인 [정지(STOP)] 요청을 통해 READY 상태로 복귀한 후 권한을 다시 획득해야 합니다.",
      actionNeeded: "STOP",
    });
  }

  if (RESET_ALLOWED_STATES.includes(safetyState.state)) {
    let reasonText = safetyState.reason || (safetyState.state === "SAFE" ? "안전 자세 진입 (장시간 명령 단절)" : "");
    messages.push({
      level: "danger",
      text: `${safetyState.state} 상태입니다. 원인(${reasonText})을 해소하고 안정 시간이 지난 뒤 [Safety Reset]을 수행하여 시스템을 초기화(INIT 재검사)해야 합니다.`,
      actionNeeded: "RESET",
    });
  }

  return messages;
}

export default function SafetyBanner() {
  const {
    connectionState, connectionStatus, safetyState, safetyStateKnown, handDetection,
    controlState, lastError, sendStop, resetSafety,
  } = useHandSocket();

  const messages = buildMessages({
    connectionState,
    connectionStatus,
    safetyState,
    safetyStateKnown,
    handDetection,
    mode: controlState.active_mode,
  });

  if (messages.length === 0 && !lastError) return null;

  return (
    <div className="container-fluid px-4 pt-3">
      {messages.map((m, i) => (
        <div key={i} className={`alert alert-${m.level} py-2 mb-2 d-flex justify-content-between align-items-center`} role="alert">
          <span>{m.text}</span>
          {m.actionNeeded === "STOP" && (
             <button className="btn btn-sm btn-outline-dark fw-bold" onClick={sendStop}>정지(STOP)</button>
          )}
          {m.actionNeeded === "RESET" && (
             <button className="btn btn-sm btn-danger fw-bold" onClick={resetSafety}>Safety Reset</button>
          )}
        </div>
      ))}
      {lastError && (
        <div className="alert alert-danger py-2 mb-2" role="alert">
          [{lastError.code}] {lastError.message}
        </div>
      )}
    </div>
  );
}