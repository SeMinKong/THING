"""Async WebSocket transport isolated from the ROS 2 executor thread."""

import asyncio
from collections import deque
import json
import logging
from threading import Event, Lock, Thread
from typing import Any, Callable, Deque, Dict, Optional, Tuple

from thing_web_bridge.protocol import BridgeRequest
from thing_web_bridge.protocol import make_ack
from thing_web_bridge.protocol import parse_request
from thing_web_bridge.protocol import ProtocolError
from thing_web_bridge.protocol import SnapshotStore


RequestHandler = Callable[[Any], Dict[str, Any]]

# rclpy 로거는 노드 소유라 이 계층에서는 표준 logging을 쓴다. 기본 설정으로도
# WARNING 이상은 stderr에 남아 launch 로그에서 볼 수 있다.
LOGGER = logging.getLogger('thing_web_bridge.websocket_server')

# FR-19·FR-31: STOP과 안전 전이는 일반 동작을 항상 선점한다. 일반 요청 하나를
# 처리하는 동안 다음 메시지를 읽지 않으면 STOP이 긴급 요청인지 확인조차 못 하고
# 대기열 맨 뒤에서 기다린다. 그래서 수신과 실행을 나누고 이 두 type만 대기열을
# 건너뛴다.
URGENT_REQUEST_TYPES = frozenset({'stop', 'reset_safety'})

# STOP이 아직 실행하지 않은 일반 요청을 폐기할 때 돌려줄 사유. 조용히 버리면
# 내부 제어 웹이 ack를 기다리며 버튼을 잠근 채로 남는다.
REASON_PREEMPTED_BY_STOP = 'web_preempted_by_stop'
# 같은 mode·owner lease 갱신이 더 새 요청으로 교체됐을 때의 사유.
REASON_SUPERSEDED = 'web_superseded'
# 대기열 상한 초과.
REASON_QUEUE_OVERFLOW = 'web_queue_overflow'
# 두 번째 브라우저 연결을 거절할 때 쓰는 close reason.
REASON_SINGLE_CONNECTION = 'single connection only'


def _dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))


