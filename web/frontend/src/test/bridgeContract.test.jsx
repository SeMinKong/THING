// ============================================================================
// 브릿지 계약 경계 회귀 시험
// ----------------------------------------------------------------------------
// 왜 이 파일이 따로 있는가
//
// 다른 시험은 fixtures.js 가 만든 snapshot 만 쓰고, fixtures 는 프런트가 원하는
// 필드를 항상 채워 준다. mock-bridge 도 messageProtocol.js 를 import 해서 같은
// 가정 위에 있다. 그래서 "프런트가 정한 것" 과 "명세서가 정한 것" 의 차이를
// 검증할 주체가 없었고, 통합 전까지 드러나지 않는 결함이 남았다.
//
// 이 파일은 fixtures 를 쓰지 않고 손으로 만든 payload 로 경계를 고정한다.
//   1. 6.4절 고정 6필드만 오는 브릿지
//   2. .msg 원문 dump (enum 이 정수)
//   3. 6.5절 위반 (session_id 가 숫자)
//   4. 선택 필드 미제공 (connection_status, hand_loss_latched)
//   5. FR-27 장치 단절 경고
//   6. 실제 화면 preset 으로 버튼 누르기
// ============================================================================
import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, render, renderHook, screen } from "@testing-library/react";

import { HandSocketProvider, useHandSocket } from "../context/HandSocketContext";
import StatusBar from "../components/StatusBar";
// import SafetyBanner from "../components/SafetyBanner";
import OrderMode from "../pages/OrderMode";
import { BASIC_GESTURES } from "../config/commandPresets";
import { CLIENT_MESSAGE, CONNECTION_STATE } from "../config/messageProtocol";

function utcNowZ() {
  return new Date().toISOString().replace(/(\.\d{3})\d*Z$/, "$1Z");
}

/** builtin_interfaces/Time. .msg 의 시각 필드는 전부 이 형태다. */
function rosTime() {
  const ms = Date.now();
  return { sec: Math.floor(ms / 1000), nanosec: (ms % 1000) * 1e6 };
}

function setup(ui = null) {
  const view = ui
    ? render(<HandSocketProvider>{ui}</HandSocketProvider>)
    : renderHook(() => useHandSocket(), {
      wrapper: ({ children }) => <HandSocketProvider>{children}</HandSocketProvider>,
    });
  return { ...view, socket: MockWebSocket.latest() };
}

function setupHook() {
  const view = renderHook(() => useHandSocket(), {
    wrapper: ({ children }) => <HandSocketProvider>{children}</HandSocketProvider>,
  });
  return { ...view, socket: MockWebSocket.latest() };
}

function emit(socket, payload) {
  act(() => {
    socket.open();
    socket.emit(payload);
  });
}

function acceptAck(requestId) {
  return { type: "ack", request_id: requestId, accepted: true, reason: "accepted" };
}

beforeEach(() => { MockWebSocket.reset(); });

// ───────────────────────────────────────────────────────────────────────────
// 1. 6.4절이 고정한 6필드만 보내는 브릿지
//
// 이전 구현은 top-level mode·recording_state 를 아예 읽지 않고 확장 필드만 봤다.
// 명세대로만 구현한 브릿지와 붙이면 화면이 "비활성화" 로 굳고 조작이 영구히
// 잠겼다. 확장 필드는 협의 대상이지만 고정 6필드는 무조건 반영해야 한다.
// ───────────────────────────────────────────────────────────────────────────

const SPEC_ONLY = {
  timestamp: utcNowZ(),
  mode: "MIMIC",
  recording_state: "RECORDING",
  landmarks: { detected: true, confidence: 0.93 },
  motor_state: { motors: [], bus_communication_ok: true },
  safety_state: { state: "RUN", motor_communication_ok: true },
};

