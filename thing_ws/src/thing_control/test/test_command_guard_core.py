"""Pure policy tests for fail-closed HandCommand validation."""

import pytest

from thing_control.command_guard_core import (
    AXIS_NAMES,
    MODE_DISABLED,
    MODE_MANUAL,
    MODE_MIMIC,
    MODE_TELEOP,
    OWNER_LOCAL,
    OWNER_NONE,
    OWNER_WEB,
    SAFETY_READY,
    SAFETY_RUN,
    SOURCE_GESTURE,
    SOURCE_MIMIC,
    SOURCE_SAFETY,
    SOURCE_SEQUENCE,
    SOURCE_TELEOP,
    CommandGuardCore,
    GuardCommand,
    GuardLimits,
)


SAFETY_HOLD = 3


def make_limits(**overrides):
    values = {
        'command_stale_timeout_ms': 300,
        'command_future_tolerance_ms': 100,
        'safety_state_timeout_ms': 1500,
        'control_state_timeout_ms': 1500,
        'command_hold_ms': 300,
        'axis_min': {name: 0.0 for name in AXIS_NAMES},
        'axis_max': {name: 1.0 for name in AXIS_NAMES},
        'max_axis_delta_per_second': {name: 10.0 for name in AXIS_NAMES},
        'mimic_max_axis_delta_per_second': {
            name: 10.0 for name in AXIS_NAMES
        },
    }
    values.update(overrides)
    return GuardLimits(**values)


def make_command(**overrides):
    values = {
        'stamp_ns': 10_000_000_000,
        'sequence': 1,
        'source': SOURCE_MIMIC,
        'axes': {name: 0.5 for name in AXIS_NAMES},
        'speed_limit': 0.5,
        'confidence': 0.9,
    }
    values.update(overrides)
    return GuardCommand(**values)


def activate_mimic(core, monotonic_ns=1_000_000_000):
    core.update_safety_state(SAFETY_READY, monotonic_ns)
    core.update_control_state(
        MODE_DISABLED,
        OWNER_NONE,
        False,
        monotonic_ns,
    )
    core.update_control_state(
        MODE_MIMIC,
        OWNER_WEB,
        True,
        monotonic_ns,
    )


def test_valid_command_is_accepted_after_observed_disabled_to_active_transition():
    core = CommandGuardCore(make_limits())
    activate_mimic(core)

    decision = core.validate(
        make_command(),
        now_ros_ns=10_050_000_000,
        now_monotonic_ns=1_050_000_000,
    )

    assert decision.accepted is True
    assert decision.reason == 'accepted'


def test_stop_latch_rejects_queued_commands_until_new_disabled_to_active_cycle():
    core = CommandGuardCore(make_limits())
    activate_mimic(core)
    core.on_stop_requested()

    assert_rejected(core, 'stop_latched')

    # A late active state from before STOP must not reopen the gate.
    core.update_control_state(MODE_MIMIC, OWNER_WEB, True, 1_100_000_000)
    assert_rejected(core, 'stop_latched', monotonic_ns=1_100_000_000)

    core.update_control_state(MODE_DISABLED, OWNER_NONE, False, 1_200_000_000)
    core.update_control_state(MODE_MIMIC, OWNER_WEB, True, 1_300_000_000)
    decision = core.validate(
        make_command(sequence=2),
        now_ros_ns=10_050_000_000,
        now_monotonic_ns=1_300_000_000,
    )
    assert decision.accepted is True


def assert_rejected(core, reason, command=None, ros_ns=10_050_000_000,
                    monotonic_ns=1_050_000_000):
    decision = core.validate(
        command or make_command(),
        now_ros_ns=ros_ns,
        now_monotonic_ns=monotonic_ns,
    )
    assert decision.accepted is False
    assert decision.reason == reason


