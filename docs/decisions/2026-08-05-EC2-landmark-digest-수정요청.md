# EC2 수정 요청: landmark 필수화 · content_digest 포함 (→ 김기현)

- **상태**: **핵심 반영 확인** (2026-08-05) — `feature/S15P11C103-96-updated-ec2-and-web`에
  `INCLUDE_IN_DIGEST=True`·`TEST_VECTOR` landmark 반영됨. 로봇 exporter가 생성한 실제
  metadata로 **교차검증: 양쪽 digest 바이트 단위 일치**(§7). 잔여: `REQUIRED=True` 전환
  (동작엔 지장 없음 — validators가 landmark 선언 허용 + uploader가 4파일 강제),
  D3(SCHEMA_DECIDED)는 계획대로 131 시점, develop 머지 대기.
- **작성일**: 2026-08-05
- **요청자**: 신수진 (uploader, S15P11C103-125)
- **수신자**: 김기현 (EC2 수신·검증, S15P11C103-136)
- **성격**: `EC2/thing_database_web/docs/pending-decisions.md`의 **P-2·C-1 공식 회신**
- **근거 문서**: [2026-08-05-landmark-4파일-및-content-digest.md](2026-08-05-landmark-4파일-및-content-digest.md) (D1·D2 상세)

---

## 1. 배경 — 왜 지금인가

uploader(125) 구현이 브랜치 `feature/S15P11C103-125-uploader`에 올라갔고(커밋 `4dea44e`),
이제 실업로드가 시작됩니다. 그런데 현재 상태로는:

- **exporter(로봇)**: `content_digest`에 landmark **포함**해서 계산
  (`thing_ws/src/thing_logger/thing_logger/exporter.py:1294,1326` — develop 머지 완료, 124)
- **EC2**: landmark **제외**하고 재계산
  (`backend/apps/landmark_contract.py:58` `INCLUDE_IN_DIGEST=False` → `digest.py:49`)

→ 양쪽이 서로 다른 바이트를 SHA-256 하므로 **모든 업로드가 `422 content_digest 불일치`로
거부**됩니다. 또한 파일은 **4개(landmark 포함)로 팀 확정**됐습니다(uploader도 4개 강제).

repo 전체를 훑어 digest를 계산하는 곳은 **exporter와 EC2 `digest.py` 정확히 두 곳뿐**임을
확인했습니다. exporter는 develop에 머지돼 있으므로 **EC2 한 곳만 바꾸면 정합이 완료**됩니다.

### 1.1 왜 "EC2를 바꾸는 쪽"으로 판단했나

정합시키는 방향은 둘 중 하나입니다 — **(A) EC2를 exporter에 맞춤(landmark 포함)** vs
**(B) exporter를 EC2에 맞춤(landmark 제외)**. (A)로 판단한 근거:

1. **로봇 코드는 이미 확정·머지 상태라 (B)는 수술이 큼.** exporter는 landmark 없으면
   metadata 생성 자체가 실패하고(`exporter.py:1294` — files가 정확히
   hand_command·motor_status·landmark여야 함), handoff 클라이언트도 4종을 강제합니다
   (`uploader_handoff.py:15`). 둘 다 develop 머지 완료(124). (B)를 택하면 검증 끝난
   로봇 코드를 다시 뜯고 재검증해야 합니다. (A)는 EC2 플래그 1줄.

2. **명세 해석상 "포함"이 자연스러움.** §6.5는 digest를 "`exported_at`과 `content_digest`
   자신을 제외한 metadata의 **의미 필드 전체**"로 정의합니다. `files`에 landmark가
   있으니 "전체"에는 landmark가 들어갑니다. 뒤따르는 "두 CSV의 …은 계산 대상에
   포함한다" 문장은 landmark를 배제한다기보다 열거가 누락된 것으로 보이며, 그래서
   EC2 쪽에서도 이를 확정하지 못해 **P-2로 등록**해 두셨던 것입니다.

3. **명세 다수 조항이 "정확히 네 파일".** §1.3·§3.9·FR-46·FR-49·8.3(검수 6)이 모두
   4파일이고, §6.5 metadata schema v1 예시에도 landmark 항목(row_count 포함)이
   있습니다. "세 파일" 문구는 구버전(V6.x) 잔재로 확인했습니다(V7.md에 43곳 잔존 —
   명세 V7.2에서 신수진이 정리 예정).

4. **제외하면 landmark가 무결성·멱등성 보호를 못 받음.** digest에서 빠지면 landmark
   내용이 다른 재업로드가 409로 걸리지 않고 200으로 조용히 무시됩니다(현재
   `landmark_contract.py` 주석에도 이 한계가 적혀 있음). 4파일이 확정된 이상
   4개 모두 같은 보호를 받는 게 일관됩니다.

5. **기존 제외의 전제가 사라짐.** 제외를 택하셨던 이유(주석 기준)는 "포함하면
   landmark 유무에 따라 같은 세션이 충돌한다"였는데, 이는 landmark가 *선택*일 때의
   문제입니다. **필수(4파일)로 확정되면 landmark는 항상 존재**하므로 그 부작용
   자체가 소멸합니다.

