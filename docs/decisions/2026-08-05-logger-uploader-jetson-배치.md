# 결정: logger·exporter·uploader의 Jetson 배치와 uploader 코드 위치

- **상태**: 확정 — 2026-08-05 신수진 확인, 문서 반영 진행
- **작성일**: 2026-08-05
- **작성자**: 신수진
- **관련 티켓**: [S15P11C103-80](https://ssafy.atlassian.net/browse/S15P11C103-80) (logger), [S15P11C103-124](https://ssafy.atlassian.net/browse/S15P11C103-124) (exporter), [S15P11C103-125](https://ssafy.atlassian.net/browse/S15P11C103-125) (격리 uploader), [S15P11C103-130](https://ssafy.atlassian.net/browse/S15P11C103-130) (컨테이너 구성)
- **관련 문서**: [2026-08-05 landmark 4파일·content_digest 합의](2026-08-05-landmark-4파일-및-content-digest.md)

---

## 결정

| # | 결정 |
|---|------|
| **P1** | **logger 노드, exporter, 격리 uploader 데몬은 Jetson Orin Nano에서 실행한다.** rosbag2 원본·임시 4파일 저장(`/var/lib/thing-robot-data/`)과 uploader 소켓(`/run/thing-uploader/uploader.sock`)도 Jetson에 둔다. |
| **P2** | **uploader 코드의 최종 위치는 `thing_ws/src/thing_logger/thing_logger/uploader.py`** (계약·설정은 `uploader_contract.py`, console_script `uploader`). 별도 최상위 패키지 `services/thing-data-uploader/`는 폐기하고 디렉터리를 삭제한다. |

## 근거

- `/thing/landmarks`는 Best Effort(sensor data) QoS라 Wi-Fi를 건너면 유실이 복구 불가능하다. LandMark JSON이 필수 4번째 공개 파일로 확정(D1)된 이상, landmark를 발행지(Jetson)에서 로컬 수신·기록하는 것이 데이터 완전성에 유리하다.
- `StartRecording`/`StopRecording`/`SetMimicResult`의 호출자인 `web_bridge_node`가 Jetson에 있어 서비스 호출이 로컬로 처리된다.
- 격리 원칙은 배치와 무관하게 유지된다: uploader는 별도 프로세스로 rclpy·rosbag2·exporter를 import하지 않고, 완료 4파일을 읽기 전용으로만 접근하며, private Unix socket 계약(`uploader_handoff.py` ↔ `uploader_contract.py`)을 그대로 쓴다.

## 수용한 트레이드오프

- `/thing/command`, `/thing/motor_status`, `/thing/control_state`, `/thing/safety_state`(모두 Reliable, Raspberry Pi 발행)가 Wi-Fi DDS를 건너 기록된다 → **MotorStatus CSV 등 기록 완전성이 무선 품질에 의존**하게 된다. 제어·안전 판단은 Raspberry Pi 로컬에서 완결되므로 영향 없다(NFR-16/28).
- 종료 후 exporter(bag 읽기·SHA-256·4파일 생성)가 vision 스택과 같은 보드에서 CPU를 쓴다. 판정 전에는 새 기록을 시작할 수 없어 기록 중 경쟁은 없다.

## 후속 작업

- **요구사항 명세서 V7.1 수정(신수진)** — 아직 미반영. 대상 조항: 4.1 장치 배치, UC-06 2번, FR-29(장치별 패키지 목록), FR-42(Jetson launch 노드 수), FR-51(token 파일 위치 Pi→Jetson), FR-53(컨테이너 구성: Jetson `docker run`에 logger·uploader 추가, 소켓·데이터 디렉터리 bind mount), 1.5.2(로봇 로컬 저장), 8.3 검수 조건.
- **컨테이너 구성 반영(윤정민, S15P11C103-130)** — Jetson `docker run` 스크립트에 logger·uploader 프로세스, `/run/thing-uploader/`·`/var/lib/thing-robot-data/` 공유 mount, `THING_UPLOADER_*`/`THING_EC2_UPLOAD_URL` env 확정.
- **Jira 125 문구 정정(신수진)** — 제목·본문의 "Raspberry Pi 격리 uploader" → Jetson.
- **이 커밋에서 반영한 문서**: 모노레포 `README.md`, `docs/architecture.md`, `docs/setup/jetson.md`, `docs/interfaces.md`(logger 구독 목록 정정 포함), `services/thing-data-uploader/` 삭제.
- **웹 담당 확인 후 반영**: `web/README.md`의 장치 다이어그램(26-28행)이 Pi에 Logger·thing-data-uploader를 그리고 있음.
