# Backend

Django 애플리케이션 위치입니다. 구현 MR에서는 다음을 포함해야 합니다.

- EC2 host와 HTTPS 환경변수 설정
- 인증된 로봇 데이터 업로드 API
- 모터·각도·상태·세션 조회 API
- 데이터 파일 다운로드 API
- DB migration과 테스트
- `.env.example`

EC2 백엔드는 로봇 명령 API나 ROS 2 DDS를 외부에 노출하지 않습니다. 웹
프로세스 장애가 Raspberry Pi의 모터 안전 제어에 영향을 주어서는 안 됩니다.
