// ============================================================================
// 셸 — 모드 화면 전용
// ----------------------------------------------------------------------------
// 색으로 채운 머리 → 알림 → 작업 영역.
//
// 노트북 한 화면에 스크롤 없이 들어가야 한다. 그래서 h-screen 을 세로 flex 로
// 쓰고 작업 영역만 flex-1 로 남긴다. min-h-0 이 없으면 자식이 넘쳐 스크롤이 생긴다.
//
// 개요 화면은 이 셸을 쓰지 않는다 (App.jsx 참고).
// ============================================================================
import { Outlet, useLocation } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
import Header from "../components/Header";
// import SafetyBanner from "../components/SafetyBanner";
import { useHandSocket } from "../context/HandSocketContext";

export default function Layout() {
  const { safetyState, safetyStateKnown } = useHandSocket();
  const location = useLocation();

  return (
    <div
      className="flex h-screen flex-col overflow-hidden bg-ink-0"
      data-safety={safetyStateKnown ? safetyState.state : "INIT"}
    >
      <Header />

      <main className="mx-auto flex min-h-0 w-full max-w-[1400px] flex-1 flex-col
                       gap-3 px-6 py-4">
        {/* 알림은 있을 때만 자리를 차지한다. 없으면 높이 0 이다 */}
        {/* <SafetyBanner /> */}

        <AnimatePresence mode="popLayout" initial={false}>
          <motion.div
            key={location.pathname}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.14 }}
            className="min-h-0 flex-1"
          >
            <Outlet />
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
}
