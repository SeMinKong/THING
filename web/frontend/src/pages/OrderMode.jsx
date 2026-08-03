// ============================================================================
// 조작(MANUAL) 모드 페이지
// ----------------------------------------------------------------------------
// FR-19/FR-20: VisionMode와 동일한 CameraStream 컴포넌트로 영상·손 검출·카메라
//   상태를 조작 모드에서도 관제할 수 있도록 함 (두 모드 모두 관제 가능해야 함)
// FR-22: 버튼 입력을 통한 명령 전달 (기본 명령 = Gesture, 추가 명령 = Sequence)
// FR-23: 웹 명령 범위 제한 (정지 명령 최우선, 큐잉 금지)
// FR-25: 모터 상태 확인
// FR-27: 위험 상태에서 새 명령 비활성화
// ============================================================================
import { useEffect, useRef, useState } from "react";
import { useHandSocket } from "../context/HandSocketContext";
import { BASIC_GESTURES, SEQUENCE_ACTIONS } from "../config/commandPresets";
import { CONTROL_MODE, CONTROL_OWNER } from "../config/messageProtocol";
import MotorStatusPanel from "../components/MotorStatusPanel";
import ModeAcquirePanel from "../components/ModeAcquirePanel";
import CameraStream from "../components/CameraStream";
import "./OrderMode.css";

