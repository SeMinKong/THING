// ============================================================================
// 구획
// ----------------------------------------------------------------------------
// 테두리를 쓰지 않는다. 흰 바탕 위에 옅은 면으로만 나눈다.
// 상자가 많으면 테두리가 정보보다 먼저 눈에 들어온다.
// ============================================================================
import { motion } from "motion/react";

/** 구획 하나. 마운트할 때 순서대로 올라온다 */
export function Panel({ children, className = "", delay = 0 }) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay, ease: [0.2, 0, 0.1, 1] }}
      className={`rounded-card bg-ink-50 ${className}`}
    >
      {children}
    </motion.section>
  );
}

/** 구획 머리. 라벨은 읽으라고 있는 글자다 — 10px 대문자로 줄이지 않는다 */
export function Head({ title, afterTitle, children }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 px-5 pb-1 pt-4">
      <div className="flex items-center gap-2">
        <h2 className="text-[13px] font-semibold text-ink-600">{title}</h2>
        {afterTitle}
      </div>
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  );
}

export function Body({ children, className = "" }) {
  return <div className={`px-5 pb-5 pt-3 ${className}`}>{children}</div>;
}

const TONE = {
  ok: "bg-st-ready/12 text-st-ready",
  warn: "bg-st-hold/12 text-st-hold",
  bad: "bg-st-fault/12 text-st-fault",
  idle: "bg-ink-200/60 text-ink-600",
  live: "bg-[var(--signal)]/12 text-[var(--signal)]",
};

/** 상태 표시. 테두리 없이 옅은 면으로 */
export function Tag({ tone = "idle", children, ...rest }) {
  return (
    <span className={`whitespace-nowrap rounded-full px-2.5 py-0.5 font-mono text-[11px] font-medium                      ${TONE[tone]}`} {...rest}>
      {children}
    </span>
  );
}
