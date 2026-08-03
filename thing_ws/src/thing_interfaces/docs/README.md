# thing_interfaces

`thing_interfaces`는 텐던 구동 로봇 손에서 사용하는 ROS 2 메시지, 서비스,
액션 인터페이스를 정의합니다.

## 7개 논리축

논리축 값은 모두 `0.0`부터 `1.0`까지의 무차원 정규화 값입니다.
`0.0`은 해당 동작의 최소 위치, `1.0`은 최대 위치를 의미합니다.

| 필드 | 의미 | 범위 | 단위 |
|---|---|---:|---|
| `thumb_flex` | 엄지 굽힘 | `0.0`~`1.0` | 정규화 값 |
| `thumb_opp` | 엄지 맞섬 | `0.0`~`1.0` | 정규화 값 |
| `thumb_abd` | 엄지 벌림 | `0.0`~`1.0` | 정규화 값 |
| `index_flex` | 검지 굽힘 | `0.0`~`1.0` | 정규화 값 |
| `middle_flex` | 중지 굽힘 | `0.0`~`1.0` | 정규화 값 |
| `ring_flex` | 약지 굽힘 | `0.0`~`1.0` | 정규화 값 |
| `little_flex` | 소지 굽힘 | `0.0`~`1.0` | 정규화 값 |

## 메시지

### `HandCommand`

| 필드 | 의미 | 범위·단위 |
|---|---|---|
| `stamp` | 명령 생성 시각 | ROS 2 시간 |
| `sequence` | 명령 순서 번호 | `uint32` |
| `source` | 명령 출처 | 아래 `SOURCE_*` 값 |
| `thumb_flex` | 엄지 굽힘 명령 | `0.0`~`1.0`, 정규화 값 |
| `thumb_opp` | 엄지 맞섬 명령 | `0.0`~`1.0`, 정규화 값 |
| `thumb_abd` | 엄지 벌림 명령 | `0.0`~`1.0`, 정규화 값 |
| `index_flex` | 검지 굽힘 명령 | `0.0`~`1.0`, 정규화 값 |
| `middle_flex` | 중지 굽힘 명령 | `0.0`~`1.0`, 정규화 값 |
| `ring_flex` | 약지 굽힘 명령 | `0.0`~`1.0`, 정규화 값 |
| `little_flex` | 소지 굽힘 명령 | `0.0`~`1.0`, 정규화 값 |
| `speed_limit` | 명령 적용 속도 제한 | 구현에서 정한 정규화 속도 제한 |
| `confidence` | 입력 명령 신뢰도 | `0.0`~`1.0`, 정규화 값 |

`source` 값은 `SOURCE_UNKNOWN(0)`, `SOURCE_MIMIC(1)`,
`SOURCE_TELEOP(2)`, `SOURCE_GESTURE(3)`, `SOURCE_SEQUENCE(4)`,
`SOURCE_SAFETY(5)`입니다.

### `HandLandmarks`

| 필드 | 의미 | 단위 |
|---|---|---|
| `header` | 좌표가 생성된 시각과 기준 프레임 | ROS 2 Header |
| `detected` | 손 검출 여부 | bool |
| `confidence` | 손 검출 신뢰도 | 정규화 값 |
| `handedness` | 왼손·오른손 구분 | 아래 `HANDEDNESS_*` 값 |
| `handedness_confidence` | 왼손·오른손 판정 신뢰도 | 정규화 값 |
| `image_width` | 입력 영상 너비 | pixel |
| `image_height` | 입력 영상 높이 | pixel |
| `landmarks` | 21개 손 랜드마크 좌표 | `Point32[21]` |

`handedness` 값은 `HANDEDNESS_UNKNOWN(0)`, `HANDEDNESS_LEFT(1)`,
`HANDEDNESS_RIGHT(2)`입니다.

### `ControlState`

| 필드 | 의미 |
|---|---|
| `stamp` | 상태 생성 시각 |
| `active_mode` | 현재 활성 제어 모드 |
| `active_owner` | 현재 제어권자 |
| `owner_alive` | 제어권자의 연결 상태 |
| `sequence_running` | 시퀀스 실행 여부 |
| `last_transition_reason` | 마지막 상태 변경 사유 |

제어 모드는 `MODE_DISABLED(0)`, `MODE_MIMIC(1)`, `MODE_MANUAL(2)`,
`MODE_TELEOP(3)`이며, 제어권자는 `OWNER_NONE(0)`, `OWNER_WEB(1)`,
`OWNER_LOCAL(2)`입니다.

### `SafetyState`

| 필드 | 의미 |
|---|---|
| `stamp` | 상태 생성 시각 |
| `state` | 현재 안전 상태 |
| `command_timeout` | 명령 timeout 발생 여부 |
| `motor_communication_ok` | 모터 통신 정상 여부 |
| `over_current` | 과전류 발생 여부 |
| `over_temperature` | 과온 발생 여부 |
| `estop_active` | 비상정지 활성화 여부 |
| `fault_code` | 오류 코드 |
| `reason` | 안전 상태 전환 또는 오류 원인 |

