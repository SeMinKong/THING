# ROS 2 및 장치 인터페이스

분야 간 인터페이스 변경은 이 문서를 먼저 갱신하고 관련 담당자의 리뷰를 받습니다.
메시지의 필드 정의는 `thing_ws/src/thing_interfaces`를 단일 기준으로 사용합니다.
Safety Manager의 8상태 전이, RESET 완료 조건과 실행법은
[`docs/safety_manager.md`](safety_manager.md)에 정리합니다.

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
| `/thing/command/validation_result` | `std_msgs/msg/Bool` | guard | safety | 단일 ordered 채널. `true`=HOLD 복구 activity, `false`=window 초기화; motor 전달 금지 |
| `/thing/motor_status` | `MotorStatus` | hardware | safety, web, logger | reliable+volatile, depth 5, 300ms freshness 미만, `header.stamp` 필수 |
| `/thing/estop` | `std_msgs/msg/Bool` | hardware GPIO adapter | safety | reliable+volatile, 300ms freshness 미만, `true`가 E-Stop 활성 |
| `/thing/control_state` | `ControlState` | manager | web, logger | 상태 변화+주기 |
| `/thing/safety_state` | `SafetyState` | safety | manager, guard, web, logger | reliable, transient local |
| `/thing/recording_state` | `RecordingState` | logger | web | reliable, transient local |
| `/thing/control/stop_requested` | `std_msgs/msg/Empty` | manager | guard, gesture/sequence, logger | reliable, depth 10, 명시적 STOP마다 1건 |
| `/thing/control/stop_barrier_ack` | `std_msgs/msg/Empty` | guard | safety | reliable+volatile, Guard latch 닫힌 뒤 1건 |
| `/thing/control/motion_active` | `std_msgs/msg/Bool` | gesture/sequence | manager | reliable, depth 10, 실행 시작·종료 시 |
| `/thing/diagnostics` | `diagnostic_msgs/DiagnosticArray` | 각 장치 | web/운영자 | 1Hz 이상 |

정확한 QoS와 주기는 하드웨어 측정 후 YAML로 조정하되 명령 stale 판정에 필요한
timestamp를 변경해서는 안 됩니다.

### MotorStatus 필드 계약

`/thing/motor_status`의 `MotorStatus.motors`에는 모터 ID 1–7의 `MotorState`를
ID 오름차순으로 정확히 7개 포함합니다. `MotorState.torque_enabled`는 실제 모터의
토크 활성 상태를 나타내는 `bool` 필드이며 `true`는 토크 ON, `false`는 토크 OFF를
뜻합니다. `communication_ok`이 `false`이면 `torque_enabled` 값을 유효한 상태로
판단하지 않고 통신 실패를 우선 처리합니다.

## 제어 Bringup

장치에서는 SROS2 deny-by-default 정책을 적용한 뒤 안전 상태, 명령 중재, 최종 검증 체인을
아래 launch로 함께 시작합니다. `control.launch.py`는 security가 꺼져 있거나 세 control
enclave artifact 중 하나라도 없으면 node를 하나도 시작하지 않습니다.

```bash
source /opt/ros/humble/setup.bash
source install/setup.bash

export ROS_DOMAIN_ID=<deployment-domain-id>
export THING_KEYSTORE=/etc/thing/sros2_keystore
ros2 security create_keystore "$THING_KEYSTORE"
ros2 security generate_artifacts \
  -k "$THING_KEYSTORE" \
  -p "$(ros2 pkg prefix --share thing_control)/security/thing_control.policy.xml"

export ROS_SECURITY_KEYSTORE="$THING_KEYSTORE"
export ROS_SECURITY_ENABLE=true
export ROS_SECURITY_STRATEGY=Enforce
ros2 launch thing_bringup control.launch.py
```

생성된 keystore의 private key와 certificate는 deployment artifact이며 Git에 넣지 않습니다.
현재 policy는 구현된 세 control node만 허용합니다. hardware와 command producer node가 구현되면
각 node의 고정 enclave와 필요한 topic만 별도 review로 추가한 뒤 artifact를 재생성해야 하며,
wildcard publish 권한이나 다른 enclave의 `/thing/command`,
`/thing/command/validation_result`,
`/thing/control/stop_barrier_ack` publish 권한은 금지합니다.