def test_ready_and_run_accept_each_supported_mode_owner_source_contract():
    cases = (
        (SAFETY_READY, MODE_MIMIC, OWNER_WEB, SOURCE_MIMIC),
        (SAFETY_RUN, MODE_MANUAL, OWNER_WEB, SOURCE_GESTURE),
        (SAFETY_READY, MODE_MANUAL, OWNER_WEB, SOURCE_SEQUENCE),
        (SAFETY_RUN, MODE_TELEOP, OWNER_LOCAL, SOURCE_TELEOP),
    )
    for safety_state, mode, owner, source in cases:
        core = CommandGuardCore(make_limits())
        core.update_safety_state(safety_state, 1_000_000_000)
        core.update_control_state(
            MODE_DISABLED,
            OWNER_NONE,
            False,
            1_000_000_000,
        )
        core.update_control_state(mode, owner, True, 1_000_000_000)
        decision = core.validate(
            make_command(source=source),
            now_ros_ns=10_050_000_000,
            now_monotonic_ns=1_050_000_000,
        )
        assert decision.accepted is True


@pytest.mark.parametrize('unsafe_state', [0, 4, 5, 6, 7])
def test_non_motion_safety_states_are_rejected(unsafe_state):
    core = CommandGuardCore(make_limits())
    activate_mimic(core)
    core.update_safety_state(unsafe_state, 1_000_000_000)
    assert_rejected(core, 'safety_not_ready')


def test_hold_validates_activity_without_forwarding_and_run_resumes_same_owner():
    core = CommandGuardCore(make_limits())
    activate_mimic(core)
    first = core.validate(
        make_command(sequence=10),
        now_ros_ns=10_050_000_000,
        now_monotonic_ns=1_050_000_000,
    )
    assert first.accepted is True

    core.update_safety_state(SAFETY_HOLD, 1_100_000_000)
    activity = core.validate(
        make_command(sequence=11),
        now_ros_ns=10_050_000_000,
        now_monotonic_ns=1_110_000_000,
    )
    assert activity.accepted is True
    assert activity.reason == 'hold_activity'
    assert activity.forward_to_hardware is False

    core.update_safety_state(SAFETY_RUN, 1_120_000_000)
    resumed = core.validate(
        make_command(sequence=12),
        now_ros_ns=10_060_000_000,
        now_monotonic_ns=1_130_000_000,
    )
    assert resumed.accepted is True
    assert resumed.forward_to_hardware is True


def test_local_hold_barrier_blocks_forward_before_hold_state_callback():
    core = CommandGuardCore(make_limits())
    activate_mimic(core)
    first = core.validate(
        make_command(sequence=20),
        now_ros_ns=10_000_000_000,
        now_monotonic_ns=1_000_000_000,
    )
    assert first.forward_to_hardware is True

    raced = core.validate(
        make_command(sequence=21),
        now_ros_ns=10_100_000_000,
        now_monotonic_ns=1_300_000_000,
    )

    assert raced.accepted is True
    assert raced.reason == 'hold_activity'
    assert raced.forward_to_hardware is False


@pytest.mark.parametrize(
    ('mode', 'owner', 'source'),
    (
        (MODE_MIMIC, OWNER_WEB, SOURCE_MIMIC),
        (MODE_MANUAL, OWNER_WEB, SOURCE_GESTURE),
        (MODE_MANUAL, OWNER_WEB, SOURCE_SEQUENCE),
        (MODE_TELEOP, OWNER_LOCAL, SOURCE_TELEOP),
    ),
)
def test_run_reentry_reopens_local_barrier_after_long_hold_recovery(
    mode,
    owner,
    source,
):
    core = CommandGuardCore(make_limits())
    core.update_safety_state(SAFETY_READY, 1_000_000_000)
    core.update_control_state(
        MODE_DISABLED,
        OWNER_NONE,
        False,
        1_000_000_000,
    )
    core.update_control_state(mode, owner, True, 1_000_000_000)
    first = core.validate(
        make_command(sequence=30, source=source),
        now_ros_ns=10_000_000_000,
        now_monotonic_ns=1_000_000_000,
    )
    assert first.forward_to_hardware is True

    core.update_safety_state(SAFETY_HOLD, 1_300_000_000)
    for sequence, elapsed_ms in ((31, 300), (32, 400), (33, 500), (34, 600)):
        activity = core.validate(
            make_command(
                sequence=sequence,
                source=source,
                stamp_ns=10_000_000_000 + elapsed_ms * 1_000_000,
            ),
            now_ros_ns=10_000_000_000 + elapsed_ms * 1_000_000,
            now_monotonic_ns=1_000_000_000 + elapsed_ms * 1_000_000,
        )
        assert activity.forward_to_hardware is False

    core.update_safety_state(SAFETY_RUN, 1_610_000_000)
    resumed = core.validate(
        make_command(
            sequence=35,
            source=source,
            stamp_ns=10_620_000_000,
        ),
        now_ros_ns=10_620_000_000,
        now_monotonic_ns=1_620_000_000,
    )

    assert resumed.accepted is True
    assert resumed.forward_to_hardware is True


