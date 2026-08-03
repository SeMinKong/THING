<p align="center">
  <img src="web/frontend/public/icon.png" width="128" alt="THING 로봇 손 로고">
</p>

<h1 align="center">THING</h1>

<p align="center">
  <strong>오른손의 21개 특징점을 7축 명령으로 변환하는 3D 프린팅 텐던 로봇 손 프로젝트</strong>
</p>

THING은 MediaPipe로 오른손의 21개 특징점(landmark)을 인식하고, 이를 네 손가락의
굽힘과 엄지의 굽힘·대립·벌림으로 구성된 7개 논리축 명령으로 변환합니다.
저장소에는 3D 프린팅용 기구·URDF, ROS 2 비전·제어 코어, React 관제 UI,
DYNAMIXEL 점검 도구와 실험 데이터 포털이 함께 들어 있습니다.

> **현재 상태**
>
> 각 구성 요소를 개별적으로 구현·시험 중인 프로토타입입니다. 카메라 입력부터
> 실물 모터 구동과 데이터 업로드까지를 한 번에 실행하는 통합 구성은 아직 제공하지 않습니다.

## 시연 영상

<p align="center">
  <a href="media/videos/파지말랑이.mp4">
    <img
      src="docs/daily-reports/2026-07-31/images/integrated-robot-hand.jpg"
      width="420"
      alt="THING 텐던 로봇 손 프로토타입"
    >
  </a>
</p>

<p align="center">
  <sub>실물 텐던 로봇 손 프로토타입 · 이미지를 클릭하면 말랑이 파지 영상을 볼 수 있습니다.</sub>
</p>

- [손가락 순차 동작 — 파도타기](media/videos/파도타기.mp4)
- [유연한 물체 파지 — 말랑이](media/videos/파지말랑이.mp4)
- [물체 파지 — 손목보호대](media/videos/파지손목보호.mp4)

영상 파일은 Git LFS로 관리합니다.

## 구현된 내용

| 영역 | 현재 저장소에 구현된 내용 |
| --- | --- |
| 기구·모델 | V5.2.1 개별 STL 25종, 17개 링크·16개 회전 관절의 URDF, 7축 텐던 연동 맵 |
| 비전 | USB 카메라 프레임 발행, MediaPipe Hands 기반 오른손 21개 landmark 검출, 정규화된 7축 `HandCommand` 생성 |
| 제어 코어 | MIMIC·MANUAL·TELEOP 명령 중재, 제어 모드·소유권 관리, STOP과 기본 안전 상태 관리 및 단위 테스트 |
| 내부 관제 UI | MIMIC·MANUAL 화면과 손 검출·7축 목표·모터·안전·기록 상태 표시 UI, 개발용 WebSocket 모의 브리지 |
| DYNAMIXEL 도구 | XL330-M288-T 7개를 위한 연결·상태 점검, 이동 계획 확인, 원위치·출력 제한·파지·웨이브 시험 스크립트 |
| 데이터 포털 | Django·React 기반 세션 업로드 검증, 목록·상세·시계열 조회와 파일 다운로드 코드 |

## 현재 구현 구조

아래 네 흐름은 현재 저장소에서 각각 확인할 수 있지만, 아직 하나의 end-to-end
실행 경로로 연결되어 있지는 않습니다.

```text
ROS 2 비전·제어 경로 — 현재 모터 연결 전
Camera
  → camera_node
  → mediapipe_node
  → hand_target_node
  → /thing/command/mimic
  → command_manager
  → /thing/command/selected

하드웨어 점검
DYNAMIXEL tools
  → U2D2
  → XL330-M288-T × 7 (독립 점검)

관제 UI 데모
Vite + React
  ↔ 개발용 WebSocket 모의 브리지

데이터 포털
React
  → Django API
  → SQLite + 세션 파일
```

위 구성은 현재 구현된 모듈을 나타냅니다. ROS 2 하드웨어 노드, Logger, 실제 Web Bridge,
통합 launch, 장치 uploader와 Jetson·Raspberry Pi 배포 설정은 통합 개발 중입니다.

