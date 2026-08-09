"""Exporter 입력·출력 데이터 계약을 검증한다."""

import csv
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from geometry_msgs.msg import Point32
from thing_interfaces.msg import HandCommand
from thing_interfaces.msg import HandLandmarks
from thing_interfaces.msg import MotorState
from thing_interfaces.msg import MotorStatus
from thing_interfaces.msg import RecordingState
from thing_logger.bag_recorder import BagRecorder
from thing_logger.exporter import ExportJob
from thing_logger.exporter import ExportValidationError
from thing_logger.exporter import RosbagSessionReader
from thing_logger.exporter import SessionExporter
from thing_logger.exporter import BagRecord
from thing_logger.exporter import build_metadata
from thing_logger.exporter import calculate_content_digest
from thing_logger.exporter import inspect_export_file
from thing_logger.exporter import validate_export_job
from thing_logger.exporter import write_hand_command_csv
from thing_logger.exporter import write_landmark_json
from thing_logger.exporter import write_metadata_json
from thing_logger.exporter import write_motor_status_csv
from thing_logger.bag_recorder import TOPIC_TYPES
from thing_logger.export_schema import canonical_filenames
from thing_logger.export_schema import HAND_COMMAND_HEADER
from thing_logger.export_schema import LANDMARK_RECORD_FIELDS
from thing_logger.export_schema import MOTOR_STATUS_HEADER


class FakeTopic:
    """rosbag2 topic metadata의 테스트 대역이다."""

    def __init__(self, name, topic_type):
        """토픽 이름과 ROS 타입을 저장한다."""
        self.name = name
        self.type = topic_type


class FakeReader:
    """메모리에서 rosbag2 reader 동작을 흉내 낸다."""

    def __init__(self, topic_types=None, records=None, open_error=None):
        """테스트에 사용할 토픽·메시지·열기 오류를 저장한다."""
        self.topic_types = topic_types or dict(TOPIC_TYPES)
        self.records = list(records or [])
        self.open_error = open_error
        self.opened_uri = None

    def open(self, storage_options, converter_options):
        """열기 설정을 저장하거나 지정된 오류를 발생시킨다."""
        if self.open_error is not None:
            raise self.open_error
        self.opened_uri = storage_options.uri

    def get_all_topics_and_types(self):
        """등록된 토픽 metadata를 반환한다."""
        return [
            FakeTopic(name, topic_type)
            for name, topic_type in self.topic_types.items()
        ]

    def has_next(self):
        """읽을 메시지가 남아 있는지 반환한다."""
        return bool(self.records)

    def read_next(self):
        """가장 먼저 기록된 메시지를 반환한다."""
        return self.records.pop(0)


