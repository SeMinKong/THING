// ============================================================================
// 영상 — MJPEG + 손 검출
// ----------------------------------------------------------------------------
// FR-20: 저장하지 않는 MJPEG 영상, 손 검출 여부, hand-loss·재개 필요 상태 표시.
//
// 검출 상태를 영상 밖 배지가 아니라 영상 안에 겹쳐 둔다. 운용자는 자기 손을
// 보고 있으므로, 검출이 끊긴 사실을 알려면 시선을 옮겨야 하는 위치에 두면
// 늦는다.
//
// 영상은 WebSocket 과 별개 경로(HTTP)다. 상태 데이터가 멀쩡해도 영상만 죽을 수
// 있고 그 반대도 가능하다. 빈 화면마다 원인과 조치를 적는다.
// ============================================================================
import { useEffect, useState } from "react";
import { useHandSocket } from "../context/HandSocketContext";
import { HAND_DETECTION, TIMING, isDeviceUsable } from "../config/messageProtocol";
import { THRESHOLD } from "../config/pending";
import { diag, OWNER } from "../config/diagnostics";
import { motion, AnimatePresence } from "motion/react";
import { Panel, Head, Body, Tag } from "../ui/Sheet";

// thing_vision 이 원본/overlay 를 별도 MJPEG 엔드포인트로 제공한다고 본다.
// 하나만 운용하면 RAW 쪽을 비워 두면 되고, 그러면 전환 버튼이 사라진다.
const OVERLAY_STREAM_URL = import.meta.env.VITE_MJPEG_STREAM_URL || "";
const RAW_STREAM_URL = import.meta.env.VITE_MJPEG_RAW_STREAM_URL || "";

