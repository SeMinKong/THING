// ============================================================================
// 개요 — 진입 화면
// ----------------------------------------------------------------------------
// 여기서 할 일은 하나다. 어느 모드로 들어갈지 고르는 것.
// 로봇 상태는 머리가 이미 답하고 있으므로 되풀이하지 않는다.
// ============================================================================
import { Link } from "react-router-dom";
import { motion } from "motion/react";
import { useHandSocket } from "../context/HandSocketContext";
import { CONTROL_MODE } from "../config/messageProtocol";

const ENTRIES = [
  { to: "/vision", mode: CONTROL_MODE.MIMIC, name: "모방" },
  { to: "/order", mode: CONTROL_MODE.MANUAL, name: "조작" },
];

export default function Home() {
  const { controlState, controlStateKnown } = useHandSocket();

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {ENTRIES.map((entry, i) => {
        const active = controlStateKnown && controlState.active_mode === entry.mode;
        return (
          <motion.div
            key={entry.to}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.32, delay: i * 0.06, ease: [0.2, 0, 0.1, 1] }}
            whileHover={{ y: -3 }}
          >
            <Link
              to={entry.to}
              className={`relative flex items-center justify-center overflow-hidden
                          rounded-card py-24 transition-colors
                          ${active ? "bg-[var(--signal)]/10" : "bg-ink-50 hover:bg-ink-100"}`}
            >
              <span className="text-[28px] font-bold tracking-[-0.02em]">{entry.name}</span>
              {active && (
                <span className="absolute right-5 top-5 rounded-full bg-[var(--signal)]
                                 px-2.5 py-0.5 font-mono text-[11px] font-medium text-white">
                  활성
                </span>
              )}
            </Link>
          </motion.div>
        );
      })}
    </div>
  );
}
