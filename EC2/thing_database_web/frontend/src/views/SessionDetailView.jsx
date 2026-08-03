// frontend/src/views/SessionDetailView.jsx
//
// [FR-48] 로봇 데이터 표시.
//   HandCommand 7논리축과 모터별 목표·현재 위치, motor-axis rad, 속도, 전류,
//   전압, 온도, 통신 여부를 표시한다.
//   단위·UTC·결측·통신실패를 구분해 표시하고
//   "모터축 각도는 실제 관절각이 아님"을 고지한다.
//
// [FR-49] metadata JSON, HandCommand CSV, MotorStatus CSV 정확히 세 파일 다운로드.
//
// 차트는 HandCommand 와 MotorStatus 중 하나를 선택해 본다.
// MotorStatus 는 한 수신 시각의 모터 하나가 한 행으로 평탄화되어 있어,
// 지표 하나를 골라 모터별 라인으로 피벗해야 시계열로 읽을 수 있다.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Download, FileJson, Sheet } from 'lucide-react';

import Skeleton from '../ui/Skeleton.jsx';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import {
  describeError,
  fetchAllSessionData,
  fetchSessionDetail,
  isNotFound,
} from '../services/sessions';
import {
  MISSING,
  formatBytes,
  formatCount,
  formatDuration,
  formatInt,
  formatNumber,
  formatUtc,
} from '../utils/format';

const LOADING = 'loading';
const NOT_FOUND = 'notFound';
const ERROR = 'error';
const READY = 'ready';

const HAND_COMMAND = 'hand_command';
const MOTOR_STATUS = 'motor_status';

/** HandCommand 7논리축. 명세서 CSV header 의 thumb_flex ~ little_flex 다. */
const LOGICAL_AXES = [
  { key: 'thumb_flex', label: '엄지 굴곡', color: '#ef4444' },
  { key: 'thumb_opp', label: '엄지 대립', color: '#f97316' },
  { key: 'thumb_abd', label: '엄지 외전', color: '#eab308' },
  { key: 'index_flex', label: '검지 굴곡', color: '#22c55e' },
  { key: 'middle_flex', label: '중지 굴곡', color: '#06b6d4' },
  { key: 'ring_flex', label: '약지 굴곡', color: '#3b82f6' },
  { key: 'little_flex', label: '소지 굴곡', color: '#a855f7' },
];

/** MotorStatus 에서 시계열로 볼 수 있는 지표 */
const MOTOR_METRICS = [
  { key: 'present_position_rad', label: '현재 위치', unit: 'rad' },
  { key: 'goal_position_rad', label: '목표 위치', unit: 'rad' },
  { key: 'velocity_rad_s', label: '속도', unit: 'rad/s' },
  { key: 'current_ampere', label: '전류', unit: 'A' },
  { key: 'voltage_volt', label: '전압', unit: 'V' },
  { key: 'temperature_celsius', label: '온도', unit: '°C' },
];

/** 모터별 라인 색상. 모터 수가 많아지면 순환한다. */
const MOTOR_COLORS = [
  '#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4', '#3b82f6',
  '#a855f7', '#ec4899', '#14b8a6', '#84cc16', '#f59e0b', '#8b5cf6',
];

const FILE_KINDS = [
  {
    key: 'metadata',
    label: 'metadata JSON',
    icon: FileJson,
    note: '세션 식별·판정·검증 정보',
  },
  {
    key: 'hand_command',
    label: 'HandCommand CSV',
    icon: Sheet,
    note: '7논리축 명령의 시각별 기록',
  },
  {
    key: 'motor_status',
    label: 'MotorStatus CSV',
    icon: Sheet,
    note: '모터별 위치·전류·온도 기록',
  },
];

/**
 * 차트에 쓸 색을 CSS 변수에서 읽는다.
 *
 * recharts 는 색을 props 로 받으므로 CSS 만으로는 축·격자·툴팁을 테마에 맞출 수
 * 없다. 하드코딩하면 다크 모드에서 축이 보이지 않는다. 그래서 실행 시점에
 * 계산된 값을 읽고, prefers-color-scheme 이 바뀌면 다시 읽는다.
 */
