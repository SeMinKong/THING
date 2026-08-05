# 내부 제어 웹 — 값 확정 회신 요청

- 기준: 요구사항 명세서 V7.1 (2026-08-04 14:23 판)
- 회신처: 웹 담당 / **저장소를 열 필요 없습니다.** 이 문서에 답만 적어 주십시오
- 반영: 웹 담당이 `web/frontend/src/config/pending.js` 한 곳을 고칩니다


---

# A. 브릿지 담당 — 4건

## A-1. snapshot 발행 주기 ★ 가장 중요

**질문** — `web_bridge_node` 가 `/ws/robot-state` 로 snapshot 을 몇 Hz 로 보냅니까? 값이 바뀌지 않아도 계속 보냅니까?

**웹의 현재 가정** — 200ms(5Hz), 주기 발행. NFR-13 의 "모터·상태 5Hz" 에서 빌려온 값이며 근거는 없습니다.

**틀리면** — 이 값 하나에서 판정 임계값 4개가 파생됩니다.

| 파생값 | 배수 | 무엇을 판정 |
|---|---|---|
| 3000ms | 15주기 | 장치 up/down (FR-24) |
| 1000ms | 5주기 | 모터 "값이 오래됨" (FR-25) |
| 1600ms | 8주기 | 영상 갱신 멈춤 (FR-20) |
| 2000ms | 10주기 | snapshot 끊김 |

실제가 더 느리면 정상 동작 중에도 화면이 상시 "연결 끊김" 입니다. 특히 **변경 시에만 발행하면 로봇이 멀쩡해도 계속 끊김으로 보입니다.**

```
발행 주기:     200 ms (5 Hz)
주기 발행인가:  [x] 예, 값이 안 바뀌어도 계속 보냄
               [ ] 아니오, 변경 시에만 보냄
```

> **회신 — 브릿지 담당 / 2026-08-05**
> 웹의 가정(200ms, 주기 발행)이 맞습니다. **파생 임계값 4개를 고칠 필요 없습니다.**
> `web_bridge_node`의 `snapshot_period_ms: 200`이며 값이 바뀌지 않아도 계속
> 보냅니다. 구독한 토픽이 하나도 안 와도 `{}`를 채운 snapshot이 나갑니다.
> NFR-13의 "손·7축 10Hz"에는 5Hz가 못 미치지만 NFR-13은 Should이고, 지금 올리면
> 웹의 임계값 4개를 같이 고쳐야 해서 유지했습니다. 나중에 필요하면 이 항목으로
> 다시 알립니다.

> 이 항목은 **자동으로도 답이 나옵니다.** 웹이 실제 도착 간격을 20표본 재서 가정값과
> 50% 이상 어긋나면 콘솔에 실측값을 띄웁니다. 통합 첫 10초면 확인됩니다.
> 다만 "주기 발행인가" 는 자동으로 알 수 없으니 회신이 필요합니다.

---

## A-2. 서비스 ack 왕복 상한

**질문** — `execute_gesture`, `execute_sequence`, `start_recording`, `stop_recording`, `set_mimic_result` 의 ack 왕복 상한은 몇 ms 입니까?

**확인한 것** — 명세는 STOP 의 Guard ACK 만 규정합니다(기본 300ms·최대 500ms, FR-35).

**웹의 현재 가정** — 2000ms.

**틀리면** — 웹은 ack 가 올 때까지 버튼을 잠급니다(중복 클릭 방지). 상한이 지나면 ack 없이 풉니다. 실제가 더 느리면 잠금이 먼저 풀려 사용자가 다시 누르고 `motion_active` 로 거부됩니다.

```
ack 왕복 상한:  2000 ms
서비스별로 다르면 각각: 전부 동일 (2000ms)
```