def test_recovery_run_stamp_handles_lost_hold_and_rejects_delayed_run():
    core = CommandGuardCore(make_limits())
    core.update_safety_state(
        SAFETY_READY,
        1_000_000_000,
        source_stamp_ns=100,
        reason='init_checks_passed',
    )
    core.update_control_state(MODE_DISABLED, OWNER_NONE, False, 1_000_000_000)
    core.update_control_state(MODE_MIMIC, OWNER_WEB, True, 1_000_000_000)
    assert core.validate(
        make_command(sequence=40),
        now_ros_ns=10_000_000_000,
        now_monotonic_ns=1_000_000_000,
    ).forward_to_hardware

    assert core.update_safety_state(
        SAFETY_RUN,
        1_610_000_000,
        source_stamp_ns=300,
        reason='command_stream_recovered',
    )
    resumed = core.validate(
        make_command(sequence=41, stamp_ns=10_620_000_000),
        now_ros_ns=10_620_000_000,
        now_monotonic_ns=1_620_000_000,
    )
    assert resumed.forward_to_hardware

    assert core.update_safety_state(
        SAFETY_HOLD,
        2_000_000_000,
        source_stamp_ns=500,
        reason='command_timeout_hold',
    )
    assert not core.update_safety_state(
        SAFETY_RUN,
        2_010_000_000,
        source_stamp_ns=400,
        reason='command_stream_recovered',
    )
    held = core.validate(
        make_command(sequence=42, stamp_ns=11_000_000_000),
        now_ros_ns=11_000_000_000,
        now_monotonic_ns=2_020_000_000,
    )
    assert held.reason == 'hold_activity'
    assert not held.forward_to_hardware

    # 이전 cycle도 같은 recovery reason이었더라도 새 transition stamp면 재개한다.
    assert core.update_safety_state(
        SAFETY_RUN,
        3_000_000_000,
        source_stamp_ns=700,
        reason='command_stream_recovered',
    )
    second_recovery = core.validate(
        make_command(sequence=43, stamp_ns=12_000_000_000),
        now_ros_ns=12_000_000_000,
        now_monotonic_ns=3_010_000_000,
    )
    assert second_recovery.forward_to_hardware

    # 같은 transition stamp를 다른 state로 재사용하면 freshness도 갱신하지 않는다.
    assert not core.update_safety_state(
        SAFETY_HOLD,
        3_020_000_000,
        source_stamp_ns=700,
        reason='command_timeout_hold',
    )

    # 다음 HOLD 표본까지 놓쳐 state/reason이 모두 같아도 새 stamp가 recovery 증거다.
    assert core.update_safety_state(
        SAFETY_RUN,
        4_000_000_000,
        source_stamp_ns=900,
        reason='command_stream_recovered',
    )
    repeated_recovery = core.validate(
        make_command(sequence=44, stamp_ns=13_000_000_000),
        now_ros_ns=13_000_000_000,
        now_monotonic_ns=4_010_000_000,
    )
    assert repeated_recovery.forward_to_hardware