class ClientSession:
    """
    Serve one browser: read fast, run general requests in order, preempt.

    수신(reader)·일반 실행(worker)·snapshot 발행(producer)을 각각 독립 task로
    두고, 모든 outbound 전송은 하나의 lock으로 직렬화한다.
    """

    def __init__(
        self,
        websocket: Any,
        snapshot_store: SnapshotStore,
        request_handler: RequestHandler,
        snapshot_period: float,
        max_pending: int = 32,
    ) -> None:
        """Bind one connection to the shared store and ROS request handler."""
        self._websocket = websocket
        self._snapshot_store = snapshot_store
        self._request_handler = request_handler
        self._snapshot_period = snapshot_period
        self._max_pending = int(max_pending)
        self._send_lock = asyncio.Lock()
        self._pending: Deque[BridgeRequest] = deque()
        self._pending_event = asyncio.Event()
        self._urgent_tasks: set = set()

    async def _send(self, payload: Dict[str, Any]) -> None:
        """
        Send one JSON object, serialized against every other sender.

        websockets는 여러 coroutine이 동시에 send()를 호출하는 것을 지원하지
        않는다. snapshot producer와 ACK 경로가 이제 진짜로 겹치므로 lock이
        필요하다.
        """
        async with self._send_lock:
            await self._websocket.send(_dumps(payload))

    async def _ack(self, request_id: str, reason: str) -> None:
        await self._send(make_ack(request_id, False, reason))

    async def _run_handler(self, request: BridgeRequest) -> None:
        """Call the blocking ROS handler off the event loop and reply once."""
        try:
            response = await asyncio.to_thread(self._request_handler, request)
        except asyncio.CancelledError:
            raise
        except Exception:
            response = make_ack(
                request.request_id, False, 'web_bridge_error')
        await self._send(response)

    def _drain_pending(self) -> list:
        """Take every not-yet-started general request out of the queue."""
        dropped = list(self._pending)
        self._pending.clear()
        return dropped

    async def _enqueue(self, request: BridgeRequest) -> None:
        """
        Queue one general request, collapsing duplicate lease renewals.

        내부 제어 웹은 1000ms마다 같은 mode·owner로 set_control_mode를 보낸다
        (FR-34 갱신). 처리 중 대기열에 같은 갱신이 쌓이면 뒤로 갈수록 밀리므로
        대기 중 갱신은 최신 하나만 남긴다. 전부 버리지는 않는다. 처리 중인
        요청이 timeout되면 실제 갱신이 끊겨 3000ms 뒤 lease가 만료된다.
        """
        if request.type == 'set_control_mode':
            for index, queued in enumerate(self._pending):
                if (
                    queued.type == 'set_control_mode'
                    and queued.payload == request.payload
                ):
                    self._pending[index] = request
                    self._pending_event.set()
                    await self._ack(queued.request_id, REASON_SUPERSEDED)
                    return
        if len(self._pending) >= self._max_pending:
            await self._ack(request.request_id, REASON_QUEUE_OVERFLOW)
            return
        self._pending.append(request)
        self._pending_event.set()

    def _spawn_urgent(self, request: BridgeRequest) -> None:
        task = asyncio.ensure_future(self._run_handler(request))
        self._urgent_tasks.add(task)
        task.add_done_callback(self._urgent_tasks.discard)

    async def handle_message(self, raw_message: Any) -> None:
        """Classify one inbound message without waiting for ROS."""
        request_id = ''
        try:
            message = json.loads(raw_message)
            if isinstance(message, dict):
                raw_request_id = message.get('request_id')
                if isinstance(raw_request_id, str):
                    request_id = raw_request_id
            request = parse_request(message)
        except (json.JSONDecodeError, UnicodeDecodeError):
            await self._ack(request_id, 'web_malformed_request')
            return
        except ProtocolError as error:
            await self._ack(request_id, error.reason)
            return

        if request.type not in URGENT_REQUEST_TYPES:
            await self._enqueue(request)
            return

        if request.type == 'stop':
            # FR-31: STOP은 선택·최종 큐를 함께 폐기한다. 이미 ROS로 나간
            # in-flight 요청은 취소할 수 없으므로 아직 시작하지 않은 것만
            # 버리고, 버린 각 요청에도 같은 request_id로 ACK를 돌려준다.
            for dropped in self._drain_pending():
                await self._ack(dropped.request_id, REASON_PREEMPTED_BY_STOP)
        self._spawn_urgent(request)

    async def _read_loop(self) -> None:
        async for raw_message in self._websocket:
            await self.handle_message(raw_message)

    async def _worker_loop(self) -> None:
        while True:
            if not self._pending:
                self._pending_event.clear()
                await self._pending_event.wait()
                continue
            await self._run_handler(self._pending.popleft())

    async def _publish_snapshots(self) -> None:
        while True:
            await self._send(self._snapshot_store.snapshot())
            await asyncio.sleep(self._snapshot_period)

    async def run(self) -> None:
        """Run reader, worker, and snapshot producer until the client leaves."""
        producer = asyncio.ensure_future(self._publish_snapshots())
        worker = asyncio.ensure_future(self._worker_loop())
        reader = asyncio.ensure_future(self._read_loop())
        tasks = (producer, worker, reader)
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                error = task.exception()
                if error is not None:
                    # 세션이 왜 끝났는지 기록이 없으면 통합 시험에서 원인
                    # 추적이 불가능하다. 정상 종료(reader의 close)는 예외
                    # 없이 끝나므로 여기 걸리는 것은 비정상 절단이나 버그다.
                    LOGGER.warning(
                        'client session task ended with %r', error)
        finally:
            for task in (*tasks, *tuple(self._urgent_tasks)):
                task.cancel()
            await asyncio.gather(
                *tasks, *tuple(self._urgent_tasks), return_exceptions=True)


