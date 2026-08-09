# 통합 작업 목록

내부망 관제·제어 웹을 실제 로봇에 붙일 때 해야 하는 일입니다. 위에서 아래로 진행합니다.

- `[ ]` 웹 담당이 하는 일
- `[!]` 다른 담당의 회신·구현이 있어야 진행되는 일
- `[·]` 통합 당일 확인만 하는 일

---

# 1. 통합 전 — 웹 담당

## 1.1 미구현 마무리

- [x] **기록 서비스 거부 사유 8종 추가** — 완료
8종(`not_mimic_mode` `start_failed` `already_recording` `result_pending` `not_recording` `session_mismatch` `stop_failed` `owner_lease_expired`)이 `messageProtocol.js` 의 `REJECT_REASON` 과 `describeReason()` 문구표에 반영됨. 남은 확인은 "브릿지가 원문 그대로 싣는가"(pending-decisions E-1) 뿐.
   > `session_mismatch` 는 웹이 이미 가진 `web_session_mismatch` 와 **다른 값**입니다. 둘을 합치지 마십시오.

- [x] **`MotorState.torque_enabled` 표시** — 완료
`MotorStatusPanel` 에 "토크" 열 추가됨. `communication_ok=false` 면 값을 유효로 안 보고 "-" 로 둠(FR-30 인수조건). FR-35 READY·RESET 전이 근거로 통합 중 유용.

- [ ] **NFR-22 이벤트 회전 로그** (Should)
  Must 가 아니므로 통합 후로 미뤄도 됩니다.

## 1.2 상대편에 전달

- [ ] **브릿지 담당에게 `docs/interfaces-bridge.md` 전달**
  1장(브릿지가 해야 하는 것)이 구현 대상입니다. `control_state` 와 `recording` 두 필드가 없으면 **화면이 통째로 잠깁니다.**

- [ ] **`docs/pending-decisions.md` 배포**
  담당별 9건입니다. A-1(snapshot 주기)과 B-1(`speed_limit`)이 우선입니다.

- [ ] **`speed_limit` 은 통합 시험 전 필수**
  FR-32 가 "실제 축 속도는 YAML 최대속도에 이 비율을 곱한다" 로 확정했습니다. 웹이 `open`·`fist` 에 보내는 `1.0` 은 **최대속도 100%** 입니다. 근거 없이 정한 값으로 전속을 내고 있습니다. 텐던 장력에 직결되므로 실물 시험 전에 받아야 합니다.

## 1.3 배포 설정

- [ ] **`.env.local` 작성** (`env.txt` 복사)
  ```
  VITE_WS_URL=ws://<Jetson-IP>:8000/ws/robot-state
  VITE_MJPEG_STREAM_URL=http://<Jetson-IP>:8080/stream/overlay
  VITE_MJPEG_RAW_STREAM_URL=          # 비우면 원본 전환 버튼이 사라집니다
  ```

- [ ] **`npm run dev` 로 띄울지 빌드로 띄울지 정하기**
  `VITE_WS_URL` 을 비우면 `ws://<현재 호스트>/ws/robot-state` 로 폴백하는데, **이를 받아 주는 것은 dev 서버 프록시뿐입니다.** 빌드 산출물이나 `preview` 에는 프록시가 없어 연결되지 않습니다. 빌드로 운영하려면 `VITE_WS_URL` 을 반드시 채웁니다.

- [ ] **Jetson MJPEG 가 http, 페이지가 https 인 경우 확인**
  혼합 콘텐츠로 브라우저가 영상을 막습니다. 내부망이라 둘 다 http 면 문제없습니다.

- [ ] **`npm test` `npm run build` `npm run lint` 통과 확인** (현재 151건)

---

# 2. 통합 전 — 다른 담당

- [!] **브릿지: `interfaces-bridge.md` 1장 구현**
  snapshot 6.4절 고정 6필드 + `control_state` + `recording`. `.msg` 원문 dump 로 보내면 됩니다.

- [!] **브릿지: snapshot 을 주기 발행으로**
  값이 안 바뀌어도 계속 보내야 합니다. 변경 시에만 보내면 **로봇이 멀쩡해도 화면이 상시 "연결 끊김"** 입니다.

- [!] **브릿지: `session_id` 를 JSON 문자열로**
  6.5절입니다. 숫자로 보내면 63-bit 값이 `JSON.parse` 시점에 손상되고 웹이 기록 종료·판정 전송을 차단합니다.

- [!] **브릿지: ack 의 `request_id` 를 요청과 동일하게**
  웹이 이 값으로 버튼 잠금을 풉니다. 없으면 2초 타임아웃으로만 풀립니다.