> **회신 — 브릿지 담당 / 2026-08-05**
> 웹의 가정(2000ms)이 맞습니다. `service_timeout_ms: 2000` 하나로 5개 서비스와
> Sequence 액션에 같이 적용합니다. 초과하면 `service_timeout`(액션은
> `action_timeout`) 실패 ACK를 보냅니다. **ACK 없이 끝나는 경우는 없습니다.**
> 서버가 아예 없으면 기다리지 않고 즉시 `service_unavailable`을 보냅니다.

---

## A-3. FR-35 타이밍 값을 snapshot 에 실어 줄 수 있습니까?

**질문** — 아래 값을 snapshot 에 함께 보내 줄 수 있습니까? Raspberry Pi 가 읽는 YAML 값입니다.

`hold_recovery_activity_ms` `hold_recovery_max_gap_ms` `safe_deadline_ms` `stop_settle_ms` `fault_clear_stable_ms` `estop_release_stable_ms` `owner_lease_timeout_ms`

**왜** — FR-27 은 HOLD 에서 "자동복귀 조건 300ms, 최대 gap 100ms, 총 1000ms SAFE deadline" 을 안내하라고 합니다. 웹은 지금 명세 조문의 숫자를 **사본 14개**로 들고 화면에 씁니다. FR-41 이 이 값들을 YAML 소관으로 두었으므로 **YAML 을 바꾸면 웹 화면이 거짓말을 합니다.**

보내 주시면 사본 14개가 통째로 사라집니다.

```
[ ] 가능 — snapshot 에 실어 보냄 (키 이름: ______________)
[x] 불가 — 웹이 명세 조문 값을 계속 사본으로 사용
```

> **회신 — 브릿지 담당 / 2026-08-05**
> 불가합니다. 그 값들은 Raspberry Pi의 YAML(FR-41)이고 Web Bridge는 Jetson에서
> 돕니다. 남의 장치 설정 파일을 읽을 수 없습니다. 브리지가 숫자를 하드코딩해서
> 보내면 YAML이 바뀔 때 웹이 거짓말을 하므로 더 나쁩니다.
>
> 대신 **"어느 절차를 따라야 하는지"는 보냅니다.** `safety_state.reset_allowed`가
> FR-35의 "SAFE·FAULT·ESTOP에서만 복구 요청" 규칙을 반영합니다. 숫자 없이
> 경로만 필요하면 이걸 쓰십시오.
>
> 사본 14개를 줄이고 싶다면 `hand_confidence_min`·`hand_loss_debounce_ms`
> ·`hand_reacquire_stable_ms` 3개는 `landmarks.confidence_min`
> ·`landmarks.reacquire_stable_ms`로 이미 실어 보내고 있으니 그건 사본을 지울 수
> 있습니다.

---

## A-4. `ControlState.sequence_running` 의 범위

**질문** — `ExecuteSequence` 액션 실행 중에만 `true` 입니까, `ExecuteGesture` 실행 중에도 `true` 입니까?

**확인한 것** — FR-22 가 "MANUAL Gesture 는 동작·초기 유지시간이 끝나면 종료하며 마지막 자세를 무기한 재발행하지 않는다", "실행 중 새 요청 거부" 로 바뀌었습니다. 그런데 `sequence_running` 이 Gesture 를 포함하는지는 여전히 6.3절 한 줄뿐입니다.

**틀리면** — "시퀀스 실행 중" 문구가 제스처 실행 중에도 뜨거나 그 반대입니다. 웹은 이 필드로 버튼을 잠그지 않으므로(ack 기반) 동작에는 영향이 없습니다.

```
[ ] Sequence 액션 전용
[ ] Gesture 실행 중에도 true
```

> **회신 — 브릿지 담당 / 2026-08-05**
> 이 필드는 `command_manager`(윤정민, S15P11C103-102)가 채웁니다. 브리지는
> `ControlState.msg` 원문을 그대로 전달만 하므로 제가 정할 수 없습니다.
> **윤정민에게 문의하십시오.** 값이 정해지면 브리지 수정 없이 그대로 반영됩니다.

---

# B. 제어·기구 담당 — 1건

## B-1. gesture·sequence 의 `speed_limit` ★ 통합 시험 전 필수

