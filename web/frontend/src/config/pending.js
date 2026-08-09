// ============================================================================
// 미확정 값 단일 출처
// ----------------------------------------------------------------------------
// 이 파일에 있는 값은 전부 "요구사항 명세서만으로는 결정할 수 없는 것" 이다.
// 코드 어디에도 이 값들을 복사해 두지 않는다. 확정되면 여기 한 곳만 고친다.
//
// ── 규칙 ────────────────────────────────────────────────────────────────────
// 1. 명세서·.msg·.srv 에 근거가 있는 값은 여기 두지 않는다. 근거 위치에 둔다.
// 2. 근거가 없는데 코드가 값을 필요로 하면 여기에 넣고 status 를 적는다.
// 3. 새 미확정 값을 여기 추가할 때는 반드시 먼저 팀에 고지한다.
//    조용히 추가하면 이 파일이 "임의값 창고" 가 되어 존재 의미가 사라진다.
//
// ── status 값 ───────────────────────────────────────────────────────────────
//   "미확정"   근거가 아예 없다. 동작하게 하려고 넣은 임시값이다.
//   "명세복제" 명세서에 숫자가 있지만 FR-41 이 YAML 을 단일 기준으로 정했다.
//              웹은 표시용으로만 쓴다. YAML 이 바뀌면 여기가 거짓말을 한다.
//   "확인대기" .msg / .srv 초기본에서 읽었으나 최신본 확인이 필요하다.
// ============================================================================

// ---------------------------------------------------------------------------
// 1. 미확정 — 근거 없음
// ---------------------------------------------------------------------------

const UNCONFIRMED = {
  /**
   * 브릿지가 snapshot 을 보내는 주기.
   *
   * 이 값 하나가 화면의 "정상 / 끊김 / 오래됨" 판정 전부를 좌우한다.
   * 아래 *_PERIODS 배수와 곱해져 임계값이 된다. 실제 주기를 알면 배수는
   * 그대로 두고 이 값만 고치면 네 판정이 동시에 맞춰진다.
   *
   * 임시값 200ms(5Hz)는 NFR-13 의 "모터·상태 5Hz" 를 빌려 온 것이지,
   * 브릿지가 그 주기로 보낸다는 근거는 없다.
   */
  BRIDGE_SNAPSHOT_PERIOD_MS: 200,

  /** 조각이 몇 주기 동안 안 바뀌면 그 장치가 멈춘 것으로 볼 것인가 (FR-24). */
  SECTION_STALE_PERIODS: 15,

  /** 모터 값이 몇 주기 동안 안 바뀌면 "값이 오래됨" 으로 볼 것인가 (FR-25). */
  MOTOR_STALE_PERIODS: 5,

  /** landmark 가 몇 주기 동안 안 바뀌면 "영상 갱신 멈춤" 으로 볼 것인가 (FR-20). */
  CAMERA_STALE_PERIODS: 8,

  /** 경과 시간을 다시 계산하는 주기. 화면 갱신 부하와 반응성의 절충. */
  RECHECK_PERIOD_MS: 1000,

  /** snapshot 이 몇 주기 동안 아예 안 오면 "연결 끊김" 으로 볼 것인가. */
  NO_SNAPSHOT_PERIODS: 10,

  /**
   * 요청을 보내고 ack 를 기다리는 최대 시간. 지나면 버튼 잠금을 푼다.
   *
   * STOP 의 Guard ACK(stop_barrier_ack)는 causal ACK 이라 웹이 관측할 고정 상한이
   * 없고, Gesture·Sequence·Recording 의 ack 지연 상한도 규정돼 있지 않다. 그래서
   * 웹이 방어적으로 이 상한을 둔다.
   */
  ACK_TIMEOUT_MS: 2000,

  /**
   * Gesture 별 speed_limit.
   *
   * `ExecuteGesture.srv` 가 호출자에게 요구하는 필드라 값을 보내야 하는데,
   * 이 숫자들의 출처가 없다. FR-41 이 gesture 7축 목표를 YAML 소관으로 뒀으므로
   * speed_limit 도 거기 있을 가능성이 크다.
   *
   * 파지 속도는 텐던 장력에 직결된다. YAML 값과 다르면 하드웨어에 부담이 갈 수
   * 있으므로 통합 시험 전에 반드시 확인해야 한다.
   */
  GESTURE_SPEED_LIMIT: {
    open: 1.0,
    fist: 1.0,
    cylindrical_grasp: 0.5,
    pinch: 0.5,
  },

  /** Sequence 별 speed_limit. 위와 같은 이유로 미확정. */
  SEQUENCE_SPEED_LIMIT: {
    countdown: 0.5,
    scissors_rock_paper: 0.5,
  },

  /**
   * `StartRecording.srv` 의 `string label` 에 무엇을 넣을 것인가.
   *
   * FR-26 은 웹이 이 서비스를 호출한다고만 하고 label 의 용도·형식을 정하지
   * 않았다. 빈 문자열로 둔다. UI 에서 입력받게 할지도 미정.
   */
  START_RECORDING_LABEL: "",

  /** 재연결 백오프. 사용자 경험 문제라 계약과 무관하지만 근거는 없다. */
  RECONNECT_BASE_DELAY_MS: 1000,
  RECONNECT_MAX_DELAY_MS: 10000,
};

