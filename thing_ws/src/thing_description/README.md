# Thing V5.2.1 Robot Description

`thing_description`은 Thing V5.2.1 오른손의 시뮬레이션 및 시각화를 위한 ROS 2 로봇 설명 패키지입니다.

## 구성

- `urdf/thing_hand.urdf`: 17개 링크와 16개 회전 관절의 기구학 모델
- `config/tendon_map.yaml`: 7개 구동축의 텐던-관절 연동 비율
- `meshes/visual/`: 시각화용 메시 17개
- `meshes/collision/`: URDF 충돌 형상용 메시 17개

## 구동축

검지·중지·약지·소지 굽힘, 엄지 굽힘, 엄지 대립, 엄지 벌림의 7축으로 구성합니다.
텐던 연동 비율은 V5.2.1의 관절 가동 범위를 바탕으로 한 초기값이며, 실제 장착 후 스풀 반경·엔코더 영점·텐던 장력·관절각 관계를 재보정해야 합니다.

## 모델 적용 범위

- 기준 자세: V5.2.1 Blender 조립 모델의 열린 자세(q=0)
- 관절 범위: V5 컨트롤러에서 추출한 ROM
- 질량·관성: PLA 유사 밀도 기반 추정값이므로, 힘 제어 또는 물리 검증 전 실측값으로 교체 필요
- 충돌 메시: 현재 시각화 메시와 동일합니다. Isaac Sim 대규모 시뮬레이션 전 단순화 또는 convex decomposition을 권장합니다.

## 설치 확인

```bash
cd thing_ws
colcon build --packages-select thing_description
source install/setup.bash
```
