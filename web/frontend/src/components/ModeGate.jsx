// ============================================================================
// 모드 게이트 — 제어권 획득·해제를 화면 이동에 묶는다
// ----------------------------------------------------------------------------
// 이전에는 제어권 패널이 각 모드 화면 안에 자리를 차지하고 있었다. 그래서
// 페이지에 들어간 뒤에야 "먼저 정지하세요" 를 읽게 됐고, 그 안내가 본 작업
// 영역을 밀어냈다.
//
// 이제 순서를 뒤집는다. 이동을 요청하면 먼저 모달이 뜨고, 그 자리에서
// 정지·획득을 마쳐야 화면이 바뀐다.
//
//   개요 → 모방      DISABLED 면 획득 모달. 획득을 누르면 요청과 이동을 함께 한다
//   모방 → 조작      MIMIC 을 쥐고 있으면 정지 모달. 정지해야 넘어간다
//   모방 → 개요      같음. 제어권을 쥔 채로 나가지 못한다
//   모방 → 모방      이미 그 모드면 그냥 이동
//
// FR-19 의 "DISABLED 를 경유하는 두 단계" 를 화면 흐름으로 만든 것이다.
// 획득 가능 조건 판단은 이전 패널과 같다 (FR-34 / ACQUIRE_ALLOWED_STATES).
// ============================================================================
import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";

import { useHandSocket } from "../context/HandSocketContext";
import {
  ACQUIRE_ALLOWED_STATES,
  CONTROL_MODE,
  CONTROL_OWNER,
  RECORDING_BUSY_STATES,
} from "../config/messageProtocol";

const ModeGateContext = createContext(null);

/** 경로 ↔ 그 경로에서 필요한 mode. 개요는 어떤 mode 도 필요하지 않다. */
// eslint-disable-next-line react-refresh/only-export-components
export const ROUTE_MODE = {
  "/": null,
  "/vision": CONTROL_MODE.MIMIC,
  "/order": CONTROL_MODE.MANUAL,
};

const MODE_LABEL = {
  [CONTROL_MODE.MIMIC]: "모방",
  [CONTROL_MODE.MANUAL]: "조작",
};

// eslint-disable-next-line react-refresh/only-export-components
export function useModeGate() {
  const ctx = useContext(ModeGateContext);
  if (!ctx) throw new Error("ModeGateProvider 안에서만 쓸 수 있습니다");
  return ctx;
}

