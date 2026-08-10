"""완료된 rosbag2 세션을 canonical 공개 파일로 변환한다."""

import csv
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import shutil
from typing import Any, Iterable, Iterator, Mapping

import rosbag2_py
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message

from thing_interfaces.msg import RecordingState
from thing_logger.bag_recorder import TOPIC_TYPES
from thing_logger.export_schema import canonical_filenames
from thing_logger.export_schema import HAND_COMMAND_HEADER
from thing_logger.export_schema import DATA_VERSION
from thing_logger.export_schema import INTERFACE_COMMIT
from thing_logger.export_schema import LANDMARK_POINT_FIELDS
from thing_logger.export_schema import LANDMARK_RECORD_FIELDS
from thing_logger.export_schema import METADATA_FIELDS
from thing_logger.export_schema import MOTOR_STATUS_HEADER
from thing_logger.export_schema import SCHEMA_VERSION


log = logging.getLogger('thing_logger.exporter')

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


class _PreSessionRecordError(ExportValidationError):
    """
    stamp가 세션 시작보다 이전인 레코드를 나타낸다.

    기록 시작 직전에 생성된 프레임·모터 상태가 파이프라인 지연으로 기록
    시작 이후에 도착하면 stamp는 세션 시작보다 이전이 된다. 파일의 선두
    레코드에서는 정상 상황이므로 건너뛰고, 정상 행이 나온 뒤 다시 나타나면
    시계 이상이므로 기존처럼 export를 중단한다. ExportValidationError의
    하위 타입이라 skip 처리를 빠뜨려도 기존과 같이 안전하게 실패한다.
    """


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

    def as_metadata(self) -> dict:
        """공개 파일 정보를 metadata JSON 형식으로 반환한다."""
        return {
            'filename': self.filename,
            'size_bytes': self.size_bytes,
            'row_count': self.row_count,
            'sha256': self.sha256,
        }


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


@dataclass
class _SessionLifecycle:
    """rosbag2 RecordingState에서 확인한 완료 세션 정보를 모은다."""

    expected_session_id: int
    session_id: int = 0
    started_at_ns: int = 0
    ended_at_ns: int = 0
    saw_recording: bool = False
    saw_stopping: bool = False

    def observe(self, message: Any) -> None:
        """기록 상태 하나를 세션 생명주기에 반영한다."""
        if message.state not in (
            RecordingState.RECORDING,
            RecordingState.STOPPING,
        ):
            return

        session_id = _require_integer(
            message.active_session_id,
            'RecordingState active_session_id',
        )
        if session_id != self.expected_session_id:
            raise ExportValidationError(
                'RecordingState session ID does not match bag path'
            )

        started_at_ns = _ros_time_to_ns(
            message.active_started_at,
            'RecordingState active_started_at',
        )
        if self.started_at_ns and self.started_at_ns != started_at_ns:
            raise ExportValidationError(
                'RecordingState session start changed inside bag'
            )
        self.session_id = session_id
        self.started_at_ns = started_at_ns

        if message.state == RecordingState.RECORDING:
            self.saw_recording = True
        if message.state == RecordingState.STOPPING:
            self.saw_stopping = True
            self.ended_at_ns = _ros_time_to_ns(
                message.header.stamp,
                'RecordingState stop timestamp',
            )

    def validate_completed(self) -> None:
        """정상 기록과 Stop 전이가 모두 존재하는지 확인한다."""
        if not self.saw_recording or not self.saw_stopping:
            raise ExportValidationError(
                'completed RecordingState lifecycle is missing'
            )
        if self.ended_at_ns <= self.started_at_ns:
            raise ExportValidationError(
                'RecordingState end must follow session start'
            )


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


