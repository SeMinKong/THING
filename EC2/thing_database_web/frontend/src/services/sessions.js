// frontend/src/services/sessions.js
//
// v1 데이터 계약 API 클라이언트 (명세서 6.5절).
//
//   GET /api/v1/sessions
//   GET /api/v1/sessions/{session_id}
//   GET /api/v1/sessions/{session_id}/data?dataset=&cursor=&limit=
//   GET /api/v1/sessions/{session_id}/download/{file_kind}
//
// 모든 응답의 시각은 RFC 3339 UTC 'Z' 이고 session_id 는 문자열이다.
// 서버 오류는 {"error":{"code","message","details"},"request_id"} envelope 으로 온다.

import axios from 'axios';

// api.js 의 인스턴스는 baseURL 이 /api 라서 /api/v1 경로와 맞지 않는다.
// v1 전용 인스턴스를 따로 둔다.
const v1 = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
});

/** 서버 오류 envelope 에서 사람이 읽을 메시지를 뽑는다. */
export function describeError(error) {
  const envelope = error?.response?.data?.error;
  if (!envelope) {
    return '서버에 연결할 수 없습니다.';
  }
  const details = Array.isArray(envelope.details) ? envelope.details : [];
  return details.length > 0
    ? `${envelope.message} (${details.join(', ')})`
    : envelope.message;
}

/**
 * 404 인지 판별한다.
 * 없는 세션(사용자 실수)과 서버 장애(운영 문제)는 화면에서 구분해야 한다.
 */
export function isNotFound(error) {
  return error?.response?.status === 404
    || error?.response?.data?.error?.code === 'NOT_FOUND';
}

export const LIST_PAGE_SIZE = 20;

/** 목록. 정렬은 서버가 started_at DESC, session_id DESC 로 보장한다. */
export async function fetchSessions({ sessionId, result, cursor, limit = LIST_PAGE_SIZE } = {}) {
  const params = { limit };
  if (sessionId) params.session_id = sessionId;
  if (result) params.result = result;
  if (cursor) params.cursor = cursor;
  const { data } = await v1.get('/sessions', { params });
  return data;
}

export async function fetchSessionDetail(sessionId) {
  const { data } = await v1.get(`/sessions/${encodeURIComponent(sessionId)}`);
  return data;
}

/** 시계열. 기본 1000행·최대 5000행이며 timestamp 오름차순이다. */
export async function fetchSessionData(sessionId, dataset, { limit = 1000, cursor } = {}) {
  const params = { dataset, limit };
  if (cursor) params.cursor = cursor;
  const { data } = await v1.get(`/sessions/${encodeURIComponent(sessionId)}/data`, { params });
  return data;
}

/**
 * 시계열 전체를 cursor 를 따라가며 모은다.
 * 대용량 세션에서 브라우저가 멈추지 않도록 maxRows 로 상한을 둔다.
 */
export async function fetchAllSessionData(sessionId, dataset, { maxRows = 5000 } = {}) {
  const rows = [];
  let cursor = null;
  let columns = [];
  let truncated = false;

  for (let page = 0; page < 20; page += 1) {
    const remaining = maxRows - rows.length;
    if (remaining <= 0) {
      truncated = true;
      break;
    }
    const body = await fetchSessionData(sessionId, dataset, {
      limit: Math.min(remaining, 5000),
      cursor,
    });
    columns = body.columns;
    rows.push(...body.rows);
    cursor = body.next_cursor;
    if (!cursor) break;
    if (page === 19) truncated = true;
  }

  return { columns, rows, truncated };
}