// ---------------------------------------------------------------------------
// 2. 명세복제 — 명세에 숫자가 있으나 YAML 이 단일 기준 (FR-41)
// ---------------------------------------------------------------------------
// 웹은 이 값들로 판정하지 않는다. 안내 문구에 숫자를 보여줄 때만 쓴다.
// 판정 주체는 전부 Raspberry Pi 다 (NFR-16).
//
// 문구에 직접 박지 말고 반드시 여기서 읽어 쓴다. 이전 구현은 SafetyBanner
// 문구에 "안정 시간(500ms)" 를 문자열로 박아 두어 YAML 이 바뀌면 화면이
// 거짓말을 하는 상태였다.

const SPEC_MIRRORED = {
  /** FR-11 / FR-34. 웹이 실제로 이 주기로 갱신 요청을 보낸다. */
  CONTROL_RENEW_PERIOD_MS: 1000,

  /** FR-11. owner lease 만료. */
  OWNER_LEASE_TIMEOUT_MS: 3000,

  /** 8.1절. confidence 하한. 검출 표시 판정에 쓴다. */
  HAND_CONFIDENCE_MIN: 0.7,

  /** 8.1절. 미검출 지속 시 발행 중단까지. hand-loss latch 근사에 쓴다. */
  HAND_LOSS_DEBOUNCE_MS: 150,

  /** 8.1절 / FR-27. 유효 재검출 인정 시간. */
  HAND_REACQUIRE_STABLE_MS: 300,

  /** FR-27 / FR-35. HOLD 자동복귀에 필요한 검증 activity 연속 시간. */
  HOLD_RECOVERY_ACTIVITY_MS: 300,

  /** FR-27 / FR-35. HOLD 자동복귀 activity 사이 허용 최대 공백. */
  HOLD_RECOVERY_MAX_GAP_MS: 100,

  /** FR-11. 마지막 hardware-forwarded 명령 뒤 HOLD 진입까지. */
  COMMAND_HOLD_MS: 300,

  /** FR-11 / FR-35. 마지막 hardware-forwarded 명령 뒤 SAFE 상승까지. */
  SAFE_DEADLINE_MS: 1000,

  /** FR-35. RESET 최소 유지 시간. 모터 이동 없이 torque OFF 재확인(현재 자세 유지). */
  STOP_SETTLE_MS: 500,

  /** FR-35. SAFE·FAULT 원인 해소 뒤 안정 시간. */
  FAULT_CLEAR_STABLE_MS: 1000,

  /** FR-35. E-Stop 물리 해제 뒤 안정 시간. */
  ESTOP_RELEASE_STABLE_MS: 500,
};

// ---------------------------------------------------------------------------
// 3. 확인대기 — 계약 가정 (런타임에서 소비하지 않는 기록)
// ---------------------------------------------------------------------------
// 값이 아니라 구조에 대한 가정이다. 코드가 참조하지는 않지만, 무엇을 가정하고
// 있는지 한 곳에서 보이도록 여기 남긴다. 확인되면 항목을 지운다.

