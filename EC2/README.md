# ec2_server

로봇 모터 로그를 수집·공개하는 **EC2 데이터 포털**입니다.

로봇이 기록 세션을 완료하면 metadata JSON, HandCommand CSV, MotorStatus CSV **세 파일을 한 번에** 업로드합니다. 서버는 내용을 검증한 뒤 원자적으로 공개하고, 세션을 조회·다운로드할 수 있습니다.

`요구사항 명세서 V6.3` 3.9절의 EC2 데이터 포털에 해당합니다. 로봇 제어·안전 기능은 포함하지 않습니다.

```
브라우저 ──443/tcp(HTTPS)──▶ Nginx ──내부 8000──▶ Gunicorn (Django) ──▶ SQLite
로봇     ──443/tcp(HTTPS)──▶   │                                    └─▶ 세션 파일
                                │
                                ├─ /            React 빌드 결과물 (dist)
                                ├─ /api/v1/     세션 데이터 API
                                └─ /static/     admin 정적 파일
```

| 항목 | 값 |
|---|---|
| 배포 URL | `https://i15c103.p.ssafy.io` (HTTPS 전용, 80 차단) |
| 저장소 | `https://lab.ssafy.com/hibears123456/ec2_server` (`master`) |
| 서버 경로 | `/home/ubuntu/ec2_server` |
| 데이터 경로 | `/var/lib/thing-data` |
| 백엔드 | Django 6.0 + DRF + Gunicorn (3 workers) |
| 프런트엔드 | React 19 + Vite 8 + React Router 7 + recharts 3 |
| 데이터 | SQLite + 로컬 파일 |
| 배포 방식 | Docker 없이 Nginx + systemd 네이티브 |
| 테스트 | 110건 |

---

## 목차

