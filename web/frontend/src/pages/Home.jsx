// ============================================================================
// 개요 — 진입 화면
// ----------------------------------------------------------------------------
// 셸 밖에서 도는 유일한 화면이다. 상태 머리도 안전 알림도 없다.
// 여기서 할 일은 하나다. 어느 모드로 들어갈지 고르는 것.
//
// 버튼은 곧바로 이동하지 않는다. 제어권 게이트가 모달을 띄우고, 획득이 끝나면
// 화면이 함께 넘어간다 (components/ModeGate.jsx).
// ============================================================================
import { motion } from "motion/react";
import { useHandSocket } from "../context/HandSocketContext";
import { useModeGate } from "../components/ModeGate";
import { CONTROL_MODE } from "../config/messageProtocol";

const ENTRIES = [
  { to: "/vision", mode: CONTROL_MODE.MIMIC, name: "모방", line: "손을 따라 움직입니다" },
  { to: "/order", mode: CONTROL_MODE.MANUAL, name: "조작", line: "버튼으로 동작을 보냅니다" },
];

export default function Home() {
  const { controlState, controlStateKnown } = useHandSocket();
  const { go } = useModeGate();

  return (
    <div className="grid min-h-screen place-items-center bg-ink-0 px-6 py-10">
      <div className="w-full max-w-[820px]">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, ease: [0.2, 0, 0.1, 1] }}
        >
          <p className="font-mono text-[24px] font-semibold tracking-tight">THING</p>
          <h1 className="mt-2 text-[36px] font-bold leading-tight tracking-[-0.02em]">
            로봇 손 관제
          </h1>
<p className="mt-2 text-[15px] leading-relaxed text-ink-600">
  <strong className="text-lg">T</strong>endon-driven robot <strong className="text-lg">H</strong>and with <strong className="text-lg">I</strong>ntelligent <strong className="text-lg">N</strong>eural <strong className="text-lg">G</strong>rasp
</p>
        </motion.div>

        <div className="mt-8 grid gap-4 sm:grid-cols-2">
          {ENTRIES.map((entry, i) => {
            const active = controlStateKnown && controlState.active_mode === entry.mode;
            return (
              <motion.button
                key={entry.to}
                type="button"
                onClick={() => go(entry.to)}
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.32, delay: 0.06 + i * 0.06,
                             ease: [0.2, 0, 0.1, 1] }}
                whileHover={{ y: -3 }}
                whileTap={{ scale: 0.99 }}
                className={`relative flex flex-col items-center justify-center gap-1.5
                            overflow-hidden rounded-card py-20 transition-colors
                            ${active
                              ? "bg-[var(--signal,var(--color-st-run))]/10"
                              : "bg-ink-50 hover:bg-ink-100"}`}
              >
                <span className="text-[48px] font-bold tracking-[-0.02em]">{entry.name}</span>
                <span className="text-[23px] text-ink-600">{entry.line}</span>
                {active && (
                  <span className="absolute right-5 top-5 rounded-full
                                   bg-[var(--signal,var(--color-st-run))] px-2.5 py-0.5
                                   font-mono text-[11px] font-medium text-white">
                    활성
                  </span>
                )}
              </motion.button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
