// ============================================================================
// 셸
// ----------------------------------------------------------------------------
// 색으로 채운 머리 → 알림 → 작업 영역.
//
// 화면 전환 시 영상이 사라졌다 다시 뜨지 않는다. 모방과 조작 둘 다 영상을
// 쓰는데, 전환할 때마다 MJPEG 연결이 끊기면 몇 프레임을 놓친다.
// layoutId 로 같은 요소임을 알려 자리만 옮긴다.
// ============================================================================
import { Outlet, useLocation } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
import Header from "../components/Header";
import SafetyBanner from "../components/SafetyBanner";
import { useHandSocket } from "../context/HandSocketContext";

export default function Layout() {
  const { safetyState, safetyStateKnown } = useHandSocket();
  const location = useLocation();

  return (
    <div
      className="min-h-screen bg-ink-0"
      data-safety={safetyStateKnown ? safetyState.state : "INIT"}
    >
      <Header />

      <main className="mx-auto flex max-w-[1400px] flex-col gap-4 px-6 py-6">
        <SafetyBanner />

        <AnimatePresence mode="popLayout" initial={false}>
          <motion.div
            key={location.pathname}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.14 }}
          >
            <Outlet />
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
}