**질문** — 아래 값이 YAML 에 있습니까? 있다면 값이 무엇입니까?

**웹의 현재 임시값**

| 대상 | 임시값 |
|---|---|
| `open` · `fist` | 1.0 |
| `pinch` · `cylindrical_grasp` | 0.5 |
| `countdown` · `scissors_rock_paper` | 0.5 |


**틀리면** — 다른 항목과 성격이 다릅니다. 나머지는 화면 표시만 부정확해지지만, 이건 **로봇에 그대로 전달되는 명령값**입니다. `command_guard` 가 0.0~1.0 범위는 검증하지만 "이 동작에 적절한 속도인지" 는 검증하지 않습니다. 파지 속도는 텐던 장력에 직결됩니다.

```
YAML 에 있습니까:  [ ] 예  [ ] 아니오

있다면 값 (YAML 최대속도 대비 비율):
  open ______  fist ______  pinch ______  cylindrical_grasp ______
  countdown ______  scissors_rock_paper ______

없다면 권장값과 근거: ________________________________
```

---

# C. 기획 담당 — 1건

## C-1. `StartRecording.srv` 의 `label`

**질문** — 녹화 시작 시 라벨 입력 UI 가 필요합니까? 필요하다면 무엇을 담습니까?

**웹의 현재 동작** — 빈 문자열을 보냅니다.

**틀리면** — 불필요하면 현 상태로 문제없습니다. 필요한데 안 만들면 세션 구분이 Session ID(63비트 숫자)뿐이라 EC2 포털에서 어느 세션인지 알아보기 어렵습니다.

```
[x] 불필요 — 빈 문자열 유지
[ ] 필요 — 담을 내용: ________________________________
          (입력 UI 추가는 별도 작업으로 산정합니다)
```

> **회신 — 브릿지 담당 / 2026-08-05**
> 2주 MVP에서는 빈 문자열 유지가 맞습니다. `label`은 `StartRecording.srv`에
> 있지만 EC2 공개 데이터 계약(6.5절 metadata JSON schema v1)에 `label` 필드가
> **없습니다.** 지금 입력해도 EC2 포털에서 보이지 않습니다. 브리지는 받은 값을
> 그대로 전달하므로 나중에 필요해지면 웹만 고치면 됩니다.

---

# D. 스펙 담당 — 1건

## D-1. 동시 접속 클라이언트 식별

**질문** — 탭을 두 개 열거나 두 대의 노트북에서 접속했을 때를 어떻게 처리합니까?

**틀리면** — 한쪽 탭에서 STOP 을 누르면 다른 탭도 제어권을 잃습니다. 다른 탭 사용자는 아무것도 안 했는데 제어가 끊긴 것으로 보입니다. **시연 중 실수로 탭을 두 개 열면 발생합니다.** 웹에서는 해결할 수 없습니다.

```
[x] 브릿지가 /ws/robot-state 연결을 하나만 허용 (가장 간단)
[ ] ControlState 에 클라이언트 식별자 추가 (.msg 변경 필요 — V7 은 additive 2건만 허용)
[ ] 운용 규칙으로만 관리 (탭 하나만 열기)
```

> **회신 — 브릿지 담당 / 2026-08-05**
> **구현 완료.** 두 번째 연결은 close code `1013`(try again later)과
> reason `single connection only`로 거절합니다. 첫 연결이 끊기면 바로 다음
> 연결을 받습니다.
>
> `.msg` 변경 안을 고른 이유가 없습니다. V7은 `SafetyState.RESET`과
> `MotorState.torque_enabled` 두 additive delta만 허용합니다(FR-30).
>
> 웹 쪽 요청: 두 번째 탭에서 `1013`을 받으면 재접속 백오프를 돌리지 말고
> "다른 탭에서 이미 사용 중"으로 안내해 주십시오. 계속 재시도하면 첫 탭이
> 끊기는 순간 어느 탭이 잡을지 예측할 수 없습니다.

