# thing 프로젝트 — 웹 파트 (thing_control_web / thing_database_web)

텐던 구동 로봇 손을 카메라 기반 손동작(MediaPipe)으로 모방 제어하거나 웹 버튼으로 조작하는
프로젝트의 **웹 파트 저장소**입니다. 요구사항 명세서 V5.2 기준으로 작성되었으며, 웹과 직접
관련 없는 로봇 손/ROS 2/Jetson/Raspberry Pi 파트는 이 저장소의 범위 밖입니다.

이 저장소에는 목적이 서로 다른 **두 개의 독립적인 Django+SPA 프로젝트**가 들어 있습니다.

| | `thing_control_web` | `thing_database_web` |
|---|---|---|
| 역할 | 로봇 손 **실시간 관제/조작** 웹 (요구사항 명세서 3.7절 FR-19~FR-28 대응) | **EC2에 쌓인 모터 로그를 회원 다운로드**로 배포하는 웹 (명세서에는 없는 신규 요구사항) |
| 실행 위치 | 로봇 손과 같은 내부망(폐쇄망) — 명세서 FR-28/NFR-20 | AWS EC2 (공개 배포 환경) |
| 프런트엔드 | React 19 + Vite (`bootstrap`) | Vue 3 + Vite |
| 백엔드 | Django 5 + Django Channels(ASGI, daphne) — WebSocket 중계 | Django(6) + DRF — REST API |
| DB | SQLite (세션 상태만, 영속 데이터 없음) | SQLite → 배포 시 RDS 등으로 대체 가능 (모델은 DB 독립적) |
| 외부 연결 | **없음.** ROS 2/EC2와 직접 통신하지 않음 | **로봇(라즈베리파이) → 업로드 API**, **회원 → 다운로드 API** |

두 프로젝트는 코드/DB/배포 서버가 완전히 분리되어 있고, 서로를 호출하지 않습니다.

---

## 1. 전체 데이터 흐름 (웹 파트 기준)

```
                          [내부망 / 폐쇄망]                                [AWS EC2 · 공개 배포]

 카메라 ──▶ Jetson(MediaPipe, thing_vision)                      thing_database_web
     │           │ ROS2 DDS                                         ┌──────────────────────┐
     │           ▼                                                  │  회원가입/로그인 (DRF) │
     │      Raspberry Pi(thing_control, thing_hardware) ──▶ 로봇 손 │  모터 로그 목록/다운로드│
     │           │                                                  └─────────┬────────────┘
     │           │ thing_web_bridge(WebSocket, JSON)                          ▲
     │           ▼                                                            │ POST 파일 업로드
     │   thing_control_web (Django Channels)                                  │ (robot_id, csv 등)
     │      ├─ ws/bridge/  ← ROS2 Web Bridge 노드                             │
     │      └─ ws/hand/    ← 브라우저(관제/조작 화면)                 ┌───────┴─────────┐
     │           │                                                   │ 로봇 손 측 업로드 │
     └── MJPEG(별도 포트) ──▶ 브라우저 영상 미리보기                   │ 스크립트(관제 담당 │
                                                                      │  범위 밖, 예: RPi)│
                                                                      └──────────────────┘
```

- **`thing_control_web`**: 로봇 손을 "지금 이 순간" 관제/조작하는 웹입니다. ROS 2 명령/상태를
  WebSocket JSON으로 중계만 하며, EC2와는 아무 관련이 없습니다(요구사항 6.3 "웹은 SQLite를
  직접 조회/수정하지 않는다"와 같은 맥락으로, 이 프로젝트도 로봇 제어 판단의 최종 권위를
  갖지 않습니다).
- **로봇 손 → EC2 업로드**: 로봇 손/라즈베리파이 쪽에서 모터 로그(csv 등)를 EC2의
  `thing_database_web` REST API(`/api/motor-data/upload/`)로 올리는 흐름입니다. 이 부분의
  실제 구현(ROS 2 노드인지, 라즈베리파이의 별도 크론/스크립트인지)은 **웹 파트 담당 범위가
  아니며, `thing_control_web`은 이 업로드 경로에 전혀 관여하지 않습니다.** 웹 파트에서는
  업로드가 "이런 HTTP 계약으로 들어온다"는 수신측 API만 제공합니다.
