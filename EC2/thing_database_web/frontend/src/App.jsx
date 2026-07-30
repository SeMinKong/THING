// frontend/src/App.jsx
import { Suspense, lazy } from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';

import HomeView from './views/HomeView.jsx';
import SessionListView from './views/SessionListView.jsx';

// 상세 화면만 recharts 를 쓴다. lazy 로 분리해 목록·홈의 초기 로드에서 제외한다.
// 이렇게 하지 않으면 차트 라이브러리가 전체 번들에 포함되어 첫 진입이 무거워진다.
const SessionDetailView = lazy(() => import('./views/SessionDetailView.jsx'));

function RouteFallback() {
  return (
    <div className="page-container wide">
      <div className="state-box card"><p>화면을 준비하고 있습니다…</p></div>
    </div>
  );
}

export default function App() {
  return (
    <div id="app-layout">
      <nav className="navbar">
        <NavLink to="/" className="nav-brand">
          🤖 RobotData-EC2
        </NavLink>

        <div className="nav-links">
          <NavLink
            to="/sessions"
            className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}
          >
            세션 목록
          </NavLink>
        </div>
      </nav>

      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/" element={<HomeView />} />
          <Route path="/sessions" element={<SessionListView />} />
          <Route path="/sessions/:sessionId" element={<SessionDetailView />} />
          {/* 정의되지 않은 경로는 홈으로 폴백 */}
          <Route path="*" element={<HomeView />} />
        </Routes>
      </Suspense>
    </div>
  );
}
