# Safety Manager 구현 및 운용

## 범위

`thing_control/safety_manager`는 8상태 안전 정책을 결정하고 `/thing/safety_state`를 발행한다.

- Safety Manager: 상태 전이, timeout, reset 완료 판정
- Command Manager: mode·owner 해제, STOP event 발행
- Command Guard: 명령 형식·freshness 검증, HOLD activity 분기
- Hardware: `estop_gpio_node`의 GPIO E-Stop heartbeat, torque disable write,
  timestamp가 채워진 MotorStatus 발행
- Web/UI: RUN 중 사용자 확인 및 confirmation token 관리

Hardware GPIO 접근과 DYNAMIXEL torque write, Web popup/token은 이 패키지의 구현 범위가 아니다.

## 최소 인터페이스 변경

기존 인터페이스 호환성을 위해 아래 두 변경만 추가한다.

1. `SafetyState.msg`: 기존 enum 뒤에 `RESET=7`
2. `MotorState.msg`: `bool torque_enabled`

`SafetyState.state_generation`, 별도 hardware ack message, custom reset service는 추가하지 않는다. `MotorStatus.msg`와 기존 service 파일은 변경하지 않는다.

## 상태 전이

| 현재 | 입력/조건 | 다음 | 핵심 동작 |
| --- | --- | --- | --- |
| INIT | MotorStatus·E-Stop 최초 정상 확인 | READY | 명령 차단, torque OFF 확인 |
| READY | 검증된 일반 명령 | RUN | 명령 실행 허용 |
| READY | 정상 STOP event | RESET | motor 이동 없이 torque OFF 재확인 |
| RUN | 명령 공백 5000ms | HOLD | 현재 setpoint 유지, motor command 차단 |
| RUN | 정상 STOP event | RESET | hardware torque OFF 대기 |
| HOLD | valid activity가 gap 100ms 이하로 300ms 연속 | RUN | 복구 activity만 확인 후 실행 재개 |
| HOLD | 마지막으로 hardware에 전달된 valid command 기준 총 10000ms 단절 | SAFE | 간헐 recovery activity로 총 timeout을 연장하지 않음 |
| RESET | 최소 500ms + fresh 7-motor torque OFF | READY | 정상 제어권 변경 완료 |
| RESET | 3000ms timeout 또는 hardware fault | FAULT | fail-closed |
| 모든 상태 | E-Stop active 또는 입력 stale | ESTOP | hardware torque OFF 전제, 명령 차단 |
| SAFE·FAULT·ESTOP | 원인 해소 + `/thing/reset_safety` | INIT | 전체 입력 재검사 후 READY |

HOLD에서도 정상 STOP event는 RESET으로 전이할 수 있다. 별도 `/thing/reset_safety` Safety Reset 서비스는 HOLD에서 거부되고 SAFE·FAULT·ESTOP에서만 허용된다.

## 정상 제어권 변경 순서

1. Web/UI가 RUN이면 사용자 확인을 수행한다.
2. Web이 기존 `/thing/set_control_mode`에 `MODE_DISABLED/OWNER_NONE`을 요청한다.
3. Command Manager가 mode·owner를 먼저 해제한다.
4. Command Manager가 `/thing/control/stop_requested`를 발행한다.
5. Command Guard가 STOP latch를 닫아 이미 큐에 있던 selected command를 차단한다.
6. Guard가 `/thing/control/stop_barrier_ack`을 발행한다.
7. Safety Manager가 causal ACK을 받은 뒤 READY·RUN·HOLD에서 RESET으로 전이한다.
8. Hardware가 RESET SafetyState를 보고 torque를 OFF하고 MotorStatus를 갱신한다.
9. Safety Manager가 완료 조건을 모두 확인하고 READY로 전이한다.

## RESET 완료 판정

Safety Manager는 RESET 진입 때 아래 두 시간을 내부에 보존한다.

- local monotonic 수신 시각: callback 순서와 timeout 판단
- ROS system timestamp: hardware 측정 timestamp와 상관관계 판단

완료 조건은 모두 만족해야 한다.