이 launch는 같은 version-controlled `control.yaml`을 사용해 `safety_manager`,
`command_manager`, `command_guard`를 시작합니다. 시작 시 safety manager는 INIT을
발행하고, guard는 `DISABLED/NONE → active` 획득 경계를 새로 관측하기 전까지
`/thing/command`를 발행하지 않습니다. 따라서 재시작으로 이전 명령을 자동 재생하지
않습니다.

## SafetyState 8상태

wire 값은 `INIT=0`, `READY=1`, `RUN=2`, `HOLD=3`, `SAFE=4`, `FAULT=5`,
`ESTOP=6`, `RESET=7`입니다. 기존 값은 유지하고 RESET만 끝에 추가합니다.
`SafetyState.stamp`는 상태 전이 source timestamp이며 같은 transition의 heartbeat에서는
고정됩니다. Command Manager와 Guard는 positive·strictly ordered stamp만 반영하고,
Safety Manager는 RESET 진입 시각을 내부에 보존합니다. hardware는
`MotorStatus.header.stamp`를 실제 측정 시각으로 채워야
합니다. Safety Manager는 수신 시각과 측정 시각이 모두 현재 RESET 진입 이후인 status만
완료 근거로 인정하므로 별도 `state_generation` 필드를 사용하지 않습니다.

`/thing/reset_safety`는 기존 `std_srvs/srv/Trigger`를 유지하며 SAFE·FAULT는 원인 해소
상태가 1000ms 연속 유지되고 ESTOP은 해제 입력이 500ms 연속 유지된 경우에만
`INIT → 전체 재검사 → READY`를 수행합니다. 정상 제어권 변경용 RESET
상태와 이 안전 복구 service는 서로 다른 경로입니다. 전류·온도 trip 값의 실물 부하 시험이
완료되지 않아 `trip_limits_validated=false`이면 INIT은 `trip_limits_unvalidated`로
fail-closed하고 READY로 전환하지 않습니다.

## 제어 mode·owner와 STOP 계약

활성 제어권은 아래 세 조합만 허용합니다.

- `MODE_MIMIC` + `OWNER_WEB`
- `MODE_MANUAL` + `OWNER_WEB`
- `MODE_TELEOP` + `OWNER_LOCAL`

`MODE_DISABLED` + `OWNER_NONE`은 명시적 STOP으로 사용합니다. 그 밖의 mode·owner
조합은 `invalid_mode`로 거부합니다. STOP이 수락되면 command manager는 mode와 owner,
실행 중 일반 동작 상태를 원자적으로 해제하고 500ms 동안 새 제어권 요청을
`stop_in_progress`로 거부합니다. 정상 상태의 STOP은 500ms가 지나도 STOP 이후 새
Manager의 STOP system timestamp보다 source stamp가 새로운 `RESET → READY` 전이(중간
fault가 선점하면 `INIT → READY` 복구 epoch)를 관측하기 전에는 cached·queued 상태로
재획득할 수 없습니다. 이미 SAFE·FAULT·ESTOP인 상태의 STOP은 아래 전용
Safety Reset 경로를 막지 않습니다.

명령 source 승인과 `/thing/command/selected` 발행은 command manager의 같은
transaction 안에서 처리합니다. 따라서 STOP 처리가 완료된 뒤 STOP 이전에 승인된
일반 명령이 새로 발행될 수 없습니다. Command Guard가 STOP event를 직접 구독해
`stop_latched`로 검증을 닫은 뒤 `/thing/control/stop_barrier_ack`을 발행합니다. Safety
Manager는 raw STOP이 아니라 이 causal ACK을 받은 뒤 RESET으로 전이하므로, 서로 다른 DDS
topic의 callback 순서와 무관하게 RESET 이후 늦게 도착한 selected command는 차단됩니다.
STOP 이후 `DISABLED/NONE → active` 경계를 새로 관측하기 전까지 Guard는 닫혀 있습니다.

