# XL330 간단 모터 제어

`motor_control.py`는 DYNAMIXEL Protocol 2.0으로 XL330-M288 모터 한 대를 제어한다.
기본 명령은 상태 조회이고, EEPROM(ID·Baud rate·Operating Mode·Position Limit)은
수정하지 않는다.

## 설치

```bash
cd ~/thing_pjt/motor

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

## 사용

프로젝트의 U2D2 연결 경로는 `/dev/serial/by-id/`에서 확인한다.

```bash
ls -l /dev/serial/by-id/

# 상태 조회: 모터를 움직이지 않음
.venv/bin/python motor_control.py --device /dev/ttyUSB0 --id 1 status

# 토크 제어
.venv/bin/python motor_control.py --device /dev/ttyUSB0 --id 1 torque-on
.venv/bin/python motor_control.py --device /dev/ttyUSB0 --id 1 torque-off

# 위치 이동: --arm을 반드시 함께 입력
.venv/bin/python motor_control.py --device /dev/ttyUSB0 --id 1 move \
  --position 2048 --velocity 10 --acceleration 1 --arm
```

`move`는 기본적으로 목표 도착 또는 오류·시간초과 후 Torque OFF를 수행한다.
계속 위치를 유지하려면 `--keep-torque`를 지정한다.

XL330의 Operating Mode는 자동으로 바꾸지 않는다. `status`로 먼저 확인하고,
Mode 3에서는 0–4095 범위, Mode 5에서는 확장 다회전 위치 값을 사용한다.

## 7개 모터 스캔

`motor_config.json`에 3개 U2D2 포트와 모터 ID 1–7이 기록되어 있다. USB 장치
경로가 달라졌다면 이 파일의 `device` 값만 수정한다.

```bash
.venv/bin/python scan_7_motors.py
```

이 스크립트는 ping과 상태 레지스터만 읽으며 어떤 모터 레지스터도 쓰지 않는다.

## 7개 모터 키보드 제어

```bash
.venv/bin/python keyboard_control_7.py
```

기본 Profile Velocity는 `300 raw`(약 68.7 rpm), Acceleration은 `1500 raw`다.
기본 Goal PWM은 `885`, Goal Current는 `1470 mA`다. 필요하면 `--velocity`,
`--acceleration`, `--goal-pwm`, `--goal-current` 옵션으로 바꿀 수 있다.

- `1`–`7`: 제어할 모터 선택
- `←` / `→`: 현재 위치에서 한 단계(-/+ 32 raw) 이동
- `↑` / `↓`: 네 단계(-/+ 128 raw) 이동
- `[` / `]`: 이동 단계 절반 / 두 배
- `t`: 선택 모터의 토크 ON/OFF
- `Space`: 선택 모터 즉시 정지(Torque OFF)
- `s`: 7개 모터 전체 즉시 정지(Torque OFF)
- `r`: 상태 새로고침
- `q`: 종료. 기본적으로 7개 모터 Torque OFF

프로그램 종료 후에도 토크를 유지하려면 `--leave-torque-on`을 추가한다.

## 7개 모터 원위치 복귀

```bash
.venv/bin/python home_all_7.py
```

ID 1부터 7까지 한 축씩 물리적 중앙각도인 `mod 4096 = 2048`로 이동한다.
Mode 4/5에서는 현재 위치와 가장 가까운 2048 동치 좌표를 선택하므로, 예를 들어
현재 위치가 `5480`이면 목표는 `6144`가 된다. 기본적으로 각 모터가 도착하면
Torque OFF한다. 토크를 유지하려면 `--keep-torque`를 사용한다.

## 7개 모터 Current Limit 설정

```bash
# 현재 제한값만 읽기
.venv/bin/python set_current_limit_7.py

# 7개 모두 1470 mA로 변경 및 읽기 검증
.venv/bin/python set_current_limit_7.py --limit 1470 --apply
```

`Current Limit(38)`은 EEPROM 값이라 Torque OFF 상태에서만 변경할 수 있다.
이 값은 최대 상한이며 Mode 5에서 실제 당기는 힘은 `Goal Current(102)` 값도
함께 영향을 준다.

## 7개 모터 Goal PWM / Goal Current 설정

```bash
# 현재 RAM 출력값 읽기
.venv/bin/python set_goal_output_7.py

# Goal PWM=885, Goal Current=1470mA 설정 및 검증
.venv/bin/python set_goal_output_7.py --pwm 885 --current 1470 --apply
```

이 값들은 RAM 설정이며 전원이 꺼지거나 재부팅되면 다시 설정해야 할 수 있다.
