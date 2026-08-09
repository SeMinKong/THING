# EC2 데이터 포털 — 값·계약 확정 회신 요청

- 기준: 요구사항 명세서 V7.1
- 회신처: EC2 담당 / **저장소를 열 필요 없습니다.** 이 문서에 답만 적어 주십시오

명세서를 실제 프로젝트에 그대로 연결한다고 가정하고 검증한 결과입니다.

| 등급 | 항목 | 내용 |
|---|---|---|
| **막힘** | P-1 | LandMark JSON 의 형식이 정의되어 있지 않습니다 |
| 해결 | P-2 | landmark 를 `content_digest` 에 넣는가 → **포함**(로봇 exporter 따라) |
| 확인 | P-3 | landmark part 상한을 얼마로 둘 것인가 |
| 확인 | P-4 | landmark 시계열 조회를 제공하는가 |
| 확인 | P-5 | 이미 업로드된 세션에 landmark 를 나중에 추가할 수 있는가 |
| 조율 | C-1 | 업로더가 네 part 를 보내기 시작했는가 |

현재 코드는 **네 파일을 받되 landmark part 는 선택**으로 동작합니다. 형식이 미정이라 "올바른 JSON 이면 받는다" 까지만 검사합니다. 전환 지점은 `backend/apps/landmark_contract.py` 와 `backend/apps/limits.py` 두 곳입니다.

---

## P-1. LandMark JSON 의 형식이 정의되어 있지 않습니다 ★

6.5절 metadata schema 의 landmark 항목이 이 상태입니다.

```json
"landmark": {
  "filename": "session_123456789012345678_landmark.json",
  "size_bytes": 12345,
  "json_data": 1234,          ← row_count 자리인데 의미 불명
  "sha256": "UTF-8"           ← 값이 hex 가 아니고 주석에 "수정 필요"
}
```

두 CSV 는 6.5절에 header 와 행 규칙이 정의되어 있지만 **landmark 는 파일 내부 구조가 어디에도 없습니다.** `HandLandmarks.msg` 는 `geometry_msgs/Point32[21]` 이지만, 세션 전체를 어떻게 담는지(프레임 배열? 시각 키? 압축?)는 정해지지 않았습니다.

**현재 코드** — "UTF-8 로 읽히는 올바른 JSON" 까지만 검사합니다. 그것만으로도 CSV·바이너리·잘린 파일은 걸러집니다. 형식이 정해지면 `landmark_contract.py` 에서 `SCHEMA_DECIDED = True` 로 바꾸고 최상위 타입·필수 키를 적으면 됩니다. **다른 파일은 손대지 않아도 됩니다.**

```
최상위 타입:     [ ] 객체(dict)  [ ] 배열(list)
필수 키:         ________________________________
개수 필드 이름:  [ ] row_count 로 통일  [ ] json_data 유지  [ ] 개수 없음
                 (현재 코드는 landmark 에 개수를 요구하지 않습니다)
sha256:          [ ] 다른 파일과 같은 64자 hex 로 확정
샘플 파일 1개를 주시면 그대로 맞추겠습니다.
```

---

## P-2. landmark 를 `content_digest` 에 넣는가 ★

> 6.5절: `content_digest` 는 `exported_at` 과 자신을 제외한 metadata 의 의미 필드 전체를 canonical JSON 으로 직렬화해 SHA-256 으로 계산한다. **두 CSV의** filename·size_bytes·row_count·sha256 은 계산 대상에 포함한다.

"의미 필드 전체" 에는 `files` 가 들어가지만, 뒤 문장은 **두 CSV 만** 열거합니다. landmark 항목이 포함인지 아닌지 정해지지 않았습니다.

`content_digest` 는 멱등성 기준(NFR-26)이므로 이 선택이 동작을 바꿉니다.

| | 포함할 때 | 제외할 때 |
|---|---|---|
| 같은 세션에 landmark 만 추가 재업로드 | **409 충돌** | 200, 파일은 덮어쓰지 않음 |
| landmark 내용이 다른 재업로드 | 409 로 걸림 | 200, 조용히 무시 |
| 기존 3파일 세션의 digest | 영향 없음 | 영향 없음 |

**결정: 포함(`INCLUDE_IN_DIGEST = True`).** 로봇 exporter(thing_logger)의 `calculate_content_digest` 가 `content_digest` 만 빼고 `files` 전체(landmark 포함)로 digest 를 계산한다. 서버가 landmark 를 빼면 재계산 digest 가 어긋나 모든 업로드가 422 가 되므로 로봇을 따른다. 로봇은 `build_metadata` 에서 항상 네 파일을 요구하므로 landmark 유무로 같은 세션이 흔들리지 않아 멱등성(NFR-26)도 안전하다.

> ⚠️ **스펙 문구 정정 필요:** V7.1 §6.5 본문은 여전히 digest 대상을 "두 CSV" 만 열거한다. 실제 로봇은 landmark 를 넣으므로 §6.5 를 "세 파일" 로 고쳐야 한다 (스펙 담당 회신 요청).

```
[ ] 제외
[x] 포함 — landmark 도 digest 대상 (§6.5 문구를 "세 파일" 로 정정)
```

---

## P-3. landmark part 상한을 얼마로 둘 것인가

6.5절의 120MiB 는 실제 용량을 모르는 상태에서 붙여 둔 값입니다. 확정이 필요합니다.