HOLD는 사용자가 요청하는 일시정지(Pause)가 아닙니다. RUN 중 마지막 유효
`HandCommand`가 300ms 동안 들어오지 않을 때 안전 관리 경로가 진입하는 command-timeout
watchdog 상태입니다. timeout 시점에 hardware가 실제로 보간 중이던 현재 setpoint를
고정하고 제한 토크로 자세를 유지하며, 마지막 목표까지 계속 이동하거나 중단된 동작을
나중에 자동 재개하지 않습니다. 마지막 유효 명령 기준 총 1000ms 동안 단절이 계속되면
SAFE 정책으로 상승합니다.

Safety Manager는 SAFE 진입 시 `safe_action_timeout_ms=3000` deadline을 시작합니다.
SAFE 진입 **후** local receipt는 전환 뒤이고 source stamp는 SAFE 전환 system timestamp보다
새로운 정상
`MotorStatus`에서 7개 모터의 `torque_enabled=false`가 모두 확인되면 안전 동작 완료로
간주해 deadline을 닫고,
확인되지 않으면 `FAULT/safe_action_timeout`으로 전환합니다. 실제 YAML 안전 자세 생성,
저속 이동과 torque-off 수행은 thing_hardware 책임이며, SAFE 진입 전 cache와 늦게 도착한
진입 전 측정값은 source stamp가 이전이므로 완료 증거로 재사용하지 않습니다.

HOLD 진입만으로 command manager의 mode와 owner를 해제하지 않습니다. manager는 현재
owner와 일치하는 source만 guard로 전달하고, guard는 stamp·source·sequence·축 범위와
변화율을 계속 검증합니다. 다만 HOLD에서는 검증된 명령을 `/thing/command`로 보내지 않고
단일 ordered `/thing/command/validation_result`에 `data=true`로 발행합니다. 검증 실패는
같은 채널의 `data=false`로 알려 연속 window를 초기화합니다. 서로 다른 DDS topic의
callback 순서에 의존하지 않습니다. activity가 100ms를
넘는 공백 없이 300ms 연속 유지되면 safety manager가 RUN으로 복귀합니다. 간헐 activity는
총 timeout을 연장하지 않으며, 마지막 hardware-forwarded command 기준 1000ms에 SAFE로
전환합니다. HOLD에서는 `/thing/reset_safety`는 거부하지만 명시적 STOP은 허용하며,
후속 상태는 RUN·RESET·SAFE 중 하나입니다.

정상 제어권 변경은 기존 `SetControlMode(MODE_DISABLED, OWNER_NONE)`을 사용합니다.
command manager가 mode·owner와 실행 중 일반 동작을 먼저 해제한 뒤 기존
`/thing/control/stop_requested`를 발행합니다. Command Guard가 STOP latch를 닫고
`/thing/control/stop_barrier_ack`을 발행한 뒤에만 Safety Manager가 READY·RUN·HOLD에서
RESET에 진입합니다. RESET은 최소 500ms 유지되고, RESET 발행 시각 이후에 측정된
`MotorStatus.header.stamp`와 7개 `MotorState.torque_enabled=false`가 모두 확인된 뒤 READY가
됩니다. 3000ms 안에 조건을 만족하지 못하거나 모터 통신·전류·온도 fault가 발생하면
FAULT로 전환합니다. RUN에서 사용자 확인 token이 필요하면 Web/UI가 확인을 완료한 뒤
기존 SetControlMode 서비스를 호출하며, ROS용 신규 reset service를 추가하지 않습니다.

Gesture 또는 Sequence 실행기는 `/thing/control/motion_active`에 실행 시작 시 `true`,
정상 종료·취소·STOP 시 `false`를 발행합니다. mode service의 거부 사유는 다음처럼
구분합니다.

- `invalid_mode`: mode enum, owner enum 또는 mode·owner 조합 자체가 허용되지 않습니다.
- `motion_active`: 요청 조합은 유효하지만 현재 Gesture 또는 Sequence가 실행 중이므로
  mode 변경을 지금 수행할 수 없습니다. 현재 mode·owner의 lease 갱신은 허용합니다.
