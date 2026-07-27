# Raspberry Pi 5 설정

기본 구성은 Ubuntu 24.04 호스트와 Ubuntu 22.04 기반 ROS 2 Humble 컨테이너입니다.

컨테이너에는 다음 접근이 필요합니다.

- U2D2 USB 장치
- 프로젝트 내부 ROS 2 네트워크
- `thing_ws` 또는 배포된 install workspace
- 장치별 로컬 DYNAMIXEL 설정

모터 안전 제한, timeout과 비상정지는 웹 서버나 Jetson 프로세스가 종료돼도
독립적으로 동작해야 합니다. 컨테이너 재시작 정책을 적용하더라도 모터를 자동으로
RUN 상태로 복구해서는 안 됩니다.

실제 Dockerfile과 compose 설정은 U2D2 장치 경로 및 네트워크 모드를 장치에서
검증한 뒤 별도 Jira 작업으로 추가합니다.