**현재 코드** — 120MiB 를 그대로 쓰되, **값을 `backend/apps/limits.py` 한 곳에서만 정합니다.** 그 값이 바뀌면 Django 요청 상한이 자동으로 따라오고, Nginx 는 설정 파일이라 import 할 수 없으므로 시험이 대조합니다.

```
$ python manage.py test apps.tests
FAIL: test_nginx_conf_matches_part_limits
AssertionError: nginx conf 의 client_max_body_size 를 391M 이상으로 고치세요
```

숫자를 세 곳에 흩어 두면 하나만 고치고 잊습니다. 그러면 큰 업로드가 Django 에 닿기 전에 Nginx 에서 413 으로 끊기고 원인을 찾기 어렵습니다.

**실측했습니다.** `manage.py make_session` 으로 20Hz(FR-11) 기준 파일을 만들어 재 본 값입니다.

| 세션 길이 | 프레임 | 크기 |
|---|---|---|
| 1분 | 1,200 | 1.1 MiB |
| 10분 | 12,000 | 10.7 MiB |
| 30분 | 36,000 | 32.2 MiB |
| **60분** (FR-51 기록 상한) | 72,000 | **64.5 MiB** |

**최장 세션이 64.5MiB 이므로 120MiB 는 두 배 가까운 여유입니다.** 다만 이 값은
아래 두 가지에 따라 달라집니다.

- **형식(P-1)** — 좌표를 배열 `[x,y,z]` 로 바꾸면 키 이름이 빠져 40% 가까이 줄어듭니다. 소수점 자리를 줄이면 더 줄어듭니다.
- **압축** — gzip 을 쓰면 좌표가 반복적이라 크게 줄지만, 그러면 `application/json` 이 아니게 되어 content type 검사와 다운로드 규약을 함께 정해야 합니다.

P-1 을 먼저 정하면 이 값은 자연히 따라옵니다.

```
landmark part 상한: ______ MiB   (실측 64.5MiB 기준. 형식이 바뀌면 줄어듭니다)
압축 여부:          [ ] 비압축 JSON 유지  [ ] gzip (규약 추가 필요)
```

확정되면 `limits.py` 의 `PART_MAX_BYTES["landmark"]` 만 고치고 시험이 알려 주는 값을 nginx conf 에 넣습니다.

---

## P-4. landmark 시계열 조회를 제공하는가

6.5절의 `GET /api/v1/sessions/{id}/data?dataset=` 는 `hand_command` 와 `motor_status` 만 정의합니다. landmark 는 목록에 없습니다.

**현재 코드** — 제공하지 않습니다. 형식이 없으면 `columns` 를 만들 수 없습니다.

```
[ ] 제공하지 않음 — 다운로드만 (현재 동작)
[ ] 제공 — 이 경우 P-1 의 형식과 columns 정의가 먼저 필요합니다
```

---

## P-5. 이미 업로드된 세션에 landmark 를 나중에 추가할 수 있는가

3파일로 올라간 세션에 landmark 만 뒤늦게 붙이는 경로가 필요합니까?

**현재 코드** — 불가능합니다. 업로드는 세션 단위이고 같은 `session_id` 재전송은 digest 비교로 200 또는 409 가 됩니다. landmark 를 digest 에서 제외했으므로(P-2) 200 이 되지만 **파일은 덮어쓰지 않습니다.**

```
[ ] 불필요 — 처음부터 네 파일로 올린다 (현재 동작)
[ ] 필요 — 추가 경로를 설계한다 (별도 산정)
```

---

## C-1. 업로더가 네 part 를 보내기 시작했는가 — 조율

공개 파일은 네 개로 확정됐습니다. 그런데 **Raspberry Pi 업로더가 네 part 를 보내기 시작했는지는 별개 문제**입니다.

`landmark_contract.REQUIRED = True` 로 바꾸는 순간 세 part 업로드는 전부 400 이 됩니다. 로봇에는 미전송 queue 가 없으므로(7.3절) **그 세션은 사라집니다.**

**현재 코드** — 선택입니다. 세 part 업로드가 계속 성공하고, landmark 가 오면 검증해서 저장합니다. 안전한 쪽으로 두었습니다.

```
업로더가 landmark part 를 보냅니까:
  [ ] 예 — REQUIRED = True 로 바꿔 네 파일을 강제한다
  [ ] 아직 — 선택 유지. 전환 시점을 알려 주면 그때 바꿉니다
```

전환 전까지는 landmark 없는 세션이 생깁니다. 상세 화면은 그 세션에 landmark 행을 그리지 않습니다 — 없는 파일에 다운로드 버튼을 주면 404 를 받기 때문입니다.

---

## 회신 후

`backend/apps/landmark_contract.py` 한 곳을 고칩니다. 값과 근거를 같은 자리에 기록합니다.

```python
PART_MAX_BYTES = {
    ...
    "landmark": 40 * MiB,
}
# status  확정
# 회신    제어 담당 / 2026-08-__
# 원문    "60분 세션 실측 32MiB. 여유 두고 40MiB"
```

| 항목 | 회신 후 작업량 |
|---|---|
| P-2 P-5 C-1 | 플래그 한 개 |
| P-3 | `limits.py` 의 상수 한 개 + 시험이 알려 주는 nginx 값 |
| P-1 | `landmark_contract.py` 의 스키마 항목 |
| P-4 "제공" | `read_views` 에 dataset 추가 (중간) |