describe("6.4절 고정 6필드만 오는 브릿지", () => {
  it("top-level mode 를 화면에 반영한다", () => {
    setup(<StatusBar />);
    emit(MockWebSocket.latest(), SPEC_ONLY);
    expect(screen.getByText(/현재 모드: 모방 모드/)).toBeInTheDocument();
  });

  it("top-level recording_state 를 화면에 반영한다", () => {
    setup(<StatusBar />);
    emit(MockWebSocket.latest(), SPEC_ONLY);
    expect(document.body.textContent).toContain("기록: RECORDING");
  });

  it("control_state 가 없으면 제어권을 주지 않는다 (fail-closed)", () => {
    const { result, socket } = setup();
    emit(socket, SPEC_ONLY);
    expect(result.current.controlStateKnown).toBe(false);
    expect(result.current.webHasControl).toBe(false);
  });

  it("control_state 누락을 정상 상태로 위장하지 않고 안내한다", () => {
    // setup(<SafetyBanner />);
    emit(MockWebSocket.latest(), SPEC_ONLY);
    expect(document.body.textContent).toContain("제어 상태");
  });
});

// ───────────────────────────────────────────────────────────────────────────
// 2. .msg 원문 dump — enum 이 정수로 온다
//
// message_to_ordereddict 는 uint8 상수를 정수로 싣는다. 브릿지에 문자열 변환을
// 요구하지 않기로 했으므로 프런트가 정규화한다.
// ───────────────────────────────────────────────────────────────────────────

const RAW_DUMP = {
  timestamp: utcNowZ(),
  mode: "MANUAL",
  recording_state: "IDLE",
  landmarks: {},
  motor_state: {},
  safety_state: { state: 2, motor_communication_ok: true, stamp: rosTime() },
  control_state: {
    active_mode: 2,        // MODE_MANUAL
    active_owner: 1,       // OWNER_WEB
    owner_alive: true,
    sequence_running: false,
    last_transition_reason: "",
    stamp: rosTime(),
  },
  recording: {
    state: 0,              // STATE_IDLE
    active_session_id: "",
    last_session_id: "",
    last_mimic_result: 0,  // RESULT_UNSET
    result_pending: false,
    stamp: rosTime(),
  },
};

describe(".msg 원문 dump (enum 정수)", () => {
  it("uint8 mode·owner 를 symbolic 으로 바꾼다", () => {
    const { result, socket } = setup();
    emit(socket, RAW_DUMP);
    expect(result.current.controlState.active_mode).toBe("MANUAL");
    expect(result.current.controlState.active_owner).toBe("WEB");
    expect(result.current.webHasControl).toBe(true);
  });

  it("uint8 SafetyState·RecordingState 를 symbolic 으로 바꾼다", () => {
    const { result, socket } = setup();
    emit(socket, RAW_DUMP);
    expect(result.current.safetyState.state).toBe("RUN");
    expect(result.current.recordingState.state).toBe("IDLE");
    expect(result.current.isSafeToOperate).toBe(true);
  });

  it("symbolic string 으로 와도 그대로 통과시킨다", () => {
    const { result, socket } = setup();
    emit(socket, {
      ...RAW_DUMP,
      control_state: { ...RAW_DUMP.control_state, active_mode: "MANUAL", active_owner: "WEB" },
    });
    expect(result.current.controlState.active_mode).toBe("MANUAL");
  });
});

// ───────────────────────────────────────────────────────────────────────────
// 3. 6.5절 위반 — session_id 가 숫자
//
// 63-bit 값은 JSON.parse 시점에 손상된다. 손상된 값을 StopRecording 으로
// 되돌려 보내면 세션이 닫히지 않고 EC2 업로드까지 멈춘다.
// ───────────────────────────────────────────────────────────────────────────

