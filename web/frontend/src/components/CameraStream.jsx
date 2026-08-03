// ============================================================================
// MIMIC/조작(MANUAL) 모드가 공통으로 사용하는 카메라 스트림 뷰어
// ----------------------------------------------------------------------------
// 요구사항: 원본 RGB / landmark overlay MJPEG 스트림, 두 모드 공통 카메라
// 상태·손 검출 여부 표시, 스트림 연결 오류와 카메라 미연결 상태 전달.
//
// 설계 메모:
// - 카메라와 Vision Node(MediaPipe)는 시스템 실행 중 항상 동작하므로(1.3),
//   이 컴포넌트는 현재 제어 모드(MIMIC/MANUAL)와 무관하게 항상 같은 방식으로
//   동작한다. 두 페이지에서 그대로 재사용한다.
// - "카메라 미연결"과 "스트림(HTTP) 연결 오류"는 서로 다른 실패 지점이므로
//   구분해서 표시한다.
//     · 카메라 미연결: ROS2 브릿지가 보내주는 connectionStatus.camera (하드웨어/
//       Jetson 쪽에서 본 카메라 상태)
//     · 스트림 연결 오류: 브라우저의 <img> 태그가 실제로 MJPEG 엔드포인트에
//       접속하는 데 실패했는지(onError/onLoad)를 별도로 추적한다. 카메라 자체는
//       정상이어도 URL 오류·방화벽 등으로 스트림만 못 받아오는 경우를 잡기 위함.
// - "프레임 갱신 상태(정지 화면 여부)"는 MJPEG 개별 프레임 도착을 <img> 태그
//   레벨에서 감지할 표준적인 방법이 없으므로, 카메라와 함께 항상 발행되는
//   HandLandmarks 수신 시각(landmarksUpdatedAt, FR-06 기준 20Hz 이상)을 대리
//   신호로 사용한다. 이 값이 일정 시간 갱신되지 않으면 정지 상태로 간주한다.
// - 영상은 모니터링 전용이며 이 컴포넌트는 캡처/저장 기능을 제공하지 않는다
//   (rosbag2 기록은 별도의 record_control 메시지로만 이루어진다).
// ============================================================================
import { useEffect, useState } from "react";
import { useHandSocket } from "../context/HandSocketContext";
import { HAND_DETECTION, TIMING, isDeviceUsable } from "../config/messageProtocol";

// TODO(Jetson IP/포트 확정 후 교체): thing_vision 노드가 원본/overlay 스트림을
// 각각 별도 MJPEG 엔드포인트로 제공한다고 가정한다. 하나의 엔드포인트만 운용한다면
// RAW 쪽 환경변수를 비워두면 되고, 그 경우 이 컴포넌트는 자동으로 overlay만 보여준다.
const OVERLAY_STREAM_URL = import.meta.env.VITE_MJPEG_STREAM_URL || "";
const RAW_STREAM_URL = import.meta.env.VITE_MJPEG_RAW_STREAM_URL || "";

// landmark 갱신이 이 시간(ms) 이상 없으면 "프레임이 갱신되지 않는다"고 간주.
// FR-06 인수조건(20Hz 이상 발행)을 기준으로 여유를 두고 잡은 값.
// ROS 상태 수신이 멈춘 것을 감지하는 임계값.
// 영상 정지 여부를 이 값으로 판단하지 않는다. landmark 발행은 hand-loss latch
// 로도 멈추므로(FR-35) 영상 정지의 대리 신호가 될 수 없다.
const ROS_STATE_STALE_THRESHOLD_MS = 1500;

