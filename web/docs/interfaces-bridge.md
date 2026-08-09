# Web Bridge ↔ 내부망 제어 웹 인터페이스

- 대상: `thing_ws/src/thing_web_bridge` (Jetson)
- 상대: `web/frontend` (Laptop)
- endpoint: `/ws/robot-state` (6.4절)
- 근거: 요구사항 명세서 V7.1, `thing_interfaces` `.msg`/`.srv`

## 분담

브릿지는 ROS 2 제어 경로 위에 있어 변경 위험이 큽니다. 그래서 **가공을 요구하지 않습니다.**

| | 담당 |
|---|---|
| `.msg` 원문을 snapshot 에 얹기 | 브릿지 |
| uint8 enum → 문자열, 시각 필드 해석 | 웹 |
| 장치 연결 상태·hand-loss latch 파생 | 웹 (브릿지가 주면 그것 우선) |
| 버튼 잠금 판정 | 웹 (ack 기반) |

브릿지에 요구하는 추가 필드는 **`control_state` 와 `recording` 두 개**입니다.

---

# 1. 브릿지가 해야 하는 것

## 1.1 snapshot top-level 6필드

| 키 | 타입 | 값 |
|---|---|---|
| `timestamp` | string | RFC 3339 UTC `Z`, snapshot 생성 시각 |
| `mode` | string | `DISABLED` \| `MIMIC` \| `MANUAL` \| `TELEOP` |
| `recording_state` | string | `IDLE` \| `STARTING` \| `RECORDING` \| `STOPPING` \| `COMPLETED` \| `FAILED` \| `INTERRUPTED` |
| `landmarks` | object | `HandLandmarks` 원문. 미수신 시 `{}` |
| `motor_state` | object | `MotorStatus` 원문. 미수신 시 `{}` |
| `safety_state` | object | `SafetyState` 원문. 미수신 시 `{}` |

- `mode` 와 `recording_state` **두 개만** symbolic string 입니다. 정수 불가.
  웹은 `mode` 가 문자열인지로 snapshot 을 판별합니다. 정수로 오면 **snapshot 전체가 버려집니다.**
- 세 객체는 미수신 시 `null` 이 아니라 `{}`.
- snapshot 에 `type` 필드를 넣지 마십시오. 웹은 `type` 유무로 snapshot 과 ack 를 구분합니다.

## 1.2 추가 필드 2개

| 키 | 내용 |
|---|---|
| `control_state` | `ControlState.msg` 원문 |
| `recording` | `RecordingState.msg` 원문 |

`rosidl_runtime_py.convert.message_to_ordereddict(msg)` 결과를 그대로 넣으면 됩니다. 필드명 변경·축약 없음.

`recording_state` 가 아닌 이유: 그 키는 1.1 의 상태 문자열이 씁니다.

미수신 시 `{}`. **`control_state` 가 없으면 웹은 owner 를 알 수 없어 제어권을 인정하지 않고 모든 조작을 막습니다** (fail-closed). `recording` 이 없으면 녹화만 막습니다.

## 1.3 uint8 enum

정수로 보내면 됩니다. 웹이 변환합니다.

| 메시지 | 필드 | 매핑 |
|---|---|---|
| `ControlState` | `active_mode` | 0 DISABLED, 1 MIMIC, 2 MANUAL, 3 TELEOP |
| `ControlState` | `active_owner` | 0 NONE, 1 WEB, 2 LOCAL |
| `SafetyState` | `state` | 0 INIT, 1 READY, 2 RUN, 3 HOLD, 4 SAFE, 5 FAULT, 6 ESTOP, **7 RESET** |
| `RecordingState` | `state` | 0 IDLE, 1 STARTING, 2 RECORDING, 3 STOPPING, 4 COMPLETED, 5 FAILED, 6 INTERRUPTED |
| `RecordingState` | `last_mimic_result` | 0 UNSET, 1 SUCCESS, 2 FAILURE |
| `HandCommand` | `source` | 0 UNKNOWN, 1 MIMIC, 2 TELEOP, 3 GESTURE, 4 SEQUENCE, 5 SAFETY |

표에 없는 정수가 오면 웹은 `UNKNOWN(n)` 으로 표시하고 조작을 막습니다. 임의로 뭉개지 않습니다.

## 1.4 시각 필드

`.msg` 마다 위치가 다릅니다.

