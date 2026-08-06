"""Contract tests for the unified manual motion arbiter."""

import math

import pytest

from thing_control.manual_executor_core import (
    ManualExecutorCore,
    SequenceStep,
)


NS_PER_MS = 1_000_000
OPEN = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
FIST = (1.0, 0.6, 0.2, 1.0, 1.0, 1.0, 1.0)
PINCH = (1.0, 0.6, 0.2, 1.0, 0.0, 0.0, 0.0)


def make_core(
    control_timeout_ms=1500,
    safety_timeout_ms=300,
    gesture_durations_ms=None,
    sequences=None,
):
    durations = gesture_durations_ms or {
        'open': 100,
        'fist': 100,
        'pinch': 300,
        'cylindrical_grasp': 300,
    }
    configured_sequences = sequences or {
        'countdown': (
            SequenceStep('open', 50),
            SequenceStep('pinch', 75),
            SequenceStep('fist', 100),
        ),
    }
    core = ManualExecutorCore(
        gestures={
            'open': OPEN,
            'fist': FIST,
            'pinch': PINCH,
            'cylindrical_grasp': (0.8,) * 7,
        },
        gesture_durations_ms=durations,
        sequences=configured_sequences,
        control_state_timeout_ms=control_timeout_ms,
        safety_state_timeout_ms=safety_timeout_ms,
    )
    core.update_control_state(
        active_mode=ManualExecutorCore.MODE_MANUAL,
        active_owner=ManualExecutorCore.OWNER_WEB,
        owner_alive=True,
        now_ns=0,
    )
    core.update_safety_state(ManualExecutorCore.SAFETY_READY, now_ns=0)
    return core


def test_gesture_acceptance_requires_fresh_manual_web_control_and_safe_state():
    core = make_core()

    accepted = core.start_gesture('open', 0.5, now_ns=1)

    assert accepted.accepted is True
    assert accepted.reason == 'accepted'
    assert accepted.generation > 0
    assert core.motion_active is True
    assert core.is_sequence_running is False


@pytest.mark.parametrize(
    ('gesture_name', 'canonical_name'),
    (
        ('home', 'open'),
        ('paper', 'open'),
        ('rock', 'fist'),
    ),
)
def test_gesture_aliases_share_the_canonical_pose(gesture_name, canonical_name):
    core = make_core()

    result = core.start_gesture(gesture_name, 1.0, now_ns=1)
    tick = core.tick(now_ns=1)

    assert result.accepted is True
    assert tick.command.gesture_name == canonical_name
    assert tick.command.axes == core.gestures[canonical_name]


@pytest.mark.parametrize('speed_limit', (0.0, -0.1, 1.01, math.nan, math.inf))
def test_invalid_speed_limits_are_rejected_fail_closed(speed_limit):
    core = make_core()

    result = core.start_gesture('open', speed_limit, now_ns=1)

    assert result.accepted is False
    assert result.reason == 'invalid_speed_limit'
    assert core.motion_active is False


def test_unknown_gesture_and_sequence_names_are_rejected():
    core = make_core()

    gesture = core.start_gesture('not-a-gesture', 1.0, now_ns=1)
    sequence = core.start_sequence('not-a-sequence', 1.0, now_ns=1)

    assert (gesture.accepted, gesture.reason) == (False, 'invalid_gesture')
    assert (sequence.accepted, sequence.reason) == (False, 'invalid_sequence')


def test_service_and_action_requests_are_mutually_exclusive_in_both_directions():
    gesture_first = make_core()
    gesture = gesture_first.start_gesture('open', 1.0, now_ns=1)
    blocked_sequence = gesture_first.start_sequence('countdown', 1.0, now_ns=2)

    assert gesture.accepted is True
    assert (blocked_sequence.accepted, blocked_sequence.reason) == (
        False,
        'motion_active',
    )

    sequence_first = make_core()
    sequence = sequence_first.start_sequence('countdown', 1.0, now_ns=1)
    blocked_gesture = sequence_first.start_gesture('open', 1.0, now_ns=2)

    assert sequence.accepted is True
    assert sequence_first.is_sequence_running is True
    assert (blocked_gesture.accepted, blocked_gesture.reason) == (
        False,
        'motion_active',
    )


