"""
Drive the real WebSocket transport with an actual client.

다른 테스트는 FakeWebSocket으로 ClientSession을 직접 호출한다. 그것만으로는
handshake, close code, 실제 프레임 왕복을 확인할 수 없다. 이 파일은 진짜
websockets 클라이언트로 붙어 web/docs/interfaces-bridge.md의 조항을 wire에서
검증한다.

Jira 완료조건: "Laptop에서 정의된 6개 필드의 상태 JSON과 request_id 기반
응답이 수신된다", "각 허용·거부 요청과 이유가 구분된다", "연결 해제·재연결에서
이전 요청 자동 재실행이 0건이다".

websockets 9.1은 Python 3.10에서 제거된 loop 인자를 asyncio에 넘겨 접속이
즉시 끊긴다. 그 환경에서는 전체를 skip한다. Jetson(Ubuntu 22.04) apt 기본값은
10.1이므로 실제 배포 환경에서는 항상 실행된다.
"""

import asyncio
import json

import pytest

from thing_web_bridge.protocol import make_ack
from thing_web_bridge.protocol import SnapshotStore
from thing_web_bridge.websocket_server import WebSocketServer

websockets = pytest.importorskip('websockets', reason='websockets required')

WEBSOCKETS_MAJOR = int(websockets.__version__.split('.', maxsplit=1)[0])

pytestmark = pytest.mark.skipif(
    WEBSOCKETS_MAJOR < 10,
    reason=(
        f'websockets {websockets.__version__} passes a removed loop argument '
        'to asyncio on Python 3.10; 10.0 or newer is required'
    ),
)

PORT = 8579
URL = f'ws://127.0.0.1:{PORT}/ws/robot-state'
TIMEOUT = 5.0

SNAPSHOT_FIXED = (
    'timestamp',
    'mode',
    'recording_state',
    'landmarks',
    'motor_state',
    'safety_state',
    'control_state',
    'recording',
)


def _accept_everything(request):
    """Stand in for the ROS dispatch path and echo the request id back."""
    return make_ack(request.request_id, True, 'accepted')


def _request(request_type, request_id, payload):
    return json.dumps({
        'request_id': request_id,
        'type': request_type,
        'timestamp': '2026-08-05T12:00:00.000Z',
        'payload': payload,
    })


def _close_code(error):
    """Read the close code across websockets 10 and 11+ layouts."""
    code = getattr(error, 'code', None)
    if code is None:
        code = getattr(getattr(error, 'rcvd', None), 'code', None)
    return code


async def _next_matching(connection, predicate, limit=40):
    """Read frames until one matches; snapshots arrive between ACKs."""
    for _ in range(limit):
        raw = await asyncio.wait_for(connection.recv(), timeout=TIMEOUT)
        message = json.loads(raw)
        if predicate(message):
            return message
    return None


def _is_snapshot(message):
    return message.get('type') is None


def _is_ack_for(request_id):
    def predicate(message):
        return (
            message.get('type') == 'ack'
            and message.get('request_id') == request_id
        )
    return predicate


@pytest.fixture(name='server')
def server_fixture():
    """Serve one real socket for the whole module."""
    server = WebSocketServer(
        SnapshotStore(),
        _accept_everything,
        host='127.0.0.1',
        port=PORT,
        snapshot_period=0.05,
    )
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _run(coroutine):
    return asyncio.run(coroutine)


# --------------------------------------------------------------------------
# snapshot
# --------------------------------------------------------------------------

def test_a_real_client_receives_the_contract_snapshot(server):
    """완료조건: 정의된 6개 필드의 상태 JSON이 수신된다."""
    async def scenario():
        async with websockets.connect(URL) as connection:
            return await _next_matching(connection, _is_snapshot)

    snapshot = _run(scenario())

    assert snapshot is not None
    assert tuple(snapshot)[:8] == SNAPSHOT_FIXED
    # 웹은 type 유무로 snapshot과 ACK를 구분한다 (계약 1.1).
    assert 'type' not in snapshot
    # 웹은 mode가 문자열인지로 snapshot을 판별한다. 정수면 전체를 버린다.
    assert isinstance(snapshot['mode'], str)
    # 아직 못 받은 객체는 null이 아니라 {} (6.4절).
    assert snapshot['landmarks'] == {}
    assert snapshot['control_state'] == {}
    assert snapshot['recording'] == {}
    for field in ('last_hand_command', 'connection_status'):
        assert field in snapshot


def test_connection_status_uses_three_values(server):
    """FR-24: up·down·unknown 세 값이며 bool이 아니다."""
    async def scenario():
        async with websockets.connect(URL) as connection:
            return await _next_matching(connection, _is_snapshot)

    status = _run(scenario())['connection_status']

    assert set(status) == {'jetson', 'rpi', 'ros2', 'camera', 'motor'}
    assert all(value in ('unknown', 'up', 'down') for value in status.values())


# --------------------------------------------------------------------------
# 요청과 ACK
# --------------------------------------------------------------------------

def test_every_request_gets_one_ack_with_the_same_request_id(server):
    """완료조건: request_id 기반 응답이 수신된다."""
    async def scenario():
        async with websockets.connect(URL) as connection:
            await connection.send(_request(
                'execute_gesture', 'req-gesture',
                {'gesture_name': 'open', 'speed_limit': 1.0}))
            return await _next_matching(
                connection, _is_ack_for('req-gesture'))

    ack = _run(scenario())

    assert ack is not None
    assert ack['accepted'] is True
    assert ack['reason'] == 'accepted'


