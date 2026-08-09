// frontend/src/ui/TabLink.jsx
//
// 활성 표시가 항목 사이를 미끄러지는 탭.
//
// layoutId 를 쓰면 활성 항목이 바뀔 때 motion 이 두 위치 사이를 자동으로
// 보간한다. 예전에는 offsetLeft·offsetWidth 를 읽어 직접 옮겼는데, 창 크기가
// 바뀌거나 폰트가 늦게 로드되면 어긋났다. 이제 레이아웃 계산이 없다.

import { NavLink } from 'react-router-dom';
import { motion } from 'motion/react';

import { EASE, prefersReducedMotion } from './motion';

export default function TabLink({ to, children }) {
  return (
    <NavLink to={to} className={({ isActive }) => (isActive ? 'nav-tab active' : 'nav-tab')}>
      {({ isActive }) => (
        <>
          {isActive && (
            <motion.span
              layoutId="nav-active-pill"
              className="nav-tab-pill"
              transition={prefersReducedMotion()
                ? { duration: 0 }
                : { type: 'spring', stiffness: 420, damping: 34 }}
            />
          )}
          <span style={{ position: 'relative' }}>{children}</span>
        </>
      )}
    </NavLink>
  );
}
