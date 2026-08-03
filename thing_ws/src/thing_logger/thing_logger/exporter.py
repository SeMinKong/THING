"""완료된 rosbag2 세션을 canonical 공개 파일로 변환한다."""

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

import rosbag2_py
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message

from thing_logger.bag_recorder import TOPIC_TYPES
from thing_logger.export_schema import HAND_COMMAND_HEADER
from thing_logger.export_schema import LANDMARK_POINT_FIELDS
from thing_logger.export_schema import LANDMARK_RECORD_FIELDS
from thing_logger.export_schema import MOTOR_STATUS_HEADER


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
HANDEDNESS_VALUES = frozenset(range(3))


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


def write_motor_status_csv(
    path: Path,
    session_id: int,
    started_at_ns: int,
    messages: Iterable[Any],
) -> int:
    """모터 상태를 모터별 canonical CSV 행으로 쓰고 행 수를 반환한다."""
    row_count = 0
    try:
        with path.open('w', encoding='utf-8', newline='') as output:
            writer = csv.writer(output, lineterminator='\n')
            writer.writerow(MOTOR_STATUS_HEADER)
            for message in messages:
                rows = _motor_status_rows(
                    session_id,
                    started_at_ns,
                    message,
                )
                writer.writerows(rows)
                row_count += len(rows)
    except ExportValidationError:
        raise
    except Exception as error:
        raise ExportError(
            f'failed to write MotorStatus CSV: {error}'
        ) from error

    return row_count


def _motor_status_rows(
    session_id: int,
    started_at_ns: int,
    message: Any,
) -> list:
    """모터 상태 메시지 하나를 검증해 일곱 CSV 행으로 평탄화한다."""
    stamp_sec, stamp_nanosec, elapsed_ms = _timestamp_parts(
        message.header.stamp,
        started_at_ns,
        'MotorStatus',
    )
    motors = list(message.motors)
    if len(motors) != 7:
        raise ExportValidationError(
            'MotorStatus must contain exactly 7 motors'
        )

    motor_ids = [
        _require_integer(motor.motor_id, 'MotorState motor_id')
        for motor in motors
    ]
    if len(set(motor_ids)) != 7:
        raise ExportValidationError('MotorStatus motor IDs must be unique')

    failed_read_count = _require_integer(
        message.failed_read_count,
        'MotorStatus failed_read_count',
    )
    if not 0 <= failed_read_count < 2**32:
        raise ExportValidationError(
            'MotorStatus failed_read_count is invalid'
        )

    rows = []
    for motor, motor_id in zip(motors, motor_ids):
        if not 0 <= motor_id < 2**8:
            raise ExportValidationError('MotorState motor_id is invalid')
        if not isinstance(motor.actuator_name, str):
            raise ExportValidationError(
                'MotorState actuator_name must be a string'
            )

        rows.append((
            str(session_id),
            stamp_sec,
            stamp_nanosec,
            elapsed_ms,
            str(message.header.frame_id),
            motor_id,
            motor.actuator_name,
            _require_int32(
                motor.goal_position_raw,
                'MotorState goal_position_raw',
            ),
            _require_int32(
                motor.present_position_raw,
                'MotorState present_position_raw',
            ),
            _require_finite(
                motor.goal_position_rad,
                'MotorState goal_position_rad',
            ),
            _require_finite(
                motor.present_position_rad,
                'MotorState present_position_rad',
            ),
            _require_finite(
                motor.velocity_rad_s,
                'MotorState velocity_rad_s',
            ),
            _require_finite(
                motor.current_ampere,
                'MotorState current_ampere',
            ),
            _require_finite(
                motor.voltage_volt,
                'MotorState voltage_volt',
            ),
            _require_finite(
                motor.temperature_celsius,
                'MotorState temperature_celsius',
            ),
            _require_uint32(
                motor.hardware_error,
                'MotorState hardware_error',
            ),
            _require_int32(
                motor.communication_result,
                'MotorState communication_result',
            ),
            _canonical_bool(
                motor.communication_ok,
                'MotorState communication_ok',
            ),
            _canonical_bool(
                message.bus_communication_ok,
                'MotorStatus bus_communication_ok',
            ),
            failed_read_count,
        ))

    return rows


def write_landmark_json(
    path: Path,
    session_id: int,
    started_at_ns: int,
    messages: Iterable[Any],
) -> int:
    """손 좌표 메시지를 canonical JSON 배열로 쓰고 행 수를 반환한다."""
    row_count = 0
    try:
        with path.open('w', encoding='utf-8', newline='') as output:
            output.write('[')
            for message in messages:
                if row_count:
                    output.write(',')
                record = _landmark_record(
                    session_id,
                    started_at_ns,
                    message,
                )
                output.write(json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(',', ':'),
                ))
                row_count += 1
            output.write(']\n')
    except ExportValidationError:
        raise
    except Exception as error:
        raise ExportError(
            f'failed to write LandMark JSON: {error}'
        ) from error

    return row_count


