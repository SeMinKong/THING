# Backend

Django 애플리케이션 위치입니다. 구현 MR에서는 다음을 포함해야 합니다.

- 내부망 host 제한과 환경변수 설정
- WebSocket 연결과 요청/응답 `request_id`
- 세션·성공/실패 판정 API
- ROS 2 Web Bridge 연결 상태
- SQLite migration과 테스트
- `.env.example`

웹 프로세스 장애가 Raspberry Pi의 모터 안전 제어에 영향을 주어서는 안 됩니다.
