import { useEffect, useState } from "react";
import { THRESHOLD } from "../config/pending";
import { AnimatePresence, motion } from "motion/react";
import { Panel, Head, Body, Tag } from "../ui/Sheet";
import Num from "../ui/Num";

// FR-25: 모터 상태 표시 - MotorStatus.msg / MotorState.msg 대응
// 7개 모터(FR-07: 7채널 서보) 각각의 목표/실제 위치, 속도, 전류, 온도,
// 통신 상태를 구분해서 보여준다.
//
// 주의(FR-25 인수조건): "이 화면은 Raspberry Pi의 안전 판단을 대신하지 않는다."
// 과전류·과온 보호(FR-14)나 통신 timeout 대응(FR-11)은 Raspberry Pi 가 독립적으로
// 수행한다. 여기 표시는 모니터링 전용이다.
//
// 제거된 상수: WARN_CURRENT_A, WARN_TEMPERATURE_C
//   웹이 자체 임계값을 들면 화면은 정상인데 로봇은 FAULT(또는 그 반대)가 된다.
//   FR-41 이 전류·온도 제한을 YAML 소관으로 두었다. 모터별 이상은 MotorState 가
//   실제로 싣는 hardware_error 로 판정하고, 알람은 SafetyState 가 담당한다.
//
// 제거된 함수: placeholderMotor
//   데이터가 없을 때 motor_id 1..7 을 만들어 냈다. FR-24 "가짜 값으로 채우지
//   않는다" 에 어긋난다. 로봇이 보낸 것만 렌더한다.

// ── 신선도 판정 ─────────────────────────────────────────────────────────────
//
// FR-25 "stale 값은 연결 끊김과 구별해야 한다." 세 상태다.
//
//   정상        지금 값
//   stale       브릿지는 살아 있는데 MotorStatus 갱신만 멈췄다
//   연결 끊김    브릿지 자체가 죽었다. 표의 모든 값이 과거다
//
// 이전 구현은 `snapshotAt − motor_state.stamp` 뺄셈으로 stale 을 쟀다. 두 문제가
// 있었다. (1) MotorStatus 의 시각은 `stamp` 가 아니라 `header.stamp` 이고
// `{sec, nanosec}` 이라 문자열 파싱이 실패했다. (2) snapshotAt 은 Jetson,
// header.stamp 는 Raspberry Pi 가 찍어 두 장비 시계 오차가 그대로 들어왔다.
//
// 지금은 값을 해석하지 않고 "마지막으로 바뀐 시각"(브라우저 시계)만 쓴다.
// 임계값은 근거가 없어 pending.js 의 THRESHOLD 에 모아 두었다.


/**
 * 경과 시간(ms)을 주기적으로 다시 계산한다.
 *
 * 렌더 중 Date.now() 를 읽으면 순수하지 않으므로 tick 만 흘리고 경과 시간은
 * 렌더에서 계산한다. since 가 바뀌면 tick 이 곧바로 최신 값을 만든다.
 */
function useElapsed(since) {
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setTick(Date.now()), THRESHOLD.RECHECK_PERIOD_MS);
    return () => clearInterval(id);
  }, []);
  if (typeof since !== "number") return null;
  return Math.max(0, tick - since);
}

/**
 * @param fill 남는 높이를 채울 것인가. 조작 화면처럼 이 표가 열 전체를 차지할 때
 *             켜면 7행이 높이를 나눠 갖고 아래가 비지 않는다.
 */
