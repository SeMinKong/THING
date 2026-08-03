# 시스템 아키텍처

## 장치별 책임

| 장치 | 책임 |
| --- | --- |
| Jetson Orin Nano | RGB 카메라, MediaPipe, 7논리축 목표, overlay/MJPEG, Web Bridge |
| Raspberry Pi 5 | 제어권 중재, 명령 검증, 안전, DYNAMIXEL, Logger·exporter·uploader |
| Laptop | 내부망 관제·제어 웹, TELEOP와 개발 |
| AWS EC2 | 공개 데이터 포털, SQLite와 세 파일 영속 저장 |

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
ControlState / SafetyState / RecordingState ──┼→ thing_web_bridge
HandCommand / MotorStatus ────────────────────┘       │
                                                      ├→ WebSocket → Laptop 내부 제어 웹
                                                      └→ MJPEG ────→ Laptop 내부 제어 웹

MIMIC recording request
  → thing_logger
  → rosbag2(HandLandmarks, HandCommand, MotorStatus, state topics)
  → 사용자 SUCCESS/FAILURE 판정
  → Logger 측 exporter
  → metadata JSON + HandCommand CSV + MotorStatus CSV
  → 격리 thing-data-uploader
  → Bearer Token HTTPS
  → EC2 Django STAGING 검증
  → EC2 SQLite READY + EC2_DATA_DIR
  → 공개 GET 조회·세 파일 다운로드
```

카메라와 Vision은 녹화 여부와 무관하게 계속 동작합니다. 카메라 영상은 MJPEG
관제에만 사용하고 파일 또는 rosbag2에 저장하지 않습니다.

로봇의 로컬 시계열 원본은 rosbag2입니다. 별도 애플리케이션 SQLite,
`session_state.json`과 영구 upload spool은 만들지 않습니다. 업로드 실패 세션은
보존된 rosbag2와 SUCCESS/FAILURE를 입력받는 수동 재업로드 절차로 처리합니다.

## 제어 모드와 안전

- `DISABLED`: 일반 명령 차단
- `MIMIC`: MediaPipe 명령 사용
- `MANUAL`: 웹 Gesture·Sequence 사용
- `TELEOP`: 로컬 키보드 개별축 제어

안전 상태는 `INIT → READY → RUN → HOLD → SAFE/FAULT`로 관리하며 비상정지는
별도 `ESTOP` 상태입니다. Raspberry Pi의 제한과 비상정지는 웹·Jetson 장애와
독립적으로 동작해야 합니다.

`SAFE`, `FAULT`, `ESTOP` 복구는 `/thing/reset_safety`의 INIT 재검사를 거쳐
READY까지만 전환합니다. 이전 mode·owner·명령·queue·녹화는 자동 복원하지
않습니다.

## 네트워크 원칙

- 프로젝트 내부망에서만 제어합니다.
- 장치들은 같은 ROS Domain ID와 합의된 DDS 설정을 사용합니다.
- 명령 timestamp는 장치 간 시간 동기화를 전제로 합니다.
- 네트워크가 복구돼도 명시적인 재개 입력 전에는 자동으로 움직이지 않습니다.
- EC2에는 ROS 2 DDS와 로봇 제어 API를 노출하지 않습니다.
- EC2 포털은 HTTPS 공개 GET으로 READY 데이터만 조회하고, 업로드는 장치별
  Bearer Token을 사용하는 Raspberry Pi uploader만 수행합니다.