def test_missing_and_stale_safety_state_fail_closed():
    core = CommandGuardCore(make_limits())
    core.update_control_state(MODE_DISABLED, OWNER_NONE, False, 1_000_000_000)
    core.update_control_state(MODE_MIMIC, OWNER_WEB, True, 1_000_000_000)
    assert_rejected(core, 'safety_state_missing')

    core.update_safety_state(SAFETY_READY, 1_000_000_000)
    assert_rejected(
        core,
        'safety_state_stale',
        monotonic_ns=2_500_000_001,
    )


def test_missing_stale_or_untrusted_control_state_fails_closed():
    core = CommandGuardCore(make_limits())
    core.update_safety_state(SAFETY_READY, 1_000_000_000)
    assert_rejected(core, 'control_state_missing')

    # A Guard restart that only receives a latched active state must not reset
    # the source sequence baseline or resume a pre-restart activation.
    core.update_control_state(MODE_MIMIC, OWNER_WEB, True, 1_000_000_000)
    assert_rejected(core, 'control_activation_not_observed')

    core.update_control_state(MODE_DISABLED, OWNER_NONE, False, 1_000_000_000)
    core.update_control_state(MODE_MIMIC, OWNER_WEB, True, 1_000_000_000)
    core.update_safety_state(SAFETY_READY, 2_500_000_000)
    assert_rejected(
        core,
        'control_state_stale',
        monotonic_ns=2_500_000_001,
    )


def test_inactive_owner_and_wrong_source_are_rejected():
    core = CommandGuardCore(make_limits())
    activate_mimic(core)
    core.update_control_state(MODE_MIMIC, OWNER_WEB, False, 1_020_000_000)
    assert_rejected(core, 'control_inactive', monotonic_ns=1_030_000_000)

    core.update_control_state(MODE_DISABLED, OWNER_NONE, False, 1_040_000_000)
    core.update_control_state(MODE_MIMIC, OWNER_WEB, True, 1_040_000_000)
    assert_rejected(
        core,
        'source_mode_mismatch',
        command=make_command(source=SOURCE_MIMIC + 1),
        monotonic_ns=1_050_000_000,
    )


def test_reserved_safety_source_is_never_accepted_as_selected_command():
    core = CommandGuardCore(make_limits())
    activate_mimic(core)
    assert_rejected(
        core,
        'source_mode_mismatch',
        command=make_command(source=SOURCE_SAFETY),
    )


def test_command_stamp_rejects_stale_and_far_future_values_at_exact_bounds():
    core = CommandGuardCore(make_limits())
    activate_mimic(core)

    assert_rejected(
        core,
        'command_stale',
        command=make_command(stamp_ns=9_749_999_999),
    )
    assert_rejected(
        core,
        'command_from_future',
        command=make_command(stamp_ns=10_150_000_001),
    )

    stale_boundary = core.validate(
        make_command(stamp_ns=9_750_000_000),
        now_ros_ns=10_050_000_000,
        now_monotonic_ns=1_050_000_000,
    )
    assert stale_boundary.accepted is True


def test_all_axis_values_confidence_and_speed_limit_are_finite_and_bounded():
    invalid_cases = []
    for axis_name in AXIS_NAMES:
        for value, reason in (
            (float('nan'), 'axis_non_finite'),
            (float('inf'), 'axis_non_finite'),
            (-0.001, 'axis_out_of_range'),
            (1.001, 'axis_out_of_range'),
        ):
            axes = {name: 0.5 for name in AXIS_NAMES}
            axes[axis_name] = value
            invalid_cases.append((make_command(axes=axes), reason))

    invalid_cases.extend(
        (
            (make_command(speed_limit=0.0), 'speed_limit_out_of_range'),
            (make_command(speed_limit=-0.1), 'speed_limit_out_of_range'),
            (make_command(speed_limit=1.01), 'speed_limit_out_of_range'),
            (make_command(speed_limit=float('nan')), 'speed_limit_non_finite'),
            (make_command(confidence=-0.01), 'confidence_out_of_range'),
            (make_command(confidence=1.01), 'confidence_out_of_range'),
            (make_command(confidence=float('inf')), 'confidence_non_finite'),
        )
    )

    for command, reason in invalid_cases:
        core = CommandGuardCore(make_limits())
        activate_mimic(core)
        assert_rejected(core, reason, command=command)