1. [시스템 구조](#1-시스템-구조)
2. [데이터 계약](#2-데이터-계약)
3. [API](#3-api)
4. [디렉터리](#4-디렉터리)
5. [설정](#5-설정)
6. [화면](#6-화면)
7. [로컬 개발](#7-로컬-개발)
8. [배포 — 수정 반영](#8-배포--수정-반영)
9. [배포 — 최초 구축](#9-배포--최초-구축)
10. [검증](#10-검증)
11. [트러블슈팅](#11-트러블슈팅)
12. [요구사항 충족 현황](#12-요구사항-충족-현황)

로봇 측 구현 안내는 별도 문서로 로봇 팀에 직접 전달합니다.
저장소에는 포함하지 않습니다.

---

## 1. 시스템 구조

| 계층 | 기술 | 역할 | 실행 방식 |
|---|---|---|---|
| 리버스 프록시 | Nginx | 443 수신, TLS 종료, 정적 파일 서빙, `/api` 프록시 | systemd (`nginx`) |
| 애플리케이션 서버 | Gunicorn | Django WSGI 구동, `127.0.0.1:8000` 바인딩 | systemd (`thing-database-web`) |
| 백엔드 | Django + DRF | 세션 API, 검증, ORM | Gunicorn 내부 |
| 프런트엔드 | React + Vite | SPA. 빌드 후 정적 파일로만 존재 | 빌드 산출물 (`frontend/dist`) |
| 데이터베이스 | SQLite | 세션 메타데이터, rate limit 카운터 | `/var/lib/thing-data/db.sqlite3` |
| 파일 저장소 | 로컬 파일시스템 | 세션 파일 3종 | `/var/lib/thing-data/sessions/` |

Docker는 사용하지 않습니다.

### 알아둘 동작

**HTTPS 전용입니다.** 포트 80은 nginx가 듣지 않고 ufw도 차단합니다. 와일드카드 인증서 `*.p.ssafy.io`를 사용하며 DNS-01로 발급된 것이라 ACME 갱신에 80이 필요하지 않습니다. `http://`로 접속하면 연결되지 않으니 팀·시연 대상에게 `https://` 전체 URL을 안내해야 합니다.

**8000번은 외부에 노출되지 않습니다.** Nginx가 `127.0.0.1:8000`으로만 접속하고 ufw는 22와 443만 허용합니다.

**세션 파일은 API를 통해서만 나갑니다.** 다운로드 엔드포인트가 저장된 SHA-256과 실제 파일을 대조한 뒤 스트리밍하므로, Nginx가 데이터 디렉터리를 직접 노출하지 않습니다.

**데이터는 배포 디렉터리 밖에 있습니다.** systemd `StateDirectory=thing-data`가 `/var/lib/thing-data`를 생성·소유하므로 `git pull`이나 재배포가 데이터를 건드리지 않습니다.

**`/admin/`은 공개되지 않습니다.** Django에는 admin이 남아 있지만 Nginx가 프록시하지 않아 SPA fallback으로 흘러갑니다. 필요하면 SSH 터널로 접근합니다.

```bash
ssh -i <pem> -L 8000:127.0.0.1:8000 ubuntu@i15c103.p.ssafy.io
# 브라우저에서 http://localhost:8000/admin/
```

---

## 2. 데이터 계약

### 세션과 세 파일

한 번의 기록 세션이 **정확히 세 파일**을 만듭니다. 파일명은 서버가 canonical 규칙으로 생성하며 클라이언트 파일명은 저장에 쓰지 않습니다.

```
session_{session_id}_metadata.json      세션 메타데이터
session_{session_id}_hand_command.csv   7논리축 최종 명령 시계열
session_{session_id}_motor_status.csv   모터별 상태 시계열
```

`session_id`는 ROS `uint64`이지만 JSON·API·SQLite에서는 **문자열**로 다룹니다. double 정밀도를 넘기 때문입니다.

### `content_digest` — 멱등성의 기준

`exported_at`과 `content_digest` 자신을 제외한 metadata 전체를 canonical JSON(key 오름차순, 공백 없음, UTF-8)으로 직렬화한 SHA-256입니다.

`exported_at`을 제외하므로 **같은 rosbag2를 수동 재생성해도 digest가 같습니다.** 재전송이 중복 없이 처리되는 근거입니다.

| 상황 | 응답 |
|---|---|
| 신규 `(robot_id, session_id)` | `201` 생성 후 공개 |
| 같은 key, 같은 digest | `200` 기존 유지, 파일 덮어쓰지 않음 |
| 같은 key, 다른 digest | `409` 거부, 기존 데이터 보존 |

계산 규칙은 `apps/digest.py`에 있고 로봇 측과 교차 검증할 테스트 벡터가 코드에 포함되어 있습니다. 규칙이 어긋나면 인증은 통과하는데 모든 업로드가 `422`로 거부되므로, 구현 전 대조가 필요합니다.

### 업로드 검증 파이프라인

각 단계에서 지정 상태 코드로 즉시 중단합니다.

```
Bearer token 검증                        → 401
part 3개 정확히 / 추가 part 거부          → 400
content type                             → 415
part별·합계 크기 상한                     → 413
staging 스트리밍 저장 + fsync
metadata JSON schema v1                  → 422
CSV header·행 수·유한값·비감소 timestamp  → 422
파일별 SHA-256 == metadata 값             → 422
content_digest 재계산 == 요청값           → 422
token robot == metadata.robot_id         → 401
(robot_id, session_id) 조회 → 201 / 200 / 409
```

### 원자성

```
/var/lib/thing-data/
├─ staging/<robot_id>/<session_id>/     검증 중. 공개하지 않는다
└─ sessions/<robot_id>/<session_id>/    READY. 공개 대상
```

검증을 통과하면 같은 파일시스템 내에서 `os.replace()`로 원자적으로 옮깁니다. `staging`과 `sessions`를 같은 루트에 둔 이유는 rename 원자성이 동일 파일시스템에서만 보장되기 때문입니다. 다른 마운트로 분리하면 copy+unlink로 대체되어 중간 상태가 노출됩니다.

실패한 시도의 staging은 정리되며, crash 후 재시도 시에도 이전 부분 파일이 제거됩니다.

### 크기 상한

세 계층에 나뉘어 있어 **변경할 때 함께 맞춰야 합니다.**

| 위치 | 설정 | 값 |
|---|---|---|
| `deploy/nginx_thing_database_web.conf` | `client_max_body_size` | 90 M |
| `backend/config/settings.py` | `DATA_UPLOAD_MAX_MEMORY_SIZE` | 85 MiB |
| `backend/apps/validators.py` | `PART_MAX_BYTES` | metadata 256 KiB / hand_command 20 MiB / motor_status 60 MiB |

세 part 합계 상한은 80.25 MiB입니다.

### CSV 규칙

UTF-8(BOM 금지), RFC 4180 quoting, comma, header 1행, LF, 소수점 `.`, bool `true|false`. `NaN`·`Infinity` 금지이며 읽기 실패 숫자는 빈 칸으로 둡니다. 모든 행에 `session_id`를 포함하고 `(stamp_sec, stamp_nanosec)`는 비감소여야 합니다.

MotorStatus는 **한 수신 시각의 모터 하나가 한 행**으로 평탄화됩니다. 모터 12개면 같은 timestamp로 12행입니다.

header 정의는 `apps/validators.py`의 `HAND_COMMAND_HEADER`, `MOTOR_STATUS_HEADER`가 단일 출처입니다.

---

## 3. API

기준 URL `https://i15c103.p.ssafy.io`

| 메서드 | 경로 | 인증 | 용도 |
|---|---|---|---|
| POST | `/api/v1/uploads/sessions` | Bearer | 세션 업로드 (3 파일) |
| GET | `/api/v1/sessions` | — | 목록 |
| GET | `/api/v1/sessions/{session_id}` | — | 상세 |
| GET | `/api/v1/sessions/{session_id}/data` | — | 시계열 |
| GET | `/api/v1/sessions/{session_id}/download/{file_kind}` | — | 파일 다운로드 |
| GET | `/health` | — | 상태 확인 |

모든 시각은 RFC 3339 UTC `Z`이고 모든 Session ID는 문자열입니다. GET은 `status=READY`인 세션만 반환하며 STAGING·FAILED는 존재하지 않는 것처럼 `404`가 됩니다.

### 인증

업로드만 `Authorization: Bearer <device-token>`을 요구합니다. 서버는 **평문 token을 저장하지 않고** SHA-256 hash만 `.env`에 보관하며 상수시간으로 비교합니다. token은 `robot_id`와 짝지어져 있어 `metadata.robot_id`가 다르면 `401`입니다.

`Idempotency-Key: <robot_id>:<session_id>:<data_version>:<content_digest>`를 함께 보내면 서버가 metadata와 대조합니다. `content_digest`에 `sha256:` 접두사가 있어 **키 안에 콜론이 4개** 나타나므로, split하지 말고 문자열을 그대로 조립해야 합니다.

### rate limit

| 대상 | 한도 |
|---|---|
| 공개 GET | IP당 120 / 분 |
| 업로드 POST | token당 10 / 분 |

초과 시 `429` + `Retry-After`입니다. 카운터는 SQLite `DatabaseCache`로 gunicorn 워커 3개가 공유합니다. `LocMemCache`를 쓰면 워커마다 독립 카운터가 생겨 실효 한도가 3배가 되므로 사용하지 않습니다.

### 목록

```
GET /api/v1/sessions?session_id=&result=&robot_id=&cursor=&limit=
```

기본 20건, 최대 100건. 정렬은 `started_at DESC, session_id DESC`. `next_cursor`는 opaque 문자열이며 클라이언트가 내부 형식을 해석하지 않습니다.

```json
{
  "items": [{
    "session_id": "123456789012345678",
    "robot_id": "THING-001",
    "started_at": "2026-07-29T00:00:00.000Z",
    "ended_at": "2026-07-29T00:01:00.000Z",
    "uploaded_at": "2026-07-29T00:01:06.000Z",
    "result": "SUCCESS",
    "duration_ms": 60000,
    "row_counts": {"hand_command": 1200, "motor_status": 4200},
    "file_sizes": {"metadata": 980, "hand_command": 1234, "motor_status": 5678}
  }],
  "next_cursor": null
}
```

### 상세

목록 필드에 `schema_version`, `data_version`, `interface_commit`, `time_sync`, `content_digest`, `downloads`가 추가됩니다. `downloads`는 세 파일의 상대경로입니다.

### 시계열

```
GET /api/v1/sessions/{session_id}/data?dataset=hand_command|motor_status&cursor=&limit=
```

기본 1000행, 최대 5000행, timestamp 오름차순. `columns`는 CSV header에서 `session_id`를 제외한 목록입니다. 정수·실수·boolean은 JSON 기본 타입이고 **읽기 실패 숫자는 `null`** 입니다.

### 다운로드

`file_kind`는 `metadata|hand_command|motor_status` enum이며 경로 입력을 받지 않습니다. 저장된 SHA-256과 실제 파일이 일치하는 READY만 제공하고 불일치하면 `404`입니다. 응답에 `Content-Disposition: attachment`와 canonical 파일명이 붙습니다.

### 오류 응답

```json
{"error":{"code":"SESSION_CONTENT_CONFLICT",
          "message":"The session already exists with different content.",
          "details":[]},
 "request_id":"..."}
```

`400` malformed, `401` 인증, `404` 없음, `405` 공개 쓰기, `409` 충돌, `413` 크기, `415` media type, `422` schema·값, `429` rate limit, `500` 내부, `503` DB·디스크 불가를 사용합니다. **경로·토큰·stack trace는 응답에 넣지 않고** 서버 로그로만 보냅니다. `request_id`로 로그와 대조할 수 있습니다.

재시도해도 되는 것은 `429`, `500`, `503`뿐입니다.

### `/health`

```json
{"status":"ok","checks":{"database":true,"data_dir":true}}
```

SQLite에 `SELECT 1`을 실행하고 데이터 디렉터리에 실제로 파일을 써 봅니다. 존재 확인만으로는 권한 문제를 잡지 못하기 때문입니다. 하나라도 실패하면 `503`이며 응답에 경로·비밀정보를 넣지 않습니다.

---

## 4. 디렉터리

```
ec2_server/
├─ README.md                          이 문서
└─ thing_database_web/
   ├─ backend/
   │  ├─ config/
   │  │  ├─ settings.py               환경변수, 크기 상한, 보안, 캐시
   │  │  ├─ urls.py                   라우팅
   │  │  ├─ wsgi.py                   Gunicorn 진입점
   │  │  └─ .env                      ★ git 추적 안 함
   │  ├─ apps/
   │  │  ├─ models.py                 Session
   │  │  ├─ digest.py                 content_digest 계산 + 테스트 벡터
   │  │  ├─ storage.py                저장 레이아웃, staging → 원자적 커밋
   │  │  ├─ validators.py             metadata schema v1, CSV 규칙
   │  │  ├─ device_auth.py            Bearer token (hash 비교)
   │  │  ├─ errors.py                 오류 코드와 envelope
   │  │  ├─ throttles.py              rate limit
   │  │  ├─ upload_views.py           POST 업로드
   │  │  ├─ read_views.py             공개 GET 4종
   │  │  ├─ serializers_v1.py         응답 직렬화, cursor, 타입 변환
   │  │  ├─ health.py                 /health
   │  │  └─ tests*.py                 테스트 110건 (4개 모듈)
   │  ├─ staticfiles/                 ★ collectstatic 산출물
   │  └─ requirements.txt
   ├─ frontend/
   │  ├─ src/
   │  │  ├─ main.jsx                  엔트리, BrowserRouter
   │  │  ├─ App.jsx                   네비게이션 + 라우트 (상세는 lazy)
   │  │  ├─ index.css                 전역 스타일
   │  │  ├─ services/sessions.js      v1 API 클라이언트
   │  │  ├─ utils/format.js           UTC·단위·결측 표시
   │  │  └─ views/                    HomeView, SessionListView, SessionDetailView
   │  ├─ .env.production              VITE_API_BASE_URL=/api
   │  └─ dist/                        ★ 빌드 산출물
   └─ deploy/
      ├─ nginx_thing_database_web.conf
      └─ thing-database-web.service

/var/lib/thing-data/                  ★ systemd StateDirectory 소유
├─ db.sqlite3
├─ staging/<robot_id>/<session_id>/
└─ sessions/<robot_id>/<session_id>/
```

★ 표시는 `.gitignore` 대상이거나 배포 디렉터리 밖입니다. `git pull`이 건드리지 않습니다. 반대로 서버에만 있는 파일은 `git pull`로 삭제되지도 않습니다.

---

## 5. 설정

### `backend/config/.env` — git 추적 안 함

| 키 | 용도 |
|---|---|
| `SECRET_KEY` | Django 서명 키 |
| `DEBUG` | 배포에서는 `False` |
| `ALLOWED_HOSTS` | 허용 호스트. 배포 도메인 포함 필수 |
| `CORS_EXTRA_ORIGINS` | 추가 CORS 허용 origin (`https://` 로) |
| `CSRF_TRUSTED_ORIGINS` | admin 로그인용 신뢰 origin (`https://` 로) |
| `DEVICE_TOKENS` | `<robot_id>:<sha256hex>` 쉼표 구분 |
| `DB_PATH` | `/var/lib/thing-data/db.sqlite3` |
| `EC2_DATA_DIR` | 기본 `/var/lib/thing-data` |

파일 권한은 `600`입니다. 새 환경변수를 추가한 커밋을 배포하면 **서버의 `.env`를 직접 편집해야 합니다.**

```bash
nano ~/ec2_server/thing_database_web/backend/config/.env
chmod 600 ~/ec2_server/thing_database_web/backend/config/.env
sudo systemctl restart thing-database-web
```

### 장치 token 발급

```bash
TOKEN=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')
HASH=$(printf '%s' "$TOKEN" | sha256sum | awk '{print $1}')

echo "  .env 에 넣을 줄:    DEVICE_TOKENS=THING-001:$HASH"
echo "  로봇에 전달할 평문: $TOKEN"
```

`printf '%s'`를 씁니다. `echo`는 개행을 붙여 hash가 달라지고 영구히 `401`이 됩니다.

### 보안 설정

`DEBUG=False`일 때 `SECURE_PROXY_SSL_HEADER`, `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_HSTS_SECONDS`가 적용됩니다.

`SECURE_PROXY_SSL_HEADER`가 없으면 Django가 요청을 HTTP로 인식해 `SECURE_SSL_REDIRECT`가 무한 리다이렉트를 만듭니다. Nginx가 `X-Forwarded-Proto`를 넘기고 있어 짝이 맞습니다.

`SECURE_HSTS_INCLUDE_SUBDOMAINS`와 `SECURE_HSTS_PRELOAD`는 **의도적으로 `False`** 입니다. `p.ssafy.io`는 여러 팀이 공유하는 도메인이라 `includeSubDomains`를 켜면 방문자 브라우저가 형제 서브도메인까지 HTTPS를 강제해 다른 팀 사이트 접속을 막을 수 있습니다. 이 때문에 발생하는 `security.W005`·`W021` 경고는 `SILENCED_SYSTEM_CHECKS`로 근거와 함께 제외했습니다.

### `deploy/` 안의 배포 경로

프로젝트 경로가 두 파일에 하드코딩되어 있습니다. 기준은 `/home/ubuntu/ec2_server`입니다.

| 파일 | 항목 |
|---|---|
| `nginx_thing_database_web.conf` | `root`, `alias` |
| `thing-database-web.service` | `WorkingDirectory`, `Environment`, `ExecStart` |

### TLS 인증서

SSAFY가 제공하는 와일드카드 `*.p.ssafy.io`를 사용합니다.

```
/etc/letsencrypt/live/p.ssafy.io/fullchain.pem
/etc/letsencrypt/live/p.ssafy.io/privkey.pem
```

certbot의 `--nginx` 플러그인은 쓰지 않습니다. 배포 절차가 저장소의 conf를 `/etc/nginx/`로 복사하므로, certbot이 `/etc` 쪽을 직접 수정하면 다음 배포에서 TLS 설정이 사라집니다. **TLS 설정의 단일 출처는 저장소의 conf 파일입니다.**

DNS-01로 발급된 인증서라 갱신에 포트 80이 필요하지 않습니다.

### 포트

| 포트 | 노출 | 용도 |
|---|---|---|
| 22 | 외부 | SSH |
| 443 | 외부 | HTTPS |
| 80 | **차단** | nginx가 듣지 않고 ufw도 거부 |
| 8000 | 내부만 | Gunicorn |
| 5173 | 로컬 개발만 | Vite dev 서버 |

---

## 6. 화면

| 경로 | 내용 |
|---|---|
| `/` | 랜딩 |
| `/sessions` | 세션 목록 — Session ID 검색, 판정 필터, cursor 페이지네이션 |
| `/sessions/:sessionId` | 세션 상세 — 메타, 다운로드, 7논리축 차트, 모터 상태 표 |

`BrowserRouter`를 사용하므로 주소창에 `/sessions`를 직접 입력해도 동작해야 합니다. Nginx의 `try_files $uri $uri/ /index.html`과 짝을 이룹니다.

상세 화면만 recharts를 쓰기 때문에 `React.lazy`로 코드 분할했습니다. 초기 번들 약 285 KB(gzip 94 KB), 차트 청크 약 375 KB(gzip 108 KB)로 목록까지는 차트 라이브러리를 받지 않습니다.

### 표시 규칙

**시각은 UTC로 표시하고 그 사실을 화면에 명시합니다.** `toLocaleString()`을 쓰지 않고 RFC 3339 문자열을 직접 파싱합니다. 브라우저 시간대로 변환되면 UTC 명시가 무의미해집니다.

**결측과 0을 구분합니다.** `null`은 `—`, `0`은 `0.000`으로 표시됩니다. 차트는 `connectNulls={false}`로 결측 구간에서 선이 끊어집니다.

**통신 실패를 구분합니다.** `communication_ok`가 `false`면 행 배경이 붉게 표시되고 `null`이면 `불명`으로 별도 표기됩니다.

**모터축 각도 고지.** 표의 rad 값은 모터 축 기준이며 실제 관절각이 아닙니다. 텐던 구동이라 모터 회전과 관절 굴곡이 1:1로 대응하지 않습니다. 화면에 고지 문구가 표시됩니다.

MotorStatus는 평탄화된 행이 많아 그대로 나열하면 읽기 어려우므로, 모터별 최신 샘플을 표로 보여주고 전체는 CSV 다운로드로 안내합니다.

---

## 7. 로컬 개발

두 개의 터미널이 필요합니다.

### 백엔드

```bash
cd thing_database_web/backend
python3 -m venv .venv
source .venv/bin/activate          # Windows Git Bash: source .venv/Scripts/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createcachetable
python manage.py runserver          # http://127.0.0.1:8000
```

`config/.env`가 필요합니다. 저장소에 없으므로 팀에서 받아 배치하세요.

### 프런트엔드

```bash
cd thing_database_web/frontend
npm install
npm run dev                        # http://localhost:5173
```

Node `^20.19.0 || >=22.12.0`이 필요합니다 (Vite 8 요구사항).

Vite dev 서버가 `/api`를 `127.0.0.1:8000`으로 프록시하므로 로컬에서도 배포와 동일하게 상대경로로 동작합니다. 백엔드를 먼저 띄워두세요.

### 테스트

```bash
cd thing_database_web/backend
source .venv/bin/activate
python manage.py test apps          # Ran 110 tests ... OK
```

| 모듈 | 대상 |
|---|---|
| `tests.py` | 크기 상한, `/health`, 레거시 제거 확인 |
| `tests_session.py` | Session 모델, 저장 레이아웃, `content_digest` |
| `tests_upload.py` | 업로드 API, 상태 코드별 시나리오 |
| `tests_read.py` | 공개 GET 4종, 페이지네이션, 무결성, rate limit |

---

## 8. 배포 — 수정 반영

### 실행 환경

| 작업 | 셸 | 위치 |
|---|---|---|
| 로컬 git 작업 | Git Bash (VS Code 터미널) | 예: `/c/ahnlabs/serve/ec2_server` |
| EC2 접속 | PowerShell | `ssh -i "C:\Users\.ssh\I15C103T.pem" ubuntu@i15c103.p.ssafy.io` |
| EC2 작업 | bash | `~/ec2_server` |

PowerShell은 접속 통로일 뿐이고 접속 후 명령은 모두 bash입니다. 세션이 끊겨도 이어가려면 `tmux new -s deploy` / `tmux attach -t deploy`를 씁니다.

### 로컬에서 push

```bash
cd /c/ahnlabs/serve/ec2_server

# 프런트엔드를 수정했다면 push 전에 빌드를 통과시킨다.
cd thing_database_web/frontend && npm run build && cd ../..

git status
git add -A               # -A: 삭제한 파일까지 스테이징
git commit -m "작업 내용"
git push
```

`git add .`는 삭제를 놓칠 수 있습니다. `.env`, `db.sqlite3`가 목록에 보이면 커밋하지 마세요.

### EC2에서 적용

```bash
cd ~/ec2_server
git pull

# ── 백엔드 ──
cd ~/ec2_server/thing_database_web/backend
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check && python manage.py test apps
deactivate
```

**`test apps`가 통과한 뒤에** 재시작합니다. 통과하지 못하면 배포하지 않는 편이 낫습니다.

```bash
sudo systemctl restart thing-database-web
sleep 3
systemctl is-active thing-database-web
```

`sleep 3`이 필요합니다. Gunicorn이 워커 3개를 띄우고 Django를 로딩하는 데 1~3초가 걸려, 곧바로 요청하면 `502`가 나옵니다.

```bash
# ── 프런트엔드 ──
cd ~/ec2_server/thing_database_web/frontend
rm -rf node_modules dist
npm ci
npm run build
```

`deploy/` 안의 파일이 바뀌지 않았다면 여기까지로 배포가 끝납니다.

```bash
# ── 배포 설정 (deploy/ 안의 파일이 바뀐 경우) ──
cd ~/ec2_server/thing_database_web/deploy

sudo cp thing-database-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart thing-database-web
```

**Nginx는 반영 전에 확인을 따로 실행합니다.** 아래 한 줄만 먼저 돌리세요.

```bash
grep -E "^\s*listen|^\s*ssl_certificate" \
  ~/ec2_server/thing_database_web/deploy/nginx_thing_database_web.conf
```

출력이 이래야 합니다.

```
    listen 443 ssl http2;
    ssl_certificate     /etc/letsencrypt/live/p.ssafy.io/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/p.ssafy.io/privkey.pem;
```

`listen 80`이 보이면 **멈춰야 합니다.** HTTPS 설정이 아닌 파일을 반영하면 ufw가 80을
막고 있어 사이트가 즉시 접속 불가가 됩니다. `nginx -t`는 문법만 검사하고 `listen` 값이
적절한지는 보지 않으므로 통과해 버립니다.

`location` 목록도 함께 확인하면 안전합니다.

```bash
grep -E "^\s*location" ~/ec2_server/thing_database_web/deploy/nginx_thing_database_web.conf
```

`/api/`, `= /health`, `/static/`, `/` 네 개가 나와야 합니다. `= /health`가 빠지면
`/health`가 SPA fallback으로 흘러가 JSON 대신 HTML이 반환됩니다.

확인이 끝나면 반영합니다.

```bash
sudo cp ~/ec2_server/thing_database_web/deploy/nginx_thing_database_web.conf \
        /etc/nginx/sites-available/thing_database_web
sudo nginx -t && sudo systemctl restart nginx
sudo ss -tlnp | grep -E ':80 |:443 '
```

`:443`만 나와야 합니다. `reload` 대신 `restart`를 쓰는 이유는 구 워커가 이전 리스닝
소켓을 붙잡고 있어 `:80`과 `:443`이 함께 보이는 과도 상태가 생기기 때문입니다.

### 반드시 지킬 네 가지

**`npm install`이 아니라 `npm ci`** — `npm install`은 `package-lock.json`을 수정할 수 있고 이 파일은 git 추적 대상이라 다음 `git pull`이 막힙니다.

```
error: Your local changes to the following files would be overwritten by merge:
       thing_database_web/frontend/package-lock.json
```

`npm ci`는 락파일을 건드리지 않고 `node_modules`를 자동으로 비우고 시작합니다.

**`deploy/` 파일은 `sudo cp`가 필요** — `git pull`은 저장소에만 받아옵니다. `/etc/`로 복사되지 않으므로 `sudo cp`를 해야 적용됩니다. `systemctl restart`만으로는 unit 파일 변경이 반영되지 않으니 **`sudo cp` → `daemon-reload`** 순서를 지킵니다.

**디렉터리 이동은 절대경로로** — `deactivate` 후 홈으로 돌아온 상태에서 `cd ../deploy`를 하면 `/home/deploy`를 찾습니다.

**EC2에서는 `git commit`·`push`를 하지 않습니다** — 배포 서버는 읽기 전용입니다. GitLab이 `403 You are not allowed to upload code`로 거부합니다. 서버에서 파일을 급히 고쳤다면 그 내용을 로컬로 옮겨 push하고, EC2는 `git pull`로만 받습니다.

서버에 로컬 커밋이 남아 있으면 다음 `git pull`이 갈라집니다. 정리는 이렇게 합니다.

```bash
cd ~/ec2_server
cp /etc/nginx/sites-available/thing_database_web /tmp/nginx_good.conf   # 보험
git fetch origin
git reset --hard origin/master
git status -sb        # ## master...origin/master (ahead/behind 없음)
```

### 변경 범위별 필요 단계

| 수정 범위 | 필요한 단계 |
|---|---|
| `.py` 파일만 | `git pull` → `test` → `restart` |
| `models.py` (마이그레이션 발생) | `git pull` → `migrate` → `test` → `restart` |
| `requirements.txt` | `git pull` → `pip install` → `restart` |
| `frontend/src` | `git pull` → `npm run build` |
| `package.json` | `git pull` → `npm ci` → `npm run build` |
| `deploy/*.conf`, `*.service` | 위의 "배포 설정" 블록 |
| `settings.py` STATIC 관련 | `collectstatic --noinput` |
| 캐시 설정 신규 | `createcachetable` |

애매하면 전체를 실행해도 무해합니다. `npm ci`가 1~2분 걸리는 것 외에 부작용은 없습니다.

### 백업 (큰 변경 전)

```bash
cp /var/lib/thing-data/db.sqlite3 ~/db.sqlite3.bak.$(date +%Y%m%d_%H%M)
sudo tar czf ~/sessions.bak.$(date +%Y%m%d_%H%M).tar.gz -C /var/lib/thing-data sessions
cp ~/ec2_server/thing_database_web/backend/config/.env ~/env.bak.$(date +%Y%m%d_%H%M)
```

---

## 9. 배포 — 최초 구축

새 서버에 처음 올릴 때만 필요합니다.

### 로컬 준비 (Windows PowerShell)

pem 키 권한을 현재 사용자만 읽게 걸어야 `ssh`가 거부하지 않습니다.

```powershell
icacls "C:\Users\<사용자>\.ssh\I15C103T.pem" /inheritance:r
icacls "C:\Users\<사용자>\.ssh\I15C103T.pem" /grant:r "$($env:USERNAME):(R)"
ssh -i "C:\Users\<사용자>\.ssh\I15C103T.pem" ubuntu@i15c103.p.ssafy.io
```

ufw를 건드리다 접속이 끊길 수 있으니 **터미널 2~3개를 미리 접속해 둡니다.**

### 시스템 패키지

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip nginx

# Node — Vite 8 요구사항: ^20.19.0 || >=22.12.0
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

node -v && npm -v && python3 --version && nginx -v
```

### ufw

```bash
sudo ufw allow 443/tcp
sudo ufw status numbered      # 22, 443 두 개만 ALLOW
```

80과 8000은 열지 않습니다. AWS 보안 그룹에도 443 인바운드가 필요합니다.

### 소스와 백엔드

```bash
cd /home/ubuntu
git clone https://lab.ssafy.com/hibears123456/ec2_server.git
cd ec2_server/thing_database_web/backend

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`config/.env`를 배치한 뒤 진행합니다.

```bash
python manage.py migrate
python manage.py createcachetable
python manage.py collectstatic --noinput
python manage.py createsuperuser        # (선택) SSH 터널로 admin 접근할 때만
deactivate
```

### Gunicorn systemd 등록

```bash
sudo cp /home/ubuntu/ec2_server/thing_database_web/deploy/thing-database-web.service \
        /etc/systemd/system/thing-database-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now thing-database-web
sudo systemctl status thing-database-web       # active (running)
ls -ld /var/lib/thing-data                     # drwxr-x--- ubuntu ubuntu
```

`StateDirectory=thing-data`가 데이터 루트를 생성·소유합니다. `sudo mkdir`이 필요 없습니다.

### 프런트엔드 빌드

```bash
cd /home/ubuntu/ec2_server/thing_database_web/frontend
npm ci
npm run build
```

### TLS 인증서 확인

```bash
sudo certbot certificates      # p.ssafy.io / *.p.ssafy.io 가 있어야 함
```

없으면 SSAFY에 문의합니다. 와일드카드는 DNS-01 발급이라 서버에서 자체 갱신할 수 없습니다.

### Nginx

```bash
sudo cp /home/ubuntu/ec2_server/thing_database_web/deploy/nginx_thing_database_web.conf \
        /etc/nginx/sites-available/thing_database_web
sudo ln -sfn /etc/nginx/sites-available/thing_database_web /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

sudo nginx -t && sudo systemctl reload nginx
sudo ss -tlnp | grep -E ':80|:443'      # :443 만 있어야 함
```

### 재부팅 자동 기동

```bash
sudo systemctl is-enabled thing-database-web nginx ufw
```

---

## 10. 검증

```bash
B=https://i15c103.p.ssafy.io

# 반영 여부
cd ~/ec2_server && git log --oneline -1
systemctl is-active thing-database-web

# HTTP 차단 (exit=7 또는 28 이 정상)
curl -sI --max-time 5 http://i15c103.p.ssafy.io/ >/dev/null 2>&1; echo "HTTP 차단 exit=$?"

# 서비스 응답
curl -sI --max-time 10 $B/ | head -1                    # HTTP/2 200
curl -s --max-time 10 $B/health; echo                    # {"status":"ok",...}
curl -s --max-time 10 $B/api/v1/sessions; echo           # {"items":[],...}
curl -s -o /dev/null -w "admin css: %{http_code}\n" --max-time 10 $B/static/admin/css/base.css

# 조회 전용 경계
curl -s -o /dev/null -w "공개 쓰기    : %{http_code}  (405)\n" --max-time 10 -X POST $B/api/v1/sessions
curl -s -o /dev/null -w "무인증 업로드: %{http_code}  (401)\n" --max-time 10 -X POST $B/api/v1/uploads/sessions
curl -s -o /dev/null -w "레거시 API   : %{http_code}  (404)\n" --max-time 10 $B/api/motor-data/files/

# TLS
echo | openssl s_client -connect i15c103.p.ssafy.io:443 -servername i15c103.p.ssafy.io 2>/dev/null \
  | openssl x509 -noout -issuer -subject -dates
```

배포 설정을 바꿨다면 실제 적용을 확인합니다.

```bash
systemctl cat thing-database-web | grep -E "WorkingDirectory|ExecStart|StateDirectory"
sudo nginx -T 2>/dev/null | grep -E "root |alias |client_max_body_size|listen "
```

### 브라우저

`https://i15c103.p.ssafy.io` 접속 후 확인합니다.

1. 자물쇠 아이콘
2. 홈 → "세션 목록" 이동
3. `/sessions`를 주소창에 직접 입력하고 새로고침해도 404가 아닌지 (SPA fallback)
4. 세션이 있으면 상세 진입 → 차트와 모터 표
5. 다운로드 버튼 → 파일이 실제로 저장되는지
6. F12 콘솔에 에러가 없는지

---

## 11. 트러블슈팅

### `git pull`이 막힘

```
error: Your local changes to the following files would be overwritten by merge
```

서버에서 추적 파일이 변경된 상태입니다. 대부분 `npm install`이 만든 `package-lock.json`이며 저장소 버전이 정답이므로 버리면 됩니다.

```bash
git status --short
git checkout -- thing_database_web/frontend/package-lock.json   # 특정 파일만
git checkout -- .                                                # 추적 파일 전체
git pull
```

`.env`와 `/var/lib/thing-data`는 영향을 받지 않습니다. git은 첫 충돌에서 멈추므로 다시 막히면 반복합니다.

### 502 Bad Gateway

Gunicorn이 응답하지 않는 상태입니다. **재시작 직후라면 로딩 중일 수 있으니** 몇 초 뒤 다시 호출해 보세요.

```bash
systemctl is-active thing-database-web
sudo journalctl -u thing-database-web -n 40 --no-pager
```

계속되면 `.env`에 새 키가 없어 `settings.py` 로딩이 실패하는 경우가 흔합니다.

### 사이트가 안 뜸

`npm run build`는 시작할 때 `dist/`를 먼저 비웁니다. 빌드가 실패하면 `dist/`가 없는 상태이므로 원인을 고치고 다시 빌드해야 복구됩니다.

```bash
node -v                        # ^20.19.0 || >=22.12.0
cd ~/ec2_server/thing_database_web/frontend
npm ci && npm run build
ls dist/assets/
```

### 업로드가 401

```bash
sudo journalctl -u thing-database-web -n 20 --no-pager | grep -i "api error"
```

| 로그 | 원인 |
|---|---|
| `DEVICE_TOKENS 가 설정되지 않았다` | `.env` 미적용 또는 재시작 누락 |
| `Authorization Bearer 헤더 없음` | 헤더 형식 오류 |
| `일치하는 장치 token 없음` | 평문↔hash 불일치. `echo` 대신 `printf '%s'` 사용 확인 |
| `token robot=...` | `metadata.robot_id`가 등록값과 다름 |

`.env` 값과 Django가 읽은 값을 대조할 수 있습니다.

```bash
cd ~/ec2_server/thing_database_web/backend && source .venv/bin/activate
python manage.py shell -c "
from apps.device_auth import configured_tokens
for h, r in configured_tokens().items(): print(f'robot={r} hash={h[:12]}...')"
deactivate
```

### 업로드가 422

`details` 배열에 실패한 검증 항목이 담깁니다. `content_digest` 불일치가 가장 흔하며 로봇 측 canonical JSON 직렬화 규칙이 어긋난 경우입니다. 로봇 팀에 전달한 업로드 인터페이스 문서의 골든 샘플과 대조하세요. 계산 규칙의 단일 출처는 `apps/digest.py`의 `TEST_VECTOR`입니다.

### 업로드가 413

```bash
sudo nginx -T 2>/dev/null | grep client_max_body_size     # 90M
```

없으면 conf를 `sudo cp`하고 `reload`합니다.

### 다운로드가 404

READY 세션인데 404면 파일이 없거나 hash가 불일치하는 경우입니다.

```bash
sudo journalctl -u thing-database-web -n 30 --no-pager | grep -iE "다운로드|READY 세션"
sudo ls -l /var/lib/thing-data/sessions/THING-001/<session_id>/
```

### 무한 리다이렉트

`SECURE_SSL_REDIRECT`가 켜졌는데 `SECURE_PROXY_SSL_HEADER`가 없거나 Nginx가 `X-Forwarded-Proto`를 넘기지 않는 경우입니다.

```bash
curl -sIL --max-time 10 https://i15c103.p.ssafy.io/api/v1/sessions | grep -E "^HTTP|^location"
```

`200` 한 줄만 나와야 합니다.

### 롤백

```bash
cd ~/ec2_server
git log --oneline -5
git reset --hard <이전_커밋>

cd thing_database_web/frontend && npm ci && npm run build
cd ../deploy
sudo cp thing-database-web.service /etc/systemd/system/
sudo cp nginx_thing_database_web.conf /etc/nginx/sites-available/thing_database_web
sudo systemctl daemon-reload && sudo systemctl restart thing-database-web
sudo nginx -t && sudo systemctl reload nginx
```

마이그레이션을 되돌려야 하면 `python manage.py migrate apps <이전_번호>`를 씁니다.

---

### Node 버전 제약

```
Vite 8         ^20.19.0 || >=22.12.0
jsdom 28       ^20.19.0 || ^22.12.0 || >=24.0.0
```

현재 서버·로컬 모두 **v20.20.2** 입니다. 테스트 의존성을 올릴 때는 `engines.node` 를
확인해야 합니다. `npm ci` 는 `EBADENGINE` 경고만 내고 설치를 진행하므로, 설치는
성공하고 실행 시점에 실패합니다.

```bash
npm ci 2>&1 | grep EBADENGINE      # 출력이 없어야 정상
```

`npm audit` 은 `react-router` RSC Mode CSRF 권고 1건을 보고합니다.
RSC 모드와 서버 액션을 쓸 때의 문제이며, 이 프로젝트는 서버가 없는 정적 SPA 라
해당하지 않습니다. 수정에는 `react-router` 8.x 메이저 상향이 필요하므로
**`npm audit fix` 를 실행하지 않습니다.**

## 12. 요구사항 충족 현황

`요구사항 명세서 V6.3` 3.9절 기준입니다.

| ID | 요구사항 | 상태 |
|---|---|---|
| FR-46 | 완료 세션 직접 EC2 전송 | ✔ |
| FR-47 | 공개 세션 목록·상세 조회 | ✔ |
| FR-48 | 로봇 데이터 표시 (7논리축·모터 상태) | ✔ |
| FR-49 | 세 파일 다운로드 | ✔ |
| FR-50 | 조회 전용 보안 경계 | ✔ |
| FR-51 | 업로드 인증과 입력 검증 | ✔ |
| FR-52 | EC2 네이티브 배포·상태 확인 | ◐ |
| NFR-25 | 다운로드 무결성 (SHA-256) | ✔ |
| NFR-26 | 업로드 멱등성 (200/409) | ✔ |
| NFR-27 | 인증·공개 데이터 보호 | ✔ |
| NFR-29 | 저장 공간과 지속성 | ✔ |

**Must 11개 중 완전 충족 10개, 부분 충족 1개.**

### 남은 항목

**FR-52 — 관리용 22번 포트를 팀 고정 IP로 제한.** AWS 보안 그룹 설정이므로 SSAFY 인프라 담당과 협의가 필요합니다. 그 외 FR-52 세부 항목(Docker 미사용, HTTPS, 443만 개방, `/health`, 데이터 경로, React 빌드, Django test, deploy check)은 모두 충족합니다.

### 명세서와 다르게 구현한 부분

**저장소 구조** — 명세서 4.9절은 `web/ec2-portal/{frontend,backend}`를 지정하지만 현재는 별도 저장소 `ec2_server/thing_database_web/`입니다. 메인 저장소로 흡수할지 명세서를 개정할지 결정이 필요합니다.

**시계열 `columns`에서 `session_id` 제외** — 명세서의 hand_command 예시에는 `session_id`가 없는데 motor_status 설명은 "CSV header와 동일"이라 모순이 있습니다. `session_id`가 응답 최상위에 이미 있어 행마다 반복할 이유가 없으므로 두 dataset 모두 제외했습니다. `serializers_v1.py`의 `OMIT_SESSION_ID_COLUMN`을 `False`로 바꾸면 포함됩니다.

**장치 token 보관 방식** — 명세서 FR-51은 `0600` systemd `EnvironmentFile`을 언급하지만 현재는 `django-environ`이 `.env`(권한 `600`)를 읽습니다. 파일 소유자만 읽을 수 있고 서비스도 같은 사용자로 동작하므로 보호 수준은 동일합니다.

**ACME 갱신용 포트 80** — 와일드카드 인증서가 DNS-01로 발급되어 80이 필요하지 않으므로 완전히 차단했습니다. 직접 HTTP-01로 발급하는 구성이라면 `/.well-known/acme-challenge/`만 열어둬야 합니다.
