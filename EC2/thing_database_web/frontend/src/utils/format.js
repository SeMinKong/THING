// frontend/src/utils/format.js
//
// 표시 규칙 (명세서 FR-48).
//
//   시각은 UTC 로 표시하고 그 사실을 화면에 명시한다.
//   결측(null)과 0 을 구분한다.
//   단위를 함께 표시한다.
//
// 서버는 RFC 3339 UTC 'Z' 로 보내므로 로컬 시간대로 변환하지 않는다.
// toLocaleString() 을 쓰면 브라우저 시간대가 섞여 UTC 명시가 무의미해진다.

/** 결측 표시. null·undefined 와 0 을 구분하기 위해 '0' 은 그대로 둔다. */
export const MISSING = '—';

/** "2026-07-29T00:01:00.000Z" -> "2026-07-29 00:01:00 UTC" */
export function formatUtc(iso) {
  if (!iso) return MISSING;
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/.exec(iso);
  return match ? `${match[1]} ${match[2]} UTC` : iso;
}

/** "2026-07-29T00:01:00.000Z" -> "00:01:00" (표 안 좁은 칸용) */
export function formatUtcTime(iso) {
  if (!iso) return MISSING;
  const match = /T(\d{2}:\d{2}:\d{2})/.exec(iso);
  return match ? match[1] : iso;
}

/** 60000 -> "1분 0초" */
export function formatDuration(ms) {
  if (ms == null) return MISSING;
  const total = Math.round(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes > 0 ? `${minutes}분 ${seconds}초` : `${seconds}초`;
}

/** 1234 -> "1.2 KB" */
export function formatBytes(bytes) {
  if (bytes == null) return MISSING;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 결측을 '—' 로, 숫자는 고정 소수점으로. 0 은 '0.000' 으로 표시된다. */
export function formatNumber(value, digits = 3) {
  if (value == null) return MISSING;
  if (typeof value !== 'number' || Number.isNaN(value)) return MISSING;
  return value.toFixed(digits);
}

/** 정수 결측 처리 */
export function formatInt(value) {
  if (value == null) return MISSING;
  return String(value);
}

/** 단위를 붙인 값. 결측이면 단위도 붙이지 않는다. */
export function withUnit(value, unit, digits = 3) {
  const text = formatNumber(value, digits);
  return text === MISSING ? MISSING : `${text} ${unit}`;
}

/** 큰 수에 천 단위 구분 */
export function formatCount(value) {
  if (value == null) return MISSING;
  return value.toLocaleString('en-US');
}
