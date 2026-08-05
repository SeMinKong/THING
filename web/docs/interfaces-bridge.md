# 내부 제어 웹 ↔ Web Bridge 계약

- 대상: `thing_ws/src/thing_web_bridge/web_bridge_node` (Jetson) ↔ `web/frontend` (Laptop)
- endpoint: `/ws/robot-state` (6.4절)
- 상위 기준: 요구사항 명세서 V7.1 6.4절, `docs/interfaces.md`의 **WebSocket** 절,
  `thing_interfaces`의 `.msg`/`.srv`
- 이 문서는 브라우저 쪽에서 보이는 모양만 다룹니다. ROS 2 계약의 단일 기준은
  `docs/interfaces.md`입니다.

**이 문서는 구현 완료 시점의 실제 동작을 동결한 것입니다.** 6.4절이 "세 객체의
세부 field와 클라이언트→서버 요청 JSON은 개발 중 확정하되 통합 시험 전에
동결한다"고 정한 그 확정본입니다.

프론트엔드 진단 메시지의 `ref` 필드가 아래 절 번호를 가리킵니다.

| 절 | 내용 |
| --- | --- |
| 1.1 | snapshot 고정 6필드 |
| 1.2 | snapshot 확장 필드 |
| 1.3 | 값 표현 (enum·실수·시각) |
| 1.3.1 | `connection_status` |
| 1.3.2 | `last_hand_command` 7축 |
| 1.3.3 | 시각 필드 |
| 1.4 | Session ID 표현 |
| 1.5 | 요청 전송 |
| 1.6 | ACK |
| 1.7 | 브리지가 하지 않는 것 |

---

## 분담

6.4절이 top-level 여덟 필드를 고정하면서 두 갈래를 구분했습니다.

| 구분 | 대상 | 취급 |
| --- | --- | --- |
| **원문 절** | `control_state`, `recording` | `.msg` **원문을 그대로** 싣습니다. enum은 정수, 파생 필드 없음 |
| **파생 표시 객체** | `landmarks`, `motor_state`, `safety_state` | symbol과 파생값을 붙입니다 |
| **표시용 mirror** | top-level `mode`, `recording_state` | 원문에서 파생한 symbol. 원문과 항상 일치 |

| | 담당 |
| --- | --- |
| `.msg` 원문을 snapshot에 얹기 | 브리지 |
| **원문 절의 uint8 enum → 문자열 변환** | **웹** (브리지는 정수를 보냅니다) |
| 파생 표시 객체의 symbol 변환 | 브리지 (1.3) |
| 시각 필드 원문 유지 | 브리지 (1.3.3) |
| 장치 연결 상태·hand-loss latch 파생 | 브리지 (1.2, 1.3.1) |
| 버튼 잠금 판정 | 웹 (ack 기반) |

`control_state`·`recording`은 **정수 enum이 그대로 옵니다.** 웹의
`toSymbol`·`*_BY_ORDINAL` 변환표가 이 두 절에서 실제로 쓰입니다. 초안에서
브리지가 symbol로 바꾸는 안을 검토했지만, 6.4절이 "원문을 그대로 싣고"로
확정해 원문 유지로 되돌렸습니다.