class WebSocketServer:
    """Serve latest-only snapshots and validated browser requests."""

    def __init__(
        self,
        snapshot_store: SnapshotStore,
        request_handler: RequestHandler,
        host: str = '0.0.0.0',
        port: int = 8000,
        path: str = '/ws/robot-state',
        snapshot_period: float = 0.2,
        max_pending: int = 32,
    ) -> None:
        """Store server configuration without opening a socket."""
        if not host:
            raise ValueError('host must not be empty')
        if not 1 <= int(port) <= 65535:
            raise ValueError('port must be in the range 1..65535')
        if not path.startswith('/'):
            raise ValueError('path must start with /')
        if snapshot_period <= 0.0:
            raise ValueError('snapshot_period must be positive')
        if int(max_pending) <= 0:
            raise ValueError('max_pending must be positive')
        self._snapshot_store = snapshot_store
        self._request_handler = request_handler
        self._host = host
        self._port = int(port)
        self._path = path
        self._snapshot_period = float(snapshot_period)
        self._max_pending = int(max_pending)
        self._thread: Optional[Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._ready = Event()
        self._exception: Optional[BaseException] = None
        self._lifecycle_lock = Lock()
        # 탭을 두 개 열면 한쪽 STOP이 다른 쪽 제어권까지 해제한다. 내부 제어
        # 웹에서는 해결할 수 없어 브리지가 연결 하나만 허용한다.
        self._client_active = False

    @property
    def address(self) -> Tuple[str, int]:
        """Return the configured bind address."""
        return self._host, self._port

    @property
    def path(self) -> str:
        """Return the one accepted WebSocket endpoint."""
        return self._path

    def start(self, timeout: float = 5.0) -> None:
        """Start the transport thread and wait for its listening socket."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._exception = None
            self._thread = Thread(
                target=self._run,
                name='thing-websocket-server',
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError('WebSocket server start timed out')
        if self._exception is not None:
            raise RuntimeError('WebSocket server failed to start') from (
                self._exception)

    def stop(self, timeout: float = 5.0) -> None:
        """Stop accepting clients and join the transport thread."""
        with self._lifecycle_lock:
            thread = self._thread
            loop = self._loop
            stop_event = self._stop_event
            if loop is not None and stop_event is not None:
                loop.call_soon_threadsafe(stop_event.set)
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise RuntimeError('WebSocket server stop timed out')
        with self._lifecycle_lock:
            self._thread = None
            self._loop = None
            self._stop_event = None

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as error:  # transport startup errors cross threads
            self._exception = error
            self._ready.set()

    async def _serve(self) -> None:
        try:
            from websockets.server import serve
        except ImportError as error:
            raise RuntimeError(
                'python3-websockets is required for web_bridge_node',
            ) from error

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        async with serve(self._client_connected, self._host, self._port):
            self._ready.set()
            await self._stop_event.wait()

    def _client_path(self, websocket: Any, handler_path: Any) -> str:
        if isinstance(handler_path, str):
            return handler_path.split('?', maxsplit=1)[0]
        request = getattr(websocket, 'request', None)
        path = getattr(request, 'path', None)
        if not isinstance(path, str):
            path = getattr(websocket, 'path', '')
        return str(path).split('?', maxsplit=1)[0]

    def new_session(self, websocket: Any) -> ClientSession:
        """Build one session; kept separate so tests can drive it directly."""
        return ClientSession(
            websocket=websocket,
            snapshot_store=self._snapshot_store,
            request_handler=self._request_handler,
            snapshot_period=self._snapshot_period,
            max_pending=self._max_pending,
        )

    async def _client_connected(
        self,
        websocket: Any,
        handler_path: Any = None,
    ) -> None:
        path = self._client_path(websocket, handler_path)
        if path != self._path:
            await websocket.close(code=1008, reason='endpoint not allowed')
            return
        if self._client_active:
            await websocket.close(code=1013, reason=REASON_SINGLE_CONNECTION)
            return
        self._client_active = True
        try:
            await self.new_session(websocket).run()
        finally:
            self._client_active = False
