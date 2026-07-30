// frontend/src/views/HomeView.jsx
import { Link } from 'react-router-dom';

export default function HomeView() {
  return (
    <div className="page-container center">
      <div className="card main-card">
        <h1>🤖 로봇 원격 데이터 센터</h1>
        <p>
          텐던 구동 로봇 핸드의 완료 세션을 조회하고 metadata JSON,
          HandCommand CSV, MotorStatus CSV를 다운로드합니다.
        </p>
        <p className="subtle small">
          로그인 없이 조회할 수 있으며, 모든 시각은 UTC로 표시됩니다.
        </p>
        <div className="btn-group">
          <Link to="/sessions" className="btn btn-primary">
            세션 목록 보기
          </Link>
        </div>
      </div>
    </div>
  );
}
