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
| `/thing/command/mimic` | `HandCommand` | vision | manager | 20Hz 이상 |
| `/thing/command/teleop` | `HandCommand` | teleop | manager | 사용자 입력 시 |
| `/thing/command/manual` | `HandCommand` | manual_executor | manager | 20Hz, Gesture·Sequence 단일 publisher |
| `/thing/command/selected` | `HandCommand` | manager | guard | 20Hz 이상 |
| `/thing/command` | `HandCommand` | guard | hardware, logger | reliable, depth 1 |
| `/thing/command/validation_result` | `std_msgs/msg/Bool` | guard | safety | 단일 ordered 채널. `true`=HOLD 복구 activity, `false`=window 초기화; motor 전달 금지 |
| `/thing/motor_status` | `MotorStatus` | hardware | safety, web, logger | reliable+volatile, depth 5, 300ms freshness 미만, `header.stamp` 필수 |
| `/thing/estop` | `std_msgs/msg/Bool` | `estop_gpio_node` | safety | reliable+volatile, 100ms heartbeat, `true`가 E-Stop 활성 |
| `/thing/control_state` | `ControlState` | manager | web, logger | 상태 변화+주기 |
| `/thing/safety_state` | `SafetyState` | safety | manager, guard, web, logger | reliable, transient local |
| `/thing/recording_state` | `RecordingState` | logger | web | reliable, transient local |
| `/thing/control/stop_requested` | `std_msgs/msg/UInt64` | manager | guard, manual executor, logger | reliable, depth 10, `data`는 명시적 STOP마다 증가하는 generation |
| `/thing/control/stop_barrier_ack` | `std_msgs/msg/UInt64` | guard | manager, safety, manual executor | reliable+volatile, `data`는 Guard latch 이후 되돌려 보내는 동일 generation |
| `/thing/control/motion_active` | `std_msgs/msg/Bool` | gesture/sequence | manager | reliable, depth 10, 실행 시작·종료 시 |
| `/thing/diagnostics` | `diagnostic_msgs/DiagnosticArray` | 각 장치 | web/운영자 | 1Hz 이상 |

정확한 QoS와 주기는 하드웨어 측정 후 YAML로 조정하되 명령 stale 판정에 필요한
timestamp를 변경해서는 안 됩니다.

### 물리 E-Stop 입력 계약

물리 E-Stop의 NC 주접점은 ROS 2, Raspberry Pi CPU와 무관하게 모터 구동 전원을
직접 차단합니다. `thing_hardware/estop_gpio_node`는 별도의 절연 보조접점만 읽어
Safety Manager에 상태를 알리는 2차 감시 경로이며, 이 노드의 소프트웨어 발행을 물리
전원 차단 대신 사용하지 않습니다.

GPIO 기본 배선은 주접점과 다른 **NO 보조접점**을 입력과 GND 사이에 연결합니다.
버튼 해제 시 접점이 열려 pull-up으로 HIGH, 버튼을 누르면 접점이 닫혀 LOW입니다.
따라서 기본 `active_low: true`는 LOW를 E-Stop active로 해석합니다. NC 주접점을
GPIO 보조입력에 그대로 연결하면 극성이 반대가 되므로 사용해서는 안 됩니다.

- 기본 배선은 `gpiochip4`, line `17`, pull-up, NO 보조접점, active-low입니다.
- 5ms polling과 50ms debounce를 사용하며 안정된 입력 변화는 즉시 발행합니다.
- 같은 상태도 100ms마다 heartbeat로 다시 발행합니다.
- GPIO open/read 실패 후 재연결 시도 간격은 500ms 이하만 허용합니다.
- 시작 전·GPIO open/read 실패·지원하지 않는 libgpiod API는 `true`로 fail-closed합니다.
- NO 보조접점 배선이 단선되면 pull-up 때문에 HIGH/inactive로 보일 수 있어 이 GPIO
  감시 경로만으로는 검출하지 못합니다. 독립된 NC 주접점의 모터 전원 차단이 최종
  안전 경계입니다.
- 물리 버튼 해제는 reset 요청이 아닙니다. 500ms 안정, `/thing/reset_safety`, INIT의
  새로운 E-Stop·MotorStatus 검사를 모두 통과해야 READY로 돌아갑니다.

### MotorStatus 필드 계약

