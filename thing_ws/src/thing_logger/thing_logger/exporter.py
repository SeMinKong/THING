"""완료된 rosbag2 세션을 canonical 공개 파일로 변환한다."""

import csv
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

import rosbag2_py
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message

from thing_logger.bag_recorder import TOPIC_TYPES
from thing_logger.export_schema import HAND_COMMAND_HEADER


ALLOWED_RESULTS = frozenset({'SUCCESS', 'FAILURE'})
HAND_COMMAND_AXES = (
    'thumb_flex',
    'thumb_opp',
    'thumb_abd',
    'index_flex',
    'middle_flex',
    'ring_flex',
    'little_flex',
)
HAND_COMMAND_SOURCES = frozenset(range(6))


class ExportError(RuntimeError):
    """세션 export 과정의 공통 실패를 나타낸다."""


class ExportValidationError(ExportError):
    """export 입력 또는 출력 계약 위반을 나타낸다."""


@dataclass(frozen=True)
class ExportJob:
    """Exporter가 처리할 완료 세션 입력을 나타낸다."""

    bag_path: str
    result: str


@dataclass(frozen=True)
class ExportFileInfo:
    """완성된 canonical 파일 하나의 검증 정보를 나타낸다."""

    path: str
    filename: str
    size_bytes: int
    row_count: int
    sha256: str


@dataclass(frozen=True)
class ExportResult:
    """Uploader에 인계할 완료된 export 결과를 나타낸다."""

    session_id: int
    directory: str
    content_digest: str
    files: Mapping[str, ExportFileInfo]


@dataclass(frozen=True)
class BagRecord:
    """rosbag2에서 역직렬화한 필수 토픽 메시지 하나를 나타낸다."""

    topic_name: str
    message: Any
    recorded_at_ns: int


def validate_export_job(job: ExportJob) -> Path:
    """입력 job을 검증하고 정규화된 rosbag2 경로를 반환한다."""
    if not isinstance(job, ExportJob):
        raise ExportValidationError('export job type is invalid')

    if job.result not in ALLOWED_RESULTS:
        raise ExportValidationError(
            'result must be SUCCESS or FAILURE'
        )

    if not job.bag_path:
        raise ExportValidationError('bag path is empty')

    bag_path = Path(job.bag_path)
    if not bag_path.is_absolute():
        raise ExportValidationError('bag path must be absolute')

    if not bag_path.exists():
        raise ExportValidationError('bag path does not exist')

    if not bag_path.is_dir():
        raise ExportValidationError('bag path is not a directory')

    return bag_path.resolve()


class RosbagSessionReader:
    """완료된 rosbag2를 검증하며 필수 메시지를 순서대로 읽는다."""

    def __init__(
        self,
        reader_factory=None,
        message_type_resolver=None,
        deserializer=None,
    ) -> None:
        """실사용 구현과 테스트 대역을 주입할 수 있게 초기화한다."""
        self._reader_factory = (
            reader_factory or rosbag2_py.SequentialReader
        )
        self._message_type_resolver = (
            message_type_resolver or get_message
        )
        self._deserializer = deserializer or deserialize_message

    def iter_records(
        self,
        bag_path: Path,
    ) -> Iterator[BagRecord]:
        """필수 토픽을 검증한 뒤 기록 순서대로 메시지를 반환한다."""
        reader = self._reader_factory()
        storage_options = rosbag2_py.StorageOptions(
            uri=str(bag_path),
            storage_id='sqlite3',
        )
        converter_options = rosbag2_py.ConverterOptions(
            input_serialization_format='cdr',
            output_serialization_format='cdr',
        )

        try:
            reader.open(storage_options, converter_options)
        except Exception as error:
            raise ExportValidationError(
                f'failed to open rosbag2: {error}'
            ) from error

        topic_types = {
            topic.name: topic.type
            for topic in reader.get_all_topics_and_types()
        }
        self._validate_topic_types(topic_types)

        resolved_types = {
            topic_name: self._resolve_message_type(topic_type)
            for topic_name, topic_type in TOPIC_TYPES.items()
        }

        while reader.has_next():
            try:
                topic_name, serialized, recorded_at_ns = (
                    reader.read_next()
                )
            except Exception as error:
                raise ExportValidationError(
                    f'failed to read rosbag2 message: {error}'
                ) from error

            if topic_name not in TOPIC_TYPES:
                continue

            try:
                message = self._deserializer(
                    serialized,
                    resolved_types[topic_name],
                )
            except Exception as error:
                raise ExportValidationError(
                    f'failed to deserialize {topic_name}: {error}'
                ) from error

            yield BagRecord(
                topic_name=topic_name,
                message=message,
                recorded_at_ns=recorded_at_ns,
            )

    @staticmethod
    def _validate_topic_types(topic_types: Mapping[str, str]) -> None:
        """필수 토픽의 존재와 ROS 메시지 타입을 검사한다."""
        missing_topics = sorted(set(TOPIC_TYPES) - set(topic_types))
        if missing_topics:
            raise ExportValidationError(
                'required topics are missing: '
                + ', '.join(missing_topics)
            )

        mismatches = [
            (
                f'{topic_name} expected {expected_type}, '
                f'got {topic_types[topic_name]}'
            )
            for topic_name, expected_type in TOPIC_TYPES.items()
            if topic_types[topic_name] != expected_type
        ]
        if mismatches:
            raise ExportValidationError(
                'topic types do not match: ' + '; '.join(mismatches)
            )

    def _resolve_message_type(self, topic_type: str):
        """문자열 ROS 타입을 Python 메시지 클래스로 변환한다."""
        try:
            return self._message_type_resolver(topic_type)
        except Exception as error:
            raise ExportValidationError(
                f'failed to resolve message type {topic_type}: {error}'
            ) from error