- [x] **`.msg`/`.srv` 최신본 확인** — 완료
  2026-08-04 최신본 반영(pending.js C-1·C-3·C-4·C-6 확정 제거). `SetControlMode` 요청은 `requested_mode`/`requested_owner`, `ControlState` 는 `active_mode`/`active_owner` 로 확인됨.

- [!] **스펙: 동시 접속 처리 결정** (D-1)
  `owner` 가 `WEB` 하나뿐이라 탭이 두 개면 둘 다 제어권 보유로 인식합니다. 웹에서 해결할 수 없습니다. 브릿지가 연결 하나만 허용하는 것이 가장 간단합니다.

---

# 3. 첫 접속 — 순서대로

브라우저 콘솔을 열고 시작합니다. **조용히 실패하는 경로를 전부 없앴으므로, 화면이 이상하면 콘솔에 이유가 있습니다.**

## 3.1 기동 확인

- [·] 콘솔에 시작 표가 나오는가 (`[진단] 내부 제어 웹 시작 — 현재 가정값`)
      WS·MJPEG 주소와 파생 임계값 4개가 표시됩니다. 주소가 `(미설정)` 이면 `.env.local` 을 확인합니다.

## 3.2 WebSocket 연결

- [·] 상단 우측이 **연결됨** 인가
- [·] `WS_ERROR` 진단이 없는가 → 있으면 주소·브릿지 기동·방화벽

## 3.3 snapshot 수신

콘솔에 아래가 나오면 각각 다음을 뜻합니다.

| 진단 | 뜻 | 조치 |
|---|---|---|
| `WS_UNRECOGNIZED_MESSAGE` | **`mode` 가 문자열이 아님** → snapshot 전체가 버려짐 | 브릿지가 `mode`·`recording_state` 를 symbolic string 으로 |
| `SNAPSHOT_MISSING_FIXED_FIELDS` | 6.4절 고정 필드 누락 | 미수신 객체는 `null` 이 아니라 `{}` 로 |
| `SNAPSHOT_NO_CONTROL_STATE` | **조작이 전부 잠김** | `ControlState.msg` 원문을 `control_state` 로 |
| `SNAPSHOT_NO_RECORDING` | 녹화만 잠김 | `RecordingState.msg` 원문을 `recording` 으로 |
| `UNKNOWN_ENUM_*` | 웹이 모르는 uint8 값 | 매핑표에 상수 추가 |
| `SESSION_ID_NUMERIC` | 63-bit 값 손상 | 10진 문자열로 |
| `HAND_COMMAND_VALUES_WRAPPER` | 7축이 `values` 안에 있음 | 최상위 필드로 (FR-30) |
| `SNAPSHOT_RATE_MISMATCH` | 실측 주기가 가정과 다름 | `pending.js` 의 `BRIDGE_SNAPSHOT_PERIOD_MS` 를 실측값으로 |

- [·] **`SNAPSHOT_RATE_MISMATCH` 가 알려 주는 실측값을 `pending.js` 에 반영**
      이 한 값에서 판정 임계값 4개가 파생됩니다. A-1 회신을 기다릴 필요 없이 여기서 확정됩니다.

## 3.4 표시 확인

- [·] 머리 색이 안전 상태에 따라 바뀌는가
- [·] 8상태 트랙의 표시자가 현재 상태에 있는가
- [·] 장치 표시가 실제 연결과 맞는가 (`단절:` 목록)
- [·] 모터 7채널이 다 나오는가. 값이 이어져 움직이는가

---

# 4. 기능 수용 시험 — 8.3절 대응

## 4.1 제어권 (FR-19 / FR-34)

- [·] MIMIC 보유 중 MANUAL 획득 버튼이 **잠겨** 있는가 (두 단계 강제)
- [·] 정지 → 획득 순서로만 전환되는가
- [·] 페이지 새로고침·재연결 후 **자동 획득이 없는가** (NFR-15·NFR-23)
- [·] 제어권 박동이 1초 주기로 뛰는가. 잃으면 멈추는가
- [·] LOCAL owner(teleop)가 잡고 있으면 `owner_conflict` 로 거부되는가

## 4.2 조작 (FR-22 / FR-23)

- [·] gesture 4종이 실제로 전송되는가 (`execute_gesture`, `gesture_name`)
- [·] 연달아 누르면 `motion_active` 로 거부되는가
- [·] **정지가 잠금과 무관하게 항상 전송되는가**
- [·] `stop_barrier_pending`·`stop_barrier_timeout` 안내가 뜨는가

## 4.3 안전 (FR-27 / FR-35)