def test_missing_or_extra_named_axis_is_rejected():
    core = CommandGuardCore(make_limits())
    activate_mimic(core)
    missing = {name: 0.5 for name in AXIS_NAMES[:-1]}
    assert_rejected(core, 'axis_set_invalid', command=make_command(axes=missing))

    core = CommandGuardCore(make_limits())
    activate_mimic(core)
    extra = {name: 0.5 for name in AXIS_NAMES}
    extra['unexpected'] = 0.5
    assert_rejected(core, 'axis_set_invalid', command=make_command(axes=extra))


def test_sequence_uses_uint32_serial_arithmetic_and_rejected_input_does_not_commit():
    core = CommandGuardCore(make_limits())
    activate_mimic(core)

    first = core.validate(
        make_command(sequence=0xFFFFFFFE),
        now_ros_ns=10_050_000_000,
        now_monotonic_ns=1_050_000_000,
    )
    assert first.accepted is True

    wrapped = core.validate(
        make_command(sequence=1),
        now_ros_ns=10_060_000_000,
        now_monotonic_ns=1_060_000_000,
    )
    assert wrapped.accepted is True

    assert_rejected(
        core,
        'sequence_duplicate',
        command=make_command(sequence=1),
        ros_ns=10_070_000_000,
        monotonic_ns=1_070_000_000,
    )
    assert_rejected(
        core,
        'sequence_out_of_order',
        command=make_command(sequence=0),
        ros_ns=10_080_000_000,
        monotonic_ns=1_080_000_000,
    )

    invalid_axes = {name: 0.5 for name in AXIS_NAMES}
    invalid_axes['thumb_flex'] = 2.0
    assert_rejected(
        core,
        'axis_out_of_range',
        command=make_command(sequence=2, axes=invalid_axes),
        ros_ns=10_090_000_000,
        monotonic_ns=1_090_000_000,
    )
    valid_same_sequence = core.validate(
        make_command(sequence=2),
        now_ros_ns=10_100_000_000,
        now_monotonic_ns=1_100_000_000,
    )
    assert valid_same_sequence.accepted is True


@pytest.mark.parametrize('sequence', [-1, 0x1_0000_0000])
def test_sequence_must_fit_uint32(sequence):
    core = CommandGuardCore(make_limits())
    activate_mimic(core)
    assert_rejected(
        core,
        'sequence_out_of_range',
        command=make_command(sequence=sequence),
    )


def test_sequence_baseline_resets_only_after_observed_disabled_reacquisition():
    core = CommandGuardCore(make_limits())
    activate_mimic(core)
    assert core.validate(
        make_command(sequence=50),
        now_ros_ns=10_050_000_000,
        now_monotonic_ns=1_050_000_000,
    ).accepted

    # A periodic ControlState refresh is not an activation boundary.
    core.update_control_state(MODE_MIMIC, OWNER_WEB, True, 1_060_000_000)
    assert_rejected(
        core,
        'sequence_out_of_order',
        command=make_command(sequence=1),
        ros_ns=10_070_000_000,
        monotonic_ns=1_070_000_000,
    )

    core.update_control_state(MODE_DISABLED, OWNER_NONE, False, 1_080_000_000)
    core.update_control_state(MODE_MIMIC, OWNER_WEB, True, 1_090_000_000)
    core.update_safety_state(SAFETY_READY, 1_090_000_000)
    assert core.validate(
        make_command(sequence=1),
        now_ros_ns=10_100_000_000,
        now_monotonic_ns=1_100_000_000,
    ).accepted


def test_manual_sources_have_independent_sequence_baselines():
    core = CommandGuardCore(make_limits())
    core.update_safety_state(SAFETY_READY, 1_000_000_000)
    core.update_control_state(MODE_DISABLED, OWNER_NONE, False, 1_000_000_000)
    core.update_control_state(MODE_MANUAL, OWNER_WEB, True, 1_000_000_000)

    for source in (SOURCE_GESTURE, SOURCE_SEQUENCE):
        decision = core.validate(
            make_command(source=source, sequence=1),
            now_ros_ns=10_050_000_000,
            now_monotonic_ns=1_050_000_000,
        )
        assert decision.accepted is True


