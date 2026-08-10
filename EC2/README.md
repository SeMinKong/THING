# thing — EC2 데이터 포털

텐던 구동 로봇 손의 **완료된 세션 기록을 공개하는 읽기 전용 웹**입니다.

로봇(Raspberry Pi)이 판정을 마친 세션을 HTTPS 로 밀어 넣고, 방문자는 로그인 없이 목록·상세·시계열을 보고 파일을 내려받습니다. **로봇을 조작하는 경로는 여기에 없습니다.**

기준: 요구사항 명세서 V7.1

---

## 어디에 있는가

```
[내부망]                                    [AWS EC2 · 본 저장소]

Raspberry Pi 5
└─ thing-data-uploader ──── Bearer Token HTTPS ────▶ Nginx
                                                      ├─ React 정적 빌드
                                                      └─ Gunicorn · Django
                                                            ├─ SQLite
                                                            └─ 세션 파일 디렉터리
                                                      ▲
                                        방문자 ── 로그인 없는 GET
```

Docker 를 쓰지 않습니다. Nginx·Gunicorn·Django·SQLite 를 systemd 로 운영합니다 (7.2절).

내부망 관제 웹은 **별도 저장소**입니다. 이 서버는 ROS 2·DDS 에 참여하지 않고 로봇에서 오는 업로드만 받습니다.

---

## 두 가지 원칙

### 쓰기 경로는 업로드 하나뿐이다

FR-50 이 요구하는 경계입니다. `POST /api/v1/uploads/sessions` 만 쓰기이고 장치 토큰이 필요합니다. 나머지는 전부 GET 이며 인증이 없습니다.

로봇 명령·mode·기록·Safety Reset·callback·socket·ROS 2·rclpy 를 **설치하지도 노출하지도 않습니다.**

### 서버가 파일명과 경로를 만든다

클라이언트가 보낸 파일명은 저장에 쓰지 않습니다 (FR-51). `robot_id` 와 `session_id` 는 정규식으로 검사해 상위 디렉터리 탈출을 원천 차단하고, 다운로드는 종류 이름(enum)으로만 받습니다. 경로를 입력으로 받는 지점이 없습니다.

```
{EC2_DATA_DIR}/staging/{robot_id}/{session_id}/   업로드 검증 중
{EC2_DATA_DIR}/sessions/{robot_id}/{session_id}/  검증 통과. 공개 대상
```

검증이 끝나면 staging 에서 sessions 로 옮기고, 실패하면 staging 을 지웁니다. 공개 데이터의 최종 기준은 **`status=READY` 인 SQLite row 와 sessions 디렉터리**입니다.

---

## 실행

```bash
cd thing_database_web/backend
pip install -r requirements.txt
# config/.env 를 채운다. 아래 「설정」 표 참고
python manage.py migrate
python manage.py runserver

python manage.py test apps              # 140건
```

```bash
cd thing_database_web/frontend
npm install
npm run dev
npm test                                # 48건
npm run build
```

### 설정

| 변수 | 용도 |
|---|---|
| `SECRET_KEY` `DEBUG` `ALLOWED_HOSTS` | Django 기본 |
| `DEVICE_TOKENS` | `robot_id:token_hash` 목록. **원문 토큰을 넣지 않습니다** |
| `EC2_DATA_DIR` | 세션 파일 루트 |
| `CORS_EXTRA_ORIGINS` `CSRF_TRUSTED_ORIGINS` | 프런트 배포 도메인 |

`DEVICE_TOKENS` 는 0600 systemd EnvironmentFile 로 주입합니다. 원문 토큰은 Raspberry Pi 의 `/etc/thing/uploader.env` 에만 두고 **Git·이미지·로그 어디에도 넣지 않습니다** (FR-51).

배포 파일은 `deploy/` 에 있습니다 — `nginx_thing_database_web.conf`, `thing-database-web.service`.

---

## API

| 경로 | 인증 | 하는 일 |
|---|---|---|
| `POST /api/v1/uploads/sessions` | Bearer | 세션 업로드. multipart |
| `GET /api/v1/sessions` | 없음 | 공개 세션 목록 |
| `GET /api/v1/sessions/{id}` | 없음 | 상세와 다운로드 링크 |
| `GET /api/v1/sessions/{id}/data?dataset=` | 없음 | 시계열. `hand_command` 또는 `motor_status` |
| `GET /api/v1/sessions/{id}/download/{kind}` | 없음 | 파일 |
| `GET /health` | 없음 | DB·디스크 확인 |

목록·상세는 `status=READY` 만 노출합니다. `STAGING`·`FAILED` 는 공개되지 않습니다.

오류는 `{"error":{"code","message","details"},"request_id"}` 형태이며 **경로·토큰·stack trace 를 응답에 넣지 않습니다.**

---

## 업로드가 통과해야 하는 검사

순서대로 걸러집니다. 하나라도 실패하면 staging 을 지우고 저장 파일을 건드리지 않습니다.

1. **인증** — Bearer 토큰과 `robot_id` 일치 → 401
2. **part 구성** — 필수 part 존재, 알 수 없는 part 거부 → 400 / 415
3. **크기** — part 별 상한과 합계 → 413
4. **metadata schema** — 필드·시각·`ended_at>started_at`·result → 422
5. **선언과 실제 일치** — 크기·SHA-256 → 422
6. **CSV 내용** — header, 행 수, 유한값, stamp 역행 → 422
7. **`content_digest` 재계산** — 서버가 다시 계산해 대조 → 422
8. **중복** — 같은 `(robot_id, session_id)` 가 같은 digest 면 200, 다르면 409

