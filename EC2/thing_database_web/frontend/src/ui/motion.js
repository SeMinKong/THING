// frontend/src/ui/motion.js
//
// 모션 규칙을 한 곳에 모은다.
//
// 라이브러리는 motion(구 Framer Motion)을 쓴다. anime.js 대신 고른 이유는
// 이 화면들이 전부 React 컴포넌트이기 때문이다. anime.js 는 DOM 을 직접 잡는
// 명령형이라 ref 를 심고 useEffect 에서 요소를 찾아 정리까지 해야 한다.
// motion 은 상태에 따라 선언하면 되고, AnimatePresence·layoutId 처럼
// React 의 마운트·언마운트와 레이아웃 변화를 다루는 기능이 있다.
// 활성 탭 표시를 좌표 계산 없이 layoutId 하나로 옮길 수 있는 게 대표적이다.
//
// 규칙 두 가지
//   · 짧게. 화면 전환은 180ms 안에 끝난다. 자료를 찾으러 온 사람에게
//     애니메이션은 기다림이다.
//   · prefers-reduced-motion 이면 전부 0 으로 만든다.

export function prefersReducedMotion() {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

const reduced = prefersReducedMotion();

/** 공통 이징. CSS 의 --ease 와 같은 곡선을 쓴다 */
export const EASE = [0.2, 0.8, 0.2, 1];

/** 화면 전환 */
export const page = reduced
  ? { initial: false, animate: { opacity: 1 }, exit: { opacity: 1 } }
  : {
      initial: { opacity: 0, y: 6 },
      animate: { opacity: 1, y: 0, transition: { duration: 0.22, ease: EASE } },
      exit: { opacity: 0, y: -4, transition: { duration: 0.12, ease: EASE } },
    };

/** 목록 컨테이너 — 자식을 순서대로 들여보낸다 */
export const list = reduced
  ? {}
  : {
      animate: { transition: { staggerChildren: 0.028, delayChildren: 0.04 } },
    };

/** 목록 항목 */
export const item = reduced
  ? { initial: false, animate: { opacity: 1 } }
  : {
      initial: { opacity: 0, y: 8 },
      animate: { opacity: 1, y: 0, transition: { duration: 0.3, ease: EASE } },
    };

/** 위에서 아래로 들어오는 블록 */
export function rise(delay = 0) {
  if (reduced) return { initial: false, animate: { opacity: 1 } };
  return {
    initial: { opacity: 0, y: 10 },
    animate: { opacity: 1, y: 0, transition: { duration: 0.36, delay, ease: EASE } },
  };
}

/** SVG stroke 그리기 — pathLength 로 하므로 길이 계산이 필요 없다 */
export function draw(delay = 0, duration = 1.15) {
  if (reduced) return { initial: false, animate: { pathLength: 1, opacity: 1 } };
  return {
    initial: { pathLength: 0, opacity: 0 },
    animate: {
      pathLength: 1,
      opacity: 1,
      transition: {
        pathLength: { duration, delay, ease: [0.4, 0, 0.2, 1] },
        opacity: { duration: 0.2, delay },
      },
    },
  };
}

/** 가로 막대가 왼쪽에서 자라난다 */
export function grow(ratio, delay = 0) {
  const safe = Number.isFinite(ratio) ? Math.max(0.02, Math.min(1, ratio)) : 0.02;
  if (reduced) return { initial: false, animate: { scaleX: safe } };
  return {
    initial: { scaleX: 0 },
    animate: { scaleX: safe, transition: { duration: 0.5, delay, ease: EASE } },
  };
}
