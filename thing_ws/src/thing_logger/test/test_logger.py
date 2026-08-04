"""Logger의 서비스와 안전 복구 흐름을 검증한다."""

from pathlib import Path
from types import SimpleNamespace

from builtin_interfaces.msg import Time

from thing_interfaces.msg import ControlState
from thing_interfaces.msg import RecordingState
from thing_interfaces.msg import SafetyState
from thing_logger.bag_recorder import BagRecorder
from thing_logger.export_worker import CompletedExport
from thing_logger.exporter import ExportResult
from thing_logger.logger import Logger
from thing_logger.session import SessionManager


class FakeNow:
    """테스트에서 고정된 ROS 시각을 제공한다."""

    nanoseconds = 100

    def to_msg(self):
        """고정 시각을 builtin_interfaces/Time으로 변환한다."""
        return Time(sec=0, nanosec=100)


class FakeClock:
    """항상 같은 시각을 반환하는 테스트 시계다."""

    def now(self):
        """고정 시각 객체를 반환한다."""
        return FakeNow()


class FakePublisher:
    """발행된 RecordingState를 메모리에 보관한다."""

    def __init__(self):
        """빈 발행 목록을 만든다."""
        self.messages = []

    def publish(self, message):
        """발행된 메시지를 저장한다."""
        self.messages.append(message)


class FakeRosLogger:
    """테스트 중 발생한 오류 로그를 수집한다."""

    def __init__(self):
        """빈 오류 목록을 만든다."""
        self.errors = []
        self.infos = []

    def error(self, message):
        """오류 메시지를 저장한다."""
        self.errors.append(message)

    def info(self, message):
        """정보 메시지를 저장한다."""
        self.infos.append(message)


class FakeExportWorker:
    """Logger가 제출한 ExportJob과 완료 결과를 보관한다."""

    def __init__(self):
        """비활성 worker와 빈 작업 목록을 만든다."""
        self.is_busy = False
        self.jobs = []
        self.completed = None

    def submit(self, job):
        """제출된 작업을 저장하고 busy 상태로 전환한다."""
        self.jobs.append(job)
        self.is_busy = True

    def take_completed(self):
        """준비된 완료 결과를 한 번 반환한다."""
        completed = self.completed
        self.completed = None
        if completed is not None:
            self.is_busy = False
        return completed


class LoggerHarness:
    """ROS 노드 생성 없이 Logger 공개 동작을 실행한다."""

    def __init__(self, tmp_path):
        """실제 세션 관리자와 BagRecorder를 임시 경로에 연결한다."""
        self.session_manager = SessionManager(
            tmp_path,
            session_id_factory=lambda: 123,
        )
        self.bag_recorder = BagRecorder()
        self.export_worker = FakeExportWorker()
        self.active_mode = ControlState.MODE_DISABLED
        self.safety_state = SafetyState.INIT
        self.recording_state_publisher = FakePublisher()
        self.clock = FakeClock()
        self.ros_logger = FakeRosLogger()

    def get_clock(self):
        """테스트 시계를 반환한다."""
        return self.clock

    def get_logger(self):
        """테스트 로그 수집기를 반환한다."""
        return self.ros_logger

    def publish_recording_state(self):
        """실제 Logger 상태 발행 메서드를 실행한다."""
        return Logger.publish_recording_state(self)

    def write_message(self, topic_name, message):
        """실제 Logger 메시지 기록 메서드를 실행한다."""
        return Logger.write_message(self, topic_name, message)

    def interrupt_recording(self, message):
        """실제 Logger 중단 메서드를 실행한다."""
        return Logger.interrupt_recording(self, message)


def make_response():
    """서비스 응답 필드를 자유롭게 저장하는 객체를 만든다."""
    return SimpleNamespace()


def start_recording(logger):
    """테스트 Logger를 안전한 MIMIC 상태에서 녹화시킨다."""
    logger.active_mode = ControlState.MODE_MIMIC
    logger.safety_state = SafetyState.READY
    request = SimpleNamespace(label='test')
    response = make_response()

    Logger.handle_start_recording(logger, request, response)
    return response


