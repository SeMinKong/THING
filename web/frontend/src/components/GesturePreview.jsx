// ============================================================================
// 동작 미리보기 — canonical gesture 4종을 손 골격으로 보여 주는 모달
// ----------------------------------------------------------------------------
// 왜 필요한가
//   조작 모드의 버튼 이름만으로는 "원통 파지" 와 "집기" 가 어떻게 다른지 알기
//   어렵다. 로봇에 보내기 전에 무슨 자세인지 확인할 수 있어야 한다.
//
// 무엇을 그리는가
//   MediaPipe 21 랜드마크와 그 연결 위상을 그대로 쓴다. HandLandmarks.msg 의
//   landmarks[21] 과 같은 순서다. 장식이 아니라 이 시스템이 다루는 대상 자체다.
//
// 7논리축 막대는 자세와 함께 움직인다. 다만 그 값은 화면 설명용 근사치다.
// 실제 목표값은 FR-41 이 YAML 소관으로 두었고 아직 확정되지 않았다
// (pending.js 의 GESTURE_SPEED_LIMIT 와 같은 처지). 그래서 숫자를 쓰지 않고
// 막대 길이로만 보여 주고, 근사치임을 화면에 밝힌다.
//
// 모달 구현
//   포털 + 배경 클릭·Esc 닫기 + Tab 순환 가둠 + 닫을 때 원래 버튼으로 초점 복귀.
//   경로를 새로 만들지 않으므로 뒤로 가기 동작이 바뀌지 않는다.
// ============================================================================
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "motion/react";
import { CANONICAL_GESTURES } from "../config/messageProtocol";

// MediaPipe 위상. 0 손목, 1-4 엄지, 5-8 검지, 9-12 중지, 13-16 약지, 17-20 소지
const BONES = [
  [0, 1], [1, 2], [2, 3], [3, 4],
  [0, 5], [5, 6], [6, 7], [7, 8],
  [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16],
  [13, 17], [17, 18], [18, 19], [19, 20],
  [0, 17],
];
const TIPS = new Set([4, 8, 12, 16, 20]);

/** 자세별 21 랜드마크. 손바닥을 정면으로 본 2D 투영이다. */
const POSES = [
  {
    id: "open", name: "편 손",
    axes: [0.02, 0.05, 0.88, 0.03, 0.03, 0.03, 0.03],
    pts: [[100, 232], [74, 208], [50, 184], [32, 164], [18, 146],
      [78, 158], [72, 122], [68, 98], [64, 76],
      [101, 150], [100, 110], [99, 84], [98, 60],
      [124, 156], [129, 118], [132, 94], [134, 72],
      [146, 170], [156, 140], [161, 120], [165, 102]],
  },
  {
    id: "fist", name: "주먹",
    axes: [0.97, 0.32, 0.08, 0.98, 0.98, 0.97, 0.96],
    pts: [[100, 232], [74, 208], [56, 190], [58, 170], [74, 164],
      [78, 158], [76, 130], [88, 122], [92, 140],
      [101, 150], [101, 122], [111, 116], [109, 136],
      [124, 156], [126, 128], [134, 124], [130, 144],
      [146, 170], [150, 146], [156, 144], [150, 160]],
  },
  {
    id: "pinch", name: "집기",
    axes: [0.58, 0.92, 0.18, 0.71, 0.04, 0.04, 0.04],
    pts: [[100, 232], [74, 208], [54, 186], [52, 158], [64, 132],
      [78, 158], [72, 124], [68, 108], [68, 126],
      [101, 150], [100, 116], [104, 94], [104, 74],
      [124, 156], [129, 120], [132, 98], [134, 78],
      [146, 170], [154, 140], [158, 120], [162, 104]],
  },
  {
    id: "cylindrical_grasp", name: "원통 파지",
    axes: [0.70, 0.81, 0.30, 0.66, 0.66, 0.65, 0.64],
    pts: [[100, 232], [74, 208], [52, 188], [46, 164], [60, 150],
      [78, 158], [70, 128], [66, 108], [78, 114],
      [101, 150], [99, 118], [97, 98], [106, 106],
      [124, 156], [128, 122], [130, 102], [121, 110],
      [146, 170], [152, 138], [154, 120], [145, 128]],
  },
];

const AXIS_NAMES = [
  "thumb_flex", "thumb_opp", "thumb_abd",
  "index_flex", "middle_flex", "ring_flex", "little_flex",
];

const CYCLE_MS = 2600;
const MORPH = { type: "spring", stiffness: 120, damping: 20, mass: 0.9 };

function HandFigure({ pose }) {
  return (
    <svg viewBox="0 0 200 260" className="w-full max-w-[240px]" role="img"
         aria-label={`손 골격 — ${pose.name}`}>
      {BONES.map(([a, b]) => (
        <motion.line
          key={`${a}-${b}`}
          animate={{
            x1: pose.pts[a][0], y1: pose.pts[a][1],
            x2: pose.pts[b][0], y2: pose.pts[b][1],
          }}
          transition={MORPH}
          stroke="var(--color-ink-200)"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
      ))}
      {pose.pts.map(([x, y], i) => (
        <motion.circle
          key={i}
          animate={{ cx: x, cy: y }}
          transition={MORPH}
          r={i === 0 ? 5 : TIPS.has(i) ? 4.5 : 3}
          fill={i === 0 ? "var(--color-ink-900)" : "var(--signal, var(--color-st-run))"}
        />
      ))}
    </svg>
  );
}