## 빠르게 확인하기

### 저장소 받기

Git LFS가 필요합니다.

```bash
git lfs install
git clone https://github.com/SeMinKong/THING.git
cd THING
git lfs pull
```

### 로봇 없이 관제 UI 실행

Node.js 환경에서 프런트엔드와 개발용 WebSocket 모의 브리지를 실행합니다.

터미널 1에서 의존성을 설치하고 mock bridge를 실행합니다.

```bash
cd web/frontend
npm ci
npm run mock
```

터미널 2에서는 저장소 루트에서 프런트엔드를 실행합니다.

```bash
cd web/frontend
npm run dev
```

브라우저에서 Vite가 안내하는 주소를 열면 제어권, 안전 상태, 기록 상태와 명령 UI를
모의 데이터로 확인할 수 있습니다. 별도의 MJPEG 주소를 설정하지 않으면 카메라 영상은
표시되지 않습니다.

### ROS 2 패키지 빌드

Ubuntu 22.04와 ROS 2 Humble 환경을 기준으로 합니다.

```bash
source /opt/ros/humble/setup.bash
cd thing_ws
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
colcon test
colcon test-result --verbose
```

### DYNAMIXEL 오프라인 테스트

저장소 루트에서 모터를 연결하지 않고 7축 설정과 동작 계획을 검증할 수 있습니다.

```bash
python3 -m unittest tests/dynamixel/test_hand_motion_offline.py -v
```

## 저장소 구성

| 경로 | 내용 |
| --- | --- |
| [`mechanical/`](mechanical/) | CAD, STL, 도면과 조립 자료 |
| [`electronics/`](electronics/) | BOM, 회로, 배선과 안전 전원 자료 |
| [`thing_ws/`](thing_ws/) | ROS 2 인터페이스·설명·비전·제어 패키지 |
| [`web/frontend/`](web/frontend/) | 내부 관제·제어 React UI |
| [`tools/dynamixel/`](tools/dynamixel/) | 독립 DYNAMIXEL 점검·제어 도구 |
| [`EC2/thing_database_web/`](EC2/thing_database_web/) | 실험 데이터 포털 프런트엔드·백엔드 |
| [`tests/`](tests/) | 자동화 테스트와 실물 시험 절차 |
| [`docs/`](docs/) | 아키텍처, 인터페이스, 환경 설정과 개발 기록 |
| [`media/`](media/) | 프로젝트 이미지와 시연 영상 |

## 문서

- [시스템 아키텍처](docs/architecture.md)
- [ROS 2·Web·EC2 인터페이스](docs/interfaces.md)
- [Ubuntu 개발 환경](docs/setup/ubuntu.md)
- [DYNAMIXEL 설정](docs/setup/dynamixel.md)
- [기구 자료 안내](mechanical/README.md)
- [전자·배선 자료 안내](electronics/README.md)
- [시험 절차](tests/README.md)
- [EC2 데이터 포털 실행·구조 안내](EC2/README.md)

## 안전

실제 모터에 토크를 인가하기 전에 모터 ID, 이동 범위, 전류 제한, 텐던 장력과
비상 정지·전원 차단 수단을 확인해야 합니다. 실물 구동 명령과 장치별 점검 순서는
아래 안전 문서를 먼저 확인한 뒤 사용하세요.

- [DYNAMIXEL 도구와 기본 점검](tools/dynamixel/README.md)
- [모터 제어 안전 지침](docs/motor-control/dynamixel/safety.md)
- [Raspberry Pi 현장 제어 안내](tools/dynamixel/rpi/README.md)

## 라이선스

이 프로젝트는 [Apache License 2.0](LICENSE)으로 배포됩니다.

## 기존 README

개편 전 기술·계획 중심 README 원문은 [README_LEGACY.md](README_LEGACY.md)에
변경 없이 보존했습니다.
