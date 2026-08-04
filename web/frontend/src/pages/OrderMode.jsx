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
import { useHandSocket } from "../context/HandSocketContext";
import { BASIC_GESTURES, SEQUENCE_ACTIONS } from "../config/commandPresets";
import { CONTROL_MODE, CONTROL_OWNER } from "../config/messageProtocol";
import MotorStatusPanel from "../components/MotorStatusPanel";
import ModeAcquirePanel from "../components/ModeAcquirePanel";
import CameraStream from "../components/CameraStream";
import { motion } from "motion/react";
import { Panel, Head, Body, Tag } from "../ui/Sheet";
import GesturePreview from "../components/GesturePreview";

export default function OrderMode() {
  const {
    connectionState,
    controlState,
    controlStateKnown,
    safetyState,
    safetyStateKnown,
    motorStatus,
    sectionUpdatedAt,
    snapshotReceivedAt,
    needsResumeConfirmation,
    isSafeToOperate,
    webHasControl,
    commandInFlight,
    sendGesture,
    sendSequence,
    sendStop,
  } = useHandSocket();

  // FR-22 "같은 시점에 Gesture 하나만 실행하고 새 일반 동작은 큐에 쌓지 않고
  // 거부한다." 이 판정 주체는 로봇이다. 웹은 ack 왕복 동안만 잠가 더블클릭을
  // 막고, 실제 중복 실행은 FR-37 motion_active 거부 문구로 안내한다.
  //
  // (이전 구현은 control_state.sequence_running 의 true→false 로 잠금을 풀었다.
  //  그 필드는 이름 그대로 ExecuteSequence 액션용일 수 있고 — FR-31 은 Gesture 와
  //  Action 을 구분한다 — 브릿지가 Gesture 실행 중에 세워 주지 않으면 제스처를
  //  한 번 보낸 뒤 패널이 영구히 잠겼다. 브릿지 구현에 대한 의존을 끊었다.)

  const isManualActive = controlState.active_mode === CONTROL_MODE.MANUAL;
  const isConnected = connectionState === "open";

  const commandsDisabled =
    !isManualActive ||
    !isConnected ||
    !isSafeToOperate ||
    !webHasControl ||
    needsResumeConfirmation ||
    commandInFlight;

  // 왜 잠겼는지 한 가지만 말한다. 여러 이유를 나열하면 읽지 않는다.
  const reason = !isConnected ? "서버에 연결되어 있지 않습니다."
    : !controlStateKnown ? "로봇의 제어 상태를 아직 받지 못했습니다."
    : needsResumeConfirmation ? "제어가 재개되지 않았습니다. 모드를 다시 획득하세요."
    : !isSafeToOperate
      ? (safetyStateKnown
        ? `안전 상태 ${safetyState.state} 에서는 조작 명령을 보낼 수 없습니다.`
        : "안전 상태를 아직 받지 못했습니다.")
    : controlState.active_owner === CONTROL_OWNER.LOCAL
      ? "로컬 프로그램이 제어권을 보유하고 있습니다."
    : !webHasControl ? "제어권을 먼저 획득하세요."
    : !isManualActive ? "조작 모드가 아닙니다."
    : commandInFlight ? "직전 명령의 응답을 기다리는 중입니다."
    : "";

  const runGesture = (gesture) => {
    if (commandsDisabled) return;
    sendGesture(gesture);
  };

  const runSequence = (action) => {
    if (commandsDisabled) return;
    sendSequence(action.id, action.speed_limit);
  };

  const handleStop = () => {
    // FR-23: 정지는 다른 일반 명령보다 우선한다. 잠금과 무관하게 항상 전송한다.
    sendStop();
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.45fr)_minmax(330px,1fr)] lg:items-start">
      <div className="flex flex-col gap-4">
        {/* FR-19 인수조건: 조작 모드에서도 영상·손 검출을 관제할 수 있어야 한다 */}
        <CameraStream />
        {/* FR-25: 명령 결과를 바로 확인할 수 있도록 영상 아래에 둔다 */}
        <MotorStatusPanel
          motorStatus={motorStatus}
          motorUpdatedAt={sectionUpdatedAt.motor_state ?? null}
          receivedAt={snapshotReceivedAt}
        />
      </div>

      <div className="flex flex-col gap-4">
        {/* FR-19 / FR-35 / NFR-23: 제어권은 사용자가 직접 획득한다 */}
        <ModeAcquirePanel targetMode={CONTROL_MODE.MANUAL} />

        <Panel>
          <div aria-label="명령">
            <Head title="명령">
              <GesturePreview />
              <Tag tone={commandsDisabled ? "idle" : "live"}>
                {commandsDisabled ? "잠김" : "전송 가능"}
              </Tag>
            </Head>

            <Body className="flex flex-col gap-4">
              {reason && <p className="text-xs leading-relaxed text-ink-500">{reason}</p>}

              {/* FR-22 기본 명령 (Gesture) */}
              <div className="grid grid-cols-2 gap-2">
                {BASIC_GESTURES.map((gesture) => (
                  <motion.button
                    key={gesture.id}
                    type="button"
                    onClick={() => runGesture(gesture)}
                    disabled={commandsDisabled}
                    aria-label={gesture.label}
                    title={gesture.label}
                    whileHover={commandsDisabled ? undefined : { y: -2 }}
                    whileTap={commandsDisabled ? undefined : { scale: 0.97 }}
                    transition={{ type: "spring", stiffness: 500, damping: 30 }}
                    className="flex flex-col items-center gap-1.5 rounded border
                               border-ink-300 bg-ink-50 px-2 py-4
                               transition-colors hover:bg-white disabled:opacity-30"
                  >
                    <span className="text-2xl leading-none" aria-hidden="true">
                      {gesture.icon}
                    </span>
                    <span className="text-xs font-medium">{gesture.label}</span>
                    <span className="font-mono text-[10px] text-ink-400">{gesture.id}</span>
                  </motion.button>
                ))}
              </div>

              {/* FR-22 추가 명령 (Sequence) — FR-39 Could */}
              <div className="grid grid-cols-2 gap-2">
                {SEQUENCE_ACTIONS.map((action) => (
                  <motion.button
                    key={action.id}
                    type="button"
                    onClick={() => runSequence(action)}
                    disabled={commandsDisabled}
                    title={action.label}
                    whileTap={commandsDisabled ? undefined : { scale: 0.97 }}
                    className="rounded-full bg-ink-100 px-3 py-2 text-xs font-medium
                               transition-colors hover:bg-ink-200/70 disabled:opacity-30"
                  >
                    <span aria-hidden="true">{action.icon}</span> {action.label}
                  </motion.button>
                ))}
              </div>

              <div className="h-px bg-ink-200" />

              <motion.button
                type="button"
                onClick={handleStop}
                whileTap={{ scale: 0.98 }}
                className="w-full rounded-full bg-st-fault py-2.5 text-[13px] font-semibold
                           text-white transition-opacity hover:opacity-90"
              >
                기능 중지
              </motion.button>
              <p className="text-xs text-ink-400">
                정지는 잠금과 무관하게 항상 전송됩니다.
              </p>
            </Body>
          </div>
        </Panel>
      </div>
    </div>
  );
}