def test_start_requires_safe_mimic_state(tmp_path):
    """MIMIC이더라도 INIT에서는 녹화를 시작하지 않는다."""
    logger = LoggerHarness(tmp_path)
    logger.active_mode = ControlState.MODE_MIMIC

    response = make_response()
    Logger.handle_start_recording(
        logger,
        SimpleNamespace(label='test'),
        response,
    )

    assert response.accepted is False
    assert response.reason == 'start_failed'
    assert logger.session_manager.state == RecordingState.IDLE
    assert logger.bag_recorder.is_recording is False


def test_normal_stop_preserves_bag_and_waits_for_result(tmp_path):
    """정상 Stop은 bag을 보존하고 결과 판정 뒤 IDLE로 돌아간다."""
    logger = LoggerHarness(tmp_path)
    start_response = start_recording(logger)
    bag_path = Path(start_response.bag_path)

    assert start_response.accepted is True
    assert logger.session_manager.state == RecordingState.RECORDING
    assert bag_path.exists()

    stop_response = make_response()
    Logger.handle_stop_recording(
        logger,
        SimpleNamespace(session_id=start_response.session_id),
        stop_response,
    )

    assert stop_response.accepted is True
    assert logger.session_manager.state == RecordingState.COMPLETED
    assert logger.session_manager.result_pending is True
    assert bag_path.exists()

    result_response = make_response()
    Logger.handle_set_mimic_result(
        logger,
        SimpleNamespace(
            session_id=start_response.session_id,
            result=RecordingState.RESULT_SUCCESS,
        ),
        result_response,
    )

    assert result_response.accepted is True
    assert logger.session_manager.state == RecordingState.IDLE
    assert logger.session_manager.result_pending is False
    assert len(logger.export_worker.jobs) == 1
    assert logger.export_worker.jobs[0].bag_path == str(bag_path)
    assert logger.export_worker.jobs[0].result == 'SUCCESS'


def test_interrupted_bag_is_deleted_and_ready_restores_idle(tmp_path):
    """안전 중단은 bag을 삭제하고 실제 READY에서만 IDLE로 복구한다."""
    logger = LoggerHarness(tmp_path)
    start_response = start_recording(logger)
    bag_path = Path(start_response.bag_path)

    safety_message = SafetyState()
    safety_message.state = SafetyState.SAFE
    Logger.handle_safety_state(logger, safety_message)

    assert logger.session_manager.state == RecordingState.INTERRUPTED
    assert logger.session_manager.result_pending is False
    assert bag_path.exists() is False

    safety_message.state = SafetyState.INIT
    Logger.handle_safety_state(logger, safety_message)
    assert logger.session_manager.state == RecordingState.INTERRUPTED

    safety_message.state = SafetyState.READY
    Logger.handle_safety_state(logger, safety_message)
    assert logger.session_manager.state == RecordingState.IDLE
    assert logger.session_manager.result_pending is False


def test_start_is_allowed_while_previous_export_is_busy(tmp_path):
    """판정이 끝났다면 이전 export 중에도 새 기록을 시작한다."""
    logger = LoggerHarness(tmp_path)
    logger.active_mode = ControlState.MODE_MIMIC
    logger.safety_state = SafetyState.READY
    logger.export_worker.is_busy = True
    response = make_response()

    Logger.handle_start_recording(
        logger,
        SimpleNamespace(label='test'),
        response,
    )

    assert response.accepted is True
    assert response.reason == ''
    assert logger.session_manager.state == RecordingState.RECORDING
    assert logger.bag_recorder.is_recording is True

    logger.interrupt_recording('test cleanup')


def test_export_completion_and_failure_are_logged(tmp_path):
    """변환 성공과 실패를 회수해 각각 진단 로그에 남긴다."""
    logger = LoggerHarness(tmp_path)
    result = ExportResult(123, '/tmp/123', 'sha256:test', {})
    logger.export_worker.completed = CompletedExport(
        SimpleNamespace(),
        result=result,
    )

    Logger.handle_export_worker_result(logger)

    assert logger.ros_logger.infos == [
        'export completed: session_id=123 content_digest=sha256:test'
    ]

    logger.export_worker.completed = CompletedExport(
        SimpleNamespace(),
        error=RuntimeError('broken bag'),
    )
    Logger.handle_export_worker_result(logger)

    assert logger.ros_logger.errors == ['export failed: broken bag']
