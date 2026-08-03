// frontend/src/views/SessionListView.jsx
//
// [FR-47] 공개 세션 목록.
//   정렬은 서버가 started_at DESC, session_id DESC 로 보장한다.
//   exact Session ID 검색은 Must, result 필터는 Should.
//   기본 20건이며 next_cursor 로 다음 페이지를 받는다.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'motion/react';
import {
  ChevronRight,
  CircleCheck,
  CircleSlash,
  CircleX,
  Inbox,
  RotateCw,
  Search,
  TriangleAlert,
  X,
} from 'lucide-react';

import Counter from '../ui/Counter.jsx';
import EmptyState from '../ui/EmptyState.jsx';
import Skeleton from '../ui/Skeleton.jsx';
import { grow, item, list, rise } from '../ui/motion';
import { describeError, fetchSessions } from '../services/sessions';
import { formatBytes, formatCount, formatDuration, formatUtc } from '../utils/format';

const LOADING = 'loading';
const ERROR = 'error';
const READY = 'ready';

/** 판정 칩. 색만으로 구분하지 않고 아이콘과 글자를 함께 둔다(색각 이상 대응) */
function ResultChip({ result }) {
  if (result === 'SUCCESS') {
    return (
      <span className="chip chip-ok">
        <CircleCheck size={12} strokeWidth={2.2} aria-hidden="true" />
        SUCCESS
      </span>
    );
  }
  if (result === 'FAILURE') {
    return (
      <span className="chip chip-no">
        <CircleX size={12} strokeWidth={2.2} aria-hidden="true" />
        FAILURE
      </span>
    );
  }
  return (
    <span className="chip">
      <CircleSlash size={12} strokeWidth={2.2} aria-hidden="true" />
      {result || 'UNSET'}
    </span>
  );
}