export default function CameraStream({ showHandLoss = false }) {
  const {
    connectionState, connectionStatus, landmarksUpdatedAt, handDetection,handLossLatched, reacquireElapsedMs, reacquireStableMs,
  } = useHandSocket();

  const [streamMode, setStreamMode] = useState("overlay");

  // 어느 URL 에서 실패했는지 함께 들고 있으면 소스 전환 때 state 를 되돌릴
  // 필요가 없다. 렌더 중 비교만 하면 된다.
  const [failedUrl, setFailedUrl] = useState(null);

  // landmarksUpdatedAt 은 새 메시지가 안 오면 안 바뀐다. 얼마나 지났는지는
  // 시간이 흘러야 알 수 있으므로 주기적으로 계산해 state 에 넣는다.
  // 렌더 중에 Date.now() 를 읽으면 순수하지 않다.
  const [staleSince, setStaleSince] = useState(0);
  useEffect(() => {
    const id = setInterval(() => {
      setStaleSince(landmarksUpdatedAt === null ? 0 : Date.now() - landmarksUpdatedAt);
    }, THRESHOLD.RECHECK_PERIOD_MS);
    return () => clearInterval(id);
  }, [landmarksUpdatedAt]);

  const activeUrl = streamMode === "raw" ? RAW_STREAM_URL : OVERLAY_STREAM_URL;
  const hasRaw = Boolean(RAW_STREAM_URL);
  const streamError = failedUrl !== null && failedUrl === activeUrl;
  const isCameraConnected = isDeviceUsable(connectionStatus.camera);

  // 영상과 검출 정보는 서로 다른 경로로 온다.
  //   영상    MJPEG (HTTP)      — WebSocket 이 끊겨도 살아 있을 수 있다
  //   검출    WebSocket snapshot — 끊기면 마지막 값이 그대로 굳는다
  // 그래서 WS 가 끊겼을 때 영상은 계속 보여 주되, 검출 표시는 현재 값인 것처럼
  // 두지 않는다. 굳은 "손 검출됨 93%" 가 떠 있으면 손 추적이 살아 있다고 오해한다.
  // 표시를 아예 없애지도 않는다. 그러면 카메라 문제인지 상태 채널 문제인지
  // 구분할 수 없다. MotorStatusPanel 의 "연결 끊김 · 마지막 값" 과 같은 방식이다.
  const detectionLive = connectionState === "open";

  // landmark 발행은 hand-loss latch 로도 멈추므로(FR-35) 영상 정지의 확정
  // 신호가 아니다. "갱신 멈춤" 이라고만 적고 영상을 가리지는 않는다.
  const isFrameStale = isCameraConnected
    && landmarksUpdatedAt !== null
    && staleSince > THRESHOLD.CAMERA_STATE_STALE_MS;

  // const confidencePct = Math.round((landmarks?.confidence ?? 0) * 100);
  const detectTone = handDetection === HAND_DETECTION.DETECTED ? "ok"
    : handDetection === HAND_DETECTION.LOW_CONFIDENCE ? "weak"
      : handDetection === HAND_DETECTION.NOT_DETECTED ? "none" : "idle";
  const detectText = handDetection === HAND_DETECTION.DETECTED
    ? "손 검출됨"
    : handDetection === HAND_DETECTION.LOW_CONFIDENCE
      ? "신뢰도 부족 — 미검출로 처리됩니다"
      : handDetection === HAND_DETECTION.NOT_DETECTED
        ? "손이 검출되지 않았습니다"
        : "손 검출 정보 수신 대기";

  const dot = { ok: "bg-st-ready", weak: "bg-st-hold", none: "bg-st-fault",
    idle: "bg-ink-400" }[detectTone];

  return (
    // 영상은 남는 높이를 채우고, 그 높이에 맞춰 4:3 을 유지한다.
    // 이전에는 aspect-4/3 만 있어서 높이가 열 너비에서 결정됐다. 셸이
    // overflow-hidden 이므로 열보다 커지면 아래쪽이 그냥 잘렸다.
    <Panel className="flex min-h-0 flex-1 flex-col">
      <Head title="영상">
        <AnimatePresence>
          {isFrameStale && (
            <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                         exit={{ opacity: 0 }}>
              <Tag tone="warn">갱신 멈춤</Tag>
            </motion.span>
          )}
        </AnimatePresence>
        {hasRaw && (
          <div className="relative flex rounded-full bg-ink-200/60 p-0.5"
               role="group" aria-label="영상 소스">
            {[["overlay", "합성"], ["raw", "원본"]].map(([key, label]) => (
              <button
                key={key}
                type="button"
                aria-pressed={streamMode === key}
                onClick={() => setStreamMode(key)}
                className="relative px-3 py-0.5 text-xs font-medium"
              >
                {streamMode === key && (
                  <motion.span
                    layoutId="stream-toggle"
                    transition={{ type: "spring", stiffness: 480, damping: 38 }}
                    className="absolute inset-0 rounded-full bg-ink-900"
                  />
                )}
                <span className={`relative ${
                  streamMode === key ? "text-white" : "text-ink-600"}`}>
                  {label}
                </span>
              </button>
            ))}
          </div>
        )}
      </Head>

      <Body className="flex min-h-0 flex-1">
        <motion.div
          layoutId="viewport"
          transition={{ type: "spring", stiffness: 300, damping: 34 }}
          className="relative mx-auto grid aspect-4/3 max-h-full max-w-full
                     place-items-center overflow-hidden rounded-card bg-ink-900"
        >
          {!activeUrl ? (
            <div className="p-6 text-center">
              <p className="font-mono text-[11px] tracking-[0.14em] text-ink-400">
                스트림 주소가 설정되지 않았습니다
              </p>
              <p className="mt-2 text-[13px] text-ink-300">
                <code>.env.local</code> 에 <code>VITE_MJPEG_STREAM_URL</code> 을 넣으세요.
              </p>
            </div>
          ) : streamError ? (
            <div className="p-6 text-center">
              <p className="font-mono text-[11px] tracking-[0.14em] text-st-fault">
                영상을 불러오지 못했습니다
              </p>
              <p className="mt-2 text-[13px] text-ink-300">
                주소가 열리는지, <code>mjpeg_streamer</code> 가 떠 있는지 확인하세요.
              </p>
              <p className="mt-1 font-mono text-[11px] text-ink-500">{activeUrl}</p>
            </div>
          ) : !isCameraConnected ? (
            <div className="p-6 text-center">
              <p className="font-mono text-[11px] tracking-[0.14em] text-ink-400">
                카메라 연결이 끊어졌습니다
              </p>
              <p className="mt-2 text-[13px] text-ink-300">
                연결이 돌아오면 영상이 다시 나옵니다.
              </p>
            </div>
          ) : (
            <img
              key={activeUrl}
              src={activeUrl}
              alt="로봇 손 제어용 실시간 영상"
              className="size-full object-contain"
              style={{ transform: "scaleX(-1)" }}
              onError={() => {
                setFailedUrl(activeUrl);
                diag.error({
                  code: `MJPEG_LOAD_FAILED_${streamMode}`,
                  owner: OWNER.CONFIG,
                  what: "MJPEG 스트림을 불러오지 못했습니다",
                  why: "브라우저가 <img> 로드에 실패했습니다. WebSocket 과 별개 경로라 "
                    + "상태 데이터는 정상이어도 영상만 안 나올 수 있습니다.",
                  fix: "① 주소를 브라우저 주소창에 직접 넣어 열리는지 ② Jetson 의 "
                    + "mjpeg_streamer 가 떠 있는지 ③ 페이지가 https 인데 스트림이 http "
                    + "여서 혼합 콘텐츠로 막힌 것은 아닌지 확인하세요.",
                  ref: "FR-20 / FR-28",
                  detail: { 주소: activeUrl, 모드: streamMode },
                });
              }}
              onLoad={() => setFailedUrl(null)}
            />
          )}

          {/* 검출 상태는 영상 안에 겹친다. 밖에 두면 시선을 옮겨야 알 수 있다 */}
          {activeUrl && !streamError && isCameraConnected && (
            <div className="absolute bottom-3 left-3">
              <motion.span
                key={detectionLive ? detectTone : "stale"}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.16 }}
                className="flex items-center gap-2 rounded-full bg-ink-900/85 px-3 py-1
                           backdrop-blur"
              >
                <span
                  className={`size-1.5 rounded-full ${detectionLive ? dot : "bg-ink-400"}`}
                  aria-hidden="true"
                />
                <span className={`font-mono text-[11px] ${
                  detectionLive ? "text-white" : "text-ink-200"}`}>
                  {detectionLive ? detectText : "손 검출 정보 끊김 · 마지막 값"}
                </span>
              </motion.span>
            </div>
          )}
          {/* FR-27: hand-loss·유효 재검출 안내. 흐름에 두면 형제로서 카메라 높이를
     다퉈 검출/미검출 반복 시 카메라가 커졌다 작아졌다 하므로, 영상 안
     absolute 오버레이로 두어 카메라 크기를 고정한다. */}
 <AnimatePresence>
   {showHandLoss && handLossLatched && (
     <motion.div
       initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}
       exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.16 }}
       className="absolute inset-x-3 top-3 rounded-card bg-ink-900/85 px-3 py-2 backdrop-blur"
     >
       <div className="flex items-center gap-2">
         <span className="size-1.5 rounded-full bg-st-hold" aria-hidden="true" />
         <span className="text-[11px] font-semibold tracking-[0.12em] text-st-hold">재개 필요</span>
       </div>
       <p className="mt-1 text-[12px] leading-snug text-ink-200">
         손 미검출이 확정되어 명령 발행이 중단됐습니다.
         손을 다시 인식해도 제어는 자동으로 재개되지 않습니다.
       </p>
       {reacquireStableMs > 0 && (
         <div className="mt-2">
           <div className="mb-1 flex items-baseline justify-between">
             <span className="text-[10px] text-ink-300">유효 재검출</span>
             <span className="font-mono text-[10px] text-ink-200">{reacquireElapsedMs} / {reacquireStableMs}ms</span>
           </div>
           <div className="h-1 overflow-hidden rounded-full bg-ink-200">
             <motion.div className="h-full rounded-full bg-st-hold"
               animate={{ width: `${Math.min(100, (reacquireElapsedMs / reacquireStableMs) * 100)}%` }}
               transition={{ duration: 0.2, ease: "linear" }} />
           </div>
         </div>
       )}
     </motion.div>
   )}
 </AnimatePresence>
        </motion.div>
      </Body>
    </Panel>
  );
}
