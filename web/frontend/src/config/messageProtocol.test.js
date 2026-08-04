// frontend/src/config/messageProtocol.test.js
//
// 프로토콜 상수·헬퍼가 요구사항 명세서 V6.3 과 일치하는지 고정한다.
// 이 값들은 backend/thing_bridge/consumers.py 와 쌍으로 유지해야 한다.

import { describe, expect, it } from "vitest";

import {
  ACQUIRE_ALLOWED_STATES,
  CANONICAL_GESTURES,
  CONTROL_MODE,
  CONTROL_OWNER,
  GESTURE_ALIASES,
  HAND_AXES,
  HAND_AXIS_KEYS,
  OPERATION_ALLOWED_STATES,
  RECORDING_BUSY_STATES,
  RECORDING_STATE,
  REJECT_REASON,
  RESET_ALLOWED_STATES,
  SAFETY_STATES,
  SEQUENCE_IDS,
  STOP_MODE,
  STOP_OWNER,
  TIMING,
  UNSAFE_TO_OPERATE_STATES,
  WEB_OWNER,
  WEB_SELECTABLE_MODES,
  describeReason,
  CONNECTION_STATE,
  emptyConnectionStatus,
  isDeviceDown,
  isDeviceUsable,
  isSnapshot,
  isValidAxisValues,
  isValidSpeedLimit,
} from "./messageProtocol";
import { PENDING, SPEC } from "./pending";

import { snapshot, validAxes } from "../test/fixtures";

describe("FR-30 7논리축", () => {
  it("배열이 아니라 고정 필드 7개다", () => {
    expect(HAND_AXIS_KEYS).toEqual([
      "thumb_flex", "thumb_opp", "thumb_abd",
      "index_flex", "middle_flex", "ring_flex", "little_flex",
    ]);
    expect(HAND_AXES).toHaveLength(7);
  });

});

describe("FR-23 / FR-32 축 값 검증", () => {
  it("0.0~1.0 유한값을 통과시킨다", () => {
    expect(isValidAxisValues(validAxes)).toBe(true);
    expect(isValidAxisValues(Object.fromEntries(
      HAND_AXIS_KEYS.map((k) => [k, 1]),
    ))).toBe(true);
  });

  it("범위 밖·NaN·무한대·누락을 거부한다", () => {
    expect(isValidAxisValues({ ...validAxes, thumb_flex: 1.1 })).toBe(false);
    expect(isValidAxisValues({ ...validAxes, thumb_flex: -0.1 })).toBe(false);
    expect(isValidAxisValues({ ...validAxes, thumb_flex: NaN })).toBe(false);
    expect(isValidAxisValues({ ...validAxes, thumb_flex: Infinity })).toBe(false);
    const partial = { ...validAxes };
    delete partial.little_flex;
    expect(isValidAxisValues(partial)).toBe(false);
    expect(isValidAxisValues({})).toBe(false);
    expect(isValidAxisValues(null)).toBe(false);
  });
});

describe("FR-32 speed_limit", () => {
  it("0.0 초과 1.0 이하만 허용한다", () => {
    expect(isValidSpeedLimit(0.5)).toBe(true);
    expect(isValidSpeedLimit(1)).toBe(true);
    expect(isValidSpeedLimit(0)).toBe(false);
    expect(isValidSpeedLimit(1.1)).toBe(false);
    expect(isValidSpeedLimit(-0.1)).toBe(false);
    expect(isValidSpeedLimit(NaN)).toBe(false);
    expect(isValidSpeedLimit(null)).toBe(false);
  });
});

describe("FR-34 mode·owner", () => {
  it("develop 상수를 그대로 쓴다", () => {
    expect(Object.keys(CONTROL_MODE)).toEqual(["DISABLED", "MIMIC", "MANUAL", "TELEOP"]);
    expect(Object.keys(CONTROL_OWNER)).toEqual(["NONE", "WEB", "LOCAL"]);
  });

  it("웹은 WEB owner 로 MIMIC·MANUAL 만 선택한다", () => {
    expect(WEB_SELECTABLE_MODES).toEqual(["MIMIC", "MANUAL"]);
    expect(WEB_OWNER).toBe("WEB");
    // TELEOP 은 OWNER_LOCAL 전용이라 웹 선택 대상이 아니다
    expect(WEB_SELECTABLE_MODES).not.toContain("TELEOP");
  });

  it("정지·해제 조합은 DISABLED + NONE", () => {
    expect([STOP_MODE, STOP_OWNER]).toEqual(["DISABLED", "NONE"]);
  });
});

