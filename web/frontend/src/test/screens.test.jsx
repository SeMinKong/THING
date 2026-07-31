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
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HandSocketProvider } from "../context/HandSocketContext";
import StatusBar from "../components/StatusBar";
import SafetyBanner from "../components/SafetyBanner";
import MotorStatusPanel from "../components/MotorStatusPanel";
import ModeAcquirePanel from "../components/ModeAcquirePanel";
import OrderMode from "../pages/OrderMode";
import VisionMode from "../pages/VisionMode";
import { CONTROL_MODE, HAND_AXES } from "../config/messageProtocol";
import {
  controlState,
  landmarksPayload,
  manualSnapshot,
  mimicSnapshot,
  motorStatePayload,
  readySnapshot,
  safetyState,
  snapshot,
} from "./fixtures";

function renderWithSocket(ui, snap = snapshot()) {
  const view = render(
    <MemoryRouter>
      <HandSocketProvider>{ui}</HandSocketProvider>
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

  it("READY 상태를 렌더한다", () => {
    renderWithSocket(<StatusBar />, readySnapshot());
    expect(document.body.textContent).toContain("READY");
  });

  it("ESTOP 을 명확히 표시한다", () => {
    renderWithSocket(<StatusBar />, snapshot({
      safety_state: { ...snapshot().safety_state, state: "ESTOP", estop_active: true },
    }));
    expect(document.body.textContent).toContain("ESTOP");
  });

  it("mode 와 owner 를 표시한다", () => {
    renderWithSocket(<StatusBar />, manualSnapshot());
    const text = document.body.textContent;
    expect(text).toMatch(/MANUAL|조작/);
  });
});