안전 상태는 `INIT(0)`, `READY(1)`, `RUN(2)`, `HOLD(3)`, `SAFE(4)`,
`FAULT(5)`, `ESTOP(6)`입니다.

### `MotorState`

| 필드 | 의미 | 단위 |
|---|---|---|
| `motor_id` | 물리 모터 ID | ID |
| `actuator_name` | 논리축 또는 액추에이터 이름 | 문자열 |
| `goal_position_raw` | 목표 encoder 위치 | raw |
| `present_position_raw` | 현재 encoder 위치 | raw |
| `goal_position_rad` | 목표 위치 | rad |
| `present_position_rad` | 현재 위치 | rad |
| `velocity_rad_s` | 현재 속도 | rad/s |
| `current_ampere` | 현재 전류 | A |
| `voltage_volt` | 입력 전압 | V |
| `temperature_celsius` | 모터 온도 | °C |
| `hardware_error` | 하드웨어 오류 코드 | 코드 |
| `communication_result` | 통신 결과 코드 | 코드 |
| `communication_ok` | 해당 주기 통신 성공 여부 | bool |

### `MotorStatus`

| 필드 | 의미 |
|---|---|
| `header` | 모터 상태 묶음을 읽은 시각과 기준 프레임 |
| `motors` | `MotorState` 배열 |
| `bus_communication_ok` | 전체 모터 버스 통신 정상 여부 |
| `failed_read_count` | 누적 읽기 실패 횟수 |
| `message` | 통신 상태 또는 오류 설명 |

### `RecordingState`

| 필드 | 의미 |
|---|---|
| `header` | 녹화 상태 생성 시각과 기준 프레임 |
| `state` | 현재 녹화 상태 |
| `active_session_id` | 현재 녹화 중인 세션 ID |
| `active_bag_path` | 현재 rosbag2 저장 경로 |
| `active_started_at` | 현재 세션 시작 시각 |
| `last_session_id` | 최근 종료된 세션 ID |
| `last_bag_path` | 최근 종료 세션 저장 경로 |
| `last_started_at` | 최근 세션 시작 시각 |
| `last_ended_at` | 최근 세션 종료 시각 |
| `result_pending` | 성공·실패 판정 대기 여부 |
| `last_mimic_result` | 최근 세션 판정 결과 |
| `message` | 녹화 상태 또는 오류 설명 |

녹화 상태는 `IDLE(0)`, `STARTING(1)`, `RECORDING(2)`, `STOPPING(3)`,
`COMPLETED(4)`, `FAILED(5)`, `INTERRUPTED(6)`입니다. 판정 결과는
`RESULT_UNSET(0)`, `RESULT_SUCCESS(1)`, `RESULT_FAILURE(2)`입니다.

## 서비스·액션 처리 결과

| 인터페이스 | 성공 조건 및 응답 | 실패·거부 사유(`reason`) |
|---|---|---|
| `SetControlMode` | 요청한 모드와 제어권을 적용하고 `accepted=true` 반환 | `invalid_mode`, `invalid_owner`, `recording_active`, `sequence_running`, `safety_not_ready` |
| `ExecuteGesture` | 실행 요청을 수락하고 `accepted=true` 반환 | `invalid_gesture`, `invalid_speed_limit`, `not_manual_mode`, `sequence_running`, `safety_not_ready` |
| `StartRecording` | 세션을 생성하고 rosbag2 녹화를 시작한 뒤 `accepted=true`, 유효한 `session_id`, `bag_path` 반환 | `not_mimic_mode`, `already_recording`, `result_pending`, `start_failed` |
| `StopRecording` | 현재 세션을 종료한 뒤 `accepted=true`, 종료된 `stopped_session_id`, `bag_path` 반환 | `not_recording`, `session_mismatch`, `stop_failed` |
| `SetMimicResult` | 종료된 세션에 성공·실패 결과를 저장하고 `accepted=true` 반환 | `session_not_found`, `recording_active`, `result_already_set`, `invalid_result` |
| `ExecuteSequence` | 전체 단계를 정상 완료하고 `success=true` 반환 | `invalid_sequence`, `invalid_speed_limit`, `sequence_running`, `safety_not_ready`, `canceled`, `safety_interrupted`, `internal_error` |

### 공통 반환 규칙

- 서비스 요청 조건이 맞지 않거나 처리 시작에 실패하면 `accepted=false`를
  반환합니다.
- 액션이 정상 완료되면 `success=true`를 반환합니다.
- 액션이 실행 중 취소되거나 안전 기능 또는 내부 오류로 중단되면
  `success=false`를 반환합니다.
- `reason`에는 위 표에 정의한 표준 문자열을 사용합니다. 성공한 경우에는
  빈 문자열을 사용합니다.
- 실패한 `StartRecording` 요청은 `session_id=0`, `bag_path=""`를
  반환합니다.
- 실패한 `StopRecording` 요청은 `stopped_session_id=0`,
  `bag_path=""`를 반환합니다.
- `ExecuteSequence` 취소는 별도 서비스가 아니라 ROS 2 Action의 취소
  기능을 사용합니다.

