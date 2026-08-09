// ============================================================================
// 머리 — 화면 위쪽 전체가 안전 상태 색이 된다
// ----------------------------------------------------------------------------
// 앞 판들은 흰 바탕에 작은 색 표시로 상태를 냈다. 그래서 상태가 바뀌어도
// 화면을 보고 있지 않으면 놓쳤다. 운용자는 영상과 자기 손을 보고 있지 상태
// 글자를 읽고 있지 않다.
//
// 그래서 머리 영역을 통째로 물들인다. ESTOP 이면 화면 위쪽이 진홍이 된다.
// 주변시로 먼저 도달하는 것이 목적이므로 색이 크고 진해야 한다.
//
// Motion 이 색 자체를 보간한다. 툭 갈아 끼우면 깜빡임으로 읽히고, 이어서
// 넘어가면 "상태가 옮겨 갔다" 로 읽힌다. 그 차이가 이 화면의 전부다.
//
// FR-27 은 "reset 가능 여부와 거부 사유를 구분" 하라고 한다. 판정 문구가 그
// 역할을 한다. 숫자는 전부 pending.js 의 SPEC 에서 읽는다.
// ============================================================================
import { useLocation } from "react-router-dom";
import { motion } from "motion/react";
import { useHandSocket } from "../context/HandSocketContext";
import { CONTROL_MODE, CONTROL_OWNER } from "../config/messageProtocol";
import { SPEC } from "../config/pending";
import StatusBar from "./StatusBar";
import { useModeGate } from "./ModeGate";

// 트랙 순서는 uint8 상수값이 아니라 FR-35 위상 순서다.
// .msg 는 RESET=7 이라 배열 끝이지만 절차상 RESET 은 HOLD 다음이다.
const TRACK = ["INIT", "READY", "RUN", "HOLD", "RESET", "SAFE", "FAULT", "ESTOP"];
const DEGRADED_FROM = 5;   // 여기부터는 /thing/reset_safety 가 필요하다

const NAV = [
  { to: "/", label: "개요", end: true },
  { to: "/vision", label: "모방" },
  { to: "/order", label: "조작" },
];

/** 판정과 근거. 판정이 먼저, 근거가 뒤. */
function verdict(state, known, mode) {
  if (!known) {
    return ["상태를 기다리는 중입니다",
      "로봇이 안전 상태를 보내기 전까지 조작을 막습니다."];
  }
  switch (state) {
    case "READY":
      return ["제어권을 획득하면 조작할 수 있습니다", "모터 토크가 꺼져 있습니다."];
    case "RUN":
      return ["조작할 수 있습니다", "명령이 모터로 전달되고 있습니다."];
    case "INIT":
      return ["점검 중입니다", "설정·비상 정지 입력·7개 모터를 확인하고 있습니다."];
    case "HOLD":
      return ["일시 정지되었습니다",
        mode === CONTROL_MODE.MIMIC
          ? `손을 다시 인식시켜 유효한 명령이 ${SPEC.HOLD_RECOVERY_ACTIVITY_MS}ms 연속`
            + `(끊김 ${SPEC.HOLD_RECOVERY_MAX_GAP_MS}ms 이내)되면 자동으로 재개됩니다. `
            + `${SPEC.SAFE_DEADLINE_MS}ms 안에 재개되지 않으면 안전 자세로 넘어갑니다.`
          : "조작 모드는 재개용 명령을 스스로 만들지 않습니다. 정지를 선택하거나, "
            + `${SPEC.SAFE_DEADLINE_MS}ms 뒤 안전 자세로 넘어갑니다.`];
    case "RESET":
      return ["정지 절차를 진행 중입니다",
        `${SPEC.STOP_SETTLE_MS}ms 안정과 모터 토크 해제를 확인하면 준비 상태가 됩니다.`];
    case "SAFE":
      return ["안전 자세로 물러났습니다",
        `원인을 없앤 뒤 ${SPEC.FAULT_CLEAR_STABLE_MS}ms 안정되면 안전 초기화를 요청할 수 있습니다.`];
    case "FAULT":
      return ["고장이 감지되었습니다",
        `과전류·과온·통신 오류를 해결하고 ${SPEC.FAULT_CLEAR_STABLE_MS}ms 안정되면 `
        + "안전 초기화를 요청할 수 있습니다."];
    case "ESTOP":
      return ["비상 정지되었습니다",
        `비상 정지 스위치를 물리적으로 풀고 ${SPEC.ESTOP_RELEASE_STABLE_MS}ms 지난 뒤 `
        + "안전 초기화를 요청할 수 있습니다."];
    default:
      return [`알 수 없는 상태입니다 (${state})`,
        "웹이 모르는 값입니다. 브라우저 콘솔의 진단 기록을 확인하세요."];
  }
}

