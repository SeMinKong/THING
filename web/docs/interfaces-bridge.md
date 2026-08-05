# 내부 제어 웹 ↔ Web Bridge 계약

- 대상: `thing_ws/src/thing_web_bridge/web_bridge_node` ↔ `web/frontend`
- 상위 기준: 요구사항 명세서 6.4절, `docs/interfaces.md`의 **WebSocket** 절
- 이 문서는 브라우저 쪽에서 보이는 모양만 다룹니다. ROS 2 계약의 단일 기준은
  `docs/interfaces.md`입니다.

프론트엔드 진단 메시지의 `ref` 필드가 아래 절 번호를 가리킵니다.

| 절 | 내용 |
| --- | --- |
| 1.1 | snapshot 고정 6필드 |
| 1.2 | snapshot 확장 필드 |
| 1.3 | enum 표현 |
| 1.3.1 | `connection_status` |
| 1.3.2 | `last_hand_command` 7축 |
| 1.4 | Session ID 표현 |
| 1.5 | 요청 전송 |
| 1.6 | ACK |
| 1.7 | 브리지가 하지 않는 것 |

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

새 snapshot은 같은 연결의 이전 snapshot을 대체합니다. 누적하지 않습니다.

동시 연결을 하나로 제한한 이유는 탭을 두 개 열면 한쪽에서 누른 STOP이 다른 쪽
제어권까지 해제하기 때문입니다. owner는 하나뿐이고 웹에서는 구분할 수 없습니다.

### 1.1 snapshot 고정 6필드

6.4절 계약입니다. 이름·순서를 바꾸지 않습니다.

```json
{
  "timestamp": "2026-08-05T12:00:00.000Z",
  "mode": "MIMIC",
  "recording_state": "RECORDING",
  "landmarks": {},
  "motor_state": {},
  "safety_state": {}
}
```

- `timestamp` — 브리지가 snapshot을 만든 시각. RFC 3339 UTC `Z`
- `mode` — `DISABLED` / `MIMIC` / `MANUAL` / `TELEOP`
- `recording_state` — `IDLE` / `STARTING` / `RECORDING` / `STOPPING` /
  `COMPLETED` / `FAILED` / `INTERRUPTED`
- 나머지 세 객체 — 아직 유효 데이터를 받지 못하면 `null`이 아니라 `{}`

### 1.2 snapshot 확장 필드

FR-21·FR-24·FR-25 표시를 위해 브리지가 얹습니다. 6.4절이 "세 객체의 세부
field는 개발 중 확정"하도록 허용한 범위입니다.

| 필드 | 내용 |
| --- | --- |
| `control_state` | `ControlState.msg` 원문. `active_mode`·`active_owner`는 symbol |
| `recording` | `RecordingState.msg` 원문. Session ID는 문자열 |
| `last_hand_command` | 최종 `/thing/command` 원문. 7논리축 표시용 |
| `connection_status` | 브리지 파생. 1.3.1 |

`motor_state`·`safety_state`·`control_state`·`recording`·`last_hand_command`
·`landmarks`에는 공통으로 두 필드가 붙습니다.

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

### 1.3 enum 표현

브리지는 `.msg`의 정수 enum을 symbol 문자열로 바꿔서 보냅니다. `mode`가 `1`이
아니라 `"MIMIC"`입니다. 정수가 오면 브리지 버그입니다.

읽기 실패로 `NaN`·`Infinity`가 된 실수는 `null`로 보냅니다. 모터 통신 실패 시
`NaN`은 정상 시나리오이며 명세 6.5절도 "읽기 실패 숫자는 JSON `null`"로 정합니다.

#### 1.3.1 `connection_status`

FR-24. 값은 `"up"` / `"down"` / `"unknown"` 세 가지입니다.

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

`HandCommand.msg` 필드를 **평평하게** 보냅니다. `values` 같은 래퍼가 없습니다.

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

### 1.4 Session ID

`uint64`를 JSON 숫자로 보내면 JavaScript가 정밀도를 잃습니다. 명세 6.5절에
따라 **10진 문자열**로 보냅니다. 값이 `0`이면 빈 문자열 `""`입니다.

```json
"recording": { "active_session_id": "8531234567890123456", "last_session_id": "" }
```

요청에서도 문자열로 보내야 합니다. 브리지는 `0`과 63-bit 초과를 거부합니다.

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

