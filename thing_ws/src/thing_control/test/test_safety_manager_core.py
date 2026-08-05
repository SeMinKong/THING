"""Specification tests for the minimal-interface eight-state safety core."""

import pytest

from thing_control.safety_manager_core import (
    ESTOP,
    FAULT,
    HOLD,
    INIT,
    READY,
    RESET,
    RUN,
    SAFE,
    HardwareStatus,
    SafetyLimits,
    SafetyManagerCore,
)


# 대부분의 상태기 단위 테스트는 wall time을 쓰지 않지만, 작은 값으로 경계를 빠르게
# 재현하면 각 테스트의 시각 관계가 읽기 쉽다. 운영 기본값 5초/10초는 별도 회귀
# 테스트에서 정확한 경계까지 검증한다.
_FAST_COMMAND_LIMITS = SafetyLimits(
    command_hold_ms=300,
    command_safe_ms=1000,
)


def ms(value):
    return value * 1_000_000


def healthy_status(time_ms, **overrides):
    values = {
        'received_ns': ms(time_ms),
        'stamp_ns': ms(time_ms),
        'motor_count': 7,
        'bus_communication_ok': True,
        'all_motors_communication_ok': True,
        'all_torque_off': True,
        'over_current': False,
        'over_temperature': False,
    }
    values.update(overrides)
    return HardwareStatus(**values)


