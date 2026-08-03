// ============================================================================
// 명시적 mode·owner 획득 패널
// ----------------------------------------------------------------------------
// 이 컴포넌트가 존재하는 이유
//
// 이전 구현은 페이지가 마운트되거나 WebSocket 이 다시 열리면 useEffect 가 스스로
// sendStop() → selectMode() 를 실행해 제어권을 잡았다. 명세는 이를 금지한다.
//
//   FR-19  "MIMIC↔MANUAL 직접 전환은 금지한다. 먼저 MODE_DISABLED, OWNER_NONE 으로
//          STOP 한 뒤 READY 에서 새 mode·owner 를 획득한다."
//          "hand-loss latch 뒤 MIMIC 재개도 DISABLED→MIMIC 의 명시적 두 단계로 수행한다."
//   FR-35  5·8단계 "사용자가 MIMIC mode 와 owner 를 새로 획득한다."
//   NFR-15 "재연결은 이전 명령을 재생하지 않는다."
//   NFR-23 "어느 경로에서도 이전 command, Gesture, Sequence 및 recording 을
//          자동 재개하지 않고 새 활성화 이후의 신선한 HandCommand 만 실행해야 한다."
//
// 자동 획득은 8.3절 검수 4번("이전 명령·토크·녹화 자동 재개 0건")을 통과할 수 없고,
// _handle_stop 이 의도적으로 상태 조건을 걸지 않기 때문에(FR-34 "누구나 내부망에서
// 안전 정지를 요청할 수 있으나") 페이지를 여는 것만으로 LOCAL owner(teleop)의
// 제어권을 빼앗을 수도 있었다.
//
// 따라서 두 단계를 화면에 그대로 노출하고, 각 단계를 사용자가 누르게 한다.
// ============================================================================
import { useHandSocket } from "../context/HandSocketContext";
import {
  ACQUIRE_ALLOWED_STATES,
  CONTROL_MODE,
  CONTROL_OWNER,
  RECORDING_BUSY_STATES,
} from "../config/messageProtocol";

const MODE_LABEL = {
  [CONTROL_MODE.MIMIC]: "모방(MIMIC)",
  [CONTROL_MODE.MANUAL]: "조작(MANUAL)",
};

export default function ModeAcquirePanel({ targetMode }) {
  const {
    connectionState,
    controlState,
    safetyState,
    safetyStateKnown,
    recordingState,
    pendingMode,
    bridgeConnected,
    webHasControl,
    selectMode,
    sendStop,
  } = useHandSocket();

  const label = MODE_LABEL[targetMode] ?? targetMode;
  const activeMode = controlState.active_mode;

  // 이 모드를 이미 보유하고 있으면 아무것도 그리지 않는다.
  if (activeMode === targetMode && webHasControl) return null;

  const isConnected = connectionState === "open";
  const isDisabled = activeMode === CONTROL_MODE.DISABLED;
  const otherOwner = (
    controlState.active_owner !== CONTROL_OWNER.NONE
    && controlState.active_owner !== CONTROL_OWNER.WEB
  );
  // FR-34: 획득은 READY 에서만 가능하다.
  const safetyReady = safetyStateKnown && ACQUIRE_ALLOWED_STATES.includes(safetyState.state);
  const recordingBusy = RECORDING_BUSY_STATES.includes(recordingState.state);

  const canStop = isConnected && bridgeConnected && !isDisabled;
  const canAcquire = (
    isConnected && bridgeConnected && isDisabled && safetyReady
    && !otherOwner && !recordingBusy
  );

  return (
    <div className="alert alert-secondary" role="region" aria-label={`${label} 모드 획득`}>
      <p className="fw-semibold mb-2">
        {label} 모드를 사용하려면 제어권을 직접 획득해야 합니다.
      </p>
      <p className="small text-muted mb-3">
        모방↔조작은 직접 전환할 수 없습니다. <strong>① 정지(STOP)로 비활성화</strong> →
        {" "}<strong>② {label} 획득</strong> 두 단계를 순서대로 수행하십시오.
        재연결·복구 뒤에도 이전 제어권은 자동으로 돌아오지 않습니다.
      </p>

      <ol className="mb-3 small">
        <li className={isDisabled ? "text-success fw-semibold" : ""}>
          현재 모드: <strong>{activeMode}</strong> / 제어권:{" "}
          <strong>{controlState.active_owner}</strong>
          {isDisabled ? " — 비활성화 완료" : " — 먼저 정지가 필요합니다"}
        </li>
        <li className={safetyReady ? "text-success fw-semibold" : ""}>
          안전 상태:{" "}
          <strong>{safetyStateKnown ? safetyState.state : "수신 대기"}</strong>
          {safetyReady ? " — 획득 가능" : " — READY 여야 획득할 수 있습니다"}
        </li>
      </ol>

      {otherOwner && (
        <p className="small text-danger mb-2">
          현재 제어권은 <strong>{controlState.active_owner}</strong> 이(가) 보유하고 있습니다.
          해당 주체가 해제한 뒤 획득할 수 있습니다.
        </p>
      )}
      {recordingBusy && (
        <p className="small text-danger mb-2">
          기록이 진행 중입니다. 기록을 종료·판정한 뒤 모드를 변경할 수 있습니다.
        </p>
      )}
      {!bridgeConnected && isConnected && (
        <p className="small text-danger mb-2">
          ROS 2 브릿지에 연결되어 있지 않아 요청을 전달할 수 없습니다.
        </p>
      )}
      {pendingMode && (
        <p className="small text-muted mb-2">
          요청한 모드(<strong>{pendingMode}</strong>)의 확정을 기다리고 있습니다.
          확정은 <code>/thing/control_state</code> 로만 판단합니다.
        </p>
      )}

      <div className="d-flex gap-2 flex-wrap">
        <button
          type="button"
          className="btn btn-outline-dark fw-semibold"
          onClick={sendStop}
          disabled={!canStop}
        >
          ① 정지(STOP)
        </button>
        <button
          type="button"
          className="btn btn-primary fw-semibold"
          onClick={() => selectMode(targetMode)}
          disabled={!canAcquire}
        >
          ② {label} 획득
        </button>
      </div>
    </div>
  );
}