`/thing/motor_status`의 `MotorStatus.motors`에는 모터 ID 1–7의 `MotorState`를
ID 오름차순으로 정확히 7개 포함합니다. `MotorState.torque_enabled`는 실제 모터의
토크 활성 상태를 나타내는 `bool` 필드이며 `true`는 토크 ON, `false`는 토크 OFF를
뜻합니다. `communication_ok`이 `false`이면 `torque_enabled` 값을 유효한 상태로
판단하지 않고 통신 실패를 우선 처리합니다.

## 제어 Bringup

같은 `ROS_DOMAIN_ID`에서 안전 상태, 명령 중재, 수동 실행, 최종 검증 체인을 아래
launch로 함께 시작합니다. 별도 keystore나 SROS2 artifact는 필요하지 않습니다.

```bash
source /opt/ros/humble/setup.bash
source install/setup.bash
export ROS_DOMAIN_ID=<deployment-domain-id>
ros2 launch thing_bringup control.launch.py
```

이 launch는 같은 version-controlled `control.yaml`을 사용해 `estop_gpio_node`,
`safety_manager`, `command_manager`, `manual_executor`, `command_guard`를 시작합니다.
시작 시 E-Stop 입력은 GPIO가 stable inactive로 확인되기 전까지 active heartbeat를
발행하고, safety manager는 INIT을 발행하며, guard는 `DISABLED/NONE → active` 획득
경계를 새로 관측하기 전까지
`/thing/command`를 발행하지 않습니다. 따라서 재시작으로 이전 명령을 자동 재생하지
않습니다.

현재 배포는 SROS2 접근 제어를 사용하지 않으므로 같은 DDS domain의 다른 node가 발행한
메시지를 신원 기반으로 차단하지 않습니다. 팀이 관리하는 신뢰된 네트워크와 통일된
`ROS_DOMAIN_ID`에서 실행하고, 외부 네트워크에는 DDS discovery를 노출하지 않습니다.

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
`HandCommand`가 5000ms 동안 들어오지 않을 때 안전 관리 경로가 진입하는 command-timeout
watchdog 상태입니다. timeout 시점에 hardware가 실제로 보간 중이던 현재 setpoint를
고정하고 제한 토크로 자세를 유지하며, 마지막 목표까지 계속 이동하거나 중단된 동작을
나중에 자동 재개하지 않습니다. 마지막 유효 명령 기준 총 10000ms 동안 단절이 계속되면
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
총 timeout을 연장하지 않으며, 마지막 hardware-forwarded command 기준 10000ms에 SAFE로
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

### Manual Executor 단일 실행 계약

`manual_executor` 하나가 `/thing/execute_gesture` Service와
`/thing/execute_sequence` Action을 함께 소유하고 `/thing/command/manual`의 유일한
publisher가 됩니다. 두 요청 형식은 다르지만 실행 슬롯은 하나이므로 Gesture 실행 중
Sequence Goal, Sequence 실행 중 Gesture 요청, 두 Sequence Goal의 동시 실행은
`motion_active`로 거부하며 큐에 쌓지 않습니다.

- 수락 조건은 fresh `ControlState=MANUAL/WEB/owner_alive`, fresh
  `SafetyState=READY|RUN`, 유한한 `0.0 < speed_limit <= 1.0`, idle 실행 슬롯입니다.
- Gesture canonical 이름은 `open`, `fist`, `pinch`, `cylindrical_grasp`이고
  `home|paper→open`, `rock→fist` alias를 지원합니다. 서비스 성공은 완료가 아니라 실행
  수락을 뜻합니다.
- Gesture는 YAML 유지시간 동안, Sequence는 YAML step별 유지시간 동안 최대 50ms
  주기로 fresh system stamp와 증가 uint32 sequence를 가진 `HandCommand`를 발행합니다.
  정상 완료 뒤에는 마지막 자세를 같은 주기로 계속 발행하며, 다음 Gesture/Sequence가
  수락되면 새 pose로 교체합니다. Executor timer가 지연돼도 Sequence의 중간 자세를
  건너뛰지 않고, 다음 자세를 처음 발행한 시각부터 해당 유지시간을 새로 계산합니다.
- 시작·종료 때 `/thing/control/motion_active`를 각각 `true`·`false`로 발행합니다. 마지막
  자세 heartbeat 중에는 `motion_active=false`이므로 다음 Gesture/Sequence admission은
  열려 있습니다. `is_sequence_running`은 node 내부 단일 실행 슬롯의 Sequence 점유
  상태이며 retained pose heartbeat는 포함하지 않습니다.
