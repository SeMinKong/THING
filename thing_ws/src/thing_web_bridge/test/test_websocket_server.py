"""Transport behaviour: STOP preemption, ordering, and single connection."""

import asyncio
import json

import pytest

from thing_web_bridge.protocol import make_ack
from thing_web_bridge.protocol import SnapshotStore
from thing_web_bridge.websocket_server import REASON_PREEMPTED_BY_STOP
from thing_web_bridge.websocket_server import REASON_QUEUE_OVERFLOW
from thing_web_bridge.websocket_server import REASON_SINGLE_CONNECTION
from thing_web_bridge.websocket_server import REASON_SUPERSEDED
from thing_web_bridge.websocket_server import WebSocketServer


class FakeWebSocket:
    """Collect what the bridge sends and feed it scripted client messages."""

    def __init__(self, incoming=()):
        self._incoming = list(incoming)
        self.sent = []
        self.closed = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._incoming:
            raise StopAsyncIteration
        return self._incoming.pop(0)

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def close(self, code, reason):
        self.closed = (code, reason)

    def acks(self):
        return [item for item in self.sent if item.get('type') == 'ack']


def message(request_type, request_id, payload=None):
    """Build one raw client frame."""
    if payload is None:
        payload = {}
    return json.dumps({
        'request_id': request_id,
        'type': request_type,
        'timestamp': '2026-08-04T12:00:00Z',
        'payload': payload,
    })


def renew(request_id, mode='MIMIC'):
    """Build one FR-34 lease renewal frame."""
    return message(
        'set_control_mode',
        request_id,
        {'requested_mode': mode, 'requested_owner': 'WEB'},
    )


def stop(request_id):
    """Build one explicit STOP frame."""
    return message(
        'stop',
        request_id,
        {'requested_mode': 'DISABLED', 'requested_owner': 'NONE'},
    )


def accept_handler(request):
    """Accept every request immediately."""
    return make_ack(request.request_id, True, 'accepted')


def make_server(handler=accept_handler, **kwargs):
    """Build a server that never opens a socket."""
    return WebSocketServer(SnapshotStore(), handler, **kwargs)


def run_inline(coroutine, monkeypatch):
    """Run the handler on the loop so ordering is deterministic in tests."""
    async def call_inline(function, *args):
        return function(*args)

    monkeypatch.setattr(asyncio, 'to_thread', call_inline)
    return asyncio.run(coroutine)


# --------------------------------------------------------------------------
# 요청 검증과 ACK
# --------------------------------------------------------------------------

def test_valid_request_is_dispatched_and_acknowledged(monkeypatch):
    websocket = FakeWebSocket()
    session = make_server().new_session(websocket)

    run_inline(session.handle_message(message('reset_safety', 'req-1')),
               monkeypatch)

    assert websocket.sent[0]['request_id'] == 'req-1'
    assert websocket.sent[0]['type'] == 'ack'
    assert websocket.sent[0]['accepted'] is True


def test_malformed_json_is_rejected_without_dispatch(monkeypatch):
    websocket = FakeWebSocket()
    session = make_server().new_session(websocket)

    run_inline(session.handle_message('{'), monkeypatch)

    assert websocket.sent[0]['accepted'] is False
    assert websocket.sent[0]['reason'] == 'web_malformed_request'


def test_unknown_request_keeps_request_id_in_rejection(monkeypatch):
    websocket = FakeWebSocket()
    session = make_server().new_session(websocket)

    run_inline(session.handle_message(message('delete_robot', 'req-unknown')),
               monkeypatch)

    assert websocket.sent[0]['request_id'] == 'req-unknown'
    assert websocket.sent[0]['accepted'] is False
    assert websocket.sent[0]['reason'] == 'web_unknown_type'


# --------------------------------------------------------------------------
# FR-19 / FR-31: STOP 선점
# --------------------------------------------------------------------------

def test_stop_runs_without_waiting_for_a_blocked_general_request():
    """일반 요청이 ROS 응답을 기다리는 동안에도 STOP은 즉시 실행된다."""
    started = asyncio.Event()
    release = asyncio.Event()
    order = []

    async def blocking_handler(request):
        if request.type == 'execute_gesture':
            order.append('gesture-start')
            started.set()
            await release.wait()
            order.append('gesture-end')
        else:
            order.append(request.type)
        return make_ack(request.request_id, True, 'accepted')

    async def scenario():
        async def to_thread(function, *args):
            return await blocking_handler(*args)

        websocket = FakeWebSocket()
        session = make_server().new_session(websocket)
        original = asyncio.to_thread
        asyncio.to_thread = to_thread
        try:
            worker = asyncio.ensure_future(session._worker_loop())
            await session.handle_message(
                message('execute_gesture', 'g-1',
                        {'gesture_name': 'open', 'speed_limit': 1.0}))
            await started.wait()
            await session.handle_message(stop('stop-1'))
            for _ in range(20):
                await asyncio.sleep(0)
                if any(a['request_id'] == 'stop-1'
                       for a in websocket.acks()):
                    break
            stop_done = [a['request_id'] for a in websocket.acks()]
            release.set()
            await asyncio.sleep(0)
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            return stop_done, order
        finally:
            asyncio.to_thread = original

    stop_done, order = asyncio.run(scenario())

    # STOP ack가 gesture보다 먼저 나갔다
    assert stop_done == ['stop-1']
    # gesture는 아직 ROS에서 끝나지 않은 상태였다
    assert order == ['gesture-start', 'stop', 'gesture-end']