def test_completed_gesture_keeps_last_pose_fresh_and_reopens_admission():
    core = make_core()
    started = core.start_gesture('fist', 0.6, now_ns=0)

    first = core.tick(now_ns=0)
    last = core.tick(now_ns=99 * NS_PER_MS)
    completed = core.tick(now_ns=100 * NS_PER_MS)
    retained = core.tick(now_ns=200 * NS_PER_MS)

    assert started.accepted is True
    assert first.command.axes == FIST
    assert first.command.source == ManualExecutorCore.SOURCE_GESTURE
    assert first.command.speed_limit == pytest.approx(0.6)
    assert last.command is not None
    # 완료 tick도 마지막 pose를 싣는다. 그렇지 않으면 20 Hz timer 한 주기를 건너뛰어
    # 마지막 active frame과 첫 retained heartbeat 사이가 최대 100 ms가 된다.
    assert completed.command.axes == FIST
    assert completed.outcome.generation == started.generation
    assert completed.outcome.success is True
    assert completed.outcome.reason == 'completed'
    assert retained.command.axes == FIST
    assert retained.command.source == ManualExecutorCore.SOURCE_GESTURE
    assert retained.command.speed_limit == pytest.approx(0.6)
    assert retained.outcome is None
    assert core.motion_active is False

    replacement = core.start_gesture('open', 0.4, now_ns=201 * NS_PER_MS)
    replaced = core.tick(now_ns=201 * NS_PER_MS)

    assert replacement.accepted is True
    assert replaced.command.axes == OPEN
    assert replaced.command.speed_limit == pytest.approx(0.4)


def test_sequence_progresses_steps_and_reports_one_based_feedback():
    core = make_core()
    started = core.start_sequence('countdown', 0.7, now_ns=0)

    step_1 = core.tick(now_ns=0)
    step_2 = core.tick(now_ns=50 * NS_PER_MS)
    step_3 = core.tick(now_ns=125 * NS_PER_MS)
    done = core.tick(now_ns=225 * NS_PER_MS)
    retained = core.tick(now_ns=226 * NS_PER_MS)

    assert started.accepted is True
    assert (step_1.current_step, step_1.total_steps) == (1, 3)
    assert step_1.command.gesture_name == 'open'
    assert step_1.command.source == ManualExecutorCore.SOURCE_SEQUENCE
    assert (step_2.current_step, step_2.command.gesture_name) == (2, 'pinch')
    assert (step_3.current_step, step_3.command.gesture_name) == (3, 'fist')
    assert done.command.axes == FIST
    assert done.outcome.success is True
    assert done.outcome.reason == 'completed'
    assert retained.command.gesture_name == 'fist'
    assert retained.command.axes == FIST
    assert retained.command.source == ManualExecutorCore.SOURCE_SEQUENCE
    assert core.is_sequence_running is False


def test_delayed_sequence_tick_never_skips_a_configured_pose():
    core = make_core(
        sequences={
            'fast': (
                SequenceStep('open', 10),
                SequenceStep('fist', 10),
            ),
        }
    )
    started = core.start_sequence('fast', 1.0, now_ns=0)
    assert started.accepted is True
    assert core.tick(now_ns=0).command.gesture_name == 'open'

    delayed = core.tick(now_ns=25 * NS_PER_MS)
    assert delayed.outcome is None
    assert delayed.command.gesture_name == 'fist'
    assert delayed.current_step == 2

    completed = core.tick(now_ns=35 * NS_PER_MS)
    assert completed.outcome.success is True


def test_cancelled_sequence_never_emits_another_command():
    core = make_core()
    started = core.start_sequence('countdown', 1.0, now_ns=0)
    assert core.tick(now_ns=0).command is not None

    cancelled = core.cancel('cancel_requested')
    after = core.tick(now_ns=1)

    assert cancelled.generation == started.generation
    assert cancelled.success is False
    assert cancelled.reason == 'cancel_requested'
    assert after.command is None
    assert core.motion_active is False


def test_stop_control_loss_and_unsafe_state_clear_a_retained_pose():
    stopped = make_core()
    stopped.start_gesture('fist', 1.0, now_ns=0)
    stopped.tick(now_ns=100 * NS_PER_MS)
    assert stopped.tick(now_ns=101 * NS_PER_MS).command is not None
    assert stopped.cancel('stop_requested') is None
    assert stopped.tick(now_ns=102 * NS_PER_MS).command is None

    lost = make_core()
    lost.start_gesture('fist', 1.0, now_ns=0)
    lost.tick(now_ns=100 * NS_PER_MS)
    lost.update_control_state(
        active_mode=ManualExecutorCore.MODE_DISABLED,
        active_owner=ManualExecutorCore.OWNER_NONE,
        owner_alive=False,
        now_ns=101 * NS_PER_MS,
    )
    assert lost.tick(now_ns=102 * NS_PER_MS).command is None

    unsafe = make_core()
    unsafe.start_gesture('fist', 1.0, now_ns=0)
    unsafe.tick(now_ns=100 * NS_PER_MS)
    unsafe.update_safety_state(
        ManualExecutorCore.SAFETY_HOLD,
        now_ns=101 * NS_PER_MS,
    )
    assert unsafe.tick(now_ns=102 * NS_PER_MS).command is None


