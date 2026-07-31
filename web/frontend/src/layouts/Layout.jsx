import { NavLink, Outlet } from "react-router-dom";
import StatusBar from "../components/StatusBar";
import SafetyBanner from "../components/SafetyBanner";

// base.html의 nav/footer 구조를 그대로 이식.
// Django의 {% block content %}는 <Outlet /> 이 대신한다.
export default function Layout() {
  return (
    <div className="d-flex flex-column min-vh-100">
      {/* 중앙 정렬된 네비게이션 바 */}
      <nav className="navbar navbar-expand-lg navbar-light bg-white border-bottom py-3">
        <div className="container-fluid">
          <div className="navbar-collapse justify-content-center">
            <ul className="navbar-nav gap-4 fs-5 fw-semibold">
              <li className="nav-item">
                <NavLink
                  className={({ isActive }) =>
                    "nav-link" + (isActive ? " active fw-bold" : "")
                  }
                  to="/"
                  end
                >
                  홈
                </NavLink>
              </li>
              <li className="nav-item">
                <NavLink
                  className={({ isActive }) =>
                    "nav-link" + (isActive ? " active fw-bold" : "")
                  }
                  to="/vision"
                >
                  손 모방 페이지
                </NavLink>
              </li>
              <li className="nav-item">
                <NavLink
                  className={({ isActive }) =>
                    "nav-link" + (isActive ? " active fw-bold" : "")
                  }
                  to="/order"
                >
                  명령 제공 페이지
                </NavLink>
              </li>
            </ul>
          </div>
        </div>
      </nav>

      {/* FR-24: 현재 모드/연결 상태/안전 상태를 화면 상단에 고정 표시 */}
      <StatusBar />

      {/* FR-27: 오류/안전 상태 안내 - 모든 페이지 공통으로 노출 */}
      <SafetyBanner />

      {/* 본문 콘텐츠 영역 - 각 페이지가 여기 렌더링됨 */}
      <main className="flex-grow-1">
        <Outlet />
      </main>

      {/* 푸터 */}
      <footer className="py-4 bg-white border-top text-center text-muted mt-5">
        <small>&copy; Tendon-driven robot Hand with Intelligent Neural Grasping - THING<br></br>All rights reserved.</small>
      </footer>
    </div>
  );
}
