import asyncio
import json

from thing_web_bridge.protocol import make_ack
from thing_web_bridge.protocol import SnapshotStore
from thing_web_bridge.websocket_server import WebSocketServer


class FakeWebSocket:
    def __init__(self, incoming=()):
        self._incoming = iter(incoming)
        self.sent = []
        self.closed = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._incoming)
        except StopIteration as error:
            raise StopAsyncIteration from error

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def close(self, code, reason):
        self.closed = (code, reason)


def make_server(handler=lambda request: make_ack(
        request.request_id, True, 'accepted')):
    return WebSocketServer(SnapshotStore(), handler)


def test_valid_request_is_dispatched_and_acknowledged(monkeypatch):
    async def call_inline(function, *args):
        return function(*args)

    monkeypatch.setattr(asyncio, 'to_thread', call_inline)
    websocket = FakeWebSocket([json.dumps({
        'request_id': 'req-1',
        'type': 'reset_safety',
        'timestamp': '2026-08-04T12:00:00Z',
        'payload': {},
    })])

    asyncio.run(make_server()._consume_requests(websocket))

    assert websocket.sent[0]['request_id'] == 'req-1'
    assert websocket.sent[0]['type'] == 'ack'
    assert websocket.sent[0]['accepted'] is True


def test_malformed_json_is_rejected_without_dispatch():
    websocket = FakeWebSocket(['{'])

    asyncio.run(make_server()._consume_requests(websocket))

    assert websocket.sent[0]['accepted'] is False
    assert websocket.sent[0]['reason'] == 'web_malformed_request'


def test_unknown_request_keeps_request_id_in_rejection():
    websocket = FakeWebSocket([json.dumps({
        'request_id': 'req-unknown',
        'type': 'delete_robot',
        'timestamp': '2026-08-04T12:00:00Z',
        'payload': {},
    })])

    asyncio.run(make_server()._consume_requests(websocket))

    assert websocket.sent[0]['request_id'] == 'req-unknown'
    assert websocket.sent[0]['accepted'] is False


def test_only_configured_endpoint_is_accepted():
    server = make_server()
    websocket = FakeWebSocket()

    asyncio.run(server._client_connected(websocket, '/wrong'))

    assert websocket.closed == (1008, 'endpoint not allowed')


def test_query_string_does_not_change_endpoint_match():
    server = make_server()
    websocket = FakeWebSocket()

    assert server._client_path(
        websocket, '/ws/robot-state?token=ignored'
    ) == '/ws/robot-state'