def test_stale_state_clears_a_retained_pose_fail_closed():
    core = make_core(control_timeout_ms=100, safety_timeout_ms=300)
    core.start_gesture('fist', 1.0, now_ns=0)
    core.tick(now_ns=100 * NS_PER_MS)

    stale = core.tick(now_ns=101 * NS_PER_MS)
    after = core.tick(now_ns=102 * NS_PER_MS)

    assert stale.command is None
    assert stale.outcome is None
    assert after.command is None


@pytest.mark.parametrize(
    ('unsafe_state', 'expected_reason'),
    (
        (ManualExecutorCore.SAFETY_HOLD, 'safety_hold'),
        (ManualExecutorCore.SAFETY_SAFE, 'safety_safe'),
        (ManualExecutorCore.SAFETY_FAULT, 'safety_fault'),
        (ManualExecutorCore.SAFETY_ESTOP, 'safety_estop'),
    ),
)
def test_safety_transition_preempts_any_active_manual_motion(
    unsafe_state,
    expected_reason,
):
    core = make_core()
    core.start_sequence('countdown', 1.0, now_ns=0)

    outcome = core.update_safety_state(unsafe_state, now_ns=1)

    assert outcome.reason == expected_reason
    assert outcome.success is False
    assert core.motion_active is False
    assert core.tick(now_ns=2).command is None


def test_stop_and_control_loss_preempt_manual_motion():
    stopped = make_core()
    stopped.start_gesture('open', 1.0, now_ns=0)
    stop_outcome = stopped.cancel('stop_requested')

    lost = make_core()
    lost.start_gesture('open', 1.0, now_ns=0)
    control_outcome = lost.update_control_state(
        active_mode=ManualExecutorCore.MODE_DISABLED,
        active_owner=ManualExecutorCore.OWNER_NONE,
        owner_alive=False,
        now_ns=1,
    )

    assert stop_outcome.reason == 'stop_requested'
    assert control_outcome.reason == 'control_lost'
    assert stopped.tick(now_ns=2).command is None
    assert lost.tick(now_ns=2).command is None


def test_stale_control_or_safety_heartbeat_cancels_motion():
    control_stale = make_core(control_timeout_ms=100, safety_timeout_ms=300)
    control_stale.start_gesture('open', 1.0, now_ns=0)
    control_result = control_stale.tick(now_ns=101 * NS_PER_MS)

    safety_stale = make_core(control_timeout_ms=300, safety_timeout_ms=100)
    safety_stale.start_gesture('open', 1.0, now_ns=0)
    safety_result = safety_stale.tick(now_ns=101 * NS_PER_MS)

    assert control_result.command is None
    assert control_result.outcome.reason == 'control_state_stale'
    assert safety_result.command is None
    assert safety_result.outcome.reason == 'safety_state_stale'


def test_requests_without_state_heartbeats_are_rejected():
    core = ManualExecutorCore(
        gestures={
            'open': OPEN,
            'fist': FIST,
            'pinch': PINCH,
            'cylindrical_grasp': (0.8,) * 7,
        },
        gesture_durations_ms={
            'open': 100,
            'fist': 100,
            'pinch': 300,
            'cylindrical_grasp': 300,
        },
        sequences={},
    )

    result = core.start_gesture('open', 1.0, now_ns=0)

    assert result.accepted is False
    assert result.reason == 'control_state_unavailable'


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({'control_timeout_ms': 1501}, 'control timeout'),
        ({'safety_timeout_ms': 301}, 'safety timeout'),
    ],
)
def test_core_rejects_timing_values_that_widen_authoritative_limits(
    kwargs,
    message,
):
    with pytest.raises(ValueError, match=message):
        make_core(**kwargs)


def test_core_rejects_unbounded_gesture_and_sequence_configuration():
    with pytest.raises(ValueError, match='gesture duration'):
        make_core(gesture_durations_ms={
            'open': 10_001,
            'fist': 100,
            'pinch': 300,
            'cylindrical_grasp': 300,
        })

    with pytest.raises(ValueError, match='step duration'):
        make_core(sequences={
            'bad': (SequenceStep('open', 10_001),),
        })