export function ModeGateProvider({ children }) {
  const navigate = useNavigate();
  const location = useLocation();
  const {
    connectionState, controlState, controlStateKnown,
    safetyState, safetyStateKnown, recordingState,
    requestedMode, modeRejectedReason, webHasControl,
    selectMode, sendStop,
  } = useHandSocket();

  // 이동 요청 대상. null 이면 모달이 닫힌 상태다.
  const [pending, setPending] = useState(null);
  // 획득 요청을 보낸 뒤 확정을 기다리는 대상. 확정되면 이동한다.
  // ref 가 아니라 state 다 — 모달이 "획득 중…" 을 렌더에 써야 하기 때문이다.
  const [awaiting, setAwaiting] = useState(null);

  const activeMode = controlState.active_mode;
  const holdsControl = webHasControl && activeMode !== CONTROL_MODE.DISABLED;

  /**
   * 화면 이동을 요청한다. 조건이 맞으면 바로 이동하고, 아니면 모달을 띄운다.
   */
  const go = useCallback((to) => {
    if (to === location.pathname) return;

    const need = ROUTE_MODE[to] ?? null;

    // 이미 그 모드를 쥐고 있으면 그냥 이동한다.
    if (need && activeMode === need && webHasControl) {
      navigate(to);
      return;
    }
    // 제어권을 쥐고 있지 않고 목적지가 개요면 막을 이유가 없다.
    if (!need && !holdsControl) {
      navigate(to);
      return;
    }
    setPending(to);
  }, [activeMode, holdsControl, location.pathname, navigate, webHasControl]);

  // 획득 요청이 control_state 로 확정되면 그때 이동한다.
  // "획득과 동시에 이동" 이지만, 확정 없이 이동하면 그 화면이 잠긴 상태로 열린다.
  // 확정을 기다리되 사용자에게는 진행 중임을 보여 준다.
  useEffect(() => {
    if (!awaiting) return;
    if (webHasControl && activeMode === (ROUTE_MODE[awaiting] ?? null)) {
      // 확정됐다. 모달을 닫고 이동한다.
      // control_state 는 로봇이 권위를 갖는 외부 상태다. 그 값이 바뀔 때 화면을
      // 맞추는 것이 이 effect 의 일이라 setState 가 들어가는 것이 정상이다.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setAwaiting(null);
      setPending(null);
      navigate(awaiting);
    }
  }, [activeMode, awaiting, navigate, webHasControl]);

  // 거부되면 대기를 풀어 모달에 사유가 보이게 한다.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (modeRejectedReason) setAwaiting(null);
  }, [modeRejectedReason]);

  // 제어권을 잃으면 모드 화면에 머물 이유가 없다. 개요로 돌린다.
  //
  // 모달이 떠 있는 동안에는 발동하지 않는다. 사용자가 목적지를 정해 두고 정지를
  // 누른 상황이라, 여기서 개요로 보내면 그 목적지가 버려진다.
  // (모방 → 조작 이동 중 정지를 눌렀는데 조작이 아니라 개요로 가던 버그)
  useEffect(() => {
    if (pending) return;
    const need = ROUTE_MODE[location.pathname] ?? null;
    if (need && controlStateKnown && !webHasControl && activeMode === CONTROL_MODE.DISABLED) {
      navigate("/");
    }
  }, [activeMode, controlStateKnown, location.pathname, navigate, pending, webHasControl]);

  const close = useCallback(() => {
    setAwaiting(null);
    setPending(null);
  }, []);

  const value = useMemo(() => ({ go, holdsControl }), [go, holdsControl]);

  // ── 모달에 필요한 판정 ──────────────────────────────────────────────────
  const need = pending ? (ROUTE_MODE[pending] ?? null) : null;
  const isConnected = connectionState === "open";
  const isDisabled = activeMode === CONTROL_MODE.DISABLED;
  const otherOwner = (
    controlState.active_owner !== CONTROL_OWNER.NONE
    && controlState.active_owner !== CONTROL_OWNER.WEB
  );
  // FR-34: 획득은 READY 에서만 가능하다.
  const safetyReady = safetyStateKnown && ACQUIRE_ALLOWED_STATES.includes(safetyState.state);
  const recordingBusy = RECORDING_BUSY_STATES.includes(recordingState.state);

  const canStop = isConnected && !isDisabled;
  const canAcquire = Boolean(
    need && isConnected && controlStateKnown && isDisabled && safetyReady
    && !otherOwner && !recordingBusy,
  );

  // 왜 못 얻는지 하나만 말한다. 목록으로 나열하면 읽지 않는다.
  const blocked = !isConnected ? "서버에 연결되어 있지 않습니다."
    : !controlStateKnown ? "로봇의 제어 상태를 아직 받지 못했습니다."
    : !isDisabled ? "다른 모드가 활성화되어 있습니다. 먼저 정지하세요."
    : otherOwner ? `${controlState.active_owner} 이(가) 제어권을 보유하고 있습니다.`
    : recordingBusy ? `기록 중(${recordingState.state})에는 모드를 바꿀 수 없습니다.`
    : !safetyReady
      ? (safetyStateKnown
        ? `안전 상태 ${safetyState.state} 에서는 획득할 수 없습니다.`
        : "안전 상태를 아직 받지 못했습니다.")
      : "";

  const handleAcquire = () => {
    if (!canAcquire || !pending) return;
    setAwaiting(pending);
    selectMode(need);
  };

  // 요구사항: "정지를 누르면 정지하고, 획득을 누르면 획득과 이동을 함께 한다."
  // 그래서 정지는 이동하지 않는다. 목적지가 모드를 요구하면 모달에 머물면서
  // 획득 버튼이 열리기를 기다린다 — FR-19 의 두 단계를 모달 안에서 마치는 것이다.
  const handleStop = () => {
    setAwaiting(null);
    sendStop();
  };

  // 목적지가 모드를 요구하지 않는 경우(개요)만, 정지가 확정되면 이동한다.
  // 그쪽에는 누를 획득 버튼이 없으므로 정지가 곧 이동의 완료다.
  useEffect(() => {
    if (!pending || ROUTE_MODE[pending]) return;
    if (isDisabled && !webHasControl) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPending(null);
      navigate(pending);
    }
  }, [isDisabled, navigate, pending, webHasControl]);

  return (
    <ModeGateContext.Provider value={value}>
      {children}
      {createPortal(
        <AnimatePresence>
          {pending && (
            <GateModal
              targetLabel={need ? MODE_LABEL[need] ?? need : "개요"}
              needsMode={Boolean(need)}
              activeLabel={MODE_LABEL[activeMode] ?? activeMode}
              isDisabled={isDisabled}
              canStop={canStop}
              canAcquire={canAcquire}
              note={modeRejectedReason || blocked}
              requestedMode={requestedMode}
              waiting={awaiting !== null}
              onStop={handleStop}
              onAcquire={handleAcquire}
              onClose={close}
            />
          )}
        </AnimatePresence>,
        document.body,
      )}
    </ModeGateContext.Provider>
  );
}

