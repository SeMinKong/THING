# 협업 규칙

이 문서는 6인 팀이 Jira와 GitLab을 이용해 동일한 방식으로 작업하기 위한 규칙을 정의합니다. Jira는 작업 관리의 단일 기준이고, GitLab은 소스 코드, 브랜치, MR과 리뷰를 관리합니다.

## 1. 기본 원칙

- `main`과 `develop`에는 직접 push하지 않고 반드시 Merge Request(MR)를 통해 반영합니다.
- 모든 일반 작업은 Jira의 스토리, 작업 또는 버그에서 시작합니다.
- 같은 작업을 GitLab Issue로 중복 관리하지 않습니다.
- Jira는 `Epic -> Story/Task/Bug`의 2-depth 구조를 사용하고 하위 작업(Sub-task)은 만들지 않습니다.
- 원칙적으로 `Jira 작업 1개 = 브랜치 1개 = MR 1개`로 관리합니다. 릴리스, 핫픽스와 데일리 리포트의 예외는 7절과 8절을 따릅니다.
- Epic은 여러 작업을 묶는 용도이므로 Epic 자체로 작업 브랜치를 만들지 않습니다.
- 본인이 작성한 MR은 최소 1명의 다른 팀원에게 리뷰받습니다.
- 하드웨어 작업도 CAD뿐 아니라 사진, 영상 또는 측정 결과를 함께 남깁니다.

## 2. 브랜치 전략

프로젝트에는 간소화한 Git Flow를 적용합니다.

### 영구 브랜치

| 브랜치 | 역할 |
| --- | --- |
| `main` | 시연과 배포가 가능한 안정 버전을 보관합니다. |
| `develop` | 기능, 기구, 전자, ROS 2 제어, 비전 및 웹 작업을 통합합니다. |

### 임시 브랜치

| 브랜치 | 생성 기준 | 병합 대상 | 용도 |
| --- | --- | --- | --- |
| `feature/*` | `develop` | `develop` | 새로운 기능이나 설계를 구현합니다. |
| `fix/*` | `develop` | `develop` | 개발 중 발견한 문제를 수정합니다. |
| `docs/*` | `develop` | `develop` | 문서와 보고서를 작성합니다. |
| `experiment/*` | `develop` | 필요 시 `develop` | 제어 방식과 비전 알고리즘 실험을 수행합니다. |
| `chore/*` | `develop` | `develop` | 개발 환경 및 저장소 설정을 변경합니다. |
| `release/*` | `develop` | `main` | 시연 전 통합 테스트와 안정화를 수행합니다. |
| `hotfix/*` | `main` | `main`, 이후 `develop` | 안정 버전에서 발견한 긴급 문제를 수정합니다. |

일반적인 흐름은 다음과 같습니다.

```text
feature/fix/docs/experiment/chore
                 |
                 v
              develop
                 |
                 v
           release/v0.1.0
                 |
                 v
               main
```

`main`과 `develop`은 삭제하지 않습니다. 임시 브랜치는 필요한 대상 브랜치에 병합한 뒤 삭제합니다.

## 3. 브랜치 이름 규칙

일반 작업 브랜치는 다음 형식을 사용합니다.

```text
<type>/<JIRA-KEY>-<area>-<short-description>
```

- `type`과 설명은 영문 소문자를 사용합니다.
- Jira 키는 `S15P11C103-12`처럼 대문자를 유지합니다.
- 단어 구분에는 하이픈(`-`)을 사용합니다.
- 한글, 공백, 언더바(`_`)는 사용하지 않습니다.
- `test`, `temp`, 팀원 이름처럼 작업 목적을 알 수 없는 이름은 사용하지 않습니다.

사용 가능한 작업 영역은 다음과 같습니다.

```text
mechanical
electronics
vision
integration
interfaces
control
hardware
bringup
logger
web
safety
test
docs
```

예시:

```text
feature/S15P11C103-12-mechanical-index-tendon
feature/S15P11C103-18-vision-hand-tracking
fix/S15P11C103-27-hardware-servo-jitter
experiment/S15P11C103-31-vision-thumb-mapping
docs/S15P11C103-8-docs-control-interface
chore/S15P11C103-6-integration-repository-layout
release/v0.1.0
hotfix/S15P11C103-42-hardware-servo-angle-limit
```

기계, 전자, 비전 등 담당 영역별 장기 브랜치는 만들지 않습니다. 담당 영역 안에서도 Jira 작업 단위로 브랜치를 나눕니다.