| 위치 | 메시지 |
|---|---|
| `stamp` | `ControlState`, `SafetyState`, `HandCommand` |
| `header.stamp` | `HandLandmarks`, `MotorStatus`, `RecordingState` |

`builtin_interfaces/Time` 원문(`{sec, nanosec}`) 그대로 보내면 됩니다. **문자열로 바꾸지 마십시오.**

웹은 이 값을 파싱하지 않고 **바뀌었는지만** 비교해 신선도를 판정합니다. 매 발행마다 전진하기만 하면 됩니다.

값이 바뀌지 않아도 **snapshot 은 주기적으로 계속 보내야 합니다.** 변경 시에만 보내면 로봇이 정상인데도 웹이 상시 "연결 끊김" 으로 표시합니다. 주기는 3.1 참조.

## 1.5 7논리축

`HandCommand` 의 7축은 **최상위 고정 필드**입니다 (FR-30). 래퍼로 감싸지 마십시오.

```json
{ "thumb_flex": 0.42, "thumb_opp": 0.10, "thumb_abd": 0.05,
  "index_flex": 0.88, "middle_flex": 0.90, "ring_flex": 0.85, "little_flex": 0.80,
  "source": 1, "sequence": 1234, "speed_limit": 1.0, "confidence": 0.93,
  "stamp": { "sec": 1785000000, "nanosec": 0 } }
```

## 1.6 session_id

`RecordingState.active_session_id`, `last_session_id` 를 **JSON 문자열**로 직렬화합니다.

```json
"last_session_id": "8531234567890123456"
```

`0`(세션 없음)은 숫자 `0` 그대로 보내도 됩니다. 웹이 "세션 없음" 으로 처리합니다.

0 이 아닌 값을 숫자로 보내면 안 됩니다.

```
브릿지 8531234567890123456  →  JS JSON.parse  →  8531234567890124000
```

63-bit 값은 파싱 시점에 손상되고 복구할 수 없습니다. 웹은 숫자 session_id 를 감지하면 `StopRecording`·`SetMimicResult` 전송을 차단합니다.

근거: 6.5절 "JSON·API·EC2에서는 10진 문자열로 표현한다"

## 1.7 클라이언트 → 서버 요청

envelope:

```json
{ "request_id": "web-...", "type": "...", "timestamp": "...", "payload": {} }
```

| `type` | ROS 2 대상 | payload |
|---|---|---|
| `set_control_mode` | `/thing/set_control_mode` | `{ requested_mode, requested_owner }` |
| `stop` | `/thing/set_control_mode` | `{ requested_mode: "DISABLED", requested_owner: "NONE" }` |
| `execute_gesture` | `/thing/execute_gesture` | `{ gesture_name, speed_limit }` |
| `execute_sequence` | `/thing/execute_sequence` | `{ sequence_name, speed_limit }` |
| `start_recording` | `/thing/start_recording` | `{ label }` |
| `stop_recording` | `/thing/stop_recording` | `{ session_id }` |
| `set_mimic_result` | `/thing/set_mimic_result` | `{ session_id, result }` |
| `reset_safety` | `/thing/reset_safety` | `{}` |

- `gesture_name` 은 canonical 4종만: `open` `fist` `pinch` `cylindrical_grasp`.
  웹이 alias(`home` `paper` `rock`)를 펴서 보내므로 브릿지는 alias 처리 불필요.
- `sequence_name`: `countdown` `scissors_rock_paper`
- `result`: `SUCCESS` \| `FAILURE`
- `session_id`: 10진 문자열
- **enum 값을 웹은 symbolic string 으로 보냅니다.** `.srv` 는 uint8 이므로 브릿지가 매핑합니다 (`requested_mode` `requested_owner` `result`).
- `set_control_mode` 의 payload 키는 `.srv` 요청 필드명과 같습니다. 웹은 `{ requested_mode, requested_owner }` 로 보내며, 이는 `thing_interfaces/srv/SetControlMode.srv`(`requested_mode`/`requested_owner`)와 일치합니다. 브릿지 재매핑은 불필요합니다.

## 1.8 서버 → 클라이언트 ack

모든 요청에 1건, 요청자에게만.

```json
{ "type": "ack", "request_id": "<요청과 동일>", "accepted": true, "reason": "accepted" }
```

**`request_id` 는 요청과 동일해야 합니다.** 웹이 이 값으로 버튼 잠금을 풉니다. 없거나 다르면 2초 타임아웃으로만 풀립니다.

