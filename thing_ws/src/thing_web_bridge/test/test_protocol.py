"""Contract tests for browser JSON and ROS 2 state mapping."""

from types import SimpleNamespace

import pytest

from thing_web_bridge.protocol import make_ack
from thing_web_bridge.protocol import parse_request
from thing_web_bridge.protocol import ProtocolError
from thing_web_bridge.protocol import SNAPSHOT_FIELDS
from thing_web_bridge.protocol import SnapshotStore


def request(request_type, payload, **overrides):
    """Build one browser request envelope."""
    message = {
        'request_id': 'req-1',
        'type': request_type,
        'timestamp': '2026-08-04T12:00:00Z',
        'payload': payload,
    }
    message.update(overrides)
    return message


def test_empty_snapshot_has_the_fixed_eight_fields_and_safe_extensions():
    """6.4절: top-level 여덟 필드를 이름·순서 그대로 고정한다."""
    snapshot = SnapshotStore().snapshot()

    assert tuple(snapshot)[:8] == SNAPSHOT_FIELDS
    assert snapshot['mode'] == 'DISABLED'
    assert snapshot['recording_state'] == 'IDLE'
    assert snapshot['landmarks'] == {}
    assert snapshot['motor_state'] == {}
    assert snapshot['safety_state'] == {}
    assert snapshot['control_state'] == {}
    assert snapshot['recording'] == {}
    assert snapshot['last_hand_command'] == {}
    assert snapshot['timestamp'].endswith('Z')


def test_verbatim_sections_keep_raw_enums_and_string_session_ids():
    """
    6.4절: control_state·recording은 원문 그대로, Session ID만 문자열이다.

    enum을 symbol로 바꾸지 않는다. 표시용 symbol은 top-level mode·
    recording_state mirror가 담당하며 두 표현은 항상 일치한다.
    """
    store = SnapshotStore()
    store.update_control_state(SimpleNamespace(
        stamp=SimpleNamespace(sec=1, nanosec=2),
        active_mode=1,
        active_owner=1,
        owner_alive=True,
        sequence_running=False,
        last_transition_reason='accepted',
    ))
    store.update_recording_state(SimpleNamespace(
        header=SimpleNamespace(
            stamp=SimpleNamespace(sec=3, nanosec=4),
            frame_id='',
        ),
        state=2,
        active_session_id=8531234567890123456,
        active_bag_path='/tmp/bag',
        active_started_at=SimpleNamespace(sec=3, nanosec=4),
        last_session_id=0,
        last_bag_path='',
        last_started_at=SimpleNamespace(sec=0, nanosec=0),
        last_ended_at=SimpleNamespace(sec=0, nanosec=0),
        result_pending=False,
        last_mimic_result=0,
        message='',
    ))

    snapshot = store.snapshot()

    # 원문: enum은 정수 그대로다.
    assert snapshot['control_state']['active_mode'] == 1
    assert snapshot['control_state']['active_owner'] == 1
    assert snapshot['recording']['state'] == 2
    assert snapshot['recording']['last_mimic_result'] == 0
    # 원문에 없는 파생 필드를 붙이지 않는다.
    assert 'age_ms' not in snapshot['control_state']
    assert 'stale' not in snapshot['control_state']
    assert 'age_ms' not in snapshot['recording']
    assert 'stale' not in snapshot['recording']
    # mirror: top-level은 symbol이며 원문과 일치한다.
    assert snapshot['mode'] == 'MIMIC'
    assert snapshot['recording_state'] == 'RECORDING'
    # Session ID만 10진 문자열, 세션 없음은 '0'이다.
    assert snapshot['recording']['active_session_id'] == (
        '8531234567890123456')
    assert snapshot['recording']['last_session_id'] == '0'


