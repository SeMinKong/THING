# I15C103 — `thing_database_web` EC2 배포 가이드

- 서버: `I15C103`
- 접속: `ssh -i I15C103T.pem ubuntu@i15c103.p.ssafy.io`
- 로컬 작업 환경: **아무것도 안 깔려있는 Windows PC** (Ubuntu는 별도 데스크탑에 설치되어 있고, 이 가이드는 그 Windows PC 기준입니다)
- 대상 프로젝트: `nerf_updated/thing_database_web` (Vue3+Vite 프런트, Django+DRF 백엔드)
- 원칙: **ufw는 항상 enable 상태 유지** — 뚫을 포트만 `allow`로 추가

이 문서와 같이 첨부된 수정본 zip에는 아래 내용이 이미 반영되어 있습니다.

| 파일 | 수정 내용 |
|---|---|
| `backend/requirements.txt` | **UTF-16(Windows 저장 시 인코딩 깨짐)로 저장되어 있던 걸 UTF-8로 재저장.** 원본 그대로 EC2(Ubuntu)에 올리면 `pip install -r requirements.txt`가 파일을 못 읽고 에러납니다. |
| `backend/config/.env` | 이 서버용 `SECRET_KEY`(새로 발급), `DEBUG=False`, `ALLOWED_HOSTS=i15c103.p.ssafy.io`, `CORS_EXTRA_ORIGINS`, `CSRF_TRUSTED_ORIGINS` 채워서 새로 생성 |
| `backend/config/settings.py` | `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`를 `.env`로 배포 도메인을 추가할 수 있게 수정 |
| `deploy/thing-database-web.service` | gunicorn을 systemd로 상시 구동시키기 위한 유닛 파일 (신규) |
| `deploy/nginx_thing_database_web.conf` | nginx가 80번 포트를 받아 `/api`, `/admin`, `/media`는 gunicorn으로 넘기고 나머지는 Vue 빌드 결과물을 서빙하는 설정 (신규) |

---

## 0. 전체 그림

```
브라우저 --80/tcp--> nginx --(내부 8000)--> gunicorn(Django) --sqlite--
                       └─ / 는 Vue 빌드 결과물(dist) 정적 서빙
```

- 외부에 노출되는 포트는 **22(ssh) / 80(http)** 두 개뿐입니다. 8000(장고)은 nginx 뒤에 숨겨서 굳이 ufw로 열 필요가 없습니다.
- `frontend/.env.production`에 이미 `VITE_API_BASE_URL=/api`로 되어 있어서, 빌드된 프런트가 같은 도메인의 `/api`를 그대로 호출합니다 → nginx 리버스 프록시 구조와 맞습니다(수정 불필요).

---

## 1. Windows PC 준비 (로컬)

완전 빈 Windows 환경에서 EC2에 접속하고 파일을 옮기는 데 필요한 최소 도구만 설치합니다.

1. **SSH 클라이언트**: Windows 10/11은 OpenSSH 클라이언트가 기본 내장되어 있습니다. PowerShell을 열고 아래로 확인:
   ```powershell
   ssh -V
   ```
   버전이 안 뜨면 `설정 > 앱 > 선택적 기능 > OpenSSH 클라이언트 추가`로 설치하세요.

2. **pem 키 파일 준비**: `I15C103T.pem`을 예를 들어 `C:\Users\<사용자>\.ssh\` 폴더에 저장합니다.
   Windows는 pem 파일 권한 검사가 리눅스와 달라서, 다음처럼 **현재 사용자만 읽기 가능**하도록 걸어줘야 `ssh`가 거부하지 않습니다 (PowerShell):
   ```powershell
   icacls "C:\Users\<사용자>\.ssh\I15C103T.pem" /inheritance:r
   icacls "C:\Users\<사용자>\.ssh\I15C103T.pem" /grant:r "$($env:USERNAME):(R)"
   ```

3. **접속 테스트**:
   ```powershell
   ssh -i "C:\Users\<사용자>\.ssh\I15C103T.pem" ubuntu@i15c103.p.ssafy.io
   ```
   ufw 관련 안내에 나온 대로, **여기서 접속이 끊길 걸 대비해 PowerShell 창을 2~3개 더 띄워 각각 접속해두고** 작업을 시작하세요.

4. **Git for Windows** (설치): https://git-scm.com/download/win — 이후 `Git Bash`에서 `scp`, `rsync` 등도 쓸 수 있어 편합니다.

5. **파일 전송 방법 두 가지 중 택1**
   - **A) Git 저장소로 관리 중이면(추천)**: 로컬에서 Windows에는 아무것도 안 깔아도 되고, EC2에서 바로 `git clone`.
   - **B) zip을 직접 올려야 하면**: Windows PowerShell에서
     ```powershell
     scp -i "C:\Users\<사용자>\.ssh\I15C103T.pem" C:\path\to\nerf_updated_deployed.zip ubuntu@i15c103.p.ssafy.io:/home/ubuntu/
     ```

이 대화에서 만든 수정본은 아래에서 zip으로 내려받아 위 B) 방식으로 올리면 됩니다.

---

## 2. EC2(Ubuntu) 쪽 — 최초 1회 시스템 준비

SSH로 접속한 상태에서 진행합니다.

```bash
sudo apt update && sudo apt upgrade -y

# Python 백엔드용
sudo apt install -y python3-venv python3-pip

# Node/npm (프런트 빌드용) - nodesource 20.x LTS
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# nginx (리버스 프록시 + 정적 파일 서빙)
sudo apt install -y nginx

