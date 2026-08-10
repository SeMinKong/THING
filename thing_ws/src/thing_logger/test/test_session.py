"""Tests for the in-memory logger session lifecycle."""

from pathlib import Path

import pytest

from thing_interfaces.msg import ControlState
from thing_interfaces.msg import RecordingState
from thing_interfaces.msg import SafetyState
from thing_logger.session import SessionManager


def make_manager(tmp_path, session_ids):
    """Create a manager that consumes deterministic test session IDs."""
    values = iter(session_ids)
    return SessionManager(tmp_path, session_id_factory=lambda: next(values))


def start_recording(manager, label='test'):
    """Move a manager through STARTING into RECORDING."""
    session = manager.begin_start(label=label, started_at_ns=100)
    manager.mark_recording()
    return session


def test_initial_state_and_start_admission(tmp_path):
    """A fresh manager is IDLE and only accepts MIMIC mode."""
    manager = make_manager(tmp_path, [123])

    assert manager.state == RecordingState.IDLE
    assert manager.active_session is None
    assert manager.last_session is None
    assert manager.result_pending is False
    assert manager.can_start(
        ControlState.MODE_DISABLED,
        SafetyState.READY,
    ) == (
        False,
        'not_mimic_mode',
    )
    assert manager.can_start(
        ControlState.MODE_MIMIC,
        SafetyState.READY,
    ) == (True, '')
    assert manager.can_start(
        ControlState.MODE_MIMIC,
        SafetyState.RUN,
    ) == (True, '')


@pytest.mark.parametrize(
    'safety_state',
    [
        SafetyState.INIT,
        SafetyState.HOLD,
        SafetyState.SAFE,
        SafetyState.FAULT,
        SafetyState.ESTOP,
    ],
)
def test_start_rejects_unsafe_state(tmp_path, safety_state):
    """READY와 RUN이 아닌 안전 상태에서는 새 녹화를 거부한다."""
    manager = make_manager(tmp_path, [123])

    assert manager.can_start(
        ControlState.MODE_MIMIC,
        safety_state,
    ) == (False, 'start_failed')


def test_session_id_is_nonzero_63_bit_and_path_is_unique(tmp_path):
    """Invalid and colliding IDs are skipped before STARTING."""
    (tmp_path / '42').mkdir()
    manager = make_manager(tmp_path, [0, 2**63, 42, 77])

    session = manager.begin_start(label='mvp', started_at_ns=100)

    assert session.session_id == 77
    assert session.bag_path == str(tmp_path / '77')
    assert 0 < session.session_id < 2**63
    assert manager.state == RecordingState.STARTING


def test_duplicate_start_is_rejected(tmp_path):
    """A recording session prevents another start."""
    manager = make_manager(tmp_path, [123])
    start_recording(manager)

    assert manager.can_start(
        ControlState.MODE_MIMIC,
        SafetyState.RUN,
    ) == (
        False,
        'already_recording',
    )


def test_cancel_start_returns_to_idle(tmp_path):
    """A rosbag startup failure clears the active session."""
    manager = make_manager(tmp_path, [123])
    manager.begin_start(label='test', started_at_ns=100)

    manager.cancel_start()

    assert manager.state == RecordingState.IDLE
    assert manager.active_session is None


def test_stop_requires_recording_and_matching_session(tmp_path):
    """Stop only accepts the currently recording session ID."""
    manager = make_manager(tmp_path, [123])

    assert manager.can_stop(123) == (False, 'not_recording')

    session = start_recording(manager)

    assert manager.can_stop(session.session_id + 1) == (
        False,
        'session_mismatch',
    )
    assert manager.can_stop(session.session_id) == (True, '')


def test_complete_waits_for_result_and_blocks_start(tmp_path):
    """A normal stop moves the session to result-pending COMPLETED."""
    manager = make_manager(tmp_path, [123])
    session = start_recording(manager)

    manager.mark_stopping()
    completed = manager.complete(ended_at_ns=200)

    assert completed is session
    assert completed.ended_at_ns == 200
    assert manager.active_session is None
    assert manager.last_session is session
    assert manager.state == RecordingState.COMPLETED
    assert manager.result_pending is True
    assert manager.can_start(
        ControlState.MODE_MIMIC,
        SafetyState.RUN,
    ) == (
        False,
        'result_pending',
    )


@pytest.mark.parametrize(
    'result',
    [
        RecordingState.RESULT_SUCCESS,
        RecordingState.RESULT_FAILURE,
    ],
)
def test_result_is_accepted_exactly_once(tmp_path, result):
    """SUCCESS and FAILURE complete the pending lifecycle exactly once."""
    manager = make_manager(tmp_path, [123])
    session = start_recording(manager)
    manager.mark_stopping()
    manager.complete(ended_at_ns=200)

    assert manager.set_result(session.session_id, result) == (True, '')
    assert manager.last_session.result == result
    assert manager.result_pending is False
    assert manager.state == RecordingState.IDLE
    assert manager.set_result(session.session_id, result) == (
        False,
        'result_already_set',
    )


def test_result_rejects_invalid_value_and_unknown_session(tmp_path):
    """A pending session rejects invalid results and unknown IDs."""
    manager = make_manager(tmp_path, [123])
    session = start_recording(manager)
    manager.mark_stopping()
    manager.complete(ended_at_ns=200)

    assert manager.set_result(session.session_id, 99) == (
        False,
        'invalid_result',
    )
    assert manager.set_result(session.session_id + 1, 1) == (
        False,
        'session_not_found',
    )


def test_interrupt_does_not_wait_for_result(tmp_path):
    """An interrupted recording is terminal and skips mimic judgment."""
    manager = make_manager(tmp_path, [123])
    session = start_recording(manager)

    interrupted = manager.interrupt(ended_at_ns=200)

    assert interrupted is session
    assert interrupted.ended_at_ns == 200
    assert manager.state == RecordingState.INTERRUPTED
    assert manager.active_session is None
    assert manager.last_session is session
    assert manager.result_pending is False

    assert manager.set_result(
        session.session_id,
        RecordingState.RESULT_SUCCESS,
    ) == (False, 'session_not_found')

    # INIT 재검사 중에는 INTERRUPTED를 유지한다.
    assert manager.can_start(
        ControlState.MODE_MIMIC,
        SafetyState.INIT,
    ) == (False, 'start_failed')

    # 실제 READY 수신 뒤 Logger가 호출하는 복구 동작이다.
    manager.reset_to_idle()
    assert manager.state == RecordingState.IDLE
    assert manager.result_pending is False


def test_failed_id_generation_does_not_create_a_session(tmp_path):
    """Exhausting invalid IDs fails without entering STARTING."""
    manager = make_manager(tmp_path, [0] * 100)

    with pytest.raises(RuntimeError, match='unique session ID'):
        manager.begin_start(label='test', started_at_ns=100)

    assert manager.state == RecordingState.IDLE
    assert manager.active_session is None
    assert not list(Path(tmp_path).iterdir())
