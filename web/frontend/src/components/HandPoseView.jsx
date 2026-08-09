// ============================================================================
// 동작 미리보기 (인라인) — 조작 모드 우측, 모터 표 아래 슬롯에 그린다
// ----------------------------------------------------------------------------
// 명령 버튼에 마우스를 올리면 그 명령의 손 자세를 여기 그린다. 버튼에서 벗어나면
// 사라진다. 시퀀스(가위바위보·카운트다운)는 스텝을 자동으로 순환한다.
//
// 렌더러(HandFigure)는 GesturePreview 모달이 쓰던 것과 동일하다 — MediaPipe 21
// 랜드마크 + 그 연결 위상(BONES)을 motion 스프링으로 morph 한다. 모달은 없앴고
// 그 골격 렌더만 여기로 옮겼다.
//
// 자세 데이터·정확도 한계는 config/handPoses.js 참고.
// ============================================================================
import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { poseSteps } from "../config/handPoses";

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
const MORPH = { type: "spring", stiffness: 120, damping: 20, mass: 0.9 };
// 시퀀스 스텝 간격. hover 중 빠르게 훑어볼 수 있게 모달(2600ms)보다 짧게 둔다
const CYCLE_MS = 1200;

function HandFigure({ pts, label }) {
  return (
    <svg
      viewBox="0 0 200 260"
      preserveAspectRatio="xMidYMid meet"
      className="h-auto w-full max-w-[170px]"
      role="img"
      aria-label={`손 골격 — ${label}`}
    >
      {BONES.map(([a, b]) => (
        <motion.line
          key={`${a}-${b}`}
          animate={{ x1: pts[a][0], y1: pts[a][1], x2: pts[b][0], y2: pts[b][1] }}
          transition={MORPH}
          stroke="var(--color-ink-200)"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
      ))}
      {pts.map(([x, y], i) => (
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

export default function HandPoseView({ command }) {
  const steps = poseSteps(command);
  const [index, setIndex] = useState(0);
  const [shownId, setShownId] = useState(command?.id ?? null);

  // 다른 명령으로 옮기면 첫 스텝부터. effect 대신 렌더 중 조정(React 권장 패턴)
  if ((command?.id ?? null) !== shownId) {
    setShownId(command?.id ?? null);
    setIndex(0);
  }

  // 시퀀스는 스텝을 순환한다. 제스처(스텝 1개)는 순환하지 않는다.
  useEffect(() => {
    if (!steps || steps.length <= 1) return undefined;
    const id = setInterval(() => setIndex((i) => (i + 1) % steps.length), CYCLE_MS);
    return () => clearInterval(id);
  }, [steps]);

  // 빈 상태 — 아직 어떤 버튼에도 마우스를 올리지 않았거나, 잠긴 상태
  if (!steps) {
    return (
      <div className="flex h-full min-h-0 items-center justify-center px-4 text-center">
        <p className="text-[13px] leading-relaxed text-ink-400">
          명령 버튼에 마우스를 올리면
          <br />
          어떤 자세인지 여기에 표시됩니다.
        </p>
      </div>
    );
  }

  const step = steps[Math.min(index, steps.length - 1)];
  const isSequence = steps.length > 1;

  return (
    <div className="flex h-full min-h-0 items-center justify-center py-1">
      <div className="w-full max-w-[170px]">
        <HandFigure pts={step.pts} label={step.name} />
      </div>
    </div>
  );
}
