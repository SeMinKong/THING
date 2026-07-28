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