export default function OrderMode() {
  const {
    connectionState,
    controlState,
    lastError,
    safetyState,
    safetyStateKnown,
    motorStatus,
    snapshotAt,
    snapshotReceivedAt,
    modeRejectedReason,
    needsResumeConfirmation,
    isSafeToOperate,
    webHasControl,
    sendGesture,
    sendSequence,
    sendStop,
  } = useHandSocket();
  // FR-22: 현재 명령이 실행 중이면 다음 명령은 큐잉하지 않고 무시한다.
  const [pendingCommandId, setPendingCommandId] = useState(null);

  // FR-22 "같은 시점에 Gesture 하나만 실행하고 새 일반 동작은 큐에 쌓지 않고
  // 거부한다." 잠금은 임의 타이머가 아니라 로봇이 알려주는 사실로 푼다.
  //   · 거부되면(lastError) 애초에 실행되지 않았으므로 즉시 해제
  //   · 수락되면 control_state.sequence_running 이 서고, 초기 유지시간이 끝나
  //     내려갈 때 해제한다 (FR-22: open·fist 1000ms, 그 외 3000ms)
  const sequenceRunning = controlState.sequence_running;
  const wasRunningRef = useRef(false);
  useEffect(() => {
    if (sequenceRunning) wasRunningRef.current = true;
    if (pendingCommandId === null) return;
    if (lastError || (wasRunningRef.current && !sequenceRunning)) {
      wasRunningRef.current = false;
      setPendingCommandId(null);
    }
  }, [pendingCommandId, lastError, sequenceRunning]);

  const isManualActive = controlState.active_mode === CONTROL_MODE.MANUAL;

  const isConnected = connectionState === "open";

  const commandsDisabled =
    !isManualActive ||
    !isConnected ||
    !isSafeToOperate ||
    !webHasControl ||
    needsResumeConfirmation ||
    controlState.sequence_running ||
    pendingCommandId !== null;

  const runGesture = (gesture) => {
    if (commandsDisabled) return;
    setPendingCommandId(gesture.id);
    const ok = sendGesture(gesture);
    if (!ok) setPendingCommandId(null);
    // TODO(ROS2 확정 후): 실제로는 브릿지가 보내는 완료/ack 신호(control_state
    // 또는 별도 gesture 완료 메시지)로 해제하는 것이 정확하다. 지금은 프론트
    // 단독 데모용으로 일정 시간 후 잠금을 해제한다.
    // 임의 타이머가 아니라 서버 ack 로 잠금을 푼다.
    // 500ms 고정이면 FR-22 의 초기 유지시간(open·fist 1000ms,
    // cylindrical_grasp·pinch 3000ms)보다 먼저 풀려 두 번째 요청이
    // motion_active 로 거부된다. 잠금 해제는 아래 useEffect 가 맡는다.
  };

  const runSequence = (sequenceId) => {
    if (commandsDisabled) return;
    sendSequence(sequenceId);
  };

  const handleStop = () => {
    // FR-23: 정지 명령은 다른 일반 명령보다 우선 처리 - 잠금 상태와 무관하게 항상 전송
    setPendingCommandId(null);
    sendStop();
  };

  return (
    <div className="container my-5 px-4">
      <h2 className="mb-4 fw-bold text-dark text-center">명령 제공 모드</h2>

      {/* FR-19 / FR-35 / NFR-23: 제어권은 사용자가 직접 획득한다. 자동 획득하지 않는다. */}
      <ModeAcquirePanel targetMode={CONTROL_MODE.MANUAL} />

      {modeRejectedReason && (
        <div className="alert alert-warning text-center" role="alert">
          {modeRejectedReason}
        </div>
      )}
      {!isConnected && (
        <div className="alert alert-danger text-center" role="alert">
          서버와 연결되어 있지 않아 명령을 보낼 수 없습니다.
        </div>
      )}
      {isConnected && !webHasControl && controlState.active_owner === CONTROL_OWNER.LOCAL && (
        <div className="alert alert-warning text-center" role="alert">
          로컬 프로그램(teleop)이 제어권을 보유하고 있어 웹에서 조작할 수 없습니다.
          해당 주체가 해제해야 합니다.
        </div>
      )}
      {needsResumeConfirmation && (
        <div className="alert alert-warning text-center" role="alert">
          제어권을 잃었습니다. 이전 명령은 자동으로 재개되지 않습니다(NFR-23).
          상단의 "제어 재개"로 안내를 닫고 아래에서 모드를 다시 획득하십시오.
        </div>
      )}
      {!isSafeToOperate && (
        <div className="alert alert-danger text-center" role="alert">
          {safetyStateKnown
            ? `현재 안전 상태(${safetyState.state})에서는 일반 조작 명령이 비활성화됩니다.`
            : "안전 상태를 아직 수신하지 못했습니다. 확인되기 전까지 조작 명령을 보내지 않습니다."}
        </div>
      )}

      <div className="row g-4 align-items-start">
        <div className="col-lg-7">
          {/* FR-19 인수조건: 두 모드 모두 영상/손 검출 관제 가능해야 함 - VisionMode와 동일한 공통 컴포넌트 사용 */}
          <CameraStream compact />

          <p className="text-center mt-2 mb-0 small text-muted">
            현재 안전 상태:{" "}
            <strong>{safetyStateKnown ? safetyState.state : "수신 대기"}</strong>
          </p>

          {/* FR-25: 모터 상태 - 조작 모드에서 명령 결과를 바로 확인할 수 있도록 배치 */}
          <div className="mt-4">
            <MotorStatusPanel
              motorStatus={motorStatus}
              snapshotAt={snapshotAt}
              receivedAt={snapshotReceivedAt}
            />
          </div>
        </div>

        <div className="col-lg-5 d-flex justify-content-lg-end">
          <div className="command-panel w-100">
            {/* FR-22 기본 명령 (Gesture) */}
            <div className="row row-cols-2 g-3 mb-3">
              {BASIC_GESTURES.map((gesture) => (
                <div className="col" key={gesture.id}>
                  <button
                    type="button"
                    className="btn btn-outline-secondary w-100 py-3 fw-semibold fs-5 rounded-3"
                    onClick={() => runGesture(gesture)}
                    disabled={commandsDisabled}
                    aria-label={gesture.label}
                    title={gesture.label}
                  >
                    {gesture.icon}
                    <span className="d-block fs-6 fw-normal mt-1">{gesture.label}</span>
                  </button>
                </div>
              ))}
            </div>

            {/* FR-22 추가 명령 (Sequence 액션) */}
            <div className="row row-cols-2 g-3 mb-4">
              {SEQUENCE_ACTIONS.map((action) => (
                <div className="col" key={action.id}>
                  <button
                    type="button"
                    className="btn btn-outline-info w-100 py-2 fw-semibold rounded-3"
                    onClick={() => runSequence(action.id)}
                    disabled={commandsDisabled}
                    title={action.label}
                  >
                    {action.icon} {action.label}
                  </button>
                </div>
              ))}
            </div>

            <div className="text-lg-end text-center">
              <button
                type="button"
                className="btn btn-danger btn-lg px-5 py-2 fw-bold shadow-sm w-100"
                style={{ maxWidth: 240 }}
                onClick={handleStop}
              >
                기능 중지
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