function Dialog({ onClose }) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(true);
  const panelRef = useRef(null);

  // 자동 순환. 사용자가 자세를 고르면 멈춘다 — 보려던 것이 지나가 버리면 안 된다.
  useEffect(() => {
    if (!playing) return undefined;
    const id = setInterval(() => setIndex((i) => (i + 1) % POSES.length), CYCLE_MS);
    return () => clearInterval(id);
  }, [playing]);

  // Esc 닫기 + Tab 을 모달 안에 가둔다
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") { onClose(); return; }
      if (e.key !== "Tab") return;
      const focusable = panelRef.current?.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    panelRef.current?.querySelector("button")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const pose = POSES[index];

  return (
    <motion.div
      className="fixed inset-0 z-50 grid place-items-center bg-ink-900/45 p-4"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <motion.div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="동작 미리보기"
        initial={{ opacity: 0, y: 16, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 8, scale: 0.99 }}
        transition={{ type: "spring", stiffness: 340, damping: 32 }}
        className="w-full max-w-[720px] overflow-hidden rounded-card bg-ink-0 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4 px-6 pt-5">
          <div>
            <h2 className="text-lg font-bold tracking-[-0.01em]">동작 미리보기</h2>
            <p className="mt-0.5 text-[13px] text-ink-400">
              로봇에 보내기 전에 어떤 자세인지 확인합니다.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            className="rounded-full bg-ink-100 px-3 py-1 text-[13px] font-medium
                       transition-colors hover:bg-ink-200"
          >
            닫기
          </button>
        </div>

        <div className="grid gap-6 px-6 py-5 sm:grid-cols-[240px_1fr]">
          <div className="justify-self-center">
            <HandFigure pose={pose} />
          </div>

          <div className="min-w-0">
            {/* 자세 선택. 고르면 자동 순환이 멈춘다 */}
            <div className="flex flex-wrap gap-1.5">
              {POSES.map((p, i) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => { setIndex(i); setPlaying(false); }}
                  aria-pressed={i === index}
                  className="relative rounded-full px-3 py-1 text-[13px] font-medium
                             transition-colors"
                >
                  {i === index && (
                    <motion.span
                      layoutId="preview-pill"
                      transition={{ type: "spring", stiffness: 460, damping: 38 }}
                      className="absolute inset-0 rounded-full bg-ink-900"
                    />
                  )}
                  <span className={`relative ${i === index ? "text-white" : "text-ink-600"}`}>
                    {p.name}
                  </span>
                </button>
              ))}
            </div>

            {/* ExecuteGesture.srv 가 실제로 받는 이름. 화면 이름과 다르다 */}
            <p className="mt-3 font-mono text-xs text-ink-400">
              gesture_name: <span className="text-ink-600">{pose.id}</span>
            </p>

            <div className="mt-5 flex flex-col gap-2">
              {AXIS_NAMES.map((name, i) => (
                <div key={name} className="grid grid-cols-[6.5rem_1fr] items-center gap-3">
                  <span className="font-mono text-[11px] text-ink-400">{name}</span>
                  <span className="h-1 overflow-hidden rounded-full bg-ink-100">
                    <motion.span
                      className="block h-full rounded-full bg-[var(--signal,var(--color-st-run))]"
                      animate={{ width: `${pose.axes[i] * 100}%` }}
                      transition={MORPH}
                    />
                  </span>
                </div>
              ))}
            </div>

            <div className="mt-5 flex items-center gap-2">
              <button
                type="button"
                onClick={() => setPlaying((v) => !v)}
                className="rounded-full bg-ink-100 px-3.5 py-1 text-[13px] font-medium
                           transition-colors hover:bg-ink-200"
              >
                {playing ? "자동 순환 멈춤" : "자동 순환"}
              </button>
            </div>
          </div>
        </div>

        <p className="bg-ink-50 px-6 py-3 text-xs leading-relaxed text-ink-400">
          막대는 자세를 설명하기 위한 개략도입니다. 실제 7논리축 목표값은 FR-41 에 따라
          YAML 이 관리하며 아직 확정되지 않았습니다. 이 화면은 로봇에 아무것도 보내지 않습니다.
        </p>
      </motion.div>
    </motion.div>
  );
}

export default function GesturePreview() {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef(null);

  // 닫을 때 열었던 버튼으로 초점을 돌려준다
  const close = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus();
  }, []);

  // 열려 있는 동안 뒤 화면이 스크롤되지 않게 한다
  useEffect(() => {
    if (!open) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-full bg-ink-100 px-3 py-0.5 text-xs font-medium text-ink-600
                   transition-colors hover:bg-ink-200"
      >
        미리보기
      </button>

      {createPortal(
        <AnimatePresence>{open && <Dialog onClose={close} />}</AnimatePresence>,
        document.body,
      )}
    </>
  );
}

// 화면 이름과 계약 이름이 어긋나면 조용히 실패한다. 개발 중에 잡는다.
if (import.meta.env?.DEV) {
  const unknown = POSES.map((p) => p.id).filter((id) => !CANONICAL_GESTURES.includes(id));
  if (unknown.length > 0) {
    console.warn("[미리보기] canonical 이 아닌 gesture:", unknown);
  }
}
