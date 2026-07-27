# Web

웹은 프로젝트 내부망에서만 제공하며 모터를 직접 제어하지 않습니다.

- `frontend/`: Vite + React 관제 UI
- `backend/`: Django, WebSocket, SQLite 세션 API

모든 명령은 Django/WebSocket에서 검증한 뒤 `thing_web_bridge`를 거쳐 ROS 2
서비스·액션·토픽으로 전달합니다.

필수 화면:

- MIMIC: MJPEG, landmark, 7논리축, 녹화와 판정
- MANUAL: 사전 정의 Gesture, Sequence, STOP과 초기 자세
- 공통: 연결, 모터, 제어 모드, 안전 및 오류 상태

WebSocket 계약은 `docs/interfaces.md`를 기준으로 관리합니다.
