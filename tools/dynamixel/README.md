# DYNAMIXEL XL330 PC 제어 도구

XL330-M288-T 7축의 연결 상태를 확인하고, 안전한 보정·동작 계획을 수행하는 독립 실행 도구다. ROS 2 `thing_hardware` 통합 전의 PC·Raspberry Pi 하드웨어 점검에 사용하며, 운영 제어 경로를 대체하지 않는다.

## 설치

```bash
python3 -m venv dxl-env
source dxl-env/bin/activate
python -m pip install -r tools/dynamixel/requirements.txt
cp tools/dynamixel/hand_motion_config.example.json \
  tools/dynamixel/hand_motion_config.json
```

실제 장치별 포트, ID, 관절 역할, 개폐 위치값은 로컬 설정 파일에만 기록한다. 이 파일과 CSV 텔레메트리는 Git에서 제외된다.

## 기본 점검

```bash
python tools/dynamixel/scan_7_motors.py
python tools/dynamixel/hand_motion_7_motors.py plan
python tools/dynamixel/hand_motion_7_motors.py show
python -m unittest tests/dynamixel/test_hand_motion_offline.py -v
```

실제 토크 인가 전에는 모든 축의 역할과 이동 범위를 확인하고, 비상 정지와 전원 차단 수단을 준비한다.

## 관련 문서

- [실제 손 동작 코드 사용 순서](../../docs/motor-control/dynamixel/hand-motion.md)
- [배포 절차](../../docs/motor-control/dynamixel/deployment.md)
- [안전 지침](../../docs/motor-control/dynamixel/safety.md)
- [이식성 및 장치 설정](../../docs/motor-control/dynamixel/portability.md)
- [프로젝트 상태](../../docs/motor-control/dynamixel/project-status.md)

## Raspberry Pi 현장 제어 스크립트

`rpi/`에는 Raspberry Pi의 `~/thing_pjt/motor`에서 실제 7축 XL330·3개 U2D2 연결에 사용한
점검·제어 스크립트를 보관한다. 통합 손 동작 제어기와는 별도의 현장 점검 도구이며, 다음 작업을
포함한다.

- 7개 모터 통신·상태 조회와 단일 모터 제어
- 개별/전체 Torque OFF가 가능한 키보드 제어
- 모든 축의 2048 기준 원위치 복귀 및 출력 제한값 설정

`rpi/motor_config.json`에는 해당 장비의 `/dev/serial/by-id/` U2D2 경로가 들어 있다. 다른
제어기에서 사용하기 전에는 경로와 모터 ID를 확인하고 수정해야 한다. 상세 사용법은
[`rpi/README.md`](rpi/README.md)를 따른다.
