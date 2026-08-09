# DYNAMIXEL 및 U2D2 설정

## 사전 확인

1. 모터 전원과 Raspberry Pi 전원을 분리합니다.
2. 비상정지가 모터 구동 전원을 실제로 차단하는지 확인합니다.
3. 한 번에 하나의 U2D2와 한 모터로 ID·baud rate를 검증합니다.
4. 중복 ID가 없는 것을 확인한 뒤 7개 모터를 연결합니다.

## USB 권한

장치 이름은 시스템마다 다를 수 있습니다.

```bash
ls -l /dev/ttyUSB* /dev/serial/by-id/ 2>/dev/null
groups
sudo usermod -aG dialout "$USER"
```

그룹 변경 후 로그아웃·로그인합니다. `/dev/serial/by-id` 경로가 제공되면
`/dev/ttyUSB0`보다 안정적이므로 우선 사용합니다.

## 설정 기록

측정한 결과만 `thing_bringup/config/motors.yaml`에 기록합니다.

- 논리축
- motor ID
- U2D2 포트
- 회전 방향
- 최소·최대 raw 위치
- 안전 초기 위치
- 전류·온도 제한

처음에는 낮은 speed limit과 current limit으로 한 축씩 시험합니다.