---

# E. 이번 갱신으로 새로 생긴 것 — 2건

## E-1. 거부 사유 8종이 추가됐습니다 (브릿지 담당)

이번 판이 FR-18 에 기록 서비스 거부 사유를, FR-34 에 lease 사유를 새로 넣었습니다. **웹의 안내 문구표에 없는 값들입니다.**

| 서비스 | 신규 사유 |
|---|---|
| `StartRecording` | `not_mimic_mode` `start_failed` `already_recording` `result_pending` |
| `StopRecording` | `not_recording` `session_mismatch` `stop_failed` |
| `SetControlMode` | `owner_lease_expired` |

**현재 동작** — 8종 반영 완료로 각 사유에 맞는 안내 문구가 표시됩니다. (표에 없는 사유가 오면 여전히 원문 노출 + `ACK_UNKNOWN_REASON_*` 진단.)

**웹이 할 일** — **완료.** 8종이 `REJECT_REASON` 과 `describeReason()` 문구표에 반영됨. 남은 것은 아래 확인 하나뿐입니다.

```
브릿지가 이 사유들을 ack 의 reason 에 그대로 실어 보냅니까?
  [x] 예 — .srv 응답의 reason 문자열을 그대로 전달
  [ ] 아니오 — 다른 문자열로 변환 (변환표: ______________)
```

> **회신 — 브릿지 담당 / 2026-08-05**
> 그대로 전달합니다. 변환표 없습니다. `.srv` 응답의 `reason`(Trigger는
> `message`)을 손대지 않고 넣습니다. 비어 있을 때만 `service_rejected`
> (Trigger는 `reset_rejected`)로 채웁니다.
>
> **단, 브리지가 자체적으로 만드는 사유가 따로 있습니다.** 전부 `web_`
> 접두어를 붙였으니 접두어로 구분하십시오. 문구표에 추가가 필요한 신규 3종:
>
> | reason | 언제 | 웹이 할 일 |
> | --- | --- | --- |
> | `web_preempted_by_stop` | STOP이 들어와 실행 전에 폐기됨 | **버튼 잠금 해제 + 정상 취소로 안내** |
> | `web_superseded` | 대기 중 lease 갱신이 최신 것으로 교체됨 | 무시 가능 (`track` 안 하는 요청) |
> | `web_queue_overflow` | 요청을 너무 빨리 보냄 | 재시도 가능 |
>
> 기존 `web_malformed_request`·`web_unknown_type`·`web_bridge_error`
> ·`service_*`·`action_*`도 같은 계열입니다. 전체 목록은
> `web/docs/interfaces-bridge.md` 1.6절에 동결했습니다.

## E-2. 정상 STOP 뒤 재획득 조건이 생겼습니다 (브릿지 담당)

> FR-35: 정상 STOP 뒤 제어권 재획득은 STOP system timestamp 보다 source stamp 가 새로운
> **RESET 과 그보다 새 stamp 의 READY 를 순서대로 관측한 뒤에만** 허용한다.

**웹의 현재 동작** — `SafetyState` 가 READY 이면 획득 버튼을 엽니다. RESET→READY 순서를 웹이 확인하지는 않습니다. 로봇이 거부하므로 안전에는 문제가 없지만, **사용자가 버튼을 눌렀는데 거부되는** 상황이 생길 수 있습니다.

**웹이 할 일** — 둘 중 어느 쪽이 나은지 알려 주십시오.

```
[x] 그대로 두기 — 눌러 보고 거부되면 안내 (지금 동작)
[ ] 웹도 순서를 추적 — STOP 뒤에는 RESET 을 본 다음 READY 를 봐야 버튼을 연다
    (이때 거부 사유가 무엇으로 옵니까: stop_in_progress)
```