def test_axis_rate_uses_monotonic_elapsed_time_and_speed_limit_ratio():
    limits = make_limits(
        max_axis_delta_per_second={name: 1.0 for name in AXIS_NAMES},
        mimic_max_axis_delta_per_second={
            name: 1.0 for name in AXIS_NAMES
        },
    )
    core = CommandGuardCore(limits)
    activate_mimic(core)
    assert core.validate(
        make_command(sequence=1),
        now_ros_ns=10_000_000_000,
        now_monotonic_ns=1_000_000_000,
    ).accepted

    too_fast_axes = {name: 0.5 for name in AXIS_NAMES}
    too_fast_axes['thumb_flex'] = 0.550001
    assert_rejected(
        core,
        'axis_rate_exceeded',
        command=make_command(sequence=2, axes=too_fast_axes),
        ros_ns=10_100_000_000,
        monotonic_ns=1_100_000_000,
    )

    boundary_axes = {name: 0.5 for name in AXIS_NAMES}
    boundary_axes['thumb_flex'] = 0.55
    accepted = core.validate(
        make_command(sequence=2, axes=boundary_axes),
        now_ros_ns=10_100_000_000,
        now_monotonic_ns=1_100_000_000,
    )
    assert accepted.accepted is True


def test_mimic_rate_profile_accepts_vision_delta_without_weakening_manual():
    normal_rates = {name: 1.5 for name in AXIS_NAMES}
    mimic_rates = {name: 10.0 for name in AXIS_NAMES}
    limits = make_limits(
        max_axis_delta_per_second=normal_rates,
        mimic_max_axis_delta_per_second=mimic_rates,
    )

    mimic = CommandGuardCore(limits)
    activate_mimic(mimic)
    assert mimic.validate(
        make_command(sequence=1),
        now_ros_ns=10_000_000_000,
        now_monotonic_ns=1_000_000_000,
    ).accepted
    vision_axes = {name: 0.5 for name in AXIS_NAMES}
    vision_axes['index_flex'] = 0.58
    mimic_decision = mimic.validate(
        make_command(sequence=2, axes=vision_axes, speed_limit=0.25),
        now_ros_ns=10_050_000_000,
        now_monotonic_ns=1_050_000_000,
    )
    assert mimic_decision.accepted is True

    manual = CommandGuardCore(limits)
    manual.update_safety_state(SAFETY_READY, 1_000_000_000)
    manual.update_control_state(
        MODE_DISABLED, OWNER_NONE, False, 1_000_000_000
    )
    manual.update_control_state(MODE_MANUAL, OWNER_WEB, True, 1_000_000_000)
    assert manual.validate(
        make_command(sequence=1, source=SOURCE_GESTURE),
        now_ros_ns=10_000_000_000,
        now_monotonic_ns=1_000_000_000,
    ).accepted
    manual_decision = manual.validate(
        make_command(
            sequence=2,
            source=SOURCE_GESTURE,
            axes=vision_axes,
            speed_limit=0.25,
        ),
        now_ros_ns=10_050_000_000,
        now_monotonic_ns=1_050_000_000,
    )
    assert manual_decision.accepted is False
    assert manual_decision.reason == 'axis_rate_exceeded'


def test_new_activation_clears_axis_rate_baseline():
    limits = make_limits(
        max_axis_delta_per_second={name: 0.1 for name in AXIS_NAMES},
    )
    core = CommandGuardCore(limits)
    activate_mimic(core)
    assert core.validate(
        make_command(sequence=1),
        now_ros_ns=10_000_000_000,
        now_monotonic_ns=1_000_000_000,
    ).accepted

    core.update_control_state(MODE_DISABLED, OWNER_NONE, False, 1_010_000_000)
    core.update_control_state(MODE_MIMIC, OWNER_WEB, True, 1_020_000_000)
    changed_axes = {name: 1.0 for name in AXIS_NAMES}
    assert core.validate(
        make_command(sequence=1, axes=changed_axes),
        now_ros_ns=10_030_000_000,
        now_monotonic_ns=1_030_000_000,
    ).accepted