- `stop_in_progress`: 명시적 STOP 수락 후 500ms 재획득 차단 구간입니다.
- `owner_lease_expired`: service 처리 전 또는 처리 중 lease deadline을 넘었습니다. Manager는
  먼저 `DISABLED/NONE`을 발행하고 현재 요청을 거부하므로, 호출자는 갱신된 SafetyState를
  확인한 뒤 새 제어권 요청으로 재시도해야 합니다.

## 서비스와 액션

| 이름 | 타입 | 제약 |
| --- | --- | --- |
| `/thing/set_control_mode` | `SetControlMode` | 녹화 중 또는 비안전 자세에서 거부 |
| `/thing/reset_safety` | `std_srvs/srv/Trigger` | SAFE·FAULT·ESTOP 원인 해소 후 INIT 재검사 |
| `/thing/execute_gesture` | `ExecuteGesture` | MANUAL에서만 허용 |
| `/thing/start_recording` | `StartRecording` | MIMIC이며 판정 대기가 없어야 함 |
| `/thing/stop_recording` | `StopRecording` | RECORDING일 때만 허용 |
| `/thing/set_mimic_result` | `SetMimicResult` | 최근 완료 세션에 1회 판정 |
| `/thing/execute_sequence` | `ExecuteSequence` | MANUAL, 취소 가능, STOP이 선점 |

## 명령 검증

`command_guard`만 `/thing/command/selected`를 받아 검증된 명령을
`/thing/command`로 발행합니다. 검증과 발행은 같은 node-level transaction에서
수행하므로, guard callback 내부에서는 SafetyState 갱신이 승인과 발행 사이에
끼어들 수 없습니다. 단, 이 lock은 서로 다른 DDS 토픽의 전역 수신 순서를 보장하지
않습니다. 따라서 Command Guard 단독으로 분산 STOP/FAULT race를 닫았다고 간주하지
않으며, 최종 모터 차단은 별도 FR-33 범위의 thing_hardware watchdog·safety override가
반드시 구현하고 검증해야 합니다.

검증 규칙은 다음과 같습니다.

- SafetyState와 ControlState를 한 번도 받지 못했거나 각 상태의 로컬 monotonic 수신
  시간이 YAML timeout을 넘으면 fail-closed로 거부합니다. command manager는 1000ms마다
  상태를 갱신하고 safety manager는 100ms마다 heartbeat를 발행하며, guard의 기본 stale
  기준은 1500ms입니다. heartbeat가 없으면 guard는 timeout 뒤 안전하게 명령을 차단합니다.
- guard가 시작될 때 latched active ControlState만 받으면 이전 활성화를 자동 재개하지
  않습니다. `DISABLED/NONE`을 관측한 뒤 새 active mode·owner 획득을 관측해야 sequence
  기준을 초기화합니다.
- source는 active mode·owner와 일치해야 합니다. MIMIC은 `SOURCE_MIMIC`, MANUAL은
  `SOURCE_GESTURE|SOURCE_SEQUENCE`, TELEOP은 `SOURCE_TELEOP`만 허용하며 예약된
  `SOURCE_SAFETY`를 일반 selected 명령으로 허용하지 않습니다.
- command stamp는 수신 ROS system time보다 300ms 넘게 오래되거나 100ms 넘게
  미래이면 거부합니다. timeout과 상태 freshness, 정규화 축 범위는 ROS parameter로
  이 값보다 느슨하게 확장할 수 없으며, 잘못된 설정은 node 시작 단계에서 거부합니다.
- source별 sequence는 uint32 serial-number 비교를 사용합니다. 동일 값과 역행 값은
  거부하고 `0xffffffff -> 0` wrap은 전진으로 처리합니다. 거부된 명령은 sequence나
  변화율 기준을 갱신하지 않습니다.
