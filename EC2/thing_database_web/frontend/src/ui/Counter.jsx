// frontend/src/ui/Counter.jsx
//
// 숫자를 0 에서 목표까지 세어 올린다.
//
// 왜 하는가: 이 화면의 숫자는 전부 "측정된 양"이다. 세어 올라가면 그것이
// 고정된 라벨이 아니라 계측 결과라는 인상이 남는다. 자릿수가 큰 값일수록 효과가
// 크다. 대신 300ms 안에 끝내서 읽기를 방해하지 않는다.
//
// motion 의 useMotionValue + animate 를 쓴다. 리렌더를 일으키지 않고 DOM 의
// textContent 만 갱신하므로 행이 많아도 부담이 없다.

import { useEffect, useRef } from 'react';
import { animate, useMotionValue } from 'motion/react';

import { EASE, prefersReducedMotion } from './motion';

export default function Counter({ value, format = (n) => String(Math.round(n)), delay = 0 }) {
  const ref = useRef(null);
  const mv = useMotionValue(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    // 값이 숫자가 아니면(측정되지 않음) 애니메이션 대상이 아니다.
    if (!Number.isFinite(value)) {
      el.textContent = format(value);
      return undefined;
    }
    if (prefersReducedMotion()) {
      el.textContent = format(value);
      return undefined;
    }
    const controls = animate(mv, value, {
      duration: 0.62,
      delay,
      ease: EASE,
      onUpdate: (n) => { el.textContent = format(n); },
      onComplete: () => { el.textContent = format(value); },
    });
    return () => controls.stop();
  }, [value, format, delay, mv]);

  // 초기 렌더에 최종 값을 넣어 둔다.
  // 스크립트가 멈추거나 서버 렌더 결과만 보이는 경우에도 숫자가 비지 않는다.
  return <span ref={ref}>{format(value)}</span>;
}
