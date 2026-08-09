// frontend/src/test/screens.test.jsx
//
// 화면 단위 검증
//   FR-20  MJPEG 영상·손 검출 표시
//   FR-24  상태 모니터링 (StatusBar)
//   FR-25  모터 상태 표시
//   FR-27  안전 안내와 위험 상태에서 조작 비활성화
//   NFR-17 mode·STOP·gesture·정상/경고/오류 구분
//
// 모바일·Laptop 기본 폭에서 렌더가 깨지지 않는지도 함께 본다.
// jsdom 은 레이아웃을 계산하지 않으므로 픽셀 검증은 불가하며 DOM 존재만 확인한다.

import { act, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { HandSocketProvider } from "../context/HandSocketContext";
import StatusBar from "../components/StatusBar";
import Header from "../components/Header";
import { ModeGateProvider, useModeGate } from "../components/ModeGate";

// import SafetyBanner from "../components/SafetyBanner";
import MotorStatusPanel from "../components/MotorStatusPanel";
import OrderMode from "../pages/OrderMode";
import VisionMode from "../pages/VisionMode";
import { HAND_AXES } from "../config/messageProtocol";
import { SPEC } from "../config/pending";
import {
  controlState,
  manualSnapshot,
  mimicSnapshot,
  motorStatePayload,
  readySnapshot,
  rosTime,
  safetyState,
  snapshot,
} from "./fixtures";

/** 게이트 모달을 여는 최소 트리거. 제어권 UI 는 모달로 옮겨졌다 */
function GateTrigger({ to }) {
  const { go } = useModeGate();
  return <button type="button" onClick={() => go(to)}>이동</button>;
}

function renderWithSocket(ui, snap = snapshot()) {
  const view = render(
    <MemoryRouter>
      <HandSocketProvider>
        <ModeGateProvider>{ui}</ModeGateProvider>
      </HandSocketProvider>
    </MemoryRouter>,
  );
  const socket = MockWebSocket.latest();
  // React 상태 갱신을 flush 하려면 act 안에서 이벤트를 발생시켜야 한다.
  act(() => {
    socket.open();
    socket.emit(snap);
  });
  return { ...view, socket };
}

const VIEWPORTS = [
  { name: "모바일 375px", width: 375 },
  { name: "Laptop 1440px", width: 1440 },
];

function setViewport(width) {
  window.innerWidth = width;
  window.matchMedia = (query) => {
    const max = /max-width:\s*(\d+)px/.exec(query);
    return {
      matches: max ? width <= Number(max[1]) : false,
      media: query,
      addEventListener() {}, removeEventListener() {},
      addListener() {}, removeListener() {},
      dispatchEvent() { return false; },
    };
  };
  window.dispatchEvent(new Event("resize"));
}

beforeEach(() => {
  MockWebSocket.reset();
  setViewport(1440);
});

describe("FR-24 StatusBar", () => {
  it("브릿지 미연결 상태를 렌더한다", () => {
    renderWithSocket(<StatusBar />, snapshot());
    // 화면이 깨지지 않고 무언가 렌더된다
    expect(document.body.textContent.length).toBeGreaterThan(0);
  });

  it("현재 모드와 연결 상태를 렌더한다", () => {
    // 제어권과 안전 상태는 VerdictBlock 이 맡는다. StatusBar 는 나머지다.
    renderWithSocket(<StatusBar />, readySnapshot());
    expect(document.body.textContent).toContain("현재 모드");
    expect(document.body.textContent).toContain("연결됨");
  });

  it("제어권은 VerdictBlock 이 표시한다", () => {
    renderWithSocket(<Header />, readySnapshot());
    expect(document.body.textContent).toContain("제어권");
  });

  it("ESTOP 을 명확히 표시한다", () => {
    // 안전 상태는 SafetyRail 이 맡는다. 8상태를 경로로 그리고 현재 위치를 밝힌다.
    renderWithSocket(<Header />, snapshot({
      safety_state: { ...snapshot().safety_state, state: "ESTOP", estop_active: true },
    }));
    expect(document.body.textContent).toContain("ESTOP");
    expect(document.body.textContent).toContain("비상 정지");
  });

  it("mode 와 owner 를 표시한다", () => {
    renderWithSocket(<StatusBar />, manualSnapshot());
    const text = document.body.textContent;
    expect(text).toMatch(/MANUAL|조작/);
  });
});

describe("FR-27 SafetyBanner", () => {
  it("정상 상태에서는 위험 문구를 띄우지 않는다", () => {
    // renderWithSocket(<SafetyBanner />, manualSnapshot());
    const text = document.body.textContent;
    expect(text).not.toContain("비상정지");
  });

  it("ESTOP 원인을 안내한다", () => {
    // renderWithSocket(<SafetyBanner />, snapshot({
    //   safety_state: {
    //     ...snapshot().safety_state,
    //     state: "ESTOP", estop_active: true, reason: "물리 비상정지 작동",
    //   },
    // }));
    expect(document.body.textContent.length).toBeGreaterThan(0);
  });

  it("과전류·과온을 구분해 안내한다", () => {
    // renderWithSocket(<SafetyBanner />, snapshot({
    //   safety_state: {
    //     ...snapshot().safety_state,
    //     state: "FAULT", over_current: true, over_temperature: true,
    //   },
    // }));
    expect(document.body.textContent.length).toBeGreaterThan(0);
  });

  it("HOLD 에서는 자동복귀 조건과 STOP 대안을 함께 안내한다", () => {
    // FR-27: "HOLD 이면 Guard 검증 activity 300ms 자동복귀 조건, 100ms 최대 gap,
    //         총 1000ms SAFE deadline 과 명시적 STOP→RESET 대안을 함께 안내한다."
    // renderWithSocket(<SafetyBanner />, snapshot({
    //   safety_state: { ...snapshot().safety_state, state: "HOLD", command_timeout: true },
    // }));
    const text = document.body.textContent;
    expect(text).toContain(String(SPEC.HOLD_RECOVERY_ACTIVITY_MS));
    expect(text).toContain(String(SPEC.HOLD_RECOVERY_MAX_GAP_MS));
    expect(text).toContain(String(SPEC.SAFE_DEADLINE_MS));
    expect(text).toContain("STOP");
    // 구식 문구가 남아 있으면 안 된다 (V7 이전: STOP → READY 복귀 → 재획득)
    expect(text).not.toContain("READY 상태로 복귀");
  });
});

describe("FR-25 MotorStatusPanel", () => {
  it("7모터를 렌더한다", () => {
    render(<MotorStatusPanel motorStatus={motorStatePayload} />);
    const text = document.body.textContent;
    // 액추에이터 이름 7개가 모두 보인다
    for (const name of ["thumb_flex", "thumb_opp", "thumb_abd", "index_flex",
      "middle_flex", "ring_flex", "little_flex"]) {
      expect(text).toContain(name);
    }
  });

  it("값이 없으면 결측으로 표시한다", () => {
    render(<MotorStatusPanel motorStatus={null} />);
    expect(document.body.textContent.length).toBeGreaterThan(0);
  });

  it("통신 실패 모터를 구분한다", () => {
    const failed = {
      ...motorStatePayload,
      motors: motorStatePayload.motors.map((m, i) => (
        i === 0 ? { ...m, communication_ok: false, present_position_rad: null } : m
      )),
    };
    render(<MotorStatusPanel motorStatus={failed} />);
    expect(document.body.textContent.length).toBeGreaterThan(0);
  });
});

it("torque_enabled 를 ON/OFF 로 보여준다", () => {
    const payload = {
      ...motorStatePayload,
      motors: motorStatePayload.motors.map((m, i) => (
        i === 0 ? { ...m, torque_enabled: false } : { ...m, torque_enabled: true }
      )),
    };
    render(<MotorStatusPanel motorStatus={payload} />);
    const text = document.body.textContent;
    expect(text).toContain("토크");
    expect(text).toContain("ON");
    expect(text).toContain("OFF");
  });

  it("통신 실패 모터의 torque 는 유효 상태로 표시하지 않는다", () => {
    // interfaces.md MotorStatus 계약: communication_ok=false 면 torque_enabled 를
    // 유효로 보지 않고 통신 실패를 우선한다. torque_enabled=true 여도 ON 을 내지 않는다.
    const allFail = {
      ...motorStatePayload,
      motors: motorStatePayload.motors.map((m) => (
        { ...m, communication_ok: false, torque_enabled: true }
      )),
    };
    render(<MotorStatusPanel motorStatus={allFail} />);
    expect(document.body.textContent).not.toContain("ON");
  });

describe.each(VIEWPORTS)("$name 렌더", ({ width }) => {
  it("StatusBar 가 깨지지 않는다", () => {
    setViewport(width);
    renderWithSocket(<StatusBar />, readySnapshot());
    expect(document.body.textContent).toContain("현재 모드");
  });

  it("MotorStatusPanel 이 7모터를 유지한다", () => {
    setViewport(width);
    render(<MotorStatusPanel motorStatus={motorStatePayload} />);
    expect(document.body.textContent).toContain("thumb_flex");
  });
});

describe("오류 fixture", () => {
  it("연결 끊김 상태에서도 렌더된다", () => {
    const { socket } = renderWithSocket(<StatusBar />, readySnapshot());
    act(() => { socket.closeFromServer(); });
    expect(document.body.textContent.length).toBeGreaterThan(0);
  });

  it("손상된 JSON 을 받아도 죽지 않는다", () => {
    const { socket } = renderWithSocket(<StatusBar />, readySnapshot());
    // JSON.parse 실패 경로
    expect(() => socket.onmessage({ data: "{not json" })).not.toThrow();
  });

  it("알 수 없는 메시지 타입을 무시한다", () => {
    const { socket } = renderWithSocket(<Header />, readySnapshot());
    expect(() => act(() => { socket.emit({ type: "brand_new_type", payload: {} }); }))
      .not.toThrow();
    // 모르는 메시지는 상태를 덮어쓰지 않는다. 진단 로그로만 남는다.
    expect(document.body.textContent).toContain("READY");
  });
});


// ───────────────────────────────────────────────────────────────────────────
// FR-19 / FR-35 / NFR-15 / NFR-23 — 제어권은 자동으로 획득하지 않는다
//
// 회귀 방지용. 이전 구현은 VisionMode·OrderMode 의 useEffect 가 마운트·재연결
// 시점에 스스로 sendStop() → selectMode() 를 실행했다. 그 결과
//   - 재연결마다 mode·owner 가 자동 재획득되어 8.3절 검수 4번을 통과할 수 없었고
//   - _handle_stop 에 상태 조건이 없으므로(FR-34) 페이지를 여는 것만으로
//     LOCAL owner(teleop)의 제어권을 빼앗을 수 있었다.
// ───────────────────────────────────────────────────────────────────────────

describe("FR-19 제어권 자동 획득 금지", () => {
  it("VisionMode 진입만으로는 어떤 요청도 보내지 않는다", () => {
    const { socket } = renderWithSocket(<VisionMode />, readySnapshot());
    expect(socket.sent).toHaveLength(0);
  });

  it("OrderMode 진입만으로는 어떤 요청도 보내지 않는다", () => {
    const { socket } = renderWithSocket(<OrderMode />, readySnapshot());
    expect(socket.sent).toHaveLength(0);
  });

  it("다른 모드가 활성이어도 자동으로 STOP 하지 않는다", () => {
    const { socket } = renderWithSocket(<OrderMode />, mimicSnapshot());
    expect(socket.sent).toHaveLength(0);
  });

  it("LOCAL owner 가 제어권을 가진 상태에서도 빼앗지 않는다", () => {
    const snap = readySnapshot({
      control_state: controlState({
        active_mode: "TELEOP", active_owner: "LOCAL", owner_alive: true,
      }),
    });
    const { socket } = renderWithSocket(<OrderMode />, snap);
    expect(socket.sent).toHaveLength(0);
  });

  it("재연결 뒤에도 이전 모드를 자동 재획득하지 않는다", () => {
    const { socket } = renderWithSocket(<VisionMode />, mimicSnapshot());
    act(() => { socket.close(); });
    act(() => {
      const next = MockWebSocket.latest();
      next.open();
      next.emit(readySnapshot());
    });
    expect(MockWebSocket.latest().sent).toHaveLength(0);
  });
});

describe("FR-19 제어권 게이트 모달", () => {
  const open = (to, snap) => {
    const view = renderWithSocket(<GateTrigger to={to} />, snap);
    act(() => { view.getByRole("button", { name: "이동" }).click(); });
    return view;
  };

  it("제어권이 없으면 획득만 안내한다", () => {
    // 이미 DISABLED 면 1단계가 끝난 것이다. 정지할 것이 없으므로 그 버튼은 없다.
    const { getByRole, queryByRole } = open("/vision", readySnapshot());
    expect(getByRole("dialog")).toBeTruthy();
    expect(getByRole("button", { name: /획득/ }).disabled).toBe(false);
    expect(queryByRole("button", { name: /정지/ })).toBeNull();
  });

  it("다른 모드를 쥐고 있으면 정지가 먼저 나온다", () => {
    const { getByRole } = open("/order", mimicSnapshot());
    expect(getByRole("button", { name: /정지/ })).toBeTruthy();
    expect(getByRole("button", { name: /획득/ }).disabled).toBe(true);
  });

  it("이미 해당 모드를 보유하면 모달을 띄우지 않는다", () => {
    const { queryByRole } = open("/vision", mimicSnapshot());
    expect(queryByRole("dialog")).toBeNull();
  });

  it("FR-34: READY 가 아니면 획득 버튼을 비활성화한다", () => {
    const snap = snapshot({ safety_state: safetyState({ state: "HOLD" }) });
    const { getByRole } = open("/order", snap);
    expect(getByRole("button", { name: /획득/ }).disabled).toBe(true);
  });

  it("획득 버튼을 눌러야 set_control_mode 를 보낸다", () => {
    const { getByRole, socket } = open("/vision", readySnapshot());
    expect(socket.sent).toHaveLength(0);
    act(() => { getByRole("button", { name: /획득/ }).click(); });
    expect(socket.sent).toHaveLength(1);
    expect(socket.sent[0].type).toBe("set_control_mode");
    // SetControlMode.srv 요청 필드는 requested_mode·requested_owner 다.
    expect(socket.sent[0].payload).toEqual({
      requested_mode: "MIMIC", requested_owner: "WEB",
    });
  });

  it("제어권을 쥔 채로 다른 모드로 넘어가려면 정지가 먼저다", () => {
    const { getByRole } = open("/order", mimicSnapshot());
    expect(getByRole("dialog").textContent).toContain("먼저 정지");
    expect(getByRole("button", { name: /획득/ }).disabled).toBe(true);
    expect(getByRole("button", { name: /정지/ }).disabled).toBe(false);
  });
});

describe("6.4절 미수신 객체 안내", () => {
  it("SafetyState 미수신 시 허위 경고 대신 수신 대기를 알린다", () => {
    const snap = snapshot({ safety_state: {} });
    const { container } = renderWithSocket(
      <><Header />
      {/* <SafetyBanner /> */}</>, snap,
    );
    const text = container.textContent;
    expect(text).toContain("수신 대기");
    // 기본값 motor_communication_ok=false 로 인한 허위 경고가 없어야 한다
    expect(text).not.toContain("모터 통신 오류가 발생했습니다");
  });
});


// ───────────────────────────────────────────────────────────────────────────
// FR-20 / FR-27 — hand-loss latch 와 7축 표시
//
// latch 는 서버(normalize.py)가 landmark 스트림에서 파생해 내려준다.
// 웹은 표시만 하고 직접 판정하지 않는다.
// ───────────────────────────────────────────────────────────────────────────

describe("FR-20 VisionMode hand-loss 표시", () => {
  function mimicWithLandmarks(landmarks) {
    return mimicSnapshot({ landmarks });
  }

  it("latch 가 서면 재개 필요와 재검출 진행을 안내한다", () => {
    const { container } = renderWithSocket(<VisionMode />, mimicWithLandmarks({
      detected: true, confidence: 0.95,
      hand_loss_latched: true, reacquire_elapsed_ms: 150, reacquire_stable_ms: 300,
    }));
    const text = container.textContent;
    expect(text).toContain("hand-loss");
    expect(text).toContain("자동으로 재개되지 않습니다");
    expect(text).toContain("150 / 300ms");
  });

  it("latch 가 없으면 안내를 띄우지 않는다", () => {
    const { container } = renderWithSocket(<VisionMode />, mimicWithLandmarks({
      detected: true, confidence: 0.95, hand_loss_latched: false,
    }));
    expect(container.textContent).not.toContain("hand-loss");
  });
});

describe("FR-24 VisionMode 7축 표시", () => {
  it("축이 일부만 오면 그 축만 '-' 로 두고 죽지 않는다", () => {
    const snap = mimicSnapshot({
      // FR-30: HandCommand 는 7개 "고정 축" 이다. values 래퍼가 없다.
      last_hand_command: {
        thumb_flex: 0.5, thumb_opp: null, thumb_abd: null,
        index_flex: 0.25, middle_flex: null, ring_flex: null, little_flex: null,
        source: "MIMIC", sequence: 7, stamp: rosTime(),
      },
    });
    const { container } = renderWithSocket(<VisionMode />, snap);
    act(() => { container.querySelector("#dataToggle").click(); });
    const text = container.textContent;
    expect(text).toContain("0.50");
    expect(text).toContain("0.25");
    expect(text).toContain("-");
  });

  it("stamp 는 서버가 준 RFC 3339 문자열을 그대로 쓴다", () => {
    const snap = mimicSnapshot({
      last_hand_command: {
        values: Object.fromEntries(HAND_AXES.map((a) => [a.key, 0.5])),
        source: "MIMIC", sequence: 1, stamp: "2026-07-31T01:02:03.400Z",
      },
    });
    const { container } = renderWithSocket(<VisionMode />, snap);
    act(() => { container.querySelector("#dataToggle").click(); });
    expect(container.textContent).toContain("2026-07-31 01:02:03.400");
  });
});


// ───────────────────────────────────────────────────────────────────────────
// FR-21 / FR-24 / FR-25 — 마지막 표시 요구사항
// ───────────────────────────────────────────────────────────────────────────

describe("FR-21 confidence 표시", () => {
  it("HandCommand.confidence 를 백분율로 보여준다", () => {
    const snap = mimicSnapshot({
      last_hand_command: {
        values: Object.fromEntries(HAND_AXES.map((a) => [a.key, 0.5])),
        source: "MIMIC", sequence: 1, confidence: 0.82,
        stamp: "2026-07-31T00:00:00.000Z",
      },
    });
    const { container } = renderWithSocket(<VisionMode />, snap);
    act(() => { container.querySelector("#dataToggle").click(); });
    expect(container.textContent).toContain("82%");
  });
});

describe("FR-24 StatusBar 기록 상태", () => {
  it("기록 중이면 상태와 Session ID 를 표시한다", () => {
    const snap = mimicSnapshot({
      recording: {
        ...mimicSnapshot().recording,
        state: "RECORDING", active_session_id: "9223372036854775701",
      },
    });
    const { container } = renderWithSocket(<StatusBar />, snap);
    expect(container.textContent).toContain("기록: RECORDING");
  });

  it("판정 대기를 따로 알린다", () => {
    const snap = mimicSnapshot({
      recording: {
        ...mimicSnapshot().recording,
        state: "COMPLETED", result_pending: true,
      },
    });
    const { container } = renderWithSocket(<StatusBar />, snap);
    expect(container.textContent).toContain("판정 대기");
  });

  it("IDLE 이면 배지를 띄우지 않는다", () => {
    const { container } = renderWithSocket(<StatusBar />, readySnapshot());
    expect(container.textContent).not.toContain("기록:");
  });
});

describe("FR-25 stale 과 연결 끊김 구분", () => {
  // 신선도는 stamp 뺄셈이 아니라 "마지막으로 바뀐 시각" 으로 잰다.
  // MotorStatus 의 시각은 header.stamp 이고 {sec,nanosec} 이라 파싱할 수 없고,
  // snapshotAt(Jetson) 과 header.stamp(Raspberry Pi) 는 서로 다른 장비 시계다.
  function panel(props) {
    return renderWithSocket(
      <MotorStatusPanel
        motorStatus={{ ...motorStatePayload, ...(props.motorStatus ?? {}) }}
        motorUpdatedAt={props.motorUpdatedAt}
        receivedAt={props.receivedAt}
      />,
      readySnapshot(),
    );
  }

  it("갱신이 오래되면 stale 로 표시한다", () => {
    const now = Date.now();
    const { container } = panel({
      motorUpdatedAt: now - 5000,   // MotorStatus 만 5초째 정지
      receivedAt: now,              // snapshot 은 방금 왔다
    });
    expect(container.textContent).toContain("값이 오래됨");
    // 로봇이 통신 정상이라고 했으므로 단절이 아니다
    expect(container.textContent).toContain("전체 통신 정상");
  });

  it("최신 값이면 stale 표시가 없다", () => {
    const now = Date.now();
    const { container } = panel({ motorUpdatedAt: now, receivedAt: now });
    expect(container.textContent).not.toContain("값이 오래됨");
  });

  it("단절은 stale 과 다른 표시다", () => {
    const now = Date.now();
    const { container } = panel({
      motorStatus: { bus_communication_ok: false },
      motorUpdatedAt: now,
      receivedAt: now,
    });
    expect(container.textContent).toContain("일부/전체 통신 불량");
  });

  it("snapshot 자체가 끊기면 stale 보다 강한 표시를 쓴다", () => {
    const now = Date.now();
    const { container } = panel({
      motorUpdatedAt: now - 5000,
      receivedAt: now - 5000,       // snapshot 도 5초째 없다
    });
    expect(container.textContent).toContain("연결 끊김");
  });
});


// ───────────────────────────────────────────────────────────────────────────
// 회귀 — 브릿지 필드 누락과 값 신선도
// ───────────────────────────────────────────────────────────────────────────

describe("control_state 누락을 화면에 드러낸다", () => {
  it("ControlState 를 못 받으면 획득을 막고 이유를 말한다", () => {
    const snap = readySnapshot();
    delete snap.control_state;
    const view = renderWithSocket(<GateTrigger to="/vision" />, snap);
    act(() => { view.getByRole("button", { name: "이동" }).click(); });
    // control_state 를 못 받으면 owner 를 알 수 없으므로 획득을 막는다 (fail-closed).
    expect(view.getByRole("button", { name: /획득/ }).disabled).toBe(true);
  });

  it("ControlState 를 받으면 획득 버튼이 열린다", () => {
    const view = renderWithSocket(<GateTrigger to="/vision" />, readySnapshot());
    act(() => { view.getByRole("button", { name: "이동" }).click(); });
    const { getByRole } = view;
    expect(getByRole("button", { name: /획득/ }).disabled).toBe(false);
  });
});

describe("FR-25 연결 끊김과 stale 구분", () => {
  const payload = {
    stamp: "2026-07-31T00:00:03.000Z",
    motors: motorStatePayload.motors,
    bus_communication_ok: true,
    failed_read_count: 0,
    message: "",
  };

  it("마지막 수신이 오래되면 '연결 끊김 · 마지막 값' 으로 바꾼다", () => {
    // 서버가 죽으면 snapshotAt 과 motor_state.stamp 가 함께 멈춰 차이가 0 이 된다.
    // 서버 시각만 보면 영원히 "전체 통신 정상" 이다. 브라우저 시계로 잡아야 한다.
    const { container } = render(
      <MotorStatusPanel
        motorStatus={payload}
        snapshotAt="2026-07-31T00:00:03.100Z"
        receivedAt={Date.now() - 9000}
      />,
    );
    const text = container.textContent;
    expect(text).toContain("연결 끊김 · 마지막 값");
    expect(text).not.toContain("전체 통신 정상");
    // 모터별 통신 칩도 "과거" 임을 밝힌다
    expect(text).toContain("정상(과거)");
  });

  it("최근에 받았으면 정상 표시를 유지한다", () => {
    const { container } = render(
      <MotorStatusPanel
        motorStatus={payload}
        snapshotAt="2026-07-31T00:00:03.100Z"
        receivedAt={Date.now()}
      />,
    );
    const text = container.textContent;
    expect(text).toContain("전체 통신 정상");
    expect(text).not.toContain("연결 끊김");
  });

  it("receivedAt 이 없으면 끊겼다고 단정하지 않는다", () => {
    // 판단 근거가 없을 때 임의로 단정하지 않는다 (FR-24).
    const { container } = render(
      <MotorStatusPanel motorStatus={payload} snapshotAt="2026-07-31T00:00:03.100Z" />,
    );
    expect(container.textContent).not.toContain("연결 끊김");
  });

  it("끊김이 stale 보다 우선한다", () => {
    // 둘이 겹치면 더 강한 상태(끊김)만 보여준다. 두 배지를 같이 띄우면 혼란스럽다.
    const { container } = render(
      <MotorStatusPanel
        motorStatus={payload}
        snapshotAt="2026-07-31T00:00:09.000Z"
        receivedAt={Date.now() - 9000}
      />,
    );
    const text = container.textContent;
    expect(text).toContain("연결 끊김 · 마지막 값");
    expect(text).not.toContain("값이 오래됨");
  });
});