class SessionExporter:
    """완료된 rosbag2를 검증된 canonical 4파일로 변환한다."""

    def __init__(
        self,
        robot_id: str,
        export_root: str,
        *,
        time_sync: bool = True,
        reader: Any = None,
    ) -> None:
        """로봇 정보, 임시 export 경로와 테스트 대역을 설정한다."""
        if not robot_id:
            raise ExportValidationError('robot ID is invalid')
        _require_bool(time_sync, 'time_sync')
        self.robot_id = robot_id
        self.export_root = Path(export_root)
        if not self.export_root.is_absolute():
            raise ExportValidationError('export root must be absolute')
        self.time_sync = time_sync
        self.reader = reader or RosbagSessionReader()
        self.cleanup_stale_exports()

    def export(self, job: ExportJob) -> ExportResult:
        """완료 세션을 변환하고 전체가 검증된 경우에만 노출한다."""
        bag_path = validate_export_job(job)
        session_id = self._session_id_from_path(bag_path)
        staging_directory = self.export_root / f'.{session_id}.part'
        final_directory = self.export_root / str(session_id)

        self.export_root.mkdir(parents=True, exist_ok=True)
        if final_directory.exists():
            raise ExportValidationError(
                'final export directory already exists'
            )
        self._remove_staging_directory(staging_directory, session_id)
        staging_directory.mkdir()

        try:
            row_counts, lifecycle = self._write_data_files(
                bag_path,
                staging_directory,
                session_id,
            )
            lifecycle.validate_completed()
            file_infos = self._validate_data_files(
                staging_directory,
                session_id,
                row_counts,
            )
            metadata = build_metadata(
                robot_id=self.robot_id,
                session_id=session_id,
                started_at_ns=lifecycle.started_at_ns,
                ended_at_ns=lifecycle.ended_at_ns,
                result=job.result,
                time_sync=self.time_sync,
                files=file_infos,
            )
            metadata_path = staging_directory / (
                canonical_filenames(session_id)['metadata'] + '.part'
            )
            write_metadata_json(metadata_path, metadata)
            self._sync_file(metadata_path)
            self._validate_metadata_file(metadata_path, metadata)
            self._commit_staging_files(staging_directory, session_id)
            self._sync_directory(staging_directory)
            os.replace(staging_directory, final_directory)
            self._sync_directory(self.export_root)
        except Exception:
            self._remove_staging_directory(staging_directory, session_id)
            raise

        final_files = {
            file_kind: ExportFileInfo(
                path=str(final_directory / info.filename),
                filename=info.filename,
                size_bytes=info.size_bytes,
                row_count=info.row_count,
                sha256=info.sha256,
            )
            for file_kind, info in file_infos.items()
        }
        metadata_final_path = final_directory / (
            canonical_filenames(session_id)['metadata']
        )
        final_files['metadata'] = inspect_export_file(
            metadata_final_path,
            row_count=1,
        )
        return ExportResult(
            session_id=session_id,
            directory=str(final_directory),
            content_digest=metadata['content_digest'],
            files=final_files,
        )

    def cleanup(self, result: ExportResult) -> None:
        """Remove only the four temporary files after uploader handoff."""
        if not isinstance(result, ExportResult):
            raise ExportValidationError('export result type is invalid')
        if result.session_id <= 0 or result.session_id >= 2**63:
            raise ExportValidationError('export session ID is invalid')

        directory = Path(result.directory)
        if not directory.is_absolute():
            raise ExportValidationError(
                'export cleanup directory must be absolute'
            )
        if directory.is_symlink():
            raise ExportValidationError(
                'export cleanup directory cannot be a symlink'
            )

        expected_directory = (
            self.export_root.resolve() / str(result.session_id)
        )
        if directory.resolve() != expected_directory:
            raise ExportValidationError(
                'export cleanup directory is outside export root'
            )
        if not directory.exists():
            return
        if not directory.is_dir():
            raise ExportValidationError(
                'export cleanup target is not a directory'
            )

        expected_names = set(
            canonical_filenames(result.session_id).values()
        )
        children = list(directory.iterdir())
        if {child.name for child in children} != expected_names:
            raise ExportValidationError(
                'export cleanup directory contents are invalid'
            )
        if any(child.is_symlink() or not child.is_file() for child in children):
            raise ExportValidationError(
                'export cleanup target contains a non-regular file'
            )

        shutil.rmtree(directory)
        self._sync_directory(self.export_root)

    def cleanup_stale_exports(self) -> None:
        """Remove safe leftover export directories from a previous run."""
        if self.export_root.is_symlink():
            raise ExportValidationError('export root cannot be a symlink')
        if not self.export_root.exists():
            return
        if not self.export_root.is_dir():
            raise ExportValidationError('export root is not a directory')

        removed = False
        for path in self.export_root.iterdir():
            stale_entry = self._parse_stale_entry(path.name)
            if stale_entry is None:
                continue
            session_id, is_staging = stale_entry

            if path.is_symlink():
                path.unlink()
                removed = True
                continue
            if not path.is_dir():
                continue
            if is_staging:
                safe_to_remove = self._is_safe_staging_directory(
                    path,
                    session_id,
                )
            else:
                safe_to_remove = self._is_safe_final_directory(
                    path,
                    session_id,
                )
            if safe_to_remove:
                shutil.rmtree(path)
                removed = True

        if removed:
            self._sync_directory(self.export_root)

    def _write_data_files(
        self,
        bag_path: Path,
        directory: Path,
        session_id: int,
    ) -> tuple:
        """rosbag2를 한 번 순회해 세 데이터 .part 파일을 작성한다."""
        filenames = canonical_filenames(session_id)
        hand_path = directory / (filenames['hand_command'] + '.part')
        motor_path = directory / (filenames['motor_status'] + '.part')
        landmark_path = directory / (filenames['landmark'] + '.part')
        lifecycle = _SessionLifecycle(session_id)
        counts = {'hand_command': 0, 'motor_status': 0, 'landmark': 0}
        skipped = {'hand_command': 0, 'motor_status': 0, 'landmark': 0}
        last_csv_timestamps = {
            'hand_command': None,
            'motor_status': None,
        }

        with ExitStack() as stack:
            hand_output = stack.enter_context(
                hand_path.open('w', encoding='utf-8', newline='')
            )
            motor_output = stack.enter_context(
                motor_path.open('w', encoding='utf-8', newline='')
            )
            landmark_output = stack.enter_context(
                landmark_path.open('w', encoding='utf-8', newline='')
            )
            hand_writer = csv.writer(hand_output, lineterminator='\n')
            motor_writer = csv.writer(motor_output, lineterminator='\n')
            hand_writer.writerow(HAND_COMMAND_HEADER)
            motor_writer.writerow(MOTOR_STATUS_HEADER)
            landmark_output.write('[\n')

            for record in self.reader.iter_records(bag_path):
                if record.topic_name == '/thing/recording_state':
                    lifecycle.observe(record.message)
                    continue
                if lifecycle.started_at_ns == 0:
                    raise ExportValidationError(
                        'data appeared before RecordingState.RECORDING'
                    )
                if record.topic_name == '/thing/command':
                    try:
                        row = _hand_command_row(
                            session_id,
                            lifecycle.started_at_ns,
                            record.message,
                        )
                    except _PreSessionRecordError:
                        # 기록 시작 직전에 생성돼 늦게 도착한 선두
                        # 레코드만 건너뛴다. 정상 행 이후는 시계 이상이다.
                        if counts['hand_command'] == 0:
                            skipped['hand_command'] += 1
                            continue
                        raise
                    last_csv_timestamps['hand_command'] = (
                        _require_nondecreasing_csv_timestamp(
                            row[1],
                            row[2],
                            last_csv_timestamps['hand_command'],
                            'HandCommand',
                        )
                    )
                    hand_writer.writerow(row)
                    counts['hand_command'] += 1
                elif record.topic_name == '/thing/motor_status':
                    try:
                        rows = _motor_status_rows(
                            session_id,
                            lifecycle.started_at_ns,
                            record.message,
                        )
                    except _PreSessionRecordError:
                        if counts['motor_status'] == 0:
                            skipped['motor_status'] += 1
                            continue
                        raise
                    last_csv_timestamps['motor_status'] = (
                        _require_nondecreasing_csv_timestamp(
                            rows[0][1],
                            rows[0][2],
                            last_csv_timestamps['motor_status'],
                            'MotorStatus',
                        )
                    )
                    motor_writer.writerows(rows)
                    counts['motor_status'] += len(rows)
                elif record.topic_name == '/thing/landmarks':
                    try:
                        landmark_record = _landmark_record(
                            session_id,
                            lifecycle.started_at_ns,
                            record.message,
                        )
                    except _PreSessionRecordError:
                        if counts['landmark'] == 0:
                            skipped['landmark'] += 1
                            continue
                        raise
                    if counts['landmark']:
                        landmark_output.write(',\n')
                    landmark_output.write(json.dumps(
                        landmark_record,
                        ensure_ascii=False,
                        separators=(',', ':'),
                    ))
                    counts['landmark'] += 1
            landmark_output.write(
                '\n]\n' if counts['landmark'] else ']\n'
            )

        for file_kind, skip_count in skipped.items():
            if skip_count:
                log.warning(
                    '%s: 세션 시작 이전 선두 레코드 %d건을 제외했다',
                    file_kind,
                    skip_count,
                )

        for path in (hand_path, motor_path, landmark_path):
            self._sync_file(path)
        return counts, lifecycle

    @staticmethod
    def _validate_data_files(
        directory: Path,
        session_id: int,
        row_counts: Mapping[str, int],
    ) -> dict:
        """세 데이터 파일을 다시 parse하고 hash 정보를 반환한다."""
        filenames = canonical_filenames(session_id)
        paths = {
            file_kind: directory / (filenames[file_kind] + '.part')
            for file_kind in ('hand_command', 'motor_status', 'landmark')
        }
        SessionExporter._validate_csv(
            paths['hand_command'],
            HAND_COMMAND_HEADER,
            row_counts['hand_command'],
        )
        SessionExporter._validate_csv(
            paths['motor_status'],
            MOTOR_STATUS_HEADER,
            row_counts['motor_status'],
        )
        SessionExporter._validate_landmark_json(
            paths['landmark'],
            row_counts['landmark'],
        )
        return {
            file_kind: inspect_export_file(path, row_counts[file_kind])
            for file_kind, path in paths.items()
        }

    @staticmethod
    def _validate_csv(
        path: Path,
        expected_header: tuple,
        expected_rows: int,
    ) -> None:
        """CSV 헤더와 실제 데이터 행 수를 다시 검사한다."""
        try:
            with path.open(encoding='utf-8', newline='') as source:
                reader = csv.reader(source)
                header = next(reader, None)
                actual_rows = sum(1 for _ in reader)
        except Exception as error:
            raise ExportValidationError(
                f'failed to parse CSV: {error}'
            ) from error
        if header != list(expected_header) or actual_rows != expected_rows:
            raise ExportValidationError('CSV header or row count mismatch')

    @staticmethod
    def _validate_landmark_json(path: Path, expected_rows: int) -> None:
        """손 좌표 JSON 배열과 레코드 수·필드·좌표 수를 검사한다."""
        try:
            with path.open(encoding='utf-8') as source:
                if source.readline() != '[\n':
                    raise ExportValidationError(
                        'LandMark JSON opening bracket is invalid'
                    )
                actual_rows = 0
                for line in source:
                    stripped = line.strip()
                    if stripped == ']':
                        break
                    if stripped.endswith(','):
                        stripped = stripped[:-1]
                    record = json.loads(stripped)
                    if tuple(record) != LANDMARK_RECORD_FIELDS:
                        raise ExportValidationError(
                            'LandMark JSON field order mismatch'
                        )
                    if len(record['landmarks']) != 21:
                        raise ExportValidationError(
                            'LandMark JSON point count mismatch'
                        )
                    actual_rows += 1
                else:
                    raise ExportValidationError(
                        'LandMark JSON closing bracket is missing'
                    )
        except Exception as error:
            if isinstance(error, ExportValidationError):
                raise
            raise ExportValidationError(
                f'failed to parse LandMark JSON: {error}'
            ) from error
        if actual_rows != expected_rows:
            raise ExportValidationError('LandMark JSON row count mismatch')

    @staticmethod
    def _validate_metadata_file(path: Path, expected: Mapping) -> None:
        """기록한 metadata를 다시 parse해 내용과 digest를 검사한다."""
        try:
            parsed = json.loads(path.read_text(encoding='utf-8'))
        except Exception as error:
            raise ExportValidationError(
                f'failed to parse metadata JSON: {error}'
            ) from error
        if parsed != expected:
            raise ExportValidationError('metadata JSON mismatch')
        if parsed['content_digest'] != calculate_content_digest(parsed):
            raise ExportValidationError('metadata content digest mismatch')

    @staticmethod
    def _commit_staging_files(directory: Path, session_id: int) -> None:
        """검증된 네 .part 파일을 staging 안의 최종 이름으로 바꾼다."""
        for filename in canonical_filenames(session_id).values():
            os.replace(
                directory / (filename + '.part'),
                directory / filename,
            )

    @staticmethod
    def _sync_file(path: Path) -> None:
        """완성 후보 파일 내용을 디스크에 반영한다."""
        with path.open('rb') as source:
            os.fsync(source.fileno())

    @staticmethod
    def _sync_directory(path: Path) -> None:
        """파일명 또는 디렉터리 rename 정보를 디스크에 반영한다."""
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _remove_staging_directory(
        cls,
        path: Path,
        session_id: int,
    ) -> None:
        """현재 세션의 미완성 staging 디렉터리만 안전하게 정리한다."""
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            if not path.is_dir() or not cls._is_safe_staging_directory(
                path,
                session_id,
            ):
                raise ExportValidationError(
                    'staging directory contents are invalid'
                )
            shutil.rmtree(path)

    @staticmethod
    def _parse_stale_entry(name: str):
        """임시 export 항목 이름에서 Session ID와 단계를 읽는다."""
        is_staging = name.startswith('.') and name.endswith('.part')
        session_text = name[1:-5] if is_staging else name
        if not session_text.isdigit():
            return None
        session_id = int(session_text)
        if session_id <= 0 or session_id >= 2**63:
            return None
        return session_id, is_staging

    @staticmethod
    def _is_safe_staging_directory(path: Path, session_id: int) -> bool:
        """작성 중 디렉터리가 exporter 소유 파일만 갖는지 확인한다."""
        filenames = set(canonical_filenames(session_id).values())
        allowed_names = filenames | {
            filename + '.part' for filename in filenames
        }
        children = list(path.iterdir())
        return (
            {child.name for child in children} <= allowed_names
            and all(
                not child.is_symlink() and child.is_file()
                for child in children
            )
        )

    @staticmethod
    def _is_safe_final_directory(path: Path, session_id: int) -> bool:
        """완료 디렉터리가 정확한 canonical 네 파일만 갖는지 확인한다."""
        expected_names = set(canonical_filenames(session_id).values())
        children = list(path.iterdir())
        return (
            {child.name for child in children} == expected_names
            and all(
                not child.is_symlink() and child.is_file()
                for child in children
            )
        )

    @staticmethod
    def _session_id_from_path(bag_path: Path) -> int:
        """세션 bag 디렉터리 이름에서 유효한 Session ID를 읽는다."""
        try:
            session_id = int(bag_path.name)
        except ValueError as error:
            raise ExportValidationError(
                'bag directory name must be a Session ID'
            ) from error
        if session_id <= 0 or session_id >= 2**63:
            raise ExportValidationError('session ID is invalid')
        return session_id