def test_stop_discards_unstarted_requests_with_an_ack(monkeypatch):
    """폐기한 일반 요청에도 같은 request_id로 실패 ACK를 돌려준다."""
    websocket = FakeWebSocket()
    session = make_server().new_session(websocket)

    async def scenario():
        # worker를 돌리지 않아 전부 대기열에 남는다.
        await session.handle_message(
            message('start_recording', 'rec-1', {'label': ''}))
        await session.handle_message(
            message('execute_gesture', 'g-1',
                    {'gesture_name': 'fist', 'speed_limit': 1.0}))
        await session.handle_message(stop('stop-1'))
        for _ in range(20):
            await asyncio.sleep(0)

    run_inline(scenario(), monkeypatch)

    preempted = {
        ack['request_id']: ack['reason']
        for ack in websocket.acks()
        if ack['reason'] == REASON_PREEMPTED_BY_STOP
    }
    assert preempted == {
        'rec-1': REASON_PREEMPTED_BY_STOP,
        'g-1': REASON_PREEMPTED_BY_STOP,
    }
    assert all(
        ack['accepted'] is False
        for ack in websocket.acks()
        if ack['reason'] == REASON_PREEMPTED_BY_STOP
    )
    # STOP 자신은 실행됐다
    assert any(
        ack['request_id'] == 'stop-1' and ack['accepted'] is True
        for ack in websocket.acks()
    )


def test_reset_safety_also_skips_the_queue_but_keeps_pending_work(monkeypatch):
    """Safety Reset은 대기열을 건너뛰되 대기 중 일반 요청을 버리지 않는다."""
    websocket = FakeWebSocket()
    session = make_server().new_session(websocket)

    async def scenario():
        await session.handle_message(
            message('start_recording', 'rec-1', {'label': ''}))
        await session.handle_message(message('reset_safety', 'reset-1'))
        for _ in range(20):
            await asyncio.sleep(0)

    run_inline(scenario(), monkeypatch)

    assert any(
        ack['request_id'] == 'reset-1' and ack['accepted'] is True
        for ack in websocket.acks()
    )
    assert not any(
        ack['reason'] == REASON_PREEMPTED_BY_STOP
        for ack in websocket.acks()
    )
    assert [queued.request_id for queued in session._pending] == ['rec-1']


# --------------------------------------------------------------------------
# FR-34: lease 갱신 중복 제거
# --------------------------------------------------------------------------

def test_duplicate_lease_renewals_collapse_to_the_latest(monkeypatch):
    """대기 중 갱신은 최신 하나만 남기고 교체된 것은 ACK로 알린다."""
    websocket = FakeWebSocket()
    session = make_server().new_session(websocket)

    async def scenario():
        await session.handle_message(renew('renew-1'))
        await session.handle_message(renew('renew-2'))
        await session.handle_message(renew('renew-3'))

    run_inline(scenario(), monkeypatch)

    assert [queued.request_id for queued in session._pending] == ['renew-3']
    superseded = [
        ack['request_id'] for ack in websocket.acks()
        if ack['reason'] == REASON_SUPERSEDED
    ]
    assert superseded == ['renew-1', 'renew-2']


def test_a_different_mode_renewal_is_not_collapsed(monkeypatch):
    """다른 mode 요청은 갱신이 아니므로 합치지 않는다."""
    websocket = FakeWebSocket()
    session = make_server().new_session(websocket)

    async def scenario():
        await session.handle_message(renew('mimic-1', 'MIMIC'))
        await session.handle_message(renew('manual-1', 'MANUAL'))

    run_inline(scenario(), monkeypatch)

    assert [queued.request_id for queued in session._pending] == [
        'mimic-1', 'manual-1']
    assert not websocket.acks()