export default function SessionListView() {
  const [items, setItems] = useState([]);
  const [phase, setPhase] = useState(LOADING);
  const [message, setMessage] = useState('');
  const [cursor, setCursor] = useState(null);
  const [nextCursor, setNextCursor] = useState(null);

  // 검색 입력과 실제 적용된 조건을 분리한다. 타이핑마다 요청하지 않는다.
  const [searchInput, setSearchInput] = useState('');
  const [applied, setApplied] = useState({ sessionId: '', result: '' });

  const load = useCallback(async () => {
    setPhase(LOADING);
    try {
      const body = await fetchSessions({
        sessionId: applied.sessionId || undefined,
        result: applied.result || undefined,
        cursor: cursor || undefined,
      });
      setItems(body.items || []);
      setNextCursor(body.next_cursor || null);
      setPhase(READY);
    } catch (error) {
      setMessage(describeError(error));
      setItems([]);
      setPhase(ERROR);
    }
  }, [applied, cursor]);

  useEffect(() => {
    load();
  }, [load]);

  const submitSearch = (event) => {
    event.preventDefault();
    setCursor(null);
    setApplied((prev) => ({ ...prev, sessionId: searchInput.trim() }));
  };

  const changeResult = (value) => {
    setCursor(null);
    setApplied((prev) => ({ ...prev, result: value }));
  };

  const resetFilters = () => {
    setSearchInput('');
    setCursor(null);
    setApplied({ sessionId: '', result: '' });
  };

  const hasFilter = applied.sessionId || applied.result;

  // 행마다 총 용량을 구하고, 같은 페이지 안 최대값을 기준으로 비율을 낸다.
  //
  // 왜: 숫자만 있으면 421 B 와 2.0 KB 의 차이가 눈에 들어오지 않는다. 막대를
  // 곁들이면 훑기만 해도 어느 세션이 긴 기록인지 보인다. 서버가 실제로 준 값만
  // 쓴다. 없는 데이터를 그려 넣지 않는다.
  const rows = useMemo(() => {
    const enriched = items.map((entry) => ({
      ...entry,
      totalBytes: Object.values(entry.file_sizes || {}).reduce(
        (sum, value) => sum + (value || 0),
        0,
      ),
    }));
    const maxBytes = Math.max(1, ...enriched.map((entry) => entry.totalBytes));
    return enriched.map((entry) => ({ ...entry, sizeRatio: entry.totalBytes / maxBytes }));
  }, [items]);

  return (
    <main className="sheet wide">
      <motion.div className="page-head" {...rise()}>
        <div>
          <p className="eyebrow">Sessions</p>
          <h2>세션 목록</h2>
          <p>
            판정이 완료되어 공개된 세션입니다. 최신 순으로 정렬되며 모든 시각은 UTC입니다.
          </p>
        </div>
        <button type="button" onClick={load} className="btn" disabled={phase === LOADING}>
          <RotateCw size={15} strokeWidth={2} aria-hidden="true" />
          {phase === LOADING ? '불러오는 중…' : '새로고침'}
        </button>
      </motion.div>

      <motion.div className="panel" {...rise(0.06)}>
        <div className="toolbar">
          <form onSubmit={submitSearch} className="field">
            <label htmlFor="session-search">Session ID</label>
            <div className="field-row">
              <input
                id="session-search"
                type="text"
                inputMode="numeric"
                placeholder="123456789012345678"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
              />
              <button type="submit" className="btn">
                <Search size={15} strokeWidth={2} aria-hidden="true" />
                검색
              </button>
            </div>
          </form>

          <div className="field">
            <label htmlFor="result-filter">판정</label>
            <select
              id="result-filter"
              value={applied.result}
              onChange={(event) => changeResult(event.target.value)}
            >
              <option value="">전체</option>
              <option value="SUCCESS">SUCCESS</option>
              <option value="FAILURE">FAILURE</option>
            </select>
          </div>

          {hasFilter && (
            <button type="button" onClick={resetFilters} className="btn btn-quiet">
              <X size={15} strokeWidth={2} aria-hidden="true" />
              조건 해제
            </button>
          )}
        </div>

        {phase === LOADING && <Skeleton rows={5} label="목록을 불러오고 있습니다…" />}

        {phase === ERROR && (
          <EmptyState
            icon={TriangleAlert}
            tone="error"
            title="목록을 불러오지 못했습니다."
            description={message}
          >
            <div className="btn-row" style={{ marginTop: 14 }}>
              <button type="button" onClick={load} className="btn">다시 시도</button>
            </div>
          </EmptyState>
        )}

        {phase === READY && rows.length === 0 && (
          hasFilter ? (
            <EmptyState
              icon={Search}
              title="조건에 맞는 세션이 없습니다."
              description="Session ID 는 정확히 일치해야 검색됩니다. 조건을 해제하면 전체 목록을 볼 수 있습니다."
            >
              <div className="btn-row" style={{ marginTop: 14 }}>
                {/* 위 툴바에도 '조건 해제' 가 있다. 같은 이름을 두 개 두면
                    스크린 리더와 시험이 어느 쪽인지 구분할 수 없다. */}
                <button type="button" onClick={resetFilters} className="btn">
                  전체 목록 보기
                </button>
              </div>
            </EmptyState>
          ) : (
            <EmptyState
              icon={Inbox}
              title="아직 업로드된 세션이 없습니다."
              description="로봇이 판정을 완료하고 업로드하면 이 목록에 바로 표시됩니다."
            />
          )
        )}

        {phase === READY && rows.length > 0 && (
          <>
            <div className="table-scroll">
              <table className="grid">
                <thead>
                  <tr>
                    <th>Session ID</th>
                    <th>로봇</th>
                    <th>시작 (UTC)</th>
                    <th>길이</th>
                    <th>판정</th>
                    <th className="num">HandCommand</th>
                    <th className="num">MotorStatus</th>
                    <th className="num">크기</th>
                    <th />
                  </tr>
                </thead>
                <motion.tbody initial="initial" animate="animate" variants={list}>
                  {rows.map((entry, index) => (
                    <motion.tr key={`${entry.robot_id}/${entry.session_id}`} variants={item}>
                      <td className="mono">{entry.session_id}</td>
                      <td><span className="chip">{entry.robot_id}</span></td>
                      <td className="subtle">{formatUtc(entry.started_at)}</td>
                      <td className="mono">{formatDuration(entry.duration_ms)}</td>
                      <td><ResultChip result={entry.result} /></td>
                      <td className="num">
                        <Counter
                          value={entry.row_counts?.hand_command}
                          format={(n) => formatCount(Math.round(n))}
                          delay={0.1 + index * 0.028}
                        />
                      </td>
                      <td className="num">
                        <Counter
                          value={entry.row_counts?.motor_status}
                          format={(n) => formatCount(Math.round(n))}
                          delay={0.1 + index * 0.028}
                        />
                      </td>
                      <td className="num tight">
                        {formatBytes(entry.totalBytes)}
                        {/* 같은 페이지 안 최대 용량 대비 비율 */}
                        <motion.span
                          className="qbar"
                          aria-hidden="true"
                          {...grow(entry.sizeRatio, 0.18 + index * 0.028)}
                        />
                      </td>
                      <td className="row-action">
                        <Link to={`/sessions/${entry.session_id}`} className="btn btn-sm">
                          상세
                          <ChevronRight size={14} strokeWidth={2.2} aria-hidden="true" />
                        </Link>
                      </td>
                    </motion.tr>
                  ))}
                </motion.tbody>
              </table>
            </div>

            <div className="pager">
              <span className="count">{rows.length}건 표시</span>
              {cursor && (
                <button type="button" onClick={() => setCursor(null)} className="btn btn-sm">
                  처음으로
                </button>
              )}
              {nextCursor && (
                <button type="button" onClick={() => setCursor(nextCursor)} className="btn btn-sm">
                  다음 페이지
                  <ChevronRight size={14} strokeWidth={2.2} aria-hidden="true" />
                </button>
              )}
            </div>
          </>
        )}
      </motion.div>
    </main>
  );
}