export default function MotorStatusPanel({
  motorStatus, motorUpdatedAt, receivedAt, fill = false,
}) {
  const sinceMs = useElapsed(receivedAt);
  const staleMs = useElapsed(motorUpdatedAt);

  const motors =
    motorStatus?.motors && motorStatus.motors.length > 0
      ? motorStatus.motors
      : [];

  const busOk = motorStatus?.bus_communication_ok ?? false;
  const hasData = motors.length > 0;

  // 브릿지가 죽었는가. receivedAt 을 주지 않으면(단독 렌더·시험) 판정하지 않는다.
  // 판단 근거가 없을 때 끊겼다고 단정하지 않는다.
  const isPast = hasData && sinceMs !== null && sinceMs > THRESHOLD.NO_SNAPSHOT_MS;

  // MotorStatus 토픽만 멈췄는가. 연결 끊김이 더 강한 상태이므로 겹치면 양보한다.
  const isStale = hasData && !isPast
    && staleMs !== null && staleMs > THRESHOLD.MOTOR_STALE_MS;

  const ageSec = staleMs !== null ? Math.round(staleMs / 100) / 10 : 0;
  const sinceSec = sinceMs !== null ? Math.round(sinceMs / 100) / 10 : null;
  const dim = isStale || isPast;

  const COLS = ["ID", "액추에이터", "목표 rad", "현재 rad", "rad/s", "A", "°C", "토크", "통신"];
  const LEFT = new Set([0, 1, 7, 8]);

  return (
    <Panel className={fill ? "flex min-h-0 flex-1 flex-col" : ""}>
      <div aria-label="모터 상태">
        <Head title="모터 7채널">
          <AnimatePresence mode="wait" initial={false}>
            {isPast ? (
              // 브릿지가 죽었다. "전체 통신 정상" 을 그대로 두면 멈춘 값이 현재
              // 상태처럼 보인다. 통신 표시를 대체한다.
              <motion.span key="past" initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                           exit={{ opacity: 0 }}>
                <Tag tone="bad"
                     title={sinceSec !== null ? `마지막 수신 ${sinceSec}초 전` : undefined}>
                  연결 끊김 · 마지막 값{sinceSec !== null ? ` (${sinceSec}s 전)` : ""}
                </Tag>
              </motion.span>
            ) : (
              <motion.span key="live" initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                           exit={{ opacity: 0 }} className="flex items-center gap-2">
                {isStale && (
                  <Tag tone="warn" title={`마지막 갱신 ${ageSec}초 전`}>
                    값이 오래됨 ({ageSec}s)
                  </Tag>
                )}
                <Tag tone={busOk ? "ok" : "bad"}>
                  {busOk ? "전체 통신 정상" : "일부/전체 통신 불량"}
                </Tag>
              </motion.span>
            )}
          </AnimatePresence>
        </Head>

        <Body className={fill ? "flex min-h-0 flex-1 flex-col" : ""}>
          {!hasData ? (
            <p className="text-xs text-ink-500">모터 상태를 아직 받지 못했습니다.</p>
          ) : (
            <div className={`overflow-x-auto ${fill ? "min-h-0 flex-1" : ""}`}>
              <table className={`w-full text-xs ${fill ? "h-full" : ""}`}>
                <thead>
                  <tr className="border-b border-ink-200">
                    {COLS.map((h, i) => (
                      <th key={h}
                          className={`whitespace-nowrap px-2 py-1.5 font-mono text-[10px]
                                      font-medium tracking-[0.1em] text-ink-400
                                      ${LEFT.has(i) ? "text-left" : "text-right"}`}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {motors.map((motor, i) => {
                    const commOk = motor.communication_ok ?? false;
                    const fault =
                      typeof motor.hardware_error === "number" && motor.hardware_error !== 0;
                    const muted = dim ? "text-ink-400" : "";
                    const alert = fault ? "text-st-fault font-medium" : muted;
                    return (
                      <tr key={motor.motor_id ?? i}
                          className="border-b border-ink-100 last:border-0
                                     hover:bg-ink-200/70">
                        <td className={`px-2 py-1.5 text-left font-mono ${muted}`}>
                          {motor.motor_id ?? "-"}
                        </td>
                        <td className={`px-2 py-1.5 text-left font-mono ${muted}`}>
                          {motor.actuator_name ?? "-"}
                        </td>
                        {/* 계측값은 spring 으로 이어 준다. 툭툭 갈아 끼우면 깜빡여 보인다 */}
                        <td className={`px-2 py-1.5 text-right font-mono ${muted}`}>
                          <Num value={motor.goal_position_rad} />
                        </td>
                        <td className={`px-2 py-1.5 text-right font-mono ${muted}`}>
                          <Num value={motor.present_position_rad} />
                        </td>
                        <td className={`px-2 py-1.5 text-right font-mono ${muted}`}>
                          <Num value={motor.velocity_rad_s} />
                        </td>
                        <td className={`px-2 py-1.5 text-right font-mono ${alert}`}>
                          <Num value={motor.current_ampere} />
                        </td>
<td className={`px-2 py-1.5 text-right font-mono ${alert}`}>
                          <Num value={motor.temperature_celsius} digits={1} />
                        </td>
                        {/* interfaces.md MotorStatus 계약: communication_ok=false 면
                            torque_enabled 를 유효 상태로 보지 않는다. 값이 없거나 통신
                            실패면 "-"(FR-24 가짜 값 금지). ON/OFF 는 판정이 아니라 표시다. */}
                        <td className="px-2 py-1.5 text-left">
                          {commOk && typeof motor.torque_enabled === "boolean" ? (
                            <Tag tone={motor.torque_enabled ? "live" : "idle"}>
                              {motor.torque_enabled ? "ON" : "OFF"}
                            </Tag>
                          ) : (
                            <span className={`font-mono ${muted || "text-ink-400"}`}>-</span>
                          )}
                        </td>
                        <td className="px-2 py-1.5 text-left">
                          <Tag tone={!commOk ? "bad"
                            : isPast ? "idle" : isStale ? "warn" : "ok"}>
                            {!commOk ? "오류"
                              : isPast ? "정상(과거)"
                                : isStale ? "정상(오래됨)" : "정상"}
                          </Tag>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {typeof motorStatus?.failed_read_count === "number"
            && motorStatus.failed_read_count > 0 && (
            <p className="mt-3 text-xs text-st-fault">
              읽기 실패 누적 {motorStatus.failed_read_count}회. 모터 통신 오류가 발생했습니다.
            </p>
          )}
        </Body>
      </div>
    </Panel>
  );
}