- Action은 `current_step`, `total_steps`, `active_gesture` feedback을 보내고 완료 시
  `success=true, reason=completed`를 반환합니다. Action cancel은 `cancel_requested`로
  종료합니다.
- `/thing/control/stop_requested`, owner/mode 상실, HOLD·SAFE·FAULT·ESTOP,
  ControlState·SafetyState heartbeat timeout은 실행과 retained pose를 즉시 취소하며
  이후 명령을 발행하지 않습니다. 기본 freshness는 ControlState 1500ms, SafetyState 300ms이고
  YAML에서 더 느슨하게 확장할 수 없습니다. ControlState와 SafetyState는 positive source
  stamp와 단조 순서를 검증하므로 zero, 수신 system time보다 100ms 넘게 미래인
  stamp, older/conflicting replay는 freshness를 갱신하거나 admission을 다시 열 수
  없습니다. 100ms 이내 clock skew는 허용합니다.
- STOP 수신 즉시 local admission latch를 닫습니다. 기존 표준 `UInt64.data` generation으로
  raw STOP과 Guard ACK를 상관시키고, 동일 ACK 이후 로컬 callback 관측 순서가
  `DISABLED/NONE`,
  `RESET|INIT → READY`, 새 `MANUAL/WEB/owner_alive` 획득까지 완성된 경우에만
  latch를 엽니다. ACK·RESET·상태가 raw STOP보다 먼저 전달되는 cross-topic reorder도
  동일 transaction으로 귀속하며, generation별 최초 ACK observation만 복구 경계로
  고정해 중복 ACK replay가 이미 관측한 복구 상태를 무효화하지 못하게 합니다. 완료
  generation의 늦은 재전달은 무시합니다.
- 거부 reason은 `invalid_gesture`, `invalid_sequence`, `invalid_speed_limit`,
  `motion_active`, `not_manual_mode`, `control_state_unavailable|stale`,
  `safety_state_unavailable|stale`, `safety_not_ready`, `stop_latched`입니다.

정규화 7축 preset과 유지시간·Sequence step은 `thing_bringup/config/control.yaml`에서
관리합니다. preset은 실제 모터 raw 위치가 아니며, 물리 endpoint calibration과
Command Guard·hardware limit을 대체하지 않습니다.

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

담당 노드는 `thing_web_bridge/web_bridge_node`이며 endpoint는
`/ws/robot-state` 하나입니다. 이 절이 6.4절 계약의 동결 지점입니다.
브라우저 쪽 사용 예시는 `web/docs/interfaces-bridge.md`에 있습니다.

### 전송 규칙

| 항목 | 값 | 근거 |
| --- | --- | --- |
| endpoint | `/ws/robot-state` | 6.4절 |
| snapshot 주기 | 200ms 고정, 값이 바뀌지 않아도 계속 발행 | NFR-13 모터·상태 5Hz |
| 동시 연결 | **1개만 허용.** 두 번째는 close 1013 | 탭 두 개면 한쪽 STOP이 다른 쪽 제어권까지 해제 |
| 잘못된 endpoint | close 1008 | 임의 경로 차단 (NFR-20) |
| 서비스 왕복 상한 | 2000ms, 초과는 `service_timeout` | |
| 대기열 상한 | 32건, 초과는 `web_queue_overflow` | |

snapshot과 ACK는 하나의 send lock으로 직렬화합니다. `websockets`는 여러
coroutine의 동시 `send()`를 지원하지 않습니다.

### snapshot 필드

top-level 6필드는 6.4절 계약이므로 순서와 이름을 바꾸지 않습니다. 그 뒤 네
필드는 FR-21·FR-24·FR-25 표시를 위한 확장입니다. 아직 유효 데이터를 받지 못한
객체는 `null`이 아니라 `{}`입니다.

| 필드 | 출처 | 내용 |
| --- | --- | --- |
| `timestamp` | 브리지 | snapshot 생성 시각, RFC 3339 UTC `Z` |
| `mode` | `ControlState.active_mode` | `DISABLED\|MIMIC\|MANUAL\|TELEOP` |
| `recording_state` | `RecordingState.state` | symbolic state |
| `landmarks` | `/thing/landmarks` | 아래 파생 필드 포함 |
| `motor_state` | `/thing/motor_status` | `.msg` 원문 + `stale`·`age_ms` |
| `safety_state` | `/thing/safety_state` | `.msg` 원문 + `reset_allowed`·`stale`·`age_ms` |
| `control_state` | `/thing/control_state` | `.msg` 원문 + `stale`·`age_ms` |
| `recording` | `/thing/recording_state` | `.msg` 원문 + `stale`·`age_ms` |
| `last_hand_command` | 최종 `/thing/command` | 7논리축 표시용 (FR-21) |
| `connection_status` | 브리지 파생 | 아래 5종 |