node -v && npm -v && python3 --version && nginx -v
```

---

## 3. ufw 포트 설정 (첨부하신 가이드 기준)

기본은 22번만 열려있는 상태입니다. 이번에 새로 필요한 건 **80/tcp** 하나뿐입니다.

```bash
sudo ufw status                # 현재 상태 확인 (inactive 든 active 든 상관없이 진행)
sudo ufw allow 80/tcp           # 웹(nginx) 포트 허용
sudo ufw status numbered        # 22, 80 두 개가 ALLOW로 잡혀있는지 확인
```

- 이미 `enable` 되어 있다면 `allow` 즉시 반영됩니다(재부팅/재활성 불필요).
- **8000번(Django/gunicorn)은 절대 ufw로 열지 마세요.** nginx가 내부적으로만 127.0.0.1:8000에 접속하고, 외부에서 8000으로 직접 오는 요청은 막아두는 게 정상입니다.
- 작업 도중 실수로 ssh가 끊기지 않게, ufw를 건드리기 전 터미널 2~3개를 유지하라는 원본 가이드 원칙은 이번에도 그대로 지키세요.
- **ufw는 계속 active 상태를 유지**해야 합니다(`sudo ufw disable` 하지 마세요).

---

## 4. 프로젝트 업로드 & 백엔드 배포

```bash
cd /home/ubuntu
unzip nerf_updated_deployed.zip     # Windows에서 올린 수정본 압축 해제
cd nerf_updated/thing_database_web/backend

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt      # UTF-8로 고쳐놓은 파일이라 정상 설치됩니다
                                      # (원본 UTF-16 그대로였다면 여기서 파싱 에러 남)

# config/.env 는 이미 이 서버용 값으로 채워서 zip에 포함되어 있음 — 그대로 사용
python manage.py migrate
python manage.py createsuperuser     # (선택) admin 계정이 필요하면

deactivate
```

동작 확인만 먼저 해보고 싶다면(선택):
```bash
source .venv/bin/activate
python manage.py runserver 0.0.0.0:8000
# 다른 창에서: curl http://127.0.0.1:8000/api/motor-data/files/
```
확인 후 `Ctrl+C`로 끄고, 아래 systemd 방식으로 전환합니다(터미널을 계속 켜둘 필요 없게).

### gunicorn을 systemd 서비스로 상시 구동

```bash
sudo cp /home/ubuntu/nerf_updated/thing_database_web/deploy/thing-database-web.service \
        /etc/systemd/system/thing-database-web.service

sudo systemctl daemon-reload
sudo systemctl enable --now thing-database-web
sudo systemctl status thing-database-web   # active (running) 확인
```

> `deploy/thing-database-web.service` 안의 `WorkingDirectory` / `ExecStart` 경로가
> `/home/ubuntu/nerf_updated/...` 로 되어 있습니다. 실제로 다른 경로에 풀었다면 그 두 줄만 맞게 고치세요.

---

## 5. 프런트엔드 빌드 (Vue)

```bash
cd /home/ubuntu/nerf_updated/thing_database_web/frontend
npm install
npm run build          # frontend/dist 폴더 생성 (.env.production의 VITE_API_BASE_URL=/api 반영됨)
```

`npm run dev`(vite 개발서버, 5173포트)는 배포에는 쓰지 않습니다 — nginx가 `dist`를 정적으로 서빙합니다.

---

## 6. nginx 설정

```bash
sudo cp /home/ubuntu/nerf_updated/thing_database_web/deploy/nginx_thing_database_web.conf \
        /etc/nginx/sites-available/thing_database_web

sudo ln -s /etc/nginx/sites-available/thing_database_web /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default   # 기본 페이지와 충돌 방지 (선택이지만 권장)

sudo nginx -t          # 문법 체크: syntax is ok / test is successful 나와야 함
sudo systemctl reload nginx
```

---

## 7. 최종 확인

- 브라우저에서 `http://i15c103.p.ssafy.io` → Vue 홈 화면이 떠야 합니다.
- 회원가입/로그인 → `/api/register/`, `/api/login/` 호출이 200으로 응답하는지 개발자도구 Network 탭에서 확인.
- `sudo ufw status` → 22/tcp, 80/tcp만 ALLOW, 상태는 계속 **active** 유지.
- 서버 재부팅 후에도 자동 기동되는지 확인하려면:
  ```bash
  sudo systemctl is-enabled thing-database-web nginx ufw
  ```

---

## 8. 알아두면 좋은 것 (README에도 이미 정리되어 있던 항목)

- `apps/services.py`의 `S3Service`는 지금은 로컬 `media/` 폴더를 쓰는 스텁입니다. 실제 S3로 옮길 땐 `.env`의 `AWS_*` 값을 채우고 `upload_file`/`generate_download_url`만 `boto3` 코드로 교체하면 됩니다.
- 업로드/다운로드 API(`MotorDataUploadView` 등)는 현재 인증이 없어 누구나 호출 가능합니다. 데모/MVP 단계면 괜찮지만, 실제 운영 전에는 로봇 업로드용 API 키(또는 HMAC 서명) 및 다운로드 권한 체크를 추가하는 걸 권장합니다(이번 수정 범위에는 포함하지 않았습니다 — 필요하시면 별도로 도와드릴 수 있어요).
- 로그인 상태를 `localStorage.isLoggedIn` 플래그로만 판단하는 `frontend/src/services/auth.js`도 실제 세션 확인 API 호출 방식으로 바꾸는 게 좋다는 점, README에 이미 메모되어 있습니다.