export default function Header() {
  const {
    safetyState, safetyStateKnown, controlState, controlStateKnown, webHasControl,
  } = useHandSocket();

  const { pathname } = useLocation();
  const { go } = useModeGate();
  const state = safetyStateKnown ? safetyState.state : null;
  const [head, why] = verdict(state, safetyStateKnown, controlState.active_mode);
  const owner = controlStateKnown ? controlState.active_owner : null;

  return (
    // 색값은 index.css 의 --signal 한 곳에서만 정의한다. JS 로 복제하면 테마를
    // 바꿨을 때 머리만 옛 색으로 남는다.
    // 보간은 CSS transition 이 한다. Motion 은 CSS 변수를 해석하지 못하는 환경이
    // 있고, 색 하나 흐르게 하려고 JS 를 쓸 이유도 없다.
    <header
      className="bg-[var(--signal)] text-white transition-colors duration-500
                 ease-[cubic-bezier(0.2,0,0.1,1)]"
    >
      <div className="mx-auto max-w-[1400px] px-6 pb-3 pt-3">
        {/* 1행 — 정체·이동·연결.
            nav 를 화면 중앙에 두려면 양옆이 같은 폭을 가져야 한다.
            flex-1 basis-0 두 개가 남는 공간을 반씩 나눠 가진다. */}
        <div className="flex items-center gap-x-4">
          <div className="flex flex-1 basis-0 items-center">
            <span className="font-mono text-[18px] font-semibold tracking-tight">THING</span>
          </div>

          {/* 이동은 게이트를 지난다. 제어권을 쥔 채로는 나가지 못한다 */}
          <nav className="flex shrink-0 gap-8" aria-label="화면 이동">
            {NAV.map((item) => {
              const isActive = item.end
                ? pathname === item.to
                : pathname.startsWith(item.to);
              return (
                <button
                  key={item.to}
                  type="button"
                  onClick={() => go(item.to)}
                  aria-current={isActive ? "page" : undefined}
                  className={`relative rounded-full px-4 py-1.5 text-[28px] font-medium
                              transition-colors ${
                                isActive ? "text-white" : "text-white/70 hover:text-white"}`}
                >
                  {/* 활성 알약이 탭 사이를 미끄러진다 */}
                  {isActive && (
                    <motion.span
                      layoutId="nav-pill"
                      transition={{ type: "spring", stiffness: 460, damping: 38 }}
                      className="absolute inset-0 rounded-full bg-white/20"
                    />
                  )}
                  <span className="relative">{item.label}</span>
                </button>
              );
            })}
          </nav>

          <div className="flex flex-1 basis-0 justify-end">
            <StatusBar />
          </div>
        </div>

        {/* 2행 — 판정. 화면에서 제일 큰 글자 */}
        <div className="mt-3 flex flex-wrap items-end gap-x-6 gap-y-2">
          <div className="min-w-0 flex-1">
            <motion.h1
              key={head}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.28, ease: [0.2, 0, 0.1, 1] }}
              className="text-[38px] font-bold leading-tight tracking-[-0.02em] sm:text-[24px]"
            >
              {head}
            </motion.h1>
            <motion.p
              key={why}
              initial={{ opacity: 0 }}
              animate={{ opacity: 0.75 }}
              transition={{ duration: 0.28, delay: 0.06 }}
              className="mt-1 max-w-[74ch] text-[19px] leading-relaxed"
            >
              {why}
            </motion.p>
          </div>

          {/* 제어권. 박동 주기는 FR-34 의 갱신 주기 그 자체다 */}
          {/* <div className="flex items-center gap-2 rounded-full bg-white/15 px-3.5 py-1.5">
            {webHasControl && (
              <span
                className="animate-lease size-1.5 rounded-full bg-white"
                style={{ "--lease-period": `${SPEC.CONTROL_RENEW_PERIOD_MS}ms` }}
                aria-hidden="true"
              />
            )}
            <span className="font-mono text-xs">
              제어권 {!controlStateKnown ? "수신 대기"
                : owner === CONTROL_OWNER.NONE ? "없음" : owner}
            </span>
          </div> */}
        </div>

        {/* 3행 — 8상태 트랙. 표시자가 칸 사이를 실제로 이동한다.
            굵은 구분선 뒤부터는 /thing/reset_safety 가 필요하다 */}
        {!safetyStateKnown && (
          <p className="mt-2 font-mono text-[11px] opacity-70">
            안전 상태 수신 대기 — 트랙에 현재 위치를 표시하지 않습니다
          </p>
        )}

        <div
          className="mt-2.5 flex gap-0.5"
          role="img"
          aria-label={`안전 상태 ${state ?? "수신 대기"}`}
        >
          {TRACK.map((name, i) => {
            const here = name === state;
            return (
              <div
                key={name}
                className={`relative flex-1 py-1 text-center
                            ${i === DEGRADED_FROM ? "ml-3" : ""}`}
              >
                <div className="absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-white/25" />
                {here && (
                  <motion.div
                    layoutId="track-marker"
                    transition={{ type: "spring", stiffness: 340, damping: 32 }}
                    className="absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-white"
                  />
                )}
                <span
                  className={`font-mono text-[10px] tracking-wider transition-opacity
                              ${here ? "opacity-100" : "opacity-45"}`}
                >
                  {name}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </header>
  );
}
