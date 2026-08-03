// ============================================================================
// 모방(MIMIC) 모드 페이지
// ----------------------------------------------------------------------------
// FR-20: 카메라 영상 + landmark overlay 표시
// FR-21: 손동작 정보(7논리축, 검출 신뢰도, 명령 발행 상태) 표시
// FR-26: 데이터 기록(rosbag2) 시작/종료/성공-실패 판정
//
// 참고: 카메라와 Vision Node(MediaPipe)는 시스템 실행 중 항상 동작하며,
// 이 페이지의 "녹화" 버튼은 카메라 자체를 켜고 끄는 것이 아니라 rosbag2
// 기록만 시작/종료한다(1.3, UC-05). 영상 원본/overlay 표시, 카메라 상태·손
// 검출 여부, 스트림 오류·프레임 정지 감지는 OrderMode와 공통으로 쓰는
// ../components/CameraStream 에 위임한다.
// ============================================================================
import { useState } from "react";
import { useHandSocket } from "../context/HandSocketContext";
import {
  HAND_AXES,
  HAND_DETECTION,
  TIMING,
  CONTROL_MODE,
  RECORDING_STATE,
  RECORDING_RESULT,
  isDeviceUsable,
} from "../config/messageProtocol";
import CameraStream from "../components/CameraStream";
import ModeAcquirePanel from "../components/ModeAcquirePanel";

/** 7축 표시. 값이 없으면 "-" 를 낸다 (FR-24: 가짜 값으로 채우지 않는다). */
function formatAxis(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(2)
    : "-";
}