| type | payload |
| --- | --- |
| `set_control_mode` | `requested_mode`: `MIMIC`\|`MANUAL`, `requested_owner`: `WEB` |
| `stop` | `requested_mode`: `DISABLED`, `requested_owner`: `NONE` |
| `execute_gesture` | `gesture_name`: `open`\|`fist`\|`pinch`\|`cylindrical_grasp`, `speed_limit` |
| `execute_sequence` | `sequence_name`: `countdown`\|`scissors_rock_paper`, `speed_limit` |
| `start_recording` | `label` (문자열, 빈 문자열 허용) |
| `stop_recording` | `session_id` (문자열) |
| `set_mimic_result` | `session_id`, `result`: `SUCCESS`\|`FAILURE` |
| `reset_safety` | `{}` |

- `DISABLED`는 `set_control_mode`가 아니라 `stop` type으로 보냅니다.
  MIMIC↔MANUAL 직접 전환은 브리지도 막습니다 (FR-19).
- `speed_limit`은 `0.0 < value <= 1.0`. `0`과 `NaN`은 거부됩니다 (FR-06).
- lease 갱신은 **웹이** 같은 mode·owner `set_control_mode`를 1000ms마다 보냅니다
  (FR-34). 브리지가 대신 보내지 않습니다. 1.7 참고.

**STOP 선점** — FR-19·FR-31에 따라 브리지는 수신과 실행을 분리합니다.

- 일반 요청은 도착 순서대로 하나씩 처리
- `stop`·`reset_safety`는 대기열을 건너뛰고 즉시 처리
- `stop`은 아직 시작하지 않은 일반 요청을 폐기하고 각각에 ACK를 돌려줌
- 대기 중 같은 mode·owner 갱신은 최신 하나만 남김

따라서 **ACK 순서가 요청 순서와 다를 수 있습니다.** 반드시 `request_id`로
찾으십시오. 순서를 가정하면 STOP ACK를 다른 요청의 응답으로 오인합니다.

### 1.6 ACK

모든 요청은 폐기되더라도 **정확히 한 번** ACK를 받습니다.

```json
{
  "type": "ack",
  "request_id": "web-abc-1",
  "accepted": true,
  "reason": "accepted",
  "timestamp": "2026-08-05T12:00:00.100Z"
}
```

`request_id`는 요청의 값을 그대로 돌려줍니다. `accepted`는 항상 boolean입니다.
type에 따라 필드가 더 붙습니다.

| type | 추가 필드 |
| --- | --- |
| `set_control_mode`, `stop` | `active_mode`, `active_owner` |
| `start_recording` | `session_id`, `bag_path` |
| `stop_recording` | `stopped_session_id`, `bag_path` |

`reason`은 두 계열입니다. **접두어로 출처가 구분됩니다.**

`web_*` — 브리지가 ROS에 보내기 전에 스스로 내린 판단입니다.

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

ROS 호출 실패입니다.

| reason | 의미 |
| --- | --- |
| `service_unavailable` | 서비스 서버가 아직 없음. Raspberry Pi 미기동 등 |
| `service_timeout` | 2000ms 안에 응답 없음 |
| `service_failed` | 호출이 예외로 끝남 |
| `service_rejected` | 서버가 거부했지만 `reason`이 비어 있음 |
| `action_unavailable` / `action_timeout` / `action_failed` | Sequence 액션 |
| `reset_rejected` | Safety Reset 거부인데 `message`가 비어 있음 |

그 밖의 값은 **ROS 응답 원문을 그대로 전달한 것**입니다. 브리지는 변환하지
않습니다. FR-37의 8종(`accepted`, `invalid_mode`, `owner_conflict`,
`safety_not_ready`, `recording_active`, `motion_active`,
`stop_barrier_pending`, `stop_barrier_timeout`), FR-18의 기록 거부 사유
(`not_mimic_mode`, `start_failed`, `already_recording`, `result_pending`,
`not_recording`, `session_mismatch`, `stop_failed`), FR-35의
`owner_lease_expired`가 여기 해당합니다.

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
  메시지 7종·서비스 5종·액션 1종 그대로입니다 (FR-30).
- **FR-35의 타이밍 숫자를 snapshot에 싣지 않습니다.** `hold_recovery_activity_ms`
  같은 값은 Raspberry Pi YAML 소관(FR-41)이고 Jetson 브리지는 그 파일을 읽을 수
  없습니다. 웹이 명세 조문 값을 계속 사용하십시오.