- **`thing_database_web`**: 위 업로드 API로 쌓인 모터 로그 메타데이터(DB)와 실 파일(로컬
  `media/`, 배포 시 S3로 대체 가능)을 회원가입한 이용자가 목록 조회 후 다운로드할 수 있게
  해주는, 명세서에는 없던 **신규 배포 전용 웹**입니다.

> 요구사항 명세서 V5.2 3.7절/5.6절은 "웹은 프로젝트 전용 내부 네트워크에서 사용하는 것을
> 기본으로 한다"(FR-28)고 명시하는데, 이는 **`thing_control_web`**에만 해당합니다.
> `thing_database_web`은 명세서 작성 시점에는 없던, 이번에 추가된 요구사항이므로
> 이 문서의 별도 절(§4)에서 설계 근거와 함께 검증합니다.

---

## 2. `thing_control_web` — 로봇 손 관제/조작 웹

요구사항 명세서 3.7절(FR-19~FR-28), 3.8절(FR-29~FR-43 중 웹 인터페이스 관련), 5.5절(NFR-13~NFR-23)에 대응합니다.

### 2.1 구조

```
thing_control_web/
├─ backend/                      # Django + Channels (ASGI)
│  ├─ config/                    # settings/urls/asgi
│  └─ thing_bridge/
│     ├─ consumers.py            # HandConsumer(브라우저) / BridgeConsumer(ROS2 브릿지)
│     ├─ routing.py              # ws/hand/, ws/bridge/
│     └─ models.py               # (영속 모델 없음 — 상태 중계 전용)
├─ frontend/                     # React + Vite
│  └─ src/
│     ├─ config/messageProtocol.js  # WS 메시지 스키마 단일 진실 공급원
│     ├─ context/HandSocketContext.jsx
│     ├─ pages/VisionMode.jsx    # 모방(MIMIC) 모드 페이지
│     ├─ pages/OrderMode.jsx     # 조작(MANUAL) 모드 페이지  → "페이지가 두 개"(1.2 MVP) 충족
│     └─ components/             # CameraStream, MotorStatusPanel, SafetyBanner, StatusBar
```

### 2.2 설계 요점

- **웹은 안전 판단의 최종 주체가 아니다**: `consumers.py`/`HandSocketContext.jsx` 주석에
  명시적으로 반복되는 원칙입니다. 실제 범위 제한·안전 상태 전이는 항상 Raspberry Pi의
  `command_guard`/`safety_manager`(ROS 2)가 수행하고, 웹은 조기 형식 검증과 UX 피드백만
  담당합니다(NFR-16 대응).
- `command_manager`만 공식 `/thing/command`를 발행한다는 FR-31 원칙에 맞춰, 웹도
  `HandConsumer`가 최종 권위를 갖지 않고 `BridgeConsumer`(ROS2 Web Bridge 노드)를 통해서만
  실제 명령이 나갑니다.
- `messageProtocol.js`가 "단일 진실 공급원"으로 선언되어 있고, `consumers.py` 상단 주석도
  이 파일을 함께 갱신하라고 안내합니다. 실제로 필드 목록(HAND_AXIS_KEYS, CONTROL_MODE,
  SAFETY_STATES, RECORDING_STATE 등)이 서로 일치합니다.
