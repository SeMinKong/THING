<!--
일반 MR 제목: [JIRA-KEY] 작업 요약
데일리 MR 제목: [Week NN] 이름 데일리 리포트
릴리스 MR 제목: [Release vX.Y.Z] 작업 요약
핫픽스 MR 제목: [JIRA-KEY] 작업 요약
일반 MR에는 Jira 키와 링크를 반드시 작성합니다.
데일리 MR에는 이번 주에 수행한 Jira 키를 나열하고, 관련 작업이 없으면 `없음`으로 작성합니다.
릴리스 MR에는 포함된 Jira 키와 링크를 모두 나열합니다.
핫픽스 MR은 같은 Jira 키와 브랜치로 `main`, `develop` 순서의 MR을 만듭니다.
-->

## 작업 유형

- [ ] 일반 Jira 작업
- [ ] 데일리 리포트 예외
- [ ] 릴리스
- [ ] 핫픽스

## 관련 Jira 작업

<!-- 예: - `S15P11C103-12`: https://jira.example.com/browse/S15P11C103-12 -->

-

## 변경 내용

-

## 검증 방법과 결과

1.

## 영향 범위

- [ ] Mechanical
- [ ] Electronics
- [ ] Vision
- [ ] ROS Interfaces
- [ ] Control
- [ ] Hardware
- [ ] Bringup
- [ ] Web
- [ ] Logger/Data
- [ ] Safety
- [ ] Tests
- [ ] Documentation

## 체크리스트

- [ ] MR 제목, 브랜치와 커밋이 작업 유형에 맞는 컨벤션을 따릅니다.
- [ ] 일반 작업은 Jira 완료 조건, 데일리 리포트는 주간 내용, 릴리스는 통합 검증 결과를 확인했습니다.
- [ ] MR 대상 브랜치가 올바릅니다.
- [ ] 실행 또는 조립 방법을 문서화했습니다.
- [ ] 필요한 시험 증거를 첨부했습니다.
- [ ] ROS 인터페이스나 WebSocket 계약 변경을 `docs/interfaces.md`에 반영했습니다.
- [ ] 위험 상태에서 일반 명령이 차단되는지 확인했습니다.
- [ ] 비밀정보와 개인 설정 파일을 포함하지 않았습니다.
