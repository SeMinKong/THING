// frontend/src/App.jsx
import { Suspense, lazy } from 'react';
import { Link, Route, Routes, useLocation } from 'react-router-dom';
import { AnimatePresence, motion } from 'motion/react';
import { Waves } from 'lucide-react';

import Skeleton from './ui/Skeleton.jsx';
import TabLink from './ui/TabLink.jsx';
import { page } from './ui/motion';
import HomeView from './views/HomeView.jsx';
import SessionListView from './views/SessionListView.jsx';

// 상세 화면만 recharts 를 쓴다. lazy 로 분리해 목록·홈의 초기 로드에서 제외한다.
// 이렇게 하지 않으면 차트 라이브러리가 전체 번들에 포함되어 첫 진입이 무거워진다.
const SessionDetailView = lazy(() => import('./views/SessionDetailView.jsx'));

function RouteFallback() {
  return (
    <div className="sheet">
      <div className="panel"><Skeleton rows={4} label="화면을 준비하고 있습니다…" /></div>
    </div>
  );
}

function Masthead() {
  return (
    <header className="masthead">
      <Link to="/" className="brand">
        <span className="brand-glyph" aria-hidden="true">
          <Waves size={19} strokeWidth={1.9} />
        </span>
        <span className="brand-text">
          <span className="brand-name">RobotData</span>
          <span className="brand-note">텐던 핸드 계측 아카이브</span>
        </span>
      </Link>

      <nav className="masthead-nav">
        <TabLink to="/sessions">세션 목록</TabLink>
      </nav>
    </header>
  );
}

export default function App() {
  const location = useLocation();

  return (
    <div id="app-layout">
      <Masthead />

      {/*
        화면 전환에 짧은 크로스페이드를 둔다. 자료를 찾으러 온 사람에게
        애니메이션은 기다림이므로 220ms 안에 끝낸다.
        mode="wait" 로 두 화면이 겹쳐 보이지 않게 한다.
      */}
      <AnimatePresence mode="wait" initial={false}>
        <motion.div key={location.pathname} {...page} style={{ display: 'contents' }}>
          <Suspense fallback={<RouteFallback />}>
            <Routes location={location}>
              <Route path="/" element={<HomeView />} />
              <Route path="/sessions" element={<SessionListView />} />
              <Route path="/sessions/:sessionId" element={<SessionDetailView />} />
              {/* 정의되지 않은 경로는 홈으로 폴백 */}
              <Route path="*" element={<HomeView />} />
            </Routes>
          </Suspense>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