`content_digest` 가 멱등성 기준입니다 (NFR-26). 같은 rosbag2 를 다시 내보내 `exported_at` 만 달라져도 같은 digest 가 나오도록, 계산에서 `exported_at` 과 `content_digest` 자신을 제외합니다.

---

## LandMark JSON — 형식 확정 전

세션마다 **네 파일**을 공개합니다 (FR-49).

```
session_{id}_metadata.json
session_{id}_hand_command.csv
session_{id}_motor_status.csv
session_{id}_landmark.json      ← 형식 미정
```

**LandMark JSON 의 내부 구조는 아직 정의되지 않았습니다.** 두 CSV 는 header 와 행 규칙이 있지만 landmark 는 없습니다. 실제 용량도 미정입니다.

그래서 지금은 **"올바른 JSON 이면 받는다"** 로 열어 두었습니다.

| | 현재 동작 |
|---|---|
| part | 선택. 업로더 전환 전이라 없어도 업로드가 성공합니다 |
| 검사 | UTF-8 디코딩 + JSON 파싱까지. CSV·바이너리·잘린 파일은 걸러집니다 |
| `content_digest` | **포함**. 로봇 exporter 가 landmark 를 digest 에 넣으므로 맞춥니다 (로봇은 항상 네 파일이라 충돌 없음) |
| 개수 필드 | 요구하지 않음 (`json_data` 의 의미가 불명) |
| 다운로드 | 실제로 올라온 세션에만 링크를 냅니다 |
| 시계열 조회 | 제공하지 않음 (`columns` 를 만들 수 없으므로) |

**전환 지점은 두 파일입니다.**

| 파일 | 무엇을 정하는가 |
|---|---|
| `apps/landmark_contract.py` | 형식·필수 여부·digest 포함 여부 |
| `apps/limits.py` | part 상한. Django 요청 상한이 자동으로 따라옵니다 |

`limits.py` 를 고치면 Nginx 도 함께 바꿔야 하는데, 설정 파일이라 import 할 수 없습니다. 그래서 시험이 conf 를 읽어 대조하고 **넣을 값을 알려 줍니다.**

```
FAIL: test_nginx_conf_matches_part_limits
AssertionError: nginx conf 의 client_max_body_size 를 391M 이상으로 고치세요
```

확정이 필요한 항목은 [`docs/pending-decisions.md`](docs/pending-decisions.md) 에 정리했습니다.

---

## 구조

```
thing_database_web/
├─ docs/pending-decisions.md      확정이 필요한 항목
├─ deploy/                        nginx conf · systemd unit
├─ backend/
│  ├─ config/                     settings · urls
│  └─ apps/
│     ├─ models.py                Session 1개 모델
│     ├─ upload_views.py          쓰기 엔드포인트. 8단계 검증
│     ├─ read_views.py            목록·상세·시계열·다운로드
│     ├─ validators.py            part 상한 · metadata schema · CSV 규칙
│     ├─ digest.py                content_digest canonical 계산
│     ├─ storage.py               경로 생성 · staging → sessions
│     ├─ device_auth.py           Bearer 토큰
│     ├─ landmark_contract.py     미확정 landmark 계약 단일 출처
│     ├─ limits.py                업로드 크기 상한 단일 출처
│     ├─ errors.py throttles.py health.py serializers_v1.py
│     └─ tests*.py                140건
└─ frontend/
   ├─ src/views/                  HomeView · SessionListView · SessionDetailView
   ├─ src/services/               axios 클라이언트
   ├─ src/ui/ src/utils/
   └─ src/**/*.test.*             48건
```

React + Vite + Motion + recharts. 한국어가 화면의 대부분이라 Pretendard 를 쓰고 계측값은 JetBrains Mono 로 등폭 자릿수를 맞춥니다.

---

## 시험

```bash
cd backend  && python manage.py test apps    # 140건
cd frontend && npm test                      # 48건
```

| 파일 | 건수 | 범위 |
|---|---|---|
| `tests_read.py` | 55 | 목록·상세·시계열·다운로드·공개 경계 |
| `tests_upload.py` | 38 | 인증·part·schema·digest·중복 |
| `tests_session.py` | 28 | 경로 생성·staging→sessions 원자성 |
| `tests.py` | 11 | 상한 관계·nginx conf 대조·health |
| `tests_landmark.py` | 8 | **landmark part 경계** |

`tests_landmark.py` 는 형식이 미정인 동안 **무엇이 되고 무엇이 안 되는지**를 고정합니다.

1. 세 part 업로드가 계속 동작하는가
2. 네 part 업로드가 통과하고 내려받아지는가
3. 깨진 JSON·CSV 위장을 거부하는가
4. part 는 왔는데 metadata 선언이 없으면 거부하는가
5. landmark 가 `content_digest` 에 포함되는가 (로봇 exporter 와 일치)

5번은 특히 중요합니다. digest 가 흔들리면 같은 세션이 409 로 충돌합니다.

---

## 하지 않는 것

- 로봇 제어·mode·기록·Safety Reset (FR-50)
- ROS 2·rclpy·DDS 참여 (7.3절)
- 영상·rosbag2 공개
- S3 (7.4절에서 최소 검수 제외)
- 로그인·회원 관리 — 공개 GET 은 인증이 없고 업로드는 장치 토큰만 씁니다

---

## 남은 항목

| 항목 | 상태 |
|---|---|
| LandMark JSON 형식 | [pending-decisions.md](docs/pending-decisions.md) P-1 |
| landmark 를 digest 에 넣는가 | **해결** — 포함(P-2, 로봇 따라) |
| landmark part 상한 | 같은 문서 P-3 |
| landmark 시계열 조회 | 같은 문서 P-4 |
| 업로더 네 part 전환 | 같은 문서 C-1 |
| 세션 보존·정리 정책 | NFR-29. 미구현 |
