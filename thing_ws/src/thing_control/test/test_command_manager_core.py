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
SAFETY_RESET = 7

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
    update_safety(manager, SAFETY_READY)


def update_safety(manager, state):
    """Issue a fresh transition stamp after any active STOP boundary."""
    stamp_ns = max(
        manager._last_safety_stamp_ns or 0,
        manager._stop_source_stamp_boundary_ns or 0,
        manager._stop_recovery_epoch_stamp_ns or 0,
    ) + 1
    return manager.update_safety_state(state, stamp_ns)


def request_stop(manager):
    boundary_ns = (manager._last_safety_stamp_ns or 0) + 1
    return manager.request_mode(MODE_DISABLED, OWNER_NONE, boundary_ns)


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


def test_mode_request_cannot_silently_reacquire_when_lease_expires(
    manager,
    clock,
):
    acquire(manager)
    clock.advance_ms(3000)

    expired = manager.request_mode(MODE_MIMIC, OWNER_WEB)

    assert expired.accepted is False
    assert expired.reason == 'owner_lease_expired'
    state = manager.snapshot()
    assert state.active_mode == MODE_DISABLED
    assert state.active_owner == OWNER_NONE
    assert state.owner_alive is False

    retried = manager.request_mode(MODE_MIMIC, OWNER_WEB)
    assert retried.accepted is True


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

    result = request_stop(manager)

    assert result.accepted is True
    state = manager.snapshot()
    assert state.active_mode == MODE_DISABLED
    assert state.active_owner == OWNER_NONE
    assert state.owner_alive is False
    assert state.sequence_running is False


def test_stop_blocks_new_control_acquisition_for_500ms(manager, clock):
    acquire(manager)
    assert request_stop(manager).accepted is True

    immediate = manager.request_mode(MODE_MIMIC, OWNER_WEB)
    clock.advance_ms(499)
    before_deadline = manager.request_mode(MODE_MIMIC, OWNER_WEB)
    update_safety(manager, SAFETY_RESET)
    update_safety(manager, SAFETY_READY)
    clock.advance_ms(1)
    at_deadline = manager.request_mode(MODE_MIMIC, OWNER_WEB)

    assert immediate.accepted is False
    assert immediate.reason == 'stop_in_progress'
    assert before_deadline.accepted is False
    assert before_deadline.reason == 'stop_in_progress'
    assert at_deadline.accepted is True


def test_stop_in_fault_does_not_require_unreachable_reset_state(manager, clock):
    update_safety(manager, SAFETY_FAULT)
    assert request_stop(manager).accepted is True

    clock.advance_ms(500)
    update_safety(manager, SAFETY_INIT)
    update_safety(manager, SAFETY_READY)

    result = manager.request_mode(MODE_MIMIC, OWNER_WEB)
    assert result.accepted is True


def test_pre_stop_queued_safety_epoch_cannot_clear_recovery_gate(manager, clock):
    assert manager.update_safety_state(SAFETY_READY, 100) is False
    assert manager.request_mode(MODE_MIMIC, OWNER_WEB).accepted is True
    assert manager.request_mode(MODE_DISABLED, OWNER_NONE, 200).accepted is True
    clock.advance_ms(500)

    # Both samples were produced before STOP even though they arrived afterward.
    assert manager.update_safety_state(SAFETY_RESET, 150) is False
    assert manager.update_safety_state(SAFETY_READY, 190) is False
    blocked = manager.request_mode(MODE_MIMIC, OWNER_WEB)
    assert blocked.accepted is False
    assert blocked.reason == 'stop_in_progress'

    assert manager.update_safety_state(SAFETY_RESET, 201) is False
    assert manager.update_safety_state(SAFETY_READY, 202) is False
    assert manager.request_mode(MODE_MIMIC, OWNER_WEB).accepted is True


def test_malformed_or_replayed_safety_stamps_cannot_authorize_control(manager):
    assert manager.update_safety_state(SAFETY_READY, 0) is False
    assert manager.request_mode(MODE_MIMIC, OWNER_WEB).accepted is False

    assert manager.update_safety_state(SAFETY_READY, 100) is False
    assert manager.request_mode(MODE_MIMIC, OWNER_WEB).accepted is True

    # Older and same-stamp state changes are ignored.
    assert manager.update_safety_state(SAFETY_FAULT, 99) is False
    assert manager.update_safety_state(SAFETY_RESET, 100) is False
    assert manager.snapshot().active_mode == MODE_MIMIC


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

    changed = update_safety(manager, unsafe_state)

    assert changed is True
    state = manager.snapshot()
    assert state.active_mode == MODE_DISABLED
    assert state.active_owner == OWNER_NONE
    assert state.owner_alive is False
    assert state.last_transition_reason == 'safety_not_ready'


def test_hold_preserves_owner_and_forwards_source_only_for_validation(manager):
    acquire(manager)

    changed = update_safety(manager, SAFETY_HOLD)

    assert changed is False
    state = manager.snapshot()
    assert state.active_mode == MODE_MIMIC
    assert state.active_owner == OWNER_WEB
    assert state.owner_alive is True
    assert manager.accepts_source(SOURCE_MIMIC) is True

    stopped = request_stop(manager)
    assert stopped.accepted is True
    assert manager.snapshot().active_mode == MODE_DISABLED
    assert manager.snapshot().active_owner == OWNER_NONE


@pytest.mark.parametrize('safety_state', [SAFETY_INIT, SAFETY_RESET])
def test_stop_is_rejected_in_init_and_reset(safety_state, manager):
    update_safety(manager, safety_state)
    stopped = request_stop(manager)
    assert stopped.accepted is False
    assert stopped.reason == 'stop_not_allowed_in_safety_state'


def test_hold_allows_only_same_owner_same_mode_lease_renewal(manager, clock):
    acquire(manager)
    update_safety(manager, SAFETY_HOLD)
    clock.advance_ms(2999)

    renewed = manager.request_mode(MODE_MIMIC, OWNER_WEB)

    assert renewed.accepted is True
    assert renewed.reason == 'accepted'
    clock.advance_ms(2999)
    assert manager.accepts_source(SOURCE_MIMIC) is True


def test_run_safety_state_keeps_active_control(manager):
    acquire(manager)

    changed = update_safety(manager, SAFETY_RUN)

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
    update_safety(manager, SAFETY_FAULT)

    result = request_stop(manager)

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


def test_manager_rejects_unbounded_or_non_integer_timing_values():
    for kwargs in (
        {'owner_lease_timeout_ms': 3001},
        {'owner_lease_timeout_ms': True},
        {'stop_reacquire_delay_ms': 501},
        {'stop_reacquire_delay_ms': 1.5},
    ):
        with pytest.raises(ValueError):
            CommandManagerCore(**kwargs)
