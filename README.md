# Human-Mimetic Tendon Robot Hand

카메라로 사용자의 손 자세를 인식하고, 7개의 DYNAMIXEL 서보로 구동되는
underactuated tendon 로봇손이 동작을 실시간으로 모방하는 3주 MVP 프로젝트입니다.

최종 요구사항의 단일 기준은
[`docs/requirements/요구사항 명세서 V5.md`](docs/requirements/요구사항%20명세서%20V5.md)입니다.

## MVP

- MediaPipe 기반 한 손의 21개 landmark 검출
- 엄지 굽힘·대립·벌림과 네 손가락 굽힘으로 구성된 7논리축 생성
- 7논리축과 XL-330-288T 7개를 1:1로 연결
- ROS 2 Humble 기반 Jetson–Raspberry Pi 무선 제어
- MIMIC·MANUAL·TELEOP 제어권 중재와 안전 제한
- 편 손, 주먹, 원통 파지, 엄지–검지 집기
- React·Django 웹 관제 및 MIMIC 데이터 기록
- MediaPipe landmark, HandCommand, MotorStatus를 rosbag2로 기록
- SQLite 세션과 사용자 성공·실패 판정 저장

Isaac Sim/Lab, VLA, imitation learning과 관절별 독립 다축 제어는 MVP 제외 범위입니다.

## 실행 장치

| 장치 | 주요 역할 |
| --- | --- |
| Jetson Orin Nano | 카메라, MediaPipe, 7축 목표 생성, MJPEG, Web Bridge |
| Raspberry Pi 5 | 명령 중재·검증, 안전 상태, DYNAMIXEL 제어 |
| Laptop | 웹 접속, 개발, TELEOP, 선택적 데이터 분석 |
| XL-330-288T × 8 | 7개 활성 구동축, 1개 예비 모터 |

Raspberry Pi는 Ubuntu 24.04 호스트에서 Ubuntu 22.04 기반 ROS 2 Humble
컨테이너를 사용하는 구성을 기본으로 합니다.

## 저장소 구성

- `thing_ws/`: ROS 2 인터페이스·비전·제어·하드웨어·로거·bringup
- `web/`: Vite + React 프론트엔드와 Django 백엔드
- `mechanical/`: CAD, STL, 조립 및 출력 자료
- `electronics/`: BOM, 회로, 배선 및 안전 전원
- `vision/`: 비전 실험과 캘리브레이션 자료
- `tests/`: 재현 가능한 시험 절차와 결과
- `docs/`: 요구사항, 아키텍처, 인터페이스 및 개발환경 문서
- `simulation/`: Post-MVP 시뮬레이션 자료

## 저장소 받기

Git LFS가 필요합니다.

```bash
sudo apt install git-lfs
git lfs install
git clone --branch develop \
  https://lab.ssafy.com/s15-webmobile3-sub1/S15P11C103.git
cd S15P11C103
git lfs pull
```

## ROS 2 빌드

Ubuntu 22.04와 ROS 2 Humble 환경에서 실행합니다.

```bash
source /opt/ros/humble/setup.bash
cd thing_ws
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
colcon test
colcon test-result --verbose
```

현재 패키지는 단계적으로 구현 중입니다. 장치별 실행 방법은 `docs/setup/`과
`thing_bringup`의 launch 파일을 기준으로 관리합니다.

## 협업

Jira를 작업 관리의 단일 기준으로 사용합니다. 최신 `develop`에서 Jira 키가 포함된
브랜치를 만들고 MR을 통해서만 병합합니다.

```bash
git switch develop
git pull --ff-only origin develop
git switch -c feature/S15P11C103-69-vision-camera-stream
```

자세한 규칙은 [`CONTRIBUTING.md`](CONTRIBUTING.md)를 참고합니다.