def test_queue_overflow_is_refused_with_a_reason(monkeypatch):
    websocket = FakeWebSocket()
    session = make_server(max_pending=2).new_session(websocket)

    async def scenario():
        for index in range(4):
            await session.handle_message(
                message('start_recording', f'rec-{index}', {'label': ''}))

    run_inline(scenario(), monkeypatch)

    refused = [
        ack['request_id'] for ack in websocket.acks()
        if ack['reason'] == REASON_QUEUE_OVERFLOW
    ]
    assert refused == ['rec-2', 'rec-3']
    assert len(session._pending) == 2


# --------------------------------------------------------------------------
# 순서 보장과 재연결
# --------------------------------------------------------------------------

def test_general_requests_keep_arrival_order(monkeypatch):
    order = []

    def handler(request):
        order.append(request.request_id)
        return make_ack(request.request_id, True, 'accepted')

    websocket = FakeWebSocket()
    session = make_server(handler).new_session(websocket)

    async def scenario():
        for index in range(3):
            await session.handle_message(
                message('start_recording', f'rec-{index}', {'label': ''}))
        worker = asyncio.ensure_future(session._worker_loop())
        for _ in range(30):
            await asyncio.sleep(0)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    run_inline(scenario(), monkeypatch)

    assert order == ['rec-0', 'rec-1', 'rec-2']


def test_a_new_session_starts_with_no_queued_work(monkeypatch):
    """재연결은 이전 요청을 재생하지 않는다 (NFR-15)."""
    server = make_server()
    first = FakeWebSocket()
    first_session = server.new_session(first)

    async def scenario():
        await first_session.handle_message(
            message('start_recording', 'rec-1', {'label': ''}))

    run_inline(scenario(), monkeypatch)
    assert len(first_session._pending) == 1

    second_session = server.new_session(FakeWebSocket())
    assert len(second_session._pending) == 0


def test_disconnect_drops_unstarted_work_and_reconnect_replays_nothing():
    """
    연결이 끊기면 대기 중 요청이 사라지고 재연결이 재실행하지 않는다.

    Jira 완료조건: "연결 해제·재연결에서 이전 요청 자동 재실행이 0건이다."
    NFR-15: "재연결은 이전 명령을 재생하지 않는다."
    """
    executed = []
    gate = asyncio.Event()

    async def blocking_handler(request):
        executed.append(request.request_id)
        await gate.wait()          # 첫 요청을 ROS에서 붙잡아 둔다
        return make_ack(request.request_id, True, 'accepted')

    async def scenario():
        async def to_thread(function, *args):
            return await blocking_handler(*args)

        original = asyncio.to_thread
        asyncio.to_thread = to_thread
        try:
            server = make_server(snapshot_period=999)
            # 요청 3건을 준 뒤 바로 연결이 끊기는 클라이언트
            first = FakeWebSocket([
                message('start_recording', 'rec-1', {'label': ''}),
                message('execute_gesture', 'g-1',
                        {'gesture_name': 'open', 'speed_limit': 1.0}),
                message('execute_gesture', 'g-2',
                        {'gesture_name': 'fist', 'speed_limit': 1.0}),
            ])
            await server.new_session(first).run()
            after_first = list(executed)

            # 재연결: 아무 요청도 보내지 않는다
            second = FakeWebSocket()
            await server.new_session(second).run()
            return after_first, list(executed), second.acks()
        finally:
            asyncio.to_thread = original
            gate.set()

    after_first, after_second, second_acks = asyncio.run(scenario())

    # 첫 세션에서 시작된 것은 rec-1 하나뿐이고 나머지는 대기열에서 사라졌다
    assert after_first == ['rec-1']
    # 재연결 뒤 추가 실행 0건
    assert after_second == after_first
    # 재연결 세션은 이전 요청의 ACK를 보내지 않는다
    assert second_acks == []


# --------------------------------------------------------------------------
# endpoint와 동시 접속
# --------------------------------------------------------------------------

def test_only_configured_endpoint_is_accepted():
    server = make_server()
    websocket = FakeWebSocket()

    asyncio.run(server._client_connected(websocket, '/wrong'))

    assert websocket.closed == (1008, 'endpoint not allowed')


def test_query_string_does_not_change_endpoint_match():
    server = make_server()

    assert server._client_path(
        FakeWebSocket(), '/ws/robot-state?token=ignored'
    ) == '/ws/robot-state'


def test_second_connection_is_refused_while_one_is_active():
    """탭을 두 개 열면 한쪽 STOP이 다른 쪽 제어권을 해제하므로 하나만 받는다."""
    server = make_server()
    server._client_active = True
    websocket = FakeWebSocket()

    asyncio.run(server._client_connected(websocket, '/ws/robot-state'))

    assert websocket.closed == (1013, REASON_SINGLE_CONNECTION)


def test_snapshot_period_and_pending_bounds_are_validated():
    with pytest.raises(ValueError):
        make_server(snapshot_period=0.0)
    with pytest.raises(ValueError):
        make_server(max_pending=0)