`reason` 값 (FR-37 / FR-18 / FR-34):

| 계열 | 값 |
|---|---|
| 수락 | `accepted` |
| 제어권 | `invalid_mode` `owner_conflict` `owner_lease_expired` `safety_not_ready` |
| 동작 | `motion_active` `recording_active` |
| 동작(Gesture·Sequence) | `invalid_gesture` `invalid_sequence` `invalid_speed_limit` `not_manual_mode` `control_state_unavailable\|stale` `safety_state_unavailable\|stale` `stop_latched` |
| 정지 | `stop_barrier_pending` `stop_barrier_timeout` |
| 기록 시작 | `not_mimic_mode` `start_failed` `already_recording` `result_pending` |
| 기록 종료 | `not_recording` `session_mismatch` `stop_failed` |

`.srv` 응답의 `reason` 문자열을 그대로 전달하십시오. 다른 문자열로 변환한다면 변환표를 주셔야 웹이 안내 문구를 맞출 수 있습니다.

---

# 2. 확정된 사항

`.msg`/`.srv` 와 명세서로 결정되어 협의가 끝난 항목입니다.

| | |
|---|---|
| uint8 상수값 | `.msg` 선언 순서와 일치. 1.3 표 |
| 시각 필드 위치·타입 | `stamp` / `header.stamp`, `{sec, nanosec}`. 1.4 |
| `HandCommand` 7축 | 최상위 고정 필드. 1.5 |
| `session_id` | JSON 10진 문자열. 1.6 |
| `ExecuteGesture.srv` | `gesture_name`, `speed_limit` |
| `ExecuteSequence.action` | `sequence_name`, `speed_limit` |
| `StopRecording.srv` | `session_id` |
| `SetMimicResult.srv` | `session_id`, `result` |
| `StartRecording.srv` | `label` |
| `SafetyState.RESET=7` | 명시적 정상 STOP 뒤 모터 이동 없이 torque OFF 를 재확인하는 상태(최소 500ms·7모터 torque_enabled=false) |

---

# 3. 결정이 필요한 항목

## 3.1 snapshot 발행 주기 — 브릿지

몇 Hz 로 보냅니까? 값이 바뀌지 않아도 계속 보냅니까?

웹의 판정 임계값 4개가 이 값에서 파생됩니다 (장치 up/down, 모터 stale, 영상 정지, snapshot 끊김). 현재 200ms(5Hz) 주기 발행을 가정합니다.

실제가 더 느리거나 변경 시에만 발행하면 **정상 동작 중에도 화면이 상시 "연결 끊김"** 입니다.

> 웹이 실제 도착 간격을 20표본 재서 가정값과 50% 이상 어긋나면 콘솔에 실측값을
> 띄웁니다. 통합 첫 10초면 주기는 확인됩니다. **"주기 발행인가" 만 회신이 필요합니다.**

## 3.2 서비스 ack 왕복 상한 — 브릿지

`execute_gesture` `execute_sequence` `start_recording` `stop_recording` `set_mimic_result` 의 상한은 몇 ms 입니까?

명세는 STOP 의 Guard ACK 만 규정합니다(기본 300ms·최대 500ms, FR-35). 웹은 나머지를 2000ms 로 가정하고, 그 시간이 지나면 ack 없이 버튼 잠금을 풉니다.

## 3.3 FR-35 타이밍 값을 snapshot 에 실어 줄 수 있습니까 — 브릿지

`hold_recovery_activity_ms` `hold_recovery_max_gap_ms` `safe_deadline_ms` `stop_settle_ms` `fault_clear_stable_ms` `estop_release_stable_ms` `owner_lease_timeout_ms`

FR-27 은 HOLD 에서 자동복귀 조건을 숫자와 함께 안내하라고 합니다. 웹은 지금 명세 조문의 숫자를 **사본 14개**로 들고 화면에 씁니다. FR-41 이 이 값들을 YAML 소관으로 두었으므로 **YAML 을 바꾸면 웹 화면이 거짓말합니다.**

보내 주시면 사본 14개가 사라집니다.

## 3.4 `ControlState.sequence_running` 의 범위 — 브릿지

`ExecuteSequence` 액션 전용입니까, `ExecuteGesture` 실행 중에도 `true` 입니까?

웹은 이 필드로 버튼을 잠그지 않습니다(ack 기반). 표시 문구를 맞추기 위한 확인이며 동작에는 영향이 없습니다.

