"""
Pin the JSON symbol tables to the frozen thing_interfaces definitions.

Jira 완료조건: "develop 인터페이스의 enum·필드와 JSON mapping fixture가 일치한다."

다른 테스트는 SimpleNamespace 가짜 객체를 쓴다. 그것만으로는 .msg의 상수 번호가
바뀌었을 때 잡지 못한다. 이 파일은 실제 메시지 클래스를 import해서 protocol.py의
symbol 표와 한 칸씩 대조한다.

ROS 환경을 source하지 않으면 import가 실패하므로 전체를 skip한다. colcon test는
환경이 있으므로 항상 실행된다.
"""

import pytest

from thing_web_bridge.protocol import CONTROL_MODES
from thing_web_bridge.protocol import CONTROL_OWNERS
from thing_web_bridge.protocol import HAND_SOURCES
from thing_web_bridge.protocol import HANDEDNESS
from thing_web_bridge.protocol import RECORDING_RESULTS
from thing_web_bridge.protocol import RECORDING_STATES
from thing_web_bridge.protocol import SAFETY_STATES

thing_interfaces = pytest.importorskip(
    'thing_interfaces.msg',
    reason='thing_interfaces requires a sourced ROS 2 workspace',
)

ControlState = thing_interfaces.ControlState
HandCommand = thing_interfaces.HandCommand
HandLandmarks = thing_interfaces.HandLandmarks
MotorState = thing_interfaces.MotorState
MotorStatus = thing_interfaces.MotorStatus
RecordingState = thing_interfaces.RecordingState
SafetyState = thing_interfaces.SafetyState


def assert_table_matches(table, message_type, prefix, names):
    """Check every symbol sits at the ordinal its .msg constant declares."""
    assert len(table) == len(names), (
        f'{message_type.__name__} {prefix}* 상수 개수가 표와 다릅니다'
    )
    for expected_index, name in enumerate(names):
        constant = getattr(message_type, f'{prefix}{name}')
        assert table[constant] == name, (
            f'{message_type.__name__}.{prefix}{name}={constant} 인데 '
            f'표에서는 {table[constant]!r} 자리입니다'
        )


# --------------------------------------------------------------------------
# enum 표 대조
# --------------------------------------------------------------------------

def test_control_mode_table_matches_control_state_msg():
    assert_table_matches(
        CONTROL_MODES, ControlState, 'MODE_',
        ('DISABLED', 'MIMIC', 'MANUAL', 'TELEOP'),
    )


def test_control_owner_table_matches_control_state_msg():
    assert_table_matches(
        CONTROL_OWNERS, ControlState, 'OWNER_',
        ('NONE', 'WEB', 'LOCAL'),
    )


def test_recording_state_table_matches_recording_state_msg():
    assert_table_matches(
        RECORDING_STATES, RecordingState, '',
        ('IDLE', 'STARTING', 'RECORDING', 'STOPPING',
         'COMPLETED', 'FAILED', 'INTERRUPTED'),
    )


def test_recording_result_table_matches_recording_state_msg():
    assert_table_matches(
        RECORDING_RESULTS, RecordingState, 'RESULT_',
        ('UNSET', 'SUCCESS', 'FAILURE'),
    )


def test_safety_state_table_matches_safety_state_msg():
    """V7에서 승인된 RESET=7 추가까지 포함해 8개 상태를 확인한다."""
    assert_table_matches(
        SAFETY_STATES, SafetyState, '',
        ('INIT', 'READY', 'RUN', 'HOLD',
         'SAFE', 'FAULT', 'ESTOP', 'RESET'),
    )
    assert SafetyState.RESET == 7


def test_handedness_table_matches_hand_landmarks_msg():
    assert_table_matches(
        HANDEDNESS, HandLandmarks, 'HANDEDNESS_',
        ('UNKNOWN', 'LEFT', 'RIGHT'),
    )


def test_hand_source_table_matches_hand_command_msg():
    assert_table_matches(
        HAND_SOURCES, HandCommand, 'SOURCE_',
        ('UNKNOWN', 'MIMIC', 'TELEOP', 'GESTURE', 'SEQUENCE', 'SAFETY'),
    )


# --------------------------------------------------------------------------
# 필드 존재 대조 — 브리지가 읽는 필드가 .msg에 실제로 있는지
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ('message_type', 'fields'),
    [
        (ControlState, ('stamp', 'active_mode', 'active_owner',
                        'owner_alive', 'sequence_running',
                        'last_transition_reason')),
        (SafetyState, ('stamp', 'state', 'command_timeout',
                       'motor_communication_ok', 'over_current',
                       'over_temperature', 'estop_active',
                       'fault_code', 'reason')),
        (RecordingState, ('header', 'state', 'active_session_id',
                          'active_bag_path', 'last_session_id',
                          'result_pending', 'last_mimic_result')),
        (HandLandmarks, ('header', 'detected', 'confidence', 'handedness',
                         'handedness_confidence', 'landmarks')),
        (HandCommand, ('stamp', 'sequence', 'source', 'thumb_flex',
                       'thumb_opp', 'thumb_abd', 'index_flex',
                       'middle_flex', 'ring_flex', 'little_flex',
                       'speed_limit', 'confidence')),
        (MotorStatus, ('header', 'motors', 'bus_communication_ok',
                       'failed_read_count', 'message')),
        (MotorState, ('motor_id', 'actuator_name', 'present_position_raw',
                      'present_position_rad', 'velocity_rad_s',
                      'current_ampere', 'voltage_volt',
                      'temperature_celsius', 'torque_enabled',
                      'communication_ok')),
    ],
)
def test_bridge_reads_only_declared_fields(message_type, fields):
    declared = set(message_type.get_fields_and_field_types())
    missing = sorted(set(fields) - declared)
    assert not missing, (
        f'{message_type.__name__}에 없는 필드를 브리지가 읽습니다: {missing}'
    )


