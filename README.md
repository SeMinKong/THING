# Human-Mimetic Tendon Robot Hand

카메라로 사용자의 손 자세를 인식하고, 7개의 DYNAMIXEL 서보로 구동되는
underactuated tendon 로봇손이 동작을 실시간으로 모방하는 프로젝트입니다.
전체 계획은 3주이며 V6.3 동결 시점의 남은 2주 동안 Must 기능 통합을 우선합니다.

최종 요구사항의 단일 기준은
[`docs/requirements/요구사항 명세서 V6.3.md`](docs/requirements/요구사항%20명세서%20V6.3.md)입니다.

## MVP

- MediaPipe 기반 한 손의 21개 landmark 검출
- 엄지 굽힘·대립·벌림과 네 손가락 굽힘으로 구성된 7논리축 생성
- 7논리축과 XL-330-288T 7개를 1:1로 연결
- ROS 2 Humble 기반 Jetson–Raspberry Pi 무선 제어
- MIMIC·MANUAL·TELEOP 제어권 중재와 안전 제한
- 편 손, 주먹, 원통 파지, 엄지–검지 집기
- Laptop 내부망 React 관제·제어 웹
- MediaPipe landmark, HandCommand, MotorStatus를 rosbag2로 기록
- rosbag2를 metadata JSON·HandCommand CSV·MotorStatus CSV로 변환
- EC2 공개 포털에서 READY 세션 조회와 정확히 세 파일 다운로드

Isaac Sim/Lab, VLA, imitation learning과 관절별 독립 다축 제어는 MVP 제외 범위입니다.

## 실행 장치

| 장치 | 주요 역할 |
| --- | --- |
| Jetson Orin Nano | 카메라, MediaPipe, 7축 목표 생성, MJPEG, Web Bridge, Logger·exporter·uploader |
| Raspberry Pi 5 | 명령 중재·검증, 안전, DYNAMIXEL |
| Laptop | 내부망 관제·제어 웹, 개발, TELEOP |
| AWS EC2 | 공개 데이터 포털, SQLite와 세 파일 영속 저장 |
| XL-330-288T × 8 | 7개 활성 구동축, 1개 예비 모터 |

Raspberry Pi는 Ubuntu 24.04 호스트에서 Ubuntu 22.04 기반 ROS 2 Humble
컨테이너를 사용하는 구성을 기본으로 합니다. Docker는 Jetson과 Raspberry Pi에만
사용하고 Docker Compose는 Raspberry Pi에서만 사용합니다.

## 저장소 구성

- `thing_ws/`: ROS 2 인터페이스·비전·제어·하드웨어·로거·bringup
- `web/internal-control/frontend/`: Laptop 내부망 Vite+React 제어 웹
- `EC2/thing_database_web/`: 팀원이 계속 사용하는 EC2 포털 단일 원본
- `deploy/`: Jetson·Raspberry Pi 실행 환경
- `mechanical/`: CAD, STL, 조립 및 출력 자료
- `electronics/`: BOM, 회로, 배선 및 안전 전원
- `vision/`: 비전 실험과 캘리브레이션 자료
- `tests/`: 재현 가능한 시험 절차와 결과
- `docs/`: 요구사항, 아키텍처, 인터페이스 및 개발환경 문서

EC2 전달본은 현재 개발 중인 프로토타입입니다. 저장소에 포함됐다는 사실만으로
V6.3의 Bearer Token·세 파일·READY·HTTPS 계약이 구현됐다고 간주하지 않습니다.

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
GitLab Runner는 사용하지 않으며 변경 영역별 로컬 검증 결과를 MR에 기록합니다.
