"""ROS 2 메시지를 세션별 rosbag2 원본으로 기록하는 모듈."""

import gc
from pathlib import Path
import shutil
from threading import RLock
from typing import Optional

import rosbag2_py
from rclpy.serialization import serialize_message


# V7.0 명세에서 정한 필수 기록 토픽
TOPIC_TYPES = {
    '/thing/landmarks': 'thing_interfaces/msg/HandLandmarks',
    '/thing/command': 'thing_interfaces/msg/HandCommand',
    '/thing/motor_status': 'thing_interfaces/msg/MotorStatus',
    '/thing/control_state': 'thing_interfaces/msg/ControlState',
    '/thing/safety_state': 'thing_interfaces/msg/SafetyState',
    '/thing/recording_state': 'thing_interfaces/msg/RecordingState',
}


class BagRecorderError(RuntimeError):
    """rosbag2 기록 과정에서 발생한 오류를 나타낸다."""


class BagRecorder:
    """하나의 녹화 세션을 rosbag2에 기록한다."""

    def __init__(self) -> None:
        """기록하지 않는 초기 상태를 생성한다."""
        # 현재 열린 rosbag2 writer
        self._writer: Optional[rosbag2_py.SequentialWriter] = None

        # 현재 기록 중인 rosbag2 경로
        self._bag_path: Optional[str] = None

        # 현재 writer에 등록된 토픽 이름
        self._registered_topics = set()

        # write와 종료 처리가 동시에 writer에 접근하지 못하도록 보호한다.
        self._lock = RLock()

    @property
    def is_recording(self) -> bool:
        """현재 rosbag2 writer가 열려 있는지 반환한다."""
        with self._lock:
            return self._writer is not None

    @property
    def bag_path(self) -> Optional[str]:
        """현재 기록 중인 rosbag2 경로를 반환한다."""
        with self._lock:
            return self._bag_path

    def start(self, bag_path: str) -> None:
        """새 rosbag2 writer를 열고 필수 토픽을 등록한다."""
        with self._lock:
            self._validate_start(bag_path)
            path = Path(bag_path)

            # 세션별 bag 디렉터리는 writer가 생성하므로 상위 경로만 만든다.
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            try:
                writer = self._open_writer(path)
            except Exception:
                # 시작 도중 만들어진 불완전한 bag 경로를 제거한다.
                self._remove_bag_safely(path)
                raise

            try:
                self._register_topics(writer)
            except Exception as error:
                # 토픽 등록에 실패한 writer와 불완전한 bag을 정리한다.
                self._release_writer_safely(writer)
                self._remove_bag_safely(path)
                raise BagRecorderError(
                    f'필수 토픽 등록 실패: {error}'
                ) from error

            # writer 시작과 토픽 등록이 모두 성공한 뒤 기록 상태를 저장한다.
            self._writer = writer
            self._bag_path = str(path)
            self._registered_topics = set(TOPIC_TYPES)

    def write(
        self,
        topic_name: str,
        message,
        timestamp_ns: int,
    ) -> None:
        """등록된 토픽 메시지를 지정한 시각으로 기록한다."""
        with self._lock:
            self._validate_write(
                topic_name,
                timestamp_ns,
            )

            try:
                serialized_message = serialize_message(message)
                self._writer.write(
                    topic_name,
                    serialized_message,
                    timestamp_ns,
                )
            except Exception as error:
                raise BagRecorderError(
                    f'rosbag2 메시지 기록 실패: {error}'
                ) from error

    def stop(self) -> str:
        """정상 Stop 요청으로 rosbag2를 종료하고 bag을 보존한다."""
        return self._close(remove_bag=False)

    def interrupt(self) -> str:
        """rosbag2를 중단하고 중단된 bag을 삭제한다."""
        return self._close(remove_bag=True)

    def _validate_start(self, bag_path: str) -> None:
        """새로운 rosbag2 기록을 시작할 수 있는지 확인한다."""
        if self._writer is not None:
            raise BagRecorderError('이미 기록 중이다')

        if not bag_path:
            raise BagRecorderError('bag 경로가 비어 있다')

        if Path(bag_path).exists():
            raise BagRecorderError('bag 경로가 이미 존재한다')

    def _open_writer(
        self,
        path: Path,
    ) -> rosbag2_py.SequentialWriter:
        """지정한 경로에 SequentialWriter를 연다."""
        storage_options = rosbag2_py.StorageOptions(
            uri=str(path),
            storage_id='sqlite3',
        )
        converter_options = rosbag2_py.ConverterOptions(
            input_serialization_format='cdr',
            output_serialization_format='cdr',
        )
        writer = rosbag2_py.SequentialWriter()

        try:
            writer.open(
                storage_options,
                converter_options,
            )
        except Exception as error:
            raise BagRecorderError(
                f'rosbag2 writer 시작 실패: {error}'
            ) from error

        return writer

    def _register_topics(
        self,
        writer: rosbag2_py.SequentialWriter,
    ) -> None:
        """V7.0 명세에서 정한 필수 토픽을 writer에 등록한다."""
        for topic_name, topic_type in TOPIC_TYPES.items():
            metadata = rosbag2_py.TopicMetadata(
                name=topic_name,
                type=topic_type,
                serialization_format='cdr',
            )
            writer.create_topic(metadata)

    def _validate_write(
        self,
        topic_name: str,
        timestamp_ns: int,
    ) -> None:
        """현재 메시지를 rosbag2에 기록할 수 있는지 확인한다."""
        if self._writer is None:
            raise BagRecorderError('기록 중이 아니다')

        if topic_name not in self._registered_topics:
            raise BagRecorderError(
                f'등록되지 않은 토픽이다: {topic_name}'
            )

        if timestamp_ns < 0:
            raise BagRecorderError('timestamp가 유효하지 않다')

    def _close(self, remove_bag: bool) -> str:
        """writer를 종료하고 내부 기록 상태를 초기화한다."""
        with self._lock:
            if self._writer is None:
                raise BagRecorderError('기록 중이 아니다')

            if self._bag_path is None:
                raise BagRecorderError('현재 bag 경로가 존재하지 않는다')

            writer = self._writer
            closed_bag_path = self._bag_path

            # 종료를 시작한 뒤 새로운 write가 실행되지 않도록 상태를 비운다.
            self._writer = None
            self._bag_path = None
            self._registered_topics.clear()

            try:
                writer = self._release_writer(writer)
            except Exception as error:
                # close 실패 시에도 Python 참조를 제거해 정리를 시도한다.
                writer = None
                gc.collect()

                # 정상 Stop도 close에 실패하면 완전한 bag으로 볼 수 없다.
                # 모든 종료 실패 경로에서 불완전한 bag 삭제를 시도한다.
                self._remove_bag_safely(
                    Path(closed_bag_path)
                )

                raise BagRecorderError(
                    f'rosbag2 writer 종료 실패: {error}'
                ) from error

            # 명시적 close가 없는 환경에서도 소멸자가 실행되게 한다.
            gc.collect()

            if remove_bag:
                self._remove_bag(
                    Path(closed_bag_path)
                )

            return closed_bag_path

    @staticmethod
    def _release_writer(
        writer: rosbag2_py.SequentialWriter,
    ) -> None:
        """지원되는 방식으로 rosbag2 writer를 종료한다."""
        close_method = getattr(
            writer,
            'close',
            None,
        )

        if callable(close_method):
            close_method()

        # close가 없는 환경에서는 호출한 쪽에서 참조를 제거한다.
        return None

    @classmethod
    def _release_writer_safely(
        cls,
        writer: rosbag2_py.SequentialWriter,
    ) -> None:
        """시작 실패 시 writer를 가능한 범위에서 정리한다."""
        try:
            writer = cls._release_writer(writer)
        except Exception:
            # 원래 발생한 오류를 유지하기 위해 정리 오류는 다시 던지지 않는다.
            writer = None

        gc.collect()

    @staticmethod
    def _remove_bag(path: Path) -> None:
        """중단되었거나 불완전한 rosbag2 경로를 삭제한다."""
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return
        except Exception as error:
            raise BagRecorderError(
                f'rosbag2 경로 삭제 실패: {error}'
            ) from error

    @classmethod
    def _remove_bag_safely(
        cls,
        path: Path,
    ) -> None:
        """기존 오류를 유지하면서 rosbag2 경로 삭제를 시도한다."""
        try:
            cls._remove_bag(path)
        except BagRecorderError:
            # 시작 오류를 가리지 않도록 정리 오류는 다시 던지지 않는다.
            pass
