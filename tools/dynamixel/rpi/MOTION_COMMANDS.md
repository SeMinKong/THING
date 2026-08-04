# 손 동작 실행 명령

아래 명령은 라즈베리파이의 `~/thing_pjt/motor` 디렉터리에서 실행한다.
동시에 다른 모터 제어 프로그램을 실행하지 않는다.

## 동시 파지

검지·중지·약지·소지를 동시에 굽힌 다음, 엄지(ID 2)를 마지막에 감싼다.
2초 유지 후 엄지를 먼저 펴고 나머지 네 손가락을 편 뒤 전체 토크를 끈다.

```bash
python grasp_control_7.py \
  --thumb-delay 0.05 \
  --hold-seconds 2 \
  --arm
```

## 손가락 웨이브

검지(ID 4) → 중지(ID 3) → 약지(ID 1) → 소지(ID 7) 순서로 굽히고,
반대 순서로 돌아오는 웨이브를 한 번 실행한다.

```bash
python wave_control_7.py \
  --strength 1.0 \
  --cycles 1 \
  --bend-time 0.8 \
  --hold-time 0.5 \
  --release-time 0.8 \
  --finger-delay 0.4 \
  --velocity 300 \
  --acceleration 1500 \
  --goal-current 1470 \
  --goal-pwm 885 \
  --arm
```

두 프로그램 모두 실행 중 `Ctrl+C`를 누르면 펼친 위치로 복귀한 뒤 전체
토크를 끈다. 현재 스크립트에는 50°C 자동 차단이 포함되어 있지 않으므로
감독 상태에서 짧게 실행한다.
