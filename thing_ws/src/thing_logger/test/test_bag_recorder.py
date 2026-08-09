"""rosbag2 기록 모듈의 공개 동작을 검증한다."""

import rosbag2_py
import pytest

from thing_interfaces.msg import RecordingState
from thing_logger.bag_recorder import BagRecorder
from thing_logger.bag_recorder import BagRecorderError
from thing_logger.bag_recorder import TOPIC_TYPES


def read_bag(bag_path):
    """생성된 rosbag2의 토픽 정보와 첫 메시지를 읽는다."""
    reader = rosbag2_py.SequentialReader()
    storage_options = rosbag2_py.StorageOptions(
        uri=str(bag_path),
        storage_id='sqlite3',
    )
    converter_options = rosbag2_py.ConverterOptions(
        input_serialization_format='cdr',
        output_serialization_format='cdr',
    )
    reader.open(storage_options, converter_options)

    topic_types = {
        topic.name: topic.type
        for topic in reader.get_all_topics_and_types()
    }
    first_message = reader.read_next() if reader.has_next() else None
    return topic_types, first_message


def test_initial_state_is_not_recording():
    """새 기록기는 열린 writer와 bag 경로를 가지지 않는다."""
    recorder = BagRecorder()

    assert recorder.is_recording is False
    assert recorder.bag_path is None


def test_required_topics_exclude_camera_image():
    """필수 여섯 토픽만 등록하고 카메라 원본은 제외한다."""
    assert TOPIC_TYPES == {
        '/thing/landmarks': 'thing_interfaces/msg/HandLandmarks',
        '/thing/command': 'thing_interfaces/msg/HandCommand',
        '/thing/motor_status': 'thing_interfaces/msg/MotorStatus',
        '/thing/control_state': 'thing_interfaces/msg/ControlState',
        '/thing/safety_state': 'thing_interfaces/msg/SafetyState',
        '/thing/recording_state': 'thing_interfaces/msg/RecordingState',
    }
    assert '/thing/image_raw' not in TOPIC_TYPES


def test_start_write_and_stop_preserve_readable_bag(tmp_path):
    """정상 Stop은 필수 토픽과 메시지가 든 bag을 보존한다."""
    bag_path = tmp_path / 'normal_session'
    recorder = BagRecorder()
    message = RecordingState()
    message.state = RecordingState.RECORDING

    recorder.start(str(bag_path))
    recorder.write('/thing/recording_state', message, 100)
    closed_path = recorder.stop()

    assert closed_path == str(bag_path)
    assert recorder.is_recording is False
    assert recorder.bag_path is None
    assert bag_path.exists()

    topic_types, first_message = read_bag(bag_path)
    assert topic_types == TOPIC_TYPES
    assert first_message is not None
    assert first_message[0] == '/thing/recording_state'
    assert first_message[2] == 100


def test_interrupt_deletes_incomplete_bag(tmp_path):
    """녹화 중 interrupt는 writer를 닫고 해당 bag을 삭제한다."""
    bag_path = tmp_path / 'interrupted_session'
    recorder = BagRecorder()

    recorder.start(str(bag_path))
    interrupted_path = recorder.interrupt()

    assert interrupted_path == str(bag_path)
    assert recorder.is_recording is False
    assert recorder.bag_path is None
    assert bag_path.exists() is False


def test_start_rejects_empty_existing_and_duplicate_paths(tmp_path):
    """빈 경로·기존 경로·중복 Start를 모두 거부한다."""
    recorder = BagRecorder()

    with pytest.raises(BagRecorderError):
        recorder.start('')

    existing_path = tmp_path / 'existing'
    existing_path.mkdir()
    with pytest.raises(BagRecorderError):
        recorder.start(str(existing_path))

    active_path = tmp_path / 'active'
    recorder.start(str(active_path))
    with pytest.raises(BagRecorderError):
        recorder.start(str(tmp_path / 'another'))

    recorder.interrupt()


def test_write_rejects_invalid_recording_requests(tmp_path):
    """비활성 writer·미등록 토픽·음수 timestamp 기록을 거부한다."""
    recorder = BagRecorder()
    message = RecordingState()

    with pytest.raises(BagRecorderError):
        recorder.write('/thing/recording_state', message, 100)

    recorder.start(str(tmp_path / 'active'))

    with pytest.raises(BagRecorderError):
        recorder.write('/thing/image_raw', message, 100)

    with pytest.raises(BagRecorderError):
        recorder.write('/thing/recording_state', message, -1)

    recorder.interrupt()


def test_topic_registration_failure_removes_partial_bag(
    tmp_path,
    monkeypatch,
):
    """필수 토픽 등록 실패 시 불완전한 bag과 기록 상태를 정리한다."""
    bag_path = tmp_path / 'failed_start'
    recorder = BagRecorder()

    def fail_registration(writer):
        """테스트를 위해 토픽 등록 실패를 발생시킨다."""
        raise RuntimeError('topic registration failed')

    monkeypatch.setattr(
        recorder,
        '_register_topics',
        fail_registration,
    )

    with pytest.raises(BagRecorderError):
        recorder.start(str(bag_path))

    assert recorder.is_recording is False
    assert recorder.bag_path is None
    assert bag_path.exists() is False


def test_stop_close_failure_removes_incomplete_bag(tmp_path, monkeypatch):
    """정상 Stop의 close 실패도 불완전한 bag 삭제를 시도한다."""
    bag_path = tmp_path / 'failed_stop'
    recorder = BagRecorder()
    recorder.start(str(bag_path))

    def fail_close(writer):
        raise RuntimeError('close failed')

    monkeypatch.setattr(recorder, '_release_writer', fail_close)

    with pytest.raises(BagRecorderError, match='종료 실패'):
        recorder.stop()

    assert recorder.is_recording is False
    assert recorder.bag_path is None
    assert bag_path.exists() is False