@pytest.mark.parametrize(
    ('message', 'expected_reason'),
    [
        (_request('stop_recording', 'r', {'session_id': '0'}),
         'invalid_session_id'),
        (_request('stop_recording', 'r', {'session_id': '007'}),
         'web_malformed_request'),
        (_request('no_such_type', 'r', {}), 'web_unknown_type'),
        (_request('execute_gesture', 'r',
                  {'gesture_name': 'nope', 'speed_limit': 1.0}),
         'web_malformed_request'),
    ],
)
def test_rejected_requests_carry_a_distinguishable_reason(
    server, message, expected_reason,
):
    """완료조건: 각 허용·거부 요청과 이유가 구분된다."""
    async def scenario():
        async with websockets.connect(URL) as connection:
            await connection.send(message)
            return await _next_matching(connection, _is_ack_for('r'))

    ack = _run(scenario())

    assert ack is not None
    assert ack['accepted'] is False
    assert ack['reason'] == expected_reason


def test_broken_json_is_answered_instead_of_dropping_the_socket(server):
    """FR-23: 형식 오류도 ACK로 알린다. 조용히 끊지 않는다."""
    async def scenario():
        async with websockets.connect(URL) as connection:
            await connection.send('{ this is not json')
            return await _next_matching(
                connection, lambda m: m.get('type') == 'ack')

    ack = _run(scenario())

    assert ack is not None
    assert ack['reason'] == 'web_malformed_request'


# --------------------------------------------------------------------------
# 연결 정책
# --------------------------------------------------------------------------

def test_a_second_connection_is_closed_with_1013(server):
    """탭 두 개면 한쪽 STOP이 다른 쪽 제어권까지 해제하므로 하나만 받는다."""
    async def scenario():
        async with websockets.connect(URL) as first:
            await _next_matching(first, _is_snapshot)
            try:
                async with websockets.connect(URL) as second:
                    await asyncio.wait_for(second.recv(), timeout=TIMEOUT)
                return 'not rejected'
            except Exception as error:  # noqa: BLE001 - close code를 읽는다
                return _close_code(error)

    assert _run(scenario()) == 1013


def test_an_unknown_path_is_closed_with_1008(server):
    """endpoint는 /ws/robot-state 하나뿐이다."""
    async def scenario():
        try:
            async with websockets.connect(
                f'ws://127.0.0.1:{PORT}/nope',
            ) as connection:
                await asyncio.wait_for(connection.recv(), timeout=TIMEOUT)
            return 'not rejected'
        except Exception as error:  # noqa: BLE001 - close code를 읽는다
            return _close_code(error)

    assert _run(scenario()) == 1008


async def _connect_when_slot_frees(attempts=200):
    """
    Connect once the previous session releases the single-connection slot.

    직전 연결을 닫은 직후에는 서버가 핸들러 task를 정리하는 몇 ms 동안 1013을
    돌려준다. 실측 3.5ms이며 브라우저 새로고침 간격에서는 겹치지 않는다.
    테스트가 그 창에 걸려 깜빡이지 않도록 짧게 재시도한다.

    거절이 드러나는 지점이 버전마다 다르다. websockets 10은 handshake를
    성공시키고 첫 recv에서 close를 알리고, 14 이상은 connect에서 바로 던진다.
    그래서 snapshot 한 건을 실제로 받아 본 뒤에야 성공으로 인정한다.
    """
    for _ in range(attempts):
        connection = None
        try:
            connection = await websockets.connect(URL)
            snapshot = await _next_matching(connection, _is_snapshot)
            if snapshot is not None:
                return connection
        except Exception:  # noqa: BLE001 - 슬롯이 아직 안 풀린 경우
            pass
        if connection is not None:
            await connection.close()
        await asyncio.sleep(0.005)
    raise AssertionError('슬롯이 풀리지 않아 재연결하지 못했다')


def test_reconnecting_replays_nothing(server):
    """NFR-15: 재연결은 이전 명령을 재생하지 않는다."""
    async def scenario():
        async with websockets.connect(URL) as first:
            await first.send(_request(
                'execute_gesture', 'req-old',
                {'gesture_name': 'fist', 'speed_limit': 1.0}))
            await _next_matching(first, _is_ack_for('req-old'))

        second = await _connect_when_slot_frees()
        try:
            # 아무 요청도 보내지 않는다. ACK가 오면 재생된 것이다.
            return await _next_matching(
                second, lambda m: m.get('type') == 'ack', limit=8)
        finally:
            await second.close()

    assert _run(scenario()) is None


def test_the_single_connection_slot_frees_after_a_disconnect(server):
    """연결을 닫으면 슬롯이 곧 풀려 재연결이 가능하다."""
    async def scenario():
        async with websockets.connect(URL) as first:
            await _next_matching(first, _is_snapshot)

        # 헬퍼가 snapshot 수신까지 확인하므로 반환되면 재연결이 성립한 것이다.
        second = await _connect_when_slot_frees()
        await second.close()
        return True

    assert _run(scenario()) is True
