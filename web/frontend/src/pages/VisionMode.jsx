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
  HAND_AXIS_KEYS,
  CONTROL_MODE,
  RECORDING_STATE,
  RECORDING_RESULT,
  formatStamp,
} from "../config/messageProtocol";
import CameraStream from "../components/CameraStream";
import { motion, AnimatePresence } from "motion/react";
import { Panel, Head, Body, Tag } from "../ui/Sheet";

/** 7축 표시. 값이 없으면 "-" 를 낸다 (FR-24: 가짜 값으로 채우지 않는다). */
function formatAxis(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(2)
    : "-";
}

export default function VisionMode() {
  const {
    connectionState,
    controlState,
    handCommand,
    recordingState,
    recordingStateKnown,
    webHasControl,
    startRecording,
    stopRecording,
    submitRecordingResult,
  } = useHandSocket();
  // 기본이 펼치기다. 접는 것은 화면이 좁을 때를 위한 선택지다
  const [showRealtimeData, setShowRealtimeData] = useState(true);

  const isMimicActive = controlState.active_mode === CONTROL_MODE.MIMIC && webHasControl;
  // "아직 모름" 을 단절로 단정하지 않는다 (FR-24).

  // ROS2 브릿지가 아직 연결되지 않았거나 값을 아직 못 받았으면 null.
  // 임의의 값(0 등)으로 채우지 않고 "데이터 없음" 상태 그대로 보여준다.
  // 서버가 7축을 채워 내려주되, 브릿지가 일부만 보냈으면 그 축은 null 이다.
  // 값이 하나도 없으면 "아직 못 받았다" 로 본다.
  // FR-21: HandCommand.confidence. 명령이 어느 정도 신뢰도로 만들어졌는지.
  const commandConfidence = typeof handCommand?.confidence === "number"
    && Number.isFinite(handCommand.confidence) ? handCommand.confidence : null;
  // FR-30: "HandCommand는 7개 고정 축·source·sequence·speed_limit·confidence" 다.
  // 7축은 최상위 필드이며 `values` 래퍼가 없다. 브릿지가 .msg 원문을 dump 하므로
  // handCommand 객체 자체가 축을 들고 있다.
  const hasAnyAxis = handCommand
    && HAND_AXIS_KEYS.some((key) => typeof handCommand[key] === "number");
  const axisValues = hasAnyAxis ? handCommand : null;
  // HandCommand.stamp 는 builtin_interfaces/Time 이라 원문 dump 시 {sec, nanosec} 다.
  // formatStamp 가 문자열·Time 양쪽을 받는다. 표시 전용이며 판정에 쓰지 않는다.
  const stampText = formatStamp(handCommand?.stamp);

  // FR-26 녹화 버튼은 MIMIC 모드에서만 활성화
  // recordingStateKnown 이 false 면 RecordingState.msg 를 못 받은 것이다.
  // session_id 없이 StopRecording 을 부를 수 없으므로 시작도 막는다 (fail-closed).
  const canStartRecording =
    isMimicActive &&
    connectionState === "open" &&
    recordingStateKnown &&
    recordingState.state === RECORDING_STATE.IDLE &&
    !recordingState.result_pending;
  const canStopRecording = isMimicActive && recordingStateKnown
    && recordingState.state === RECORDING_STATE.RECORDING;
  const isRecordingActive = [RECORDING_STATE.STARTING, RECORDING_STATE.RECORDING, RECORDING_STATE.STOPPING].includes(
    recordingState.state
  );

  // FR-26: 녹화 종료 후 성공/실패 판정이 완료되기 전에는 다음 녹화를 시작할 수 없다.
  const needsResultJudgement = recordingState.result_pending;

  // 엄지 3축과 네 손가락 4축으로 묶는다. 묶음 자체가 손의 구조를 담는다
  const AXIS_GROUPS = [
    { name: "엄지", keys: ["thumb_flex", "thumb_opp", "thumb_abd"] },
    { name: "네 손가락", keys: ["index_flex", "middle_flex", "ring_flex", "little_flex"] },
  ];
  const axisLabel = Object.fromEntries(HAND_AXES.map((a) => [a.key, a.label]));
  // 축 막대는 spring 으로 움직인다. 손이 움직이는 것을 보고 있으므로
  // 막대도 이어져 움직여야 같은 것을 보고 있다고 느낀다
  const BAR = { type: "spring", stiffness: 200, damping: 26, mass: 0.5 };

  return (
    <div className="grid h-full min-h-0 gap-3 lg:grid-cols-[minmax(0,1.35fr)_minmax(340px,1fr)]">
  {/* 카메라는 남는 높이를 전부 받는다. hand-loss 안내는 흐름에 두면 형제로서
     카메라 높이를 다투므로(검출/미검출 반복 시 카메라가 커졌다 작아졌다 함)
     CameraStream 내부에 오버레이로 겹친다 — showHandLoss 로 켠다 (FR-27). */}
 <div className="flex min-h-0 flex-col gap-3">
   <CameraStream showHandLoss />
 </div>

      <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
        {/* FR-21 Should: 7논리축 목표와 confidence */}
        <Panel>
          <div aria-label="손동작 정보">
            <Head title="7논리축">
              <button
                type="button"
                id="dataToggle"
                aria-pressed={showRealtimeData}
                onClick={() => setShowRealtimeData((v) => !v)}
                className="rounded-full bg-ink-100 px-2 py-0.5 text-xs
                           transition-colors hover:bg-ink-200/70"
              >
                {showRealtimeData ? "접기" : "펼치기"}
              </button>
            </Head>

            <AnimatePresence initial={false}>
              {showRealtimeData && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.22, ease: [0.2, 0, 0.1, 1] }}
                  className="overflow-hidden"
                >
                  <Body className="flex flex-col gap-4">
                    {!axisValues ? (
                      <p className="text-xs text-ink-500">
                        ROS2 브릿지로부터 아직 데이터를 받지 못했습니다.
                      </p>
                    ) : (
                      <div className="flex flex-col gap-4">
                        {AXIS_GROUPS.map((group) => (
                          <div key={group.name} className="flex flex-col gap-1.5">
                            <span className="text-xs font-medium text-ink-400">
                              {group.name}
                            </span>
                            {group.keys.map((key) => {
                              const v = axisValues?.[key];
                              const known = typeof v === "number" && Number.isFinite(v);
                              return (
                                <div key={key}
                                     className="grid grid-cols-[5.5rem_1fr_2.75rem]
                                                items-center gap-3">
                                  <span className="font-mono text-[11px] text-ink-500"
                                        title={axisLabel[key]}>
                                    {key}
                                  </span>
                                  <span className="h-1 overflow-hidden rounded-full
                                                   bg-ink-200">
                                    <motion.span
                                      className="block h-full rounded-full bg-[var(--signal)]"
                                      animate={{
                                        width: known ? `${Math.min(100, v * 100)}%` : "0%",
                                      }}
                                      transition={BAR}
                                    />
                                  </span>
                                  <span className={`text-right font-mono text-xs
                                    ${known ? "" : "text-ink-300"}`}>
                                    {formatAxis(v)}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        ))}
                      </div>
                    )}

                    <div className="h-px bg-ink-200" />
                    {[
                      ["confidence", commandConfidence !== null
                        ? `${Math.round(commandConfidence * 100)}%` : "-"],
                      ["source / sequence",
                        `${handCommand?.source ?? "-"} / ${handCommand?.sequence ?? "-"}`],
                      ["stamp", stampText],
                    ].map(([k, val]) => (
                      <div key={k} className="flex items-baseline justify-between gap-3">
                        <span className="font-mono text-[10px] tracking-[0.14em]
                                         text-ink-400">
                          {k}
                        </span>
                        <span className="font-mono text-xs">{val}</span>
                      </div>
                    ))}
                  </Body>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </Panel>

        {/* FR-26: 기록 UI */}
        <Panel>
          <div aria-label="기록">
            <Head title="기록">
              <Tag tone={isRecordingActive ? "live" : "idle"}>
                {recordingStateKnown ? recordingState.state : "수신 대기"}
              </Tag>
            </Head>

            <Body className="flex flex-col gap-3">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-xs text-ink-400">
                  Session ID
                </span>
                <span className="truncate font-mono text-xs">
                  {recordingState.active_session_id || recordingState.last_session_id || "-"}
                </span>
              </div>

              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={startRecording}
                  disabled={!canStartRecording}
                  className="flex-1 rounded-full bg-ink-900 px-3 py-2 text-[13px] font-semibold
                             text-white transition-opacity hover:opacity-90 disabled:opacity-25"
                >
                  기록 시작
                </button>
                <button
                  type="button"
                  onClick={stopRecording}
                  disabled={!canStopRecording}
                  className="flex-1 rounded-full bg-ink-100 px-3 py-2 text-[13px]
                             font-medium transition-colors hover:bg-ink-200/70
                             disabled:opacity-30"
                >
                  기록 종료
                </button>
              </div>

              {!isMimicActive && (
                <p className="text-xs leading-relaxed text-ink-500">
                  기록은 모방 모드에서 웹이 제어권을 보유한 동안에만 가능합니다.
                </p>
              )}
              {isMimicActive && !recordingStateKnown && (
                <p className="text-xs leading-relaxed text-st-fault">
                  기록 상태(<code>RecordingState</code>)를 받지 못해 Session ID 를
                  확인할 수 없습니다.
                </p>
              )}

              <AnimatePresence initial={false}>
                {needsResultJudgement && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={{ duration: 0.22 }}
                    className="overflow-hidden"
                  >
                    <div className="mb-3 h-px bg-ink-200" />
                    <p className="text-[13px]">
                      판정 대기 중입니다. 방금 기록한 세션의 모방이 성공했습니까?
                    </p>
                    <div className="mt-3 flex gap-2">
                      <button
                        type="button"
                        onClick={() => submitRecordingResult(RECORDING_RESULT.SUCCESS)}
                        className="flex-1 rounded-full bg-st-ready px-3 py-2 text-[13px]
                                   font-semibold text-white hover:opacity-90"
                      >
                        성공
                      </button>
                      <button
                        type="button"
                        onClick={() => submitRecordingResult(RECORDING_RESULT.FAILURE)}
                        className="flex-1 rounded-full bg-ink-200 px-3 py-2
                                   text-[13px] font-medium hover:bg-ink-200/70"
                      >
                        실패
                      </button>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </Body>
          </div>
        </Panel>
      </div>
    </div>
  );
}
