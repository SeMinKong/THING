# uploader 실기기 통합시험 절차 (S15P11C103-138 준비)

- **작성일**: 2026-08-05
- **작성자**: 신수진
- **대상**: rosbag2 → export → handoff → EC2 READY → 조회·다운로드 종단 검증
- **전제**: uploader 데몬 구현 완료(`4dea44e`, E2E 20케이스 통과),
  EC2 digest 정합 확인(96 브랜치, 교차검증 통과 —
  [수정요청 문서 §7](decisions/2026-08-05-EC2-landmark-digest-수정요청.md))
- **남은 미지수**: ① 실기기 컨테이너 기동(소켓 권한·env 주입·실행 명령 정합, 윤정민)
  ② 대용량 landmark 실측(P-3). 이 둘은 책상 검증이 불가능하므로 실기기에서 잡는다.

---

## 1. 시험 전 준비물 (없으면 시작 불가)

| # | 항목 | 담당 | 비고 |
|---|------|------|------|
| 1 | EC2에 96 브랜치 배포(또는 develop 머지 후 배포) | 김기현 | 미배포 시 전량 422 — 시험 무의미 |
| 2 | Jetson에 125 브랜치 `colcon build` | 신수진 | `ros2 run thing_logger uploader` 생김 |
| 3 | 토큰 짝: 원문 → Jetson env / SHA-256 hash → EC2 `.env` `DEVICE_TOKENS=THING-001:<hash>` | 신수진·김기현 | 원문 토큰은 Git·이미지·Compose·로그 금지 |
| 4 | uploader 컨테이너 구성: 소켓 dir(rw)·임시 dir(**ro**)·env 3개(`THING_UPLOADER_SOCKET`/`THING_EC2_UPLOAD_URL`/`THING_UPLOADER_TOKEN`) | 윤정민 | env 이름은 잠정 — 코드(`uploader_contract.py`)와 반드시 동일하게 |
| 5 | 실물 compose/Dockerfile을 Git에 커밋(FR-53 "버전 관리된 Compose") | 윤정민 | 130 |

## 2. 시험 순서 — 단계별로 끊어서, 한 번에 다 하지 않는다

### 0단계. uploader 단독 기동
```
ros2 run thing_logger uploader   # 또는 컨테이너 ENTRYPOINT
```
- 기대: `uploader 기동: {...device_token: ***redacted***...}` →
  `listening on /run/thing-uploader/uploader.sock (mode=0o660)`
- 토큰/URL env 누락 시 즉시 종료(exit 2)가 정상.

### 1단계. 실세션 종단 (해피패스)
1. 내부망 웹에서 MIMIC 녹화 시작 → 종료 → SUCCESS 판정
2. 기대 로그: logger `export ok` → uploader `session=<id> state=UPLOADED accepted=True`
3. EC2 포털에서 해당 세션 READY 확인, **4파일 다운로드** 성공

### 2단계. 멱등·충돌
- 같은 rosbag2를 다시 판정→export하면 EC2 200(idempotent)으로 성공해야 함
- (선택) 내용 다른 동일 session_id 업로드 → 409 확인

### 3단계. 장애 주입 (NFR-28 — 138의 핵심)
1. EC2(또는 네트워크) 내린 상태에서 세션 정상 종료·판정
2. 기대: uploader `state=FAILED` + 사유 로그, **로봇 제어·안전·다음 녹화는 정상 동작**
3. 프로세스 재시작 후 실패 업로드가 **자동 재개되지 않음** 확인(진단 로그만 남음)
4. uploader 컨테이너를 아예 내리고 세션 종료 → logger에
   `uploader socket request failed` 남고 제어 무영향 확인

### 4단계. 대용량 landmark 실측 (P-3 확정)
```
# EC2 쪽 시험 도구로 큰 landmark 생성
python manage.py make_session --out /tmp/big --landmark-frames 20000 --post
```
- 프레임 수를 늘려가며 실제 60초 세션 상당 용량 측정 → **P-3 상한 확정**
- 413이 나면 `limits.py`·Django·Nginx 상한을 실측값 기준으로 함께 조정(김기현)

## 3. 예상 오류 → 원인 지도

| 증상(로그) | 원인 | 1차 조치 |
|---|---|---|
| logger: `uploader socket request failed: Connection refused` | uploader 미기동 / 소켓 경로 불일치 / 마운트 누락 | 0단계 재확인, 컨테이너 마운트 |
| logger: `…: Permission denied` | 소켓 mode·uid·group 불일치 (예상 1순위) | `THING_UPLOADER_SOCKET_MODE`·컨테이너 유저 정합(윤정민) |
| uploader: `EC2 거부 (HTTP 401)` | 토큰↔hash 불일치, robot_id 불일치 | 준비물 3 재확인 |
| uploader: `EC2 거부 (HTTP 413)` | landmark 상한 초과 | 4단계 — P-3 실측으로 상한 조정 |
| uploader: `EC2 거부 (HTTP 422)` | EC2에 96 미배포(digest) 또는 schema | 준비물 1 확인 |
| uploader: `전송 실패: SSLError` | 인증서 | EC2 TLS 확인 |
| uploader: `전송 실패: ConnectTimeout` | 보안그룹·도메인·443 | 네트워크 경로 확인 |
| uploader: `content conflict (409)` | 같은 session_id 다른 내용 | 정상 동작(멱등 보호) — 세션 ID 확인 |

오류는 세 곳에 남는다: **uploader stdout(진단 로그) · logger의 `export failed:` 로그 ·
EC2 Django 로그**. 셋을 같이 보면 어느 구간에서 끊겼는지 바로 나온다.

## 4. 통과 기준 (138 인수 조건과 연결)

- [ ] 판정 수락 후 5초 안에 upload 시도 시작(§8.1)
- [ ] 해피패스: 201 → READY → 4파일 다운로드
- [ ] 멱등: 재업로드 200, 중복 세션 0
- [ ] 장애: handoff/upload 실패에도 제어·안전·rosbag2 독립 동작, 자동 재개 0건
- [ ] 재시작: 컨테이너 재시작 후 이전 recording·판정·upload 재개 0건
- [ ] P-3: landmark 실측 용량 기록·상한 확정
