"""Pure fail-closed policy and libgpiod v1 adapter for E-Stop input."""

from typing import Any, Optional


_MAX_ESTOP_DETECTION_MS = 100
_MAX_ESTOP_HEARTBEAT_MS = 100
_MAX_ESTOP_REOPEN_MS = 500


def validate_configuration_types(
    *,
    gpio_chip: str,
    gpio_line: int,
    active_low: bool,
    poll_interval_ms: int,
    debounce_ms: int,
    heartbeat_period_ms: int,
    reopen_interval_ms: int,
    safety_timeout_ms: int,
) -> None:
    """Reject implicit parameter coercion at the safety adapter boundary."""
    expected_types = {
        'gpio_chip': (gpio_chip, str),
        'gpio_line': (gpio_line, int),
        'active_low': (active_low, bool),
        'poll_interval_ms': (poll_interval_ms, int),
        'debounce_ms': (debounce_ms, int),
        'heartbeat_period_ms': (heartbeat_period_ms, int),
        'reopen_interval_ms': (reopen_interval_ms, int),
        'safety_timeout_ms': (safety_timeout_ms, int),
    }
    for name, (value, expected_type) in expected_types.items():
        if type(value) is not expected_type:
            raise TypeError(f'{name} must be {expected_type.__name__}')


def validate_timing(
    *,
    poll_interval_ms: int,
    debounce_ms: int,
    heartbeat_period_ms: int,
    reopen_interval_ms: int,
    safety_timeout_ms: int,
) -> None:
    """Validate timing without allowing configuration to widen safety limits."""
    values = {
        'poll_interval_ms': poll_interval_ms,
        'debounce_ms': debounce_ms,
        'heartbeat_period_ms': heartbeat_period_ms,
        'reopen_interval_ms': reopen_interval_ms,
        'safety_timeout_ms': safety_timeout_ms,
    }
    for name, value in values.items():
        if type(value) is not int:
            raise TypeError(f'{name} must be int')
    if any(value <= 0 for value in values.values()):
        raise ValueError('E-Stop timing values must be positive')
    if safety_timeout_ms > 300:
        raise ValueError('E-Stop safety timeout cannot exceed 300 ms')

    # A contact can change immediately after a poll. Two poll periods cover
    # first observation and the timer callback that closes the debounce window.
    worst_case_detection_ms = debounce_ms + (2 * poll_interval_ms)
    if worst_case_detection_ms > _MAX_ESTOP_DETECTION_MS:
        raise ValueError(
            'E-Stop debounce and polling must detect a stable press within '
            '100 ms'
        )
    if heartbeat_period_ms > _MAX_ESTOP_HEARTBEAT_MS:
        raise ValueError('E-Stop heartbeat period cannot exceed 100 ms')
    if reopen_interval_ms > _MAX_ESTOP_REOPEN_MS:
        raise ValueError('E-Stop GPIO reopen interval cannot exceed 500 ms')
    if heartbeat_period_ms >= safety_timeout_ms:
        raise ValueError(
            'E-Stop heartbeat period must be below the safety timeout'
        )


class EstopInputCore:
    """Debounce one GPIO level while treating unknown input as active E-Stop."""

    def __init__(self, *, debounce_ns: int, active_low: bool) -> None:
        if debounce_ns <= 0:
            raise ValueError('debounce_ns must be positive')
        self._debounce_ns = debounce_ns
        self._active_low = active_low
        self._active = True
        self._candidate_active: Optional[bool] = None
        self._candidate_since_ns: Optional[int] = None
        self._last_observation_ns: Optional[int] = None

    @property
    def active(self) -> bool:
        """Return the stable fail-closed E-Stop state."""
        return self._active

    def observe(self, raw_level: int, now_ns: int) -> bool:
        """Observe a GPIO level and return True only on a stable state change."""
        if raw_level not in (0, 1, False, True):
            return self.fail_closed()
        if now_ns < 0:
            return self.fail_closed()
        if (
            self._last_observation_ns is not None
            and now_ns < self._last_observation_ns
        ):
            return self.fail_closed()
        self._last_observation_ns = now_ns

        level_high = bool(raw_level)
        observed_active = not level_high if self._active_low else level_high
        if observed_active != self._candidate_active:
            self._candidate_active = observed_active
            self._candidate_since_ns = now_ns
            return False

        if (
            observed_active != self._active
            and self._candidate_since_ns is not None
            and now_ns - self._candidate_since_ns >= self._debounce_ns
        ):
            self._active = observed_active
            return True
        return False

    def fail_closed(self) -> bool:
        """Force E-Stop active and require a new full debounce before release."""
        changed = not self._active
        self._active = True
        self._candidate_active = None
        self._candidate_since_ns = None
        return changed


class GpiodV1LineReader:
    """Own one libgpiod v1 input line and release all resources on errors."""

    def __init__(
        self,
        *,
        gpiod_module: Any,
        chip_name: str,
        line_offset: int,
        consumer: str,
    ) -> None:
        if not chip_name:
            raise ValueError('chip_name must not be empty')
        if line_offset < 0:
            raise ValueError('line_offset must be non-negative')
        self._gpiod = gpiod_module
        self._chip_name = chip_name
        self._line_offset = line_offset
        self._consumer = consumer
        self._chip: Optional[Any] = None
        self._line: Optional[Any] = None

    def _open(self) -> None:
        if self._line is not None:
            return
        try:
            self._chip = self._gpiod.Chip(self._chip_name)
            self._line = self._chip.get_line(self._line_offset)
            self._line.request(
                consumer=self._consumer,
                type=self._gpiod.LINE_REQ_DIR_IN,
                flags=self._gpiod.LINE_REQ_FLAG_BIAS_PULL_UP,
            )
        except Exception:
            self.close()
            raise

    def read(self) -> int:
        """Read 0/1, closing the line first when libgpiod reports an error."""
        try:
            self._open()
            return int(self._line.get_value())
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Release the GPIO line and chip; repeated calls are safe."""
        line, chip = self._line, self._chip
        self._line = None
        self._chip = None
        if line is not None:
            try:
                line.release()
            except Exception:
                pass
        if chip is not None:
            try:
                chip.close()
            except Exception:
                pass