```text
RESET 경과시간 >= 500ms
MotorStatus callback 수신시각 >= RESET 진입 monotonic 시각
MotorStatus callback 수신시각 < RESET 진입 + 3000ms
MotorStatus.header.stamp >= RESET 진입 ROS timestamp
MotorStatus.header.stamp가 수신 system time 기준 300ms 미만 과거, 100ms 이하 미래
MotorStatus.motors가 고유 motor_id 7개이며 수치가 모두 finite
MotorStatus.bus_communication_ok == true
모든 MotorState.communication_ok == true
모든 MotorState.torque_enabled == false
과전류·과온 없음
```

Hardware는 `MotorStatus.header.stamp`를 실제 측정 시각으로 채워야 한다. RESET 진입 전 timestamp의 늦은 status는 완료 근거로 인정하지 않는다. 정확히 3000ms 또는 그 이후에는 완료 조건보다 timeout 판정을 먼저 수행해 FAULT로 간다.

### Clock·heartbeat·DDS trust 경계

- watchdog, RESET, release window와 모든 ROS timer는 steady clock을 사용한다. system clock은 ROS stamp 생성 및 source header 비교에만 사용한다.
- E-Stop과 MotorStatus callback은 이전 receipt를 덮어쓰기 전에 callback-to-callback gap을 검사한다. timer callback이 지연돼도 300ms 이상 gap은 각각 ESTOP/FAULT로 남고, 재개된 inactive E-Stop heartbeat 시점부터 500ms release window를 새로 센다.
- Command Guard는 SafetyState보다 resumed command를 먼저 받아도 마지막 실제 hardware forward 후 5000ms 이상이면 local HOLD barrier로 hardware forwarding을 막고 validation activity만 발행한다.
- validation activity는 실제 hardware-forwarded command와 별도로 처리해 delayed Safety tick의 RUN watchdog baseline을 갱신하지 않는다. recovery와 10000ms SAFE deadline이 동시에 성립하면 SAFE가 우선한다.
- Guard의 HOLD activity(`true`)와 검증 실패(`false`)는 단일 ordered `/thing/command/validation_result`로 전달되어 서로 다른 DDS topic 재정렬 없이 recovery stable window를 갱신하거나 초기화한다.
- SafetyState stamp는 같은 transition의 periodic heartbeat 동안 고정하고 상태 전이 때만 증가한다. Guard는 stamp가 역행한 지연 상태를 거부하고 `command_stream_recovered` 전이를 관측해 HOLD 표본을 놓친 경우에도 local forwarding 기준을 안전하게 다시 연다.
- MotorStatus 유발 FAULT publication은 최대 한 20ms steady tick 동안 coalesce한다. 같은 ready set의 active E-Stop은 ESTOP으로 우선 publish하며, E-Stop이 없으면 deadline 직후 FAULT를 publish한다.
- `builtin_interfaces/Time.nanosec`가 `[0, 1000000000)` 밖이면 MotorStatus와 HandCommand를, SafetyState source stamp가 0 이하이거나 역행·동일 stamp로 enum이 바뀌면 Guard와 Manager가 해당 상태를 malformed/replayed로 거부한다. Manual Executor는 표준 `UInt64.data` generation으로 raw STOP과 Guard ACK을 상관시키고, 동일 ACK 이후 로컬 callback 관측 순서가 `DISABLED`, `RESET/INIT→READY`, 새 `MANUAL`까지 완성된 경우에만 admission latch를 연다.
- `control.launch.py`는 별도 security artifact 없이 `estop_gpio_node`와 네 control node를 동일한 `control.yaml`로 시작하며, 노드 이름·토픽·서비스 계약은 기존과 동일하게 유지한다.
- 모든 timing parameter는 strict integer이며 V6.4 fail-closed envelope의 유한 상한을 초과할 수 없다.

## ROS 인터페이스

### 구독

- `/thing/command` (`thing_interfaces/msg/HandCommand`)
- `/thing/command/validation_result` (`std_msgs/msg/Bool`), reliable depth 10 단일 ordered 결과 채널
- `/thing/control/stop_barrier_ack` (`std_msgs/msg/UInt64`, `data`에 raw STOP과 동일한 generation을 담고 Guard latch 이후 발행하는 causal ACK)
- `/thing/motor_status` (`thing_interfaces/msg/MotorStatus`), reliable + volatile, depth 5 heartbeat
- `/thing/estop` (`std_msgs/msg/Bool`), `estop_gpio_node`의 reliable + volatile
  100ms heartbeat. `true`는 active이고 입력 불명·open/read 실패도 active로 처리

