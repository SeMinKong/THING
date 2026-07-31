// FR-24: 로봇 및 시스템 상태 모니터링 - 화면 상단에 항상 고정 표시.
import { useHandSocket } from "../context/HandSocketContext";
import {
  CONNECTION_STATE,
  CONTROL_MODE,
  CONTROL_OWNER,
  isDeviceDown,
  RECORDING_BUSY_STATES,
} from "../config/messageProtocol";

const MODE_LABEL = {
  [CONTROL_MODE.DISABLED]: "비활성화",
  [CONTROL_MODE.MIMIC]: "모방 모드",
  [CONTROL_MODE.MANUAL]: "조작 모드",
  [CONTROL_MODE.TELEOP]: "로컬 teleop 모드",
};

const OWNER_LABEL = {
  [CONTROL_OWNER.NONE]: "제어권 없음",
  [CONTROL_OWNER.WEB]: "웹",
  [CONTROL_OWNER.LOCAL]: "로컬 프로그램",
};

// 안전 상태별 배지 색상 - 정상/대기/경고/오류 구분 (FR-24 인수조건)
const SAFETY_BADGE = {
  INIT: "bg-secondary",
  READY: "bg-primary",
  RUN: "bg-success",
  HOLD: "bg-warning text-dark",
  SAFE: "bg-warning text-dark",
  FAULT: "bg-danger",
  ESTOP: "bg-danger",
};

const CONNECTION_BADGE = {
  open: { className: "bg-success", label: "연결됨" },
  connecting: { className: "bg-secondary", label: "연결 중" },
  reconnecting: { className: "bg-warning text-dark", label: "재연결 중" },
  closed: { className: "bg-danger", label: "연결 끊김" },
};

const SUBSYSTEM_LABEL = {
  jetson: "Jetson",
  rpi: "Raspberry Pi",
  ros2: "ROS 2",
  camera: "카메라",
  motor: "모터",
};

export default function StatusBar() {
  const {
    connectionState,
    controlState,
    safetyState,
    safetyStateKnown,
    recordingState,
    connectionStatus,
    lastError,
    needsResumeConfirmation,
    resumeControl,
  } = useHandSocket();

  const conn = CONNECTION_BADGE[connectionState] ?? CONNECTION_BADGE.closed;
  // 관측된 단절만 표시한다. "아직 모름" 을 끊김으로 단정하지 않는다 (FR-24).
  const downSubsystems = Object.entries(connectionStatus).filter(
    ([, state]) => isDeviceDown(state),
  );
  const unknownSubsystems = Object.entries(connectionStatus).filter(
    ([, state]) => state === CONNECTION_STATE.UNKNOWN,
  );

  return (
    <div className="border-bottom bg-white sticky-top">
      <div className="container-fluid py-2 px-4 d-flex flex-wrap align-items-center gap-3 small">
        <span className="fw-bold">
          현재 모드: {MODE_LABEL[controlState.active_mode] ?? controlState.active_mode}
        </span>

        <span className="text-muted">제어권: {OWNER_LABEL[controlState.active_owner] ?? controlState.active_owner}</span>

        <span className={`badge ${conn.className}`}>{conn.label}</span>

        {/* 6.4절 {} 규칙: 아직 SafetyState 를 받지 못한 상태와 INIT 을 구분한다. */}
        <span className={`badge ${
          safetyStateKnown ? (SAFETY_BADGE[safetyState.state] ?? "bg-secondary") : "bg-secondary"
        }`}>
          안전 상태: {safetyStateKnown ? safetyState.state : "수신 대기"}
        </span>

        {/* FR-13/FR-27: 비상정지 작동 시 명확하게 안내 */}
        {/* FR-24: "SafetyState와 RecordingState를 구분해 표시해야 한다."
            기록 UI 는 모방 화면에만 있지만 상태 자체는 어느 화면에서든 보여야 한다. */}
        {recordingState.state !== "IDLE" && (
          <span className={`badge ${
            RECORDING_BUSY_STATES.includes(recordingState.state)
              ? "bg-danger" : "bg-info-subtle text-info border"}`}>
            기록: {recordingState.state}
            {recordingState.active_session_id
              && ` (${recordingState.active_session_id})`}
          </span>
        )}
        {recordingState.result_pending && (
          <span className="badge bg-warning text-dark">판정 대기</span>
        )}

        {safetyStateKnown && safetyState.estop_active && (
          <span className="badge bg-danger">비상정지(E-STOP) 작동 중</span>
        )}

        {controlState.sequence_running && (
          <span className="badge bg-info-subtle text-info">시퀀스 실행 중</span>
        )}

        {/* command_manager/command_guard가 보낸 권위 있는 상태 전이·거부 사유.
            ControlState.msg.last_transition_reason에 대응 (이전에는 값만 저장되고
            화면에 표시되지 않던 필드). */}
        {controlState.last_transition_reason && (
          <span
            className="badge bg-secondary-subtle text-dark border"
            title="command_manager / command_guard가 보고한 마지막 상태 전이 사유"
          >
            제어 상태 사유: {controlState.last_transition_reason}
          </span>
        )}

        {connectionState === "open" && downSubsystems.length > 0 && (
          <span
            className="badge bg-warning text-dark"
            title={downSubsystems.map(([k]) => SUBSYSTEM_LABEL[k] ?? k).join(", ")}
          >
            연결 끊김: {downSubsystems.map(([k]) => SUBSYSTEM_LABEL[k] ?? k).join(", ")}
          </span>
        )}

        {/* FR-24: "아직 모름" 을 끊김으로 단정하지 않고 별도로 알린다.
            브릿지의 /thing/diagnostics 파생이 없으면 계속 미확인으로 남는다. */}
        {connectionState === "open" && unknownSubsystems.length > 0 && (
          <span
            className="badge bg-secondary"
            title={unknownSubsystems.map(([k]) => SUBSYSTEM_LABEL[k] ?? k).join(", ")}
          >
            상태 미확인: {unknownSubsystems.map(([k]) => SUBSYSTEM_LABEL[k] ?? k).join(", ")}
          </span>
        )}

        {lastError && (
          <span className="badge bg-danger" title={lastError.message}>
            오류: {lastError.code}
          </span>
        )}

        {needsResumeConfirmation && (
          <button
            type="button"
            className="btn btn-sm btn-warning fw-bold ms-auto"
            onClick={resumeControl}
          >
            제어 재개
          </button>
        )}
      </div>
    </div>
  );
}
