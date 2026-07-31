# Linux 모터 제어기 배포

## 1. 파일 복사

저장소의 `tools/dynamixel/` 디렉터리를 Linux 제어기의 작업 디렉터리로 복사한다.

예:

```bash
mkdir -p ~/motor
cp tools/dynamixel/hand_motion_7_motors.py ~/motor/
cp tools/dynamixel/scan_7_motors.py ~/motor/
cp tools/dynamixel/hand_motion_config.example.json \
   ~/motor/hand_motion_config.json
```

이미 보정한 `hand_motion_config.json`이 있으면 덮어쓰지 않는다.

Raspberry Pi 현장에서 사용한 개별 점검 스크립트는 `tools/dynamixel/rpi/`에 별도로 보관한다.
이 디렉터리에는 3개 U2D2의 실제 `/dev/serial/by-id/` 경로가 기록되어 있으므로, 다른 장비로
복사할 때에는 `motor_config.json`의 포트와 ID를 먼저 확인한다. 이 스크립트는 통합 손 동작
제어기를 대체하지 않으며, 통신·출력·원위치 점검 용도로 사용한다.

## 2. Python 환경

```bash
python3 -m venv ~/dxl-env
source ~/dxl-env/bin/activate
python -m pip install --upgrade pip
python -m pip install dynamixel-sdk==4.0.5
```

검증 환경은 Python 3.13.5와 DYNAMIXEL SDK 4.0.5다.

## 3. 직렬 포트 확인

```bash
ls -l /dev/serial/by-id/
```

`hand_motion_config.json`의 장치 경로가 실제 `/dev/serial/by-id/` 값과 같은지 확인한다. `scan_7_motors.py`는 이 설정 파일을 함께 사용한다. 사용자에게 직렬 포트 접근 권한이 있어야 한다.

## 4. 하드웨어 I/O 없는 확인

```bash
cd ~/motor
source ~/dxl-env/bin/activate
python -m py_compile hand_motion_7_motors.py scan_7_motors.py
python hand_motion_7_motors.py plan
python hand_motion_7_motors.py show
```

## 5. 모터 검색

```bash
python scan_7_motors.py
```

7개가 모두 검색된 뒤에만 매핑과 보정을 진행한다.

## 6. 런타임 파일

프로그램은 다음 파일을 생성할 수 있다.

- `.hand_motor_control.lock`
- `logs/hand/*.csv`
- `__pycache__/`

이 파일들은 Git에 올리지 않는다.

상세 보정·동작 절차는 [hand-motion.md](hand-motion.md)를 따른다.
