// ============================================================================
// 상단 바 우측 — 모드·기록·장치·연결
// ----------------------------------------------------------------------------
// 안전 상태와 제어권은 판정 띠가 맡는다. 여기는 나머지다.
// 정상일 때는 조용히 있고 이상이 있을 때만 늘어난다. 항상 다 보여 주면 정작
// 이상이 생겼을 때 눈에 안 띈다.
//
// 알약이 생기고 사라지는 것도 정보다. 갑자기 나타나면 놓치므로 스치듯 들어온다.
// ============================================================================
import { motion, AnimatePresence } from "motion/react";
import { useHandSocket } from "../context/HandSocketContext";
import {
  CONNECTION_STATE, CONTROL_MODE, RECORDING_BUSY_STATES, isDeviceDown,
} from "../config/messageProtocol";

const MODE_LABEL = {
  [CONTROL_MODE.DISABLED]: "비활성화",
  [CONTROL_MODE.MIMIC]: "모방 모드",
  [CONTROL_MODE.MANUAL]: "조작 모드",
  [CONTROL_MODE.TELEOP]: "로컬 teleop 모드",
};

const LINK_LABEL = {
  open: "연결됨", connecting: "연결 중", reconnecting: "재연결 중", closed: "연결 끊김",
};

const DEVICE_LABEL = {
  jetson: "Jetson", rpi: "Raspberry Pi", ros2: "ROS 2", camera: "카메라", motor: "모터",
};

function Pill({ children }) {
  return (
    <motion.span
      layout
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.9 }}
      transition={{ type: "spring", stiffness: 500, damping: 34 }}
      className="rounded-full bg-white/20 px-2.5 py-0.5 font-mono text-[11px]"
    >
      {children}
    </motion.span>
  );
}

export default function StatusBar() {
  const {
    connectionState, controlState, recordingState, connectionStatus,
    needsResumeConfirmation, resumeControl,
  } = useHandSocket();

  const live = connectionState === "open";
  const down = Object.entries(connectionStatus).filter(([, s]) => isDeviceDown(s));
  const unknown = Object.entries(connectionStatus)
    .filter(([, s]) => s === CONNECTION_STATE.UNKNOWN);
  const busy = RECORDING_BUSY_STATES.includes(recordingState.state);

  return (
    <div className="ml-auto flex flex-wrap items-center gap-2 text-white">
      <span className="font-mono text-lg opacity-80">
        현재 모드: {MODE_LABEL[controlState.active_mode] ?? controlState.active_mode}
      </span>

      <AnimatePresence mode="popLayout">
        {recordingState.state !== "IDLE" && (
          <Pill key="rec">
            {busy && <span className="mr-1.5 inline-block size-1.5 rounded-full bg-white" />}
            기록: {recordingState.state}
          </Pill>
        )}
        {/* FR-26: 판정 대기는 다음 기록을 막는 상태라 모방 화면 밖에서도 보여야 한다 */}
        {recordingState.result_pending && <Pill key="pend">판정 대기</Pill>}
        {down.length > 0 && (
          <Pill key="down">단절: {down.map(([k]) => DEVICE_LABEL[k] ?? k).join(", ")}</Pill>
        )}
        {down.length === 0 && unknown.length > 0 && (
          <Pill key="unk">
            상태 미확인: {unknown.map(([k]) => DEVICE_LABEL[k] ?? k).join(", ")}
          </Pill>
        )}
        {needsResumeConfirmation && (
          <motion.button
            key="resume"
            type="button"
            layout
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.9 }}
            whileTap={{ scale: 0.96 }}
            onClick={resumeControl}
            className="rounded-full bg-white px-3 py-1 text-xs font-semibold
                       text-[var(--signal)]"
          >
            제어 재개
          </motion.button>
        )}
      </AnimatePresence>

      <span className="flex items-center gap-1.5 font-mono text-lg opacity-80">
        <span className={`size-1.5 rounded-full ${live ? "bg-white" : "bg-white/40"}`}
              aria-hidden="true" />
        {LINK_LABEL[connectionState] ?? "연결 끊김"}
      </span>
    </div>
  );
}