`landmarks`·`motor_state`·`safety_state`만 표시 객체로 두는 이유는 SafetyState의
reset 가능 여부·reason과 hand-loss latch가 develop 스키마 밖 파생값이기
때문입니다. 브리지는 토픽의 실제 수신 시각을 직접 보므로 신선도·latch 판정의
원천을 가지고 있고, 웹은 snapshot만 받아 근사만 가능합니다 (FR-24 "가짜 값으로
채우지 않는다").

---

## 1. WebSocket

endpoint는 `/ws/robot-state` 하나입니다. 프론트는 `WS_URL` 외부 설정으로
내부망 주소를 주입합니다 (FR-28).

| 항목 | 값 |
| --- | --- |
| snapshot 주기 | **200ms 고정, 주기 발행.** 값이 바뀌지 않아도 계속 보냅니다 |
| 동시 연결 | **1개만.** 두 번째 연결은 close code `1013`으로 거절 |
| 잘못된 경로 | close code `1008` |
| 서비스 왕복 상한 | 2000ms |
| 요청 대기열 상한 | 32건. 초과 시 `web_queue_overflow` |

새 snapshot은 같은 연결의 이전 snapshot을 대체합니다. 누적하지 않습니다.

동시 연결을 하나로 제한한 이유는 탭을 두 개 열면 한쪽에서 누른 STOP이 다른 쪽
제어권까지 해제하기 때문입니다. owner는 `WEB` 하나뿐이고 클라이언트 식별자가
없어 웹에서는 구분할 수 없습니다.

연결이 끊기면 슬롯은 **수 ms 안에**(실측 3.5ms) 풀립니다. 새로고침 간격에서는
겹치지 않지만, 자동 재연결이 이전 소켓을 닫은 직후 곧바로 붙으면 드물게
`1013`을 받을 수 있습니다. 그 경우 짧게 두고 한 번 더 시도하면 됩니다.

### 1.1 snapshot 고정 8필드

6.4절 계약입니다. 이름·순서를 바꾸지 않습니다.

```json
{
  "timestamp": "2026-08-05T12:00:00.000Z",
  "mode": "MIMIC",
  "recording_state": "RECORDING",
  "landmarks": {},
  "motor_state": {},
  "safety_state": {},
  "control_state": {},
  "recording": {}
}
```

- `timestamp` — 브리지가 snapshot을 만든 시각. RFC 3339 UTC `Z`
- `mode` — `DISABLED` / `MIMIC` / `MANUAL` / `TELEOP`.
  `control_state.active_mode`에서 파생한 표시용 mirror이며 원문과 항상 일치합니다
- `recording_state` — `IDLE` / `STARTING` / `RECORDING` / `STOPPING` /
  `COMPLETED` / `FAILED` / `INTERRUPTED`. `recording.state`의 mirror입니다
- `landmarks` / `motor_state` / `safety_state` — 파생 표시 객체 (1.2)
- `control_state` / `recording` — `.msg` **원문** (1.2)
- 다섯 객체 모두 아직 유효 데이터를 받지 못하면 `null`이 아니라 `{}`

두 가지를 지킵니다. 웹이 이 두 규칙으로 메시지를 판별하기 때문입니다.

- **`mode`와 `recording_state`는 항상 문자열입니다.** 웹은 `mode`가 문자열인지로
  snapshot을 판별합니다. 정수로 오면 snapshot 전체가 버려집니다.
- **snapshot에는 `type` 필드가 없습니다.** 웹은 `type` 유무로 snapshot과 ACK를
  구분합니다. `type`은 ACK에만 있습니다 (1.6).

### 1.2 원문 절과 파생 표시 객체

**원문 절 — `control_state`, `recording`**

6.4절이 "`ControlState.msg`·`RecordingState.msg` 원문을 그대로 싣고"로 정한
두 절입니다. **enum은 정수 그대로이고 파생 필드가 붙지 않습니다.**
`age_ms`·`stale`도 없습니다.

예외는 Session ID 하나뿐입니다 (1.4). 6.4절이 "10진 문자열로 직렬화하며 세션
없음은 `"0"`으로 표현한다"고 명시했습니다.

`control_state`가 없으면 웹은 owner를 알 수 없어 제어권을 인정하지 않고 모든
조작을 막습니다 (fail-closed). `recording`이 없으면 녹화만 막힙니다.

**추가 top-level 필드 — 8필드 밖**

FR-21·FR-24 표시를 위해 브리지가 더 얹습니다.

| 필드 | 내용 |
| --- | --- |
| `last_hand_command` | 최종 `/thing/command` 원문. 7논리축 표시용 (FR-21 Should) |
| `connection_status` | 브리지 파생. 1.3.1 |

**파생 표시 객체 — `landmarks`, `motor_state`, `safety_state`**

이 셋과 `last_hand_command`에는 공통으로 두 필드가 붙습니다.

| 필드 | 내용 |
| --- | --- |
| `age_ms` | 마지막 수신 이후 경과 ms |
| `stale` | 표시용 임계값 초과 여부. 데이터 토픽 1000ms, 상태 토픽 5000ms |

`stale`은 표시 전용입니다. 제어·안전 판정은 Raspberry Pi가 하며 이 값을 쓰지
않습니다. `age_ms`를 같이 보내므로 웹이 자기 임계값으로 다시 판단해도 됩니다
(FR-25 "stale 값은 연결 끊김과 구별해야 한다").

`safety_state`에는 `reset_allowed`가 추가됩니다. FR-35에 따라
`SAFE`·`FAULT`·`ESTOP`에서만 `true`입니다.

`landmarks`에는 FR-27이 Web Bridge에 배정한 hand-loss 파생 상태가 붙습니다.

| 필드 | 내용 |
| --- | --- |
| `detect_valid` | `detected` + 오른손 + `confidence >= 0.70` |
| `hand_loss_latched` | 무효가 150ms 연속되면 `true` |
| `reacquire_elapsed_ms` | 현재 연속 유효 경과 ms |
| `reacquire_stable_ms` | 재검출 인정 기준값 `300` |
| `confidence_min` | 유효 판정에 쓴 임계값 `0.70` |

**`hand_loss_latched`는 재검출만으로 해제되지 않습니다.** FR-01이
"재검출은 복구 조건 확인일 뿐 이전 명령이나 제어를 자동 재개하거나 즉시 latch를
해제하지 않는다"고 정합니다. 브리지는 SafetyState가 실제로 `RUN`이 된 것을
관측했을 때만 닫습니다. 그래서 `reacquire_elapsed_ms >= 300`이면서
`hand_loss_latched`가 `true`인 상태가 정상적으로 존재합니다. 이 조합에서
"제어가 재개됐다"고 표시하면 안 됩니다.

### 1.3 값 표현 (enum·실수·시각)

enum 표현은 **절에 따라 다릅니다.**

| 위치 | 표현 | 예 |
| --- | --- | --- |
| top-level `mode`, `recording_state` | **symbol 문자열** (mirror) | `"MIMIC"` |
| `control_state`, `recording` (원문 절) | **정수 그대로** | `active_mode: 1` |
| `landmarks`, `safety_state`, `last_hand_command` (파생 표시 객체) | **symbol 문자열** | `"RIGHT"`, `"FAULT"` |

원문 절에서 정수를 문자열로 바꾸는 것은 웹 몫입니다(`toSymbol`·`*_BY_ORDINAL`).
아래 표는 그 대조표이며, `test_interface_contract.py`가 실제 `.msg` 상수와 자동
검증합니다.

| 메시지 | 필드 | 순서 |
| --- | --- | --- |
| `ControlState` | `active_mode` | 0 DISABLED, 1 MIMIC, 2 MANUAL, 3 TELEOP |
| `ControlState` | `active_owner` | 0 NONE, 1 WEB, 2 LOCAL |
| `SafetyState` | `state` | 0 INIT, 1 READY, 2 RUN, 3 HOLD, 4 SAFE, 5 FAULT, 6 ESTOP, **7 RESET** |
| `RecordingState` | `state` | 0 IDLE, 1 STARTING, 2 RECORDING, 3 STOPPING, 4 COMPLETED, 5 FAILED, 6 INTERRUPTED |
| `RecordingState` | `last_mimic_result` | 0 UNSET, 1 SUCCESS, 2 FAILURE |
| `HandLandmarks` | `handedness` | 0 UNKNOWN, 1 LEFT, 2 RIGHT |
| `HandCommand` | `source` | 0 UNKNOWN, 1 MIMIC, 2 TELEOP, 3 GESTURE, 4 SEQUENCE, 5 SAFETY |

읽기 실패로 `NaN`·`Infinity`가 된 실수는 `null`로 보냅니다. 모터 통신 실패 시
`NaN`은 정상 시나리오이며 명세 6.5절도 "읽기 실패 숫자는 JSON `null`"로 정합니다.

#### 1.3.1 `connection_status`

FR-24. 값은 `"up"` / `"down"` / `"unknown"` 세 가지입니다. bool이 아닙니다.

```json
"connection_status": {
  "jetson": "up", "rpi": "up", "ros2": "up",
  "camera": "up", "motor": "unknown"
}
```

세 번째 값이 필요한 이유는 "아직 못 받았다"와 "끊겼다"가 다르기 때문입니다.
`camera`를 `false`로 단정하면 MJPEG 스트림이 정상인데도 영상이 가려집니다.

| 키 | 브리지의 판정 근거 |
| --- | --- |
| `jetson` | snapshot 자체가 Jetson에서 생성되므로 도달했다는 사실이 증거. 항상 `up` |
| `rpi` | `control_state`·`safety_state`·`motor_status` 중 하나라도 신선 |
| `ros2` | 어느 토픽이든 신선 |
| `camera` | `landmarks` 신선도로 **대리 판정** |
| `motor` | `motor_status` 신선 |

`camera`는 `image_raw`를 직접 구독하지 않습니다. `camera_node`·`mediapipe_node`
·`hand_target_node`·`mjpeg_streamer`를 각각 구분하지도 않습니다. NFR-09가
"상세 원인은 diagnostics·로그에서 확인한다"로 웹의 범위를 정했기 때문입니다.
노드별 상태가 필요하면 `/thing/diagnostics`를 보십시오.

#### 1.3.2 `last_hand_command` 7축

`HandCommand.msg`의 7논리축은 **최상위 고정 필드**입니다 (FR-30). `values` 같은
래퍼로 감싸지 않습니다.

```json
"last_hand_command": {
  "stamp": {"sec": 1785283200, "nanosec": 0},
  "sequence": 1234, "source": "MIMIC",
  "thumb_flex": 0.1, "thumb_opp": 0.2, "thumb_abd": 0.0,
  "index_flex": 0.3, "middle_flex": 0.3,
  "ring_flex": 0.2, "little_flex": 0.2,
  "speed_limit": 0.25, "confidence": 0.92,
  "age_ms": 40, "stale": false
}
```

출처는 최종 `/thing/command`입니다. `command_guard`를 통과해 모터로 실제
전달된 목표이므로 화면 값과 로봇 동작이 어긋나지 않습니다.

#### 1.3.3 시각 필드

`.msg`마다 위치가 다릅니다. 브리지는 원문 위치를 그대로 둡니다.

| 위치 | 메시지 |
| --- | --- |
| `stamp` | `ControlState`, `SafetyState`, `HandCommand` |
| `header.stamp` | `HandLandmarks`, `MotorStatus`, `RecordingState` |

`builtin_interfaces/Time`은 **`{sec, nanosec}` 원문 그대로** 보냅니다. 문자열로
바꾸지 않습니다. 문자열 시각은 top-level `timestamp`(1.1) 하나뿐입니다.

웹은 이 값을 파싱하지 않고 바뀌었는지만 비교해 신선도를 판정해도 됩니다.
브리지가 `age_ms`·`stale`을 함께 주므로(1.2) 그쪽을 쓰는 편이 정확합니다.

### 1.4 Session ID

`uint64`를 JSON 숫자로 보내면 JavaScript가 정밀도를 잃습니다.

```
8531234567890123456  →  JS JSON.parse  →  8531234567890124000
```

63-bit 값은 파싱 시점에 손상되고 복구할 수 없습니다. 6.4절이 **10진 문자열**로
직렬화하고 **세션 없음은 `"0"`으로 표현**하도록 정했습니다.

```json
"recording": { "active_session_id": "8531234567890123456", "last_session_id": "0" }
```

`"0"`은 JavaScript에서 truthy이므로 그대로 쓰면 활성 세션이 없는데도
`StopRecording(session_id="0")`을 보내게 됩니다. 웹의 `readSessionId`가 `"0"`을
빈 문자열로 정규화하고 있어 이 부분은 이미 안전합니다.

요청에서는 `"0"`을 보내면 안 됩니다. 브리지는 `0`과 63-bit 초과를
`invalid_session_id`로, 표준 10진 표기가 아닌 값(선행 `0`, 비ASCII 숫자 등)을
`web_malformed_request`로 거부합니다 (1.6).

### 1.5 요청 전송

```json
{
  "request_id": "web-abc-1",
  "type": "set_control_mode",
  "timestamp": "2026-08-05T12:00:00.000Z",
  "payload": { "requested_mode": "MIMIC", "requested_owner": "WEB" }
}
```

네 필드가 정확히 있어야 하고 `payload` 키도 정확히 일치해야 합니다. 남거나
모자라면 `web_malformed_request`입니다 (FR-23).

| type | ROS 2 대상 | payload |
| --- | --- | --- |
| `set_control_mode` | `/thing/set_control_mode` | `requested_mode`: `MIMIC`\|`MANUAL`, `requested_owner`: `WEB` |
| `stop` | `/thing/set_control_mode` | `requested_mode`: `DISABLED`, `requested_owner`: `NONE` |
| `execute_gesture` | `/thing/execute_gesture` | `gesture_name`: `open`\|`fist`\|`pinch`\|`cylindrical_grasp`, `speed_limit` |
| `execute_sequence` | `/thing/execute_sequence` | `sequence_name`: `countdown`\|`scissors_rock_paper`, `speed_limit` |
| `start_recording` | `/thing/start_recording` | `label` (문자열, 빈 문자열 허용) |
| `stop_recording` | `/thing/stop_recording` | `session_id` (문자열) |
| `set_mimic_result` | `/thing/set_mimic_result` | `session_id`, `result`: `SUCCESS`\|`FAILURE` |
| `reset_safety` | `/thing/reset_safety` | `{}` |

- **enum은 웹이 symbol 문자열로 보냅니다.** `.srv`는 uint8이므로 브리지가
  정수로 매핑합니다 (`requested_mode`·`requested_owner`·`result`).
- payload 키는 `.srv` 요청 필드명과 같습니다. 브리지 재매핑은 없습니다.
- `gesture_name`은 canonical 4종만 받습니다. 웹이 alias(`home`·`paper`·`rock`)를
  펴서 보내므로 브리지는 alias를 처리하지 않습니다.
- `DISABLED`는 `set_control_mode`가 아니라 `stop` type으로 보냅니다.
  MIMIC↔MANUAL 직접 전환은 브리지도 막습니다 (FR-19).
- `speed_limit`은 `0.0 < value <= 1.0`. `0`과 `NaN`은 거부됩니다 (FR-06).
- lease 갱신은 **웹이** 같은 mode·owner `set_control_mode`를 1000ms마다 보냅니다
  (FR-34). 브리지가 대신 보내지 않습니다. 1.7 참고.

**STOP 선점** — FR-19·FR-31에 따라 브리지는 수신과 실행을 분리합니다.

- 일반 요청은 도착 순서대로 하나씩 처리
- `stop`·`reset_safety`는 대기열을 건너뛰고 즉시 처리
- `stop`은 아직 시작하지 않은 일반 요청을 폐기하고 각각에 ACK를 돌려줌
- 대기 중 같은 mode·owner 갱신은 최신 하나만 남김 (`web_superseded`)

따라서 **ACK 순서가 요청 순서와 다를 수 있습니다.** 반드시 `request_id`로
찾으십시오. 순서를 가정하면 STOP ACK를 다른 요청의 응답으로 오인합니다.

### 1.6 ACK

모든 요청은 폐기되더라도 **정확히 한 번** ACK를 받습니다. 요청자에게만 갑니다.

```json
{
  "type": "ack",
  "request_id": "web-abc-1",
  "accepted": true,
  "reason": "accepted",
  "timestamp": "2026-08-05T12:00:00.100Z"
}
```

`request_id`는 요청의 값을 그대로 돌려줍니다. 웹이 이 값으로 버튼 잠금을
풉니다. `accepted`는 항상 boolean입니다. type에 따라 필드가 더 붙습니다.

| type | 추가 필드 |
| --- | --- |
| `set_control_mode`, `stop` | `active_mode`, `active_owner` |
| `start_recording` | `session_id`, `bag_path` |
| `stop_recording` | `stopped_session_id`, `bag_path` |

`reason`은 세 계열입니다. **접두어로 출처가 구분됩니다.**

**`web_*`·`invalid_*`** — 브리지가 ROS에 보내기 전에 스스로 내린 판단입니다.

| reason | 웹이 할 일 |
| --- | --- |
| `web_malformed_request` | 요청 형식 버그. 콘솔 확인 |
| `web_unknown_type` | 8종에 없는 type. 요청 버그 |
| `web_preempted_by_stop` | STOP 때문에 폐기됨. **버튼 잠금을 풀고 정상 취소로 안내** |
| `web_superseded` | lease 갱신이 최신 것으로 교체됨. 무시해도 됨 |
| `web_queue_overflow` | 요청을 너무 빨리 보냄. 재시도 가능 |
| `web_bridge_error` | 브리지 내부 예외 |
| `invalid_mode` | mode·owner 조합 오류 |
| `invalid_session_id` | Session ID가 `0`이거나 63-bit 초과 |

**`service_*`·`action_*`** — ROS 호출 실패입니다.

| reason | 의미 |
| --- | --- |
| `service_unavailable` | 서비스 서버가 아직 없음. Raspberry Pi 미기동 등 |
| `service_timeout` | 2000ms 안에 응답 없음 |
| `service_failed` | 호출이 예외로 끝남 |
| `service_rejected` | 서버가 거부했지만 `reason`이 비어 있음 |
| `action_unavailable` / `action_timeout` / `action_failed` | Sequence 액션 |
| `reset_rejected` | Safety Reset 거부인데 `message`가 비어 있음 |

**그 밖의 값** — ROS 응답 원문을 그대로 전달한 것입니다. 브리지는 변환하지
않습니다.

| 계열 | 값 |
| --- | --- |
| 제어권 (FR-37) | `accepted` `invalid_mode` `owner_conflict` `safety_not_ready` |
| 동작 | `motion_active` `recording_active` |
| 정지 | `stop_barrier_pending` `stop_barrier_timeout` `stop_in_progress` |
| 기록 시작 (FR-18) | `not_mimic_mode` `start_failed` `already_recording` `result_pending` |
| 기록 종료 (FR-18) | `not_recording` `session_mismatch` `stop_failed` |
| lease (FR-35) | `owner_lease_expired` |

> 웹의 문구표(`REASON_MESSAGES`)에 없는 `reason`은 "요청이 거부되었습니다.
> (원문)" 형태로 표시됩니다. 동작에는 문제가 없지만 사용자에게 영문 원문이
> 노출됩니다. `web_preempted_by_stop`·`web_superseded`·`invalid_session_id`는
> 아직 문구표에 없으므로 추가가 필요합니다.

### 1.7 브리지가 하지 않는 것

- **lease를 대신 갱신하지 않습니다.** 갱신은 owner인 웹이 1000ms마다 보냅니다.
  브리지가 대행하면 브라우저를 닫아도 lease가 유지되어 NFR-15의 "새로고침·종료
  ·연결 단절 시 lease가 만료되어 안전 전이한다"를 깨뜨립니다.
- **재연결 시 이전 요청을 재생하지 않습니다.** 대기열은 연결마다 새로 만듭니다.
- **모터에 직접 접근하지 않습니다.** 모든 일반 명령은 `command_manager`와
  `command_guard`를 거칩니다.
- **STOP·Safety Reset을 command 토픽의 가짜 명령으로 넣지 않습니다** (FR-32).
- **rosbag2·DB를 조회하지 않습니다** (FR-26). 완료된 공개 세션은 EC2 포털의
  GET API에서 봅니다.
- **커스텀 ROS 메시지·서비스·액션·토픽을 만들지 않습니다.** `thing_interfaces`는
  메시지 7종·서비스 5종·액션 1종 그대로입니다 (FR-30). Safety Reset도 표준
  `std_srvs/Trigger`를 씁니다.
- **FR-35의 타이밍 숫자를 snapshot에 싣지 않습니다.** `hold_recovery_activity_ms`
  같은 값은 Raspberry Pi YAML 소관(FR-41)이고 Jetson 브리지는 그 파일을 읽을 수
  없습니다. 웹이 명세 조문 값을 계속 사용하십시오.
- **`ControlState.sequence_running`을 브리지가 정하지 않습니다.** 원문을 그대로
  전달만 합니다. 범위는 `command_manager` 소관입니다.

---

## 2. 확정된 사항

`.msg`/`.srv`와 명세서로 결정되어 협의가 끝난 항목입니다.

| | |
| --- | --- |
| top-level 고정 필드 | **여덟 개.** 이름·순서 고정 (1.1) |
| uint8 상수값 | `.msg` 선언 순서와 일치. 1.3 표 |
| `control_state`·`recording` | **원문 그대로.** enum은 정수, 파생 필드 없음 (1.2) |
| 원문 절 enum 변환 주체 | **웹** (`toSymbol`·`*_BY_ORDINAL`) |
| 파생 표시 객체 | `landmarks`·`motor_state`·`safety_state` 셋만 (1.2) |
| top-level `mode`·`recording_state` | 원문에서 파생한 symbol mirror. 항상 일치 |
| 시각 필드 위치·타입 | `stamp` / `header.stamp`, `{sec, nanosec}` 원문 (1.3.3) |
| `HandCommand` 7축 | 최상위 고정 필드 (1.3.2) |
| `session_id` | JSON 10진 문자열, **세션 없음은 `"0"`** (1.4) |
| snapshot 발행 주기 | **200ms(5Hz) 주기 발행.** 값이 안 바뀌어도 계속 발행 |
| 서비스 ack 왕복 상한 | **2000ms.** 초과 시 `service_timeout` ACK가 반드시 감 |
| 동시 접속 | **브리지가 1개만 허용.** 두 번째는 close `1013` |
| 파생 필드 주체 | **브리지.** `connection_status`·hand-loss (1.2, 1.3.1) |
| FR-35 타이밍 값 | 브리지가 전달하지 않음. 웹이 명세 조문 값 사용 (1.7) |
| `sequence_running` 범위 | `command_manager` 소관. 브리지는 원문 전달만 |
| 정상 STOP 뒤 재획득 거부 | `stop_in_progress`. 웹은 순서 추적 없이 현행 유지 |
| `SafetyState.RESET=7` | 명시적 정상 STOP 뒤 torque OFF를 재확인하는 상태 |
| `ExecuteGesture.srv` | `gesture_name`, `speed_limit` |
| `ExecuteSequence.action` | `sequence_name`, `speed_limit` |
| `StartRecording.srv` | `label` |
| `StopRecording.srv` | `session_id` |
| `SetMimicResult.srv` | `session_id`, `result` |

경위와 근거는 [pending-decisions.md](pending-decisions.md)의 회신 항목에
있습니다.

---

## 3. 웹이 하지 않는 것

- 임의 ROS topic·motor ID 전송 (NFR-20)
- 7논리축 값 전송 — `ExecuteGesture.srv`에 해당 필드가 없고 목표값은 YAML
  소관 (FR-41)
- rosbag2·EC2 SQLite 접근 (FR-26)
- 안전 판정 — 범위·속도·timeout·E-Stop은 Raspberry Pi 담당 (NFR-16)
- 제어권 자동 획득·재획득 — 재연결·복구 후에도 사용자가 직접 (NFR-15, NFR-23)

---

## 4. 검증

계약 예시는 [sample-snapshot.jsonc](sample-snapshot.jsonc)입니다.

### 로봇 없이 웹만 확인

```bash
cd web/frontend
npm run mock                  # ws://localhost:8000/ws/robot-state
npm run mock -- --no-derived  # 선택 필드를 빼고 웹의 파생 경로 확인
```

mock은 개발용 픽스처이지 계약의 근거가 아닙니다. 계약은 이 문서, 경계 검증은
`bridgeContract.test.jsx`입니다.

### 브리지만 확인

```bash
cd thing_ws
colcon test --packages-select thing_web_bridge
colcon test-result --verbose
```

계약 관련 검증은 다음이 자동으로 돕니다.

- `test_interface_contract.py` — 1.3 symbol 표를 실제 `.msg` 상수와 대조
- `test_protocol.py` — 요청 검증과 거부 `reason` 구분 (1.5, 1.6)
- `test_websocket_server.py` — STOP 선점, 단일 연결, 재연결 무재실행

기동 방법은 [thing_web_bridge README](../../thing_ws/src/thing_web_bridge/README.md)를
보십시오.

### 통합 중 문제가 생기면

브라우저 콘솔을 보십시오. 조용히 실패하는 경로를 전부 없앴습니다.

```
▼ [진단:브릿지] snapshot 에 control_state 가 없습니다 (3회째)
   증상   owner 를 알 수 없어 제어권을 인정하지 않습니다(fail-closed).
   조치   ControlState.msg 원문을 control_state 키로 실어 보내세요.
   근거   FR-19 / interfaces-bridge.md 1.2
```

앞머리에 담당이 붙습니다. 콘솔에서 `__diag()`를 치면 지금까지 잡힌 문제 전체가
나옵니다.