def test_guard_limits_reject_missing_axes_and_non_positive_thresholds():
    missing_axis = {name: 0.0 for name in AXIS_NAMES[:-1]}
    with pytest.raises(ValueError, match='axis_min'):
        make_limits(axis_min=missing_axis)

    with pytest.raises(ValueError, match='command_stale_timeout_ms'):
        make_limits(command_stale_timeout_ms=0)

    invalid_rate = {name: 1.0 for name in AXIS_NAMES}
    invalid_rate['thumb_flex'] = 0.0
    with pytest.raises(ValueError, match='max_axis_delta_per_second'):
        make_limits(max_axis_delta_per_second=invalid_rate)

    missing_mimic_axis = {name: 10.0 for name in AXIS_NAMES[:-1]}
    with pytest.raises(ValueError, match='mimic_max_axis_delta_per_second'):
        make_limits(
            mimic_max_axis_delta_per_second=missing_mimic_axis,
        )

    unsafe_mimic_rate = {name: 10.0 for name in AXIS_NAMES}
    unsafe_mimic_rate['index_flex'] = 10.000001
    with pytest.raises(ValueError, match='mimic_max_axis_delta_per_second'):
        make_limits(mimic_max_axis_delta_per_second=unsafe_mimic_rate)


def test_guard_local_hold_starts_at_configured_five_second_boundary():
    def decision_after(delay_ms):
        core = CommandGuardCore(make_limits(command_hold_ms=5000))
        activate_mimic(core)
        first = core.validate(
            make_command(sequence=1),
            now_ros_ns=10_000_000_000,
            now_monotonic_ns=1_000_000_000,
        )
        assert first.forward_to_hardware is True

        now_monotonic_ns = 1_000_000_000 + delay_ms * 1_000_000
        core.update_safety_state(SAFETY_READY, now_monotonic_ns)
        core.update_control_state(
            MODE_MIMIC,
            OWNER_WEB,
            True,
            now_monotonic_ns,
        )
        return core.validate(
            make_command(
                sequence=2,
                stamp_ns=10_000_000_000 + delay_ms * 1_000_000,
            ),
            now_ros_ns=10_000_000_000 + delay_ms * 1_000_000,
            now_monotonic_ns=now_monotonic_ns,
        )

    before_boundary = decision_after(4999)
    assert before_boundary.reason == 'accepted'
    assert before_boundary.forward_to_hardware is True

    at_boundary = decision_after(5000)
    assert at_boundary.reason == 'hold_activity'
    assert at_boundary.forward_to_hardware is False


def test_guard_limits_allow_five_second_local_hold_but_no_more():
    assert make_limits(command_hold_ms=5000).command_hold_ms == 5000
    with pytest.raises(ValueError, match='command_hold_ms'):
        make_limits(command_hold_ms=5001)


def test_guard_limits_cannot_weaken_v6_3_safety_contract():
    with pytest.raises(ValueError, match='command_stale_timeout_ms'):
        make_limits(command_stale_timeout_ms=301)
    with pytest.raises(ValueError, match='command_future_tolerance_ms'):
        make_limits(command_future_tolerance_ms=101)
    with pytest.raises(ValueError, match='safety_state_timeout_ms'):
        make_limits(safety_state_timeout_ms=1501)
    with pytest.raises(ValueError, match='control_state_timeout_ms'):
        make_limits(control_state_timeout_ms=1501)

    below_normalized_range = {name: 0.0 for name in AXIS_NAMES}
    below_normalized_range['thumb_flex'] = -0.1
    with pytest.raises(ValueError, match='axis_min'):
        make_limits(axis_min=below_normalized_range)

    above_normalized_range = {name: 1.0 for name in AXIS_NAMES}
    above_normalized_range['thumb_flex'] = 1.1
    with pytest.raises(ValueError, match='axis_max'):
        make_limits(axis_max=above_normalized_range)
