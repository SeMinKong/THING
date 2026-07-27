# 3주 MVP 프로젝트 계획

최종 범위는 `docs/requirements/요구사항 명세서 V5.md`를 기준으로 합니다.
일정이 충돌할 경우 실물 안전 구동과 end-to-end MIMIC을 우선합니다.

## Week 1 — 안전한 7축 구동 기반

- 최종 V5, ROS 2 메시지, 토픽과 장치별 책임 확정
- 7개 XL-330 ID·U2D2 포트·회전 방향·허용 범위 정의
- 한 축 안전 구동 후 7축 sync read/write 검증
- 물리적 비상정지와 모터 전원 차단 시험
- RGB 카메라 공통 입력과 HandLandmarks 발행

완료 기준:

- 시작 시 저속으로 초기 자세에 진입한다.
- 잘못된 목표가 모터에 전달되기 전에 제한된다.
- 카메라 연결 실패와 모터 통신 실패를 구분할 수 있다.
- 21개 landmark가 정해진 메시지 형식으로 발행된다.

## Week 2 — MIMIC·MANUAL 수직 통합

- 5개 손가락 굽힘과 엄지 대립·벌림 계산
- 필터, deadband, 변화율 및 속도 제한
- command manager, command guard와 safety manager 구현
- MIMIC·MANUAL·TELEOP 제어권 분리
- React·Django 기본 관제와 Web Bridge 연동
- 편 손, 주먹, 원통 파지와 집기 자세 튜닝

완료 기준:

- 7논리축이 20Hz 이상 발행되고 7개 모터가 부드럽게 추종한다.
- 일반 명령 충돌과 위험 상태의 명령이 거부된다.
- 웹에서 영상, 7축 명령, 모터 및 안전 상태를 확인한다.

## Week 3 — 기록·시험·시연 안정화

- rosbag2와 SQLite Session 기록
- 녹화 중 모드 전환 금지와 판정 대기 상태 구현
- 통신 timeout, HOLD, SAFE, FAULT와 복구 절차 시험
- 원통 파지와 집기 각각 10회 반복 시험
- bringup, 설치 문서, CI와 최종 시연 절차 정리

완료 기준:

- 지정 물체를 3초 이상 유지하고 10회 중 7회 이상 성공한다.
- landmark, 7축 HandCommand와 7개 MotorStatus를 재생·분석할 수 있다.
- 통신 복구 후 사용자 승인 없이 자동 재동작하지 않는다.
- 새 장치에서 문서만으로 빌드와 bringup을 재현할 수 있다.

## MVP 제외

- Isaac Sim/Lab, VLA, imitation learning
- 관절별 독립 다축 제어와 전체 촉각 센서
- 외부 인터넷 원격제어
- 축–모터 공유 구동
