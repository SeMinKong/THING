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


def test_empty_snapshot_has_fixed_six_fields_and_safe_extensions():
    snapshot = SnapshotStore().snapshot()

    assert tuple(snapshot)[:6] == SNAPSHOT_FIELDS
    assert snapshot['mode'] == 'DISABLED'
    assert snapshot['recording_state'] == 'IDLE'
    assert snapshot['landmarks'] == {}
    assert snapshot['motor_state'] == {}
    assert snapshot['safety_state'] == {}
    assert snapshot['control_state'] == {}
    assert snapshot['recording'] == {}
    assert snapshot['last_hand_command'] == {}
    assert snapshot['timestamp'].endswith('Z')


def test_snapshot_maps_control_and_recording_enums_and_session_ids():
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

    assert snapshot['mode'] == 'MIMIC'
    assert snapshot['control_state']['active_owner'] == 'WEB'
    assert snapshot['recording_state'] == 'RECORDING'
    assert snapshot['recording']['active_session_id'] == (
        '8531234567890123456')
    assert snapshot['recording']['last_session_id'] == ''


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


def test_ack_matches_frontend_shape():
    ack = make_ack('req-7', False, 'safety_not_ready')

    assert ack['type'] == 'ack'
    assert ack['request_id'] == 'req-7'
    assert ack['accepted'] is False
    assert ack['reason'] == 'safety_not_ready'
    assert ack['timestamp'].endswith('Z')