export default function VisionMode() {
  const {
    connectionState,
    connectionStatus,
    controlState,
    handDetection,
    handLossLatched,
    reacquireElapsedMs,
    reacquireStableMs,
    handCommand,
    recordingState,
    webHasControl,
    startRecording,
    stopRecording,
    submitRecordingResult,
  } = useHandSocket();
  const [showRealtimeData, setShowRealtimeData] = useState(false);

  const isMimicActive = controlState.active_mode === CONTROL_MODE.MIMIC && webHasControl;
  // "아직 모름" 을 단절로 단정하지 않는다 (FR-24).
  const isCameraConnected = isDeviceUsable(connectionStatus.camera)
    && connectionState === "open";
  const handDetected = handDetection === HAND_DETECTION.DETECTED;

  // ROS2 브릿지가 아직 연결되지 않았거나 값을 아직 못 받았으면 null.
  // 임의의 값(0 등)으로 채우지 않고 "데이터 없음" 상태 그대로 보여준다.
  // 서버가 7축을 채워 내려주되, 브릿지가 일부만 보냈으면 그 축은 null 이다.
  // 값이 하나도 없으면 "아직 못 받았다" 로 본다.
  // FR-21: HandCommand.confidence. 명령이 어느 정도 신뢰도로 만들어졌는지.
  const commandConfidence = typeof handCommand?.confidence === "number"
    && Number.isFinite(handCommand.confidence) ? handCommand.confidence : null;
  const rawAxisValues = handCommand?.values ?? null;
  const hasAnyAxis = rawAxisValues
    && HAND_AXES.some((axis) => typeof rawAxisValues[axis.key] === "number");
  const axisValues = hasAnyAxis ? rawAxisValues : null;
  // `normalize.py` 가 stamp 를 RFC 3339 UTC 문자열로 통일해 내려준다.
  // HandCommand.msg 의 builtin_interfaces/Time, epoch 초, 문자열 어느 쪽으로
  // 오더라도 서버 경계에서 한 가지 형태가 된다. 여기서 산술하지 않는다.
  const stampText = typeof handCommand?.stamp === "string"
    ? handCommand.stamp.replace("T", " ").replace("Z", "")
    : "-";

  // FR-26 녹화 버튼은 MIMIC 모드에서만 활성화
  const canStartRecording =
    isMimicActive &&
    connectionState === "open" &&
    recordingState.state === RECORDING_STATE.IDLE &&
    !recordingState.result_pending;
  const canStopRecording = isMimicActive && recordingState.state === RECORDING_STATE.RECORDING;
  const isRecordingActive = [RECORDING_STATE.STARTING, RECORDING_STATE.RECORDING, RECORDING_STATE.STOPPING].includes(
    recordingState.state
  );

  // FR-26: 녹화 종료 후 성공/실패 판정이 완료되기 전에는 다음 녹화를 시작할 수 없다.
  const needsResultJudgement = recordingState.result_pending;

  return (
    <div className="container my-5" style={{ maxWidth: 800 }}>
      {/* FR-19 / FR-35 / NFR-23: 제어권은 사용자가 직접 획득한다. 자동 획득하지 않는다. */}
      <ModeAcquirePanel targetMode={CONTROL_MODE.MIMIC} />

      {/* FR-20: 카메라 영상(원본/overlay 선택, 오류·정지 프레임 감지) + 손 검출 여부 - 두 모드 공통 컴포넌트 */}
      <div className="mb-4">
        <CameraStream />
      </div>

      {/* FR-26: 데이터 기록 시작/종료 제어 */}
      <div className="text-center mb-3">
        <div className="d-flex justify-content-center gap-2 flex-wrap">
          <button
            className="btn btn-success btn-lg px-4 py-2 fw-bold shadow-sm"
            onClick={startRecording}
            disabled={!canStartRecording}
          >
            녹화 시작
          </button>
          <button
            className="btn btn-warning btn-lg px-4 py-2 fw-bold shadow-sm"
            onClick={stopRecording}
            disabled={!canStopRecording}
          >
            녹화 정지
          </button>
        </div>
        {isRecordingActive && (
          <p className="mt-2 mb-0 text-danger fw-semibold">
            {/* 6.5절: Session ID 는 10진 문자열. 63-bit 값이라 숫자로 다루면
                브라우저에서 정밀도가 손상된다. */}
            ● {recordingState.state} (Session ID: {recordingState.active_session_id || "-"})
          </p>
        )}
        {!isMimicActive && (
          <p className="mt-2 mb-0 small text-muted">
            녹화는 모방(MIMIC) 모드에서 웹이 제어권을 보유한 동안에만 가능합니다.
          </p>
        )}
      </div>

      {/* FR-26: 녹화 종료 후 성공/실패 판정 - 판정 전까지 다음 녹화 시작 불가 */}
      {needsResultJudgement && (
        <div className="alert alert-info text-center mb-4" role="alert">
          <p className="fw-semibold mb-2">
            방금 종료한 세션(ID: {recordingState.last_session_id})의 모방 성공 여부를 판정해 주세요.
          </p>
          <div className="d-flex justify-content-center gap-2">
            <button
              className="btn btn-success"
              onClick={() => submitRecordingResult(RECORDING_RESULT.SUCCESS)}
            >
              성공
            </button>
            <button
              className="btn btn-danger"
              onClick={() => submitRecordingResult(RECORDING_RESULT.FAILURE)}
            >
              실패
            </button>
          </div>
        </div>
      )}

      {/* 실시간 데이터 토글 섹션 (FR-21: 기본 화면에는 직관적 데이터만, 세부는 토글) */}
      <div className="border-top pt-4">
        <div className="form-check form-switch d-flex align-items-center justify-content-between p-3 bg-light rounded-3 mb-3">
          <label className="form-check-label fw-bold fs-5 text-dark m-0" htmlFor="dataToggle">
            실시간 로봇 손 데이터 출력 (7논리축)
          </label>
          <input
            className="form-check-input ms-3"
            type="checkbox"
            id="dataToggle"
            style={{ width: "2.5em", height: "1.25em" }}
            checked={showRealtimeData}
            onChange={(e) => setShowRealtimeData(e.target.checked)}
          />
        </div>

        {/* FR-20: "손 검출 여부와 hand-loss·재개 필요 상태를 표시"
            FR-27: "손 미검출과 유효 재검출 300ms를 표시"
            latch 는 서버(`normalize.py`)가 landmark 스트림에서 파생한다. */}
        {handLossLatched && (
          <div className="alert alert-warning py-2 mb-3" role="alert">
            <p className="fw-semibold mb-1">
              손 인식이 끊겨 명령 발행이 중단되었습니다 (hand-loss).
            </p>
            <p className="small mb-2">
              손을 다시 인식해도 제어는 자동으로 재개되지 않습니다.
              정지(STOP) 후 모방 모드와 제어권을 다시 획득해야 합니다.
            </p>
            <div className="d-flex align-items-center gap-2">
              <div className="progress flex-grow-1" style={{ height: "0.5rem" }}>
                <div
                  className="progress-bar bg-warning"
                  role="progressbar"
                  style={{
                    width: `${reacquireStableMs > 0
                      ? Math.round((reacquireElapsedMs / reacquireStableMs) * 100)
                      : 0}%`,
                  }}
                  aria-valuenow={reacquireElapsedMs}
                  aria-valuemin={0}
                  aria-valuemax={reacquireStableMs}
                />
              </div>
              <span className="small text-muted text-nowrap">
                유효 재검출 {reacquireElapsedMs} / {reacquireStableMs}ms
              </span>
            </div>
          </div>
        )}

        {showRealtimeData && !axisValues && (
          <p className="text-center text-muted small mb-2">
            ROS2 브릿지로부터 아직 데이터를 받지 못했습니다.
          </p>
        )}

        {showRealtimeData && (
          <div className="table-responsive bg-white border rounded-3 p-2 shadow-sm">
            <table className="table table-sm table-borderless align-middle text-center m-0">
              <thead className="table-light">
                <tr className="small text-uppercase fw-bold text-muted border-bottom">
                  <th>Timestamp</th>
                  {HAND_AXES.map((axis) => (
                    <th key={axis.key}>{axis.label}</th>
                  ))}
                  {/* FR-21: "7논리축 목표와 confidence를 숫자 또는 게이지로 표시한다." */}
                  <th>Conf.</th>
                  <th>Camera</th>
                  <th>Hand</th>
                </tr>
              </thead>
              <tbody className="font-monospace fs-6">
                <tr>
                  <td className="text-muted small">{stampText}</td>
                  {HAND_AXES.map((axis) => (
                    <td className="fw-bold" key={axis.key}>
                      {formatAxis(axisValues?.[axis.key])}
                    </td>
                  ))}
                  <td className={commandConfidence !== null
                    && commandConfidence < TIMING.HAND_CONFIDENCE_MIN
                    ? "text-warning fw-bold" : "fw-bold"}>
                    {commandConfidence !== null
                      ? `${Math.round(commandConfidence * 100)}%` : "-"}
                  </td>
                  <td>
                    <span
                      className={`badge ${
                        isCameraConnected ? "bg-primary-subtle text-primary" : "bg-danger-subtle text-danger"
                      }`}
                    >
                      {isCameraConnected ? "True" : "False"}
                    </span>
                  </td>
                  <td>
                    <span
                      className={`badge ${
                        handDetected ? "bg-info-subtle text-info" : "bg-secondary-subtle text-secondary"
                      }`}
                    >
                      {handDetected ? "True" : "False"}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
            <p className="small text-muted mt-2 mb-0">
              명령 발행 상태(source): {handCommand?.source ?? "-"} / sequence: {handCommand?.sequence ?? "-"}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