6. **지금 바꿔야 하는 이유**: uploader가 생기기 전에는 이 불일치가 드러나지 않았지만,
   이제 첫 실업로드부터 422가 됩니다. 아직 업로드 0건이라 마이그레이션 부담 없이
   바꿀 수 있는 마지막 시점입니다.

## 2. 지금 바꿔주실 것 (필수 — 이것만으로 422 해소)

### `backend/apps/landmark_contract.py`

| 줄 | 현재 | 변경 | 의미 |
|----|------|------|------|
| L51 | `REQUIRED = False` | `True` | landmark를 필수 4번째 part로 (C-1 회신: **네, uploader는 4 part를 보냅니다**) |
| L58 | `INCLUDE_IN_DIGEST = False` | `True` | digest 계산에 landmark 포함 (P-2 회신: **포함**) ← **422 해소 핵심** |

### `backend/apps/digest.py`

- `TEST_VECTOR`(L97)에 **landmark 항목 추가** + 기대 digest 재계산:

```python
"landmark": {
    "filename": "session_123456789012345678_landmark.json",
    "size_bytes": 12345,
    "row_count": 1200,
    "sha256": "c" * 64,
},
```

지금 벡터는 CSV 2개뿐이라 포함/제외 차이를 잡지 못합니다. 로봇 쪽에서 같은 벡터로
교차검증하겠습니다(131).

## 3. 바꾸면 예상되는 것 (버그 아님 — 설계된 파급)

- `tests_landmark.py:91,114`의 `assertFalse(SCHEMA_DECIDED)`·`assertFalse(INCLUDE_IN_DIGEST)`
  가드 테스트가 **의도대로** 깨짐 → True 기준으로 갱신
- 3-part로 보내는 업로드 테스트 → 4-part로 갱신
- 그 외 `upload_views.py`·`validators.py`·`storage.py`는 플래그를 읽어가므로 **자동 추종**
  (수정 불필요 — 원래 그렇게 설계해 두신 덕분입니다)

## 4. 나중에 해도 되는 것 (131 시점 — 지금은 보류 가능)

- `SCHEMA_DECIDED = True` + `ROOT_TYPE = list` (L63·L66)
  - LandMark JSON 스키마는 **exporter 출력 형식으로 확정**(P-1 회신):
    최상위 **배열**, 레코드 12필드(`session_id·timestamp·stamp_sec·stamp_nanosec·
    elapsed_ms·detected·confidence·handedness·handedness_confidence·image_width·
    image_height·landmarks`), `landmarks`는 정확히 21개 `{x,y,z}`.
    상세 표: [합의 문서 §3](2026-08-05-landmark-4파일-및-content-digest.md)
  - ⚠️ 이걸 켜면 `make_session.py`의 `sample_payload()`(dict 형식)가 자기 서버 검증에
    걸리므로 **exporter 배열 형식으로 같이 갱신** 필요
- landmark part 상한 확정(P-3): 현 120MiB는 임시값 — 실측 후 Django·Nginx 상한 동반 조정

## 5. 배포 순서 걱정 없음

아직 실업로드 0건입니다(uploader가 오늘 생김). **EC2를 먼저 바꿔두시면** 되고,
exporter는 이미 포함 상태라 로봇 쪽 변경이 없습니다. 한쪽만 바뀐 채로 실업로드가
시작되는 상황만 피하면 됩니다.

## 6. 확인 요청

- [ ] `REQUIRED=True` · `INCLUDE_IN_DIGEST=True` 반영
- [ ] `TEST_VECTOR` landmark 추가 · 가드/3-part 테스트 갱신
- [ ] `pending-decisions.md`에 P-1·P-2·C-1 해소 기록
- [ ] [합의 문서](2026-08-05-landmark-4파일-및-content-digest.md) §8 sign-off

문의는 신수진에게. uploader 쪽 E2E(성공 201/멱등 200/409/401/timeout/변조 차단 등
20케이스)는 검증돼 있어, 플래그 전환 후 바로 종단 시험(138) 가능합니다.

## 7. 반영 확인 기록 (2026-08-05)

`feature/S15P11C103-96-updated-ec2-and-web` 기준 교차검증 결과:

- `INCLUDE_IN_DIGEST = True` 반영 확인, `digest.py` 주석·`TEST_VECTOR`에 landmark 반영 확인
- 로봇 `exporter.build_metadata()`가 생성한 실제 metadata(landmark 포함)에 대해
  로봇 `calculate_content_digest()`와 EC2 `compute_content_digest()`가
  **`sha256:f8084eca…7305851`로 바이트 단위 일치** → 422 digest 불일치 해소
- landmark 1바이트 변경 시 digest가 달라짐 → landmark도 멱등성·무결성 보호 확인
- validators는 `REQUIRED=False`에서도 landmark 선언을 허용하므로 4-part 업로드 통과 가능
- 남은 항목: `REQUIRED=True`(엄격화), D3(131 시점), P-3 상한 실측, develop 머지
