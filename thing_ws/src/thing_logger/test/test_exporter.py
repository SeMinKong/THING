"""Exporter 입력·출력 데이터 계약을 검증한다."""

import csv
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from thing_logger.exporter import ExportJob
from thing_logger.exporter import ExportValidationError
from thing_logger.exporter import RosbagSessionReader
from thing_logger.exporter import validate_export_job
from thing_logger.exporter import write_hand_command_csv
from thing_logger.bag_recorder import TOPIC_TYPES
from thing_logger.export_schema import HAND_COMMAND_HEADER


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
        (
            {'stamp': SimpleNamespace(sec=9, nanosec=0)},
            'precedes session start',
        ),
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
