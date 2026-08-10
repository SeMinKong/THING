"""V7.0 exporter 출력 계약을 검증한다."""

from thing_logger.export_schema import canonical_filenames
from thing_logger.export_schema import DATA_VERSION
from thing_logger.export_schema import FILE_KINDS
from thing_logger.export_schema import HAND_COMMAND_HEADER
from thing_logger.export_schema import INTERFACE_COMMIT
from thing_logger.export_schema import LANDMARK_POINT_FIELDS
from thing_logger.export_schema import LANDMARK_RECORD_FIELDS
from thing_logger.export_schema import METADATA_FIELDS
from thing_logger.export_schema import MOTOR_STATUS_HEADER
from thing_logger.export_schema import SCHEMA_VERSION


def test_fixed_versions_and_file_names():
    """버전과 정확히 네 canonical 파일명을 고정한다."""
    assert SCHEMA_VERSION == 1
    assert DATA_VERSION == 1
    assert INTERFACE_COMMIT == (
        '626c59e09f108e6e5eb6d2313efe28bf0e51ed03'
    )
    assert FILE_KINDS == (
        'metadata',
        'hand_command',
        'motor_status',
        'landmark',
    )
    assert canonical_filenames(123) == {
        'metadata': 'session_123_metadata.json',
        'hand_command': 'session_123_hand_command.csv',
        'motor_status': 'session_123_motor_status.csv',
        'landmark': 'session_123_landmark.json',
    }


def test_hand_command_csv_header_matches_v7():
    """CSV의 HandCommand 열 순서를 V7.0과 동일하게 유지한다."""
    assert HAND_COMMAND_HEADER == (
        'session_id', 'stamp_sec', 'stamp_nanosec', 'elapsed_ms',
        'sequence', 'source', 'thumb_flex', 'thumb_opp',
        'thumb_abd', 'index_flex', 'middle_flex', 'ring_flex',
        'little_flex', 'speed_limit', 'confidence',
    )


def test_motor_status_csv_header_matches_v7():
    """CSV의 MotorStatus 평탄화 열 순서를 V7.0과 동일하게 유지한다."""
    assert MOTOR_STATUS_HEADER == (
        'session_id', 'stamp_sec', 'stamp_nanosec', 'elapsed_ms',
        'frame_id', 'motor_id', 'actuator_name', 'goal_position_raw',
        'present_position_raw', 'goal_position_rad',
        'present_position_rad', 'velocity_rad_s', 'current_ampere',
        'voltage_volt', 'temperature_celsius', 'torque_enabled',
        'hardware_error',
        'communication_result', 'communication_ok',
        'bus_communication_ok', 'failed_read_count',
    )


def test_json_field_contracts_are_fixed():
    """metadata와 landmark JSON 필드 및 21개 점 구조를 고정한다."""
    assert METADATA_FIELDS == (
        'schema_version', 'data_version', 'robot_id', 'session_id',
        'started_at', 'ended_at', 'result',
        'interface_commit', 'time_sync', 'content_digest', 'files',
    )
    assert LANDMARK_RECORD_FIELDS == (
        'session_id', 'timestamp', 'stamp_sec', 'stamp_nanosec',
        'elapsed_ms', 'detected', 'confidence', 'handedness',
        'handedness_confidence', 'image_width', 'image_height',
        'landmarks',
    )
    assert LANDMARK_POINT_FIELDS == ('x', 'y', 'z')
