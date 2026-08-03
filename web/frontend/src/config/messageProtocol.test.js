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
  MOTOR_COUNT,
  OPERATION_ALLOWED_STATES,
  RECORDING_BUSY_STATES,
  RECORDING_STATE,
  REJECT_REASON,
  RESET_ALLOWED_STATES,
  SAFETY_STATES,
  SEQUENCE_IDS,
  SNAPSHOT_FIXED_FIELDS,
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
  zeroAxisValues,
} from "./messageProtocol";

import { snapshot, validAxes } from "../test/fixtures";

describe("FR-30 7논리축", () => {
  it("배열이 아니라 고정 필드 7개다", () => {
    expect(HAND_AXIS_KEYS).toEqual([
      "thumb_flex", "thumb_opp", "thumb_abd",
      "index_flex", "middle_flex", "ring_flex", "little_flex",
    ]);
    expect(HAND_AXES).toHaveLength(7);
  });

  it("FR-07: 7채널 서보", () => {
    expect(MOTOR_COUNT).toBe(7);
  });
});

describe("FR-23 / FR-32 축 값 검증", () => {
  it("0.0~1.0 유한값을 통과시킨다", () => {
    expect(isValidAxisValues(validAxes)).toBe(true);
    expect(isValidAxisValues(zeroAxisValues())).toBe(true);
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
  it("7개 상태를 쓴다", () => {
    expect(SAFETY_STATES).toEqual(["INIT", "READY", "RUN", "HOLD", "SAFE", "FAULT", "ESTOP"]);
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
  it("고정 6필드 목록", () => {
    expect(SNAPSHOT_FIXED_FIELDS).toEqual([
      "timestamp", "mode", "recording_state", "landmarks", "motor_state", "safety_state",
    ]);
  });

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

describe("FR-37 거부 사유", () => {
  it("표준 6종", () => {
    expect(Object.values(REJECT_REASON)).toEqual([
      "accepted", "invalid_mode", "owner_conflict",
      "safety_not_ready", "recording_active", "motion_active",
    ]);
  });

  it("stop_in_progress 는 쓰지 않는다", () => {
    // FR-37 표준에 없다. safety_not_ready 가 그 상황을 덮는다.
    expect(Object.values(REJECT_REASON)).not.toContain("stop_in_progress");
  });

  it("accepted 는 안내 문구가 없다", () => {
    expect(describeReason(REJECT_REASON.ACCEPTED)).toBe("");
    expect(describeReason("")).toBe("");
    expect(describeReason(null)).toBe("");
  });

  it("FR-27: 각 사유가 다음에 할 일을 안내한다", () => {
    const invalid = describeReason(REJECT_REASON.INVALID_MODE);
    expect(invalid).toContain("정지");

    const notReady = describeReason(REJECT_REASON.SAFETY_NOT_READY);
    expect(notReady).toContain("안전 초기화");

    const recording = describeReason(REJECT_REASON.RECORDING_ACTIVE);
    expect(recording).toContain("판정");

    const motion = describeReason(REJECT_REASON.MOTION_ACTIVE);
    expect(motion).toContain("대기열");

    const owner = describeReason(REJECT_REASON.OWNER_CONFLICT);
    expect(owner).toContain("제어권");
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
      .toEqual(["CONTROL_RENEW_PERIOD_MS", "HAND_CONFIDENCE_MIN"]);
    expect(TIMING.CONTROL_RENEW_PERIOD_MS).toBe(1000);   // FR-34
    expect(TIMING.HAND_CONFIDENCE_MIN).toBe(0.7);        // 8.1절
  });

  it("갱신 주기가 lease 만료보다 충분히 짧다", () => {
    // FR-11 owner_lease_timeout_ms=3000 안에 최소 두 번 갱신할 기회가 있어야
    // 한다. lease 값은 로봇 소관이라 여기서 상수로 두지 않고 조문 값을 쓴다.
    const OWNER_LEASE_TIMEOUT_MS = 3000;   // FR-11 (판정 주체: Raspberry Pi)
    expect(OWNER_LEASE_TIMEOUT_MS / TIMING.CONTROL_RENEW_PERIOD_MS)
      .toBeGreaterThanOrEqual(2);
  });
});
