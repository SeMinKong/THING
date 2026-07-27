# Jetson Orin Nano 설정

- JetPack 6.2 / Ubuntu 22.04
- ROS 2 Humble
- RGB 카메라, OpenCV, MediaPipe
- `thing_vision`, `thing_web_bridge`와 선택적 `thing_logger`

카메라 번호·해상도·FPS는 `thing_bringup/config/vision.yaml`로 관리합니다.
프레임 획득 timestamp와 `camera_color_optical_frame`을 모든 후속 메시지에서
일관되게 사용합니다.

MediaPipe Python 패키지는 Jetson 아키텍처에서 공식 wheel 지원 여부가 달라질 수
있으므로 설치 방법과 검증된 버전을 `vision/requirements.txt`에 고정하기 전에
실제 장치에서 확인합니다.
