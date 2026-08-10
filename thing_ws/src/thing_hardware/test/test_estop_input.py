"""Behavior tests for the fail-closed E-Stop GPIO input."""

import pytest

import thing_hardware.estop_input as estop_input
from thing_hardware.estop_input import (
    EstopInputCore,
    GpiodV1LineReader,
    validate_timing,
)


MS = 1_000_000


def test_startup_stays_active_until_inactive_level_is_stable():
    """Unknown startup input is E-Stop until HIGH is stable for 50 ms."""
    core = EstopInputCore(debounce_ns=50 * MS, active_low=True)

    assert core.active is True
    assert core.observe(1, 0) is False
    assert core.observe(1, 49 * MS) is False
    assert core.active is True

    assert core.observe(1, 50 * MS) is True
    assert core.active is False


def test_press_and_release_each_require_a_stable_debounce_window():
    core = EstopInputCore(debounce_ns=50 * MS, active_low=True)
    core.observe(1, 0)
    core.observe(1, 50 * MS)

    assert core.observe(0, 55 * MS) is False
    assert core.observe(1, 80 * MS) is False  # contact bounce
    assert core.observe(0, 85 * MS) is False
    assert core.observe(0, 134 * MS) is False
    assert core.observe(0, 135 * MS) is True
    assert core.active is True

    assert core.observe(1, 140 * MS) is False
    assert core.observe(1, 190 * MS) is True
    assert core.active is False


def test_unreadable_input_forces_estop_immediately():
    core = EstopInputCore(debounce_ns=50 * MS, active_low=True)
    core.observe(1, 0)
    core.observe(1, 50 * MS)
    assert core.active is False

    assert core.fail_closed() is True
    assert core.active is True
    assert core.fail_closed() is False


def test_timing_validation_preserves_100ms_detection_and_300ms_heartbeat():
    validate_timing(
        poll_interval_ms=5,
        debounce_ms=50,
        heartbeat_period_ms=100,
        reopen_interval_ms=500,
        safety_timeout_ms=300,
    )

    with pytest.raises(ValueError, match='100 ms'):
        validate_timing(
            poll_interval_ms=20,
            debounce_ms=70,
            heartbeat_period_ms=100,
            reopen_interval_ms=500,
            safety_timeout_ms=300,
        )

    with pytest.raises(ValueError, match='heartbeat'):
        validate_timing(
            poll_interval_ms=5,
            debounce_ms=50,
            heartbeat_period_ms=101,
            reopen_interval_ms=500,
            safety_timeout_ms=300,
        )

    with pytest.raises(ValueError, match='heartbeat'):
        validate_timing(
            poll_interval_ms=5,
            debounce_ms=50,
            heartbeat_period_ms=300,
            reopen_interval_ms=500,
            safety_timeout_ms=300,
        )

    with pytest.raises(TypeError, match='poll_interval_ms'):
        validate_timing(
            poll_interval_ms=True,
            debounce_ms=50,
            heartbeat_period_ms=100,
            reopen_interval_ms=500,
            safety_timeout_ms=300,
        )


def test_reopen_interval_is_capped_at_500ms():
    validate_timing(
        poll_interval_ms=5,
        debounce_ms=50,
        heartbeat_period_ms=100,
        reopen_interval_ms=500,
        safety_timeout_ms=300,
    )

    with pytest.raises(ValueError, match='reopen interval'):
        validate_timing(
            poll_interval_ms=5,
            debounce_ms=50,
            heartbeat_period_ms=100,
            reopen_interval_ms=501,
            safety_timeout_ms=300,
        )


def test_configuration_types_reject_implicit_bool_string_and_float_coercion():
    valid = {
        'gpio_chip': 'gpiochip4',
        'gpio_line': 17,
        'active_low': True,
        'poll_interval_ms': 5,
        'debounce_ms': 50,
        'heartbeat_period_ms': 100,
        'reopen_interval_ms': 500,
        'safety_timeout_ms': 300,
    }
    invalid = {
        'gpio_chip': 4,
        'gpio_line': True,
        'active_low': 1,
        'poll_interval_ms': True,
        'debounce_ms': 50.0,
        'heartbeat_period_ms': '100',
        'reopen_interval_ms': False,
        'safety_timeout_ms': 300.0,
    }

    for name, value in invalid.items():
        parameters = dict(valid)
        parameters[name] = value
        with pytest.raises(TypeError, match=name):
            estop_input.validate_configuration_types(**parameters)


class FakeLine:
    def __init__(self, value=1):
        self.value = value
        self.request_kwargs = None
        self.released = False

    def request(self, **kwargs):
        self.request_kwargs = kwargs

    def get_value(self):
        return self.value

    def release(self):
        self.released = True


class FakeChip:
    def __init__(self, line):
        self.line = line
        self.requested_offset = None
        self.closed = False

    def get_line(self, offset):
        self.requested_offset = offset
        return self.line

    def close(self):
        self.closed = True


class FakeGpiod:
    LINE_REQ_DIR_IN = 10
    LINE_REQ_FLAG_BIAS_PULL_UP = 20

    def __init__(self):
        self.line = FakeLine()
        self.chip = FakeChip(self.line)
        self.chip_name = None

    def Chip(self, chip_name):
        self.chip_name = chip_name
        return self.chip


def test_gpiod_v1_reader_requests_active_low_auxiliary_contact_input():
    module = FakeGpiod()
    reader = GpiodV1LineReader(
        gpiod_module=module,
        chip_name='gpiochip4',
        line_offset=17,
        consumer='thing-estop',
    )

    assert reader.read() == 1
    assert module.chip_name == 'gpiochip4'
    assert module.chip.requested_offset == 17
    assert module.line.request_kwargs == {
        'consumer': 'thing-estop',
        'type': module.LINE_REQ_DIR_IN,
        'flags': module.LINE_REQ_FLAG_BIAS_PULL_UP,
    }

    reader.close()
    assert module.line.released is True
    assert module.chip.closed is True