def write_hand_command_csv(
    path: Path,
    session_id: int,
    started_at_ns: int,
    messages: Iterable[Any],
) -> int:
    """명령 메시지를 canonical CSV로 쓰고 행 수를 반환한다."""
    row_count = 0
    skipped_count = 0
    last_timestamp_ns = None
    try:
        with path.open('w', encoding='utf-8', newline='') as output:
            writer = csv.writer(output, lineterminator='\n')
            writer.writerow(HAND_COMMAND_HEADER)
            for message in messages:
                try:
                    row = _hand_command_row(
                        session_id,
                        started_at_ns,
                        message,
                    )
                except _PreSessionRecordError:
                    # 기록 시작 직전에 생성돼 늦게 도착한 선두 레코드만
                    # 건너뛴다. 정상 행 이후의 과거 stamp는 시계 이상이다.
                    if row_count == 0:
                        skipped_count += 1
                        continue
                    raise
                last_timestamp_ns = _require_nondecreasing_csv_timestamp(
                    row[1],
                    row[2],
                    last_timestamp_ns,
                    'HandCommand',
                )
                writer.writerow(row)
                row_count += 1
    except ExportValidationError:
        raise
    except Exception as error:
        raise ExportError(
            f'failed to write HandCommand CSV: {error}'
        ) from error

    if skipped_count:
        log.warning(
            'HandCommand: 세션 시작 이전 선두 레코드 %d건을 제외했다',
            skipped_count,
        )
    return row_count


