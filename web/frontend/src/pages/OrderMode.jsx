// ============================================================================
// 조작(MANUAL) 모드 페이지
// ----------------------------------------------------------------------------
// FR-25: 이 화면에서는 영상보다 모터 상태가 중요하다. 보낸 명령이 실제로
//   반영됐는지를 왼쪽 모터 표에서 확인한다. 영상·손 검출 관제는 모방 화면이 맡는다.
// FR-22: 버튼 입력을 통한 명령 전달 (기본 명령 = Gesture, 추가 명령 = Sequence)
// FR-23: 웹 명령 범위 제한 (정지 명령 최우선, 큐잉 금지)
// FR-25: 모터 상태 확인
// FR-27: 위험 상태에서 새 명령 비활성화
// ============================================================================
import { useState } from "react";
import { useHandSocket } from "../context/HandSocketContext";
import { BASIC_GESTURES, SEQUENCE_ACTIONS } from "../config/commandPresets";
import { CONTROL_MODE, CONTROL_OWNER } from "../config/messageProtocol";
import MotorStatusPanel from "../components/MotorStatusPanel";
import { motion } from "motion/react";
import { Panel, Head, Body, Tag } from "../ui/Sheet";
import HandPoseView from "../components/HandPoseView";

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
  } = useHandSocket();

  // FR-22 "같은 시점에 Gesture 하나만 실행하고 새 일반 동작은 큐에 쌓지 않고
  // 거부한다." 이 판정 주체는 로봇이다. 웹은 ack 왕복 동안만 잠가 더블클릭을
  // 막고, 실제 중복 실행은 FR-37 motion_active 거부 문구로 안내한다.
  //
  // (이전 구현은 control_state.sequence_running 의 true→false 로 잠금을 풀었다.
  //  그 필드는 이름 그대로 ExecuteSequence 액션용일 수 있고 — FR-31 은 Gesture 와
  //  Action 을 구분한다 — 브릿지가 Gesture 실행 중에 세워 주지 않으면 제스처를
  //  한 번 보낸 뒤 패널이 영구히 잠겼다. 브릿지 구현에 대한 의존을 끊었다.)

  // hover 한 명령의 자세를 우측 미리보기 슬롯에 그린다. disabled 버튼은 mouseenter 를
  // 쏘지 않으므로 잠겼을 때는 자연히 표시되지 않고, 렌더에서도 command=null 로 한 번 더 막는다.
  const [hoveredCommand, setHoveredCommand] = useState(null);

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

  // 정지는 화면 이동 게이트가 맡는다 (components/ModeGate.jsx).
  // sendStop() 은 SetControlMode(DISABLED, NONE) 이므로 실행 중 동작을 끊는 것이
  // 아니라 제어권을 해제하는 것이다. 여기 버튼으로 두면 누르는 순간 제어권이
  // 풀려 개요로 되돌아가므로, 이동 흐름 안에 두는 편이 맞다.

  return (
    <div className="grid h-full min-h-0 gap-3 lg:grid-cols-[minmax(0,1.35fr)_minmax(340px,1fr)]">
            <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
        <Panel className="flex min-h-0 flex-1 flex-col">
          <div aria-label="명령" className="flex min-h-0 flex-1 flex-col">
            <Head title="명령">
              <Tag tone={commandsDisabled ? "idle" : "live"}>
                {commandsDisabled ? "잠김" : "전송 가능"}
              </Tag>
            </Head>

            <Body className="flex min-h-0 flex-1 flex-col gap-3">
              {reason && <p className="text-xl leading-relaxed text-ink-500">{reason}</p>}

              {/* FR-22 기본 명령 (Gesture) 및 추가 명령 (Sequence) — 동일한 2열 그리드에서 6개 버튼이 남는 높이를 나눠 갖는다 */}
              {/* leave 는 버튼이 아니라 그리드 전체에 건다. 버튼 사이 gap 은 그리드 안쪽이라
                  버튼을 옮기는 동안 프리뷰가 사라지지 않고 자세 morph 가 이어진다. */}
              <div
                className="grid min-h-0 flex-1 grid-cols-2 gap-2"
                onMouseLeave={() => setHoveredCommand(null)}
              >
                {[...BASIC_GESTURES, ...SEQUENCE_ACTIONS].map((command) => {
                  const isSequence = SEQUENCE_ACTIONS.some((action) => action.id === command.id);

                  return (
                    <motion.button
                      key={command.id}
                      type="button"
                      onClick={() => (isSequence ? runSequence(command) : runGesture(command))}
                      onMouseEnter={() => setHoveredCommand(command)}
                      disabled={commandsDisabled}
                      aria-label={command.label}
                      title={command.label}
                      whileHover={commandsDisabled ? undefined : { y: -2 }}
                      whileTap={commandsDisabled ? undefined : { scale: 0.97 }}
                      transition={{ type: "spring", stiffness: 500, damping: 30 }}
                      className="flex flex-col items-center justify-center gap-1.5 rounded-2xl border border-action-200 bg-action-50 px-2 py-3 text-center transition-colors hover:border-action-700 hover:bg-action-100 disabled:opacity-30"           >
                      <span className="text-2xl leading-none" aria-hidden="true">
                        {command.icon}
                      </span>
                      <span className="text-3xl font-medium">{command.label}</span>
                      {!isSequence && <span className="font-mono text-[20px] text-ink-400">{command.id}</span>}
                    </motion.button>
                  );
                })}
              </div>

            </Body>
          </div>
        </Panel>
      </div>
      {/* FR-25: 조작 모드에서는 영상보다 모터 상태가 중요하다.
          보낸 명령이 실제로 반영됐는지를 여기서 확인한다.
          모터 표는 내용 높이로 두고, 그 아래 남는 공간을 동작 미리보기 슬롯으로 쓴다. */}
      <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
        <MotorStatusPanel
          motorStatus={motorStatus}
          motorUpdatedAt={sectionUpdatedAt.motor_state ?? null}
          receivedAt={snapshotReceivedAt}
        />
        <Panel className="flex min-h-0 flex-1 flex-col" delay={0.05}>
          <Head
            title="동작 미리보기"
            afterTitle={hoveredCommand ? (
              <span className="text-ink-400 text-sm">{hoveredCommand.label}</span>
            ) : null}
          />
          <Body className="flex min-h-0 flex-1 flex-col">
            {/* 잠김(조작 모드 아님·제어권 없음 등)이면 command=null → 미리보기 안 뜸 */}
            <HandPoseView command={commandsDisabled ? null : hoveredCommand} />
          </Body>
        </Panel>
      </div>


    </div>
  );
}