def test_interface_set_stays_seven_five_one():
    """FR-30: additive delta 2건만 허용하고 7 msg·5 srv·1 action을 유지한다."""
    from thing_interfaces import action, msg, srv
    for name in ('HandCommand', 'HandLandmarks', 'MotorState', 'MotorStatus',
                 'ControlState', 'SafetyState', 'RecordingState'):
        assert hasattr(msg, name), f'msg {name} 누락'
    for name in ('SetControlMode', 'ExecuteGesture', 'StartRecording',
                 'StopRecording', 'SetMimicResult'):
        assert hasattr(srv, name), f'srv {name} 누락'
    assert hasattr(action, 'ExecuteSequence')
    # 브리지는 커스텀 Safety Reset 타입을 만들지 않는다. 표준 Trigger를 쓴다.
    assert not hasattr(srv, 'ResetSafety')


# --------------------------------------------------------------------------
# 실제 메시지를 채워 snapshot 직렬화까지 통과하는지
# --------------------------------------------------------------------------

def test_real_messages_serialize_into_the_snapshot_contract():
    from thing_web_bridge.protocol import SNAPSHOT_FIELDS, SnapshotStore

    store = SnapshotStore()

    control = ControlState()
    control.active_mode = ControlState.MODE_MIMIC
    control.active_owner = ControlState.OWNER_WEB
    control.owner_alive = True
    store.update_control_state(control)

    safety = SafetyState()
    safety.state = SafetyState.FAULT
    safety.fault_code = 10
    store.update_safety_state(safety)

    recording = RecordingState()
    recording.state = RecordingState.RECORDING
    recording.active_session_id = 8531234567890123456
    recording.last_mimic_result = RecordingState.RESULT_UNSET
    store.update_recording_state(recording)

    landmarks = HandLandmarks()
    landmarks.detected = True
    landmarks.handedness = HandLandmarks.HANDEDNESS_RIGHT
    landmarks.confidence = 0.93
    store.update_landmarks(landmarks)

    command = HandCommand()
    command.source = HandCommand.SOURCE_MIMIC
    command.thumb_flex = 0.25
    store.update_hand_command(command)

    motor = MotorState()
    motor.motor_id = 1
    motor.actuator_name = 'ring_flex'
    motor.current_ampere = float('nan')  # 통신 실패 시 정상 시나리오
    status = MotorStatus()
    status.motors = [motor]
    store.update_motor_state(status)

    snapshot = store.snapshot()

    assert tuple(snapshot)[:8] == SNAPSHOT_FIELDS
    # mirror는 symbol, 원문 두 절은 정수 그대로 (6.4절)
    assert snapshot['mode'] == 'MIMIC'
    assert snapshot['recording_state'] == 'RECORDING'
    assert snapshot['control_state']['active_mode'] == ControlState.MODE_MIMIC
    assert snapshot['control_state']['active_owner'] == ControlState.OWNER_WEB
    assert snapshot['recording']['state'] == RecordingState.RECORDING
    # 파생 표시 객체 세 개는 symbol과 파생값을 갖는다
    assert snapshot['safety_state']['state'] == 'FAULT'
    assert snapshot['safety_state']['reset_allowed'] is True
    assert snapshot['landmarks']['handedness'] == 'RIGHT'
    assert snapshot['landmarks']['detect_valid'] is True
    assert snapshot['last_hand_command']['source'] == 'MIMIC'
    # uint64는 10진 문자열, 세션 없음은 '0' (6.4절)
    assert snapshot['recording']['active_session_id'] == '8531234567890123456'
    assert snapshot['recording']['last_session_id'] == '0'
    # NaN은 예외가 아니라 null
    assert snapshot['motor_state']['motors'][0]['current_ampere'] is None


def test_snapshot_is_json_serializable_with_real_messages():
    import json

    from thing_web_bridge.protocol import SnapshotStore

    store = SnapshotStore()
    store.update_safety_state(SafetyState())
    store.update_motor_state(MotorStatus())
    store.update_landmarks(HandLandmarks())
    store.update_control_state(ControlState())
    store.update_recording_state(RecordingState())
    store.update_hand_command(HandCommand())

    # 직렬화 불가 타입이 남아 있으면 여기서 TypeError가 난다.
    encoded = json.dumps(store.snapshot(), allow_nan=False)
    assert 'NaN' not in encoded
    assert 'Infinity' not in encoded