def test_snapshot_maps_safety_and_derives_reset_allowed():
    store = SnapshotStore()
    store.update_safety_state(SimpleNamespace(
        stamp=SimpleNamespace(sec=1, nanosec=0),
        state=5,
        command_timeout=False,
        motor_communication_ok=False,
        over_current=False,
        over_temperature=False,
        estop_active=False,
        fault_code=10,
        reason='motor fault',
    ))

    payload = store.snapshot()['safety_state']

    assert payload['state'] == 'FAULT'
    assert payload['reset_allowed'] is True
    assert payload['fault_code'] == 10


@pytest.mark.parametrize(
    ('request_type', 'payload'),
    [
        (
            'set_control_mode',
            {'requested_mode': 'MIMIC', 'requested_owner': 'WEB'},
        ),
        (
            'stop',
            {'requested_mode': 'DISABLED', 'requested_owner': 'NONE'},
        ),
        (
            'execute_gesture',
            {'gesture_name': 'open', 'speed_limit': 1.0},
        ),
        (
            'execute_sequence',
            {'sequence_name': 'countdown', 'speed_limit': 0.5},
        ),
        ('start_recording', {'label': ''}),
        ('stop_recording', {'session_id': '123'}),
        (
            'set_mimic_result',
            {'session_id': '123', 'result': 'SUCCESS'},
        ),
        ('reset_safety', {}),
    ],
)
def test_frontend_request_types_are_accepted(request_type, payload):
    parsed = parse_request(request(request_type, payload))

    assert parsed.type == request_type
    assert parsed.payload == payload


@pytest.mark.parametrize(
    'message',
    [
        request('unknown', {}),
        request(
            'set_control_mode',
            {'requested_mode': 'TELEOP', 'requested_owner': 'WEB'},
        ),
        request('stop_recording', {'session_id': 8531234567890123456}),
        request('stop_recording', {'session_id': '0'}),
        # 선행 0은 int() 하면 미지정 센티널 0이나 다른 ID로 바뀐다
        request('stop_recording', {'session_id': '00'}),
        request('stop_recording', {'session_id': '007'}),
        # isdigit()은 통과하지만 int()가 ValueError를 내는 유니코드 숫자
        request('stop_recording', {'session_id': '²'}),
        request(
            'execute_gesture',
            {'gesture_name': 'custom', 'speed_limit': 0.5},
        ),
        request(
            'execute_gesture',
            {'gesture_name': 'open', 'speed_limit': float('nan')},
        ),
        request('reset_safety', {'topic': '/thing/command'}),
        request('start_recording', {'label': '', 'extra': True}),
        request('start_recording', {'label': ''}, timestamp='not-a-time'),
    ],
)
def test_malformed_or_unsafe_requests_are_rejected(message):
    with pytest.raises(ProtocolError):
        parse_request(message)


@pytest.mark.parametrize(
    ('session_id', 'expected_reason'),
    [
        # 값 사유: 계약이 "0이거나 63-bit 초과"에 전용 reason을 둔다.
        ('0', 'invalid_session_id'),
        (str(2 ** 63), 'invalid_session_id'),
        (str(2 ** 63 + 1), 'invalid_session_id'),
        # 형식 사유: 표준 10진 문자열이 아님.
        ('00', 'web_malformed_request'),
        ('007', 'web_malformed_request'),
        ('²', 'web_malformed_request'),
        ('12a', 'web_malformed_request'),
        (123, 'web_malformed_request'),
    ],
)
def test_session_id_reason_distinguishes_format_from_value(
    session_id, expected_reason,
):
    """완료조건 ②: 거부 이유를 계약 표대로 구분한다."""
    message = request('stop_recording', {'session_id': session_id})
    with pytest.raises(ProtocolError) as caught:
        parse_request(message)
    assert caught.value.reason == expected_reason


def test_ack_matches_frontend_shape():
    ack = make_ack('req-7', False, 'safety_not_ready')

    assert ack['type'] == 'ack'
    assert ack['request_id'] == 'req-7'
    assert ack['accepted'] is False
    assert ack['reason'] == 'safety_not_ready'
    assert ack['timestamp'].endswith('Z')