- [·] HOLD 에서 자동복귀 조건(300ms·gap 100ms·SAFE 1000ms)과 STOP→RESET 대안을 **함께** 안내하는가
- [·] MANUAL 의 HOLD 에서 "재개용 명령을 스스로 만들지 않는다" 가 나오는가
- [·] RESET(7) 이 `UNKNOWN(7)` 이 아니라 `RESET` 으로 나오는가
- [·] SAFE·FAULT·ESTOP 에서 안전 초기화 버튼이 나오고, READY·RUN 에서는 안 나오는가
- [·] 손 미검출 확정 시 "자동 재개되지 않습니다" 가 나오는가
- [·] **정상 STOP 뒤 재획득이 RESET→READY 순서를 관측한 뒤에만 되는가** (E-2)
      웹은 READY 만 보고 버튼을 엽니다. 눌렀는데 거부되면 그 사유를 알려 주십시오

## 4.4 기록 (FR-18 / FR-26 / FR-40)

- [·] 기록 시작 → 종료 → 성공·실패 판정이 순서대로 되는가
- [·] Session ID 가 화면에 문자열로 나오는가 (숫자로 오면 차단됩니다)
- [·] 판정 대기 중에 새 기록이 막히는가
- [·] 판정 대기 표시가 조작 화면에서도 보이는가

## 4.5 영상 (FR-20)

- [·] MJPEG 가 나오는가
- [·] 손 검출·신뢰도 표시가 영상 안에 겹쳐 나오는가
- [·] **WebSocket 을 끊었을 때** 영상은 유지되고 검출 표시가 "마지막 값" 으로 바뀌는가

---

# 5. 알려진 지뢰

- [·] **탭 두 개를 열지 마십시오** (D-1 해결 전)
      둘 다 제어권 보유로 인식하고, 한쪽 STOP 이 다른 쪽 제어를 끊습니다. 시연 중 실수하기 쉽습니다.

- [·] **`speed_limit` 이 최대속도 비율입니다**
      B-1 회신 전에는 실물 파지 시험을 하지 마십시오.

- [·] **landmark 파생 필드가 없으면 재검출 진행률이 안 나옵니다**
      `hand_loss_latched`·`reacquire_*` 는 선택 필드입니다. 없으면 웹이 150ms 로 근사하고 진행률 바를 감춥니다. Must 요건(자동 재개 안 됨 고지)은 충족됩니다.

- [·] **`FR-35` 타이밍 숫자는 웹의 사본입니다**
      YAML 을 바꾸면 화면이 거짓말합니다. A-3 이 "가능" 이면 사본 14개가 사라집니다.

---

# 6. 통합 중 상시

- [·] 문제가 생기면 **콘솔 `__diag()`** 를 복사해 공유
      담당·횟수·증상·조치·근거·실제값이 나옵니다. 담당별 이슈 목록이 됩니다.
- [·] "이 숫자 근거가 뭐냐" 는 **`__pending()`** 으로 답
- [ ] 회신이 오면 `pending.js` 의 키를 고치고 **출처를 같은 자리에 기록**
      ```js
      BRIDGE_SNAPSHOT_PERIOD_MS: 100,
      // status  확정
      // 회신    브릿지 담당 / 2026-08-__
      // 원문    "10Hz 고정 주기 발행"
      ```

---

# 7. 통합 후

- [ ] `pending.js` 에서 확정된 항목의 status 정리
- [ ] `CONTRACT_ASSUMPTIONS` 에서 확인된 항목 삭제 (현재 C-2·C-5·C-7·C-8)
- [ ] `interfaces-bridge.md` 3장의 결정 사항을 1장으로 이동
- [ ] `bridgeContract.test.jsx` 에 통합에서 실제로 만난 실패를 회귀로 추가
- [ ] NFR-22 이벤트 로그 (미뤘다면)
- [ ] FR-24 의 MediaPipe·hand_target·MJPEG 개별 상태 — NFR-09 가 diagnostics 소관으로 정리했으므로 하지 않아도 됩니다

---

# 회신 대기 목록

`docs/pending-decisions.md` 에 상세가 있습니다.

| | 담당 | 없으면 |
|---|---|---|
| A-1 snapshot 주기 | 브릿지 | 자동 측정으로 대체 가능 |
| A-2 ack 상한 | 브릿지 | 2초 가정. 잠금이 먼저 풀릴 수 있음 |
| A-3 FR-35 타이밍 동봉 | 브릿지 | 사본 14개 유지 |
| A-4 `sequence_running` 범위 | 브릿지 | 표시 문구만 부정확 |
| **B-1 `speed_limit`** | 제어·기구 | **실물 시험 불가** |
| C-1 `label` | 기획 | 빈 문자열 유지 |
| D-1 동시 접속 | 스펙 | 탭 하나만 열기 |
| E-1 거부 사유 전달 방식 | 브릿지 | 웹이 8종 추가하면 대부분 해결 |
| E-2 STOP 뒤 재획득 | 브릿지 | 눌러 보고 거부되면 안내 |
