# 협업 규칙

## 기본 원칙

- `main` 브랜치에 직접 push하지 않습니다.
- 모든 작업은 GitLab Issue에서 시작합니다.
- 한 브랜치는 한 Issue만 처리합니다.
- 본인이 작성한 Merge Request는 다른 팀원에게 리뷰받습니다.
- 하드웨어 작업도 CAD뿐 아니라 사진, 영상 또는 측정 결과를 함께 남깁니다.

## 브랜치 이름

```text
<issue-number>-<area>-<short-description>
```

예시:

```text
12-mech-finger-prototype
23-vision-finger-flexion
31-firmware-servo-limit
```

## 완료 조건

Issue는 결과 파일과 검증 자료가 모두 등록되었을 때 완료합니다.

- 코드: 실행 방법과 테스트 결과
- 기구: CAD, 출력 파일, 조립 사진과 동작 시험
- 전자: 회로도, 배선도와 전원 시험
- 실험: 조건, 측정값, 결론과 다음 행동

## Jira 연동

브랜치, 커밋 및 Merge Request 제목에는 관련 Jira 작업 키를 포함합니다.