### 발행

- `/thing/safety_state` (`thing_interfaces/msg/SafetyState`), reliable + transient local, 100ms heartbeat

### 서비스

- `/thing/reset_safety` (`std_srvs/srv/Trigger`): SAFE·FAULT·ESTOP 안전 복구 전용

정상 제어권 변경은 신규 service 없이 기존 `/thing/set_control_mode`를 사용한다.

## 고정 안전 시간

`thing_bringup/config/control.yaml` 기본값은 다음과 같다.

| 항목 | 값 |
| --- | ---: |
| RUN→HOLD command timeout | 5000ms |
| RUN/HOLD→SAFE 총 timeout | 10000ms |
| HOLD recovery stable | 300ms |
| HOLD recovery max gap | 100ms |
| RESET 최소 유지 | 500ms |
| RESET hardware timeout | 3000ms |
| SAFE/FAULT 원인 해소 안정 | 1000ms |
| E-Stop 해제 안정 | 500ms |
| MotorStatus freshness | 300ms |
| E-Stop GPIO poll/debounce | 5ms / 50ms |
| E-Stop heartbeat | 100ms |
| E-Stop input freshness | 300ms |
| SafetyState heartbeat | 100ms |

더 느슨한 안전 시간으로 parameter를 확장하면 node 시작 단계에서 거부한다.

### 전류·온도 trip 검증 gate

운영 `control.yaml`은 현재 `trip_limits_validated: true`,
`max_current_ampere: 1.47`, `max_temperature_celsius: 70.0`을 사용한다. 반면 YAML 없이
`ros2 run thing_control safety_manager`로 단독 실행할 때의 코드 fallback은 의도적으로
`trip_limits_validated: false`와 `max_current_ampere: 0.145`를 유지하므로
`INIT/trip_limits_unvalidated`에서 열리지 않는다.

1.47 A는 ROBOTIS 공식 XL330-M288-T e-Manual의 권장 5.0 V stall current와 같다.
제조사 사양 근거와 프로젝트 실물 부하 검증은 별개이며, 이 설정 설명이 실물 시험 완료를
뜻하지 않는다: <https://emanual.robotis.com/docs/en/dxl/x/xl330-m288/>

Hardware 통합 담당자는 7개 XL330-M288-T의 실물 부하·온도 시험과 제조사 범위를 근거로
운영 trip 값을 리뷰해야 한다. 단순히 READY 전이를 위해 flag나 한계를 바꾸면 안 되며,
자동화 테스트는 synthetic status를 검증할 때 node parameter override를 사용할 수 있다.

## 빌드와 실행

```bash
cd thing_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select thing_interfaces thing_hardware thing_control thing_bringup
source install/setup.bash

export ROS_DOMAIN_ID=<deployment-domain-id>
ros2 launch thing_bringup control.launch.py
```

시작 직후 `estop_gpio_node`는 GPIO가 stable inactive로 확인되기 전까지 active를
발행합니다. 이후 heartbeat가 300ms 안에 오지 않으면 ESTOP, MotorStatus를 한 번도
받지 못하면 FAULT로 fail-closed합니다. READY 전이를 위해 motor status node가
`/thing/motor_status`를 freshness 제한보다 빠르게 발행해야 하며,
전류·온도 trip 값의 실물 검증도 완료돼야 한다.

상태 확인:

```bash
ros2 topic echo /thing/safety_state \
  --qos-durability transient_local \
  --qos-reliability reliable
```

안전 원인 해소 후 복구:

```bash
ros2 service call /thing/reset_safety std_srvs/srv/Trigger '{}'
```

## 검증

```bash
cd thing_ws
source /opt/ros/humble/setup.bash
source install/setup.bash
colcon test --packages-select thing_interfaces thing_hardware thing_control thing_bringup
colcon test-result --verbose
```

핵심 자동화 검증:

- 순수 코어 전이·경계값 테스트
- ROS callback/서비스 테스트
- HOLD에서 activity 발행 및 `/thing/command` 차단 테스트
- 세 노드 end-to-end `RUN → RESET → READY` 테스트
- launch/config/entry point wiring 테스트
- 설치 schema에서 generation/custom service 부재 확인
