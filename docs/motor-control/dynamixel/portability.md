# 플랫폼 및 이식성

## 실제 모터 제어

현재 실제 제어 코드는 Linux 기반 모터 제어기를 전제로 한다.

- `/dev/serial/by-id/` 직렬 장치
- `/proc`를 이용한 기존 모터 프로세스 검색
- `fcntl.flock` 단일 프로세스 잠금
- `SIGHUP`, `SIGTERM`, 터미널 TTY

따라서 Windows에서는 CAD 작업과 오프라인 테스트는 가능하지만, `hand_motion_7_motors.py --arm` 실동작은 지원 대상으로 검증하지 않았다.

## 장치별 수정 항목

`tools/dynamixel/hand_motion_config.json`에서 다음 값을 실제 장치에 맞춘다.

- 각 버스의 `/dev/serial/by-id/` 경로
- 모터 ID와 물리 축 역할
- 축별 위치 한계
- 축별 Goal Current
- 보정된 `open_raw`, `closed_raw`

`scan_7_motors.py`는 같은 설정 파일의 버스와 ID를 읽으므로 별도의 포트 목록을 수정할 필요가 없다.

## 검증된 현재 환경

- Python 3.13.5
- DYNAMIXEL SDK 4.0.5
- Protocol 2.0
- Baud rate 57,600 bps
- XL330-M288-T, model number 1200
- HNX330-N101 호른
- 3개 FTDI TTL 버스, 모터 ID 1~7

Python 소스 문법은 3.10 이상을 요구한다. 실제 하드웨어 동작은 현재 제어기 환경에서만 확인했다.

## 권한

Linux 사용자가 직렬 장치에 접근할 수 있어야 한다. 배포 환경에 따라 `dialout` 그룹 등의 권한 설정이 필요할 수 있다. 정확한 그룹과 udev 규칙은 운영체제에서 `/dev/serial/by-id/` 링크가 가리키는 장치를 확인한 뒤 설정한다.

## 별도 확인이 필요한 항목

현재 저장소에는 다음 환경 정보가 확정 사양으로 기록되어 있지 않다.

- 전원공급기 모델과 정격
- 배선 굵기 및 퓨즈
- 비상 정지 회로
- 공통 GND 구성

추측값을 문서화하지 말고 실제 하드웨어를 확인한 뒤 추가한다.
