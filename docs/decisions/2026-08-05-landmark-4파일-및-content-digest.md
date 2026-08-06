# 합의: LandMark JSON 필수화 · content_digest 포함 · 재업로드 CLI 폐기

- **상태**: 제안 — 팀 합의 대기
- **작성일**: 2026-08-05
- **작성자**: 신수진
- **관련 티켓**: [S15P11C103-125](https://ssafy.atlassian.net/browse/S15P11C103-125) (격리 uploader), [S15P11C103-136](https://ssafy.atlassian.net/browse/S15P11C103-136) (EC2 수신·검증), [S15P11C103-131](https://ssafy.atlassian.net/browse/S15P11C103-131) (계약 fixture 테스트), [S15P11C103-137](https://ssafy.atlassian.net/browse/S15P11C103-137) (재업로드 CLI — 폐기 대상)
- **관련 문서**: 요구사항 명세서 V7.1 §3.9·§6.5·FR-46, `EC2/thing_database_web/docs/pending-decisions.md` (P-1·P-2·P-3·P-4·P-5·C-1)

---

## 1. 배경 — 왜 지금 정하고 가야 하나

uploader(125) 구현 직전 점검에서, **landmark 처리와 `content_digest` 정의가 로봇·EC2·명세 세 곳에서 서로 어긋나** 있음을 확인했다. 특히 아래는 **잠복 중인 치명 버그**다.

> **exporter는 `content_digest`에 landmark를 포함**해서 계산하는데
> (`build_metadata`가 `files`에 hand_command·motor_status·landmark 3개를 넣고
> `calculate_content_digest`는 `content_digest` 키만 제외 —
> `thing_ws/src/thing_logger/thing_logger/exporter.py:1294,1326`),
> **EC2는 landmark를 제외**하고 재계산한다
> (`INCLUDE_IN_DIGEST=False` — `EC2/.../backend/apps/digest.py:49`).
>
> → 두 쪽이 서로 다른 바이트를 SHA-256 하므로, 서버 재계산값 ≠ 요청값 →
> **모든 업로드가 `422 content_digest 불일치`로 거부된다.**
> 지금 안 터지는 이유는 uploader 데몬이 아직 없어서 아무도 POST를 안 하기 때문이다.
> uploader를 켜는 순간 첫 업로드부터 100% 실패한다.

이 문서는 그 불일치를 포함해 얽힌 세 가지(landmark 필수화 · digest 포함 · 재업로드 CLI)를 **한 번에 정합적으로** 확정하기 위한 합의 요청이다.

---

## 2. 결정 항목 (합의 대상)

| # | 결정 | 근거 |
|---|------|------|
| **D1** | **LandMark JSON을 필수 4번째 파일로 확정.** uploader는 항상 4 part를 보내고, EC2는 `REQUIRED=True`로 3-part 업로드를 거부한다. | 명세 V7.1 §3.9·§6.5·8.3(검수 6)이 "정확히 네 파일"로 확정. C-1 전환. |
| **D2** | **landmark를 `content_digest` 계산에 포함.** EC2 `INCLUDE_IN_DIGEST=True`. | exporter가 이미 포함 중 → EC2를 맞춰야 §1 버그 해소. landmark도 멱등성·무결성 보호를 받게 됨. D1로 항상 존재하므로 "3파일↔4파일 → 409" 부작용 없음. |
| **D3** | **LandMark JSON 스키마를 exporter 출력 형식으로 확정** (아래 §3). EC2 `landmark_contract.SCHEMA_DECIDED=True`. | P-1 "형식 미정"은 사실상 이미 `export_schema.py`에 존재. 생성기(exporter)를 단일 기준으로 삼는다. **적용 시점은 D1·D2와 분리 가능**: `SCHEMA_DECIDED=False`여도 업로드 종단은 D1·D2만으로 동작하므로, 스키마 강화는 131(fixture 테스트)과 함께 켜도 된다. 켜는 시점에 `make_session.sample_payload()` 갱신이 **필수**(아래 §5). |
| **D4** | **수동 재업로드 CLI 폐기.** 티켓 137 취소, uploader(125)·MVP에 CLI 없음. | 실패 시 자동 재시도·영구 queue·재개 없음 원칙과 일관. 범위 축소. |

---

## 3. 확정 LandMark JSON 스키마 (D3 상세)

`export_schema.py`의 `LANDMARK_RECORD_FIELDS` + `_landmark_record`가 실제 생성하는 형식이며, **이것을 계약으로 확정한다.**

- **최상위**: JSON **배열**(`[ ... ]`) — 레코드의 시각 오름차순 나열. (EC2 `ROOT_TYPE`을 `dict`→`list`로 변경 필요)
- **레코드 필드**(고정 순서 12개):

| 필드 | 타입 | 비고 |
|------|------|------|
| `session_id` | string | 10진 문자열 |
| `timestamp` | string | RFC 3339 UTC `Z` |
| `stamp_sec` | int | ROS stamp 초 |
| `stamp_nanosec` | int | ROS stamp 나노초 |
| `elapsed_ms` | int | 세션 시작 상대 밀리초 |
| `detected` | bool | |
| `confidence` | float | 0.0–1.0 |
| `handedness` | int | 유효 enum |
| `handedness_confidence` | float | 0.0–1.0 |
| `image_width` | int | uint32, >0 |
| `image_height` | int | uint32, >0 |
| `landmarks` | array | **정확히 21개** `{"x":float,"y":float,"z":float}` |

- EC2 검증 강도: 최소 `SCHEMA_DECIDED=True`, `ROOT_TYPE=list`. 필요 시 레코드 필수 키·21점 검사까지 추가(선택). **exporter가 단일 기준이므로 EC2는 exporter 출력으로 fixture 교차검증**(티켓 131).

---

## 4. content_digest 규칙 (D2 상세) — 양쪽 바이트 동일이 절대 조건

`content_digest = "sha256:" + SHA256( canonical_json(metadata) )`

- `canonical_json`: `content_digest`·`exported_at` 키 제외 → `json.dumps(sort_keys=True, separators=(',',':'), ensure_ascii=False)` → UTF-8. (exporter는 `exported_at` 필드가 없어 제외가 no-op → 동일)
- **`files`에 landmark 항목 포함** (D2). 즉 metadata의 `files.{hand_command,motor_status,landmark}` 3개 모두가 digest 대상.
- **한 곳이라도 어긋나면 다시 전량 422.** `digest.py`의 `TEST_VECTOR`에 **landmark 항목을 추가**해서 exporter와 EC2가 같은 metadata → 같은 digest를 내는지 CI에서 교차검증한다(현재 벡터는 CSV 2개뿐이라 포함/제외 차이를 잡지 못함).

---

## 5. 컴포넌트별 변경 목록 (누가 · 무엇을)

| 컴포넌트 | 파일 | 변경 | 담당 |
|---------|------|------|------|
| **로봇 exporter** | `thing_ws/.../thing_logger/exporter.py`, `export_schema.py` | **코드 변경 없음** (이미 landmark 포함·스키마 보유). landmark 출력이 §3과 일치함만 확인 | 신수진 |
| **로봇 uploader (125)** | `thing_ws/src/thing_logger/thing_logger/uploader.py`·`uploader_contract.py` (console_script `uploader`, [배치 결정](2026-08-05-logger-uploader-jetson-배치.md) P2) | 항상 **4 part** 전송(metadata·hand_command·motor_status·landmark). 특별 분기 없음 | 신수진 |
| **EC2 backend** | `backend/apps/landmark_contract.py` | `REQUIRED=True`, `INCLUDE_IN_DIGEST=True`, `SCHEMA_DECIDED=True`, `ROOT_TYPE=list` | 김기현 |
| | `backend/apps/digest.py` | `TEST_VECTOR`에 landmark 추가 + 교차검증 | 김기현 |
| | `backend/apps/limits.py` | landmark part 상한(현 120MiB) 확정/조정 → Django·Nginx 상한 동반 | 김기현 |
| | `backend/apps/management/commands/make_session.py` | `sample_payload()`가 dict(`{"schema_note","frames"}`) 형식 — **D3 적용(SCHEMA_DECIDED=True) 시 자기 서버 검증에 걸림**. exporter 배열·12필드 형식으로 갱신 (D1·D2만 적용하는 동안은 그대로 동작) | 김기현 |
| | `backend/apps/tests_landmark.py` 등 | `assertFalse(SCHEMA_DECIDED)`·`assertFalse(INCLUDE_IN_DIGEST)` 가드 테스트는 플래그 전환 시 **의도적으로 깨지도록** 설계된 것 — True 기준으로 갱신, 3-part 업로드 테스트를 4-part로 | 김기현 |
| **EC2 frontend** | `frontend/src/views/SessionDetailView.jsx` | 기능 변경 **없음**. 노트 문구 "형식 확정 전" 갱신(코스메틱) | 김기현 |
| | `frontend/src/test/fixtures.js` | `downloads`·`file_sizes`·`row_counts`에 landmark 추가(테스트) | 김기현 |
| **명세** | 요구사항 명세서 §6.5 외 | digest 문구를 "네 파일 포함"으로 수정, LandMark JSON 스키마 §3 반영. **"세 파일" 잔재가 V7.md에 43곳**, multipart 합계 80.25MiB는 3파일 합(landmark 상한 누락, P-3) — 함께 정리 → V7.2 | 신수진 |
| **미결정 회신** | `EC2/.../docs/pending-decisions.md` | P-1·P-2·C-1 = 해소, P-3 = 상한 확정, P-4 = MVP 미제공 유지, P-5 = 멱등상 불가 | 김기현 |
| **Jira** | 137 | **취소/폐기**. 125·136에 본 합의 링크 | 신수진 |

---

## 6. 롤아웃 순서 (동시성 안전)

digest 규칙은 **로봇·EC2를 동시에 바꿔야** 한다(한쪽만 바꾸면 그 순간부터 전량 422). 순서:

1. 본 문서 합의 · sign-off
2. EC2: `landmark_contract` 3플래그(+`ROOT_TYPE`) 전환, `digest.py` TEST_VECTOR에 landmark 추가, 테스트 통과
3. 로봇: exporter landmark 출력이 §3·TEST_VECTOR와 일치함을 교차검증(변경 없음 확인)
4. uploader(125) 구현 → 4 part 업로드 종단 통합(티켓 138)
5. exporter·EC2 배포는 **함께**. 스테이징에서 201/200/409/422 시나리오 검증 후 공개

---

## 7. 미해결 · 리스크

- **Jira 125 완료조건 충돌**: 125에 "실패 bag를 수동 CLI로 다시 export/upload할 수 있다"가 남아 있어 D4(CLI 폐기)와 모순 — 이대로면 125 완료 검수에서 걸린다. 티켓 문구 수정은 보류 중(신수진 판단 대기).
- **P-3**: landmark 120MiB 상한은 실측 없는 임시값. 대표 60초 세션 실제 용량으로 확정 필요(Django 210MiB·Nginx 220MiB 동반 조정).
- EC2가 landmark의 `image_width/height` 등 필드를 표시/다운로드 외에 쓸지(현재 다운로드 전용, P-4 시계열 미제공 유지).
- 이미 스테이징에 올라간 시험 세션이 있으면 digest 규칙 변경으로 재계산됨 — MVP 전이라 영향 없다고 가정.

---

## 8. Sign-off

| 역할 | 이름 | 합의 | 날짜 |
|------|------|------|------|
| 격리 uploader·ACK 계약 | 신수진 | ☐ | |
| EC2 수신·검증·응답 | 김기현 | ☐ | |
| Compose·종단 장애 시험 | 윤정민 | ☐ | |