## 4. 작업 시작 방법

Jira 작업을 배정받은 뒤 최신 `develop`에서 브랜치를 생성합니다.

```bash
git switch develop
git pull --ff-only origin develop
git switch -c feature/S15P11C103-12-mechanical-index-tendon
```

작업을 시작하면 Jira 상태를 `진행 중`에 해당하는 상태로 변경합니다. 리뷰할 준비가 된 MR을 만들면 `검토`에 해당하는 상태로 변경하고, 병합과 완료 조건 확인이 모두 끝난 뒤에만 `완료`로 변경합니다. 실제 상태 이름은 팀 Jira 보드의 워크플로를 따릅니다.

다른 작업을 시작하기 전 현재 변경 사항을 먼저 commit 또는 stash해야 합니다.

## 5. 커밋 메시지 규칙

일반 작업의 커밋 메시지는 다음 형식을 사용합니다.

```text
<type>(<area>): <summary> [JIRA-KEY]
```

커밋 종류는 다음과 같습니다.

| 종류 | 용도 |
| --- | --- |
| `feat` | 기능이나 설계를 추가합니다. |
| `fix` | 문제를 수정합니다. |
| `docs` | 문서를 추가하거나 수정합니다. |
| `test` | 테스트 코드나 시험 자료를 추가합니다. |
| `refactor` | 기능 변경 없이 구조를 개선합니다. |
| `chore` | 환경, 도구 및 저장소 설정을 변경합니다. |

예시:

```text
feat(mechanical): add index finger tendon guide [S15P11C103-12]
fix(hardware): reduce servo angle oscillation [S15P11C103-27]
docs(integration): define serial protocol [S15P11C103-8]
```

한 커밋에는 하나의 논리적인 변경만 포함합니다. 비밀번호, API 키, 대용량 임시 파일은 commit하지 않습니다.

## 6. Push 및 Merge Request

처음 push할 때 원격 추적 브랜치를 함께 설정합니다.

```bash
git push -u origin feature/S15P11C103-12-mechanical-index-tendon
```

일반 작업의 MR 제목은 다음 형식을 사용합니다.

```text
[JIRA-KEY] 작업 요약
```

예시:

```text
[S15P11C103-12] 검지 tendon guide 설계
```

MR 설명의 `관련 Jira 작업`에 Jira 키와 링크를 작성합니다. GitLab Issue 번호인 `#23`은 사용하지 않습니다. Jira 키를 대문자로 기록하면 GitLab과 Jira 연동에서 커밋과 MR을 상호 참조할 수 있습니다.

`Closes`, `Resolves`, `Fixes` 같은 Jira 종료 키워드는 Jira 자동 전환이 설정되어 있고 완료 조건을 모두 충족한 경우에만 사용합니다. 기본적으로는 병합과 검증을 마친 뒤 Jira 상태를 직접 완료로 변경합니다.

MR 대상은 다음과 같이 선택합니다.

- `feature/*`, `fix/*`, `docs/*`, `experiment/*`, `chore/*` -> `develop`
- `release/*` -> `main`
- `hotfix/*` -> 먼저 `main`, 이후 동일 수정 사항을 `develop`에 반영

MR을 만들 때 다음 사항을 확인합니다.

- 일반 작업의 Jira 키가 브랜치 이름, 커밋 및 MR 제목에 포함되어 있는가
- MR 설명에 관련 Jira 키와 링크가 기록되어 있는가
- 변경 이유와 검증 방법이 MR 설명에 적혀 있는가
- 코드 실행 결과 또는 하드웨어 시험 자료가 첨부되어 있는가
- 최소 1명의 리뷰 승인을 받았는가
- 충돌 없이 대상 브랜치에 병합 가능한가

일반 작업 MR은 필요하면 Squash하여 병합하고 `Delete source branch`를 선택합니다. `release/*`와 `hotfix/*`는 변경 이력과 브랜치 관계를 유지하기 위해 Squash하지 않는 것을 권장합니다.

## 7. Release와 Hotfix

### Release

시연 또는 주차별 안정 버전이 필요하면 `develop`에서 릴리스 브랜치를 만듭니다.

```powershell
git switch develop
git pull --ff-only origin develop
git switch -c release/v0.1.0
git push -u origin release/v0.1.0
```

릴리스 MR 제목은 `[Release vX.Y.Z] 작업 요약` 형식을 사용하고, 설명에 포함된 Jira 작업 키와 링크를 나열합니다. 릴리스 브랜치와 MR 제목은 단일 Jira 키 규칙의 예외입니다.