def _landmark_record(
    session_id: int,
    started_at_ns: int,
    message: Any,
) -> dict:
    """손 좌표 메시지 하나를 검증해 canonical JSON 객체로 변환한다."""
    stamp_sec, stamp_nanosec, elapsed_ms = _timestamp_parts(
        message.header.stamp,
        started_at_ns,
        'HandLandmarks',
    )
    confidence = _require_normalized(
        message.confidence,
        'HandLandmarks confidence',
    )
    handedness = _require_integer(
        message.handedness,
        'HandLandmarks handedness',
    )
    if handedness not in HANDEDNESS_VALUES:
        raise ExportValidationError(
            'HandLandmarks handedness is invalid'
        )
    handedness_confidence = _require_normalized(
        message.handedness_confidence,
        'HandLandmarks handedness_confidence',
    )
    image_width = _require_uint32(
        message.image_width,
        'HandLandmarks image_width',
    )
    image_height = _require_uint32(
        message.image_height,
        'HandLandmarks image_height',
    )
    if image_width == 0 or image_height == 0:
        raise ExportValidationError(
            'HandLandmarks image dimensions must be positive'
        )

    landmarks = list(message.landmarks)
    if len(landmarks) != 21:
        raise ExportValidationError(
            'HandLandmarks must contain exactly 21 points'
        )
    points = [
        {
            field_name: _require_finite(
                getattr(point, field_name),
                f'HandLandmarks point {field_name}',
            )
            for field_name in LANDMARK_POINT_FIELDS
        }
        for point in landmarks
    ]

    record = {
        'session_id': str(session_id),
        'timestamp': _format_timestamp_utc(stamp_sec, stamp_nanosec),
        'stamp_sec': stamp_sec,
        'stamp_nanosec': stamp_nanosec,
        'elapsed_ms': elapsed_ms,
        'detected': _require_bool(
            message.detected,
            'HandLandmarks detected',
        ),
        'confidence': confidence,
        'handedness': handedness,
        'handedness_confidence': handedness_confidence,
        'image_width': image_width,
        'image_height': image_height,
        'landmarks': points,
    }
    if tuple(record) != LANDMARK_RECORD_FIELDS:
        raise ExportValidationError(
            'LandMark JSON field order does not match schema'
        )
    return record


def _timestamp_parts(
    stamp: Any,
    started_at_ns: int,
    message_name: str,
) -> tuple:
    """ROS 시각을 검증하고 초·나노초·세션 상대 밀리초로 반환한다."""
    stamp_sec = _require_integer(stamp.sec, f'{message_name} stamp.sec')
    stamp_nanosec = _require_integer(
        stamp.nanosec,
        f'{message_name} stamp.nanosec',
    )
    if stamp_sec < 0 or not 0 <= stamp_nanosec < 1_000_000_000:
        raise ExportValidationError(f'{message_name} timestamp is invalid')

    timestamp_ns = stamp_sec * 1_000_000_000 + stamp_nanosec
    elapsed_ns = timestamp_ns - started_at_ns
    if elapsed_ns < 0:
        raise ExportValidationError(
            f'{message_name} timestamp precedes session start'
        )
    return stamp_sec, stamp_nanosec, elapsed_ns // 1_000_000


def _format_timestamp_utc(stamp_sec: int, stamp_nanosec: int) -> str:
    """ROS 시각을 millisecond 정밀도의 RFC 3339 UTC 문자열로 만든다."""
    try:
        utc_time = datetime.fromtimestamp(stamp_sec, timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise ExportValidationError('timestamp is outside UTC range') from error
    milliseconds = stamp_nanosec // 1_000_000
    return f'{utc_time:%Y-%m-%dT%H:%M:%S}.{milliseconds:03d}Z'


def _require_bool(value: Any, field_name: str) -> bool:
    """실제 boolean 필드만 반환한다."""
    if not isinstance(value, bool):
        raise ExportValidationError(f'{field_name} must be boolean')
    return value


def _canonical_bool(value: Any, field_name: str) -> str:
    """boolean을 CSV용 소문자 문자열로 변환한다."""
    return 'true' if _require_bool(value, field_name) else 'false'


def _require_int32(value: Any, field_name: str) -> int:
    """ROS int32 범위의 정수만 반환한다."""
    converted = _require_integer(value, field_name)
    if not -(2**31) <= converted < 2**31:
        raise ExportValidationError(f'{field_name} is outside int32 range')
    return converted


def _require_uint32(value: Any, field_name: str) -> int:
    """ROS uint32 범위의 정수만 반환한다."""
    converted = _require_integer(value, field_name)
    if not 0 <= converted < 2**32:
        raise ExportValidationError(f'{field_name} is outside uint32 range')
    return converted
