// frontend/src/test/viewport.test.jsx
//
// 완료 조건: "모바일·Laptop 기본 화면 ... 테스트가 통과한다"
//
// jsdom 은 레이아웃을 계산하지 않으므로 픽셀 단위 검증은 불가하다.
// 대신 두 가지를 고정한다.
//   1) 각 폭에서 핵심 정보가 DOM 에 존재하고 렌더가 깨지지 않는지
//   2) 반응형 규칙이 CSS 에 실제로 존재하는지 (미디어쿼리 계약)

import fs from 'node:fs';
import path from 'node:path';

import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../services/sessions';
import SessionListView from '../views/SessionListView.jsx';
import SessionDetailView from '../views/SessionDetailView.jsx';
import { SESSION_ID, detailResponse, handCommandData, listResponse, motorStatusData } from './fixtures';

vi.mock('../services/sessions', async () => {
  const actual = await vi.importActual('../services/sessions');
  return {
    ...actual,
    fetchSessions: vi.fn(),
    fetchSessionDetail: vi.fn(),
    fetchAllSessionData: vi.fn(),
  };
});

/** 기준 화면 폭 */
const VIEWPORTS = [
  { name: '모바일 (375px)', width: 375 },
  { name: '태블릿 (768px)', width: 768 },
  { name: 'Laptop (1440px)', width: 1440 },
];

function setViewport(width) {
  window.innerWidth = width;
  // matchMedia 는 jsdom 기본 구현이 없어 폭 기준으로 대체한다
  window.matchMedia = (query) => {
    const max = /max-width:\s*(\d+)px/.exec(query);
    return {
      matches: max ? width <= Number(max[1]) : false,
      media: query,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent() { return false; },
    };
  };
  window.dispatchEvent(new Event('resize'));
}

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchSessions.mockResolvedValue(listResponse);
  api.fetchSessionDetail.mockResolvedValue(detailResponse);
  api.fetchAllSessionData.mockImplementation((_id, dataset) => Promise.resolve(
    dataset === 'hand_command' ? handCommandData : motorStatusData,
  ));
});

describe.each(VIEWPORTS)('$name', ({ width }) => {
  it('목록 화면이 핵심 정보를 렌더한다', async () => {
    setViewport(width);
    render(<MemoryRouter><SessionListView /></MemoryRouter>);

    expect(await screen.findByText(SESSION_ID)).toBeInTheDocument();
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByLabelText('Session ID')).toBeInTheDocument();
    // fixture 세션 2건 -> 상세 링크 2개
    expect(screen.getAllByRole('link', { name: '상세' })).toHaveLength(2);
  });

  it('상세 화면이 차트와 표를 렌더한다', async () => {
    setViewport(width);
    render(
      <MemoryRouter initialEntries={[`/sessions/${SESSION_ID}`]}>
        <Routes>
          <Route path="/sessions/:sessionId" element={<SessionDetailView />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('timeseries-chart')).toBeInTheDocument();
    expect(screen.getAllByRole('table')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'HandCommand' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'MotorStatus' })).toBeInTheDocument();
  });
});

describe('반응형 CSS 계약', () => {
  const css = fs.readFileSync(path.resolve(__dirname, '../index.css'), 'utf-8');

  it('좁은 화면 미디어쿼리가 존재한다', () => {
    expect(css).toMatch(/@media\s*\(max-width:\s*640px\)/);
    expect(css).toMatch(/@media\s*\(max-width:\s*860px\)/);
  });

  it('넓은 표는 가로 스크롤로 처리한다', () => {
    expect(css).toMatch(/\.table-scroll\s*\{[^}]*overflow-x:\s*auto/);
  });

  it('모션 최소화 설정을 존중한다', () => {
    expect(css).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
  });

  it('키보드 포커스 표시가 있다', () => {
    expect(css).toMatch(/:focus-visible/);
  });
});
