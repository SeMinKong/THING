# Internal Control Frontend

Laptop에서 native Node.js로 실행하는 Vite+React 내부망 관제·제어 웹의
위치입니다.

- MIMIC·MANUAL mode와 owner 획득·해제
- Jetson MJPEG 카메라 overlay
- ControlState·SafetyState·RecordingState와 모터 상태 표시
- STOP, Gesture, Sequence와 recording 요청
- Jetson `thing_web_bridge` WebSocket 연결

구현 MR에서는 `VITE_WS_URL`, `VITE_MJPEG_URL`을 외부 설정으로 제공하고 다음을
포함해야 합니다.

- lint, test와 production build 명령
- WebSocket 계약과 오류·재연결 시험
- SAFE·FAULT·ESTOP 상태에서 일반 명령 차단 표시

이 웹은 EC2 SQLite·rosbag2를 직접 읽지 않으며 외부 인터넷에 배포하지 않습니다.
EC2 조회·다운로드 포털은 `EC2/thing_database_web`에서 별도로 개발합니다.
