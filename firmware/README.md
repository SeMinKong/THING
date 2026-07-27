# Firmware

MVP의 DYNAMIXEL 제어는 Raspberry Pi의 ROS 2 `thing_hardware` 패키지가 담당합니다.
현재 MVP에는 별도 마이크로컨트롤러 펌웨어가 없습니다.

이 디렉터리는 향후 독립 안전 MCU 또는 전원 제어 펌웨어를 실제로 채택할 때만
사용합니다. MCU를 추가할 경우 역할, 통신 프로토콜, 전원 차단 권한과 시험 절차를
요구사항 및 `docs/architecture.md`에 먼저 반영해야 합니다.

- `src/`: 향후 승인된 MCU 펌웨어
- `tests/`: 하드웨어 독립 안전 시험
