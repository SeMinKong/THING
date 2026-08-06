// FR-27: 웹 오류 및 안전 안내
// 손 검출 실패/카메라 연결 실패/ROS2 통신 단절/RPi 연결 실패/모터 통신 오류/
// timeout/과전류/과온/비상정지 등을 사용자가 이해할 수 있는 문구로 안내한다.
import { motion, AnimatePresence } from "motion/react";
import { useHandSocket } from "../context/HandSocketContext";
import { RESET_ALLOWED_STATES, isDeviceDown } from "../config/messageProtocol";
import { SPEC } from "../config/pending";

const SKIN = {
  danger: "bg-st-fault/8",
  warning: "bg-st-hold/8",
};

/** 알림 한 줄. 렌더 안에서 정의하면 매번 새 타입이 되어 재마운트된다 */
function Row({ level, children }) {
  return (
    <div className={`rounded-card px-5 py-4 ${SKIN[level] ?? SKIN.warning}`}>
      <div className="text-[13.5px] leading-relaxed text-ink-900">{children}</div>
    </div>
  );
}

function buildMessages({
  connectionState, connectionStatus, safetyState, safetyStateKnown,
  controlStateKnown, sessionIdProtocolError, mode,
}) {
  const messages = [];
  const online = connectionState === "open";

  if (!online) {
    messages.push({ level: "danger", text: "웹 서버(ROS2 브릿지) 연결이 끊어졌습니다." });
  }

  // connectionStatus 는 "unknown" | "up" | "down" 문자열이다.
  // 이전 구현은 `!connectionStatus.ros2` 로 bool 처럼 읽어서 값이 무엇이든 항상
  // false 가 되었고, 전 장치가 down 이어도 아래 경고가 한 번도 뜨지 않았다.
  // 관측된 단절만 경고한다. "아직 모름" 은 단절이 아니다 (FR-24).
  const DEVICE_MESSAGES = [
    ["ros2", "danger", "ROS 2 통신이 단절되었습니다."],
    ["jetson", "warning", "Jetson 상태 갱신이 멈췄습니다."],
    ["rpi", "warning", "Raspberry Pi 상태 갱신이 멈췄습니다."],
    ["camera", "warning", "카메라 영상 갱신이 멈췄습니다."],
    ["motor", "danger", "모터 통신이 단절되었습니다."],
  ];
  if (online) {
    for (const [key, level, text] of DEVICE_MESSAGES) {
      if (isDeviceDown(connectionStatus[key])) messages.push({ level, text });
    }
  }

  // 브릿지가 control_state 를 안 보내면 화면이 "비활성화 / 제어권 없음" 으로
  // 보인다. 로봇의 정상 상태와 똑같이 생겨서 누락을 알아챌 수 없다.
  if (online && !controlStateKnown) {
    messages.push({
      level: "warning",
      text: "제어 상태(ControlState)를 받지 못했습니다. 현재 모드와 제어권을 확인할 수 없어 조작을 막습니다.",
    });
  }

  if (sessionIdProtocolError) {
    messages.push({
      level: "danger",
      text: "로봇이 Session ID 를 숫자로 보냈습니다. 63-bit 값이 손상되어 기록 종료·판정을 보낼 수 없습니다. "
        + "브릿지가 10진 문자열로 보내야 합니다(6.5절).",
    });
  }

  // 손 미검출 안내는 CameraStream 이 담당한다 — 좌하단 검출 배지(순간 상태)와
  // 상단 오버레이("재개 필요", 래치). 여기(in-flow 배너)에 중복으로 두면
  // handDetection 이 순간 토글될 때 배너가 떴다 사라지며 아래 카메라가 커졌다
  // 작아졌다 하므로 제거했다. 같은 정보를 오버레이로만 제공한다 (FR-27).

  // 6.4절 {} 규칙: SafetyState 를 아직 받지 못했으면 기본값은 관측값이 아니다.
  // 이 구간에서 개별 플래그를 읽으면 motor_communication_ok=false 때문에
  // "모터 통신 오류" 같은 허위 경고가 접속 직후 항상 떴다.
  if (!safetyStateKnown) {
    messages.push({
      level: "warning",
      text: "안전 상태(SafetyState)를 아직 수신하지 못했습니다. 로봇 상태를 판단할 수 없습니다.",
    });
    return messages;
  }

  if (safetyState.command_timeout) {
    messages.push({ level: "warning", text: "명령 수신 timeout이 발생했습니다." });
  }
  if (safetyState.over_current) {
    messages.push({ level: "danger", text: "모터 과전류가 감지되었습니다." });
  }
  if (safetyState.over_temperature) {
    messages.push({ level: "danger", text: "모터 과온이 감지되었습니다." });
  }
  if (safetyState.motor_communication_ok === false) {
    messages.push({ level: "danger", text: "모터 통신 오류가 발생했습니다." });
  }
  
  if (safetyState.estop_active) {
    // 안정 시간은 FR-41 이 YAML 소관으로 둔 값이다. 문구에 숫자를 직접 박으면
    // YAML 이 바뀔 때 화면이 거짓말을 하므로 설정 사본에서 읽고 "설정값" 임을 밝힌다.
    messages.push({
      level: "danger",
      text: "비상정지가 작동했습니다. 물리 E-Stop 을 해제하고 설정된 안정 시간"
        + `(현재 설정 ${SPEC.ESTOP_RELEASE_STABLE_MS}ms)`
        + " 뒤 Safety Reset 이 필요합니다.",
    });
  }

  // FR-27 / FR-35 복구 절차.
  //
  // V7 에서 HOLD 복구가 바뀌었다. 이전에는 "명시적 STOP → READY → 재획득" 하나였고
  // 지금은 자동복귀 경로가 생겼다. FR-27 은 세 가지를 함께 안내하라고 한다.
  //   (a) Guard 검증 activity 300ms 연속(최대 gap 100ms)이면 RUN 자동복귀
  //   (b) 명시적 STOP → Guard ACK → RESET → READY
  //   (c) 아무것도 성립하지 않으면 총 1000ms 에 SAFE 상승
  // 그리고 "MANUAL은 현재 recovery activity를 자동 생성하지 않는다" 는 단서가 있어
  // 모드에 따라 (a) 가능 여부가 갈린다.
  //
  // 숫자는 FR-41 이 YAML 소관으로 둔 값이라 설정 사본에서 읽는다.
  if (safetyState.state === "HOLD") {
    const t = SPEC;
    if (mode === "MIMIC") {
      messages.push({
        level: "warning",
        text: "HOLD 상태입니다. 손을 다시 인식시켜 유효한 명령이 "
          + `${t.HOLD_RECOVERY_ACTIVITY_MS}ms 연속(끊김 ${t.HOLD_RECOVERY_MAX_GAP_MS}ms 이내)되면 `
          + "제어가 자동으로 재개됩니다. 이 동안 모터로는 명령이 전달되지 않습니다.",
      });
    } else {
      // FR-27: "MANUAL은 현재 recovery activity를 자동 생성하지 않는다."
      // 조건 자체는 함께 안내하되 이 모드에서는 성립하지 않음을 밝힌다.
      messages.push({
        level: "warning",
        text: "HOLD 상태입니다. 자동복귀 조건은 유효한 명령이 "
          + `${t.HOLD_RECOVERY_ACTIVITY_MS}ms 연속(끊김 ${t.HOLD_RECOVERY_MAX_GAP_MS}ms 이내)`
          + "되는 것인데, 조작 모드는 그 명령을 스스로 만들지 않습니다. "
          + "정지(STOP)를 선택하거나, 그대로 두면 안전 자세로 전환됩니다.",
      });
    }
    messages.push({
      level: "warning",
      text: `마지막 명령으로부터 ${t.SAFE_DEADLINE_MS}ms(설정값) 안에 재개되지 않으면 `
        + "안전 자세(SAFE)로 전환되며, 그 뒤에는 원인 해소와 Safety Reset 절차가 필요합니다. "
        + "지금 정지(STOP)를 요청하면 제어기 확인 뒤 RESET 절차(모터 이동 없이 토크 해제 확인)를 거쳐 준비 상태로 돌아갑니다.",
      actionNeeded: "STOP",
    });
  }

  // V7.1/safety_manager. 명시적 STOP 뒤 모터 이동 없이 torque OFF 를 재확인하는 정상 절차다(home_position 복귀 아님).
  if (safetyState.state === "RESET") {
    messages.push({
      level: "warning",
      text: `정지 절차를 진행 중입니다. 모터를 움직이지 않고 토크를 해제하는 중이며, 설정된 유지 시간(${SPEC.STOP_SETTLE_MS}ms)과 7개 모터의 토크 해제가 확인되면 준비(READY) 상태가 됩니다. 그 뒤 모드를 다시 획득하십시오.`,
    });
  }

  // 알 수 없는 SafetyState 상수. toSymbol 이 UNKNOWN(n) 으로 드러낸다.
  if (typeof safetyState.state === "string" && safetyState.state.startsWith("UNKNOWN(")) {
    messages.push({
      level: "danger",
      text: `로봇이 웹이 모르는 안전 상태 ${safetyState.state} 를 보냈습니다. `
        + "웹과 인터페이스 버전이 다를 수 있습니다. 안전을 위해 조작을 막습니다.",
    });
  }

  if (RESET_ALLOWED_STATES.includes(safetyState.state)) {
    let reasonText = safetyState.reason || (safetyState.state === "SAFE" ? "안전 자세 진입 (장시간 명령 단절)" : "");
    messages.push({
      level: "danger",
      text: `${safetyState.state} 상태입니다. 원인(${reasonText})을 해소하고 안정 시간이 지난 뒤 [Safety Reset]을 수행하여 시스템을 초기화(INIT 재검사)해야 합니다.`,
      actionNeeded: "RESET",
    });
  }

  return messages;
}

