"""Exporter worker의 직렬 비동기 실행을 검증한다."""

from threading import Event

import pytest

from thing_logger.export_worker import ExportWorker
from thing_logger.exporter import ExportJob
from thing_logger.exporter import ExportResult


class FakeExporter:
    """호출과 결과를 제어할 수 있는 exporter 테스트 대역이다."""

    def __init__(self):
        """동기화 이벤트와 기본 성공 결과를 생성한다."""
        self.started = Event()
        self.release = Event()
        self.error = None
        self.jobs = []
        self.cleaned = []

    def export(self, job):
        """허용 신호까지 기다린 뒤 성공 결과 또는 오류를 반환한다."""
        self.jobs.append(job)
        self.started.set()
        self.release.wait(timeout=2)
        if self.error is not None:
            raise self.error
        return ExportResult(123, '/tmp/123', 'sha256:test', {})

    def cleanup(self, result):
        """정리 요청을 저장한다."""
        self.cleaned.append(result)


class FakeUploaderClient:
    """인계된 export 결과와 지정 오류를 보관한다."""

    def __init__(self, error=None):
        """기본 호출 목록과 선택 오류를 저장한다."""
        self.error = error
        self.results = []

    def handoff(self, result):
        """결과를 저장한 뒤 지정된 경우 오류를 발생시킨다."""
        self.results.append(result)
        if self.error is not None:
            raise self.error


def wait_for_completed(worker):
    """테스트 제한시간 안에 worker 완료 결과를 반환한다."""
    completed = None
    for _ in range(1000):
        completed = worker.take_completed()
        if completed is not None:
            return completed
        Event().wait(0.001)
    raise AssertionError('export worker did not complete')


def test_export_worker_runs_one_job_asynchronously(tmp_path):
    """제출은 즉시 반환하고 worker가 성공 결과를 별도로 제공한다."""
    bag_path = tmp_path / '123'
    bag_path.mkdir()
    exporter = FakeExporter()
    worker = ExportWorker(exporter)
    job = ExportJob(str(bag_path), 'SUCCESS')

    worker.submit(job)
    assert exporter.started.wait(timeout=1)
    assert worker.is_busy is True
    exporter.release.set()
    completed = wait_for_completed(worker)
    worker.shutdown()

    assert completed.job == job
    assert completed.result.session_id == 123
    assert completed.error is None
    assert worker.is_busy is False


def test_export_worker_queues_overlapping_jobs_in_memory(tmp_path):
    """겹친 완료 세션을 거부하지 않고 메모리에서 순서대로 처리한다."""
    first_path = tmp_path / '123'
    second_path = tmp_path / '124'
    first_path.mkdir()
    second_path.mkdir()
    exporter = FakeExporter()
    worker = ExportWorker(exporter)
    first_job = ExportJob(str(first_path), 'SUCCESS')
    second_job = ExportJob(str(second_path), 'FAILURE')
    worker.submit(first_job)
    assert exporter.started.wait(timeout=1)
    worker.submit(second_job)

    exporter.release.set()
    first_completed = wait_for_completed(worker)
    second_completed = wait_for_completed(worker)
    worker.shutdown()

    assert [first_completed.job, second_completed.job] == [
        first_job, second_job,
    ]
    assert exporter.jobs == [first_job, second_job]
    assert worker.is_busy is False


def test_export_worker_rejects_submit_after_shutdown(tmp_path):
    """종료가 시작된 worker에는 새 메모리 작업을 추가하지 않는다."""
    bag_path = tmp_path / '123'
    bag_path.mkdir()
    exporter = FakeExporter()
    worker = ExportWorker(exporter)
    worker.shutdown()

    with pytest.raises(RuntimeError, match='shutting down'):
        worker.submit(ExportJob(str(bag_path), 'SUCCESS'))


def test_export_worker_reports_export_error(tmp_path):
    """Exporter 예외를 worker 종료 없이 완료 오류로 전달한다."""
    bag_path = tmp_path / '123'
    bag_path.mkdir()
    exporter = FakeExporter()
    exporter.error = RuntimeError('export failed')
    worker = ExportWorker(exporter)
    job = ExportJob(str(bag_path), 'SUCCESS')

    worker.submit(job)
    assert exporter.started.wait(timeout=1)
    exporter.release.set()
    completed = wait_for_completed(worker)
    worker.shutdown()

    assert completed.job == job
    assert completed.result is None
    assert isinstance(completed.error, RuntimeError)
    assert str(completed.error) == 'export failed'


def test_export_worker_hands_result_to_uploader_before_success(tmp_path):
    """Exporter 결과를 uploader에 인계한 뒤에만 성공으로 보고한다."""
    bag_path = tmp_path / '123'
    bag_path.mkdir()
    exporter = FakeExporter()
    uploader = FakeUploaderClient()
    worker = ExportWorker(exporter, uploader)

    worker.submit(ExportJob(str(bag_path), 'SUCCESS'))
    assert exporter.started.wait(timeout=1)
    exporter.release.set()
    completed = wait_for_completed(worker)
    worker.shutdown()

    assert uploader.results == [completed.result]
    assert exporter.cleaned == [completed.result]
    assert completed.error is None


def test_export_worker_reports_uploader_handoff_error(tmp_path):
    """Uploader 인계 실패를 export 완료로 오인하지 않는다."""
    bag_path = tmp_path / '123'
    bag_path.mkdir()
    exporter = FakeExporter()
    uploader = FakeUploaderClient(RuntimeError('handoff failed'))
    worker = ExportWorker(exporter, uploader)

    worker.submit(ExportJob(str(bag_path), 'FAILURE'))
    assert exporter.started.wait(timeout=1)
    exporter.release.set()
    completed = wait_for_completed(worker)
    worker.shutdown()

    assert completed.result is None
    assert isinstance(completed.error, RuntimeError)
    assert str(completed.error) == 'handoff failed'
    assert len(exporter.cleaned) == 1
    assert exporter.cleaned[0].session_id == 123
