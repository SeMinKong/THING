# ROS 2 및 장치 인터페이스

분야 간 인터페이스 변경은 이 문서를 먼저 갱신하고 관련 담당자의 리뷰를 받습니다.
메시지의 필드 정의는 `thing_ws/src/thing_interfaces`를 단일 기준으로 사용합니다.

## 7논리축–모터 매핑

| 논리축 | 범위 | 물리 모터 | ID/포트 | 방향 | 위치 범위 |
| --- | --- | --- | --- | --- | --- |
| `thumb_flex` | 0.0–1.0 | XL-330 | YAML 확정 | YAML 확정 | YAML 확정 |
| `thumb_opp` | 0.0–1.0 | XL-330 | YAML 확정 | YAML 확정 | YAML 확정 |
| `thumb_abd` | 0.0–1.0 | XL-330 | YAML 확정 | YAML 확정 | YAML 확정 |
| `index_flex` | 0.0–1.0 | XL-330 | YAML 확정 | YAML 확정 | YAML 확정 |
| `middle_flex` | 0.0–1.0 | XL-330 | YAML 확정 | YAML 확정 | YAML 확정 |
| `ring_flex` | 0.0–1.0 | XL-330 | YAML 확정 | YAML 확정 | YAML 확정 |
| `little_flex` | 0.0–1.0 | XL-330 | YAML 확정 | YAML 확정 | YAML 확정 |

실제 ID, U2D2 포트, 방향과 raw 위치 범위는
`thing_bringup/config/motors.yaml`에서 관리합니다.

## 토픽

| 토픽 | 타입 | 발행자 | 구독자 | 권장 주기/QoS |
| --- | --- | --- | --- | --- |
| `/thing/landmarks` | `HandLandmarks` | MediaPipe | target, web, logger | 센서 데이터, best effort |
| `/thing/command/mimic` | `HandCommand` | vision | manager, logger | 20Hz 이상 |
| `/thing/command/teleop` | `HandCommand` | teleop | manager | 사용자 입력 시 |
| `/thing/command/manual` | `HandCommand` | gesture/sequence | manager | 동작 실행 시 |
| `/thing/command/selected` | `HandCommand` | manager | guard | 20Hz 이상 |
| `/thing/command` | `HandCommand` | guard | hardware, logger | reliable, depth 1 |
| `/thing/motor_status` | `MotorStatus` | hardware | safety, web, logger | 제어 주기와 분리 |
| `/thing/control_state` | `ControlState` | manager | web, logger | 상태 변화+주기 |
| `/thing/safety_state` | `SafetyState` | safety | manager, guard, web, logger | reliable, transient local |
| `/thing/recording_state` | `RecordingState` | logger | web | reliable, transient local |
| `/thing/control/stop_requested` | `std_msgs/msg/Empty` | manager | safety, gesture/sequence, logger | reliable, depth 10, 명시적 STOP마다 1건 |
| `/thing/control/motion_active` | `std_msgs/msg/Bool` | gesture/sequence | manager | reliable, depth 10, 실행 시작·종료 시 |
| `/thing/diagnostics` | `diagnostic_msgs/DiagnosticArray` | 각 장치 | web/운영자 | 1Hz 이상 |

정확한 QoS와 주기는 하드웨어 측정 후 YAML로 조정하되 명령 stale 판정에 필요한
timestamp를 변경해서는 안 됩니다.

## 제어 mode·owner와 STOP 계약

활성 제어권은 아래 세 조합만 허용합니다.

- `MODE_MIMIC` + `OWNER_WEB`
- `MODE_MANUAL` + `OWNER_WEB`
- `MODE_TELEOP` + `OWNER_LOCAL`

`MODE_DISABLED` + `OWNER_NONE`은 명시적 STOP으로 사용합니다. 그 밖의 mode·owner
조합은 `invalid_mode`로 거부합니다. STOP이 수락되면 command manager는 mode와 owner,
실행 중 일반 동작 상태를 원자적으로 해제하고 500ms 동안 새 제어권 요청을
`stop_in_progress`로 거부합니다.

명령 source 승인과 `/thing/command/selected` 발행은 command manager의 같은
transaction 안에서 처리합니다. 따라서 STOP 처리가 완료된 뒤 STOP 이전에 승인된
일반 명령이 새로 발행될 수 없습니다.

HOLD 진입만으로는 mode와 owner를 해제하지 않습니다. HOLD에서
`SetControlMode(MODE_DISABLED, OWNER_NONE)`이 수락되면 command manager가
`/thing/control/stop_requested`를 발행합니다. safety manager는 command timeout 기반
SAFE 상승을 취소하고 HOLD에서 `stop_settle_ms=500` 동안 안정화한 뒤, 새
FAULT·ESTOP이 없을 때 토크 OFF 상태인 READY로 전환합니다. SAFE·FAULT·ESTOP에서
STOP은 mode·owner만 해제하며 안전 상태를 변경하지 않습니다. 실제 모터 토크
override는 safety manager와 hardware node의 내부 경로가 담당합니다.

Gesture 또는 Sequence 실행기는 `/thing/control/motion_active`에 실행 시작 시 `true`,
정상 종료·취소·STOP 시 `false`를 발행합니다. mode service의 거부 사유는 다음처럼
구분합니다.

- `invalid_mode`: mode enum, owner enum 또는 mode·owner 조합 자체가 허용되지 않습니다.
- `motion_active`: 요청 조합은 유효하지만 현재 Gesture 또는 Sequence가 실행 중이므로
  mode 변경을 지금 수행할 수 없습니다. 현재 mode·owner의 lease 갱신은 허용합니다.
- `stop_in_progress`: 명시적 STOP 수락 후 500ms 재획득 차단 구간입니다.

## 서비스와 액션

| 이름 | 타입 | 제약 |
| --- | --- | --- |
| `/thing/set_control_mode` | `SetControlMode` | 녹화 중 또는 비안전 자세에서 거부 |
| `/thing/execute_gesture` | `ExecuteGesture` | MANUAL에서만 허용 |
| `/thing/start_recording` | `StartRecording` | MIMIC이며 판정 대기가 없어야 함 |
| `/thing/stop_recording` | `StopRecording` | RECORDING일 때만 허용 |
| `/thing/set_mimic_result` | `SetMimicResult` | 최근 완료 세션에 1회 판정 |
| `/thing/execute_sequence` | `ExecuteSequence` | MANUAL, 취소 가능, STOP이 선점 |

## 명령 검증

- `NaN`, 무한대, 오래된 timestamp와 역행 sequence를 거부합니다.
- 7논리축은 `0.0–1.0`이고 물리 위치 제한은 Raspberry Pi에서 다시 적용합니다.
- 일반 동작 실행 중 새 일반 동작은 큐잉하지 않고 거부합니다.
- STOP과 안전 명령은 일반 명령보다 높은 우선순위로 선점합니다.
- 통신 timeout 기본값은 300ms로 시작하고 200–500ms 범위에서 시험합니다.

## WebSocket

WebSocket은 JSON을 사용하되 ROS 메시지 필드명을 가능한 그대로 유지합니다.
명령 요청에는 `request_id`, `type`, `timestamp`, `payload`를 포함하고 응답에는
동일한 `request_id`, `accepted`, `reason`을 포함합니다.