> **회신 — 브릿지 담당 / 2026-08-05**
> 그대로 두는 쪽을 권합니다. 이유는 두 가지입니다.
>
> 1. `command_manager` 코드를 확인했습니다. STOP 뒤 재획득 차단 구간에서는
>    `stop_in_progress`로 거부하고 현재 ControlState를 유지합니다. **안전에는
>    문제가 없고** 사용자에게는 한 번의 거부 안내로 끝납니다.
> 2. 웹이 RESET→READY 순서를 추적하려면 `SafetyState`의 stamp를 비교해야 하는데,
>    5Hz snapshot으로는 500ms짜리 RESET 구간을 놓칠 수 있습니다. 놓치면 버튼이
>    영구히 안 열립니다. 거부되는 쪽이 갇히는 쪽보다 낫습니다.
>
> 거부 사유는 `stop_in_progress`입니다. `REJECT_REASON`에 없으므로 문구표에
> 추가해 주십시오. FR-37 8종 목록에도 빠져 있는데 `docs/interfaces.md`의
> "제어 mode·owner와 STOP 계약" 절에 이미 정의돼 있습니다.

---

# 해소된 항목

## A-5. FR-24 장치 상태 세분화 — 회신 불필요

이전 회신표에서 `camera`·`MediaPipe`·`hand_target`·`MJPEG` 개별 상태를 브릿지가 줄 수 있는지 물었습니다. **NFR-09 가 웹의 범위를 확정하면서 해소됐습니다.**

> NFR-09: Laptop 내부망 웹에서 **손 검출, mode·owner, 연결, SafetyState, RecordingState 를 확인**하고
> **상세 원인은 diagnostics·로그에서 확인한다.**

세분화는 웹이 아니라 diagnostics 의 몫입니다. 웹의 현재 5종(`jetson` `rpi` `ros2` `camera` `motor`)으로 충분합니다. 브릿지에 `/thing/diagnostics` 파싱을 요구하지 않겠습니다.

---

# 회신 후

웹 담당이 `pending.js` 의 키를 고치고 **회신 출처를 같은 자리에 기록**합니다.

```js
BRIDGE_SNAPSHOT_PERIOD_MS: 100,
// status  확정
// 회신    브릿지 담당 / 2026-08-05
// 원문    "10Hz 고정 주기 발행. 값이 안 바뀌어도 계속 보냄"
```

나중에 "누가 이렇게 정했나" 를 코드만 보고 답할 수 있습니다.

| 항목 | 회신 후 작업량 |
|---|---|
| A-1 A-2 B-1 | 키 하나 수정 |
| A-4 E-2 | 표시 문구 |
| A-3 | `SPEC` 14개 제거 후 snapshot 에서 읽기 (중간) |
| C-1 | 라벨 입력 UI (별도 산정) |
| E-1 | 회신과 무관하게 진행. 변환표가 있으면 반영 |
| D-1 | 웹 작업 없음 (브릿지 또는 스펙) |


---

# 회신 완료 (2026-08-05, 브릿지 담당)

A-1 A-2 A-3 A-4 C-1 D-1 E-1 E-2 회신했습니다. A-5는 이미 해소 처리됐습니다.

브릿지 쪽 후속 조치 요약입니다.

| 항목 | 브릿지 조치 | 웹 조치 |
| --- | --- | --- |
| A-1 | 200ms 유지 | 없음 |
| A-2 | 2000ms 유지 | 없음 |
| A-3 | 불가 | 명세 값 사본 유지. 단 hand 관련 3개는 snapshot에서 읽을 수 있음 |
| A-4 | 해당 없음 | 윤정민에게 문의 |
| C-1 | 없음 | 없음 |
| D-1 | 연결 1개 제한 구현 완료 | `1013` 수신 시 재접속 백오프 대신 안내 |
| E-1 | 그대로 전달 | `web_preempted_by_stop` 등 3종 문구 추가 |
| E-2 | 없음 | `stop_in_progress` 문구 추가 |

계약 전문은 `web/docs/interfaces-bridge.md`에 동결했습니다. ROS 2 쪽 단일 기준은
`docs/interfaces.md`의 WebSocket 절입니다.
