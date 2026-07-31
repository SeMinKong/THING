import { useEffect, useState } from "react";

// FR-25: 모터 상태 표시 - MotorStatus.msg / MotorState.msg 대응
// 7개 모터(FR-07: 7채널 서보) 각각의 목표/실제 위치, 속도, 전류, 온도,
// 통신 상태를 구분해서 보여준다.
//
// 주의(FR-25 인수조건): "웹 화면의 상태 표시가 실제 모터 안전 제어를
// 대신해서는 안 된다" - 이 패널은 어디까지나 모니터링용이며, 과전류/과온
// 보호(FR-14)나 통신 timeout 대응(FR-11)은 Raspberry Pi 쪽에서 독립적으로
// 수행된다. 아래 경고 임계값은 화면 표시용 참고치이며 실제 안전 임계값이
// 기구/모터 스펙에 맞춰 확정되면 함께 갱신해야 한다.

// 제거된 상수: WARN_CURRENT_A, WARN_TEMPERATURE_C
//
// 웹이 자체 임계값을 들고 있으면 화면은 정상인데 로봇은 FAULT(또는 그 반대)가
// 된다. FR-41 은 전류·온도 제한을 YAML 소관으로 두었고, FR-25 는 "이 화면은
// Raspberry Pi 의 안전 판단을 대신하지 않는다" 고 한다.
// 모터별 이상 표시는 MotorState.msg 가 실제로 싣는 hardware_error 로 판정하고,
// 알람 자체는 SafetyState.over_current·over_temperature 가 담당한다.

// 제거된 함수: placeholderMotor
//
// 데이터가 없을 때 motor_id 1..7 과 actuator_name "axis_1".."axis_7" 을 만들어
// 냈다. 실제 YAML 의 motor ID 매핑과 다를 것이 거의 확실하고, FR-24 의
// "가짜 값으로 채우지 않는다" 에 어긋난다. 이제 로봇이 보낸 것만 렌더한다.

//: FR-25 "stale 값은 연결 끊김과 구별해야 한다."
//
// 이 값을 넘게 갱신이 없으면 stale 로 본다. MotorStatus 는 FR-36 의 프로젝트
// 기본이 Reliable depth 5 이고 NFR-13 이 5Hz 를 목표로 하므로, 몇 주기를 놓친
// 경우를 잡도록 여유를 두었다. 실물 조정 결과는 docs/interfaces.md 에 반영한다.
const MOTOR_STALE_MS = 1000;

//: 이 시간 넘게 snapshot 자체가 오지 않으면 브릿지가 죽은 것으로 본다.
//
// FR-25 는 "stale 값은 연결 끊김과 구별해야 한다" 고 요구한다. 세 상태다.
//
//   정상        지금 값
//   stale       브릿지는 살아 있는데 MotorStatus 갱신만 멈췄다
//               → 서버가 찍은 두 시각(snapshotAt − motor_state.stamp) 차이로 잰다.
//                 브라우저 시계 오차의 영향을 받지 않는다.
//   연결 끊김    브릿지 자체가 죽었다. 표의 모든 값이 과거다
//               → 브라우저 기준 "마지막 수신 경과" 로 잰다.
//
// 처음에는 서버 시각 차이만 봤다. 그런데 서버가 죽으면 snapshotAt 과
// motor_state.stamp 가 **함께** 멈춰서 차이가 0 으로 고정된다. 그래서 mock 이나
// bridge_simulator 를 종료해도 모터 상태가 "전체 통신 정상" 인 채로 남았다.
// 새로고침하면 사라지지만 새로고침 없이도 드러나야 한다.
//
// 값을 지우지 않고 남기는 이유: 마지막 상태가 진단에 쓰인다. 다만 "과거" 임을
// 분명히 표시한다. 표시 없이 남기면 FR-24 "가짜 값으로 채우지 않는다" 위반이다.
const NO_SNAPSHOT_MS = 2000;

/** 두 RFC 3339 시각의 차이(ms). 하나라도 없으면 null. */
function ageMs(nowText, stampText) {
  if (typeof nowText !== "string" || typeof stampText !== "string") return null;
  const now = Date.parse(nowText);
  const stamp = Date.parse(stampText);
  if (Number.isNaN(now) || Number.isNaN(stamp)) return null;
  return now - stamp;
}

function fmt(value, digits = 2) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "-";
}

