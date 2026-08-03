# Tests

이 디렉터리는 `procedures/`의 재현 가능한 시험 절차와 `results/`의 측정 결과를 관리합니다.

실험 조건과 측정 결과를 재현 가능한 형태로 기록합니다.

핵심 지표:

- 손 펼침/주먹 반복 횟수
- 시험 물체별 파지 성공률
- 비전 입력부터 서보 동작까지의 지연시간
- tendon 이탈, 서보 과열 및 통신 손실 여부

필수 절차:

- `procedures/grasp-test.md`: 원통 파지와 엄지–검지 집기
- `procedures/safety-test.md`: timeout, SAFE/FAULT와 비상정지
- `procedures/recording-test.md`: rosbag2, Session ID와 판정 상태

시험 결과는 날짜, commit, 설정 파일 버전과 실제 측정값을 `results/`에 남깁니다.
