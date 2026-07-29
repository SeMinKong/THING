# Web

웹 기능은 배포 위치에 따라 다음 두 경계로 분리합니다.

- EC2 공개 웹: 로봇 상태·모터·각도·세션 데이터 조회와 데이터 파일 다운로드
- Laptop 내부망 UI: 실시간 영상·상태 확인과 허용된 로봇 제어

EC2 공개 웹에서는 ROS 2 DDS, 모터 명령 API, WebSocket 제어 채널을 노출하지
않습니다. Raspberry Pi의 격리 uploader가 완료된 세 파일을 인증된 HTTPS 요청으로
업로드하고, EC2는 READY 상태의 저장 데이터를 조회·다운로드하는 역할만 담당합니다.

- `internal-control/frontend/`: Laptop에서 native로 실행하는 Vite+React 제어 UI
- `../EC2/thing_database_web/`: EC2 공개 포털 frontend·backend·deploy 단일 원본
- `backend/`: V6.3 이전 placeholder이며 새 EC2 코드를 구현하지 않음

내부망 제어 WebSocket 계약은 `docs/interfaces.md`를 기준으로 관리하며 EC2 공개
포털과 코드·실행 위치·환경변수를 분리합니다.
