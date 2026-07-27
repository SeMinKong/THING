# 시스템 아키텍처

## 장치별 책임

| 장치 | 책임 |
| --- | --- |
| Jetson Orin Nano | RGB 카메라, MediaPipe, 7논리축 목표, overlay/MJPEG, Web Bridge |
| Raspberry Pi 5 | 제어권 중재, 명령 검증, 안전 상태, DYNAMIXEL 제어 |
| Laptop | 웹 클라이언트, TELEOP, 개발과 선택적 데이터 분석 |

## 제어 데이터 흐름

```text
사용자 손
  → camera_node
  → mediapipe_node
  → HandLandmarks
  → hand_target_node
  → /thing/command/mimic
                         ┐
웹 Gesture/Sequence ─────┼→ command_manager → /thing/command/selected
로컬 TELEOP ─────────────┘
  → command_guard
  → /thing/command
  → dynamixel_node
  → XL-330-288T × 7
  → MotorStatus / diagnostics
```

웹은 모터에 직접 명령하지 않습니다. 모든 일반 명령은 `command_manager`와
`command_guard`를 통과해야 합니다.

## 관제와 기록 흐름

```text
camera overlay → MJPEG ───────────────────────┐
HandLandmarks / HandCommand / MotorStatus ────┼→ thing_web_bridge → Django/React
ControlState / SafetyState / RecordingState ──┘

MIMIC recording request
  → thing_logger
  → Session ID + SQLite metadata
  → rosbag2(HandLandmarks, HandCommand, MotorStatus, state topics)
  → 사용자 SUCCESS/FAILURE 판정
```

카메라와 Vision은 녹화 여부와 무관하게 계속 동작합니다. 카메라 영상은 MJPEG
관제에만 사용하고 파일 또는 rosbag2에 저장하지 않습니다.

## 제어 모드와 안전

- `DISABLED`: 일반 명령 차단
- `MIMIC`: MediaPipe 명령 사용
- `MANUAL`: 웹 Gesture·Sequence 사용
- `TELEOP`: 로컬 키보드 개별축 제어

안전 상태는 `INIT → READY → RUN → HOLD → SAFE/FAULT`로 관리하며 비상정지는
별도 `ESTOP` 상태입니다. Raspberry Pi의 제한과 비상정지는 웹·Jetson 장애와
독립적으로 동작해야 합니다.

## 네트워크 원칙

- 프로젝트 내부망에서만 제어합니다.
- 장치들은 같은 ROS Domain ID와 합의된 DDS 설정을 사용합니다.
- 명령 timestamp는 장치 간 시간 동기화를 전제로 합니다.
- 네트워크가 복구돼도 명시적인 재개 입력 전에는 자동으로 움직이지 않습니다.