`.msg` 원문을 실을 때 enum은 정수 대신 symbol로 바꾸고 `uint64` Session ID는
10진 문자열로 바꿉니다. 값이 0인 Session ID는 빈 문자열입니다. 읽기 실패로
`NaN`·`Infinity`가 온 실수는 `null`로 보냅니다. 모터 통신 실패 시 `NaN`은 정상
시나리오이므로 여기서 예외를 던지면 안 됩니다.

`landmarks` 파생 필드 — FR-27은 hand-loss latch를 develop ROS 2 메시지에 필드를
추가하지 않고 Web Bridge가 파생한 표시 상태로만 제공하라고 정합니다.

| 필드 | 의미 |
| --- | --- |
| `detect_valid` | `detected` + 오른손 + `confidence >= 0.70` (FR-01 MIMIC 유효 기준) |
| `hand_loss_latched` | 무효가 150ms 연속되면 `true`. **재검출만으로 해제하지 않고** SafetyState가 실제로 `RUN`이 됐을 때만 닫음 (FR-01) |
| `reacquire_elapsed_ms` | 현재 연속 유효 경과 시간 |
| `reacquire_stable_ms` | 재검출 인정 기준값 300 |
| `confidence_min` | 유효 판정에 쓴 임계값 |

`connection_status` — FR-24. 값은 `up`·`down`·`unknown` 세 가지입니다. bool 두
값으로는 "아직 못 받았다"와 "끊겼다"를 구분할 수 없고, `camera`를 `false`로
단정하면 MJPEG가 정상인데 영상이 가려집니다.

| 키 | 판정 근거 |
| --- | --- |
| `jetson` | snapshot 자체가 Jetson에서 생성되므로 도달했다는 사실이 증거 |
| `rpi` | `control_state`·`safety_state`·`motor_status` 중 하나라도 신선 |
| `ros2` | 어느 토픽이든 신선 |
| `camera` | `landmarks` 신선도로 대리 판정. `image_raw`를 구독하지 않으며 노드별 세분화는 diagnostics 소관 (NFR-09) |
| `motor` | `motor_status` 신선 |

신선도 임계값은 표시 전용입니다. 데이터 토픽 1000ms, 상태 토픽 5000ms이며
제어·안전 판정에는 쓰지 않습니다. 각 섹션의 `age_ms`를 함께 보내므로 웹이
자기 기준으로 다시 판단할 수 있습니다 (FR-25).

### 요청 type

브리지는 아래 8종만 받고 임의 topic·motor ID·ROS 이름을 받지 않습니다
(FR-23). payload 키가 정확히 일치하지 않으면 거부합니다.

| type | payload | 변환 대상 |
| --- | --- | --- |
| `set_control_mode` | `requested_mode`(`MIMIC\|MANUAL`), `requested_owner`(`WEB`) | `/thing/set_control_mode` |
| `stop` | `requested_mode`(`DISABLED`), `requested_owner`(`NONE`) | 같은 서비스, FR-10의 명시적 STOP |
| `execute_gesture` | `gesture_name`(4종), `speed_limit` | `/thing/execute_gesture` |
| `execute_sequence` | `sequence_name`(2종), `speed_limit` | `/thing/execute_sequence` — Could |
| `start_recording` | `label` | `/thing/start_recording` |
| `stop_recording` | `session_id`(문자열) | `/thing/stop_recording` |
| `set_mimic_result` | `session_id`, `result`(`SUCCESS\|FAILURE`) | `/thing/set_mimic_result` |
| `reset_safety` | 없음 | `/thing/reset_safety` |

MIMIC↔MANUAL 직접 전환은 브리지에서도 막습니다. `set_control_mode`는
`MIMIC`·`MANUAL`만 받고 `DISABLED`는 `stop` type으로만 보냅니다 (FR-19).

`speed_limit`은 `0.0 < value <= 1.0`만 받고 `0`과 비유한값을 거부합니다
(FR-06). Session ID는 0이 아닌 63-bit 십진 문자열만 받습니다 (FR-18).

### STOP 선점

