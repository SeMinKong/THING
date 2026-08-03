# Ubuntu 개발 PC 설정

## 필수 도구

```bash
sudo apt update
sudo apt install git git-lfs python3-colcon-common-extensions python3-rosdep
git lfs install
```

ROS 2 Humble은 Ubuntu 22.04 환경을 기준으로 합니다. Ubuntu 24.04에서는 프로젝트의
Raspberry Pi용 Ubuntu 22.04 컨테이너 또는 별도 22.04 환경을 사용합니다.

## 저장소와 의존성

```bash
git clone --branch develop \
  https://lab.ssafy.com/s15-webmobile3-sub1/S15P11C103.git
cd S15P11C103
git lfs pull
source /opt/ros/humble/setup.bash
cd thing_ws
rosdep install --from-paths src --ignore-src -r -y
```

## 빌드와 시험

```bash
colcon build --symlink-install
source install/setup.bash
colcon test
colcon test-result --verbose
```