## 3.5 정상 STOP 뒤 재획득 조건 — 브릿지

FR-35 는 정상 STOP 뒤 재획득을 **RESET 과 그보다 새 stamp 의 READY 를 순서대로 관측한 뒤에만** 허용합니다.

웹은 `SafetyState` 가 READY 이면 획득 버튼을 엽니다. 순서를 추적하지 않으므로 **사용자가 눌렀는데 거부되는** 상황이 생깁니다. 안전에는 문제가 없습니다.

거부 시 어떤 `reason` 이 오는지 알려 주시면 문구를 맞추겠습니다. 웹이 순서를 함께 추적하는 편이 낫다면 그렇게 바꿉니다.

## 3.6 동시 접속 클라이언트 식별 — 스펙

`owner` 가 `WEB` 하나뿐이고 클라이언트 식별자가 없습니다. 탭이 두 개면 둘 다 1초마다 같은 `SetControlMode(MIMIC, WEB)` 를 보내고, `command_manager` 는 같은 mode·owner 라 양쪽 모두 갱신으로 처리합니다. **두 화면 모두 "제어권 보유" 로 표시되고, 한쪽 STOP 이 다른 쪽 제어를 끊습니다.**

웹에서 해결할 수 없습니다. 브릿지가 `/ws/robot-state` 연결을 하나만 허용하는 것이 가장 간단합니다.

---

# 4. 선택 필드

보내면 웹이 그대로 씁니다. 없으면 웹이 파생합니다. 어느 쪽이든 동작합니다.

| 키 | 위치 | 없을 때 웹 동작 |
|---|---|---|
| `connection_status` | top-level | snapshot 조각의 갱신 여부로 파생 |
| `hand_loss_latched` | `landmarks` 안 | 검출 실패 150ms 지속으로 근사 |
| `reacquire_elapsed_ms` | `landmarks` 안 | 재검출 진행률 바 미표시 |
| `reacquire_stable_ms` | `landmarks` 안 | 재검출 진행률 바 미표시 |
| `last_hand_command` | top-level | 7논리축·confidence 표 미표시 (FR-21 Should) |

`connection_status` 형식:

```json
{ "jetson": "up", "rpi": "up", "ros2": "up", "camera": "up", "motor": "down" }
```

값은 `"unknown"` \| `"up"` \| `"down"` 셋 중 하나. bool 아님.

> NFR-09 는 웹에서 손 검출·mode·owner·연결·SafetyState·RecordingState 를 확인하고
> **상세 원인은 diagnostics·로그에서 확인한다** 고 정했습니다. 그래서 `camera`·
> `MediaPipe`·`hand_target`·`MJPEG` 개별 상태는 요구하지 않습니다. 위 5종이면 됩니다.

---

# 5. 웹이 하지 않는 것

- 임의 ROS topic·motor ID 전송 (NFR-20)
- 7논리축 값 전송 — `ExecuteGesture.srv` 에 해당 필드가 없고 목표값은 YAML 소관 (FR-41)
- rosbag2·EC2 SQLite 접근 (FR-26)
- 안전 판정 — 범위·속도·timeout·E-Stop 은 Raspberry Pi 담당 (NFR-16)
- 제어권 자동 획득·재획득 — 재연결·복구 후에도 사용자가 직접 (NFR-15, NFR-23)

---

# 6. 검증

검증을 위한 파일은 [sample-snapshot.jsonc](sample-snapshot.jsonc)

## 로봇 없이 확인

```bash
cd web/frontend
npm run mock                  # ws://localhost:8000/ws/robot-state
npm run mock -- --no-derived  # 선택 필드를 빼고 웹의 파생 경로 확인
```

mock 은 개발용 픽스처이지 계약의 근거가 아닙니다. 계약은 이 문서, 경계 검증은 `bridgeContract.test.jsx` 입니다.

## 통합 중 문제가 생기면

브라우저 콘솔을 보십시오. 조용히 실패하는 경로를 전부 없앴습니다.

```
▼ [진단:브릿지] snapshot 에 control_state 가 없습니다 (3회째)
   증상   owner 를 알 수 없어 제어권을 인정하지 않습니다(fail-closed).
   조치   ControlState.msg 원문을 control_state 키로 실어 보내세요.
   근거   FR-19 / interfaces-bridge.md 1.2
```

앞머리에 담당이 붙습니다. 콘솔에서 `__diag()` 를 치면 지금까지 잡힌 문제 전체가 나옵니다.
