// frontend/src/views/SessionDetailView.test.jsx
//
// 완료 조건
//   "로딩·빈 결과·404·서버 오류를 구분해 표시한다"
//   "HandCommand 또는 MotorStatus 중 선택한 한 종류의 기본 시계열 차트를 제공한다"
//   "대용량 세션에서도 페이지가 멈추지 않도록 조회 범위를 제한한다"

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../services/sessions';
import SessionDetailView from './SessionDetailView.jsx';
import {
  SESSION_ID, detailResponse, handCommandData, motorStatusData,
  networkError, notFoundError, serverError,
} from '../test/fixtures';

vi.mock('../services/sessions', async () => {
  const actual = await vi.importActual('../services/sessions');
  return {
    ...actual,
    fetchSessionDetail: vi.fn(),
    fetchAllSessionData: vi.fn(),
  };
});

function renderDetail(id = SESSION_ID) {
  return render(
    <MemoryRouter initialEntries={[`/sessions/${id}`]}>
      <Routes>
        <Route path="/sessions/:sessionId" element={<SessionDetailView />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** 정상 응답을 물려준다. overrides 로 truncated 등을 바꿀 수 있다. */
function mockHappyPath({ hc = {}, ms = {} } = {}) {
  api.fetchSessionDetail.mockResolvedValue(detailResponse);
  api.fetchAllSessionData.mockImplementation((_id, dataset) => Promise.resolve(
    dataset === 'hand_command'
      ? { ...handCommandData, ...hc }
      : { ...motorStatusData, ...ms },
  ));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('로딩 상태', () => {
  it('요청 중에는 로딩 문구를 보여준다', async () => {
    let resolve;
    api.fetchSessionDetail.mockReturnValue(new Promise((r) => { resolve = r; }));
    api.fetchAllSessionData.mockResolvedValue(handCommandData);
    renderDetail();
    expect(screen.getByText(/불러오고 있습니다/)).toBeInTheDocument();
    resolve(detailResponse);
    await waitFor(() => expect(screen.queryByText(/불러오고 있습니다/)).not.toBeInTheDocument());
  });
});

describe('404 와 서버 오류 구분', () => {
  it('404 는 "찾을 수 없습니다" 로 구분해 표시한다', async () => {
    api.fetchSessionDetail.mockRejectedValue(notFoundError);
    renderDetail('999');

    expect(await screen.findByText(/세션을 찾을 수 없습니다/)).toBeInTheDocument();
    // 서버 장애 문구가 아니어야 한다
    expect(screen.queryByText(/불러오지 못했습니다/)).not.toBeInTheDocument();
    // 목록으로 유도한다
    expect(screen.getByRole('link', { name: /목록에서 찾기/ })).toBeInTheDocument();
    // 요청한 ID 를 되짚어준다
    expect(screen.getByText('999')).toBeInTheDocument();
  });

  it('404 화면에는 다시 시도 버튼을 두지 않는다', async () => {
    api.fetchSessionDetail.mockRejectedValue(notFoundError);
    renderDetail('999');
    await screen.findByText(/세션을 찾을 수 없습니다/);
    expect(screen.queryByRole('button', { name: /다시 시도/ })).not.toBeInTheDocument();
  });

  it('500 은 서버 오류로 구분해 표시하고 재시도를 제공한다', async () => {
    api.fetchSessionDetail.mockRejectedValue(serverError);
    renderDetail();

    expect(await screen.findByText(/세션을 불러오지 못했습니다/)).toBeInTheDocument();
    expect(screen.queryByText(/찾을 수 없습니다/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /다시 시도/ })).toBeInTheDocument();
  });

  it('네트워크 오류는 연결 실패로 표시한다', async () => {
    api.fetchSessionDetail.mockRejectedValue(networkError);
    renderDetail();
    expect(await screen.findByText(/서버에 연결할 수 없습니다/)).toBeInTheDocument();
  });
});

describe('메타와 파일', () => {
  it('metadata 필드를 UTC 로 표시한다', async () => {
    mockHappyPath();
    renderDetail();

    await screen.findByText('THING-001');
    expect(screen.getByText('2026-07-29 00:00:00 UTC')).toBeInTheDocument();
    expect(screen.getByText('2026-07-29 00:01:00 UTC')).toBeInTheDocument();
    expect(screen.getByText('1분 0초')).toBeInTheDocument();
    expect(screen.getByText(detailResponse.interface_commit)).toBeInTheDocument();
    expect(screen.getByText(detailResponse.content_digest)).toBeInTheDocument();
  });

  it('세 파일의 존재와 크기를 표시한다', async () => {
    mockHappyPath();
    renderDetail();

    await screen.findByText('metadata JSON');
    expect(screen.getByText('HandCommand CSV')).toBeInTheDocument();
    expect(screen.getByText('MotorStatus CSV')).toBeInTheDocument();
    expect(screen.getByText('913 B')).toBeInTheDocument();
    expect(screen.getByText('421 B')).toBeInTheDocument();
    expect(screen.getByText('758 B')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: '다운로드' })).toHaveLength(3);
  });
});

describe('시계열 dataset 토글', () => {
  it('기본은 HandCommand 이고 차트가 렌더된다', async () => {
    mockHappyPath();
    renderDetail();

    await screen.findByTestId('timeseries-chart');
    expect(screen.getByRole('button', { name: 'HandCommand' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'MotorStatus' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText(/7논리축 최종 명령값/)).toBeInTheDocument();
    // HandCommand 에는 지표 선택이 없다
    expect(screen.queryByLabelText('지표')).not.toBeInTheDocument();
  });

  it('MotorStatus 로 바꾸면 지표 선택이 나타난다', async () => {
    mockHappyPath();
    renderDetail();
    await screen.findByTestId('timeseries-chart');

    await userEvent.click(screen.getByRole('button', { name: 'MotorStatus' }));

    expect(screen.getByRole('button', { name: 'MotorStatus' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('지표')).toBeInTheDocument();
    expect(screen.getByText(/모터별 현재 위치 \(rad\)/)).toBeInTheDocument();
    expect(screen.getByTestId('timeseries-chart')).toBeInTheDocument();
  });

  it('지표를 바꿀 수 있다', async () => {
    mockHappyPath();
    renderDetail();
    await screen.findByTestId('timeseries-chart');
    await userEvent.click(screen.getByRole('button', { name: 'MotorStatus' }));

    await userEvent.selectOptions(screen.getByLabelText('지표'), 'temperature_celsius');
    expect(screen.getByText(/모터별 온도 \(°C\)/)).toBeInTheDocument();
  });

  it('MotorStatus 차트에는 관절각 고지가 붙는다', async () => {
    mockHappyPath();
    renderDetail();
    await screen.findByTestId('timeseries-chart');
    await userEvent.click(screen.getByRole('button', { name: 'MotorStatus' }));

    expect(screen.getAllByText(/실제 관절각이 아닙니다/).length).toBeGreaterThanOrEqual(2);
  });

  it('데이터가 없으면 차트 대신 안내를 보여준다', async () => {
    mockHappyPath({ hc: { rows: [] } });
    renderDetail();
    await screen.findByText('THING-001');
    expect(screen.queryByTestId('timeseries-chart')).not.toBeInTheDocument();
    expect(screen.getByText(/표시할 데이터가 없습니다/)).toBeInTheDocument();
  });
});

describe('조회 범위 제한', () => {
  it('상한 5000행으로 요청한다', async () => {
    mockHappyPath();
    renderDetail();
    await screen.findByTestId('timeseries-chart');

    expect(api.fetchAllSessionData).toHaveBeenCalledWith(SESSION_ID, 'hand_command', { maxRows: 5000 });
    expect(api.fetchAllSessionData).toHaveBeenCalledWith(SESSION_ID, 'motor_status', { maxRows: 5000 });
  });

  it('HandCommand 가 잘리면 경고를 보여준다', async () => {
    mockHappyPath({ hc: { truncated: true } });
    renderDetail();
    await screen.findByTestId('timeseries-chart');
    expect(screen.getByText(/앞부분 3행만 표시합니다/)).toBeInTheDocument();
  });

  it('MotorStatus 가 잘리면 표가 세션 종료 시점이 아님을 알린다', async () => {
    mockHappyPath({ ms: { truncated: true } });
    renderDetail();
    await screen.findByText('THING-001');

    expect(screen.getByText(/조회 구간 안에서의/)).toBeInTheDocument();
    expect(screen.getByText(/세션 종료/)).toBeInTheDocument();
    // 잘렸을 때 "전체" 라고 쓰지 않는다
    expect(screen.getByText(/조회한 4행/)).toBeInTheDocument();
  });

  it('잘리지 않았으면 전체라고 표시한다', async () => {
    mockHappyPath();
    renderDetail();
    await screen.findByText('THING-001');
    expect(screen.getByText(/전체 4행/)).toBeInTheDocument();
    expect(screen.queryByText(/조회 구간 안에서의/)).not.toBeInTheDocument();
  });
});

describe('모터 상태 표 — 결측과 통신실패 구분', () => {
  it('결측은 결측 기호로, 0 은 0 으로 표시한다', async () => {
    mockHappyPath();
    renderDetail();
    await screen.findByText('THING-001');

    const table = within(screen.getAllByRole('table')[1]);
    // motor 12 마지막 샘플: present_position_rad = null
    expect(table.getAllByText('—').length).toBeGreaterThan(0);
    // motor 11 마지막 샘플: velocity_rad_s = 0.52, goal_position_rad = 0.157
    expect(table.getByText('0.157')).toBeInTheDocument();
  });

  it('통신 실패 모터를 구분해 표시한다', async () => {
    mockHappyPath();
    renderDetail();
    await screen.findByText('THING-001');

    // '실패' 는 pill 과 '실패' 컬럼 헤더에 모두 있어 tbody 로 범위를 좁힌다
    const body = within(screen.getAllByRole('rowgroup')[3]);
    expect(body.getByText('실패')).toBeInTheDocument();
    expect(body.getByText('정상')).toBeInTheDocument();
    expect(screen.getByText(/통신 실패 행/)).toBeInTheDocument();

    // 실패 행에 표시 클래스가 붙는다
    const failedRow = body.getByText('실패').closest('tr');
    expect(failedRow).toHaveClass('row-fail');
  });
});

describe('제어 기능 미포함', () => {
  it('업로드·STOP·mode·Safety Reset 조작이 없다', async () => {
    mockHappyPath();
    renderDetail();
    await screen.findByTestId('timeseries-chart');

    const controls = [...screen.getAllByRole('button'), ...screen.getAllByRole('link')]
      .map((el) => el.textContent).join(' ');
    for (const forbidden of ['업로드하', 'STOP', 'Safety', '제어', '초기화']) {
      expect(controls).not.toContain(forbidden);
    }
  });
});