function useChartTheme() {
  const read = () => {
    if (typeof window === 'undefined') {
      return { rule: '#dedbd5', ink: '#1a1917', ink3: '#9b968d', surface: '#fff', mono: 'monospace' };
    }
    const css = getComputedStyle(document.documentElement);
    const pick = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
    return {
      rule: pick('--rule', '#dedbd5'),
      ink: pick('--ink', '#1a1917'),
      ink3: pick('--ink-3', '#9b968d'),
      surface: pick('--surface', '#ffffff'),
      mono: pick('--mono', 'monospace'),
    };
  };

  const [theme, setTheme] = useState(read);

  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const update = () => setTheme(read());
    query.addEventListener?.('change', update);
    return () => query.removeEventListener?.('change', update);
  }, []);

  return theme;
}

export default function SessionDetailView() {
  const theme = useChartTheme();
  const { sessionId } = useParams();

  const [detail, setDetail] = useState(null);
  const [handCommand, setHandCommand] = useState({ rows: [], truncated: false });
  const [motorStatus, setMotorStatus] = useState({ rows: [], truncated: false });
  const [phase, setPhase] = useState(LOADING);
  const [message, setMessage] = useState('');

  const [dataset, setDataset] = useState(HAND_COMMAND);
  const [metric, setMetric] = useState(MOTOR_METRICS[0].key);

  const load = useCallback(async () => {
    setPhase(LOADING);
    try {
      const meta = await fetchSessionDetail(sessionId);
      const [hc, ms] = await Promise.all([
        fetchAllSessionData(sessionId, HAND_COMMAND, { maxRows: 5000 }),
        fetchAllSessionData(sessionId, MOTOR_STATUS, { maxRows: 5000 }),
      ]);
      setDetail(meta);
      setHandCommand(hc);
      setMotorStatus(ms);
      setPhase(READY);
    } catch (error) {
      // 없는 세션(사용자 실수)과 서버 장애(운영 문제)는 취할 행동이 달라 구분한다.
      if (isNotFound(error)) {
        setPhase(NOT_FOUND);
        return;
      }
      setMessage(describeError(error));
      setPhase(ERROR);
    }
  }, [sessionId]);

  useEffect(() => {
    load();
  }, [load]);

  // HandCommand 차트. null 은 그대로 둔다 —
  // connectNulls={false} 가 선을 끊어 결측을 드러낸다.
  const handCommandChart = useMemo(
    () => handCommand.rows.map((row) => ({
      elapsed_ms: row.elapsed_ms,
      ...Object.fromEntries(LOGICAL_AXES.map(({ key }) => [key, row[key]])),
    })),
    [handCommand.rows],
  );

  const motorIds = useMemo(
    () => [...new Set(motorStatus.rows.map((r) => r.motor_id))]
      .filter((id) => id != null)
      .sort((a, b) => a - b),
    [motorStatus.rows],
  );

  // MotorStatus 차트. 평탄화된 행을 elapsed_ms 로 묶고 모터별 컬럼으로 피벗한다.
  const motorStatusChart = useMemo(() => {
    const byTime = new Map();
    motorStatus.rows.forEach((row) => {
      if (!byTime.has(row.elapsed_ms)) {
        byTime.set(row.elapsed_ms, { elapsed_ms: row.elapsed_ms });
      }
      byTime.get(row.elapsed_ms)[`m${row.motor_id}`] = row[metric];
    });
    return [...byTime.values()].sort((a, b) => a.elapsed_ms - b.elapsed_ms);
  }, [motorStatus.rows, metric]);

  // 모터별 최신 샘플
  const latestPerMotor = useMemo(() => {
    const byMotor = new Map();
    motorStatus.rows.forEach((row) => {
      byMotor.set(row.motor_id, row);
    });
    return [...byMotor.values()].sort((a, b) => (a.motor_id ?? 0) - (b.motor_id ?? 0));
  }, [motorStatus.rows]);

  const commFailures = useMemo(
    () => motorStatus.rows.filter((row) => row.communication_ok === false).length,
    [motorStatus.rows],
  );

  if (phase === LOADING) {
    return (
      <div className="sheet wide">
        <div className="panel"><Skeleton rows={4} label="세션을 불러오고 있습니다…" /></div>
      </div>
    );
  }

  if (phase === NOT_FOUND) {
    return (
      <div className="sheet wide">
        <div className="panel">
          <p>세션을 찾을 수 없습니다.</p>
          <small>
            Session ID <code>{sessionId}</code> 에 해당하는 공개 세션이 없습니다.
            아직 업로드되지 않았거나 ID가 잘못되었을 수 있습니다.
          </small>
        </div>
        <div className="btn-row">
          <Link to="/sessions" className="btn btn-primary">← 목록에서 찾기</Link>
        </div>
      </div>
    );
  }

  if (phase === ERROR) {
    return (
      <div className="sheet wide">
        <div className="panel">
          <p>세션을 불러오지 못했습니다.</p>
          <small>{message}</small>
        </div>
        <div className="btn-row">
          <Link to="/sessions" className="btn">← 목록으로</Link>
          <button type="button" onClick={load} className="btn btn-primary">다시 시도</button>
        </div>
      </div>
    );
  }

  const showingHandCommand = dataset === HAND_COMMAND;
  const activeMetric = MOTOR_METRICS.find((m) => m.key === metric);
  const chartData = showingHandCommand ? handCommandChart : motorStatusChart;
  const chartTruncated = showingHandCommand ? handCommand.truncated : motorStatus.truncated;
  const chartRowCount = showingHandCommand ? handCommand.rows.length : motorStatus.rows.length;

  return (
    <div className="sheet wide">
      <div className="page-head">
        <div>
          <h2>세션 상세</h2>
          <p className="mono subtle">{detail.session_id}</p>
        </div>
        <Link to="/sessions" className="btn">← 목록으로</Link>
      </div>

      {/* ── 메타 ── */}
      <section className="panel">
        <h3>세션 정보</h3>
        <dl className="facts">
          <div><dt>로봇</dt><dd><span className="chip">{detail.robot_id}</span></dd></div>
          <div>
            <dt>판정</dt>
            <dd>
              <span className={detail.result === 'SUCCESS' ? 'chip chip-ok' : 'chip chip-no'}>
                {detail.result}
              </span>
            </dd>
          </div>
          <div><dt>시작</dt><dd>{formatUtc(detail.started_at)}</dd></div>
          <div><dt>종료</dt><dd>{formatUtc(detail.ended_at)}</dd></div>
          <div><dt>업로드</dt><dd>{formatUtc(detail.uploaded_at)}</dd></div>
          <div><dt>기록 길이</dt><dd>{formatDuration(detail.duration_ms)}</dd></div>
          <div>
            <dt>시각 동기</dt>
            <dd>
              {detail.time_sync
                ? <span className="chip chip-ok">동기됨</span>
                : <span className="chip chip-wa">비동기</span>}
            </dd>
          </div>
          <div><dt>Schema / Data 버전</dt><dd>{detail.schema_version} / {detail.data_version}</dd></div>
          <div className="span-2">
            <dt>Interface commit</dt>
            <dd className="mono small">{detail.interface_commit}</dd>
          </div>
          <div className="span-2">
            <dt>Content digest</dt>
            <dd className="mono small break">{detail.content_digest}</dd>
          </div>
        </dl>
      </section>

      {/* ── 다운로드 ── */}
      <section className="panel">
        <h3>파일 다운로드</h3>
        <p className="subtle small">세션마다 아래 세 파일만 공개됩니다.</p>
        <table className="grid">
          <thead>
            <tr>
              <th>파일</th>
              <th className="num">크기</th>
              <th className="num">행 수</th>
              <th className="row-action" />
            </tr>
          </thead>
          <tbody>
            {FILE_KINDS.map(({ key, label, icon: Icon, note }) => (
              <tr key={key}>
                <td>
                  <span className="file-row">
                    <span className="file-icon" aria-hidden="true">
                      <Icon size={16} strokeWidth={1.8} />
                    </span>
                    <span className="file-meta">
                      <span className="file-name">{label}</span>
                      <span className="file-note">{note}</span>
                    </span>
                  </span>
                </td>
                <td className="num">{formatBytes(detail.file_sizes?.[key])}</td>
                <td className="num">
                  {key === 'metadata' ? MISSING : formatCount(detail.row_counts?.[key])}
                </td>
                <td className="row-action">
                  <a href={detail.downloads?.[key]} className="btn btn-sm">
                    <Download size={13} strokeWidth={2.2} aria-hidden="true" />
                    다운로드
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* ── 시계열 차트 ── */}
      <section className="panel">
        <div className="panel-head">
          <h3>시계열</h3>
          <div className="chart-controls">
            <div className="segment" role="group" aria-label="데이터 종류 선택">
              <button
                type="button"
                onClick={() => setDataset(HAND_COMMAND)}
                className={showingHandCommand ? 'active' : ''}
                aria-pressed={showingHandCommand}
              >
                HandCommand
              </button>
              <button
                type="button"
                onClick={() => setDataset(MOTOR_STATUS)}
                className={!showingHandCommand ? 'active' : ''}
                aria-pressed={!showingHandCommand}
              >
                MotorStatus
              </button>
            </div>

            {!showingHandCommand && (
              <div className="filter-group">
                <label htmlFor="metric-select">지표</label>
                <select
                  id="metric-select"
                  value={metric}
                  onChange={(event) => setMetric(event.target.value)}
                >
                  {MOTOR_METRICS.map(({ key, label, unit }) => (
                    <option key={key} value={key}>{`${label} (${unit})`}</option>
                  ))}
                </select>
              </div>
            )}
          </div>
        </div>

        <p className="subtle small">
          {showingHandCommand
            ? '7논리축 최종 명령값 (정규화, 무차원).'
            : `모터별 ${activeMetric.label} (${activeMetric.unit}).`}
          {' '}x축은 세션 시작 기준 경과 시간(ms)입니다. 결측 구간은 선이 끊어져 표시됩니다.
        </p>

        {chartTruncated && (
          <p className="note note-warn">
            행이 많아 앞부분 {formatCount(chartRowCount)}행만 표시합니다.
            전체는 CSV를 다운로드하세요.
          </p>
        )}

        {chartData.length === 0 ? (
          <p className="subtle">표시할 데이터가 없습니다.</p>
        ) : (
          <div className="chart-frame" data-testid="timeseries-chart">
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 24, left: 0 }}>
                {/* 축·격자 색을 하드코딩하면 다크 모드에서 보이지 않는다.
                    실행 시점에 CSS 변수를 읽어 테마를 따른다. */}
                <CartesianGrid strokeDasharray="2 4" stroke={theme.rule} vertical={false} />
                <XAxis
                  dataKey="elapsed_ms"
                  tick={{ fontSize: 11, fill: theme.ink3, fontFamily: theme.mono }}
                  stroke={theme.rule}
                  label={{
                    value: 'elapsed_ms',
                    position: 'insideBottom',
                    offset: -14,
                    fontSize: 11,
                    fill: theme.ink3,
                  }}
                />
                <YAxis
                  tick={{ fontSize: 11, fill: theme.ink3, fontFamily: theme.mono }}
                  stroke={theme.rule}
                  domain={['auto', 'auto']}
                  width={46}
                />
                <Tooltip
                  contentStyle={{
                    background: theme.surface,
                    border: `1px solid ${theme.rule}`,
                    borderRadius: 6,
                    fontSize: 12,
                    fontFamily: theme.mono,
                    boxShadow: '0 4px 14px -6px rgba(0,0,0,0.25)',
                  }}
                  labelStyle={{ color: theme.ink3, fontSize: 11 }}
                  itemStyle={{ color: theme.ink }}
                  formatter={(value, name) => [
                    value == null ? MISSING : formatNumber(value),
                    showingHandCommand
                      ? (LOGICAL_AXES.find((a) => a.key === name)?.label ?? name)
                      : `모터 ${String(name).replace('m', '')}`,
                  ]}
                  labelFormatter={(value) => `elapsed ${formatCount(value)} ms`}
                />
                <Legend
                  verticalAlign="top"
                  height={32}
                  formatter={(name) => (showingHandCommand
                    ? (LOGICAL_AXES.find((a) => a.key === name)?.label ?? name)
                    : `모터 ${String(name).replace('m', '')}`)}
                />
                {showingHandCommand
                  ? LOGICAL_AXES.map(({ key, color }) => (
                    <Line
                      key={key}
                      type="monotone"
                      dataKey={key}
                      stroke={color}
                      strokeWidth={1.8}
                      dot={false}
                      connectNulls={false}
                      isAnimationActive={false}
                    />
                  ))
                  : motorIds.map((id, index) => (
                    <Line
                      key={id}
                      type="monotone"
                      dataKey={`m${id}`}
                      stroke={MOTOR_COLORS[index % MOTOR_COLORS.length]}
                      strokeWidth={1.8}
                      dot={false}
                      connectNulls={false}
                      isAnimationActive={false}
                    />
                  ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {!showingHandCommand && (
          <p className="note note-info">
            ⚠️ <strong>rad 값은 모터 축 기준이며 실제 관절각이 아닙니다.</strong>
            {' '}텐던 구동 방식이라 모터 회전과 관절 굴곡이 1:1로 대응하지 않습니다.
          </p>
        )}
      </section>

      {/* ── 모터 상태 표 ── */}
      <section className="panel">
        <h3>모터 상태 (모터별 최신 샘플)</h3>

        <p className="note note-info">
          ⚠️ 표의 <strong>모터축 각도(rad)는 모터 축 기준값이며 실제 관절각이 아닙니다.</strong>
          {' '}텐던 구동 방식이라 모터 회전과 관절 굴곡이 1:1로 대응하지 않습니다.
        </p>

        {motorStatus.truncated && (
          <p className="note note-warn">
            MotorStatus 행이 많아 앞부분 {formatCount(motorStatus.rows.length)}행만 조회했습니다.
            아래 표는 <strong>조회 구간 안에서의</strong> 모터별 마지막 샘플이며 세션 종료
            시점이 아닙니다. 전체는 MotorStatus CSV를 다운로드하세요.
          </p>
        )}

        <p className="subtle small">
          MotorStatus는 한 수신 시각의 모터 하나가 한 행입니다.
          {motorStatus.truncated
            ? ` 조회한 ${formatCount(motorStatus.rows.length)}행`
            : ` 전체 ${formatCount(motorStatus.rows.length)}행`}
          {' '}중 모터별 마지막 샘플을 표시합니다.
          결측값은 <code>{MISSING}</code>이며 0과 구분됩니다.
          {commFailures > 0 && (
            <> 통신 실패 행 <strong>{formatCount(commFailures)}건</strong>이 있습니다.</>
          )}
        </p>

        {latestPerMotor.length === 0 ? (
          <p className="subtle">표시할 데이터가 없습니다.</p>
        ) : (
          <div className="table-scroll">
            <table className="data-table flush compact">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>액추에이터</th>
                  <th className="num">목표 raw</th>
                  <th className="num">현재 raw</th>
                  <th className="num">목표 (rad)</th>
                  <th className="num">현재 (rad)</th>
                  <th className="num">속도 (rad/s)</th>
                  <th className="num">전류 (A)</th>
                  <th className="num">전압 (V)</th>
                  <th className="num">온도 (°C)</th>
                  <th>통신</th>
                  <th className="num">실패</th>
                </tr>
              </thead>
              <tbody>
                {latestPerMotor.map((row) => {
                  const failed = row.communication_ok === false;
                  return (
                    <tr key={row.motor_id} className={failed ? 'row-fail' : undefined}>
                      <td className="mono">{formatInt(row.motor_id)}</td>
                      <td>{row.actuator_name || MISSING}</td>
                      <td className="num mono">{formatInt(row.goal_position_raw)}</td>
                      <td className="num mono">{formatInt(row.present_position_raw)}</td>
                      <td className="num mono">{formatNumber(row.goal_position_rad)}</td>
                      <td className="num mono">{formatNumber(row.present_position_rad)}</td>
                      <td className="num mono">{formatNumber(row.velocity_rad_s)}</td>
                      <td className="num mono">{formatNumber(row.current_ampere)}</td>
                      <td className="num mono">{formatNumber(row.voltage_volt, 2)}</td>
                      <td className="num mono">{formatInt(row.temperature_celsius)}</td>
                      <td>
                        {row.communication_ok === true && <span className="chip chip-ok">정상</span>}
                        {row.communication_ok === false && (
                          <span className="pill pill-fail" title={`result ${row.communication_result}`}>
                            실패
                          </span>
                        )}
                        {row.communication_ok == null && <span className="chip chip-wa">불명</span>}
                      </td>
                      <td className="num mono">{formatInt(row.failed_read_count)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <p className="subtle small">
          버스 통신 상태와 하드웨어 오류 코드 등 전체 컬럼은 MotorStatus CSV에 포함되어 있습니다.
        </p>
      </section>
    </div>
  );
}