describe("FR-35 SafetyState", () => {
  it("8개 상태를 쓴다 (V7 RESET 포함)", () => {
    // V7 FR-30 승인 변경: "SafetyState.msg: 기존 상수 뒤 uint8 RESET=7 추가.
    // 기존 상수 번호와 필드 순서는 변경하지 않는다."
    // 배열 순서가 곧 uint8 상수값이므로 RESET 은 반드시 맨 뒤다.
    expect(SAFETY_STATES).toEqual([
      "INIT", "READY", "RUN", "HOLD", "SAFE", "FAULT", "ESTOP", "RESET",
    ]);
    expect(SAFETY_STATES.indexOf("RESET")).toBe(7);
  });

  it("RESET 은 정상 제어 상태다 — 조작 허용도, reset_safety 대상도 아니다", () => {
    // FR-35: RESET 은 명시적 STOP 의 settle·torque-off 확인 구간이고,
    // /thing/reset_safety 는 "HOLD·READY·RUN·RESET에서는 거부한다".
    expect(OPERATION_ALLOWED_STATES).not.toContain("RESET");
    expect(RESET_ALLOWED_STATES).not.toContain("RESET");
    expect(UNSAFE_TO_OPERATE_STATES).not.toContain("RESET");
    expect(ACQUIRE_ALLOWED_STATES).not.toContain("RESET");
  });

  it("획득은 READY 에서만", () => {
    expect(ACQUIRE_ALLOWED_STATES).toEqual(["READY"]);
  });

  it("FR-38 조작은 READY|RUN 에서만", () => {
    expect(OPERATION_ALLOWED_STATES).toEqual(["READY", "RUN"]);
    // INIT·HOLD 에서 조작이 열리면 안 된다
    expect(OPERATION_ALLOWED_STATES).not.toContain("INIT");
    expect(OPERATION_ALLOWED_STATES).not.toContain("HOLD");
  });

  it("reset_safety 는 SAFE·FAULT·ESTOP 에서만", () => {
    expect(RESET_ALLOWED_STATES).toEqual(["SAFE", "FAULT", "ESTOP"]);
    // FR-35: HOLD 에서는 이 서비스를 쓰지 않는다
    expect(RESET_ALLOWED_STATES).not.toContain("HOLD");
  });

  it("위험 상태 목록", () => {
    expect(UNSAFE_TO_OPERATE_STATES).toEqual(["SAFE", "FAULT", "ESTOP"]);
  });
});

describe("FR-18 RecordingState", () => {
  it("7개 상태", () => {
    expect(Object.keys(RECORDING_STATE)).toHaveLength(7);
    expect(RECORDING_STATE.INTERRUPTED).toBe("INTERRUPTED");
  });

  it("기록 중 판정 상태", () => {
    expect(RECORDING_BUSY_STATES).toEqual(["STARTING", "RECORDING", "STOPPING"]);
  });
});

describe("FR-38 canonical gesture", () => {
  it("Must 4종", () => {
    expect(CANONICAL_GESTURES).toEqual(["open", "fist", "pinch", "cylindrical_grasp"]);
  });

  it("cylinder_grasp 오타가 없다", () => {
    expect(CANONICAL_GESTURES).not.toContain("cylinder_grasp");
  });

  it("alias 는 home|paper→open, rock→fist", () => {
    expect(GESTURE_ALIASES).toEqual({ home: "open", paper: "open", rock: "fist" });
  });
});

describe("FR-39 sequence", () => {
  it("countdown, scissors_rock_paper 만", () => {
    expect(SEQUENCE_IDS).toEqual(["countdown", "scissors_rock_paper"]);
  });
});

describe("6.4절 snapshot 판별", () => {
  it("snapshot 은 type 이 없고 timestamp·mode 가 최상위에 있다", () => {
    expect(isSnapshot(snapshot())).toBe(true);
  });

  it("ack 은 snapshot 이 아니다", () => {
    expect(isSnapshot({ type: "ack", request_id: "r", accepted: true })).toBe(false);
  });

  it("빈 값·잘못된 형태를 거른다", () => {
    expect(isSnapshot(null)).toBe(false);
    expect(isSnapshot({})).toBe(false);
    expect(isSnapshot({ timestamp: "x" })).toBe(false);
  });
});