- 정지(`stop`) 명령은 다른 검증 없이 항상 브릿지로 전달됩니다(FR-23 "동작 정지 명령은
  다른 일반 명령보다 우선하여 처리").
- 녹화는 MIMIC 모드에서만, 판정 대기 중에는 재시작 불가 등 FR-26/FR-40의 인수조건이
  `_handle_record_control`에 그대로 반영되어 있습니다.

### 2.3 실행 방법 (개발)

```bash
# 1) 백엔드
cd thing_control_web/backend
pip install -r requirements.txt   # Django 5, channels, daphne, channels_redis 등
python manage.py migrate
python manage.py runserver 0.0.0.0:8000   # 또는 daphne config.asgi:application

# 2) 프런트엔드 (별도 터미널)
cd thing_control_web/frontend
npm install
npm run dev        # vite.config.js가 /ws → ws://localhost:8000 로 프록시
```

- 실제 Jetson MJPEG 스트림 주소는 `frontend/.env`의 `VITE_MJPEG_STREAM_URL`로 설정합니다.

---

## 3. `thing_database_web` — EC2 모터 데이터 다운로드 웹 (신규)

명세서 V5.2에는 없는, 이번에 추가된 요구사항(로봇 → EC2 업로드, 회원 다운로드)을 구현한
프로젝트입니다. `thing_control_web`과 프로세스/DB/배포 서버가 완전히 분리되어 있습니다.

### 3.1 구조

```
thing_database_web/
├─ backend/
│  ├─ config/                # settings(.env 기반), urls
│  └─ apps/
│     ├─ models.py           # MotorLogFile(robot_id, file_name, s3_key), UserProfile(1:1 User)
│     ├─ services.py         # S3Service — 지금은 로컬 media/ 폴더를 S3 대역으로 사용
│     ├─ views.py            # 회원가입/로그인/업로드/다운로드/목록 API
│     └─ migrations/
├─ frontend/                 # Vue 3 + Vite
│  └─ src/
│     ├─ views/HomeView.vue / LoginView.vue / RegisterView.vue / DataDownloadView.vue
│     ├─ router/index.js     # /download 는 requiresAuth 가드
│     └─ services/api.js, auth.js
```

### 3.2 API

| Method | URL | 설명 |
|---|---|---|
| POST | `/api/register/` | 이름/아이디/비번/이메일/사용목적 회원가입 |
| POST | `/api/login/` | 세션 로그인 |
| POST | `/api/motor-data/upload/` | **로봇(라즈베리파이 등) → 서버** 모터 로그 파일 업로드 |
| GET | `/api/motor-data/files/` | 업로드된 파일 메타데이터 목록 |
| GET | `/api/motor-data/download/<id>/` | 다운로드 URL 발급 |

### 3.3 S3/EC2 관련 설계

- `apps/services.py`의 `S3Service`는 **지금은 실제 AWS S3가 아니라 로컬 `media/` 폴더**를
  사용하는 스텁 구현입니다(주석에 명시: "AWS 대신 로컬 프로젝트 내부의 'media' 폴더를
  저장소로 활용"). `settings.py` 12번 섹션에 `AWS_ACCESS_KEY_ID` 등 실배포용 S3 설정
  뼈대(`.env`로 주입)가 이미 준비되어 있어, 나중에 `S3Service`의 두 메서드
  (`upload_file`, `generate_download_url`)만 `boto3`(requirements.txt에 이미 포함) 호출로
  교체하면 실제 S3 연동으로 전환 가능한 구조입니다.
- `requirements.txt`에 `gunicorn`이 포함되어 있어, EC2에서는 `runserver`가 아니라
  `gunicorn config.wsgi:application`으로 구동하는 것을 전제로 합니다.

### 3.4 실행 방법 (개발)

```bash
# 1) 백엔드
cd thing_database_web/backend
python -m venv .venv && source .venv/bin/activate   # 또는 conda env(ec2_web)
pip install -r requirements.txt
# config/.env 파일에 SECRET_KEY, DEBUG, ALLOWED_HOSTS 등을 채워야 함(코드에서 필수로 읽음)
python manage.py migrate
python manage.py runserver 0.0.0.0:8000

# 2) 프런트엔드 (별도 터미널)
cd thing_database_web/frontend
npm install
npm run dev        # http://localhost:5173, VITE_API_BASE_URL 기본값은 http://localhost:8000/api
```

- `backend/config/.env` 파일이 저장소에 포함되어 있지 않습니다(`settings.py`가
  `environ.Env.read_env(BASE_DIR/config/.env)`로 필수 로드). 로컬/EC2 각각에 직접
  생성해야 합니다.

---

## 4. 프로젝트 진행 상태 검증

### 4.1 요구사항과 일치하는 부분

- `thing_control_web`은 명세서 3.7절(FR-19~FR-28)의 핵심 규칙(모드 동시 활성화 금지, 녹화
  중 모드 변경 금지, 정지 명령 최우선 처리, 웹이 최종 안전 주체가 아님, SQLite 직접
  미접근 등)을 백엔드/프런트 양쪽에 일관되게 반영하고 있습니다.
- `HandCommand`/`ControlState`/`SafetyState`/`RecordingState` 메시지 필드가 FR-30
  `.msg` 정의와 `messageProtocol.js`/`consumers.py` 사이에서 이름이 일치합니다.
- 7논리축이 배열이 아닌 고정 필드(`thumb_flex` 등)로 일관되게 다뤄집니다.
- `thing_control_web`은 코드 어디에도 EC2/S3/AWS 관련 설정이 없어, "이 쪽에서는 EC2 서버와
  연결이 없다"는 안내와 정확히 일치합니다.
- `thing_database_web`은 로봇 → 업로드, 회원 → 다운로드라는 신규 요구사항 두 가지를 모두
  구현하고 있고, S3 전환을 염두에 둔 서비스 계층 분리, EC2 배포를 전제로 한 스크립트
  (`gunicorn`, `ec2_web` conda env) 등 배포 환경에 대한 고려가 보입니다.

### 4.2 결론

- **`thing_control_web`**은 명세서 3.7/3.8/5.5절 요구사항을 웹 담당 범위 내에서 충실히
  구현했고, EC2/DB와 연결이 없다는 전제와도 코드상으로 일치합니다. WS 상태 필드의
  snake_case(백엔드) ↔ camelCase(프런트) 네이밍 불일치는 프런트 필드를 snake_case로
  통일해 정리했습니다.
- **`thing_database_web`**은 이번에 추가된 두 요구사항(로봇→EC2 업로드, 회원→다운로드)을
  기능적으로는 모두 구현했으나, 인증/권한 처리가 아직 배포 수준에 이르지 못했습니다
  (업로드 API 인증 부재, `localStorage` 플래그 기반 로그인 상태 판단 등). MVP/데모
  단계에서는 무방하지만, 실제 배포 전 보안 검토가 필요합니다. `LoginView.vue`의 임시
  관리자 우회 로그인 코드와 라우터에 연결되지 않던 목업 컴포넌트(`DataDownload.vue`)는
  정리했습니다.

---

## 5. 실제 배포 환경에서 반드시 수정해야 하는 파일

로컬 개발용 기본값이 그대로 들어 있어, 실제 내부망(`thing_control_web`)·EC2
(`thing_database_web`) 배포 전에 아래 파일들을 확인/수정해야 합니다.

### 5.1 `thing_control_web`

| 파일 | 수정 내용 |
|---|---|
| `frontend/.env` | `VITE_MJPEG_STREAM_URL`을 로컬(`127.0.0.1:5000`)이 아닌 실제 Jetson MJPEG 주소로 교체. 필요 시 `VITE_MJPEG_RAW_STREAM_URL`(원본 영상)도 설정. |
| `backend/config/settings.py` | `SECRET_KEY`는 `DJANGO_SECRET_KEY` 환경변수로 반드시 교체(기본값은 저장소에 공개된 개발용 키). `DEBUG`는 `DJANGO_DEBUG=false`로 설정. `ALLOWED_HOSTS`는 `DJANGO_ALLOWED_HOSTS`에 실제 내부망 Jetson/RaspberryPi/노트북 IP 또는 호스트명을 명시(현재는 미설정 시 개발용 `localhost`만 허용). |
| `backend/config/settings.py` (CHANNEL_LAYERS) | 관람객이 여러 명 동시 접속해 daphne를 여러 워커로 띄워야 한다면 `REDIS_URL` 환경변수를 설정해 `channels_redis`로 전환. |

### 5.2 `thing_database_web`

| 파일 | 수정 내용 |
|---|---|
| `backend/config/.env` (신규 생성 필요, 저장소에 없음) | `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS`(EC2 도메인/IP), `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_REGION_NAME`을 실제 값으로 채움. |
| `backend/apps/services.py` (`S3Service`) | 지금은 로컬 `media/` 폴더를 쓰는 스텁 구현. `upload_file`/`generate_download_url`을 `boto3` 기반 실제 S3 업로드·Presigned URL 발급 로직으로 교체. |
| `backend/apps/views.py` | `MotorDataUploadView`(로봇 업로드), `MotorDataDownloadView`, `MotorDataListView`에 인증/권한(`permission_classes`, 로봇 업로드용 API 키 또는 HMAC 서명 등)을 추가. 현재는 누구나 호출 가능. |
| `backend/config/settings.py` (`CORS_ALLOWED_ORIGINS`) | `http://localhost:5173` 등 로컬 주소를 실제 배포된 프런트엔드 도메인으로 교체. |
| `frontend/src/services/auth.js` | `localStorage.isLoggedIn` 플래그만으로 로그인 여부를 판단하는 현재 로직을, 서버 세션 상태를 실제로 확인하는 방식(예: 로그인 확인 API 호출)으로 교체. |
| `frontend/.env.production` | `VITE_API_BASE_URL=/api` 값이 실제 EC2의 리버스 프록시(nginx 등) 설정과 일치하는지 확인. |