export default function SafetyBanner() {
  const {
    connectionState, connectionStatus, safetyState, safetyStateKnown,
    controlState, controlStateKnown, sessionIdProtocolError,
    lastError, sendStop, resetSafety,
  } = useHandSocket();

  const messages = buildMessages({
    connectionState,
    connectionStatus,
    safetyState,
    safetyStateKnown,
    controlStateKnown,
    sessionIdProtocolError,
    mode: controlState.active_mode,
  });

  // FR-27: reset 가능 여부와 거부 사유를 구분한다.
  const canReset = RESET_ALLOWED_STATES.includes(safetyState.state) && safetyStateKnown;

  // 알림은 들어오고 나갈 때가 정보다. 갑자기 나타나고 사라지면 놓친다.
  const enter = {
    initial: { opacity: 0, height: 0 },
    animate: { opacity: 1, height: "auto" },
    exit: { opacity: 0, height: 0 },
    transition: { duration: 0.24, ease: [0.2, 0, 0.1, 1] },
  };

  return (
    <div className="flex flex-col gap-2" role="region" aria-label="안전 안내">
      <AnimatePresence initial={false}>
        {lastError && (
          <motion.div key={`err-${lastError.code}`} {...enter} className="overflow-hidden">
            <Row level="danger">
              <span className="font-mono text-xs font-medium text-st-fault">
                [{lastError.code}]
              </span>{" "}
              {lastError.message}
            </Row>
          </motion.div>
        )}

        {messages.map((m, i) => (
          <motion.div key={`${m.level}-${m.text.slice(0, 24)}-${i}`} {...enter}
                      className="overflow-hidden">
            <Row level={m.level}>
              {m.text}
              {m.actionNeeded === "STOP" && (
                <motion.button
                  type="button"
                  whileTap={{ scale: 0.97 }}
                  onClick={sendStop}
                  className="mt-3 block rounded-full bg-st-fault px-4 py-1.5 text-[13px]
                             font-semibold text-white"
                >
                  정지(STOP)
                </motion.button>
              )}
              {m.actionNeeded === "RESET" && canReset && (
                <motion.button
                  type="button"
                  whileTap={{ scale: 0.97 }}
                  onClick={resetSafety}
                  className="mt-3 block rounded-full bg-ink-900 px-4 py-1.5 text-[13px]
                             font-semibold text-white"
                >
                  안전 초기화 요청
                </motion.button>
              )}
            </Row>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
