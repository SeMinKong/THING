"""Behavior tests for command manager arbitration and ownership."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from thing_control.command_manager_core import CommandManagerCore


MODE_DISABLED = 0
MODE_MIMIC = 1
MODE_MANUAL = 2
MODE_TELEOP = 3

OWNER_NONE = 0
OWNER_WEB = 1
OWNER_LOCAL = 2

SOURCE_MIMIC = 1
SOURCE_TELEOP = 2
SOURCE_GESTURE = 3
SOURCE_SEQUENCE = 4
SOURCE_SAFETY = 5

SAFETY_INIT = 0
SAFETY_READY = 1
SAFETY_RUN = 2
SAFETY_HOLD = 3
SAFETY_FAULT = 5

RECORDING_IDLE = 0
RECORDING_RECORDING = 2
RECORDING_COMPLETED = 4


class FakeClock:
    """Controllable monotonic clock for lease tests."""

    def __init__(self):
        self.now_ns = 0

    def __call__(self):
        return self.now_ns

    def advance_ms(self, milliseconds):
        self.now_ns += milliseconds * 1_000_000


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def manager(clock):
    return CommandManagerCore(
        owner_lease_timeout_ms=3000,
        monotonic_ns=clock,
    )


def make_ready(manager):
    manager.update_safety_state(SAFETY_READY)


def acquire(manager, mode=MODE_MIMIC, owner=OWNER_WEB):
    make_ready(manager)
    result = manager.request_mode(mode, owner)
    assert result.accepted is True
    return result


def test_initial_state_is_disabled_without_an_owner(manager):
    state = manager.snapshot()

    assert state.active_mode == MODE_DISABLED
    assert state.active_owner == OWNER_NONE
    assert state.owner_alive is False
    assert state.sequence_running is False


def test_active_mode_is_rejected_before_safety_is_ready(manager):
    result = manager.request_mode(MODE_MIMIC, OWNER_WEB)

    assert result.accepted is False
    assert result.reason == 'safety_not_ready'


def test_ready_state_allows_first_owner_to_acquire_mode(manager):
    make_ready(manager)

    result = manager.request_mode(MODE_MIMIC, OWNER_WEB)

    assert result.accepted is True
    assert result.reason == 'accepted'
    assert result.active_mode == MODE_MIMIC
    assert result.active_owner == OWNER_WEB
    assert manager.snapshot().owner_alive is True


def test_same_mode_and_owner_request_renews_lease(manager, clock):
    acquire(manager)
    clock.advance_ms(2500)

    result = manager.request_mode(MODE_MIMIC, OWNER_WEB)
    clock.advance_ms(2500)

    assert result.accepted is True
    assert manager.check_lease() is False
    assert manager.snapshot().owner_alive is True


def test_different_owner_cannot_take_active_control(manager):
    acquire(manager)

    result = manager.request_mode(MODE_TELEOP, OWNER_LOCAL)

    assert result.accepted is False
    assert result.reason == 'owner_conflict'
    assert manager.snapshot().active_owner == OWNER_WEB


def test_active_mode_cannot_change_without_disabled_transition(manager):
    acquire(manager)

    result = manager.request_mode(MODE_MANUAL, OWNER_WEB)

    assert result.accepted is False
    assert result.reason == 'invalid_mode'
    assert manager.snapshot().active_mode == MODE_MIMIC


@pytest.mark.parametrize(
    ('mode', 'owner'),
    [
        (MODE_DISABLED, OWNER_WEB),
        (MODE_MIMIC, OWNER_NONE),
        (MODE_MIMIC, OWNER_LOCAL),
        (MODE_MANUAL, OWNER_LOCAL),
        (MODE_TELEOP, OWNER_WEB),
        (99, OWNER_WEB),
        (MODE_MIMIC, 99),
    ],
)
def test_invalid_mode_owner_pairs_are_rejected(manager, mode, owner):
    make_ready(manager)

    result = manager.request_mode(mode, owner)

    assert result.accepted is False
    assert result.reason == 'invalid_mode'


def test_stop_releases_owner_and_clears_motion_state(manager):
    acquire(manager, MODE_MANUAL, OWNER_WEB)
    manager.set_sequence_running(True)

    result = manager.request_mode(MODE_DISABLED, OWNER_NONE)

    assert result.accepted is True
    state = manager.snapshot()
    assert state.active_mode == MODE_DISABLED
    assert state.active_owner == OWNER_NONE
    assert state.owner_alive is False
    assert state.sequence_running is False


def test_stop_blocks_new_control_acquisition_for_500ms(manager, clock):
    acquire(manager)
    assert manager.request_mode(MODE_DISABLED, OWNER_NONE).accepted is True

    immediate = manager.request_mode(MODE_MIMIC, OWNER_WEB)
    clock.advance_ms(499)
    before_deadline = manager.request_mode(MODE_MIMIC, OWNER_WEB)
    clock.advance_ms(1)
    at_deadline = manager.request_mode(MODE_MIMIC, OWNER_WEB)

    assert immediate.accepted is False
    assert immediate.reason == 'stop_in_progress'
    assert before_deadline.accepted is False
    assert before_deadline.reason == 'stop_in_progress'
    assert at_deadline.accepted is True


def test_motion_blocks_valid_mode_change_with_motion_active_reason(manager):
    acquire(manager, MODE_MANUAL, OWNER_WEB)
    assert manager.set_sequence_running(True) is True

    result = manager.request_mode(MODE_TELEOP, OWNER_LOCAL)

    assert result.accepted is False
    assert result.reason == 'motion_active'
    assert manager.snapshot().active_mode == MODE_MANUAL


def test_current_owner_can_renew_lease_while_motion_is_active(manager):
    acquire(manager, MODE_MANUAL, OWNER_WEB)
    assert manager.set_sequence_running(True) is True

    result = manager.request_mode(MODE_MANUAL, OWNER_WEB)

    assert result.accepted is True


@pytest.mark.parametrize(
    ('mode', 'allowed_sources'),
    [
        (MODE_MIMIC, {SOURCE_MIMIC}),
        (MODE_MANUAL, {SOURCE_GESTURE, SOURCE_SEQUENCE}),
        (MODE_TELEOP, {SOURCE_TELEOP}),
    ],
)
def test_only_sources_for_active_mode_are_selected(
    manager,
    mode,
    allowed_sources,
):
    owner = OWNER_LOCAL if mode == MODE_TELEOP else OWNER_WEB
    acquire(manager, mode, owner)

    accepted = {
        source
        for source in (
            SOURCE_MIMIC,
            SOURCE_TELEOP,
            SOURCE_GESTURE,
            SOURCE_SEQUENCE,
            SOURCE_SAFETY,
        )
        if manager.accepts_source(source)
    }

    assert accepted == allowed_sources


def test_owner_lease_expiry_atomically_disables_control(manager, clock):
    acquire(manager, mode=MODE_MANUAL, owner=OWNER_WEB)
    assert manager.set_sequence_running(True) is True
    assert manager.snapshot().sequence_running is True
    clock.advance_ms(3000)

    changed = manager.check_lease()

    assert changed is True
    state = manager.snapshot()
    assert state.active_mode == MODE_DISABLED
    assert state.active_owner == OWNER_NONE
    assert state.owner_alive is False
    assert state.sequence_running is False
    assert state.last_transition_reason == 'owner_lease_expired'


def test_expired_owner_cannot_select_command_before_timer_runs(manager, clock):
    acquire(manager)
    clock.advance_ms(3000)

    accepted = manager.accepts_source(SOURCE_MIMIC)

    assert accepted is False
    assert manager.snapshot().active_mode == MODE_DISABLED


@pytest.mark.parametrize('unsafe_state', [SAFETY_INIT, SAFETY_FAULT])
def test_unsafe_transition_releases_active_control(manager, unsafe_state):
    acquire(manager)

    changed = manager.update_safety_state(unsafe_state)

    assert changed is True
    state = manager.snapshot()
    assert state.active_mode == MODE_DISABLED
    assert state.active_owner == OWNER_NONE
    assert state.owner_alive is False
    assert state.last_transition_reason == 'safety_not_ready'


def test_hold_blocks_commands_but_preserves_mode_until_explicit_stop(manager):
    acquire(manager)

    changed = manager.update_safety_state(SAFETY_HOLD)

    assert changed is False
    state = manager.snapshot()
    assert state.active_mode == MODE_MIMIC
    assert state.active_owner == OWNER_WEB
    assert state.owner_alive is True
    assert manager.accepts_source(SOURCE_MIMIC) is False

    stopped = manager.request_mode(MODE_DISABLED, OWNER_NONE)
    assert stopped.accepted is True
    assert manager.snapshot().active_mode == MODE_DISABLED


def test_run_safety_state_keeps_active_control(manager):
    acquire(manager)

    changed = manager.update_safety_state(SAFETY_RUN)

    assert changed is False
    assert manager.snapshot().active_mode == MODE_MIMIC


@pytest.mark.parametrize(
    ('recording_state', 'result_pending'),
    [
        (RECORDING_RECORDING, False),
        (RECORDING_COMPLETED, True),
    ],
)
def test_recording_blocks_new_active_mode(
    manager,
    recording_state,
    result_pending,
):
    make_ready(manager)
    manager.update_recording_state(recording_state, result_pending)

    result = manager.request_mode(MODE_MIMIC, OWNER_WEB)

    assert result.accepted is False
    assert result.reason == 'recording_active'


def test_recording_does_not_block_current_owner_lease_renewal(manager):
    acquire(manager)
    manager.update_recording_state(RECORDING_RECORDING, False)

    result = manager.request_mode(MODE_MIMIC, OWNER_WEB)

    assert result.accepted is True


def test_stop_is_accepted_during_recording_and_fault(manager):
    acquire(manager)
    manager.update_recording_state(RECORDING_RECORDING, False)
    manager.update_safety_state(SAFETY_FAULT)

    result = manager.request_mode(MODE_DISABLED, OWNER_NONE)

    assert result.accepted is True
    assert result.reason == 'accepted'


def test_simultaneous_acquisition_accepts_exactly_one_owner(manager):
    make_ready(manager)

    with ThreadPoolExecutor(max_workers=2) as executor:
        requests = (
            (MODE_MIMIC, OWNER_WEB),
            (MODE_TELEOP, OWNER_LOCAL),
        )
        results = list(
            executor.map(
                lambda pair: manager.request_mode(*pair),
                requests,
            )
        )

    assert sum(result.accepted for result in results) == 1
    assert {result.reason for result in results} == {
        'accepted',
        'owner_conflict',
    }
