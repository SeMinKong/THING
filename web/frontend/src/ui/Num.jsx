// ============================================================================
// 계측값
// ----------------------------------------------------------------------------
// 값이 툭툭 갈아 끼워지면 5Hz 로 들어오는 데이터가 깜빡이는 것처럼 보인다.
// spring 으로 이어 주면 모터가 실제로 움직이는 것처럼 읽힌다.
//
// 다만 값이 없을 때(-)는 보간하지 않는다. 0 으로 미끄러지면 "0 을 관측했다" 로
// 오해된다. FR-24 의 "가짜 값으로 채우지 않는다" 와 같은 이유다.
// ============================================================================
import { useEffect } from "react";
import { motion, useSpring, useTransform } from "motion/react";

export default function Num({ value, digits = 2 }) {
  const known = typeof value === "number" && Number.isFinite(value);
  const spring = useSpring(known ? value : 0, { stiffness: 210, damping: 28, mass: 0.5 });
  const text = useTransform(spring, (v) => v.toFixed(digits));

  useEffect(() => { if (known) spring.set(value); }, [known, value, spring]);

  if (!known) return <span className="text-ink-300">-</span>;
  return <motion.span>{text}</motion.span>;
}
