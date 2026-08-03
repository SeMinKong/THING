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
import { motion, AnimatePresence } from "motion/react";
import { useHandSocket } from "../context/HandSocketContext";
import { Panel, Head, Body } from "../ui/Sheet";
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
    controlStateKnown,
    requestedMode,
    modeRejectedReason,
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

  // 2계층에서는 snapshot 이 도착했다는 것 자체가 브릿지가 살아 있다는 뜻이다.
  // 별도 bridge_connected 플래그를 요구하지 않는다.
  const canStop = isConnected && !isDisabled;
  const canAcquire = (
    isConnected && controlStateKnown && isDisabled && safetyReady
    && !otherOwner && !recordingBusy
  );

  // 왜 못 얻는지 하나만 말한다. 목록으로 나열하면 읽지 않는다.
  const blocked = !isConnected ? "서버에 연결되어 있지 않습니다."
    : !controlStateKnown ? "로봇의 제어 상태를 아직 받지 못했습니다."
    : !isDisabled ? "다른 모드가 활성화되어 있습니다. 먼저 정지하세요."
    : otherOwner ? `${controlState.active_owner} 이(가) 제어권을 보유하고 있습니다.`
    : recordingBusy ? `기록 중(${recordingState.state})에는 모드를 바꿀 수 없습니다.`
    : !safetyReady
      ? (safetyStateKnown
        ? `안전 상태 ${safetyState.state} 에서는 획득할 수 없습니다.`
        : "안전 상태를 아직 받지 못했습니다.")
      : "";

  const note = modeRejectedReason || blocked;

  return (
    <Panel>
      <div role="region" aria-label={`${label} 모드 획득`}>
        <Head title="제어권">
          <span className="font-mono text-xs text-ink-600">{label}</span>
        </Head>

        <Body className="flex flex-col gap-4">
          {/* 두 단계는 실제 절차다 (FR-19). 번호가 순서를 담는다.
              1단계가 끝나면 표시가 채워진다 — 진행이 보여야 다음을 누른다 */}
          <ol className="flex flex-col gap-2">
            {[
              { n: 1, text: "정지해서 비활성화 상태로 만듭니다", done: isDisabled },
              { n: 2, text: `${label} 모드와 제어권을 획득합니다`, done: false },
            ].map((step) => (
              <li key={step.n} className="flex items-baseline gap-3 text-[13px]">
                <motion.span
                  animate={step.done
                    ? { borderColor: "var(--color-st-ready)", color: "var(--color-st-ready)" }
                    : { borderColor: "var(--color-ink-300)", color: "var(--color-ink-400)" }}
                  transition={{ duration: 0.25 }}
                  className="shrink-0 rounded border px-1.5 font-mono text-[11px]"
                >
                  {step.n}
                </motion.span>
                <span className={step.done ? "text-ink-900" : "text-ink-500"}>
                  {step.text}{step.done && " — 완료"}
                </span>
              </li>
            ))}
          </ol>

          <AnimatePresence mode="wait" initial={false}>
            {note && (
              <motion.p
                key={note}
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.18 }}
                className={`overflow-hidden text-xs leading-relaxed ${
                  modeRejectedReason ? "text-st-hold" : "text-ink-500"}`}
              >
                {note}
              </motion.p>
            )}
          </AnimatePresence>

          {requestedMode && (
            <p className="text-xs leading-relaxed text-ink-500">
              요청한 모드(<strong className="font-semibold text-ink-900">{requestedMode}</strong>)의
              확정을 기다리는 중입니다. 확정은 <code>/thing/control_state</code> 로만 판단합니다.
            </p>
          )}

          <div className="flex gap-2">
            <button
              type="button"
              onClick={sendStop}
              disabled={!canStop}
              className="flex-1 rounded-full bg-st-fault/12 px-3 py-2
                         text-[13px] font-semibold text-st-fault transition-colors
                         hover:bg-st-fault/20 disabled:opacity-35"
            >
              정지(STOP)
            </button>
            <button
              type="button"
              onClick={() => selectMode(targetMode)}
              disabled={!canAcquire}
              className="flex-1 rounded-full bg-ink-900 px-3 py-2 text-[13px] font-semibold
                         text-white transition-opacity hover:opacity-90 disabled:opacity-25"
            >
              {label} 획득
            </button>
          </div>

          <p className="text-xs leading-relaxed text-ink-400">
            연결이 복구되거나 손이 다시 인식되어도 제어는 자동으로 재개되지 않습니다.
          </p>
        </Body>
      </div>
    </Panel>
  );
}