def heartbeat(core, time_ms, **overrides):
    target_ns = ms(time_ms)
    period_ns = ms(100)

    last_estop_ns = (
        core._estop_received_ns
        if core._estop_received_ns is not None
        else core._started_ns
    )
    next_estop_ns = last_estop_ns + period_ns
    while next_estop_ns < target_ns:
        core.update_estop(False, next_estop_ns)
        next_estop_ns += period_ns

    last_hardware_ns = (
        core._hardware.received_ns
        if core._hardware is not None
        else core._started_ns
    )
    next_hardware_ns = last_hardware_ns + period_ns
    while next_hardware_ns < target_ns:
        core.update_hardware_status(healthy_status(next_hardware_ns // 1_000_000))
        next_hardware_ns += period_ns

    core.update_estop(False, target_ns)
    core.update_hardware_status(healthy_status(time_ms, **overrides))


def ready_core(limits=_FAST_COMMAND_LIMITS):
    core = SafetyManagerCore(limits=limits, started_ns=0)
    heartbeat(core, 0)
    core.tick(0)
    assert core.snapshot().state == READY
    return core


def enter_run(core, time_ms=10):
    core.on_validated_command(ms(time_ms))
    assert core.snapshot().state == RUN


def enter_hold(core):
    enter_run(core, 10)
    hold_at_ms = 10 + core._limits.command_hold_ms
    heartbeat(core, hold_at_ms)
    core.tick(ms(hold_at_ms))
    assert core.snapshot().state == HOLD


def begin_reset(core, time_ms=10):
    changed = core.on_control_stop_requested(
        now_ns=ms(time_ms),
        state_stamp_ns=ms(time_ms),
    )
    assert changed is True
    assert core.snapshot().state == RESET


def test_init_stays_fail_closed_when_trip_limits_are_unvalidated():
    core = SafetyManagerCore(started_ns=0, configuration_valid=False)
    core.update_hardware_status(healthy_status(0))
    core.update_estop(False, 0)
    core.tick(1)

    assert core.snapshot().state == INIT
    assert core.snapshot().reason == 'trip_limits_unvalidated'


def test_init_requires_fresh_estop_and_seven_healthy_torque_off_motors():
    core = SafetyManagerCore(started_ns=0)
    core.tick(ms(299))
    assert core.snapshot().state == INIT

    core.update_hardware_status(healthy_status(299))
    core.tick(ms(299))
    assert core.snapshot().state == INIT

    core.update_estop(False, ms(299))
    core.tick(ms(299))
    assert core.snapshot().state == READY
    assert core.snapshot().transition_epoch == 1


def test_startup_missing_estop_preempts_simultaneous_hardware_fault():
    core = SafetyManagerCore(started_ns=0)
    core.update_hardware_status(healthy_status(
        300,
        valid_measurement=False,
        invalid_reason='malformed_motor_status',
    ))

    assert core.snapshot().state == ESTOP
    assert core.snapshot().reason == 'estop_input_stale'


def test_configured_300ms_gap_enters_hold():
    core = ready_core()
    enter_run(core, 10)
    heartbeat(core, 309)
    core.tick(ms(309))
    assert core.snapshot().state == RUN

    heartbeat(core, 310)
    core.tick(ms(310))
    assert core.snapshot().state == HOLD
    assert core.snapshot().command_timeout is True


def test_validation_activity_closes_delayed_run_before_recovery():
    core = ready_core()
    enter_run(core)

    core.on_validated_activity(ms(310))

    assert core.snapshot().state == HOLD
    assert core.snapshot().command_timeout is True
    assert core.snapshot().reason == 'command_timeout_hold'


def test_hold_recovery_requires_300ms_continuous_validated_activity():
    core = ready_core()
    enter_hold(core)

    for time_ms in (400, 500, 600):
        core.on_validated_command(ms(time_ms))
        assert core.snapshot().state == HOLD
    core.on_validated_command(ms(700))
    assert core.snapshot().state == RUN
    assert core.snapshot().command_timeout is False

    heartbeat(core, 999)
    core.tick(ms(999))
    assert core.snapshot().state == RUN
    heartbeat(core, 1000)
    core.tick(ms(1000))
    assert core.snapshot().state == HOLD


def test_hold_recovery_window_restarts_after_activity_gap_over_100ms():
    core = ready_core()
    enter_hold(core)

    for time_ms in (400, 500, 601, 701, 801):
        core.on_validated_command(ms(time_ms))
    assert core.snapshot().state == HOLD
    core.on_validated_command(ms(901))
    assert core.snapshot().state == RUN


def test_hold_recovery_window_restarts_after_guard_validation_failure():
    core = ready_core()
    enter_hold(core)

    core.on_validated_activity(ms(400))
    core.on_validation_failed(ms(450))
    for time_ms in (500, 600, 700):
        core.on_validated_activity(ms(time_ms))
    assert core.snapshot().state == HOLD
    core.on_validated_activity(ms(800))
    assert core.snapshot().state == RUN


def test_configured_1000ms_gap_enters_safe_without_recovery():
    core = ready_core()
    enter_hold(core)
    heartbeat(core, 1009)
    core.tick(ms(1009))
    assert core.snapshot().state == HOLD
    heartbeat(core, 1010)
    core.tick(ms(1010))
    assert core.snapshot().state == SAFE


def test_safe_action_times_out_after_3000ms_without_torque_off_completion():
    core = ready_core()
    enter_hold(core)
    heartbeat(core, 1010, all_torque_off=False)
    core.tick(ms(1010))
    assert core.snapshot().state == SAFE

    for time_ms in range(1110, 4011, 100):
        core.update_estop(False, ms(time_ms))
        core.update_hardware_status(healthy_status(
            time_ms,
            all_torque_off=False,
        ))
        core.tick(ms(time_ms))

    assert core.snapshot().state == FAULT
    assert core.snapshot().reason == 'safe_action_timeout'


def test_safe_action_rejects_pre_entry_source_stamp_as_completion():
    core = ready_core()
    enter_hold(core)
    heartbeat(core, 900, all_torque_off=False)
    core.tick(ms(900))
    core.update_estop(False, ms(1010))
    core.update_hardware_status(healthy_status(
        1010,
        stamp_ns=ms(900),
        all_torque_off=False,
    ))
    core.tick(ms(1010))
    assert core.snapshot().state == SAFE

    # This sample is newer than the cached 900 ms status, but its source stamp
    # still predates the SAFE transition at 1010 ms. DDS may deliver it late.
    core.update_estop(False, ms(1110))
    core.update_hardware_status(healthy_status(
        1110,
        stamp_ns=ms(1005),
        all_torque_off=True,
    ))
    core.tick(ms(1110))

    for time_ms in range(1210, 4011, 100):
        core.update_estop(False, ms(time_ms))
        core.update_hardware_status(healthy_status(
            time_ms,
            all_torque_off=False,
        ))
        core.tick(ms(time_ms))

    assert core.snapshot().state == FAULT
    assert core.snapshot().reason == 'safe_action_timeout'


def test_safe_action_accepts_post_entry_torque_off_completion():
    core = ready_core()
    enter_hold(core)
    heartbeat(core, 1010, all_torque_off=False)
    core.tick(ms(1010))
    assert core.snapshot().state == SAFE

    for time_ms in range(1110, 4011, 100):
        core.update_estop(False, ms(time_ms))
        core.update_hardware_status(healthy_status(
            time_ms,
            all_torque_off=True,
        ))
        core.tick(ms(time_ms))

    assert core.snapshot().state == SAFE
    assert core.snapshot().reason == 'command_timeout_safe'


def test_safe_action_rejects_torque_off_completion_at_deadline_before_tick():
    core = ready_core()
    enter_hold(core)
    heartbeat(core, 1010, all_torque_off=False)
    core.tick(ms(1010))
    assert core.snapshot().state == SAFE

    for time_ms in range(1110, 4010, 100):
        core.update_estop(False, ms(time_ms))
        core.update_hardware_status(healthy_status(
            time_ms,
            all_torque_off=False,
        ))
    core.update_estop(False, ms(4010))
    core.update_hardware_status(healthy_status(
        4010,
        all_torque_off=True,
    ))

    assert core.snapshot().state == FAULT
    assert core.snapshot().reason == 'safe_action_timeout'


def test_hold_recovery_at_absolute_safe_deadline_prefers_safe():
    core = ready_core()
    enter_hold(core)

    for time_ms in (710, 810, 910):
        core.on_validated_command(ms(time_ms))
        assert core.snapshot().state == HOLD
    core.on_validated_command(ms(1010))

    assert core.snapshot().state == SAFE
    assert core.snapshot().reason == 'command_timeout_safe'


def test_existing_stop_event_enters_reset_from_ready_run_or_hold():
    ready = ready_core()
    begin_reset(ready)

    running = ready_core()
    enter_run(running)
    begin_reset(running, 20)

    hold = ready_core()
    enter_hold(hold)
    assert hold.on_control_stop_requested(
        now_ns=ms(320),
        state_stamp_ns=ms(320),
    ) is True
    assert hold.snapshot().state == RESET


def test_reset_requires_500ms_and_motor_status_stamped_after_reset_entry():
    core = ready_core()
    begin_reset(core, 10)

    # Arrived later, but its measurement timestamp predates RESET.
    heartbeat(
        core,
        510,
        stamp_ns=ms(9),
        all_torque_off=True,
    )
    core.tick(ms(510))
    assert core.snapshot().state == RESET

    core.update_hardware_status(healthy_status(
        511,
        stamp_ns=ms(511),
        all_torque_off=False,
    ))
    core.update_estop(False, ms(511))
    core.tick(ms(511))
    assert core.snapshot().state == RESET

    core.update_hardware_status(healthy_status(
        512,
        stamp_ns=ms(512),
        all_torque_off=True,
    ))
    core.update_estop(False, ms(512))
    core.tick(ms(512))
    assert core.snapshot().state == READY


def test_reset_rejects_source_stamp_equal_to_reset_entry():
    core = ready_core()
    begin_reset(core, 10)

    for time_ms in (110, 210, 310, 410):
        core.update_estop(False, ms(time_ms))
        core.update_hardware_status(healthy_status(
            time_ms,
            all_torque_off=False,
        ))
    core.update_estop(False, ms(510))
    core.update_hardware_status(healthy_status(
        510,
        stamp_ns=ms(10),
        all_torque_off=True,
    ))
    core.tick(ms(510))

    assert core.snapshot().state == RESET


def test_safety_reset_requires_new_estop_and_motor_samples_after_init_entry():
    """A fresh pre-reset cache is not the same as re-running INIT checks."""
    core = ready_core()
    core.force_fault('synthetic_fault')
    for time_ms in (10, 210, 410, 610, 810, 1010):
        heartbeat(core, time_ms)
        core.tick(ms(time_ms))

    accepted = core.request_safety_reset(
        ms(1010),
        state_stamp_ns=ms(1010),
    )
    assert accepted.accepted
    assert core.snapshot().state == INIT

    # The sample at 1010 ms arrived before INIT began. It cannot complete the
    # new inspection merely because it remains inside the 300 ms freshness TTL.
    core.tick(ms(1011))
    assert core.snapshot().state == INIT

    core.update_estop(False, ms(1011))
    core.update_hardware_status(healthy_status(
        1011,
        stamp_ns=ms(1010),
    ))
    core.tick(ms(1011))
    assert core.snapshot().state == INIT

    core.update_hardware_status(healthy_status(1012))
    core.tick(ms(1012))
    assert core.snapshot().state == READY


def test_motor_communication_fault_requires_three_consecutive_failed_reads():
    core = ready_core()

    for time_ms in (10, 20):
        core.update_estop(False, ms(time_ms))
        core.update_hardware_status(healthy_status(
            time_ms,
            all_motors_communication_ok=False,
        ))
        assert core.snapshot().state == READY

    core.update_estop(False, ms(30))
    core.update_hardware_status(healthy_status(
        30,
        all_motors_communication_ok=False,
    ))
    assert core.snapshot().state == FAULT
    assert core.snapshot().reason == 'motor_communication_failed'


def test_bus_communication_fault_requires_300ms_continuous_failure():
    core = ready_core()

    core.update_hardware_status(healthy_status(
        10,
        bus_communication_ok=False,
    ))
    assert core.snapshot().state == READY

    for time_ms in (100, 200, 309):
        core.update_estop(False, ms(time_ms))
        core.update_hardware_status(healthy_status(
            time_ms,
            bus_communication_ok=False,
        ))
        assert core.snapshot().state == READY

    core.update_estop(False, ms(310))
    core.tick(ms(310))
    assert core.snapshot().state == FAULT
    assert core.snapshot().reason == 'bus_communication_failed'


def test_owner_lease_expiry_enters_hold_immediately_from_run():
    core = ready_core()
    enter_run(core, 10)

    changed = core.on_owner_lease_expired(ms(20))

    assert changed is True
    assert core.snapshot().state == HOLD
    assert core.snapshot().command_timeout is True
    assert core.snapshot().reason == 'owner_lease_expired'


def test_reset_timeout_or_hardware_failure_enters_fault():
    timeout = ready_core()
    begin_reset(timeout, 10)
    heartbeat(timeout, 3009, all_torque_off=False)
    timeout.tick(ms(3009))
    assert timeout.snapshot().state == RESET
    heartbeat(timeout, 3010, all_torque_off=False)
    timeout.tick(ms(3010))
    assert timeout.snapshot().state == FAULT
    assert timeout.snapshot().reason == 'reset_action_timeout'

    failed = ready_core()
    begin_reset(failed, 10)
    for time_ms in (20, 30, 40):
        failed.update_estop(False, ms(time_ms))
        failed.update_hardware_status(healthy_status(
            time_ms,
            bus_communication_ok=False,
            all_motors_communication_ok=False,
        ))
    assert failed.snapshot().state == FAULT
    assert failed.snapshot().reason == 'motor_communication_failed'


def test_estop_and_fault_preempt_reset():
    estop = ready_core()
    begin_reset(estop, 10)
    estop.update_estop(True, ms(20))
    assert estop.snapshot().state == ESTOP

    fault = ready_core()
    begin_reset(fault, 10)
    fault.update_hardware_status(healthy_status(20, over_current=True))
    assert fault.snapshot().state == FAULT


def test_estop_reset_requires_fresh_inactive_input_stable_for_500ms():
    core = ready_core()
    core.update_estop(True, ms(10))
    assert core.snapshot().state == ESTOP

    core.update_estop(False, ms(20))
    heartbeat(core, 519)
    early = core.request_safety_reset(ms(519))
    assert not early.accepted
    assert early.reason == 'estop_release_not_stable'

    heartbeat(core, 520)
    accepted = core.request_safety_reset(ms(520))
    assert accepted.accepted
    assert core.snapshot().state == INIT
    heartbeat(core, 521)
    core.tick(ms(521))
    assert core.snapshot().state == READY


def test_estop_with_hardware_fault_requires_both_clear_windows():
    core = ready_core()
    core.update_estop(True, ms(10))
    core.update_hardware_status(healthy_status(20, over_current=True))
    assert core.snapshot().state == ESTOP

    core.update_hardware_status(healthy_status(100))
    core.update_estop(False, ms(110))
    for time_ms in (200, 300, 400, 500, 600, 610):
        core.update_estop(False, ms(time_ms))
        core.update_hardware_status(healthy_status(time_ms))
    too_early = core.request_safety_reset(ms(610))
    assert not too_early.accepted
    assert too_early.reason == 'fault_clear_not_stable'

    for time_ms in (700, 800, 900, 1000, 1100):
        core.update_estop(False, ms(time_ms))
        core.update_hardware_status(healthy_status(time_ms))
    accepted = core.request_safety_reset(ms(1100))
    assert accepted.accepted
    assert core.snapshot().state == INIT


def test_safe_then_estop_keeps_safe_fault_clear_window():
    core = ready_core()
    enter_hold(core)
    heartbeat(core, 1010)
    core.tick(ms(1010))
    assert core.snapshot().state == SAFE

    core.update_estop(True, ms(1020))
    assert core.snapshot().state == ESTOP
    core.update_hardware_status(healthy_status(1100))
    core.update_estop(False, ms(1100))
    for time_ms in (1200, 1300, 1400, 1500, 1600):
        core.update_hardware_status(healthy_status(time_ms))
        core.update_estop(False, ms(time_ms))

    too_early = core.request_safety_reset(ms(1600))
    assert not too_early.accepted
    assert too_early.reason == 'fault_clear_not_stable'

    for time_ms in (1700, 1800, 1900, 2000, 2100):
        core.update_hardware_status(healthy_status(time_ms))
        core.update_estop(False, ms(time_ms))
    assert core.request_safety_reset(ms(2100)).accepted


def test_fault_then_estop_keeps_both_clear_windows():
    core = ready_core()
    core.force_fault('forced_fault')
    core.update_estop(True, ms(10))
    assert core.snapshot().state == ESTOP

    core.update_hardware_status(healthy_status(100))
    core.update_estop(False, ms(100))
    for time_ms in (200, 300, 400, 500, 600):
        core.update_hardware_status(healthy_status(time_ms))
        core.update_estop(False, ms(time_ms))
    too_early = core.request_safety_reset(ms(600))
    assert not too_early.accepted
    assert too_early.reason == 'fault_clear_not_stable'

    for time_ms in (700, 800, 900, 1000, 1100):
        core.update_hardware_status(healthy_status(time_ms))
        core.update_estop(False, ms(time_ms))
    assert core.request_safety_reset(ms(1100)).accepted


def test_estop_release_window_restarts_after_heartbeat_staleness():
    core = ready_core()
    core.update_estop(True, ms(10))
    core.update_estop(False, ms(20))

    # E-Stop heartbeat만 끊긴 경로를 시험한다. MotorStatus는 fresh하게 유지해
    # 복합 fault의 1000 ms clear window가 이 단일 원인 테스트에 섞이지 않게 한다.
    for time_ms in (100, 200, 300, 320):
        core.update_hardware_status(healthy_status(time_ms))
    core.tick(ms(320))
    assert core.snapshot().state == ESTOP
    assert core.snapshot().reason == 'estop_input_stale'

    core.update_estop(False, ms(321))
    heartbeat(core, 820)
    early = core.request_safety_reset(ms(820))
    assert not early.accepted
    assert early.reason == 'estop_release_not_stable'

    heartbeat(core, 821)
    accepted = core.request_safety_reset(ms(821))
    assert accepted.accepted


def test_resumed_estop_heartbeat_cannot_mask_gap_without_tick():
    core = ready_core()
    core.update_estop(True, ms(10))
    core.update_estop(False, ms(20))

    core.update_estop(False, ms(600))

    assert core.snapshot().state == ESTOP
    assert core.snapshot().reason == 'estop_input_stale'
    rejected = core.request_safety_reset(ms(600))
    assert not rejected.accepted
    assert rejected.reason == 'estop_release_not_stable'


def test_resumed_motor_status_cannot_mask_gap_without_tick():
    core = ready_core()
    core.update_estop(False, ms(299))

    core.update_hardware_status(healthy_status(300))

    assert core.snapshot().state == FAULT
    assert core.snapshot().reason == 'motor_status_stale'


def test_fault_reset_requires_healthy_inputs_stable_for_1000ms():
    core = ready_core()
    core.force_fault('synthetic_fault')

    for time_ms in (10, 210, 410, 610, 810, 1009):
        heartbeat(core, time_ms)
        core.tick(ms(time_ms))
    early = core.request_safety_reset(ms(1009))
    assert not early.accepted
    assert early.reason == 'fault_clear_not_stable'

    heartbeat(core, 1010)
    accepted = core.request_safety_reset(ms(1010))
    assert accepted.accepted
    assert core.snapshot().state == INIT


def test_reset_safety_is_rejected_in_normal_and_reset_states():
    for state in (INIT, READY, RUN, HOLD, RESET):
        core = SafetyManagerCore(started_ns=0)
        heartbeat(core, 0)
        if state == READY:
            core.tick(0)
        elif state == RUN:
            core.tick(0)
            enter_run(core)
        elif state == HOLD:
            core.tick(0)
            enter_hold(core)
        elif state == RESET:
            core.tick(0)
            begin_reset(core)
        result = core.request_safety_reset(ms(20))
        assert not result.accepted
        assert result.reason == 'safety_reset_not_allowed'


def test_stale_estop_input_fails_closed_to_estop():
    core = ready_core()
    core.update_hardware_status(healthy_status(300))
    core.tick(ms(300))
    assert core.snapshot().state == ESTOP
    assert core.snapshot().reason == 'estop_input_stale'


def test_missing_estop_from_startup_fails_closed_after_300ms():
    core = SafetyManagerCore(started_ns=0)
    core.tick(ms(299))
    assert core.snapshot().state == INIT
    core.tick(ms(300))
    assert core.snapshot().state == ESTOP
    assert core.snapshot().reason == 'estop_input_stale'


def test_estop_cannot_be_demoted_by_concurrent_hardware_fault():
    core = ready_core()
    core.update_estop(True, ms(10))
    core.update_hardware_status(HardwareStatus(
        received_ns=ms(20),
        stamp_ns=ms(20),
        motor_count=0,
        bus_communication_ok=False,
        all_motors_communication_ok=False,
        all_torque_off=False,
        over_current=False,
        over_temperature=False,
        valid_measurement=False,
        invalid_reason='motor_set_invalid',
    ))
    assert core.snapshot().state == ESTOP


def test_malformed_hardware_status_fails_closed():
    core = ready_core()
    core.update_hardware_status(HardwareStatus(
        received_ns=ms(10),
        stamp_ns=ms(10),
        motor_count=7,
        bus_communication_ok=True,
        all_motors_communication_ok=True,
        all_torque_off=True,
        over_current=False,
        over_temperature=False,
        valid_measurement=False,
        invalid_reason='duplicate_motor_ids',
    ))
    assert core.snapshot().state == FAULT
    assert core.snapshot().reason == 'duplicate_motor_ids'


def test_reset_exact_timeout_boundary_is_fault_even_with_late_ack():
    core = ready_core()
    begin_reset(core, 0)
    heartbeat(core, 3000)
    core.tick(ms(3000))
    assert core.snapshot().state == FAULT
    assert core.snapshot().reason == 'reset_action_timeout'


def test_intermittent_hold_activity_cannot_postpone_total_safe_timeout():
    core = ready_core()
    enter_run(core, 0)
    heartbeat(core, 300)
    core.tick(ms(300))
    assert core.snapshot().state == HOLD

    for time_ms in (400, 550, 700, 850, 1000):
        heartbeat(core, time_ms)
        core.on_validated_command(ms(time_ms))
        core.tick(ms(time_ms))
    assert core.snapshot().state == SAFE


def test_state_transition_epoch_changes_only_internally_on_transitions():
    core = ready_core()
    ready_epoch = core.snapshot().transition_epoch
    heartbeat(core, 2)
    core.tick(ms(2))
    assert core.snapshot().transition_epoch == ready_epoch

    enter_run(core, 10)
    assert core.snapshot().transition_epoch == ready_epoch + 1


def test_default_command_watchdog_enters_hold_at_5s_and_safe_at_10s():
    limits = SafetyLimits()
    assert limits.command_hold_ms == 5000
    assert limits.command_safe_ms == 10000

    core = SafetyManagerCore(started_ns=0)
    heartbeat(core, 0)
    core.tick(0)
    enter_run(core, 0)

    heartbeat(core, 4999)
    core.tick(ms(4999))
    assert core.snapshot().state == RUN

    heartbeat(core, 5000)
    core.tick(ms(5000))
    assert core.snapshot().state == HOLD

    heartbeat(core, 9999)
    core.tick(ms(9999))
    assert core.snapshot().state == HOLD

    heartbeat(core, 10000)
    core.tick(ms(10000))
    assert core.snapshot().state == SAFE


@pytest.mark.parametrize(
    'kwargs',
    [
        {'command_hold_ms': 5001},
        {'command_safe_ms': 10001},
        {'recovery_stable_ms': 299},
        {'recovery_stable_ms': 1001},
        {'recovery_max_gap_ms': 101},
        {'reset_min_ms': 499},
        {'reset_min_ms': 3000},
        {'reset_timeout_ms': 3001},
        {'estop_release_ms': 499},
        {'estop_release_ms': 501},
        {'fault_clear_stable_ms': 999},
        {'fault_clear_stable_ms': 1001},
        {'hardware_status_timeout_ms': 301},
        {'estop_input_timeout_ms': 301},
    ],
)
def test_fixed_safety_envelopes_cannot_be_widened(kwargs):
    with pytest.raises(ValueError):
        SafetyLimits(**kwargs)
