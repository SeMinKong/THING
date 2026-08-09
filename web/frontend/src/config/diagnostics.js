// ============================================================================
// 통합 시험용 진단
// ----------------------------------------------------------------------------
// 목적: 화면이 이상할 때 "무엇이 / 왜 / 누가 고쳐야 하는지" 를 콘솔만 보고
//       알 수 있게 한다. 통합 시험 첫날의 삽질을 줄이는 것이 전부다.
//
// ── 설계 원칙 ───────────────────────────────────────────────────────────────
// 1. 침묵하지 않는다. 조용히 버려지는 메시지·필드가 있으면 반드시 알린다.
//    이전 구현은 인식 못 한 WebSocket 메시지를 return 으로 버렸고, 그 결과
//    화면이 "연결됨" 인데 아무것도 안 움직이는 상태가 됐다.
// 2. 담당을 밝힌다. 브릿지가 고칠 것인지, 웹인지, 설정인지 매번 적는다.
//    이게 없으면 콘솔에 오류가 보여도 누구에게 말할지 몰라 방치된다.
// 3. 도배하지 않는다. snapshot 은 초당 5~20회 온다. 같은 문제를 매 프레임
//    출력하면 콘솔이 못 쓰게 되고 진짜 문제가 묻힌다. code 별로 1회만 낸다.
// 4. 판단하지 않는다. 진단은 표시만 하고 제어 로직에 관여하지 않는다.
//    안전 판정 주체는 Raspberry Pi 다 (NFR-16).
//
// 콘솔에서 `__diag()` 로 지금까지 잡힌 문제 전체를 볼 수 있다.
// ============================================================================

/** 누가 고쳐야 하는가. 메시지 앞머리에 그대로 붙는다. */
export const OWNER = {
  BRIDGE: "브릿지",
  WEB: "웹",
  CONFIG: "설정",
  SPEC: "스펙",
  ROBOT: "로봇",
};

const LEVEL = { ERROR: "error", WARN: "warn", INFO: "info" };

/** code -> { count, first, last, ...payload } */
const seen = new Map();

/** 같은 code 를 이 시간 안에 다시 보면 출력하지 않는다. */
const REPEAT_SUPPRESS_MS = 30_000;

function stamp() {
  return new Date().toISOString().slice(11, 23);
}

/**
 * 문제 하나를 보고한다.
 *
 * @param code   중복 억제 키. 같은 원인이면 같은 code 를 쓴다.
 * @param owner  OWNER 중 하나
 * @param what   무슨 일이 일어났는가 (한 줄)
 * @param why    왜 문제인가 / 화면에 어떻게 나타나는가
 * @param fix    무엇을 하면 되는가
 * @param detail 실제 값. 객체를 그대로 넘겨도 된다
 * @param ref    근거 문서 위치
 */
function report(level, { code, owner, what, why, fix, detail, ref }) {
  const now = Date.now();
  const prev = seen.get(code);
  if (prev) {
    prev.count += 1;
    prev.last = now;
    if (now - prev.lastPrinted < REPEAT_SUPPRESS_MS) return;
    prev.lastPrinted = now;
  } else {
    seen.set(code, {
      code, owner, what, why, fix, ref, level,
      count: 1, first: now, last: now, lastPrinted: now, detail,
    });
  }

  const entry = seen.get(code);
  const repeat = entry.count > 1 ? ` (${entry.count}회째)` : "";
  const head = `[진단:${owner}] ${what}${repeat}`;

  const group = console.groupCollapsed ?? console.log;
  group.call(console, `%c${head}`, "font-weight:bold");
  console.log(`시각   ${stamp()}`);
  if (why) console.log(`증상   ${why}`);
  if (fix) console.log(`조치   ${fix}`);
  if (ref) console.log(`근거   ${ref}`);
  if (detail !== undefined) console.log("실제값", detail);
  if (console.groupEnd) console.groupEnd();
}

export const diag = {
  error: (info) => report(LEVEL.ERROR, info),
  warn: (info) => report(LEVEL.WARN, info),
  info: (info) => report(LEVEL.INFO, info),
  /** 같은 원인이 해소되면 다시 보고할 수 있게 잠금을 푼다. */
  clear: (code) => seen.delete(code),
  /** 지금까지 잡힌 문제 전체. */
  all: () => [...seen.values()].map((e) => ({
    code: e.code, owner: e.owner, level: e.level, count: e.count,
    what: e.what, why: e.why, fix: e.fix, ref: e.ref, detail: e.detail,
    처음: new Date(e.first).toISOString().slice(11, 23),
    마지막: new Date(e.last).toISOString().slice(11, 23),
  })),
};

