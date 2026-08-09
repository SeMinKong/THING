# thing — 내부망 관제·제어 웹

텐던 구동 로봇 손을 카메라 기반 손동작(MediaPipe)으로 모방 제어하거나 웹 버튼으로 조작하는 프로젝트의 내부망 관제·제어 웹입니다.

운용자는 폐쇄 내부망 랩의 노트북에서 자기 손을 비추는 MJPEG 영상을 보며, 1초마다 갱신되는 제어권으로 7개 DYNAMIXEL 을 움직입니다. 이 화면의 단 하나의 일은 **"지금 조작해도 되는가" 를 시선을 옮기지 않고 알게 하는 것**입니다.

기준: 요구사항 명세서 V7.1

(2026-08-07 18:07 추가)
기존 기능에서 SafetyBanner.jsx 관련 기능을 제거하였습니다.
기능은 전부 정상 동작하지만, test 관련 작업은 정상적으로 이루어지지 않을 수 있습니다.
---

## 어디에 있는가

브라우저는 Jetson 의 `thing_web_bridge` ROS 2 노드에 직접 붙습니다. 중계 서버가 없습니다.

```
[내부망]                                      [AWS EC2 · 별도 저장소]

Laptop
└─ web/frontend  (본 저장소, Vite + React)
      │  WebSocket /ws/robot-state  +  MJPEG (별도 포트)
      ▼
Jetson Orin Nano
└─ camera, mediapipe, hand_target, mjpeg_streamer, web_bridge_node
      │  ROS 2 DDS
      ▼
Raspberry Pi 5
├─ thing-control        command_manager · command_guard · safety_manager · motor_driver_node · logger
└─ thing-data-uploader ──── HTTPS ────▶ EC2 데이터 포털
      │  U2D2
      ▼
7 × DYNAMIXEL XL330-M288-T → 텐던 로봇 손
```

**Web Bridge 는 이 저장소 밖입니다** (`thing_ws/src/thing_web_bridge`). 인터페이스 계약은 [`docs/interfaces-bridge.md`](docs/interfaces-bridge.md) 에 있습니다.

---

## 세 가지 원칙

### 웹은 안전 판단 주체가 아니다

범위·속도·timeout·E-Stop 판정은 전부 Raspberry Pi 가 합니다. 웹이 꺼져도 로봇의 안전 판단은 계속됩니다 (NFR-16). 웹은 조기 형식 검증과 표시만 합니다.

### 브릿지에는 가공을 요구하지 않는다

브릿지는 ROS 2 제어 경로 위에 있어 변경 위험이 큽니다. 파생 로직은 웹이 가져갑니다.

| | 담당 |
|---|---|
| `.msg` 원문 dump 를 snapshot 에 얹기 | 브릿지 |
| uint8 enum → 문자열, 시각 필드 해석 | 웹 |
| 장치 연결 상태·hand-loss latch 파생 | 웹 (브릿지가 주면 그것 우선) |
| 버튼 잠금 판정 | 웹 (ack 기반) |

브릿지에 요구하는 추가 필드는 `control_state`, `recording` 두 개뿐입니다.

### 미수신과 관측값을 구분한다

프런트가 관대해질수록 브릿지 누락이 안 보입니다. `control_state` 가 없으면 화면이 "제어권 없음" 으로 보이는데, 이는 로봇의 **정상 상태와 똑같이 생겼습니다.** 그래서 `*Known` 플래그로 "아직 못 받았다" 를 드러내고, 그 상태에서는 조작을 막습니다 (fail-closed).

---

## 실행

```bash
cd web/frontend
npm install

cp env.txt .env.local        # 장비별 실제 주소. 커밋하지 않음
npm run dev
```

| 명령 | 하는 일 |
|---|---|
| `npm run dev` | 개발 서버 |
| `npm run build` | 배포 산출물 |
| `npm run preview` | 산출물 미리보기 |
| `npm test` | vitest 151건 |
| `npm run lint` | eslint |
| `npm run mock` | 로봇 없이 브릿지 흉내 |

Node 는 Vite 8 요구사항(20.19+ 또는 22.12+)을 따릅니다. 폰트는 번들에 포함되어 있어 외부 네트워크가 없어도 됩니다.

### 주소 설정

`.env` 의 기본값은 **의도적으로 비어 있습니다.** 잘못된 기본값이 남으면 "영상이 안 나온다" 로만 보이고 원인을 못 찾습니다. 비어 있으면 기동 시 콘솔이 오류로 알립니다.

| 변수 | 필수 | 예 |
|---|---|---|
| `VITE_WS_URL` | ● | `ws://192.168.0.10:8000/ws/robot-state` |
| `VITE_MJPEG_STREAM_URL` | ● | `http://192.168.0.10:8080/stream/overlay` |
| `VITE_MJPEG_RAW_STREAM_URL` | | 비우면 원본 전환 버튼이 사라짐 |
| `VITE_DEV_WS_TARGET` | | `npm run dev` 프록시가 붙을 WS 주소(vite.config.js). dev 전용, 빌드엔 영향 없음. 비우면 `ws://localhost:8000` |

