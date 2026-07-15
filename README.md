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

- `docs/`: 계획, 아키텍처, 인터페이스, 일일·주간 기록
- `mechanical/`: 관절, tendon, spool 및 손 구조 CAD
- `electronics/`: 회로, 배선 및 BOM
- `firmware/`: 마이크로컨트롤러와 서보 제어 코드
- `vision/`: 손 인식, 캘리브레이션 및 동작 매핑
- `simulation/`: URDF/USD와 Isaac Lab 실험
- `tests/`: 시험 절차와 측정 결과
- `media/`: 조립 및 시연 자료

## 협업 흐름

Jira를 작업 관리의 단일 기준으로 사용하고, GitLab은 소스 코드, 브랜치, Merge Request(MR)와 리뷰를 관리하는 데 사용합니다. 같은 작업을 위한 GitLab Issue는 별도로 만들지 않습니다.

1. Jira Story, Task 또는 Bug에 목적과 완료 조건을 작성하고 담당자를 지정합니다.
2. 최신 `develop`에서 Jira 키가 포함된 브랜치를 생성합니다. 예: `feature/S15P11C103-23-vision-finger-flexion`.
3. 작업과 검증 자료를 commit하고 원격 브랜치에 push합니다. 커밋 메시지는 `<type>(<area>): <summary> [JIRA-KEY]` 형식을 사용합니다.
4. `[JIRA-KEY] 작업 요약` 형식의 MR을 `develop` 대상으로 만들고, 설명에 Jira 키와 링크를 기록합니다.
5. 최소 1명의 리뷰와 검증을 통과한 뒤 `develop`에 병합합니다.
6. 병합된 MR과 결과 자료를 Jira에 연결하고 완료 조건을 확인한 뒤 Jira 작업을 완료합니다.

`main`에는 `release/*`와 `hotfix/*`만 MR을 통해 병합합니다. 데일리 리포트는 Jira 작업별 브랜치 대신 [주차별 예외 흐름](docs/daily-reports/README.md)을 사용합니다.

세부 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고합니다.