FR-19·FR-31은 STOP과 안전 전이가 일반 동작을 항상 선점하라고 정합니다. 요청
하나를 처리하는 동안 다음 메시지를 읽지 않으면 STOP이 긴급 요청인지 확인조차
못 하므로 수신과 실행을 분리합니다.

- 일반 요청은 도착 순서대로 하나씩 실행합니다.
- `stop`과 `reset_safety`는 대기열을 건너뛰고 즉시 실행합니다.
- `stop`은 아직 시작하지 않은 일반 요청을 폐기하고 각각에 같은 `request_id`로
  실패 ACK를 돌려줍니다. 이미 ROS로 나간 in-flight 요청은 취소할 수 없습니다.
- `reset_safety`는 대기열을 건너뛰되 대기 중 요청을 폐기하지 않습니다.
- 대기 중 같은 mode·owner `set_control_mode`(FR-34 lease 갱신)는 최신 하나만
  남깁니다. 전부 버리지는 않습니다. in-flight가 timeout되면 실제 갱신이 끊겨
  3000ms 뒤 lease가 만료됩니다.

ACK 응답 순서는 요청 순서와 다를 수 있습니다. 클라이언트는 순서가 아니라
`request_id`로 응답을 찾아야 합니다.

### ACK reason

응답 `reason`은 두 계열입니다. **접두어로 출처를 구분합니다.**

`web_*`는 브리지가 ROS에 보내기 전에 스스로 내린 판단입니다.

| reason | 의미 |
| --- | --- |
| `web_malformed_request` | envelope·payload 키·값 형식이 규칙에 맞지 않음 |
| `web_unknown_type` | 위 8종에 없는 type |
| `web_preempted_by_stop` | STOP이 들어와 실행 전에 폐기됨 |
| `web_superseded` | 대기 중 lease 갱신이 더 새 요청으로 교체됨 |
| `web_queue_overflow` | 대기열 상한 초과 |
| `web_bridge_error` | 브리지 내부 예외 |
| `invalid_mode` | mode·owner 조합이 허용 범위 밖 |
| `invalid_session_id` | Session ID가 0이거나 63-bit를 넘음 |

ROS 호출 자체가 실패한 경우입니다.

| reason | 의미 |
| --- | --- |
| `service_unavailable` | 서비스 서버가 아직 없음 |
| `service_timeout` | 2000ms 안에 응답 없음 |
| `service_failed` | 호출이 예외로 끝남 |
| `service_rejected` | 서버가 거부했지만 `reason`이 비어 있음 |
| `action_unavailable` / `action_timeout` / `action_failed` | Sequence 액션의 같은 경우 |
| `reset_rejected` | Safety Reset 거부인데 `message`가 비어 있음 |

그 밖의 값은 **ROS 응답의 `reason`·`message` 원문을 그대로 전달한 것**입니다.
브리지는 변환하지 않습니다. FR-37의 `accepted`·`invalid_mode`·`owner_conflict`
·`safety_not_ready`·`recording_active`·`motion_active`·`stop_barrier_pending`
·`stop_barrier_timeout`, FR-18의 `not_mimic_mode`·`start_failed`
·`already_recording`·`result_pending`·`not_recording`·`session_mismatch`
·`stop_failed`, FR-35의 `owner_lease_expired`가 여기 해당합니다.

`web_*` 계열은 develop 스키마에 없으므로 이 문서가 단일 기준입니다 (FR-41).

### 브리지가 하지 않는 것

- 모터 command 토픽에 직접 발행하지 않습니다. 모든 일반 명령은
  `command_manager`와 `command_guard`를 거칩니다 (FR-06 경로).
- `HandCommand`에 mode·owner·enable을 넣지 않습니다 (FR-30).
- STOP·Safety Reset을 command 토픽의 가짜 명령으로 넣지 않습니다 (FR-32).
- lease를 대신 갱신하지 않습니다. 갱신은 owner인 내부 제어 웹이 1000ms마다
  보냅니다. 브리지가 대행하면 브라우저를 닫아도 lease가 유지되어 NFR-15의
  "종료·연결 단절 시 lease가 만료되어 안전 전이한다"를 깨뜨립니다.
- 재연결 시 이전 요청을 재생하지 않습니다. 대기열은 연결마다 새로 만듭니다
  (NFR-15).
- rosbag2·DB를 조회하지 않습니다 (FR-26).
- 커스텀 ROS 메시지·서비스·액션·토픽을 만들지 않습니다. `thing_interfaces`는
  메시지 7종·서비스 5종·액션 1종을 그대로 유지합니다 (FR-30).
