# 실제 손 동작 코드 사용 순서

`hand_motion_7_motors.py`는 무부하 모터 왕복 시험이 아니라, 보정된 힘줄 끝점을 이용해 실제 손 포즈를 만드는 실행 코드다.

## 먼저 알아둘 점

- ID 1~7의 기본 역할은 아직 확인되지 않은 가정이다. 현재 설정은 `엄지 굽힘, 검지, 중지, 약지, 소지, 엄지 대립, 엄지 벌림` 순서다.
- `2048`은 모터의 전기적 중앙값일 뿐 손의 펼침 위치가 아니다.
- 코드가 토크를 켜기 전에 현재 위치를 목표 위치로 기록하므로 시작 순간의 급격한 이동을 막는다.
- Current-based Position Mode를 사용한다. 보정 전류는 80 mA, 기본 동작 전류는 90~100 mA, 영구 하드웨어 전류 상한은 최대 150 mA다.
- 현재 하드웨어 전류 상한이 150 mA보다 높으면 150 mA로 낮춘다. 이미 더 낮다면 올리지 않는다.
- 온도 55 °C, 하드웨어 오류, 지속 과전류가 발생하면 전체 토크를 해제한다.
- 이동 중 목표 위치를 따라가지 못하면서 전류가 계속 높으면 접촉으로 판단해 해당 축을 현재 위치에 정지시킨다.
- `Ctrl+C`, SSH 종료(SIGHUP), SIGTERM, 예외 상황에서 토크 해제를 시도한다.
- 실동작 전 전용 잠금을 잡고, 이미 실행 중인 기존 모터 스크립트가 있으면 시작을 거부한다.
- 종료 시 Torque Off를 모터별 최대 3회 재시도하고 레지스터를 다시 읽어 확인한다.

## 1. 계획만 확인

아래 명령은 포트를 열지 않고 모터도 움직이지 않는다.

```bash
cd ~/motor
source ~/dxl-env/bin/activate
python hand_motion_7_motors.py plan
```

## 2. ID와 실제 축 배치 확인

전원을 끈 상태에서 각 모터의 힘줄을 직접 추적한다. `hand_motion_config.json`의 `axes`에서 ID별 `role`과 `label`이 실제 배치와 다르면 수정한다.

다음 일곱 역할은 각각 한 번만 있어야 한다.

```text
thumb_flex
index_flex
middle_flex
ring_flex
little_flex
thumb_opposition
thumb_abduction
```

배치가 실제 연결과 일치할 때만 확인한다.

```bash
python hand_motion_7_motors.py confirm-map --confirm MAP
```

## 3. 한 축씩 실제 끝점 보정

처음에는 반드시 한 축만 보정한다. 손과 물체를 구동부에서 치우고, 즉시 전원을 끌 수 있게 준비한다.

```bash
python hand_motion_7_motors.py calibrate --id 1 --arm --confirm CALIBRATE
```

보정 화면 명령:

```text
+ 또는 + 25   raw 증가 방향(CCW, 정면 기준)
- 또는 - 25   raw 감소 방향(CW, 정면 기준)
open           현재 실제 위치를 완전히 편/비활성 끝점으로 기록
closed         현재 실제 위치를 안전한 최대 굽힘/활성 끝점으로 기록
active         closed와 동일(엄지 대립·벌림 축에서 사용)
status         현재 상태 확인
save           두 끝점을 설정 파일에 원자적으로 저장하고 종료
quit           저장하지 않고 토크를 해제하고 종료
```

`+ 25`와 `- 25`는 약 0.27 mm씩 줄을 움직이는 작은 조그다(줄 중심 유효 반지름 약 7 mm 가정). 처음에는 open↔closed 간격 200~300 raw(약 2.15~3.22 mm) 정도에서 동작을 확인하고 필요할 때만 천천히 넓힌다.

굽힘 축은 `open=완전히 편 자세`, `closed=안전한 최대 굽힘`이다. 엄지 대립·벌림 축은 `open=비활성/중립`, `active=안전한 최대 활성`으로 기록한다. 내부 저장 이름은 모든 축에서 `closed_raw`이지만 포즈에서는 0.0~1.0 활성 비율로만 사용한다.

ID 1부터 7까지 반복한다.

## 4. 실제 포즈 목표를 Dry Run으로 확인

```bash
python hand_motion_7_motors.py show
```

모든 축의 보정 여부와 `open`, `fist`, `point`, `pinch`, `cylinder`의 절대 raw 목표가 출력된다. 포트는 열리지 않는다.

## 5. 실제 손 동작 시퀀스 실행

모든 축의 매핑과 끝점을 확인한 뒤 실행한다.

```bash
python hand_motion_7_motors.py show --arm --confirm HAND
```

실행 순서는 다음과 같다.

```text
open → fist → open → point → open → pinch → open → cylinder → open
```

한 포즈만 실행하려면:

```bash
python hand_motion_7_motors.py pose pinch
python hand_motion_7_motors.py pose pinch --arm --confirm HAND
```

첫 줄은 Dry Run, 두 번째 줄만 실제 동작이다. 실제 실행 로그는 `~/motor/logs/hand/`에 CSV로 남는다.

## 중지

- 동작 중 `Ctrl+C`: 전체 토크 해제
- SSH 연결 종료: 전체 토크 해제 시도
- 이상한 소리, 줄의 비틀림, 프레임 휨이 보이면 소프트웨어를 기다리지 말고 모터 전원을 끈다.
- 텐던이 연결된 뒤에는 `continuous_7_motors.py`, `control_7_motors.py`, `move_all_to_center.py`, 기존 `finger_demo_7_motors.py`를 실행하지 않는다.
