# 남은 2주 MVP 프로젝트 계획

최종 범위는 `docs/requirements/요구사항 명세서 V6.3.md`를 기준으로 합니다.
일정이 충돌하면 실물 안전 구동, end-to-end MIMIC, 세 파일 업로드와 최소 공개
조회를 세부 UI·성능 개선보다 우선합니다.

## Week 1 — 로봇 수직 통합과 데이터 생성

- 7개 XL-330 ID·U2D2 포트·회전 방향·허용 범위 정의
- Vision 7논리축과 command manager·guard·safety manager 연결
- HOLD·SAFE·FAULT·ESTOP, STOP과 Safety Reset 복구 시험
- Laptop 내부 제어 웹과 Jetson WebSocket·MJPEG 연결
- rosbag2 기록·판정과 정확히 세 파일 exporter 구현

완료 기준:

- Vision→ROS 2→7개 모터가 제한 안에서 동작합니다.
- 위험 명령이 차단되고 Reset 뒤 이전 명령이 자동 재개되지 않습니다.
- 내부망 웹에서 영상·모터·기록·안전 상태를 확인합니다.
- 대표 rosbag2에서 V6.3 schema의 세 파일을 생성합니다.

## Week 2 — EC2 최소 포털과 통합 검수

- 장치 Bearer Token, 멱등성, 세 파일 검증과 STAGING→READY 구현
- 공개 목록·상세·기본 시계열과 세 파일 다운로드 구현
- Nginx HTTPS, Gunicorn, Django, systemd와 EC2 영속 저장 검증
- 수동 `reupload_session`과 10분 연속동작 시험
- bringup·배포·운영 문서와 로컬 검증 절차 정리

완료 기준:

- READY 세션만 공개되고 DB·파일 schema·hash가 일치합니다.
- EC2 재부팅 뒤 목록·상세·다운로드가 유지됩니다.
- EC2 장애가 로봇 제어·안전을 중단시키지 않습니다.
- 문서화된 Laptop·Jetson·Pi·EC2 기동 절차를 재현합니다.

## MVP 제외

- Isaac Sim/Lab, VLA, imitation learning
- 관절별 독립 다축 제어와 전체 촉각 센서
- 외부 인터넷 원격제어
- 축–모터 공유 구동
- 로봇 애플리케이션 SQLite와 영구 upload spool
- EC2 rosbag2 다운로드, S3와 다중 로봇 운영
- GitLab CI/CD와 자동 rollback
