// frontend/src/views/SessionListView.test.jsx
//
// 완료 조건: "로딩·빈 결과·404·서버 오류를 구분해 표시한다" 중 목록 화면 몫.

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../services/sessions';
import SessionListView from './SessionListView.jsx';
import { emptyListResponse, listResponse, networkError, serverError } from '../test/fixtures';

vi.mock('../services/sessions', async () => {
  const actual = await vi.importActual('../services/sessions');
  return { ...actual, fetchSessions: vi.fn() };
});

function renderList() {
  return render(<MemoryRouter><SessionListView /></MemoryRouter>);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('로딩 상태', () => {
  it('요청 중에는 로딩 문구를 보여준다', async () => {
    let resolve;
    api.fetchSessions.mockReturnValue(new Promise((r) => { resolve = r; }));
    renderList();
    expect(screen.getByText(/불러오고 있습니다/)).toBeInTheDocument();
    resolve(emptyListResponse);
    await waitFor(() => expect(screen.queryByText(/불러오고 있습니다/)).not.toBeInTheDocument());
  });
});

describe('정상 렌더링', () => {
  it('session_id·UTC 시각·결과를 표로 보여준다', async () => {
    api.fetchSessions.mockResolvedValue(listResponse);
    renderList();

    expect(await screen.findByText('123456789012345678')).toBeInTheDocument();

    // 판정 pill 은 표 안에서 확인한다.
    // 필터 select 에도 SUCCESS/FAILURE option 이 있어 화면 전체 검색은 중복된다.
    const table = within(screen.getByRole('table'));
    expect(table.getByText('999999999999999999')).toBeInTheDocument();
    expect(table.getByText('SUCCESS')).toBeInTheDocument();
    expect(table.getByText('FAILURE')).toBeInTheDocument();

    // UTC 표기가 API 값과 일치하고 시간대 변환이 없어야 한다
    expect(table.getByText('2026-07-29 00:00:00 UTC')).toBeInTheDocument();
    expect(screen.getByText(/시작 \(UTC\)/)).toBeInTheDocument();
  });

  it('행 수와 파일 크기 합계를 보여준다', async () => {
    api.fetchSessions.mockResolvedValue(listResponse);
    renderList();
    await screen.findByText('123456789012345678');

    const table = within(screen.getByRole('table'));
    expect(table.getByText('2.0 KB')).toBeInTheDocument();   // 913+421+758 = 2092
    expect(table.getByText('3')).toBeInTheDocument();        // hand_command row_counts
    expect(table.getByText('4')).toBeInTheDocument();        // motor_status row_counts
  });
});

describe('빈 결과', () => {
  it('데이터가 없으면 안내 문구를 보여준다', async () => {
    api.fetchSessions.mockResolvedValue(emptyListResponse);
    renderList();
    expect(await screen.findByText(/아직 업로드된 세션이 없습니다/)).toBeInTheDocument();
  });

  it('검색 결과가 없을 때는 조건 안내로 구분한다', async () => {
    api.fetchSessions.mockResolvedValue(emptyListResponse);
    renderList();
    await screen.findByText(/아직 업로드된 세션이 없습니다/);

    await userEvent.type(screen.getByLabelText('Session ID'), '111');
    await userEvent.click(screen.getByRole('button', { name: '검색' }));

    expect(await screen.findByText(/조건에 맞는 세션이 없습니다/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '조건 해제' })).toBeInTheDocument();
  });
});

describe('오류 fixture', () => {
  it('500 은 서버 오류로 표시한다', async () => {
    api.fetchSessions.mockRejectedValue(serverError);
    renderList();
    expect(await screen.findByText(/목록을 불러오지 못했습니다/)).toBeInTheDocument();
    expect(screen.getByText(/An internal error occurred/)).toBeInTheDocument();
  });

  it('네트워크 오류는 연결 실패 문구를 보여준다', async () => {
    api.fetchSessions.mockRejectedValue(networkError);
    renderList();
    expect(await screen.findByText(/목록을 불러오지 못했습니다/)).toBeInTheDocument();
    expect(screen.getByText(/서버에 연결할 수 없습니다/)).toBeInTheDocument();
  });

  it('오류 화면에는 데이터 표를 그리지 않는다', async () => {
    api.fetchSessions.mockRejectedValue(serverError);
    renderList();
    await screen.findByText(/목록을 불러오지 못했습니다/);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});

describe('제어 기능 미포함', () => {
  it('업로드·STOP·mode 같은 조작 버튼이 없다', async () => {
    api.fetchSessions.mockResolvedValue(listResponse);
    renderList();
    await screen.findByText('123456789012345678');

    const labels = screen.getAllByRole('button').map((b) => b.textContent);
    for (const forbidden of ['업로드', 'STOP', 'Safety', 'mode', '제어']) {
      expect(labels.join(' ')).not.toContain(forbidden);
    }
  });
});