export default function MotorStatusPanel({ motorStatus, snapshotAt, receivedAt }) {
  // 시간이 흐르면 "마지막 수신 이후 경과" 가 변한다. 렌더 중 Date.now() 를 읽으면
  // 순수하지 않으므로 0.5초마다 계산한 결과만 state 에 넣는다.
  const [sinceMs, setSinceMs] = useState(
    () => (typeof receivedAt === "number" ? Date.now() - receivedAt : null),
  );
  useEffect(() => {
    if (typeof receivedAt !== "number") return undefined;
    const id = setInterval(() => {
      const next = Date.now() - receivedAt;
      // 값이 그대로면 setState 하지 않는다.
      setSinceMs((prev) => (prev === next ? prev : next));
    }, 500);
    return () => clearInterval(id);
  }, [receivedAt]);

  const motors =
    motorStatus?.motors && motorStatus.motors.length > 0
      ? motorStatus.motors
      : [];

  const busOk = motorStatus?.bus_communication_ok ?? false;
  const hasData = motors.length > 0;

  // 브릿지가 죽었는가. receivedAt 을 주지 않으면(단독 렌더·시험) 판정하지 않는다.
  // 판단 근거가 없을 때 끊겼다고 단정하지 않는다.
  const isPast = hasData && sinceMs !== null && sinceMs > NO_SNAPSHOT_MS;

  // MotorStatus 토픽만 멈췄는가. 연결 끊김이 더 강한 상태이므로 겹치면 양보한다.
  const age = ageMs(snapshotAt, motorStatus?.stamp);
  const isStale = hasData && !isPast && age !== null && age > MOTOR_STALE_MS;

  const ageSec = age !== null ? Math.round(age / 100) / 10 : 0;
  const sinceSec = sinceMs !== null ? Math.round(sinceMs / 100) / 10 : null;
  const dim = isStale || isPast;

  return (
    <div className="bg-white border rounded-3 p-3 shadow-sm">
      <div className="d-flex justify-content-between align-items-center mb-2">
        <h6 className="fw-bold m-0">모터 상태 (7채널)</h6>
        <div className="d-flex gap-2">
          {isPast ? (
            // 브릿지가 죽었다. "전체 통신 정상" 을 그대로 두면 멈춘 값이 현재
            // 상태처럼 보인다. 통신 배지를 대체한다.
            <span className="badge bg-danger"
                  title={sinceSec !== null ? `마지막 수신 ${sinceSec}초 전` : undefined}>
              연결 끊김 · 마지막 값{sinceSec !== null ? ` (${sinceSec}s 전)` : ""}
            </span>
          ) : (
            <>
              {isStale && (
                <span className="badge bg-warning text-dark"
                      title={`마지막 갱신 ${ageSec}초 전`}>
                  값이 오래됨 ({ageSec}s)
                </span>
              )}
              <span className={`badge ${
                !hasData ? "bg-secondary" : busOk ? "bg-success" : "bg-danger"}`}>
                {hasData ? (busOk ? "전체 통신 정상" : "일부/전체 통신 불량") : "데이터 없음"}
              </span>
            </>
          )}
        </div>
      </div>

      <div className="table-responsive">
        <table className="table table-sm table-borderless align-middle text-center mb-0">
          <thead className="table-light small text-uppercase text-muted">
            <tr>
              <th>축</th>
              <th>목표(rad)</th>
              <th>현재(rad)</th>
              <th>속도(rad/s)</th>
              <th>전류(A)</th>
              <th>온도(°C)</th>
              <th>통신</th>
            </tr>
          </thead>
          <tbody className={`font-monospace small${dim ? " opacity-50" : ""}`}>
            {motors.length === 0 && (
              <tr>
                <td colSpan={7} className="text-center text-muted py-3">
                  모터 상태를 아직 수신하지 못했습니다.
                </td>
              </tr>
            )}
            {/* stale 이면 행 전체를 흐리게 해 "지금 값" 이 아님을 드러낸다. */}
            {motors.map((motor, idx) => {
              // FR-09/FR-25 인수조건: 값을 못 받았으면(마지막 값이 없으면) "-"로,
              // 연결이 끊긴 건 통신 배지로 구분하지 수치를 임의로 채우지 않는다.
              const commOk = motor.communication_ok ?? false;
              // DYNAMIXEL 이 올린 하드웨어 오류 비트. 0 이 정상이다.
              const hardwareFault =
                typeof motor.hardware_error === "number" && motor.hardware_error !== 0;
              const overCurrent = hardwareFault;
              const overTemp = hardwareFault;

              return (
                <tr key={motor.motor_id ?? idx} className={!commOk ? "text-muted" : ""}>
                  <td className="fw-bold">
                    {motor.actuator_name ?? `motor_${motor.motor_id ?? idx + 1}`}
                    <span className="text-muted"> (#{motor.motor_id ?? idx + 1})</span>
                  </td>
                  <td>{fmt(motor.goal_position_rad)}</td>
                  <td>{fmt(motor.present_position_rad)}</td>
                  <td>{fmt(motor.velocity_rad_s)}</td>
                  <td className={overCurrent ? "text-danger fw-bold" : ""}>
                    {fmt(motor.current_ampere)}
                    {overCurrent && " ⚠"}
                  </td>
                  <td className={overTemp ? "text-danger fw-bold" : ""}>
                    {fmt(motor.temperature_celsius, 1)}
                    {overTemp && " ⚠"}
                  </td>
                  <td>
                    {/* 끊긴 뒤의 값은 "정상" 이 아니라 마지막 관측값이다 */}
                    <span className={`badge ${
                      isPast ? "bg-secondary" : commOk ? "bg-success" : "bg-danger"}`}>
                      {isPast
                        ? (commOk ? "정상(과거)" : "끊김(과거)")
                        : (commOk ? "정상" : "끊김")}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {motorStatus?.failed_read_count > 0 && (
        <p className="small text-muted mb-0 mt-2">
          누적 읽기 실패 횟수: {motorStatus.failed_read_count}
        </p>
      )}
    </div>
  );
}
