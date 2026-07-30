// frontend/src/views/SessionListView.jsx
//
// [FR-47] 공개 세션 목록.
//   정렬은 서버가 started_at DESC, session_id DESC 로 보장한다.
//   exact Session ID 검색은 Must, result 필터는 Should.
//   기본 20건이며 next_cursor 로 다음 페이지를 받는다.

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { describeError, fetchSessions } from '../services/sessions';
import { formatBytes, formatCount, formatDuration, formatUtc } from '../utils/format';

const LOADING = 'loading';
const ERROR = 'error';
const READY = 'ready';

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

  return (
    <div className="page-container wide">
      <div className="header-actions">
        <div>
          <h2>📁 세션 목록</h2>
          <p className="subtle">
            판정이 완료되어 공개된 세션입니다. 최신 순으로 정렬되며 모든 시각은 UTC입니다.
          </p>
        </div>
        <button type="button" onClick={load} className="btn btn-secondary" disabled={phase === LOADING}>
          {phase === LOADING ? '불러오는 중…' : '새로고침'}
        </button>
      </div>

      <div className="card filter-bar">
        <form onSubmit={submitSearch} className="filter-group">
          <label htmlFor="session-search">Session ID</label>
          <input
            id="session-search"
            type="text"
            inputMode="numeric"
            placeholder="예: 123456789012345678"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
          />
          <button type="submit" className="btn btn-primary">검색</button>
        </form>

        <div className="filter-group">
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
          <button type="button" onClick={resetFilters} className="btn btn-secondary">
            조건 해제
          </button>
        )}
      </div>

      {phase === LOADING && (
        <div className="state-box card"><p>목록을 불러오고 있습니다…</p></div>
      )}

      {phase === ERROR && (
        <div className="state-box card state-error">
          <p>목록을 불러오지 못했습니다.</p>
          <small>{message}</small>
        </div>
      )}

      {phase === READY && items.length === 0 && (
        <div className="state-box card">
          <p>
            {hasFilter
              ? '조건에 맞는 세션이 없습니다.'
              : '📥 아직 업로드된 세션이 없습니다.'}
          </p>
          <small>
            {hasFilter
              ? '조건을 해제하면 전체 목록을 볼 수 있습니다.'
              : '로봇이 판정을 완료하고 업로드하면 이 목록에 바로 표시됩니다.'}
          </small>
        </div>
      )}

      {phase === READY && items.length > 0 && (
        <>
          <table className="data-table card">
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
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const totalBytes = Object.values(item.file_sizes || {}).reduce(
                  (sum, value) => sum + (value || 0),
                  0,
                );
                return (
                  <tr key={`${item.robot_id}/${item.session_id}`}>
                    <td className="mono">{item.session_id}</td>
                    <td><span className="badge">{item.robot_id}</span></td>
                    <td>{formatUtc(item.started_at)}</td>
                    <td>{formatDuration(item.duration_ms)}</td>
                    <td>
                      <span className={item.result === 'SUCCESS' ? 'pill pill-ok' : 'pill pill-fail'}>
                        {item.result}
                      </span>
                    </td>
                    <td className="num">{formatCount(item.row_counts?.hand_command)}</td>
                    <td className="num">{formatCount(item.row_counts?.motor_status)}</td>
                    <td className="num">{formatBytes(totalBytes)}</td>
                    <td>
                      <Link to={`/sessions/${item.session_id}`} className="btn btn-primary btn-sm">
                        상세
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <div className="pager">
            <span className="subtle">{items.length}건 표시</span>
            {cursor && (
              <button type="button" onClick={() => setCursor(null)} className="btn btn-secondary">
                처음으로
              </button>
            )}
            {nextCursor && (
              <button type="button" onClick={() => setCursor(nextCursor)} className="btn btn-secondary">
                다음 페이지 →
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