// ---------------------------------------------------------------------------
// snapshot 발행 주기 자동 측정
// ---------------------------------------------------------------------------
// pending.js 의 BRIDGE_SNAPSHOT_PERIOD_MS 는 회신을 못 받아 가정한 값이다.
// 실제 도착 간격을 재서 가정과 크게 다르면 알려 준다. 회신을 기다리지 않고도
// 통합 시험 첫 10초 안에 정답을 알 수 있다.

const RATE_SAMPLES = 20;
const rate = { times: [], reported: false };

export function observeSnapshotRate(assumedPeriodMs) {
  const now = Date.now();
  rate.times.push(now);
  if (rate.times.length > RATE_SAMPLES) rate.times.shift();
  if (rate.times.length < RATE_SAMPLES || rate.reported) return;

  const span = rate.times[rate.times.length - 1] - rate.times[0];
  const measured = Math.round(span / (rate.times.length - 1));
  const ratio = measured / assumedPeriodMs;
  if (ratio > 1.5 || ratio < 0.5) {
    rate.reported = true;
    diag.warn({
      code: "SNAPSHOT_RATE_MISMATCH",
      owner: OWNER.WEB,
      what: `snapshot 실측 주기 ${measured}ms 가 가정값 ${assumedPeriodMs}ms 와 다릅니다`,
      why: "이 값에서 장치 up/down·모터 stale·영상 정지·연결 끊김 임계값 4개가 "
        + "파생됩니다. 실제가 더 느리면 정상인데도 화면이 상시 '연결 끊김' 으로 보입니다.",
      fix: `src/config/pending.js 의 BRIDGE_SNAPSHOT_PERIOD_MS 를 ${measured} 으로 고치세요. `
        + "임계값 4개는 자동으로 맞춰집니다.",
      ref: "web/docs/pending-decisions.md A-1",
      detail: { 실측: `${measured}ms`, 가정: `${assumedPeriodMs}ms`, 표본: rate.times.length },
    });
  }
}

// ---------------------------------------------------------------------------
// 기동 시 1회 — 무엇을 가정하고 도는지 먼저 밝힌다
// ---------------------------------------------------------------------------

let announced = false;

export function announceStartup({ wsUrl, mjpegUrl, pending, thresholds }) {
  if (announced) return;
  announced = true;

  const group = console.groupCollapsed ?? console.log;
  group.call(console, "%c[진단] 내부 제어 웹 시작 — 현재 가정값",
    "font-weight:bold;color:#0a7");
  console.table({
    "WebSocket": { 값: wsUrl || "(미설정)" },
    "MJPEG": { 값: mjpegUrl || "(미설정)" },
    "snapshot 주기(가정)": { 값: `${pending.BRIDGE_SNAPSHOT_PERIOD_MS}ms` },
    "장치 down 임계": { 값: `${thresholds.SECTION_STALE_MS}ms` },
    "모터 stale 임계": { 값: `${thresholds.MOTOR_STALE_MS}ms` },
    "영상 정지 임계": { 값: `${thresholds.CAMERA_STATE_STALE_MS}ms` },
    "snapshot 끊김 임계": { 값: `${thresholds.NO_SNAPSHOT_MS}ms` },
    "ack 대기 상한": { 값: `${pending.ACK_TIMEOUT_MS}ms` },
  });
  console.log("미확정 값 전체는 __pending(), 진단 이력은 __diag() 로 확인하세요.");
  if (console.groupEnd) console.groupEnd();

  if (!wsUrl) {
    diag.error({
      code: "WS_URL_MISSING",
      owner: OWNER.CONFIG,
      what: "VITE_WS_URL 이 비어 있습니다",
      why: "ws://<현재 호스트>/ws/robot-state 로 폴백합니다. 이 주소를 받아 주는 것은 "
        + "`npm run dev` 의 프록시뿐이라, build 산출물이나 preview 에서는 연결되지 않습니다.",
      fix: "env.txt 를 .env.local 로 복사하고 Jetson 주소를 적으세요. "
        + "예) ws://192.168.0.10:8000/ws/robot-state",
      ref: "FR-28 / web/frontend/.env",
    });
  }
  if (!mjpegUrl) {
    diag.error({
      code: "MJPEG_URL_MISSING",
      owner: OWNER.CONFIG,
      what: "VITE_MJPEG_STREAM_URL 이 비어 있습니다",
      why: "영상 영역이 비어 보입니다. 로봇이나 브릿지 문제가 아닙니다.",
      fix: ".env.local 에 mjpeg_streamer 의 HTTP endpoint 를 적으세요. "
        + "예) http://192.168.0.10:8080/stream/overlay",
      ref: "FR-20 / FR-28",
    });
  }
}

if (typeof window !== "undefined") {
  window.__diag = () => diag.all();
}