describe("6.5절 session_id 는 10진 문자열", () => {
  const NUMERIC_ID = {
    timestamp: utcNowZ(),
    mode: "MIMIC",
    recording_state: "RECORDING",
    landmarks: {},
    motor_state: {},
    safety_state: { state: "RUN", stamp: rosTime() },
    control_state: {
      active_mode: "MIMIC", active_owner: "WEB", owner_alive: true, stamp: rosTime(),
    },
    recording: {
      state: "RECORDING",
      // 계약 위반. 브릿지가 JSON 숫자로 보낸 63-bit 값을 실제와 같은 경로로 만든다.
      // (JS 소스에 리터럴로 쓰면 eslint no-loss-of-precision 이 먼저 잡는다 —
      //  그 규칙이 잡는다는 사실 자체가 이 시험의 근거다.)
      active_session_id: JSON.parse('{"v":8531234567890123456}').v,
      last_session_id: "",
      result_pending: false,
      stamp: rosTime(),
    },
  };

  it("숫자 session_id 를 감지한다", () => {
    const { result, socket } = setup();
    emit(socket, NUMERIC_ID);
    expect(result.current.sessionIdProtocolError).toBe(true);
  });

  it("손상됐을 수 있는 ID 를 로봇에 되돌려 보내지 않는다", () => {
    const { result, socket } = setup();
    emit(socket, NUMERIC_ID);
    act(() => { result.current.stopRecording(); });
    expect(socket.sentOf(CLIENT_MESSAGE.STOP_RECORDING)).toHaveLength(0);
    expect(result.current.lastError.code).toBe("SESSION_ID_NOT_STRING");
  });

  it("문자열이면 그대로 보낸다", () => {
    const { result, socket } = setup();
    emit(socket, {
      ...NUMERIC_ID,
      recording: { ...NUMERIC_ID.recording, active_session_id: "8531234567890123456" },
    });
    act(() => { result.current.stopRecording(); });
    const sent = socket.sentOf(CLIENT_MESSAGE.STOP_RECORDING);
    expect(sent).toHaveLength(1);
    expect(sent[0].payload.session_id).toBe("8531234567890123456");
  });
});

// ───────────────────────────────────────────────────────────────────────────
// 4. 선택 필드 미제공 — 프런트가 파생한다
// ───────────────────────────────────────────────────────────────────────────

describe("선택 필드가 없을 때 프런트 파생", () => {
  it("connection_status 없이 조각 신선도로 장치 상태를 만든다", () => {
    const now = utcNowZ();
    const { result, socket } = setup();
    emit(socket, {
      timestamp: now,
      mode: "MIMIC",
      recording_state: "IDLE",
      landmarks: { detected: true, confidence: 0.9, stamp: now },
      motor_state: { motors: [], bus_communication_ok: true, stamp: now },
      safety_state: { state: "RUN", stamp: now },
      control_state: { active_mode: "MIMIC", active_owner: "NONE", stamp: now },
    });
    for (const key of ["jetson", "camera", "rpi", "ros2", "motor"]) {
      expect(result.current.connectionStatus[key], key).toBe(CONNECTION_STATE.UP);
    }
  });

  it("조각이 갱신을 멈추면 그 장치를 down 으로 본다", () => {
    // 신선도는 stamp 뺄셈이 아니라 "지문이 마지막으로 바뀐 시각" 으로 잰다.
    // .msg 의 시각 필드는 위치(stamp / header.stamp)와 타입({sec,nanosec})이
    // 제각각이라 파싱에 의존하면 안 된다.
    vi.useFakeTimers();
    try {
      const { result, socket } = setup();
      const frozen = { sec: 1785000000, nanosec: 0 };   // landmarks 는 이 값에서 멈춘다
      const tick = (n) => ({ sec: 1785000000 + n, nanosec: 0 });

      emit(socket, {
        timestamp: utcNowZ(),
        mode: "MIMIC",
        recording_state: "IDLE",
        landmarks: { header: { stamp: frozen }, detected: false },
        motor_state: { header: { stamp: tick(0) }, motors: [], bus_communication_ok: true },
        safety_state: { stamp: tick(0), state: "RUN" },
        control_state: { stamp: tick(0), active_mode: "MIMIC", active_owner: "NONE" },
      });
      expect(result.current.connectionStatus.camera).toBe(CONNECTION_STATE.UP);

      // 5초 뒤: landmarks 지문은 그대로, 나머지는 갱신
      act(() => { vi.advanceTimersByTime(5000); });
      act(() => {
        socket.emit({
          timestamp: utcNowZ(),
          mode: "MIMIC",
          recording_state: "IDLE",
          landmarks: { header: { stamp: frozen }, detected: false },
          motor_state: { header: { stamp: tick(5) }, motors: [], bus_communication_ok: true },
          safety_state: { stamp: tick(5), state: "RUN" },
          control_state: { stamp: tick(5), active_mode: "MIMIC", active_owner: "NONE" },
        });
      });
      act(() => { vi.advanceTimersByTime(1100); });   // 재계산 tick

      expect(result.current.connectionStatus.camera).toBe(CONNECTION_STATE.DOWN);
      expect(result.current.connectionStatus.rpi).toBe(CONNECTION_STATE.UP);
    } finally {
      vi.useRealTimers();
    }
  });

  it("모터 버스 단절은 bus_communication_ok 로 판정한다", () => {
    const now = utcNowZ();
    const { result, socket } = setup();
    emit(socket, {
      timestamp: now,
      mode: "MIMIC",
      recording_state: "IDLE",
      landmarks: {},
      motor_state: { motors: [], bus_communication_ok: false, stamp: now },
      safety_state: { state: "RUN", stamp: now },
      control_state: { active_mode: "MIMIC", active_owner: "NONE", stamp: now },
    });
    expect(result.current.connectionStatus.motor).toBe(CONNECTION_STATE.DOWN);
  });

  it("브릿지가 connection_status 를 주면 그것을 쓴다", () => {
    const now = utcNowZ();
    const { result, socket } = setup();
    emit(socket, {
      timestamp: now,
      mode: "MIMIC",
      recording_state: "IDLE",
      landmarks: { detected: true, stamp: now },
      motor_state: {},
      safety_state: { state: "RUN", stamp: now },
      connection_status: {
        jetson: "up", rpi: "down", ros2: "up", camera: "up", motor: "down",
      },
    });
    expect(result.current.connectionStatus.rpi).toBe(CONNECTION_STATE.DOWN);
  });
});