export default function CameraStream({ compact = false }) {
  const {
    connectionState, connectionStatus, landmarks, landmarksUpdatedAt, handDetection,
  } = useHandSocket();

  // 원본/overlay 중 어떤 스트림을 볼지 선택 (완료조건: 선택해서 확인 가능해야 함)
  const [streamMode, setStreamMode] = useState("overlay"); // "overlay" | "raw"

  // 브라우저 <img> 레벨에서 실제로 스트림 로드가 실패했는지 추적.
  const [streamError, setStreamError] = useState(false);

  // 화면을 주기적으로 재렌더링해서 landmarksUpdatedAt 기준 "정지 여부"를 다시 계산한다.
  // (landmarksUpdatedAt 자체는 새 메시지가 안 오면 값이 바뀌지 않으므로, 시간 경과를
  // 반영하려면 별도의 tick이 필요하다.)
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  // 카메라 상태가 "아직 모름" 이어도 스트림은 띄운다.
  // 브릿지의 /thing/diagnostics 파생 규칙이 확정되지 않아 camera 가 계속
  // unknown 일 수 있는데, 그때 영상을 가리면 MJPEG 이 정상인데도 화면이 빈다.
  // 실제 실패는 <img> 의 onError 가 잡는다.
  const isCameraConnected = isDeviceUsable(connectionStatus.camera)
    && connectionState === "open";

  const activeUrl = streamMode === "raw" ? RAW_STREAM_URL : OVERLAY_STREAM_URL;
  const hasRaw = Boolean(RAW_STREAM_URL);

  // 스트림 소스가 바뀌면(원본<->overlay 전환) 이전 소스의 오류 상태를 초기화한다.
  useEffect(() => {
    setStreamError(false);
  }, [activeUrl]);

  const isFrameStale =
    isCameraConnected && landmarksUpdatedAt !== null
    && Date.now() - landmarksUpdatedAt > ROS_STATE_STALE_THRESHOLD_MS;

  const height = compact ? 220 : 360;

  return (
    <div>
      {/* 원본/overlay 선택 - RAW 스트림 주소가 설정된 경우에만 노출 */}
      {hasRaw && (
        <div className="d-flex justify-content-center gap-2 mb-2" role="group" aria-label="영상 소스 선택">
          <button
            type="button"
            className={`btn btn-sm ${streamMode === "overlay" ? "btn-dark" : "btn-outline-dark"}`}
            onClick={() => setStreamMode("overlay")}
          >
            landmark overlay
          </button>
          <button
            type="button"
            className={`btn btn-sm ${streamMode === "raw" ? "btn-dark" : "btn-outline-dark"}`}
            onClick={() => setStreamMode("raw")}
          >
            원본 영상
          </button>
        </div>
      )}

      <div
        className="bg-light border text-secondary d-flex align-items-center justify-content-center rounded-3 shadow-sm mx-auto overflow-hidden"
        style={{ width: "100%", maxWidth: compact ? 480 : 640, height }}
      >
        {!isCameraConnected ? (
          // 카메라(하드웨어/Jetson) 자체가 연결되지 않은 경우
          <p className="m-0 fw-medium text-danger">카메라 연결이 끊어졌습니다</p>
        ) : !activeUrl ? (
          <div className="text-center">
            <div className="spinner-grow spinner-grow-sm text-secondary mb-2" role="status"></div>
            <p className="m-0 fw-medium">
              카메라는 연결되었지만 {streamMode === "raw" ? "원본" : "overlay"} 스트림 주소가 아직 설정되지
              않았습니다.
            </p>
            <p className="m-0 small text-muted">
              (
              {streamMode === "raw" ? "VITE_MJPEG_RAW_STREAM_URL" : "VITE_MJPEG_STREAM_URL"}
              환경변수 설정 필요)
            </p>
          </div>
        ) : streamError ? (
          // 카메라는 연결되어 있지만 브라우저에서 실제 MJPEG 스트림 로드 자체가 실패한 경우
          <p className="m-0 fw-medium text-danger">
            영상 스트림 연결에 실패했습니다.
            <br />
            <span className="small fw-normal">주소를 확인하거나 잠시 후 다시 시도해 주세요.</span>
          </p>
        ) : (
          <img
            key={activeUrl}
            src={activeUrl}
            alt={streamMode === "raw" ? "원본 카메라 영상" : "손 landmark overlay 영상"}
            style={{ width: "100%", height: "100%", objectFit: "contain" }}
            onError={() => setStreamError(true)}
            onLoad={() => setStreamError(false)}
          />
        )}
      </div>

      <div className="text-center mt-2 d-flex justify-content-center gap-2 flex-wrap">
        {/* 8.1절: 미검출은 "confidence 0.70 미만 또는 미검출" 이다.
            detected 만 보면 ROS 가 이미 발행을 멈춘 프레임을 "검출됨" 으로
            표시하게 되므로 임계 미달을 따로 구분한다 (FR-20, FR-27). */}
        {isCameraConnected && handDetection === HAND_DETECTION.UNKNOWN && (
          <span className="badge bg-secondary">손 검출 정보 수신 대기</span>
        )}
        {isCameraConnected && handDetection === HAND_DETECTION.NOT_DETECTED && (
          <span className="badge bg-warning text-dark">손이 검출되지 않았습니다</span>
        )}
        {isCameraConnected && handDetection === HAND_DETECTION.LOW_CONFIDENCE && (
          <span className="badge bg-warning text-dark">
            신뢰도 부족 ({Math.round((landmarks?.confidence ?? 0) * 100)}% &lt;{" "}
            {Math.round(TIMING.HAND_CONFIDENCE_MIN * 100)}%) — 미검출로 처리됩니다
          </span>
        )}
        {isCameraConnected && handDetection === HAND_DETECTION.DETECTED && (
          <span className="badge bg-success">
            손 검출됨 (신뢰도 {Math.round((landmarks?.confidence ?? 0) * 100)}%)
          </span>
        )}

        {/* 완료조건: 프레임 갱신 상태(정지 화면 여부)를 알 수 있어야 한다 */}
        {isCameraConnected && !streamError && activeUrl && (
          <span className={`badge ${isFrameStale ? "bg-danger" : "bg-secondary-subtle text-secondary"}`}>
            {isFrameStale ? "영상이 갱신되지 않고 있습니다 (정지 화면 가능성)" : "실시간 갱신 중"}
          </span>
        )}
      </div>
    </div>
  );
}
