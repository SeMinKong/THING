"""Async WebSocket transport isolated from the ROS 2 executor thread."""

import asyncio
import json
from threading import Event, Lock, Thread
from typing import Any, Callable, Dict, Optional

from thing_web_bridge.protocol import make_ack
from thing_web_bridge.protocol import parse_request
from thing_web_bridge.protocol import ProtocolError
from thing_web_bridge.protocol import SnapshotStore


RequestHandler = Callable[[Any], Dict[str, Any]]


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
        self._snapshot_store = snapshot_store
        self._request_handler = request_handler
        self._host = host
        self._port = int(port)
        self._path = path
        self._snapshot_period = float(snapshot_period)
        self._thread: Optional[Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._ready = Event()
        self._exception: Optional[BaseException] = None
        self._lifecycle_lock = Lock()

    @property
    def address(self) -> tuple[str, int]:
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

    async def _client_connected(
        self,
        websocket: Any,
        handler_path: Any = None,
    ) -> None:
        path = self._client_path(websocket, handler_path)
        if path != self._path:
            await websocket.close(code=1008, reason='endpoint not allowed')
            return

        producer = asyncio.create_task(self._publish_snapshots(websocket))
        consumer = asyncio.create_task(self._consume_requests(websocket))
        done, pending = await asyncio.wait(
            (producer, consumer),
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.exception()

    async def _publish_snapshots(self, websocket: Any) -> None:
        while True:
            snapshot = self._snapshot_store.snapshot()
            await websocket.send(
                json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')),
            )
            await asyncio.sleep(self._snapshot_period)

    async def _consume_requests(self, websocket: Any) -> None:
        async for raw_message in websocket:
            request_id = ''
            try:
                message = json.loads(raw_message)
                if isinstance(message, dict):
                    raw_request_id = message.get('request_id')
                    if isinstance(raw_request_id, str):
                        request_id = raw_request_id
                request = parse_request(message)
                response = await asyncio.to_thread(
                    self._request_handler,
                    request,
                )
            except (json.JSONDecodeError, UnicodeDecodeError):
                response = make_ack(
                    request_id,
                    False,
                    'web_malformed_request',
                )
            except ProtocolError as error:
                response = make_ack(request_id, False, error.reason)
            except Exception:
                response = make_ack(request_id, False, 'web_bridge_error')
            await websocket.send(
                json.dumps(response, ensure_ascii=False, separators=(',', ':')),
            )