// ───────────────────────────────────────────────────────────────────────────
// 5. FR-27 장치 단절 경고
//
// connectionStatus 가 "unknown"|"up"|"down" 문자열로 바뀔 때 SafetyBanner 만
// bool 시절 코드(`!connectionStatus.ros2`)로 남아, 전 장치가 down 이어도 경고가
// 한 번도 뜨지 않았다.
// ───────────────────────────────────────────────────────────────────────────

describe("FR-27 장치 단절 경고", () => {
  const withConnection = (connection_status) => ({
    timestamp: utcNowZ(),
    mode: "MIMIC",
    recording_state: "IDLE",
    landmarks: {},
    motor_state: {},
    safety_state: { state: "RUN", motor_communication_ok: true },
    control_state: { active_mode: "MIMIC", active_owner: "NONE", owner_alive: false },
    connection_status,
  });

  it("전 장치 down 이면 경고를 띄운다", () => {
    // setup(<SafetyBanner />);
    emit(MockWebSocket.latest(), withConnection({
      jetson: "down", rpi: "down", ros2: "down", camera: "down", motor: "down",
    }));
    const text = document.body.textContent;
    expect(text).toContain("ROS 2 통신이 단절");
    expect(text).toContain("모터 통신이 단절");
  });

  it("unknown 은 단절로 단정하지 않는다", () => {
    // setup(<SafetyBanner />);
    emit(MockWebSocket.latest(), withConnection({
      jetson: "unknown", rpi: "unknown", ros2: "unknown",
      camera: "unknown", motor: "unknown",
    }));
    expect(document.body.textContent).not.toContain("단절");
  });
});

// ───────────────────────────────────────────────────────────────────────────
// 6. 실제 화면 preset 으로 버튼 누르기
//
// 기존 시험은 sendGesture 에 values 를 직접 넣어 호출했다. 실제 화면은
// commandPresets 의 객체를 그대로 넘기는데 그 객체에는 values 가 없어서,
// 검증기에 걸려 버튼 4개가 전부 아무것도 보내지 않았다. 시험이 실제 경로를
// 타지 않으면 이런 결함을 잡지 못한다.
// ───────────────────────────────────────────────────────────────────────────