describe("거부 사유 (interfaces.md / safety_manager.md / FR-18)", () => {
  it("stop_barrier_* 는 더 이상 쓰지 않는다", () => {
    expect(Object.values(REJECT_REASON)).not.toContain("stop_barrier_pending");
    expect(Object.values(REJECT_REASON)).not.toContain("stop_barrier_timeout");
  });

  it("interfaces.md 모드 서비스 사유를 포함한다", () => {
    const values = Object.values(REJECT_REASON);
    for (const r of ["invalid_mode", "motion_active", "stop_in_progress",
      "owner_lease_expired", "safety_not_ready"]) {
      expect(values).toContain(r);
    }
  });

  it("기록 서비스 사유(FR-18)를 포함한다", () => {
    const values = Object.values(REJECT_REASON);
    for (const r of ["not_mimic_mode", "start_failed", "already_recording",
      "result_pending", "not_recording", "session_mismatch", "stop_failed"]) {
      expect(values).toContain(r);
    }
  });

  it("Manual Executor 사유를 포함한다", () => {
    const values = Object.values(REJECT_REASON);
    for (const r of ["invalid_gesture", "invalid_sequence", "invalid_speed_limit",
      "not_manual_mode", "control_state_stale", "safety_state_stale", "stop_latched"]) {
      expect(values).toContain(r);
    }
  });

  it("로봇 session_mismatch 는 웹 web_session_mismatch 와 다른 값이다", () => {
    expect(REJECT_REASON.SESSION_MISMATCH).toBe("session_mismatch");
    expect(REJECT_REASON.SESSION_MISMATCH).not.toBe("web_session_mismatch");
  });

  it("새 표준 사유도 사용자 문구가 있다 (fallback 아님)", () => {
    expect(describeReason("stop_in_progress")).not.toContain("(");
    expect(describeReason("owner_lease_expired")).not.toContain("(");
    expect(describeReason("result_pending")).not.toContain("(");
  });

  it("accepted 는 안내 문구가 없다", () => {
    expect(describeReason(REJECT_REASON.ACCEPTED)).toBe("");
    expect(describeReason("")).toBe("");
    expect(describeReason(null)).toBe("");
  });

  it("FR-27: 각 사유가 다음에 할 일을 안내한다", () => {
    expect(describeReason(REJECT_REASON.INVALID_MODE)).toContain("정지");
    expect(describeReason(REJECT_REASON.SAFETY_NOT_READY)).toContain("안전 초기화");
    expect(describeReason(REJECT_REASON.RECORDING_ACTIVE)).toContain("판정");
    expect(describeReason(REJECT_REASON.MOTION_ACTIVE)).toContain("대기열");
    expect(describeReason(REJECT_REASON.OWNER_CONFLICT)).toContain("제어권");
    expect(describeReason(REJECT_REASON.OWNER_LEASE_EXPIRED)).toContain("제어권");
  });

  it("모르는 사유도 원문을 보여준다", () => {
    expect(describeReason("brand_new_reason")).toContain("brand_new_reason");
  });
});

describe("FR-24 장치 연결", () => {
  it("5개 장치를 모두 미확인으로 시작한다", () => {
    // FR-24 "가짜 값으로 채우지 않는다": bool 로는 "아직 못 받았다" 와 "끊겼다"
    // 를 구분할 수 없다. camera 를 false 로 단정하면 MJPEG 이 정상인데도
    // 영상이 가려진다.
    const status = emptyConnectionStatus();
    expect(Object.keys(status)).toEqual(["jetson", "rpi", "ros2", "camera", "motor"]);
    expect(Object.values(status).every((v) => v === CONNECTION_STATE.UNKNOWN))
      .toBe(true);
  });

  it("미확인은 단절로 취급하지 않는다", () => {
    expect(isDeviceDown(CONNECTION_STATE.DOWN)).toBe(true);
    expect(isDeviceDown(CONNECTION_STATE.UNKNOWN)).toBe(false);
    expect(isDeviceDown(CONNECTION_STATE.UP)).toBe(false);
    // 미확인이어도 표시·조작을 막지 않는다
    expect(isDeviceUsable(CONNECTION_STATE.UNKNOWN)).toBe(true);
    expect(isDeviceUsable(CONNECTION_STATE.DOWN)).toBe(false);
  });
});

describe("FR-11 타이밍 상수", () => {
  it("웹이 실제로 쓰는 값만 들고 있다", () => {
    // FR-41 은 FR-11 timeout 을 YAML 소관으로 두었다. 웹이 복제본을 들고 있으면
    // YAML 이 바뀔 때 두 곳이 갈린다. 판정 주체는 Raspberry Pi 다.
    expect(Object.keys(TIMING).sort())
      // 근거 없는 값(신선도 임계값, speed_limit, 재접속 백오프 등)은
      // config/pending.js 에 모아 두고 여기서는 참조만 한다.
      .toEqual([
        "ACK_TIMEOUT_MS",
        "CONTROL_RENEW_PERIOD_MS",
        "HAND_CONFIDENCE_MIN",
        "HAND_LOSS_DEBOUNCE_MS",
      ]);
    // 숫자를 직접 비교하지 않는다. pending.js 가 단일 출처이므로 그쪽을 가리킨다.
    expect(TIMING.CONTROL_RENEW_PERIOD_MS).toBe(SPEC.CONTROL_RENEW_PERIOD_MS);
    expect(TIMING.HAND_CONFIDENCE_MIN).toBe(SPEC.HAND_CONFIDENCE_MIN);
    expect(TIMING.HAND_LOSS_DEBOUNCE_MS).toBe(SPEC.HAND_LOSS_DEBOUNCE_MS);
    expect(TIMING.ACK_TIMEOUT_MS).toBe(PENDING.ACK_TIMEOUT_MS);
  });

  it("갱신 주기가 lease 만료보다 충분히 짧다", () => {
    // FR-11 owner_lease_timeout_ms 안에 최소 두 번 갱신할 기회가 있어야 한다.
    // lease 값은 로봇 소관이라 pending.js 의 SPEC 에서 읽는다.
    expect(SPEC.OWNER_LEASE_TIMEOUT_MS / TIMING.CONTROL_RENEW_PERIOD_MS)
      .toBeGreaterThanOrEqual(2);
  });
});
