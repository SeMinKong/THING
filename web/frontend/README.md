# Frontend

Vite + React 애플리케이션 위치입니다. 프로젝트 생성 MR에서는 다음을 함께
포함해야 합니다.

- 로봇 상태·모터·각도 조회 화면
- 세션 목록과 데이터 다운로드 화면
- 조회 API 오류·재시도 처리
- `.env.example`
- lint, test와 production build 명령
- ROS 메시지 필드와 분리된 UI 모델

원격 모터 제어 UI는 EC2 배포에 포함하지 않습니다. 토큰, 장치 IP와 비밀번호는
소스에 포함하지 않습니다.
