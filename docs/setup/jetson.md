# Jetson Orin Nano 설정

- JetPack 6.2 / Ubuntu 22.04
- ROS 2 Humble
- RGB 카메라, OpenCV, MediaPipe
- `thing_vision`, `thing_web_bridge`, `thing_logger`(logger 노드·exporter)와
  격리 uploader 데몬(`thing_logger.uploader`, logger와 별도 프로세스 —
  [배치 결정](../decisions/2026-08-05-logger-uploader-jetson-배치.md))

카메라 번호·해상도·FPS는 `thing_bringup/config/vision.yaml`로 관리합니다.
프레임 획득 timestamp와 `camera_color_optical_frame`을 모든 후속 메시지에서
일관되게 사용합니다.

MediaPipe Python 패키지는 Jetson 아키텍처에서 공식 wheel 지원 여부가 달라질 수
있으므로 설치 방법과 검증된 버전을 `vision/requirements.txt`에 고정하기 전에
실제 장치에서 확인합니다.

## Logger와 uploader 실행

uploader 비밀값은 `/etc/thing-uploader.env`에 `KEY=VALUE` 형식으로 둡니다.
Logger와 격리 uploader 프로세스는 다음 명령 하나로 함께 실행합니다.

```bash
ros2 launch thing_bringup logger.launch.py
```

다른 env 파일을 사용할 때만 launch 인자를 지정합니다.

```bash
ros2 launch thing_bringup logger.launch.py \
  uploader_env_file:=/path/to/thing-uploader.env
```