def write_hand_command_csv(
    path: Path,
    session_id: int,
    started_at_ns: int,
    messages: Iterable[Any],
) -> int:
    """명령 메시지를 canonical CSV로 쓰고 행 수를 반환한다."""
    row_count = 0
    try:
        with path.open('w', encoding='utf-8', newline='') as output:
            writer = csv.writer(output, lineterminator='\n')
            writer.writerow(HAND_COMMAND_HEADER)
            for message in messages:
                writer.writerow(
                    _hand_command_row(
                        session_id,
                        started_at_ns,
                        message,
                    )
                )
                row_count += 1
    except ExportValidationError:
        raise
    except Exception as error:
        raise ExportError(
            f'failed to write HandCommand CSV: {error}'
        ) from error

    return row_count


def _hand_command_row(
    session_id: int,
    started_at_ns: int,
    message: Any,
) -> tuple:
    """명령 하나를 검증해 canonical CSV 행으로 변환한다."""
    stamp_sec = _require_integer(message.stamp.sec, 'stamp.sec')
    stamp_nanosec = _require_integer(
        message.stamp.nanosec,
        'stamp.nanosec',
    )
    if stamp_sec < 0 or not 0 <= stamp_nanosec < 1_000_000_000:
        raise ExportValidationError('HandCommand timestamp is invalid')

    timestamp_ns = stamp_sec * 1_000_000_000 + stamp_nanosec
    elapsed_ns = timestamp_ns - started_at_ns
    if elapsed_ns < 0:
        raise ExportValidationError(
            'HandCommand timestamp precedes session start'
        )

    sequence = _require_integer(message.sequence, 'sequence')
    if not 0 <= sequence < 2**32:
        raise ExportValidationError('HandCommand sequence is invalid')

    source = _require_integer(message.source, 'source')
    if source not in HAND_COMMAND_SOURCES:
        raise ExportValidationError('HandCommand source is invalid')

    axis_values = tuple(
        _require_normalized(
            getattr(message, field_name),
            f'HandCommand {field_name}',
        )
        for field_name in HAND_COMMAND_AXES
    )
    speed_limit = _require_finite(
        message.speed_limit,
        'HandCommand speed_limit',
    )
    if not 0.0 < speed_limit <= 1.0:
        raise ExportValidationError(
            'HandCommand speed_limit must be greater than 0 and at most 1'
        )
    confidence = _require_normalized(
        message.confidence,
        'HandCommand confidence',
    )

    return (
        str(session_id),
        stamp_sec,
        stamp_nanosec,
        elapsed_ns // 1_000_000,
        sequence,
        source,
        *axis_values,
        speed_limit,
        confidence,
    )


def _require_integer(value: Any, field_name: str) -> int:
    """bool이 아닌 정수 필드만 반환한다."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExportValidationError(f'{field_name} must be an integer')
    return value


def _require_finite(value: Any, field_name: str) -> float:
    """유한한 숫자 필드를 float로 반환한다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExportValidationError(f'{field_name} must be numeric')
    converted = float(value)
    if not math.isfinite(converted):
        raise ExportValidationError(f'{field_name} must be finite')
    return converted


def _require_normalized(value: Any, field_name: str) -> float:
    """0.0부터 1.0까지의 유한한 정규화 값을 반환한다."""
    converted = _require_finite(value, field_name)
    if not 0.0 <= converted <= 1.0:
        raise ExportValidationError(
            f'{field_name} must be between 0 and 1'
        )
    return converted
