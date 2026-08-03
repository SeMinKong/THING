// frontend/src/utils/format.test.js
//
// 완료 조건: "UTC 표기와 API 값이 일치한다"
// 결측(null)과 0 의 구분도 여기서 고정한다.

import { describe, expect, it } from 'vitest';
import {
  MISSING, formatBytes, formatCount, formatDuration,
  formatInt, formatNumber, formatUtc, formatUtcTime,
} from './format';

describe('formatUtc', () => {
  it('API 의 RFC 3339 값을 그대로 UTC 로 표기한다', () => {
    expect(formatUtc('2026-07-29T00:01:00.000Z')).toBe('2026-07-29 00:01 UTC'.replace('00:01', '00:01:00'));
  });

  it('브라우저 시간대로 변환하지 않는다', () => {
    // KST(+09:00)로 변환되면 09:00 이 되어야 하는데 그러지 않아야 한다.
    const out = formatUtc('2026-07-29T00:00:00.000Z');
    expect(out).toContain('00:00:00');
    expect(out).not.toContain('09:00:00');
    expect(out).toContain('UTC');
  });

  it('자정을 넘는 값도 날짜가 밀리지 않는다', () => {
    expect(formatUtc('2026-07-29T23:30:00.000Z')).toBe('2026-07-29 23:30:00 UTC');
  });

  it('null 은 결측 기호', () => {
    expect(formatUtc(null)).toBe(MISSING);
    expect(formatUtcTime(null)).toBe(MISSING);
  });
});

describe('결측과 0 구분', () => {
  it('null 은 결측 기호, 0 은 0 으로 표시한다', () => {
    expect(formatNumber(null)).toBe(MISSING);
    expect(formatNumber(0)).toBe('0.000');
    expect(formatInt(null)).toBe(MISSING);
    expect(formatInt(0)).toBe('0');
  });

  it('NaN 은 결측으로 처리한다', () => {
    expect(formatNumber(Number.NaN)).toBe(MISSING);
  });

  it('소수 자리수를 지정할 수 있다', () => {
    expect(formatNumber(11.9, 2)).toBe('11.90');
  });
});

describe('보조 포맷', () => {
  it('기록 길이', () => {
    expect(formatDuration(60000)).toBe('1분 0초');
    expect(formatDuration(30000)).toBe('30초');
    expect(formatDuration(null)).toBe(MISSING);
  });

  it('파일 크기', () => {
    expect(formatBytes(421)).toBe('421 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(null)).toBe(MISSING);
  });

  it('행 수 천 단위 구분', () => {
    expect(formatCount(4200)).toBe('4,200');
    expect(formatCount(null)).toBe(MISSING);
  });
});