def _hand_command_row(
    session_id: int,
    started_at_ns: int,
    message: Any,
) -> tuple:
    """명령 하나를 검증해 canonical CSV 행으로 변환한다."""
    stamp_sec, stamp_nanosec, elapsed_ms = _timestamp_parts(
        message.stamp,
        started_at_ns,
        'HandCommand',
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
        elapsed_ms,
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
    skipped_count = 0
    last_timestamp_ns = None
    try:
        with path.open('w', encoding='utf-8', newline='') as output:
            writer = csv.writer(output, lineterminator='\n')
            writer.writerow(MOTOR_STATUS_HEADER)
            for message in messages:
                try:
                    rows = _motor_status_rows(
                        session_id,
                        started_at_ns,
                        message,
                    )
                except _PreSessionRecordError:
                    # 기록 시작 직전에 측정돼 늦게 도착한 선두 상태만
                    # 건너뛴다. 정상 행 이후의 과거 stamp는 시계 이상이다.
                    if row_count == 0:
                        skipped_count += 1
                        continue
                    raise
                last_timestamp_ns = _require_nondecreasing_csv_timestamp(
                    rows[0][1],
                    rows[0][2],
                    last_timestamp_ns,
                    'MotorStatus',
                )
                writer.writerows(rows)
                row_count += len(rows)
    except ExportValidationError:
        raise
    except Exception as error:
        raise ExportError(
            f'failed to write MotorStatus CSV: {error}'
        ) from error

    if skipped_count:
        log.warning(
            'MotorStatus: 세션 시작 이전 선두 메시지 %d건을 제외했다',
            skipped_count,
        )
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
            _canonical_bool(
                motor.torque_enabled,
                'MotorState torque_enabled',
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
    skipped_count = 0
    try:
        with path.open('w', encoding='utf-8', newline='') as output:
            output.write('[\n')
            for message in messages:
                try:
                    record = _landmark_record(
                        session_id,
                        started_at_ns,
                        message,
                    )
                except _PreSessionRecordError:
                    # 기록 시작 직전에 촬영돼 늦게 도착한 선두 프레임만
                    # 건너뛴다. 정상 행 이후의 과거 stamp는 시계 이상이다.
                    if row_count == 0:
                        skipped_count += 1
                        continue
                    raise
                if row_count:
                    output.write(',\n')
                output.write(json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(',', ':'),
                ))
                row_count += 1
            output.write('\n]\n' if row_count else ']\n')
    except ExportValidationError:
        raise
    except Exception as error:
        raise ExportError(
            f'failed to write LandMark JSON: {error}'
        ) from error

    if skipped_count:
        log.warning(
            'HandLandmarks: 세션 시작 이전 선두 프레임 %d건을 제외했다',
            skipped_count,
        )
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
        raise _PreSessionRecordError(
            f'{message_name} timestamp precedes session start'
        )
    return stamp_sec, stamp_nanosec, elapsed_ns // 1_000_000


def _require_nondecreasing_csv_timestamp(
    stamp_sec: int,
    stamp_nanosec: int,
    previous_timestamp_ns,
    message_name: str,
) -> int:
    """CSV 메시지 시각이 이전 시각보다 빠르지 않은지 검사한다."""
    timestamp_ns = stamp_sec * 1_000_000_000 + stamp_nanosec
    if (
        previous_timestamp_ns is not None
        and timestamp_ns < previous_timestamp_ns
    ):
        raise ExportValidationError(
            f'{message_name} timestamp must be nondecreasing'
        )
    return timestamp_ns


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


def inspect_export_file(path: Path, row_count: int) -> ExportFileInfo:
    """완성 후보 파일의 크기·행 수·SHA-256 정보를 계산한다."""
    if row_count < 0:
        raise ExportValidationError('row count cannot be negative')
    if not path.is_file():
        raise ExportValidationError('export file does not exist')

    hasher = hashlib.sha256()
    size_bytes = 0
    try:
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                hasher.update(chunk)
                size_bytes += len(chunk)
    except Exception as error:
        raise ExportError(f'failed to hash export file: {error}') from error

    return ExportFileInfo(
        path=str(path),
        filename=path.name.removesuffix('.part'),
        size_bytes=size_bytes,
        row_count=row_count,
        sha256=hasher.hexdigest(),
    )


def build_metadata(
    *,
    robot_id: str,
    session_id: int,
    started_at_ns: int,
    ended_at_ns: int,
    result: str,
    time_sync: bool,
    files: Mapping[str, ExportFileInfo],
) -> dict:
    """세션 정보와 세 데이터 파일 정보로 canonical metadata를 만든다."""
    if not robot_id or not isinstance(robot_id, str):
        raise ExportValidationError('robot ID is invalid')
    if session_id <= 0 or session_id >= 2**63:
        raise ExportValidationError('session ID is invalid')
    if ended_at_ns <= started_at_ns:
        raise ExportValidationError('session end must follow session start')
    if result not in ALLOWED_RESULTS:
        raise ExportValidationError(
            'result must be SUCCESS or FAILURE'
        )
    _require_bool(time_sync, 'time_sync')

    expected_file_kinds = {'hand_command', 'motor_status', 'landmark'}
    if set(files) != expected_file_kinds:
        raise ExportValidationError(
            'metadata requires hand_command, motor_status, and landmark files'
        )

    ordered_files = {
        file_kind: files[file_kind].as_metadata()
        for file_kind in ('hand_command', 'motor_status', 'landmark')
    }
    metadata = {
        'schema_version': SCHEMA_VERSION,
        'data_version': DATA_VERSION,
        'robot_id': robot_id,
        'session_id': str(session_id),
        'started_at': _format_ns_utc(started_at_ns),
        'ended_at': _format_ns_utc(ended_at_ns),
        'result': result,
        'interface_commit': INTERFACE_COMMIT,
        'time_sync': time_sync,
        'content_digest': '',
        'files': ordered_files,
    }
    metadata['content_digest'] = calculate_content_digest(metadata)

    if tuple(metadata) != METADATA_FIELDS:
        raise ExportValidationError(
            'metadata field order does not match schema'
        )
    return metadata


def calculate_content_digest(metadata: Mapping[str, Any]) -> str:
    """Digest 자신을 제외한 canonical metadata의 SHA-256을 만든다."""
    digest_payload = {
        key: value
        for key, value in metadata.items()
        if key != 'content_digest'
    }
    canonical_bytes = json.dumps(
        digest_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return 'sha256:' + hashlib.sha256(canonical_bytes).hexdigest()


def write_metadata_json(path: Path, metadata: Mapping[str, Any]) -> None:
    """metadata를 고정 필드 순서의 UTF-8 JSON 파일로 기록한다."""
    if tuple(metadata) != METADATA_FIELDS:
        raise ExportValidationError(
            'metadata field order does not match schema'
        )
    try:
        path.write_text(
            json.dumps(
                metadata,
                ensure_ascii=False,
                separators=(',', ':'),
            ) + '\n',
            encoding='utf-8',
        )
    except Exception as error:
        raise ExportError(f'failed to write metadata JSON: {error}') from error


def _format_ns_utc(timestamp_ns: int) -> str:
    """나노초 UTC epoch를 millisecond RFC 3339 문자열로 변환한다."""
    if not isinstance(timestamp_ns, int) or timestamp_ns < 0:
        raise ExportValidationError('UTC timestamp is invalid')
    stamp_sec, stamp_nanosec = divmod(timestamp_ns, 1_000_000_000)
    return _format_timestamp_utc(stamp_sec, stamp_nanosec)


def _ros_time_to_ns(stamp: Any, field_name: str) -> int:
    """ROS Time 필드를 검증해 epoch nanoseconds로 반환한다."""
    stamp_sec = _require_integer(stamp.sec, f'{field_name}.sec')
    stamp_nanosec = _require_integer(
        stamp.nanosec,
        f'{field_name}.nanosec',
    )
    if stamp_sec < 0 or not 0 <= stamp_nanosec < 1_000_000_000:
        raise ExportValidationError(f'{field_name} is invalid')
    return stamp_sec * 1_000_000_000 + stamp_nanosec
