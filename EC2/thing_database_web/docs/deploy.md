# EC2 배포 절차

`PROJECT_ROOT = /home/ubuntu/ec2_server/thing_database_web`
서버 체크아웃 경로가 다르면 아래 경로와 `deploy/` 두 파일의 경로를 함께 고칩니다.

---

## 이번 변경에 필요한 것만

| 단계 | 필요한가 | 왜 |
|---|---|---|
| `git pull` | ● | |
| `pip install` | ✕ | `requirements.txt` 변경 없음 |
| `manage.py migrate` | ✕ | 모델 변경 없음 (`makemigrations --check` → No changes) |
| `manage.py collectstatic` | ✕ | 정적 파일 추가 없음 |
| `npm ci` | ✕ | `package-lock.json` 변경 없음 |
| **`npm run build`** | ● | `SessionDetailView.jsx` 가 바뀜. `dist/` 는 git 에 없음 |
| **nginx conf 복사 + reload** | ● | `client_max_body_size` 가 211M 로 바뀜 |
| **gunicorn 재시작** | ● | Python 코드·`settings.py` 가 바뀜 |

`node_modules` 가 서버에 없거나 상태가 의심되면 `npm ci` 를 넣습니다. `package-lock.json` 이 그대로라 결과는 같고 시간만 더 걸립니다.

---

## 순서

```bash
cd /home/ubuntu/ec2_server
git pull
```

### 1. 프런트엔드 빌드

`dist/` 는 `.gitignore` 에 있어 **git pull 로 오지 않습니다.** 서버에서 빌드해야 화면이 바뀝니다.

```bash
cd thing_database_web/frontend
npm run build          # node_modules 가 없거나 의심되면 먼저 npm ci
```

빌드가 끝나면 `dist/index.html` 의 시각을 확인합니다. 갱신되지 않았다면 빌드가 실패한 것입니다.

```bash
ls -l dist/index.html
```

### 2. nginx 설정 반영

**이 단계를 빼먹으면 조용히 실패합니다.** `git pull` 은 저장소의 conf 파일만 바꾸고 `/etc/nginx` 는 건드리지 않습니다. 반영하지 않으면 landmark 를 포함한 큰 업로드가 **Nginx 에서 413 으로 끊깁니다.** Django 로그에는 아무것도 남지 않아 원인을 찾기 어렵습니다.

```bash
cd /home/ubuntu/ec2_server/thing_database_web/deploy
sudo cp nginx_thing_database_web.conf /etc/nginx/sites-available/thing_database_web
sudo nginx -t && sudo systemctl reload nginx
```

반영됐는지 확인합니다.

```bash
grep client_max_body_size /etc/nginx/sites-available/thing_database_web
# client_max_body_size 211M;
```

> 저장소의 `apps/tests.py` 가 conf 파일을 읽어 상한을 대조하지만, **읽는 대상은
> 저장소 파일이고 `/etc/nginx` 가 아닙니다.** 테스트가 통과해도 배포된 conf 는
> 옛 값일 수 있으므로 위 `grep` 으로 직접 확인하십시오.

### 3. 백엔드 재시작

```bash
sudo systemctl restart thing-database-web
sudo systemctl status thing-database-web --no-pager
```

`Active: active (running)` 이 아니면 로그를 봅니다.

```bash
sudo journalctl -u thing-database-web -n 50 --no-pager
```

---

## 확인

### 서비스가 살아 있는가

```bash
curl -sS https://<도메인>/health
```

DB·디스크 확인 결과가 돌아옵니다. 실패하면 `EC2_DATA_DIR` 권한이나 SQLite 파일을 봅니다.

### 화면이 바뀌었는가

세션 상세 화면을 열어 파일 표를 봅니다. 이번 변경으로 이렇게 달라집니다.

- 표 위 문구가 "세션마다 아래 세 파일만 공개됩니다" → **"이 세션에 공개된 파일입니다"**
- landmark 가 올라온 세션에는 **LandMark JSON 행**이 생깁니다
- landmark 가 없는 기존 세션에는 그 행이 없습니다

브라우저 캐시로 옛 화면이 보일 수 있습니다. 강제 새로고침(`Ctrl+Shift+R`)으로 확인합니다.

### 업로드 상한이 올라갔는가

큰 파일을 직접 던져 봅니다. 413 이 아니라 401 이나 400 이 오면 Nginx 를 통과한 것입니다.

```bash
head -c 150M /dev/zero > /tmp/big.bin
curl -sS -o /dev/null -w "%{http_code}\n" \
  -X POST https://<도메인>/api/v1/uploads/sessions \
  -F "metadata=@/tmp/big.bin"
rm /tmp/big.bin
```

`413` 이 오면 nginx conf 가 반영되지 않았습니다. 2단계를 다시 합니다.

### 서버에서 테스트를 돌려 볼 때

```bash
cd /home/ubuntu/ec2_server/thing_database_web/backend
.venv/bin/python manage.py test apps      # 140건
```

테스트는 별도 테스트 DB 를 만들고 지우므로 운영 데이터를 건드리지 않습니다. 다만 운영 중 부하가 걸리니 급하지 않으면 배포 전 로컬에서 돌리는 편이 낫습니다.

---

## 건드리지 않는 것

`git pull` 과 재시작이 아래를 지우거나 덮어쓰지 않습니다. `.gitignore` 와 systemd `StateDirectory` 가 배포 디렉터리 밖에 두고 있습니다.

| | 위치 |
|---|---|
| 업로드된 세션 파일 | `/var/lib/thing-data/sessions/` |
| SQLite DB | `backend/db.sqlite3` (gitignore) |
| 비밀값 | `backend/config/.env` (gitignore) |
| 장치 토큰 hash | systemd EnvironmentFile (0600) |
| Python venv | `backend/.venv/` (gitignore) |

---

## 되돌리기

프런트엔드와 백엔드를 함께 되돌립니다. 마이그레이션이 없어 DB 는 그대로입니다.

```bash
cd /home/ubuntu/ec2_server
git log --oneline -5
git checkout <이전-커밋>

cd thing_database_web/frontend && npm run build

cd ../deploy
sudo cp nginx_thing_database_web.conf /etc/nginx/sites-available/thing_database_web
sudo nginx -t && sudo systemctl reload nginx

sudo systemctl restart thing-database-web
```

nginx 상한을 낮추면 그동안 올라온 큰 landmark 파일은 그대로 남아 다운로드됩니다. 새 업로드만 413 이 됩니다.

---

## 다음 배포에 달라지는 것

이번에는 필요 없었지만 아래가 바뀌면 단계가 늘어납니다.

| 바뀌면 | 추가할 단계 |
|---|---|
| `requirements.txt` | `.venv/bin/pip install -r requirements.txt` |
| `models.py` 또는 `migrations/` | `.venv/bin/python manage.py migrate` |
| `package-lock.json` | `npm ci` (`npm install` 아님 — lock 을 그대로 재현) |
| `static/` 추가 | `manage.py collectstatic --noinput` |
| `deploy/*.service` | `sudo cp` → `daemon-reload` → `restart` |
| `apps/limits.py` 의 상한 | nginx conf 값도 함께. 테스트가 넣을 값을 알려 줍니다 |