// 2026-08-04 thing_interfaces / safety_manager.md 확정으로 제거:
//   C-1 SetControlMode 필드명 = requested_mode/requested_owner (SetControlMode.srv)
//   C-3 uint8 상수값 = .msg 선언 순서 (SafetyState.msg 등 wire값 일치)
//   C-4 sequence_running = Sequence 단일 슬롯 점유 (interfaces.md is_sequence_running)
//   C-6 MotorState.torque_enabled = bool 존재·의미 확정 (MotorState.msg)
export const CONTRACT_ASSUMPTIONS = [
  {
    id: "C-2",
    item: "enum 을 웹이 symbolic string 으로 보내고 브릿지가 uint8 로 매핑",
    assumed: "mode / owner / result 전부 문자열 전송",
    basis: "합의. .srv 는 uint8(requested_mode 등)이라 변환 주체가 필요하고 브릿지로 정했다. 브릿지 실동작은 통합 시 확인",
    affects: "set_control_mode, stop, set_mimic_result",
  },
  {
    id: "C-5",
    item: "FR-24 의 camera·MediaPipe·hand_target·MJPEG 개별 연결 상태",
    assumed: "미구현. 브릿지 diagnostics 없이는 파생 불가",
    basis: "FR-24 Must 문장은 Jetson·RPi·ROS2·카메라·모터 5종이며 이는 충족",
    affects: "FR-24 구현 힌트 줄 미충족",
  },
  {
    id: "C-7",
    item: "hardware_error 비트 해석",
    assumed: "0 이 아니면 오류. 과전류·과온 비트를 구분하지 않는다",
    basis: "DYNAMIXEL 오류 비트표가 프로젝트 문서에 없음",
    affects: "FR-25 모터별 오류 상세",
  },
  {
    id: "C-8",
    item: "동시 접속 클라이언트 식별",
    assumed: "owner 가 WEB 하나뿐이라 탭 2개면 둘 다 제어권 보유로 인식 (ControlState.msg 에 식별자 없음 확인)",
    basis: "FR-34 에 클라이언트 식별자 없음. 웹에서 해결 불가 (D-1 결정 대기)",
    affects: "시연 중 탭 중복 시 STOP 이 다른 탭에도 적용",
  },
];
// ---------------------------------------------------------------------------
// 내보내기
// ---------------------------------------------------------------------------

export const PENDING = UNCONFIRMED;
export const SPEC = SPEC_MIRRORED;

/** BRIDGE_SNAPSHOT_PERIOD_MS 에서 파생되는 임계값. 주기가 확정되면 함께 맞춰진다. */
export const THRESHOLD = {
  /** 조각이 이 시간 동안 안 바뀌면 발행 주체가 멈춘 것으로 본다. */
  get SECTION_STALE_MS() {
    return PENDING.BRIDGE_SNAPSHOT_PERIOD_MS * PENDING.SECTION_STALE_PERIODS;
  },
  /** 모터 값이 이 시간 동안 안 바뀌면 "값이 오래됨". */
  get MOTOR_STALE_MS() {
    return PENDING.BRIDGE_SNAPSHOT_PERIOD_MS * PENDING.MOTOR_STALE_PERIODS;
  },
  /** landmark 가 이 시간 동안 안 바뀌면 "영상 갱신 멈춤". */
  get CAMERA_STATE_STALE_MS() {
    return PENDING.BRIDGE_SNAPSHOT_PERIOD_MS * PENDING.CAMERA_STALE_PERIODS;
  },
  /** 경과 시간 재계산 주기. 배수가 아니라 그대로 쓴다. */
  get RECHECK_PERIOD_MS() {
    return PENDING.RECHECK_PERIOD_MS;
  },
  /** snapshot 이 이 시간 동안 아예 안 오면 "연결 끊김". */
  get NO_SNAPSHOT_MS() {
    return PENDING.BRIDGE_SNAPSHOT_PERIOD_MS * PENDING.NO_SNAPSHOT_PERIODS;
  },
};

/**
 * 개발 중 확인용. 콘솔에서 `__pending()` 으로 현재 미확정 항목을 볼 수 있다.
 * 통합 시험 때 "이 화면의 이 숫자는 무슨 근거냐" 를 즉시 답하기 위한 것이다.
 */
if (typeof window !== "undefined" && import.meta.env?.DEV) {
  window.__pending = () => ({
    미확정: UNCONFIRMED,
    파생임계값: {
      SECTION_STALE_MS: THRESHOLD.SECTION_STALE_MS,
      MOTOR_STALE_MS: THRESHOLD.MOTOR_STALE_MS,
      CAMERA_STATE_STALE_MS: THRESHOLD.CAMERA_STATE_STALE_MS,
      NO_SNAPSHOT_MS: THRESHOLD.NO_SNAPSHOT_MS,
    },
    명세복제: SPEC_MIRRORED,
    계약가정: CONTRACT_ASSUMPTIONS,
  });
}
