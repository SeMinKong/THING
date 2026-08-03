"""세션 export를 ROS callback과 분리하는 단일 worker."""

from dataclasses import dataclass
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Optional

from thing_logger.exporter import ExportJob
from thing_logger.exporter import ExportResult


class ExportBusyError(RuntimeError):
    """이전 세션 export가 아직 끝나지 않았음을 나타낸다."""


@dataclass(frozen=True)
class CompletedExport:
    """worker가 완료한 export 결과 또는 오류를 나타낸다."""

    job: ExportJob
    result: Optional[ExportResult] = None
    error: Optional[BaseException] = None


class ExportWorker:
    """한 번에 하나의 완료 세션만 비동기로 export한다."""

    _SHUTDOWN = object()

    def __init__(self, exporter, uploader_client=None) -> None:
        """주입된 exporter를 실행하는 단일 worker thread를 시작한다."""
        self._exporter = exporter
        self._uploader_client = uploader_client
        self._tasks = Queue(maxsize=1)
        self._completed = Queue(maxsize=1)
        self._state_lock = Lock()
        self._busy = False
        self._thread = Thread(
            target=self._run,
            name='thing-logger-exporter',
            daemon=True,
        )
        self._thread.start()

    @property
    def is_busy(self) -> bool:
        """대기·실행·결과 회수 중인 export가 있는지 반환한다."""
        with self._state_lock:
            return self._busy

    def submit(self, job: ExportJob) -> None:
        """빈 worker에 export 작업을 넣고 즉시 반환한다."""
        with self._state_lock:
            if self._busy:
                raise ExportBusyError('an export is already in progress')
            self._busy = True
        self._tasks.put_nowait(job)

    def take_completed(self) -> Optional[CompletedExport]:
        """완료 결과를 한 번 반환하고 worker를 다음 작업에 개방한다."""
        try:
            completed = self._completed.get_nowait()
        except Empty:
            return None

        self._completed.task_done()
        with self._state_lock:
            self._busy = False
        return completed

    def shutdown(self) -> None:
        """진행 중 작업을 마친 뒤 worker thread를 종료한다."""
        if not self._thread.is_alive():
            return
        self._tasks.put(self._SHUTDOWN)
        self._thread.join()

    def _run(self) -> None:
        """Queue 작업을 순서대로 처리하고 완료 결과를 보관한다."""
        while True:
            task = self._tasks.get()
            try:
                if task is self._SHUTDOWN:
                    return
                try:
                    result = self._exporter.export(task)
                    if self._uploader_client is not None:
                        self._uploader_client.handoff(result)
                    completed = CompletedExport(task, result=result)
                except Exception as error:
                    completed = CompletedExport(task, error=error)
                self._completed.put(completed)
            finally:
                self._tasks.task_done()
