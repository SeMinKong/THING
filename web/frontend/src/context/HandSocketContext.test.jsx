// frontend/src/context/HandSocketContext.test.jsx
//
// 요구사항 명세서 V6.3 검증
//   6.4절  endpoint /ws/robot-state, snapshot v1 파싱
//   FR-11  control_renew_period_ms=1000, owner_lease_timeout_ms=3000
//   FR-19  mode·owner 는 control_state 로 확인한다 (낙관적 반영 금지)
//   FR-24  브릿지 단절 시 연결 상태를 추정하지 않는다
//   FR-27  거부 사유를 구분해 안내한다
//   FR-34  획득·갱신·해제
//   FR-35  재검출·연결 복구만으로 제어가 재개되지 않는다
//   NFR-15 재연결은 이전 명령을 재생하지 않는다

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HandSocketProvider, useHandSocket } from "./HandSocketContext";
import { BASIC_GESTURES } from "../config/commandPresets";
import {
  CLIENT_MESSAGE,
  CONNECTION_STATE,
  HAND_DETECTION,
  REJECT_REASON,
  TIMING,
  WEB_REASON,
} from "../config/messageProtocol";
import {
  ack,
  landmarksPayload,
  manualSnapshot,
  mimicSnapshot,
  motorStatePayload,
  readySnapshot,
  reject,
  snapshot,
} from "../test/fixtures";

function setup() {
  const view = renderHook(() => useHandSocket(), {
    wrapper: ({ children }) => <HandSocketProvider>{children}</HandSocketProvider>,
  });
  const socket = MockWebSocket.latest();
  return { ...view, socket };
}

/** 연결을 열고 초기 snapshot 을 넣는다. */
function open(socket, snap = snapshot()) {
  act(() => {
    socket.open();
    socket.emit(snap);
  });
}

