"""V7.0 canonical export 파일 계약."""

SCHEMA_VERSION = 1
DATA_VERSION = 1
INTERFACE_COMMIT = '626c59e09f108e6e5eb6d2313efe28bf0e51ed03'

FILE_KINDS = (
    'metadata',
    'hand_command',
    'motor_status',
    'landmark',
)

HAND_COMMAND_HEADER = (
    'session_id',
    'stamp_sec',
    'stamp_nanosec',
    'elapsed_ms',
    'sequence',
    'source',
    'thumb_flex',
    'thumb_opp',
    'thumb_abd',
    'index_flex',
    'middle_flex',
    'ring_flex',
    'little_flex',
    'speed_limit',
    'confidence',
)

MOTOR_STATUS_HEADER = (
    'session_id',
    'stamp_sec',
    'stamp_nanosec',
    'elapsed_ms',
    'frame_id',
    'motor_id',
    'actuator_name',
    'goal_position_raw',
    'present_position_raw',
    'goal_position_rad',
    'present_position_rad',
    'velocity_rad_s',
    'current_ampere',
    'voltage_volt',
    'temperature_celsius',
    'torque_enabled',
    'hardware_error',
    'communication_result',
    'communication_ok',
    'bus_communication_ok',
    'failed_read_count',
)

LANDMARK_RECORD_FIELDS = (
    'session_id',
    'timestamp',
    'stamp_sec',
    'stamp_nanosec',
    'elapsed_ms',
    'detected',
    'confidence',
    'handedness',
    'handedness_confidence',
    'image_width',
    'image_height',
    'landmarks',
)

LANDMARK_POINT_FIELDS = ('x', 'y', 'z')

METADATA_FIELDS = (
    'schema_version',
    'data_version',
    'robot_id',
    'session_id',
    'started_at',
    'ended_at',
    'result',
    'interface_commit',
    'time_sync',
    'content_digest',
    'files',
)


def canonical_filenames(session_id: int) -> dict:
    """Session ID에 해당하는 네 canonical 파일명을 반환한다."""
    session_text = str(session_id)
    return {
        'metadata': f'session_{session_text}_metadata.json',
        'hand_command': f'session_{session_text}_hand_command.csv',
        'motor_status': f'session_{session_text}_motor_status.csv',
        'landmark': f'session_{session_text}_landmark.json',
    }