def make_hand_command(**overrides):
    """유효한 기본 HandCommand 테스트 객체를 만든다."""
    values = {
        'stamp': SimpleNamespace(sec=10, nanosec=250_000_000),
        'sequence': 7,
        'source': 1,
        'thumb_flex': 0.1,
        'thumb_opp': 0.2,
        'thumb_abd': 0.3,
        'index_flex': 0.4,
        'middle_flex': 0.5,
        'ring_flex': 0.6,
        'little_flex': 0.7,
        'speed_limit': 0.8,
        'confidence': 0.9,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_motor_state(motor_id, **overrides):
    """유효한 기본 MotorState 테스트 객체를 만든다."""
    values = {
        'motor_id': motor_id,
        'actuator_name': f'axis_{motor_id}',
        'goal_position_raw': 100 + motor_id,
        'present_position_raw': 90 + motor_id,
        'goal_position_rad': 0.1 * motor_id,
        'present_position_rad': 0.09 * motor_id,
        'velocity_rad_s': 0.2,
        'current_ampere': 0.3,
        'voltage_volt': 12.0,
        'temperature_celsius': 35.0,
        'torque_enabled': True,
        'hardware_error': 0,
        'communication_result': 0,
        'communication_ok': True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_motor_status(**overrides):
    """일곱 모터를 포함한 기본 MotorStatus 테스트 객체를 만든다."""
    values = {
        'header': SimpleNamespace(
            stamp=SimpleNamespace(sec=10, nanosec=500_000_000),
            frame_id='motor_bus',
        ),
        'motors': [make_motor_state(index) for index in range(1, 8)],
        'bus_communication_ok': True,
        'failed_read_count': 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_landmarks(**overrides):
    """21개 좌표를 포함한 기본 HandLandmarks 테스트 객체를 만든다."""
    values = {
        'header': SimpleNamespace(
            stamp=SimpleNamespace(sec=10, nanosec=750_000_000),
        ),
        'detected': True,
        'confidence': 0.95,
        'handedness': 2,
        'handedness_confidence': 0.98,
        'image_width': 640,
        'image_height': 480,
        'landmarks': [
            SimpleNamespace(x=index / 100, y=0.2, z=-0.01)
            for index in range(21)
        ],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_recording_state(state, session_id=123):
    """변환 생명주기를 확인할 기록 상태 테스트 객체를 만든다."""
    return SimpleNamespace(
        header=SimpleNamespace(
            stamp=SimpleNamespace(
                sec=20 if state == 3 else 10,
                nanosec=0,
            ),
        ),
        state=state,
        active_session_id=session_id,
        active_started_at=SimpleNamespace(sec=10, nanosec=0),
    )


class FakeSessionReader:
    """미리 준비한 역직렬화 메시지를 반환하는 session reader다."""

    def __init__(self, records):
        """반환할 기록 목록을 저장한다."""
        self.records = records

    def iter_records(self, bag_path):
        """저장된 기록을 입력 순서대로 반환한다."""
        yield from self.records


def make_complete_bag_records():
    """정상 완료 세션의 최소 export 입력을 만든다."""
    return [
        BagRecord(
            '/thing/recording_state',
            make_recording_state(2),
            10_000_000_000,
        ),
        BagRecord('/thing/command', make_hand_command(), 10_250_000_000),
        BagRecord(
            '/thing/motor_status',
            make_motor_status(),
            10_500_000_000,
        ),
        BagRecord(
            '/thing/landmarks',
            make_landmarks(),
            10_750_000_000,
        ),
        BagRecord(
            '/thing/recording_state',
            make_recording_state(3),
            20_000_000_000,
        ),
    ]


@pytest.mark.parametrize('result', ['SUCCESS', 'FAILURE'])
def test_validate_export_job_accepts_completed_bag_directory(
    tmp_path,
    result,
):
    """완료 bag 경로와 허용된 판정만 입력으로 수락한다."""
    bag_path = tmp_path / '123'
    bag_path.mkdir()

    validated_path = validate_export_job(
        ExportJob(str(bag_path), result),
    )

    assert validated_path == bag_path.resolve()


@pytest.mark.parametrize(
    'result',
    ['', 'UNSET', 'success', 'FAILED', 'SUCCESS '],
)
def test_validate_export_job_rejects_invalid_result(tmp_path, result):
    """SUCCESS·FAILURE 이외의 판정은 거부한다."""
    bag_path = tmp_path / '123'
    bag_path.mkdir()

    with pytest.raises(
        ExportValidationError,
        match='SUCCESS or FAILURE',
    ):
        validate_export_job(ExportJob(str(bag_path), result))


def test_validate_export_job_rejects_invalid_bag_paths(tmp_path):
    """빈·상대·누락·일반 파일 경로를 rosbag2 입력으로 거부한다."""
    with pytest.raises(ExportValidationError, match='empty'):
        validate_export_job(ExportJob('', 'SUCCESS'))

    with pytest.raises(ExportValidationError, match='absolute'):
        validate_export_job(ExportJob('relative/bag', 'SUCCESS'))

    with pytest.raises(ExportValidationError, match='does not exist'):
        validate_export_job(
            ExportJob(str(tmp_path / 'missing'), 'SUCCESS'),
        )

    regular_file = tmp_path / 'not-a-bag'
    regular_file.write_text('not a directory', encoding='utf-8')
    with pytest.raises(ExportValidationError, match='not a directory'):
        validate_export_job(ExportJob(str(regular_file), 'SUCCESS'))


def test_validate_export_job_rejects_unknown_object():
    """ExportJob이 아닌 임의 객체를 거부한다."""
    with pytest.raises(ExportValidationError, match='type is invalid'):
        validate_export_job(object())


def test_export_job_is_immutable():
    """Queue에 전달된 세션 입력은 이후 변경할 수 없다."""
    job = ExportJob('/tmp/session', 'SUCCESS')

    with pytest.raises(FrozenInstanceError):
        job.result = 'FAILURE'


def test_rosbag_reader_streams_required_records(tmp_path):
    """필수 메시지를 기록 순서와 timestamp를 유지해 반환한다."""
    bag_path = tmp_path / '123'
    bag_path.mkdir()
    fake_reader = FakeReader(
        records=[
            ('/thing/command', b'command', 100),
            ('/thing/landmarks', b'landmarks', 200),
        ],
    )
    resolved_types = []

    def resolve_message_type(topic_type):
        resolved_types.append(topic_type)
        return topic_type

    reader = RosbagSessionReader(
        reader_factory=lambda: fake_reader,
        message_type_resolver=resolve_message_type,
        deserializer=lambda serialized, _: serialized.decode(),
    )

    records = list(reader.iter_records(bag_path))

    assert fake_reader.opened_uri == str(bag_path)
    assert set(resolved_types) == set(TOPIC_TYPES.values())
    assert [record.topic_name for record in records] == [
        '/thing/command',
        '/thing/landmarks',
    ]
    assert [record.message for record in records] == [
        'command',
        'landmarks',
    ]
    assert [record.recorded_at_ns for record in records] == [100, 200]


def test_rosbag_reader_accepts_registered_topics_with_no_rows(tmp_path):
    """필수 토픽이 등록된 빈 세션은 빈 iterator로 처리한다."""
    bag_path = tmp_path / 'empty'
    bag_path.mkdir()
    fake_reader = FakeReader()
    reader = RosbagSessionReader(
        reader_factory=lambda: fake_reader,
        message_type_resolver=lambda topic_type: topic_type,
    )

    assert list(reader.iter_records(bag_path)) == []


def test_rosbag_reader_rejects_missing_required_topic(tmp_path):
    """필수 토픽이 등록되지 않은 bag을 거부한다."""
    bag_path = tmp_path / 'missing-topic'
    bag_path.mkdir()
    topic_types = dict(TOPIC_TYPES)
    topic_types.pop('/thing/landmarks')
    reader = RosbagSessionReader(
        reader_factory=lambda: FakeReader(topic_types=topic_types),
    )

    with pytest.raises(ExportValidationError, match='landmarks'):
        list(reader.iter_records(bag_path))


def test_rosbag_reader_rejects_wrong_topic_type(tmp_path):
    """필수 토픽의 ROS 메시지 타입이 다르면 거부한다."""
    bag_path = tmp_path / 'wrong-type'
    bag_path.mkdir()
    topic_types = dict(TOPIC_TYPES)
    topic_types['/thing/command'] = 'wrong_msgs/msg/Command'
    reader = RosbagSessionReader(
        reader_factory=lambda: FakeReader(topic_types=topic_types),
    )

    with pytest.raises(ExportValidationError, match='do not match'):
        list(reader.iter_records(bag_path))


def test_rosbag_reader_wraps_open_failure(tmp_path):
    """손상되거나 열 수 없는 bag 오류를 exporter 오류로 변환한다."""
    bag_path = tmp_path / 'broken'
    bag_path.mkdir()
    reader = RosbagSessionReader(
        reader_factory=lambda: FakeReader(
            open_error=RuntimeError('broken database'),
        ),
    )

    with pytest.raises(ExportValidationError, match='failed to open'):
        list(reader.iter_records(bag_path))


def test_hand_command_csv_writes_canonical_header_and_row(tmp_path):
    """HandCommand를 고정 헤더와 세션 상대시각으로 기록한다."""
    output_path = tmp_path / 'hand_command.csv.part'

    row_count = write_hand_command_csv(
        output_path,
        session_id=123,
        started_at_ns=10_000_000_000,
        messages=[make_hand_command()],
    )

    with output_path.open(encoding='utf-8', newline='') as output:
        rows = list(csv.reader(output))

    assert row_count == 1
    assert rows[0] == list(HAND_COMMAND_HEADER)
    assert rows[1] == [
        '123', '10', '250000000', '250', '7', '1',
        '0.1', '0.2', '0.3', '0.4', '0.5', '0.6', '0.7',
        '0.8', '0.9',
    ]


def test_hand_command_csv_allows_empty_data(tmp_path):
    """명령이 없는 세션도 헤더만 가진 CSV로 표현한다."""
    output_path = tmp_path / 'hand_command.csv.part'

    row_count = write_hand_command_csv(
        output_path,
        session_id=123,
        started_at_ns=10_000_000_000,
        messages=[],
    )

    assert row_count == 0
    assert output_path.read_text(encoding='utf-8') == (
        ','.join(HAND_COMMAND_HEADER) + '\n'
    )


@pytest.mark.parametrize(
    ('overrides', 'error_pattern'),
    [
        ({'thumb_flex': float('nan')}, 'must be finite'),
        ({'index_flex': 1.1}, 'between 0 and 1'),
        ({'speed_limit': 0.0}, 'greater than 0'),
        ({'confidence': -0.1}, 'between 0 and 1'),
        ({'source': 9}, 'source is invalid'),
        ({'sequence': -1}, 'sequence is invalid'),
    ],
)
def test_hand_command_csv_rejects_invalid_values(
    tmp_path,
    overrides,
    error_pattern,
):
    """범위·유한값·시간 계약을 위반한 명령을 거부한다."""
    output_path = tmp_path / 'hand_command.csv.part'

    with pytest.raises(ExportValidationError, match=error_pattern):
        write_hand_command_csv(
            output_path,
            session_id=123,
            started_at_ns=10_000_000_000,
            messages=[make_hand_command(**overrides)],
        )


def test_hand_command_csv_skips_leading_pre_session_records(tmp_path):
    """세션 시작 이전 stamp의 선두 명령은 건너뛰고 정상 행부터 쓴다."""
    output_path = tmp_path / 'hand_command.csv.part'
    messages = [
        make_hand_command(
            stamp=SimpleNamespace(sec=9, nanosec=999_000_000),
        ),
        make_hand_command(),
    ]

    row_count = write_hand_command_csv(
        output_path,
        session_id=123,
        started_at_ns=10_000_000_000,
        messages=messages,
    )

    assert row_count == 1
    with output_path.open(encoding='utf-8', newline='') as output:
        rows = list(csv.reader(output))
    assert len(rows) == 2
    assert rows[1][1] == '10'


def test_hand_command_csv_rejects_pre_session_after_valid_rows(tmp_path):
    """정상 행 이후의 세션 이전 stamp는 시계 이상이므로 거부한다."""
    output_path = tmp_path / 'hand_command.csv.part'
    messages = [
        make_hand_command(),
        make_hand_command(stamp=SimpleNamespace(sec=9, nanosec=0)),
    ]

    with pytest.raises(
        ExportValidationError,
        match='precedes session start',
    ):
        write_hand_command_csv(
            output_path,
            session_id=123,
            started_at_ns=10_000_000_000,
            messages=messages,
        )


def test_hand_command_csv_all_pre_session_records_yield_empty(tmp_path):
    """모든 명령이 세션 시작 이전이면 헤더만 가진 CSV가 된다."""
    output_path = tmp_path / 'hand_command.csv.part'
    messages = [
        make_hand_command(stamp=SimpleNamespace(sec=9, nanosec=0)),
    ]

    row_count = write_hand_command_csv(
        output_path,
        session_id=123,
        started_at_ns=10_000_000_000,
        messages=messages,
    )

    assert row_count == 0
    assert output_path.read_text(encoding='utf-8') == (
        ','.join(HAND_COMMAND_HEADER) + '\n'
    )


def test_hand_command_csv_rejects_decreasing_timestamps(tmp_path):
    """CSV에서 HandCommand 시각이 이전 행보다 빠르면 거부한다."""
    output_path = tmp_path / 'hand_command.csv.part'
    messages = [
        make_hand_command(
            stamp=SimpleNamespace(sec=11, nanosec=0),
        ),
        make_hand_command(
            stamp=SimpleNamespace(sec=10, nanosec=500_000_000),
        ),
    ]

    with pytest.raises(ExportValidationError, match='nondecreasing'):
        write_hand_command_csv(
            output_path,
            session_id=123,
            started_at_ns=10_000_000_000,
            messages=messages,
        )


def test_motor_status_csv_flattens_seven_motors(tmp_path):
    """모터 상태 하나를 고정 헤더의 모터별 일곱 행으로 기록한다."""
    output_path = tmp_path / 'motor_status.csv.part'

    row_count = write_motor_status_csv(
        output_path,
        session_id=123,
        started_at_ns=10_000_000_000,
        messages=[make_motor_status()],
    )

    with output_path.open(encoding='utf-8', newline='') as output:
        rows = list(csv.reader(output))

    assert row_count == 7
    assert rows[0] == list(MOTOR_STATUS_HEADER)
    assert len(rows) == 8
    assert rows[1][:7] == [
        '123', '10', '500000000', '500', 'motor_bus', '1', 'axis_1',
    ]
    assert rows[1][MOTOR_STATUS_HEADER.index('torque_enabled')] == 'true'
    assert rows[1][-4:] == ['0', 'true', 'true', '0']


def test_motor_status_csv_allows_empty_data(tmp_path):
    """모터 상태가 없는 세션도 헤더만 가진 CSV로 표현한다."""
    output_path = tmp_path / 'motor_status.csv.part'

    row_count = write_motor_status_csv(
        output_path,
        session_id=123,
        started_at_ns=10_000_000_000,
        messages=[],
    )

    assert row_count == 0
    assert output_path.read_text(encoding='utf-8') == (
        ','.join(MOTOR_STATUS_HEADER) + '\n'
    )


def test_motor_status_csv_skips_leading_pre_session_records(tmp_path):
    """세션 시작 이전 stamp의 선두 모터 상태는 건너뛴다."""
    output_path = tmp_path / 'motor_status.csv.part'
    messages = [
        make_motor_status(
            header=SimpleNamespace(
                stamp=SimpleNamespace(sec=9, nanosec=0),
                frame_id='motor_bus',
            ),
        ),
        make_motor_status(),
    ]

    row_count = write_motor_status_csv(
        output_path,
        session_id=123,
        started_at_ns=10_000_000_000,
        messages=messages,
    )

    assert row_count == 7
    with output_path.open(encoding='utf-8', newline='') as output:
        rows = list(csv.reader(output))
    assert len(rows) == 8
    assert rows[1][1] == '10'


def test_motor_status_csv_rejects_wrong_motor_count(tmp_path):
    """한 수신 시각에 모터가 일곱 개가 아니면 거부한다."""
    output_path = tmp_path / 'motor_status.csv.part'
    message = make_motor_status(
        motors=[make_motor_state(index) for index in range(1, 7)],
    )

    with pytest.raises(ExportValidationError, match='exactly 7'):
        write_motor_status_csv(
            output_path,
            session_id=123,
            started_at_ns=10_000_000_000,
            messages=[message],
        )


def test_motor_status_csv_rejects_duplicate_ids_and_nonfinite_values(
    tmp_path,
):
    """중복 모터 ID와 유한하지 않은 측정값을 거부한다."""
    output_path = tmp_path / 'motor_status.csv.part'
    duplicate_ids = [make_motor_state(1) for _ in range(7)]
    with pytest.raises(ExportValidationError, match='must be unique'):
        write_motor_status_csv(
            output_path,
            123,
            10_000_000_000,
            [make_motor_status(motors=duplicate_ids)],
        )

    invalid_motors = [make_motor_state(index) for index in range(1, 8)]
    invalid_motors[0].current_ampere = float('inf')
    with pytest.raises(ExportValidationError, match='must be finite'):
        write_motor_status_csv(
            output_path,
            123,
            10_000_000_000,
            [make_motor_status(motors=invalid_motors)],
        )

    invalid_torque = [make_motor_state(index) for index in range(1, 8)]
    invalid_torque[0].torque_enabled = 1
    with pytest.raises(ExportValidationError, match='must be boolean'):
        write_motor_status_csv(
            output_path,
            123,
            10_000_000_000,
            [make_motor_status(motors=invalid_torque)],
        )


def test_motor_status_csv_rejects_decreasing_timestamps(tmp_path):
    """CSV에서 MotorStatus 시각이 이전 묶음보다 빠르면 거부한다."""
    output_path = tmp_path / 'motor_status.csv.part'
    messages = [
        make_motor_status(header=SimpleNamespace(
            stamp=SimpleNamespace(sec=11, nanosec=0),
            frame_id='motor_bus',
        )),
        make_motor_status(header=SimpleNamespace(
            stamp=SimpleNamespace(sec=10, nanosec=500_000_000),
            frame_id='motor_bus',
        )),
    ]

    with pytest.raises(ExportValidationError, match='nondecreasing'):
        write_motor_status_csv(
            output_path,
            session_id=123,
            started_at_ns=10_000_000_000,
            messages=messages,
        )


def test_landmark_json_writes_canonical_record(tmp_path):
    """HandLandmarks를 고정 필드와 21개 좌표 JSON으로 기록한다."""
    output_path = tmp_path / 'landmark.json.part'

    row_count = write_landmark_json(
        output_path,
        session_id=123,
        started_at_ns=10_000_000_000,
        messages=[make_landmarks()],
    )

    records = json.loads(output_path.read_text(encoding='utf-8'))
    assert row_count == 1
    assert len(records) == 1
    assert tuple(records[0]) == LANDMARK_RECORD_FIELDS
    assert records[0]['session_id'] == '123'
    assert records[0]['timestamp'] == '1970-01-01T00:00:10.750Z'
    assert records[0]['elapsed_ms'] == 750
    assert len(records[0]['landmarks']) == 21
    assert tuple(records[0]['landmarks'][0]) == ('x', 'y', 'z')


def test_landmark_json_allows_empty_data(tmp_path):
    """landmark가 없는 세션은 빈 JSON 배열로 표현한다."""
    output_path = tmp_path / 'landmark.json.part'

    row_count = write_landmark_json(
        output_path,
        session_id=123,
        started_at_ns=10_000_000_000,
        messages=[],
    )

    assert row_count == 0
    assert output_path.read_text(encoding='utf-8') == '[\n]\n'


def test_landmark_json_skips_leading_pre_session_records(tmp_path):
    """세션 시작 이전 stamp의 선두 프레임은 건너뛴다."""
    output_path = tmp_path / 'landmark.json.part'
    messages = [
        make_landmarks(
            header=SimpleNamespace(
                stamp=SimpleNamespace(sec=9, nanosec=0),
            ),
        ),
        make_landmarks(),
    ]

    row_count = write_landmark_json(
        output_path,
        session_id=123,
        started_at_ns=10_000_000_000,
        messages=messages,
    )

    assert row_count == 1
    records = json.loads(output_path.read_text(encoding='utf-8'))
    assert len(records) == 1
    assert records[0]['stamp_sec'] == 10


@pytest.mark.parametrize(
    ('overrides', 'error_pattern'),
    [
        ({'landmarks': []}, 'exactly 21'),
        ({'confidence': 1.1}, 'between 0 and 1'),
        ({'handedness': 9}, 'handedness is invalid'),
        ({'image_width': 0}, 'dimensions must be positive'),
        ({'detected': 1}, 'must be boolean'),
    ],
)
def test_landmark_json_rejects_invalid_values(
    tmp_path,
    overrides,
    error_pattern,
):
    """좌표 개수·enum·범위·타입 계약 위반을 거부한다."""
    output_path = tmp_path / 'landmark.json.part'

    with pytest.raises(ExportValidationError, match=error_pattern):
        write_landmark_json(
            output_path,
            session_id=123,
            started_at_ns=10_000_000_000,
            messages=[make_landmarks(**overrides)],
        )


def make_file_infos(tmp_path):
    """세 canonical 파일의 metadata 테스트 정보를 만든다."""
    infos = {}
    for file_kind in ('hand_command', 'motor_status', 'landmark'):
        path = tmp_path / f'session_123_{file_kind}.data.part'
        path.write_bytes(file_kind.encode('utf-8'))
        infos[file_kind] = inspect_export_file(path, row_count=1)
    return infos


def test_inspect_export_file_calculates_size_and_sha256(tmp_path):
    """파일의 실제 byte 크기와 SHA-256을 계산한다."""
    path = tmp_path / 'session_123_hand_command.csv.part'
    path.write_bytes(b'abc')

    info = inspect_export_file(path, row_count=7)

    assert info.path == str(path)
    assert info.filename == 'session_123_hand_command.csv'
    assert info.size_bytes == 3
    assert info.row_count == 7
    assert info.sha256 == (
        'ba7816bf8f01cfea414140de5dae2223'
        'b00361a396177a9cb410ff61f20015ad'
    )


def test_build_metadata_uses_three_data_files_and_canonical_digest(tmp_path):
    """metadata에 세 데이터 파일 정보와 canonical digest를 기록한다."""
    files = make_file_infos(tmp_path)

    metadata = build_metadata(
        robot_id='THING-001',
        session_id=123,
        started_at_ns=10_000_000_000,
        ended_at_ns=20_000_000_000,
        result='SUCCESS',
        time_sync=True,
        files=files,
    )

    assert metadata['session_id'] == '123'
    assert metadata['started_at'] == '1970-01-01T00:00:10.000Z'
    assert metadata['ended_at'] == '1970-01-01T00:00:20.000Z'
    assert tuple(metadata['files']) == (
        'hand_command', 'motor_status', 'landmark',
    )
    assert metadata['content_digest'].startswith('sha256:')
    assert len(metadata['content_digest']) == 71
    assert metadata['content_digest'] == calculate_content_digest(metadata)


def test_content_digest_detects_content_change(tmp_path):
    """데이터 hash 또는 세션 판정 변경을 digest로 감지한다."""
    files = make_file_infos(tmp_path)
    base = build_metadata(
        robot_id='THING-001',
        session_id=123,
        started_at_ns=10_000_000_000,
        ended_at_ns=20_000_000_000,
        result='SUCCESS',
        time_sync=True,
        files=files,
    )
    failure = build_metadata(
        robot_id='THING-001',
        session_id=123,
        started_at_ns=10_000_000_000,
        ended_at_ns=20_000_000_000,
        result='FAILURE',
        time_sync=True,
        files=files,
    )

    assert base['content_digest'] != failure['content_digest']


def test_write_metadata_json_preserves_schema_order(tmp_path):
    """JSON metadata를 schema 순서와 compact UTF-8 형식으로 쓴다."""
    metadata = build_metadata(
        robot_id='THING-001',
        session_id=123,
        started_at_ns=10_000_000_000,
        ended_at_ns=20_000_000_000,
        result='SUCCESS',
        time_sync=True,
        files=make_file_infos(tmp_path),
    )
    path = tmp_path / 'metadata.json.part'

    write_metadata_json(path, metadata)

    parsed = json.loads(path.read_text(encoding='utf-8'))
    assert tuple(parsed) == tuple(metadata)
    assert parsed == metadata


def test_session_exporter_skips_leading_pre_session_records(tmp_path):
    """세션 시작 이전에 생성돼 늦게 도착한 선두 데이터를 건너뛴다."""
    bag_path = tmp_path / 'bags' / '123'
    bag_path.mkdir(parents=True)
    export_root = tmp_path / 'tmp-upload'
    records = [
        BagRecord(
            '/thing/recording_state',
            make_recording_state(2),
            10_000_000_000,
        ),
        BagRecord(
            '/thing/landmarks',
            make_landmarks(
                header=SimpleNamespace(
                    stamp=SimpleNamespace(sec=9, nanosec=700_000_000),
                ),
            ),
            10_020_000_000,
        ),
        BagRecord(
            '/thing/motor_status',
            make_motor_status(
                header=SimpleNamespace(
                    stamp=SimpleNamespace(sec=9, nanosec=900_000_000),
                    frame_id='motor_bus',
                ),
            ),
            10_030_000_000,
        ),
        BagRecord('/thing/command', make_hand_command(), 10_250_000_000),
        BagRecord(
            '/thing/motor_status',
            make_motor_status(),
            10_500_000_000,
        ),
        BagRecord('/thing/landmarks', make_landmarks(), 10_750_000_000),
        BagRecord(
            '/thing/recording_state',
            make_recording_state(3),
            20_000_000_000,
        ),
    ]
    exporter = SessionExporter(
        'THING-001',
        str(export_root),
        reader=FakeSessionReader(records),
    )

    result = exporter.export(ExportJob(str(bag_path), 'SUCCESS'))

    metadata = json.loads(
        Path(result.files['metadata'].path).read_text(encoding='utf-8')
    )
    assert metadata['files']['hand_command']['row_count'] == 1
    assert metadata['files']['motor_status']['row_count'] == 7
    assert metadata['files']['landmark']['row_count'] == 1
    landmark_records = json.loads(
        Path(result.files['landmark'].path).read_text(encoding='utf-8')
    )
    assert len(landmark_records) == 1
    assert landmark_records[0]['stamp_sec'] == 10


def test_session_exporter_atomically_exposes_four_valid_files(tmp_path):
    """정상 완료 bag은 검증된 canonical 4파일 디렉터리로 노출한다."""
    bag_path = tmp_path / 'bags' / '123'
    bag_path.mkdir(parents=True)
    export_root = tmp_path / 'tmp-upload'
    exporter = SessionExporter(
        'THING-001',
        str(export_root),
        reader=FakeSessionReader(make_complete_bag_records()),
    )

    result = exporter.export(ExportJob(str(bag_path), 'SUCCESS'))

    final_directory = export_root / '123'
    assert result.session_id == 123
    assert result.directory == str(final_directory)
    assert set(result.files) == {
        'metadata', 'hand_command', 'motor_status', 'landmark',
    }
    assert {path.name for path in final_directory.iterdir()} == {
        'session_123_metadata.json',
        'session_123_hand_command.csv',
        'session_123_motor_status.csv',
        'session_123_landmark.json',
    }
    assert not (export_root / '.123.part').exists()
    assert not list(final_directory.glob('*.part'))

    metadata = json.loads(
        (final_directory / 'session_123_metadata.json').read_text(
            encoding='utf-8',
        )
    )
    assert result.content_digest == metadata['content_digest']
    assert metadata['files']['hand_command']['row_count'] == 1
    assert metadata['files']['motor_status']['row_count'] == 7
    assert metadata['files']['landmark']['row_count'] == 1

    assert metadata['content_digest'] == calculate_content_digest(metadata)

    with Path(result.files['hand_command'].path).open(
        encoding='utf-8', newline=''
    ) as source:
        hand_rows = list(csv.reader(source))
    assert hand_rows[0] == list(HAND_COMMAND_HEADER)
    assert hand_rows[1][:6] == ['123', '10', '250000000', '250', '7', '1']

    with Path(result.files['motor_status'].path).open(
        encoding='utf-8', newline=''
    ) as source:
        motor_rows = list(csv.reader(source))
    assert motor_rows[0] == list(MOTOR_STATUS_HEADER)
    assert [row[5] for row in motor_rows[1:]] == [
        str(motor_id) for motor_id in range(1, 8)
    ]
    assert all(row[15] == 'true' for row in motor_rows[1:])

    landmark_records = json.loads(
        Path(result.files['landmark'].path).read_text(encoding='utf-8')
    )
    assert len(landmark_records) == 1
    landmark = landmark_records[0]
    assert tuple(landmark) == LANDMARK_RECORD_FIELDS
    assert landmark['session_id'] == '123'
    assert landmark['timestamp'] == '1970-01-01T00:00:10.750Z'
    assert landmark['elapsed_ms'] == 750
    assert len(landmark['landmarks']) == 21
    assert landmark['landmarks'][20] == {
        'x': pytest.approx(0.2),
        'y': pytest.approx(0.2),
        'z': pytest.approx(-0.01),
    }


def test_session_exporter_reads_actual_rosbag2_fixture(tmp_path):
    """실제 rosbag2 기록을 읽어 canonical 4파일로 변환한다."""
    bag_path = tmp_path / 'bags' / '123'
    recorder = BagRecorder()
    recorder.start(str(bag_path))

    recording = RecordingState()
    recording.header.stamp.sec = 10
    recording.state = RecordingState.RECORDING
    recording.active_session_id = 123
    recording.active_started_at.sec = 10
    recorder.write('/thing/recording_state', recording, 10_000_000_000)

    command = HandCommand()
    command.stamp.sec = 10
    command.stamp.nanosec = 250_000_000
    command.sequence = 7
    command.source = HandCommand.SOURCE_MIMIC
    command.thumb_flex = 0.1
    command.thumb_opp = 0.2
    command.thumb_abd = 0.3
    command.index_flex = 0.4
    command.middle_flex = 0.5
    command.ring_flex = 0.6
    command.little_flex = 0.7
    command.speed_limit = 0.8
    command.confidence = 0.9
    recorder.write('/thing/command', command, 10_250_000_000)

    motor_status = MotorStatus()
    motor_status.header.stamp.sec = 10
    motor_status.header.stamp.nanosec = 500_000_000
    motor_status.header.frame_id = 'motor_bus'
    motor_status.bus_communication_ok = True
    for motor_id in range(1, 8):
        motor = MotorState()
        motor.motor_id = motor_id
        motor.actuator_name = f'axis_{motor_id}'
        motor.goal_position_raw = 100 + motor_id
        motor.present_position_raw = 90 + motor_id
        motor.goal_position_rad = 0.1 * motor_id
        motor.present_position_rad = 0.09 * motor_id
        motor.velocity_rad_s = 0.2
        motor.current_ampere = 0.3
        motor.voltage_volt = 12.0
        motor.temperature_celsius = 35.0
        motor.torque_enabled = True
        motor.communication_ok = True
        motor_status.motors.append(motor)
    recorder.write(
        '/thing/motor_status',
        motor_status,
        10_500_000_000,
    )

    landmarks = HandLandmarks()
    landmarks.header.stamp.sec = 10
    landmarks.header.stamp.nanosec = 750_000_000
    landmarks.detected = True
    landmarks.confidence = 0.95
    landmarks.handedness = HandLandmarks.HANDEDNESS_RIGHT
    landmarks.handedness_confidence = 0.98
    landmarks.image_width = 640
    landmarks.image_height = 480
    for index in range(21):
        landmarks.landmarks[index] = Point32(
            x=index / 100,
            y=0.2,
            z=-0.01,
        )
    recorder.write('/thing/landmarks', landmarks, 10_750_000_000)

    stopping = RecordingState()
    stopping.header.stamp.sec = 20
    stopping.state = RecordingState.STOPPING
    stopping.active_session_id = 123
    stopping.active_started_at.sec = 10
    recorder.write('/thing/recording_state', stopping, 20_000_000_000)
    recorder.stop()

    result = SessionExporter(
        'THING-001',
        str(tmp_path / 'tmp-upload'),
    ).export(ExportJob(str(bag_path), 'SUCCESS'))

    metadata = json.loads(
        Path(result.files['metadata'].path).read_text(encoding='utf-8')
    )
    assert set(result.files) == {
        'metadata', 'hand_command', 'motor_status', 'landmark',
    }
    assert metadata['files']['hand_command']['row_count'] == 1
    assert metadata['files']['motor_status']['row_count'] == 7
    assert metadata['files']['landmark']['row_count'] == 1

    exported_landmarks = json.loads(
        Path(result.files['landmark'].path).read_text(encoding='utf-8')
    )
    assert len(exported_landmarks) == 1
    exported = exported_landmarks[0]
    assert tuple(exported) == LANDMARK_RECORD_FIELDS
    assert exported['session_id'] == '123'
    assert exported['timestamp'] == '1970-01-01T00:00:10.750Z'
    assert exported['stamp_sec'] == landmarks.header.stamp.sec
    assert exported['stamp_nanosec'] == landmarks.header.stamp.nanosec
    assert exported['elapsed_ms'] == 750
    assert exported['detected'] is landmarks.detected
    assert exported['confidence'] == pytest.approx(landmarks.confidence)
    assert exported['handedness'] == landmarks.handedness
    assert exported['handedness_confidence'] == pytest.approx(
        landmarks.handedness_confidence
    )
    assert exported['image_width'] == landmarks.image_width
    assert exported['image_height'] == landmarks.image_height
    assert len(exported['landmarks']) == len(landmarks.landmarks) == 21
    for exported_point, bag_point in zip(
        exported['landmarks'],
        landmarks.landmarks,
    ):
        assert tuple(exported_point) == ('x', 'y', 'z')
        assert exported_point['x'] == pytest.approx(bag_point.x)
        assert exported_point['y'] == pytest.approx(bag_point.y)
        assert exported_point['z'] == pytest.approx(bag_point.z)


def test_session_exporter_produces_same_digest_for_same_content(tmp_path):
    """같은 완료 bag과 판정은 같은 digest를 만든다."""
    bag_path = tmp_path / 'bags' / '123'
    bag_path.mkdir(parents=True)
    first = SessionExporter(
        'THING-001',
        str(tmp_path / 'first'),
        reader=FakeSessionReader(make_complete_bag_records()),
    ).export(ExportJob(str(bag_path), 'SUCCESS'))
    second = SessionExporter(
        'THING-001',
        str(tmp_path / 'second'),
        reader=FakeSessionReader(make_complete_bag_records()),
    ).export(ExportJob(str(bag_path), 'SUCCESS'))

    assert first.content_digest == second.content_digest


def test_session_exporter_reexports_same_bag_after_cleanup(tmp_path):
    """정리 후 같은 임시 경로로 재생성해도 canonical 데이터는 같다."""
    bag_path = tmp_path / 'bags' / '123'
    bag_path.mkdir(parents=True)
    export_root = tmp_path / 'tmp-upload'
    records = make_complete_bag_records()

    first = SessionExporter(
        'THING-001',
        str(export_root),
        reader=FakeSessionReader(records),
    )
    first_result = first.export(ExportJob(str(bag_path), 'SUCCESS'))
    first_files = {
        file_kind: Path(info.path).read_bytes()
        for file_kind, info in first_result.files.items()
    }

    first.cleanup(first_result)
    assert not Path(first_result.directory).exists()

    second_result = SessionExporter(
        'THING-001',
        str(export_root),
        reader=FakeSessionReader(records),
    ).export(ExportJob(str(bag_path), 'SUCCESS'))

    assert first_result.content_digest == second_result.content_digest
    assert {
        file_kind: Path(info.path).read_bytes()
        for file_kind, info in second_result.files.items()
    } == first_files


def test_session_exporter_cleans_staging_after_invalid_landmarks(tmp_path):
    """좌표 계약 위반 시 staging과 최종 파일을 모두 노출하지 않는다."""
    bag_path = tmp_path / 'bags' / '123'
    bag_path.mkdir(parents=True)
    records = make_complete_bag_records()
    records[3] = BagRecord(
        '/thing/landmarks',
        make_landmarks(landmarks=[]),
        10_750_000_000,
    )
    export_root = tmp_path / 'tmp-upload'
    exporter = SessionExporter(
        'THING-001',
        str(export_root),
        reader=FakeSessionReader(records),
    )

    with pytest.raises(ExportValidationError, match='exactly 21'):
        exporter.export(ExportJob(str(bag_path), 'SUCCESS'))

    assert not (export_root / '.123.part').exists()
    assert not (export_root / '123').exists()


@pytest.mark.parametrize(
    ('file_kind', 'error_pattern'),
    [
        ('hand_command', 'CSV header or row count mismatch'),
        ('motor_status', 'CSV header or row count mismatch'),
        ('landmark', 'LandMark JSON row count mismatch'),
    ],
)
def test_session_exporter_hides_row_count_mismatch(
    tmp_path,
    file_kind,
    error_pattern,
):
    """계산 행 수와 실제 파일 행 수가 다르면 결과를 노출하지 않는다."""

    class IncorrectRowCountExporter(SessionExporter):
        def _write_data_files(self, bag_path, directory, session_id):
            row_counts, lifecycle = super()._write_data_files(
                bag_path,
                directory,
                session_id,
            )
            row_counts[file_kind] += 1
            return row_counts, lifecycle

    bag_path = tmp_path / 'bags' / '123'
    bag_path.mkdir(parents=True)
    export_root = tmp_path / 'tmp-upload'
    exporter = IncorrectRowCountExporter(
        'THING-001',
        str(export_root),
        reader=FakeSessionReader(make_complete_bag_records()),
    )

    with pytest.raises(ExportValidationError, match=error_pattern):
        exporter.export(ExportJob(str(bag_path), 'SUCCESS'))

    assert not (export_root / '.123.part').exists()
    assert not (export_root / '123').exists()


@pytest.mark.parametrize('topic_name', ['/thing/command', '/thing/motor_status'])
def test_session_exporter_hides_decreasing_csv_timestamps(
    tmp_path,
    topic_name,
):
    """CSV 시각 역행 시 staging을 정리하고 최종 파일을 노출하지 않는다."""
    bag_path = tmp_path / 'bags' / '123'
    bag_path.mkdir(parents=True)
    records = make_complete_bag_records()
    if topic_name == '/thing/command':
        later_message = make_hand_command(
            stamp=SimpleNamespace(sec=11, nanosec=0),
        )
        insert_at = 1
    else:
        later_message = make_motor_status(header=SimpleNamespace(
            stamp=SimpleNamespace(sec=11, nanosec=0),
            frame_id='motor_bus',
        ))
        insert_at = 2
    records.insert(
        insert_at,
        BagRecord(topic_name, later_message, 11_000_000_000),
    )
    export_root = tmp_path / 'tmp-upload'
    exporter = SessionExporter(
        'THING-001',
        str(export_root),
        reader=FakeSessionReader(records),
    )

    with pytest.raises(ExportValidationError, match='nondecreasing'):
        exporter.export(ExportJob(str(bag_path), 'SUCCESS'))

    assert not (export_root / '.123.part').exists()
    assert not (export_root / '123').exists()


def test_session_exporter_rejects_incomplete_recording_lifecycle(tmp_path):
    """정상 RECORDING·STOPPING 흐름이 없는 bag을 공개하지 않는다."""
    bag_path = tmp_path / 'bags' / '123'
    bag_path.mkdir(parents=True)
    records = make_complete_bag_records()[:-1]
    export_root = tmp_path / 'tmp-upload'
    exporter = SessionExporter(
        'THING-001',
        str(export_root),
        reader=FakeSessionReader(records),
    )

    with pytest.raises(ExportValidationError, match='lifecycle is missing'):
        exporter.export(ExportJob(str(bag_path), 'SUCCESS'))

    assert not (export_root / '.123.part').exists()
    assert not (export_root / '123').exists()


def test_session_exporter_never_overwrites_existing_final_directory(
    tmp_path,
):
    """같은 Session ID의 기존 완료 디렉터리를 덮어쓰지 않는다."""
    bag_path = tmp_path / 'bags' / '123'
    bag_path.mkdir(parents=True)
    export_root = tmp_path / 'tmp-upload'
    final_directory = export_root / '123'
    final_directory.mkdir(parents=True)
    marker = final_directory / 'existing'
    marker.write_text('keep', encoding='utf-8')
    exporter = SessionExporter(
        'THING-001',
        str(export_root),
        reader=FakeSessionReader(make_complete_bag_records()),
    )

    with pytest.raises(ExportValidationError, match='already exists'):
        exporter.export(ExportJob(str(bag_path), 'SUCCESS'))

    assert marker.read_text(encoding='utf-8') == 'keep'


def test_session_exporter_cleanup_removes_files_but_preserves_bag(tmp_path):
    """Uploader 인계 후 임시 네 파일만 지우고 rosbag2는 보존한다."""
    bag_path = tmp_path / 'bags' / '123'
    bag_path.mkdir(parents=True)
    bag_marker = bag_path / 'metadata.yaml'
    bag_marker.write_text('rosbag2', encoding='utf-8')
    export_root = tmp_path / 'tmp-upload'
    exporter = SessionExporter(
        'THING-001',
        str(export_root),
        reader=FakeSessionReader(make_complete_bag_records()),
    )
    result = exporter.export(ExportJob(str(bag_path), 'SUCCESS'))

    exporter.cleanup(result)

    assert not Path(result.directory).exists()
    assert bag_marker.read_text(encoding='utf-8') == 'rosbag2'


def test_session_exporter_cleanup_rejects_unexpected_file(tmp_path):
    """예상하지 않은 파일이 섞인 디렉터리는 재귀 삭제하지 않는다."""
    bag_path = tmp_path / 'bags' / '123'
    bag_path.mkdir(parents=True)
    exporter = SessionExporter(
        'THING-001',
        str(tmp_path / 'tmp-upload'),
        reader=FakeSessionReader(make_complete_bag_records()),
    )
    result = exporter.export(ExportJob(str(bag_path), 'SUCCESS'))
    unexpected = Path(result.directory) / 'unexpected.txt'
    unexpected.write_text('keep', encoding='utf-8')

    with pytest.raises(ExportValidationError, match='contents are invalid'):
        exporter.cleanup(result)

    assert unexpected.read_text(encoding='utf-8') == 'keep'


def test_session_exporter_cleans_stale_exports_on_startup(tmp_path):
    """시작 시 안전한 이전 .part와 완료 네 파일만 정리한다."""
    export_root = tmp_path / 'tmp-upload'
    staging_directory = export_root / '.123.part'
    staging_directory.mkdir(parents=True)
    (staging_directory / 'session_123_hand_command.csv.part').write_text(
        'partial',
        encoding='utf-8',
    )
    (staging_directory / 'session_123_motor_status.csv').write_text(
        'renamed-before-crash',
        encoding='utf-8',
    )
    final_directory = export_root / '456'
    final_directory.mkdir()
    for filename in canonical_filenames(456).values():
        (final_directory / filename).write_text('complete', encoding='utf-8')

    bag_directory = tmp_path / 'rosbag2' / '123'
    bag_directory.mkdir(parents=True)
    bag_marker = bag_directory / 'metadata.yaml'
    bag_marker.write_text('rosbag2', encoding='utf-8')
    unrelated = export_root / 'operator-note'
    unrelated.write_text('keep', encoding='utf-8')

    SessionExporter('THING-001', str(export_root))

    assert not staging_directory.exists()
    assert not final_directory.exists()
    assert unrelated.read_text(encoding='utf-8') == 'keep'
    assert bag_marker.read_text(encoding='utf-8') == 'rosbag2'


def test_session_exporter_startup_cleanup_does_not_follow_symlink(tmp_path):
    """잔여 항목 symlink는 대상 디렉터리를 따라가 삭제하지 않는다."""
    export_root = tmp_path / 'tmp-upload'
    export_root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    marker = outside / 'keep.txt'
    marker.write_text('keep', encoding='utf-8')
    stale_link = export_root / '.123.part'
    stale_link.symlink_to(outside, target_is_directory=True)

    SessionExporter('THING-001', str(export_root))

    assert not stale_link.exists()
    assert marker.read_text(encoding='utf-8') == 'keep'


def test_session_exporter_startup_cleanup_preserves_unknown_contents(
    tmp_path,
):
    """Exporter 소유로 확인할 수 없는 잔여 디렉터리는 보존한다."""
    export_root = tmp_path / 'tmp-upload'
    suspicious = export_root / '.123.part'
    suspicious.mkdir(parents=True)
    marker = suspicious / 'unexpected.txt'
    marker.write_text('keep', encoding='utf-8')

    SessionExporter('THING-001', str(export_root))

    assert marker.read_text(encoding='utf-8') == 'keep'


def test_session_exporter_rejects_relative_export_root():
    """시작 정리 범위가 모호한 상대 임시 경로를 거부한다."""
    with pytest.raises(ExportValidationError, match='must be absolute'):
        SessionExporter('THING-001', 'tmp-upload')