`VITE_WS_URL` 을 비운 채 배포하지 마십시오. `ws://<현재 호스트>/ws/robot-state` 로 폴백하는데, 이를 받아 주는 것은 `npm run dev` 의 프록시뿐입니다.

---

## 로봇 없이 확인하기

`tools/mock-bridge.mjs` 가 Jetson `thing_web_bridge` 를 대신합니다. Node 만 있으면 되고 추가 의존성이 없습니다.

```bash
npm run mock                    # ws://localhost:8000/ws/robot-state
npm run mock -- --no-derived    # 선택 필드를 빼고 웹의 파생 경로 확인
```

키 입력(Enter)으로 상황을 만듭니다.

| 키 | 상황 | 확인할 것 |
|---|---|---|
| `s` | SafetyState 8상태 순환 | 머리 색 전환, 복구 안내 (FR-27) |
| `r` | 판정 대기 강제 생성 | 성공·실패 판정 UI (FR-26) |
| `l` | 손 신뢰도를 0.70 아래로 | "미검출로 처리됩니다" (8.1절) |
| `h` | 손 미검출 토글 | hand-loss 안내와 자동 재개 금지 |
| `o` | LOCAL owner 가 제어권 획득 | `owner_conflict`, 획득 차단 (FR-34) |
| `d` | 모터 연결 단절 | 장치 표시, 모터 통신 오류 (FR-24) |

mock 은 `messageProtocol.js` 를 import 하므로 상수가 따로 자라지 않습니다. 다만 **mock 은 개발용 픽스처이지 계약의 근거가 아닙니다.** 계약은 `docs/interfaces-bridge.md`, 계약 경계 검증은 `src/test/bridgeContract.test.jsx` 입니다.

---

## 통합 시험 중 문제가 생기면

브라우저 콘솔을 보십시오. 조용히 실패하는 경로를 전부 없앴습니다.

```
▼ [진단:브릿지] snapshot 에 control_state 가 없습니다 (3회째)
   증상   owner 를 알 수 없어 제어권을 인정하지 않습니다(fail-closed).
   조치   ControlState.msg 원문을 control_state 키로 실어 보내세요.
   근거   FR-19 / interfaces-bridge.md 1.2
```

앞머리에 **담당**이 붙습니다 (`브릿지 / 웹 / 설정 / 스펙 / 로봇`). 콘솔에 오류가 보여도 누구에게 말할지 모르면 방치되기 때문입니다. 같은 문제는 30초에 한 번만 출력하고 누적 횟수를 표시합니다 — snapshot 이 초당 여러 번 오는데 매번 찍으면 진짜 문제가 묻힙니다.

| 콘솔 명령 | 내용 |
|---|---|
| `__diag()` | 지금까지 잡힌 문제 전체 (담당·횟수·증상·조치·근거·실제값) |
| `__pending()` | 현재 미확정 값과 파생 임계값 |

`snapshot` 발행 주기는 **자동 측정**됩니다. 실제 도착 간격을 20표본 재서 가정값과 50% 이상 어긋나면 실측값과 고칠 위치를 알려 줍니다.

---

## 화면

**머리 영역 전체가 안전 상태 색입니다.** 운용자는 영상과 자기 손을 보고 있지 상태 글자를 읽고 있지 않습니다. ESTOP 이면 화면 위쪽이 통째로 진홍이 됩니다. 색이 주변시로 먼저 도달하는 것이 목적입니다.

머리 안에는 판정 한 줄("조작할 수 있습니다"), 근거 한 줄, 그리고 8상태 트랙이 있습니다. **상태가 바뀌면 표시자가 트랙 위를 이동합니다** — 어디서 어디로 갔는지가 거리로 전달됩니다. 트랙 순서는 uint8 상수값이 아니라 FR-35 위상 순서이며, 굵은 구분선 뒤부터는 `/thing/reset_safety` 가 필요합니다.

| 화면 | 내용 |
|---|---|
| 개요 | 모드 선택 두 개. 로봇 상태는 머리가 답하므로 되풀이하지 않습니다 |
| 모방 | 영상·손 검출·7논리축·hand-loss·기록 UI·모터 상태 |
| 조작 | 영상·제어권 획득·gesture 4종·sequence 2종·정지·모터 상태 |

모방↔조작 전환 시 **영상이 유지됩니다.** MJPEG 연결이 끊겼다 붙으면 몇 프레임을 놓칩니다.

조작 화면의 「미리보기」는 canonical gesture 4종을 MediaPipe 21 랜드마크 골격으로 보여 줍니다. 보내기 전에 "원통 파지" 와 "집기" 가 어떻게 다른지 확인하기 위한 것이며, **로봇에 아무것도 보내지 않습니다.**

계측값은 spring 으로 이어집니다. 툭툭 갈아 끼우면 5Hz 로 들어오는 데이터가 깜빡이는 것처럼 보입니다.

---

## 구조

