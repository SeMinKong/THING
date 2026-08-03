"""Logger 내부 rosbag2 worker Queue 동작을 검증한다."""

import pytest

from thing_logger.bag_recorder import BagRecorderError
from thing_logger.recording_worker import RecordingWorker


class FakeRecorder:
    """처리된 작업 순서를 저장하는 테스트용 recorder."""

    def __init__(self):
        self.operations = []
        self.is_recording = False
        self.bag_path = None
        self.fail_write = False

    def start(self, bag_path):
        self.operations.append(('start', bag_path))
        self.is_recording = True
        self.bag_path = bag_path

    def write(self, topic_name, message, timestamp_ns):
        self.operations.append(
            ('write', topic_name, message, timestamp_ns)
        )
        if self.fail_write:
            raise BagRecorderError('write failed')

    def stop(self):
        self.operations.append(('stop',))
        self.is_recording = False
        closed_path = self.bag_path
        self.bag_path = None
        return closed_path

    def interrupt(self):
        self.operations.append(('interrupt',))
        self.is_recording = False
        closed_path = self.bag_path
        self.bag_path = None
        return closed_path


def test_worker_processes_writes_before_stop():
    """Stop은 앞서 Queue에 들어간 write가 끝난 뒤 실행된다."""
    recorder = FakeRecorder()
    worker = RecordingWorker(recorder)

    worker.start('/tmp/session')
    worker.write('/thing/command', 'first', 100)
    worker.write('/thing/motor_status', 'second', 200)
    assert worker.stop() == '/tmp/session'
    worker.shutdown()

    assert recorder.operations == [
        ('start', '/tmp/session'),
        ('write', '/thing/command', 'first', 100),
        ('write', '/thing/motor_status', 'second', 200),
        ('stop',),
    ]


def test_worker_reports_asynchronous_write_error():
    """비동기 write 오류를 Logger가 회수할 수 있다."""
    recorder = FakeRecorder()
    recorder.fail_write = True
    worker = RecordingWorker(recorder)

    worker.start('/tmp/session')
    worker.write('/thing/command', 'message', 100)
    worker._queue.join()

    error = worker.take_async_error()
    assert isinstance(error, BagRecorderError)
    assert str(error) == 'write failed'
    assert worker.take_async_error() is None

    worker.interrupt()
    worker.shutdown()


def test_stop_reports_error_from_earlier_queued_write():
    """Stop barrier는 앞선 비동기 write 실패를 성공으로 숨기지 않는다."""
    recorder = FakeRecorder()
    recorder.fail_write = True
    worker = RecordingWorker(recorder)

    worker.start('/tmp/session')
    worker.write('/thing/command', 'message', 100)

    with pytest.raises(BagRecorderError, match='write failed'):
        worker.stop()

    assert recorder.is_recording is True
    worker.interrupt()
    worker.shutdown()
