"""rosbag2 I/O를 ROS callback과 분리하는 전용 worker."""

from dataclasses import dataclass
from queue import Queue
from threading import Event, Lock, Thread
from typing import Any, Optional

from thing_logger.bag_recorder import BagRecorder
from thing_logger.bag_recorder import BagRecorderError


@dataclass
class _Task:
    """worker가 순서대로 처리할 하나의 rosbag2 작업."""

    operation: str
    arguments: tuple = ()
    completed: Optional[Event] = None
    result: Any = None
    error: Optional[BaseException] = None


class RecordingWorker:
    """단일 Queue에서 rosbag2 작업 순서를 보장한다."""

    def __init__(self, recorder: Optional[BagRecorder] = None) -> None:
        self._recorder = recorder or BagRecorder()
        self._queue = Queue()
        self._error_lock = Lock()
        self._async_error: Optional[BagRecorderError] = None
        self._thread = Thread(
            target=self._run,
            name='thing-logger-bag-writer',
            daemon=True,
        )
        self._thread.start()

    @property
    def is_recording(self) -> bool:
        return self._recorder.is_recording

    @property
    def bag_path(self) -> Optional[str]:
        return self._recorder.bag_path

    def start(self, bag_path: str) -> None:
        """Writer 시작 완료를 기다린 뒤 반환한다."""
        self.take_async_error()
        self._submit_and_wait('start', bag_path)

    def write(self, topic_name: str, message, timestamp_ns: int) -> None:
        """메시지를 기록 Queue에 넣고 callback에는 즉시 반환한다."""
        self._raise_async_error()
        self._queue.put(
            _Task('write', (topic_name, message, timestamp_ns))
        )

    def stop(self) -> str:
        """앞선 write를 모두 처리한 뒤 정상 종료한다."""
        self._raise_async_error()
        return self._submit_and_wait('stop')

    def interrupt(self) -> str:
        """앞선 작업 뒤 writer를 닫고 중단 bag을 삭제한다."""
        try:
            return self._submit_and_wait('interrupt')
        finally:
            self.take_async_error()

    def take_async_error(self) -> Optional[BagRecorderError]:
        """worker에서 발생한 비동기 write 오류를 한 번 반환한다."""
        with self._error_lock:
            error = self._async_error
            self._async_error = None
            return error

    def shutdown(self) -> None:
        """대기 중 작업을 마치고 worker thread를 종료한다."""
        if not self._thread.is_alive():
            return
        self._submit_and_wait('shutdown')
        self._thread.join()

    def _submit_and_wait(self, operation: str, *arguments):
        completed = Event()
        task = _Task(operation, arguments, completed)
        self._queue.put(task)
        completed.wait()
        if task.error is not None:
            raise task.error
        return task.result

    def _raise_async_error(self) -> None:
        error = self.take_async_error()
        if error is not None:
            raise error

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            try:
                if task.operation == 'shutdown':
                    return
                if task.operation == 'stop':
                    self._raise_async_error()
                method = getattr(self._recorder, task.operation)
                task.result = method(*task.arguments)
            except BaseException as error:
                task.error = error
                if task.operation == 'write':
                    wrapped_error = error
                    if not isinstance(error, BagRecorderError):
                        wrapped_error = BagRecorderError(str(error))
                    with self._error_lock:
                        if self._async_error is None:
                            self._async_error = wrapped_error
            finally:
                if task.completed is not None:
                    task.completed.set()
                self._queue.task_done()