```
web/
├─ docs/
│  ├─ interfaces-bridge.md    브릿지와의 인터페이스 계약
│  └─ pending-decisions.md    담당별 회신표
└─ frontend/
   ├─ .env / env.txt          주소 설정 (실제 값은 .env.local 에)
   ├─ tools/mock-bridge.mjs
   └─ src/
      ├─ config/
      │  ├─ messageProtocol.js   WS 메시지 스키마·enum 정규화·파생
      │  ├─ pending.js           근거 없는 값 단일 출처
      │  ├─ diagnostics.js       통합 시험용 진단
      │  └─ commandPresets.js    FR-22 버튼 정의
      ├─ context/HandSocketContext.jsx
      ├─ components/             Header, StatusBar, ~~SafetyBanner~~, CameraStream,
      │                          MotorStatusPanel, ModeGate, GesturePreview
      ├─ pages/                  Home, VisionMode, OrderMode
      ├─ ui/                     Sheet, Num
      └─ test/
         └─ bridgeContract.test.jsx   브릿지 계약 경계 회귀
```

React + Vite + Tailwind v4 + Motion. 한국어가 화면의 대부분이라 Pretendard 를 쓰고, 계측값은 JetBrains Mono 로 등폭 자릿수를 맞춥니다.

### 근거 없는 값은 한 곳에 모읍니다

명세서·`.msg`·`.srv` 로 결정되지 않는 값은 코드 어디에도 두지 않고 `src/config/pending.js` 한 곳에서만 정의합니다. 확정되면 그 파일만 고치면 됩니다.

세 구획으로 나뉩니다.

| 구획 | 성격 |
|---|---|
| `PENDING` | 근거 없음. 동작하게 하려고 넣은 임시값 |
| `SPEC` | 명세에 숫자가 있으나 FR-41 이 YAML 을 단일 기준으로 정함. 표시용으로만 사용 |
| `CONTRACT_ASSUMPTIONS` | 값이 아니라 구조에 대한 가정. 런타임에서 소비하지 않는 기록 |

특히 `BRIDGE_SNAPSHOT_PERIOD_MS` 하나에서 판정 임계값 4개가 파생됩니다. 실제 주기를 알면 그 값만 고쳐도 장치 상태·모터 stale·영상 정지·연결 끊김 판정이 동시에 맞춰집니다.

목록과 담당은 [`docs/pending-decisions.md`](docs/pending-decisions.md) 에 있습니다.

---

## 시험

```bash
npm test
```

| 파일 | 건수 | 범위 |
|---|---|---|
| `config/messageProtocol.test.js` | 35 | 상수·enum·검증 함수 |
| `context/HandSocketContext.test.jsx` | 45 | 요청 envelope, lease, 거부 사유, 복구 |
| `test/screens.test.jsx` | 51 | 화면 렌더·표시 판정 |
| `test/bridgeContract.test.jsx` | 20 | **브릿지 계약 경계** |

`bridgeContract.test.jsx` 는 fixtures 를 쓰지 않고 **손으로 만든 payload** 로 경계를 고정합니다. 나머지는 fixtures 가 만든 snapshot 을 쓰는데, fixtures 는 프런트가 원하는 필드를 항상 채워 주므로 "프런트가 정한 것" 과 "명세서가 정한 것" 의 차이를 검증하지 못합니다. 고정하는 경계는 다음과 같습니다.

1. 6.4절 고정 6필드만 오는 브릿지에서도 화면이 갱신되는가
2. `.msg` 원문 dump(enum 이 정수, `header.stamp`)를 정규화하는가
3. 숫자 `session_id` 를 감지하고 되돌려 보내지 않는가
4. 선택 필드가 없을 때 파생이 동작하는가
5. 실제 화면 preset 으로 버튼을 눌렀을 때 실제로 전송되는가

---

## 웹이 하지 않는 것

- 임의 ROS topic·motor ID 전송 (NFR-20)
- 7논리축 값 전송 — `ExecuteGesture.srv` 에 해당 필드가 없고 목표값은 YAML 소관 (FR-41)
- rosbag2·EC2 SQLite 접근 (FR-26)
- 안전 판정 (NFR-16)
- 제어권 자동 획득·재획득 — 재연결·복구 후에도 사용자가 직접 (NFR-15, NFR-23)

---

## 남은 항목

| 항목 | 근거 | 상태 |
|---|---|---|
| `pending.js` 임시값 11개 | [pending-decisions.md](docs/pending-decisions.md) | 회신 대기 |
| `.msg`/`.srv` 최신본 확인 | 같은 문서 3장 | 완료 (pending.js 2026-08-04 반영) |
| 기록 서비스 거부 사유 8종 | FR-18 / FR-34 | 완료 (REJECT_REASON·describeReason 반영) |
| `MotorState.torque_enabled` 표시 | FR-30 | 완료 (MotorStatusPanel 열 추가) |
| 이벤트 회전 로그 | NFR-22 (Should) | 미구현 |
| 동시 접속 클라이언트 식별 | FR-34 에 식별자 없음 | 웹에서 해결 불가 |