릴리스 브랜치에서는 새로운 기능을 추가하지 않고 통합 테스트와 결함 수정만 수행합니다. 검증 후 `main`으로 MR을 병합하고 같은 이름의 Git tag를 남깁니다.

```text
v0.1.0  1차 통합 시연
v0.2.0  2차 통합 시연
v1.0.0  최종 시연 버전
```

릴리스 브랜치에서 별도 수정이 발생했다면 해당 수정도 `develop`에 반영한 뒤 브랜치를 삭제합니다.

### Hotfix

`main`의 안정 버전에서 긴급 문제가 발견되면 `main`에서 `hotfix/*`를 생성합니다. 핫픽스는 하나의 Jira 작업과 브랜치를 사용하지만 `main`과 `develop`에 각각 MR을 만드는 `Jira 작업 1개 = MR 1개` 규칙의 예외입니다. 수정 후 먼저 `main`에 병합하되 소스 브랜치를 삭제하지 않고, 동일한 브랜치로 `develop` 대상 MR까지 병합한 뒤 브랜치를 삭제합니다.

## 8. 일일 보고서 예외

일일 보고서마다 Jira 작업과 MR을 만들면 관리 비용이 커지므로 팀원별 주차 브랜치 하나를 사용합니다. 이 절은 일반 작업의 `Jira 작업 1개 = 브랜치 1개 = MR 1개`, 브랜치 이름, 커밋 메시지와 MR 제목 규칙에 대한 명시적인 예외입니다.

데일리 리포트만을 위한 Jira 작업은 새로 만들지 않습니다. 대신 보고서의 `관련 Jira 작업`에 그날 수행한 Jira 키를 기록하고, 관련 작업이 없으면 `없음`으로 작성합니다.

주차 브랜치는 다음 형식을 사용합니다. `NN`은 프로젝트 주차를 두 자리로 표기하고, 영문 이름은 팀에서 합의한 표기를 계속 사용합니다.

```text
docs/week-01-kongsemin
docs/week-01-kimminsu
```

데일리 MR 제목과 커밋 메시지는 다음 형식을 사용합니다.

```text
MR:     [Week NN] 이름 데일리 리포트
commit: docs(docs): add YYYY-MM-DD daily report
```

- 월요일에 최신 `develop`에서 주차 브랜치를 생성합니다.
- `develop`을 대상으로 Draft MR을 미리 생성합니다.
- 매일 같은 브랜치의 `docs/daily-reports/YYYY-MM-DD/`에 본인 보고서를 commit하고 push합니다.
- 금요일에 Draft를 해제하고 주간 내용을 확인한 뒤 한 번 병합합니다.
- Draft MR의 변경 내용은 병합 전에도 팀원이 확인할 수 있습니다.
- 금요일 병합 전에는 소스 브랜치를 삭제하지 않습니다.
- 일일 보고서를 이유로 `main`에 직접 push하지 않습니다.

## 9. 작업 완료 조건

Jira 작업은 결과 파일과 검증 자료가 모두 등록되었을 때 완료합니다.

- 코드: 실행 방법과 테스트 결과
- 기구: CAD, 출력 파일, 조립 사진과 동작 시험
- 전자: 회로도, 배선도와 전원 시험
- 실험: 조건, 측정값, 결론과 다음 행동
- 문서: 변경 목적, 관련 링크와 검토 결과

MR이 병합되었더라도 검증 자료가 없다면 Jira 작업을 완료로 변경하지 않습니다.

## 10. GitLab 권장 설정

- 기본 브랜치는 `develop`으로 설정합니다.
- `main`과 `develop`을 Protected branch로 지정합니다.
- 두 브랜치 모두 직접 push를 금지하고 MR을 통해서만 병합합니다.
- `main` 병합 권한은 Maintainer로 제한합니다.
- `develop` MR도 최소 1명의 승인을 받도록 설정합니다.
- GitLab CI/CD와 Auto DevOps를 사용하지 않으며 `Pipelines must succeed`를 해제합니다.
- 변경 영역별 빌드·시험은 로컬에서 실행하고 결과를 MR에 기록합니다.
- Jira를 작업 관리의 단일 기준으로 사용하므로 GitLab Issues에는 새 작업을 만들지 않습니다.
- 일반 MR은 병합 후 소스 브랜치를 삭제합니다.