describe("FR-22 조작 버튼이 실제로 전송한다", () => {
  const MANUAL_READY = {
    timestamp: utcNowZ(),
    mode: "MANUAL",
    recording_state: "IDLE",
    landmarks: {},
    motor_state: {},
    safety_state: { state: "RUN", motor_communication_ok: true, stamp: rosTime() },
    control_state: {
      active_mode: "MANUAL",
      active_owner: "WEB",
      owner_alive: true,
      sequence_running: false,
      stamp: rosTime(),
    },
    recording: { state: "IDLE", active_session_id: "", last_session_id: "" },
  };

  it("제스처 버튼 4개가 모두 execute_gesture 를 보낸다", () => {
    const { socket } = setup(<OrderMode />);
    emit(socket, MANUAL_READY);

    for (const preset of BASIC_GESTURES) {
      socket.sent.length = 0;
      const button = screen.getByTitle(preset.label);
      expect(button.disabled, `${preset.id} 버튼이 비활성`).toBe(false);
      act(() => { button.click(); });

      const sent = socket.sentOf(CLIENT_MESSAGE.EXECUTE_GESTURE);
      expect(sent, `${preset.id} 가 전송되지 않았다`).toHaveLength(1);
      expect(sent[0].payload.gesture_name).toBe(preset.id);

      // ack 로 잠금을 푼다 (sequence_running 에 의존하지 않는다)
      act(() => { socket.emit(acceptAck(sent[0].request_id)); });
    }
  });

  it("ack 전에는 다음 명령을 막고 ack 뒤에 푼다", () => {
    const { socket } = setup(<OrderMode />);
    emit(socket, MANUAL_READY);

    act(() => { screen.getByTitle("손 펴기").click(); });
    const first = socket.sentOf(CLIENT_MESSAGE.EXECUTE_GESTURE)[0];
    expect(screen.getByTitle("주먹 쥐기").disabled).toBe(true);

    act(() => { socket.emit(acceptAck(first.request_id)); });
    expect(screen.getByTitle("주먹 쥐기").disabled).toBe(false);
  });

  it("sequence_running 이 계속 false 여도 잠기지 않는다", () => {
    // 브릿지가 Gesture 에 sequence_running 을 세우지 않는 경우.
    // 이전 구현은 이 필드의 true→false 로 잠금을 풀어서 영구히 잠겼다.
    const { socket } = setup(<OrderMode />);
    emit(socket, MANUAL_READY);

    for (let i = 0; i < 3; i += 1) {
      socket.sent.length = 0;
      act(() => { screen.getByTitle("손 펴기").click(); });
      const sent = socket.sentOf(CLIENT_MESSAGE.EXECUTE_GESTURE);
      expect(sent, `${i + 1}번째 전송 실패`).toHaveLength(1);
      act(() => {
        socket.emit(acceptAck(sent[0].request_id));
        socket.emit(MANUAL_READY);   // sequence_running 은 계속 false
      });
    }
  });

  it("STOP 은 명령이 대기 중이어도 전송된다", () => {
    // 조작 화면의 「기능 중지」 버튼은 삭제했다. sendStop() 은
    // SetControlMode(DISABLED, NONE) 이라 동작 중단이 아니라 제어권 해제이고,
    // 그 흐름은 화면 이동 게이트가 맡는다 (components/ModeGate.jsx).
    // 계약상 확인할 것은 "대기 중인 요청이 있어도 STOP 이 나간다" 이므로
    // context 의 sendStop 을 직접 검사한다.
    const { result, socket } = setupHook();
    emit(socket, MANUAL_READY);

    act(() => { result.current.sendGesture("open", 1.0); });   // 잠금 상태로 만든다
    expect(result.current.commandInFlight).toBe(true);
    socket.sent.length = 0;
    act(() => { result.current.sendStop(); });
    expect(socket.sentOf(CLIENT_MESSAGE.STOP)).toHaveLength(1);
    expect(result.current.commandInFlight).toBe(false);
  });
});
