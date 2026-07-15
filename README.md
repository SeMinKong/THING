# Human-Mimetic Tendon Robot Hand

이 저장소는 tendon 구동 로봇손의 기구·전자·제어·비전·시뮬레이션·시험 자료를 한곳에서 통합 관리합니다.

인체 손의 굽힘과 파지 동작을 tendon 구조와 서보모터로 모방하고, 카메라 기반 손 추적을 통해 실시간으로 제어하는 5주 학부 프로젝트입니다.

## 5주 MVP

- 5개 손가락과 엄지 대립축을 포함한 6-서보 로봇손 제작
- 손 펴기, 주먹, 원통 파지 및 가벼운 물체 집기 구현
- MediaPipe 기반 실시간 손동작 미믹
- 파지 성공률, 반복 동작, 지연시간 측정
- Isaac Lab의 기성 dexterous-hand 예제 실행을 도전 목표로 수행

## 저장소 구성

- `docs/`: 계획, 아키텍처, 인터페이스, 주간 기록
- `mechanical/`: 관절, tendon, spool 및 손 구조 CAD
- `electronics/`: 회로, 배선 및 BOM
- `firmware/`: 마이크로컨트롤러와 서보 제어 코드
- `vision/`: 손 인식, 캘리브레이션 및 동작 매핑
- `simulation/`: URDF/USD와 Isaac Lab 실험
- `tests/`: 시험 절차와 측정 결과
- `media/`: 조립 및 시연 자료

## 협업 흐름

1. GitLab Issue에 목적과 완료 조건을 작성합니다.
2. Issue 번호를 포함한 브랜치를 생성합니다. 예: `23-vision-finger-flexion`.
3. 작업과 시험 결과를 커밋하고 원격 브랜치로 push합니다.
4. Merge Request에 `Closes #23`을 작성하고 리뷰를 요청합니다.
5. 리뷰와 시험을 통과한 변경만 `main`에 병합합니다.

세부 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고합니다.