beforeEach(() => {
  MockWebSocket.reset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("6.4절 endpoint", () => {
  it("/ws/robot-state 에 접속한다", () => {
    setup();
    expect(MockWebSocket.latest().url).toContain("/ws/robot-state");
  });

  it("구 경로 ws/hand 를 쓰지 않는다", () => {
    setup();
    expect(MockWebSocket.latest().url).not.toContain("/ws/hand");
  });
});

describe("6.4절 snapshot 파싱", () => {
  it("control_state·safety_state·recording_state 를 반영한다", () => {
    const { result, socket } = setup();
    open(socket, mimicSnapshot());

    expect(result.current.controlState.active_mode).toBe("MIMIC");
    expect(result.current.controlState.active_owner).toBe("WEB");
    expect(result.current.safetyState.state).toBe("RUN");
    expect(result.current.recordingState.state).toBe("IDLE");
  });

  it("연결 상태 5개를 반영한다", () => {
    const { result, socket } = setup();
    open(socket, readySnapshot());
    for (const key of ["jetson", "rpi", "ros2", "camera", "motor"]) {
      expect(result.current.connectionStatus[key]).toBe(CONNECTION_STATE.UP);
    }
  });

  it("빈 객체 {} 로는 표시를 갱신하지 않는다", () => {
    const { result, socket } = setup();
    // 값이 들어온 뒤
    open(socket, readySnapshot({ landmarks: landmarksPayload }));
    expect(result.current.landmarks).toEqual(landmarksPayload);

    // 빈 객체가 와도 기존 표시를 지우지 않는다
    act(() => socket.emit(readySnapshot({ landmarks: {} })));
    expect(result.current.landmarks).toEqual(landmarksPayload);
  });

  it("motor_state 를 반영한다", () => {
    const { result, socket } = setup();
    open(socket, readySnapshot({ motor_state: motorStatePayload }));
    expect(result.current.motorStatus.motors).toHaveLength(7);
  });

  it("landmarks 수신 시각을 기록한다", () => {
    const { result, socket } = setup();
    expect(result.current.landmarksUpdatedAt).toBeNull();
    open(socket, readySnapshot({ landmarks: landmarksPayload }));
    expect(result.current.landmarksUpdatedAt).toBeGreaterThan(0);
  });
});

describe("요청 envelope", () => {
  it("request_id·type·timestamp·payload 를 보낸다", () => {
    const { result, socket } = setup();
    open(socket, readySnapshot());

    act(() => { result.current.selectMode("MIMIC"); });

    const sent = socket.sentOf(CLIENT_MESSAGE.SET_CONTROL_MODE);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      type: "set_control_mode",
      payload: { requested_mode: "MIMIC", requested_owner: "WEB" },
    });
    expect(typeof sent[0].request_id).toBe("string");
    expect(sent[0].timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
  });

  it("STOP 은 DISABLED+NONE 을 보낸다", () => {
    const { result, socket } = setup();
    open(socket, mimicSnapshot());

    act(() => { result.current.sendStop(); });

    const sent = socket.sentOf(CLIENT_MESSAGE.STOP);
    expect(sent[0].payload).toEqual({ requested_mode: "DISABLED", requested_owner: "NONE" });
  });

  it("6.3절 서비스 이름과 1:1 대응한다", () => {
    const { result, socket } = setup();
    open(socket, mimicSnapshot({
      recording: {
        ...mimicSnapshot().recording,
        state: "RECORDING",
        active_session_id: "4242",
        last_session_id: "4141",
        result_pending: true,
      },
    }));

    act(() => {
      result.current.startRecording();
      result.current.stopRecording();
      result.current.submitRecordingResult("SUCCESS");
    });

    const types = socket.sent.map((m) => m.type);
    expect(types).toContain("start_recording");
    expect(types).toContain("stop_recording");
    expect(types).toContain("set_mimic_result");
    // 구 계약의 record_control 은 쓰지 않는다
    expect(types).not.toContain("record_control");
    // FR-30 이 동결한 서비스 5종에 캘리브레이션이 없고 6.3절 목록에도 없다.
    // 6.4절은 "기존 ROS 2 서비스·액션에만 매핑" 하도록 제한한다 (FR-04 는 Should).
    expect(types).not.toContain("calibration_capture");
    expect(result.current.sendCalibrationCapture).toBeUndefined();
  });

  // ── FR-40: .srv 요청 필수 필드 ──────────────────────────────────────────

  it("stop_recording 은 활성 세션 ID 를 함께 보낸다", () => {
    const { result, socket } = setup();
    open(socket, mimicSnapshot({
      recording: {
        ...mimicSnapshot().recording,
        state: "RECORDING",
        active_session_id: "4242",
      },
    }));

    act(() => { result.current.stopRecording(); });

    const sent = socket.sentOf(CLIENT_MESSAGE.STOP_RECORDING);
    expect(sent).toHaveLength(1);
    expect(sent[0].payload).toEqual({ session_id: "4242" });
  });

  it("set_mimic_result 는 판정 대기 세션 ID 를 함께 보낸다", () => {
    const { result, socket } = setup();
    open(socket, mimicSnapshot({
      recording: {
        ...mimicSnapshot().recording,
        state: "COMPLETED",
        last_session_id: "4141",
        result_pending: true,
      },
    }));

    act(() => { result.current.submitRecordingResult("FAILURE"); });

    const sent = socket.sentOf(CLIENT_MESSAGE.SET_MIMIC_RESULT);
    expect(sent).toHaveLength(1);
    expect(sent[0].payload).toEqual({ session_id: "4141", result: "FAILURE" });
  });

  it("세션 ID 를 모르면 기록 요청을 보내지 않는다", () => {
    const { result, socket } = setup();
    open(socket, mimicSnapshot());   // active_session_id·last_session_id = ""

    act(() => { result.current.stopRecording(); });
    expect(socket.sentOf(CLIENT_MESSAGE.STOP_RECORDING)).toHaveLength(0);
    expect(result.current.lastError.code).toBe("NO_ACTIVE_SESSION");

    act(() => { result.current.submitRecordingResult("SUCCESS"); });
    expect(socket.sentOf(CLIENT_MESSAGE.SET_MIMIC_RESULT)).toHaveLength(0);
    expect(result.current.lastError.code).toBe("NO_PENDING_SESSION");
  });
});

describe("6.4절 미수신 객체 {} 규칙", () => {
  it("safety_state 가 {} 면 관측된 상태로 취급하지 않는다", () => {
    const { result, socket } = setup();
    open(socket, snapshot({ safety_state: {} }));

    expect(result.current.safetyStateKnown).toBe(false);
    // fail-closed: 상태를 모르면 조작도 reset 도 허용하지 않는다
    expect(result.current.isSafeToOperate).toBe(false);
    expect(result.current.canResetSafety).toBe(false);
  });

  it("safety_state 를 받으면 known 으로 전환한다", () => {
    const { result, socket } = setup();
    open(socket, readySnapshot());

    expect(result.current.safetyStateKnown).toBe(true);
    expect(result.current.safetyState.state).toBe("READY");
    expect(result.current.isSafeToOperate).toBe(true);
  });
});

describe("FR-20 / FR-27 손 검출 판정", () => {
  it("8.1절 기준으로 confidence 미달을 미검출과 구분한다", () => {
    const { result, socket } = setup();

    open(socket, readySnapshot());
    expect(result.current.handDetection).toBe(HAND_DETECTION.UNKNOWN);

    act(() => {
      socket.emit(readySnapshot({ landmarks: { detected: true, confidence: 0.93 } }));
    });
    expect(result.current.handDetection).toBe(HAND_DETECTION.DETECTED);

    act(() => {
      socket.emit(readySnapshot({ landmarks: { detected: true, confidence: 0.4 } }));
    });
    expect(result.current.handDetection).toBe(HAND_DETECTION.LOW_CONFIDENCE);

    act(() => {
      socket.emit(readySnapshot({ landmarks: { detected: false, confidence: 0.0 } }));
    });
    expect(result.current.handDetection).toBe(HAND_DETECTION.NOT_DETECTED);
  });
});

describe("FR-23 / FR-38 웹측 사전 검증", () => {
  // 7축 값은 검증하지 않는다. ExecuteGesture.srv 가 gesture_name·speed_limit 만
  // 받고 7축 목표는 FR-41 YAML 소관이므로 웹은 축값을 아예 다루지 않는다.

  it("canonical 이 아닌 gesture 는 보내지 않는다", () => {
    const { result, socket } = setup();
    open(socket, manualSnapshot());
    act(() => { result.current.sendGesture("init_pose", 0.5); });
    expect(socket.sentOf(CLIENT_MESSAGE.EXECUTE_GESTURE)).toHaveLength(0);
    expect(result.current.lastError.code).toBe("INVALID_GESTURE");
  });

  it("FR-38 alias 는 canonical 이름으로 펴서 보낸다", () => {
    const { result, socket } = setup();
    open(socket, manualSnapshot());
    act(() => { result.current.sendGesture("rock", 1.0); });
    const sent = socket.sentOf(CLIENT_MESSAGE.EXECUTE_GESTURE);
    expect(sent).toHaveLength(1);
    expect(sent[0].payload.gesture_name).toBe("fist");
  });

  it("잘못된 speed_limit 은 보내지 않는다", () => {
    const { result, socket } = setup();
    open(socket, manualSnapshot());
    act(() => { result.current.sendGesture("open", 0); });
    expect(socket.sentOf(CLIENT_MESSAGE.EXECUTE_GESTURE)).toHaveLength(0);
    expect(result.current.lastError.code).toBe("INVALID_SPEED_LIMIT");
  });

  it("실제 화면 preset 을 그대로 받아 보낸다", () => {
    const { result, socket } = setup();
    open(socket, manualSnapshot());

    // 회귀 방지: OrderMode 는 commandPresets 의 객체를 그대로 넘기고 그 객체에는
    // values 가 없다. 검증기가 values 를 요구하면 버튼 4개가 전부 죽는다.
    for (const preset of BASIC_GESTURES) {
      socket.sent.length = 0;
      let ok;
      act(() => { ok = result.current.sendGesture(preset); });
      expect(ok, `${preset.id} 가 전송되지 않았다`).toBe(true);
      const sent = socket.sentOf(CLIENT_MESSAGE.EXECUTE_GESTURE);
      expect(sent).toHaveLength(1);
      expect(sent[0].payload).toEqual({
        gesture_name: preset.id,
        speed_limit: preset.speed_limit,
      });
      act(() => { socket.emit(ack(sent[0].request_id)); });
    }
  });

  it("preset 에 speed_limit 이 없으면 보내지 않는다", () => {
    const { result, socket } = setup();
    open(socket, manualSnapshot());
    act(() => { result.current.sendGesture({ id: "open" }); });
    expect(socket.sentOf(CLIENT_MESSAGE.EXECUTE_GESTURE)).toHaveLength(0);
    expect(result.current.lastError.code).toBe("INVALID_SPEED_LIMIT");
  });

  it("FR-39 지원하지 않는 sequence 는 보내지 않는다", () => {
    const { result, socket } = setup();
    open(socket, manualSnapshot());

    act(() => { result.current.sendSequence("wave"); });
    expect(socket.sentOf(CLIENT_MESSAGE.EXECUTE_SEQUENCE)).toHaveLength(0);

    act(() => { result.current.sendSequence("countdown"); });
    expect(socket.sentOf(CLIENT_MESSAGE.EXECUTE_SEQUENCE)).toHaveLength(1);
  });

  it("웹이 선택할 수 없는 mode 는 보내지 않는다", () => {
    const { result, socket } = setup();
    open(socket, readySnapshot());

    act(() => { result.current.selectMode("TELEOP"); });
    expect(socket.sentOf(CLIENT_MESSAGE.SET_CONTROL_MODE)).toHaveLength(0);
  });
});

describe("FR-19 낙관적 반영 금지", () => {
  it("요청만으로 mode 가 바뀌지 않는다", () => {
    const { result, socket } = setup();
    open(socket, readySnapshot());

    act(() => { result.current.selectMode("MIMIC"); });

    // 서버가 확정하지 않았으므로 여전히 DISABLED
    expect(result.current.controlState.active_mode).toBe("DISABLED");
    expect(result.current.webHasControl).toBe(false);
  });

  it("control_state 수신으로만 확정된다", () => {
    const { result, socket } = setup();
    open(socket, readySnapshot());
    act(() => { result.current.selectMode("MIMIC"); });

    act(() => { socket.emit(mimicSnapshot()); });

    expect(result.current.controlState.active_mode).toBe("MIMIC");
    expect(result.current.webHasControl).toBe(true);
  });

  it("요청한 mode 를 노출해 확정 대기임을 알린다", () => {
    const { result, socket } = setup();
    open(socket, readySnapshot());
    act(() => { result.current.selectMode("MIMIC"); });
    // 확정은 control_state 로만 판단한다 (FR-19). 그 전까지는 요청 중 표시.
    expect(result.current.requestedMode).toBe("MIMIC");
    expect(result.current.webHasControl).toBe(false);
  });
});

describe("FR-27 / FR-37 거부 사유 안내", () => {
  it("ack 거부 시 사유를 사용자 문구로 보여준다", () => {
    const { result, socket } = setup();
    open(socket, readySnapshot());
    act(() => { result.current.selectMode("MIMIC"); });
    const requestId = socket.sentOf(CLIENT_MESSAGE.SET_CONTROL_MODE)[0].request_id;

    act(() => { socket.emit(reject(requestId, REJECT_REASON.SAFETY_NOT_READY)); });

    expect(result.current.modeRejectedReason).toContain("안전 초기화");
    // code 는 FR-37 사유 원문을 그대로 담는다
    expect(result.current.lastError.code).toBe(REJECT_REASON.SAFETY_NOT_READY);
    expect(result.current.lastError.message).toContain("안전 초기화");
  });

  it("추적한 요청이 수락되면 안내를 지운다", () => {
    const { result, socket } = setup();
    open(socket, manualSnapshot());

    act(() => { socket.emit(reject("r-1", REJECT_REASON.MOTION_ACTIVE)); });
    expect(result.current.modeRejectedReason).not.toBe("");

    act(() => { result.current.sendGesture("open", 1.0); });
    const sent = socket.sentOf(CLIENT_MESSAGE.EXECUTE_GESTURE).at(-1);
    act(() => { socket.emit(ack(sent.request_id, true)); });
    expect(result.current.modeRejectedReason).toBe("");
  });

  it("추적하지 않은 요청의 수락 ack 는 안내를 지우지 않는다", () => {
    // lease 갱신은 1000ms 마다 set_control_mode 를 보내고 매번 accepted ack 를
    // 받는다. 그 ack 로 거부 사유를 지우면 FR-27 이 요구하는 안내가 1초 만에
    // 사라져 사용자가 읽을 수 없다.
    const { result, socket } = setup();
    open(socket, manualSnapshot());

    act(() => { socket.emit(reject("r-1", REJECT_REASON.MOTION_ACTIVE)); });
    const shown = result.current.lastError.code;

    act(() => { socket.emit(ack("갱신-등-추적하지-않은-요청", true)); });
    expect(result.current.lastError.code).toBe(shown);
    expect(result.current.modeRejectedReason).not.toBe("");
  });

  it("브릿지 미연결 사유도 안내한다", () => {
    const { result, socket } = setup();
    open(socket, snapshot());
    act(() => { socket.emit(reject("r-1", WEB_REASON.BRIDGE_OFFLINE)); });
    expect(result.current.lastError.code).toBe(WEB_REASON.BRIDGE_OFFLINE);
    expect(result.current.lastError.message).toContain("브릿지");
  });
});

describe("FR-11 owner lease 갱신", () => {
  it("제어권 확정 후 1000ms 마다 같은 mode·owner 를 재요청한다", () => {
    vi.useFakeTimers();
    const { socket } = setup();
    open(socket, mimicSnapshot());

    // 확정 직후에는 갱신 요청이 없다
    expect(socket.sentOf(CLIENT_MESSAGE.SET_CONTROL_MODE)).toHaveLength(0);

    act(() => { vi.advanceTimersByTime(TIMING.CONTROL_RENEW_PERIOD_MS); });
    let renewals = socket.sentOf(CLIENT_MESSAGE.SET_CONTROL_MODE);
    expect(renewals).toHaveLength(1);
    expect(renewals[0].payload).toEqual({ requested_mode: "MIMIC", requested_owner: "WEB" });

    act(() => { vi.advanceTimersByTime(TIMING.CONTROL_RENEW_PERIOD_MS * 2); });
    renewals = socket.sentOf(CLIENT_MESSAGE.SET_CONTROL_MODE);
    expect(renewals).toHaveLength(3);
  });

  it("lease 만료(3000ms) 안에 최소 2회 갱신한다", () => {
    vi.useFakeTimers();
    const { socket } = setup();
    open(socket, mimicSnapshot());

    // FR-11 owner_lease_timeout_ms=3000. 값의 주인은 로봇(YAML)이라 웹 TIMING 에
    // 두지 않고 조문 값을 직접 쓴다.
    act(() => { vi.advanceTimersByTime(3000); });
    expect(socket.sentOf(CLIENT_MESSAGE.SET_CONTROL_MODE).length)
      .toBeGreaterThanOrEqual(2);
  });

  it("제어권이 없으면 갱신하지 않는다", () => {
    vi.useFakeTimers();
    const { socket } = setup();
    open(socket, readySnapshot());   // DISABLED + NONE

    act(() => { vi.advanceTimersByTime(TIMING.OWNER_LEASE_TIMEOUT_MS * 2); });
    expect(socket.sentOf(CLIENT_MESSAGE.SET_CONTROL_MODE)).toHaveLength(0);
  });

  it("제어권을 잃으면 갱신을 멈춘다", () => {
    vi.useFakeTimers();
    const { socket } = setup();
    open(socket, mimicSnapshot());

    act(() => { vi.advanceTimersByTime(TIMING.CONTROL_RENEW_PERIOD_MS) });
    const before = socket.sentOf(CLIENT_MESSAGE.SET_CONTROL_MODE).length;

    // owner_alive=false 로 lease 상실
    act(() => { socket.emit(readySnapshot()); });
    act(() => { vi.advanceTimersByTime(TIMING.CONTROL_RENEW_PERIOD_MS * 3); });

    expect(socket.sentOf(CLIENT_MESSAGE.SET_CONTROL_MODE)).toHaveLength(before);
  });

  it("STOP 을 누르면 갱신을 멈춘다", () => {
    vi.useFakeTimers();
    const { result, socket } = setup();
    open(socket, mimicSnapshot());

    act(() => { result.current.sendStop(); });
    const before = socket.sentOf(CLIENT_MESSAGE.SET_CONTROL_MODE).length;

    act(() => { vi.advanceTimersByTime(TIMING.CONTROL_RENEW_PERIOD_MS * 3); });
    expect(socket.sentOf(CLIENT_MESSAGE.SET_CONTROL_MODE)).toHaveLength(before);
  });
});

describe("FR-35 / NFR-15 자동 재개 금지", () => {
  it("제어권을 잃으면 재개 안내를 띄운다", () => {
    const { result, socket } = setup();
    open(socket, mimicSnapshot());
    expect(result.current.needsResumeConfirmation).toBe(false);

    act(() => { socket.emit(readySnapshot()); });   // lease 상실
    expect(result.current.needsResumeConfirmation).toBe(true);
  });

  it("재연결해도 이전 mode 를 자동 재획득하지 않는다", () => {
    vi.useFakeTimers();
    const { socket } = setup();
    open(socket, mimicSnapshot());

    act(() => { socket.closeFromServer(); });
    // 재연결 타이머 진행
    act(() => { vi.advanceTimersByTime(2000); });

    const reconnected = MockWebSocket.latest();
    act(() => { reconnected.open(); });
    act(() => { vi.advanceTimersByTime(TIMING.OWNER_LEASE_TIMEOUT_MS) });

    // 자동 재획득·갱신 요청이 없어야 한다
    expect(reconnected.sentOf(CLIENT_MESSAGE.SET_CONTROL_MODE)).toHaveLength(0);
  });

  it("resumeControl 은 아무 명령도 보내지 않고 안내만 닫는다", () => {
    const { result, socket } = setup();
    open(socket, mimicSnapshot());
    act(() => { socket.emit(readySnapshot()); });
    expect(result.current.needsResumeConfirmation).toBe(true);

    const before = socket.sent.length;
    act(() => { result.current.resumeControl(); });

    expect(result.current.needsResumeConfirmation).toBe(false);
    expect(socket.sent).toHaveLength(before);
  });
});

describe("FR-24 브릿지 단절", () => {
  it("연결이 끊기면 장치 상태를 미확인으로 되돌린다", () => {
    // FR-24: 실제 값을 추정하거나 유지하지 않는다. 다만 false 로 단정하면
    // "관측된 단절" 과 구분되지 않고, camera=false 는 MJPEG 이 정상인데도
    // 영상을 가려버린다.
    const { result, socket } = setup();
    open(socket, readySnapshot());
    expect(result.current.connectionStatus.jetson).toBe(CONNECTION_STATE.UP);

    act(() => { socket.closeFromServer(); });

    expect(result.current.connectionState).toBe("closed");
    expect(result.current.controlStateKnown).toBe(false);
    for (const key of ["jetson", "rpi", "ros2", "camera", "motor"]) {
      expect(result.current.connectionStatus[key]).toBe(CONNECTION_STATE.UNKNOWN);
    }
  });

  it("데이터가 비어 있는 snapshot 을 그대로 표시한다", () => {
    const { result, socket } = setup();
    open(socket, snapshot());
    expect(result.current.controlState.active_mode).toBe("DISABLED");
    expect(result.current.landmarks).toBeNull();
  });
});

describe("FR-38 / FR-35 파생 상태", () => {
  it("isSafeToOperate 는 READY·RUN 에서만 true", () => {
    const cases = [
      ["INIT", false], ["READY", true], ["RUN", true],
      ["HOLD", false], ["SAFE", false], ["FAULT", false], ["ESTOP", false],
    ];
    for (const [state, expected] of cases) {
      MockWebSocket.reset();
      const { result, socket } = setup();
      open(socket, snapshot({ safety_state: { ...snapshot().safety_state, state } }));
      expect(result.current.isSafeToOperate, state).toBe(expected);
    }
  });

  it("canResetSafety 는 SAFE·FAULT·ESTOP 에서만 true", () => {
    const cases = [
      ["INIT", false], ["READY", false], ["RUN", false], ["HOLD", false],
      ["SAFE", true], ["FAULT", true], ["ESTOP", true],
    ];
    for (const [state, expected] of cases) {
      MockWebSocket.reset();
      const { result, socket } = setup();
      open(socket, snapshot({ safety_state: { ...snapshot().safety_state, state } }));
      expect(result.current.canResetSafety, state).toBe(expected);
    }
  });

  it("HOLD 에서 resetSafety 를 누르면 STOP 절차를 안내한다", () => {
    const { result, socket } = setup();
    open(socket, snapshot({ safety_state: { ...snapshot().safety_state, state: "HOLD" } }));

    act(() => { result.current.resetSafety(); });

    expect(socket.sentOf(CLIENT_MESSAGE.RESET_SAFETY)).toHaveLength(0);
    expect(result.current.lastError.code).toBe("RESET_NOT_ALLOWED");
    expect(result.current.lastError.message).toContain("정지");
  });

  it("ESTOP 에서는 resetSafety 를 보낸다", () => {
    const { result, socket } = setup();
    open(socket, snapshot({ safety_state: { ...snapshot().safety_state, state: "ESTOP" } }));

    act(() => { result.current.resetSafety(); });
    expect(socket.sentOf(CLIENT_MESSAGE.RESET_SAFETY)).toHaveLength(1);
  });

  it("webHasControl 은 owner_alive 까지 확인한다", () => {
    MockWebSocket.reset();
    const a = setup();
    open(a.socket, snapshot({
      control_state: { ...snapshot().control_state, active_owner: "WEB", owner_alive: false },
    }));
    expect(a.result.current.webHasControl).toBe(false);

    MockWebSocket.reset();
    const b = setup();
    open(b.socket, mimicSnapshot());
    expect(b.result.current.webHasControl).toBe(true);
  });
});