function GateModal({
  targetLabel, needsMode, activeLabel, isDisabled, canStop, canAcquire,
  note, requestedMode, waiting, onStop, onAcquire, onClose,
}) {
  const panelRef = useRef(null);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") { onClose(); return; }
      if (e.key !== "Tab") return;
      const items = panelRef.current?.querySelectorAll(
        'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      );
      if (!items?.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    panelRef.current?.querySelector("button:not([disabled])")?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  return (
    <motion.div
      className="fixed inset-0 z-50 grid place-items-center bg-ink-900/45 p-4"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.16 }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <motion.div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={`${targetLabel} 이동`}
        initial={{ opacity: 0, y: 14, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 8, scale: 0.99 }}
        transition={{ type: "spring", stiffness: 340, damping: 32 }}
        className="w-full max-w-[460px] overflow-hidden rounded-card bg-ink-0 shadow-2xl"
      >
        <div className="px-6 pt-5">
          <h2 className="text-lg font-bold tracking-[-0.01em]">
            {needsMode ? `${targetLabel} 모드로 이동` : "개요로 이동"}
          </h2>
          <p className="mt-1 text-[13px] text-ink-400">
            {!needsMode
              ? "제어권을 쥔 채로는 나갈 수 없습니다. 먼저 정지하세요."
              : isDisabled
                ? "제어권을 획득하면 화면이 함께 넘어갑니다."
                : "다른 모드가 활성화되어 있습니다. 정지한 뒤 획득하세요."}
          </p>
        </div>

        {/* 두 단계는 실제 절차다 (FR-19). 번호가 순서를 담는다 */}
        <ol className="mt-4 flex flex-col gap-2 px-6">
          <li className="flex items-baseline gap-3 text-[13px]">
            <span className={`shrink-0 rounded border px-1.5 font-mono text-[11px] ${
              isDisabled
                ? "border-st-ready/50 bg-st-ready/10 text-st-ready"
                : "border-ink-200 text-ink-400"}`}
            >
              1
            </span>
            <span className={isDisabled ? "text-ink-900" : "text-ink-600"}>
              정지해서 비활성화 상태로 만듭니다
              {isDisabled ? " — 완료" : ` (현재 ${activeLabel})`}
            </span>
          </li>
          {needsMode && (
            <li className="flex items-baseline gap-3 text-[13px]">
              <span className="shrink-0 rounded border border-ink-200 px-1.5
                               font-mono text-[11px] text-ink-400">
                2
              </span>
              <span className="text-ink-600">
                {targetLabel} 모드와 제어권을 획득하고 화면을 넘깁니다
              </span>
            </li>
          )}
        </ol>

        <AnimatePresence initial={false}>
          {(note || requestedMode) && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.18 }}
              className="overflow-hidden px-6"
            >
              <p className="mt-3 text-xs leading-relaxed text-ink-600">
                {requestedMode
                  ? `요청한 모드(${requestedMode})의 확정을 기다리는 중입니다.`
                  : note}
              </p>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="mt-5 flex gap-2 px-6">
          {/* 정지할 것이 있을 때만 그린다. 이미 비활성화 상태면 1단계가 끝난 것이다 */}
          {canStop && (
            <button
              type="button"
              onClick={onStop}
              className="flex-1 rounded-full bg-st-fault/12 px-3 py-2 text-[13px]
                         font-semibold text-st-fault transition-colors
                         hover:bg-st-fault/20"
            >
              정지(STOP)
            </button>
          )}
          {needsMode && (
            <button
              type="button"
              onClick={onAcquire}
              disabled={!canAcquire || waiting}
              className="flex-1 rounded-full bg-ink-900 px-3 py-2 text-[13px]
                         font-semibold text-white transition-opacity
                         hover:opacity-90 disabled:opacity-25"
            >
              {waiting ? "획득 중…" : `${targetLabel} 획득`}
            </button>
          )}
        </div>

        <div className="mt-4 flex items-center justify-between gap-3 bg-ink-50 px-6 py-3">
          <p className="text-xs leading-relaxed text-ink-400">
            연결 복구되거나 손이 다시 인식되어도 제어는 자동으로 재개되지 않습니다.
          </p>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-full bg-ink-100 px-3 py-1 text-xs font-medium
                       transition-colors hover:bg-ink-200"
          >
            취소
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}