- 7개 named axis와 confidence는 유한한 `0.0~1.0`, speed_limit은 유한한
  `0.0 < value <= 1.0`이어야 합니다. 잘못된 값을 조용히 clamp하거나 기본값으로
  바꾸지 않습니다.
- 축별 변화 허용량은 `YAML max_delta_per_second × command speed_limit × monotonic
  elapsed seconds`입니다. `speed_limit`은 7논리축 변화율과 하드웨어 속도 상한에
  곱하는 무차원 `0.0~1.0` 배율이며 DYNAMIXEL raw velocity 값이 아닙니다. 새 활성화의
  첫 명령은 범위 검사만 수행하고 이후 명령부터 마지막으로 수락된 명령과 비교합니다.

거부 시 `/thing/command`를 발행하지 않고 `/thing/diagnostics`의
`thing_control/command_guard` 상태와 throttled warning log에 아래 reason을 남깁니다.
거부 reason은 즉시 `DiagnosticStatus.WARN`으로 발행합니다. 그와 별도로 마지막 판단을
`diagnostic_period_ms` 주기(기본 1000ms, 허용 범위 1~1000ms)로 반복 발행하므로
1Hz보다 느려지도록 설정할 수 없습니다. 마지막 판단이 `accepted`이면
`DiagnosticStatus.OK`, 시작 전 또는 마지막 판단이 거부이면 `DiagnosticStatus.WARN`입니다.

| reason | 의미 |
| --- | --- |
| `safety_state_missing` | SafetyState를 받지 못함 |
| `safety_state_stale` | SafetyState 수신 freshness 초과 |
| `safety_not_ready` | INIT·SAFE·FAULT·ESTOP·RESET; HOLD는 검증 activity만 허용 |
| `control_state_missing` | ControlState를 받지 못함 |
| `control_state_stale` | ControlState 수신 freshness 초과 |
| `control_inactive` | mode·owner·owner_alive 조합이 활성 상태가 아님 |
| `control_activation_not_observed` | DISABLED 이후의 새 활성화 경계를 관측하지 못함 |
| `stop_latched` | STOP 이후 새 DISABLED→active 획득 경계를 아직 관측하지 못함 |
| `source_mode_mismatch` | source가 active mode와 불일치 |
| `command_stale` | command stamp가 300ms보다 오래됨 |
| `command_from_future` | command stamp가 100ms보다 먼 미래임 |
| `axis_set_invalid` | 7개 named axis 구성이 아님 |
| `axis_non_finite` | axis가 NaN 또는 Infinity |
| `axis_out_of_range` | axis가 YAML min·max를 벗어남 |
| `speed_limit_non_finite` | speed_limit이 NaN 또는 Infinity |
| `speed_limit_out_of_range` | speed_limit이 `0.0 < value <= 1.0`가 아님 |
| `confidence_non_finite` | confidence가 NaN 또는 Infinity |
| `confidence_out_of_range` | confidence가 `0.0~1.0`을 벗어남 |
| `sequence_out_of_range` | sequence가 uint32 범위가 아님 |
| `sequence_duplicate` | source별 마지막 수락 sequence와 동일 |
| `sequence_out_of_order` | uint32 serial-number 기준 역행 |
| `axis_rate_exceeded` | 축별 변화율 한계 초과 |
| `monotonic_time_regressed` | 로컬 monotonic 시간이 역행함 |

command_guard는 HandCommand에 없는 전류·온도·모터 통신값을 검사한 것처럼 처리하지
않습니다. 해당 실제값과 YAML trip limit 검사는 thing_hardware와 safety_manager가
MotorStatus를 기준으로 담당합니다. command_guard는 정규화 목표·speed_limit·변화율과
현재 상태 freshness까지만 책임지고 물리 motor ID·raw 위치 매핑과 DYNAMIXEL write는
수행하지 않습니다.

## WebSocket

WebSocket은 JSON을 사용하되 ROS 메시지 필드명을 가능한 그대로 유지합니다.
명령 요청에는 `request_id`, `type`, `timestamp`, `payload`를 포함하고 응답에는
동일한 `request_id`, `accepted`, `reason`을 포함합니다.