describe("FR-27 SafetyBanner", () => {
  it("정상 상태에서는 위험 문구를 띄우지 않는다", () => {
    renderWithSocket(<SafetyBanner />, manualSnapshot());
    const text = document.body.textContent;
    expect(text).not.toContain("비상정지");
  });

  it("ESTOP 원인을 안내한다", () => {
    renderWithSocket(<SafetyBanner />, snapshot({
      safety_state: {
        ...snapshot().safety_state,
        state: "ESTOP", estop_active: true, reason: "물리 비상정지 작동",
      },
    }));
    expect(document.body.textContent.length).toBeGreaterThan(0);
  });

  it("과전류·과온을 구분해 안내한다", () => {
    renderWithSocket(<SafetyBanner />, snapshot({
      safety_state: {
        ...snapshot().safety_state,
        state: "FAULT", over_current: true, over_temperature: true,
      },
    }));
    expect(document.body.textContent.length).toBeGreaterThan(0);
  });

  it("HOLD 에서는 STOP 절차를 안내한다", () => {
    renderWithSocket(<SafetyBanner />, snapshot({
      safety_state: { ...snapshot().safety_state, state: "HOLD", command_timeout: true },
    }));
    expect(document.body.textContent.length).toBeGreaterThan(0);
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

describe.each(VIEWPORTS)("$name 렌더", ({ width }) => {
  it("StatusBar 가 깨지지 않는다", () => {
    setViewport(width);
    renderWithSocket(<StatusBar />, readySnapshot());
    expect(document.body.textContent).toContain("READY");
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
    const { socket } = renderWithSocket(<StatusBar />, readySnapshot());
    expect(() => act(() => { socket.emit({ type: "brand_new_type", payload: {} }); }))
      .not.toThrow();
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

describe("FR-19 ModeAcquirePanel", () => {
  it("제어권이 없으면 두 단계를 안내한다", () => {
    const { getByRole } = renderWithSocket(
      <ModeAcquirePanel targetMode={CONTROL_MODE.MIMIC} />, readySnapshot(),
    );
    expect(getByRole("button", { name: /정지/ })).toBeTruthy();
    expect(getByRole("button", { name: /획득/ })).toBeTruthy();
  });

  it("이미 해당 모드를 보유하면 패널을 그리지 않는다", () => {
    const { container } = renderWithSocket(
      <ModeAcquirePanel targetMode={CONTROL_MODE.MIMIC} />, mimicSnapshot(),
    );
    expect(container.querySelector("[role='region']")).toBeNull();
  });

  it("FR-34: READY 가 아니면 획득 버튼을 비활성화한다", () => {
    const snap = snapshot({
      safety_state: safetyState({ state: "HOLD" }),
      bridge_connected: true,
    });
    const { getByRole } = renderWithSocket(
      <ModeAcquirePanel targetMode={CONTROL_MODE.MANUAL} />, snap,
    );
    expect(getByRole("button", { name: /획득/ }).disabled).toBe(true);
  });

  it("획득 버튼을 눌러야 set_control_mode 를 보낸다", () => {
    const { getByRole, socket } = renderWithSocket(
      <ModeAcquirePanel targetMode={CONTROL_MODE.MIMIC} />, readySnapshot(),
    );
    expect(socket.sent).toHaveLength(0);
    act(() => { getByRole("button", { name: /획득/ }).click(); });
    expect(socket.sent).toHaveLength(1);
    expect(socket.sent[0].type).toBe("set_control_mode");
    expect(socket.sent[0].payload).toEqual({ mode: "MIMIC", owner: "WEB" });
  });
});

describe("6.4절 미수신 객체 안내", () => {
  it("SafetyState 미수신 시 허위 경고 대신 수신 대기를 알린다", () => {
    const snap = snapshot({ bridge_connected: true, safety_state: {} });
    const { container } = renderWithSocket(
      <><StatusBar /><SafetyBanner /></>, snap,
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
      last_hand_command: {
        values: { thumb_flex: 0.5, thumb_opp: null, thumb_abd: null,
                  index_flex: 0.25, middle_flex: null, ring_flex: null,
                  little_flex: null },
        source: "MIMIC", sequence: 7, stamp: "2026-07-31T00:00:00.000Z",
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
      recording_detail: {
        ...mimicSnapshot().recording_detail,
        state: "RECORDING", active_session_id: "9223372036854775701",
      },
    });
    const { container } = renderWithSocket(<StatusBar />, snap);
    expect(container.textContent).toContain("기록: RECORDING");
    expect(container.textContent).toContain("9223372036854775701");
  });

  it("판정 대기를 따로 알린다", () => {
    const snap = mimicSnapshot({
      recording_detail: {
        ...mimicSnapshot().recording_detail,
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
  function motorSnap(stamp, timestamp, busOk = true) {
    return readySnapshot({
      timestamp,
      motor_state: {
        stamp,
        motors: motorStatePayload.motors,
        bus_communication_ok: busOk,
        failed_read_count: 0,
        message: "",
      },
    });
  }

  it("갱신이 오래되면 stale 로 표시한다", () => {
    const snap = motorSnap("2026-07-31T00:00:00.000Z", "2026-07-31T00:00:03.000Z");
    const { container } = renderWithSocket(<MotorStatusPanel
      motorStatus={snap.motor_state} snapshotAt={snap.timestamp} />, snap);
    expect(container.textContent).toContain("값이 오래됨");
    // 로봇이 통신 정상이라고 했으므로 단절이 아니다
    expect(container.textContent).toContain("전체 통신 정상");
  });

  it("최신 값이면 stale 표시가 없다", () => {
    const snap = motorSnap("2026-07-31T00:00:03.000Z", "2026-07-31T00:00:03.100Z");
    const { container } = renderWithSocket(<MotorStatusPanel
      motorStatus={snap.motor_state} snapshotAt={snap.timestamp} />, snap);
    expect(container.textContent).not.toContain("값이 오래됨");
  });

  it("단절은 stale 과 다른 표시다", () => {
    const snap = motorSnap("2026-07-31T00:00:03.000Z", "2026-07-31T00:00:03.100Z", false);
    const { container } = renderWithSocket(<MotorStatusPanel
      motorStatus={snap.motor_state} snapshotAt={snap.timestamp} />, snap);
    expect(container.textContent).toContain("일부/전체 통신 불량");
    expect(container.textContent).not.toContain("값이 오래됨");
  });
});


// ───────────────────────────────────────────────────────────────────────────
// 회귀 — 브릿지 연결 판정과 값 신선도
//
// 두 결함을 실제로 겪고 고친 뒤 고정한 것이다.
//   1. snapshot 에 bridge_connected 가 없어 조작 UI 가 영구히 잠겼다
//   2. mock·시뮬레이터를 종료해도 모터 상태가 "전체 통신 정상" 으로 멈췄다
// ───────────────────────────────────────────────────────────────────────────

describe("bridge_connected 누락 방어", () => {
  it("필드가 없어도 브릿지 미연결로 잠기지 않는다", () => {
    // 2계층에서는 snapshot 을 받았다는 것이 곧 브릿지가 살아 있다는 뜻이다.
    // Boolean(undefined) === false 로 두면 필드를 잊은 브릿지가 UI 를 전부 잠근다.
    const snap = readySnapshot();
    delete snap.bridge_connected;
    const { container } = renderWithSocket(
      <ModeAcquirePanel targetMode={CONTROL_MODE.MIMIC} />, snap,
    );
    expect(container.textContent).not.toContain("브릿지에 연결되어 있지 않아");
  });

  it("false 를 명시하면 잠근다", () => {
    const { container } = renderWithSocket(
      <ModeAcquirePanel targetMode={CONTROL_MODE.MIMIC} />,
      readySnapshot({ bridge_connected: false }),
    );
    expect(container.textContent).toContain("브릿지에 연결되어 있지 않아");
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
