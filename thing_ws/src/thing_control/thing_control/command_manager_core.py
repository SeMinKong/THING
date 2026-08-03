"""Thread-safe state machine for command source arbitration."""

from dataclasses import dataclass
from threading import RLock
from time import monotonic_ns
from typing import Callable, Optional


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

SAFETY_READY = 1
SAFETY_RUN = 2
SAFETY_HOLD = 3

RECORDING_STARTING = 1
RECORDING_RECORDING = 2
RECORDING_STOPPING = 3

_VALID_MODE_OWNER_PAIRS = frozenset(
    (
        (MODE_DISABLED, OWNER_NONE),
        (MODE_MIMIC, OWNER_WEB),
        (MODE_MANUAL, OWNER_WEB),
        (MODE_TELEOP, OWNER_LOCAL),
    )
)
_NORMAL_SAFETY_STATES = frozenset((SAFETY_READY, SAFETY_RUN))
_ACTIVE_RECORDING_STATES = frozenset(
    (RECORDING_STARTING, RECORDING_RECORDING, RECORDING_STOPPING)
)
_ALLOWED_SOURCES = {
    MODE_MIMIC: frozenset((SOURCE_MIMIC,)),
    MODE_MANUAL: frozenset((SOURCE_GESTURE, SOURCE_SEQUENCE)),
    MODE_TELEOP: frozenset((SOURCE_TELEOP,)),
}


@dataclass(frozen=True)
class CommandManagerState:
    """Externally visible command manager state."""

    active_mode: int
    active_owner: int
    owner_alive: bool
    sequence_running: bool
    last_transition_reason: str


@dataclass(frozen=True)
class ModeRequestResult:
    """Result returned for a control mode request."""

    accepted: bool
    active_mode: int
    active_owner: int
    reason: str


class CommandManagerCore:
    """Own mode, owner lease, and command-source arbitration state."""

    def __init__(
        self,
        owner_lease_timeout_ms: int = 3000,
        stop_reacquire_delay_ms: int = 500,
        monotonic_ns: Callable[[], int] = monotonic_ns,
    ) -> None:
        if owner_lease_timeout_ms <= 0:
            raise ValueError('owner_lease_timeout_ms must be positive')
        if stop_reacquire_delay_ms <= 0:
            raise ValueError('stop_reacquire_delay_ms must be positive')

        self._lock = RLock()
        self._monotonic_ns = monotonic_ns
        self._lease_timeout_ns = owner_lease_timeout_ms * 1_000_000
        self._stop_reacquire_delay_ns = (
            stop_reacquire_delay_ms * 1_000_000
        )
        self._lease_deadline_ns: Optional[int] = None
        self._stop_blocked_until_ns: Optional[int] = None

        self._active_mode = MODE_DISABLED
        self._active_owner = OWNER_NONE
        self._owner_alive = False
        self._sequence_running = False
        self._last_transition_reason = 'initialized'

        self._safety_state = 0
        self._recording_state = 0
        self._result_pending = False

    def snapshot(self) -> CommandManagerState:
        """Return a consistent current state, expiring a stale lease first."""
        with self._lock:
            self._expire_lease_locked()
            return self._snapshot_locked()

    def request_mode(
        self,
        requested_mode: int,
        requested_owner: int,
    ) -> ModeRequestResult:
        """Acquire, renew, or release control in one critical section."""
        with self._lock:
            self._expire_lease_locked()

            if not self._valid_mode_owner_pair(
                requested_mode,
                requested_owner,
            ):
                return self._result_locked(False, 'invalid_mode')

            if requested_mode == MODE_DISABLED:
                self._stop_blocked_until_ns = (
                    self._monotonic_ns() + self._stop_reacquire_delay_ns
                )
                self._release_locked('accepted')
                return self._result_locked(True, 'accepted')

            if (
                requested_mode == self._active_mode
                and requested_owner == self._active_owner
                and self._owner_alive
            ):
                if self._safety_state not in _NORMAL_SAFETY_STATES:
                    return self._result_locked(False, 'safety_not_ready')
                self._renew_lease_locked()
                self._last_transition_reason = 'accepted'
                return self._result_locked(True, 'accepted')

            if self._stop_reacquire_blocked_locked():
                return self._result_locked(False, 'stop_in_progress')

            if self._sequence_running:
                return self._result_locked(False, 'motion_active')

            if self._active_owner != OWNER_NONE:
                if requested_owner == self._active_owner:
                    return self._result_locked(False, 'invalid_mode')
                return self._result_locked(False, 'owner_conflict')

            if self._safety_state != SAFETY_READY:
                return self._result_locked(False, 'safety_not_ready')

            if self._recording_active_locked():
                return self._result_locked(False, 'recording_active')

            self._active_mode = requested_mode
            self._active_owner = requested_owner
            self._owner_alive = True
            self._sequence_running = False
            self._last_transition_reason = 'accepted'
            self._renew_lease_locked()
            return self._result_locked(True, 'accepted')

    def accepts_source(self, source: int) -> bool:
        """Return whether a command source matches active safe control."""
        with self._lock:
            self._expire_lease_locked()
            if not self._owner_alive:
                return False
            if self._safety_state not in _NORMAL_SAFETY_STATES:
                return False
            return source in _ALLOWED_SOURCES.get(
                self._active_mode,
                frozenset(),
            )

    def check_lease(self) -> bool:
        """Expire the owner lease if due and report whether state changed."""
        with self._lock:
            return self._expire_lease_locked()

    def update_safety_state(self, safety_state: int) -> bool:
        """Apply safety state while preserving ownership during HOLD."""
        with self._lock:
            self._safety_state = safety_state
            if (
                safety_state != SAFETY_HOLD
                and safety_state not in _NORMAL_SAFETY_STATES
                and self._active_owner != OWNER_NONE
            ):
                return self._release_locked('safety_not_ready')
            return False

    def update_recording_state(
        self,
        recording_state: int,
        result_pending: bool,
    ) -> None:
        """Track whether recording forbids a new normal mode request."""
        with self._lock:
            self._recording_state = recording_state
            self._result_pending = result_pending

    def set_sequence_running(self, running: bool) -> bool:
        """Update sequence activity for integration with the executor."""
        with self._lock:
            requested = bool(running)
            if requested and (
                self._active_mode != MODE_MANUAL or not self._owner_alive
            ):
                return False
            changed = self._sequence_running != requested
            self._sequence_running = requested
            return changed

    def _valid_mode_owner_pair(self, mode: int, owner: int) -> bool:
        return (mode, owner) in _VALID_MODE_OWNER_PAIRS

    def _stop_reacquire_blocked_locked(self) -> bool:
        if self._stop_blocked_until_ns is None:
            return False
        if self._monotonic_ns() < self._stop_blocked_until_ns:
            return True
        self._stop_blocked_until_ns = None
        return False

    def _recording_active_locked(self) -> bool:
        return (
            self._recording_state in _ACTIVE_RECORDING_STATES
            or self._result_pending
        )

    def _renew_lease_locked(self) -> None:
        self._lease_deadline_ns = (
            self._monotonic_ns() + self._lease_timeout_ns
        )

    def _expire_lease_locked(self) -> bool:
        if self._lease_deadline_ns is None or not self._owner_alive:
            return False
        if self._monotonic_ns() < self._lease_deadline_ns:
            return False
        return self._release_locked('owner_lease_expired')

    def _release_locked(self, reason: str) -> bool:
        changed = (
            self._active_mode != MODE_DISABLED
            or self._active_owner != OWNER_NONE
            or self._owner_alive
            or self._sequence_running
        )
        self._active_mode = MODE_DISABLED
        self._active_owner = OWNER_NONE
        self._owner_alive = False
        self._sequence_running = False
        self._lease_deadline_ns = None
        self._last_transition_reason = reason
        return changed

    def _snapshot_locked(self) -> CommandManagerState:
        return CommandManagerState(
            active_mode=self._active_mode,
            active_owner=self._active_owner,
            owner_alive=self._owner_alive,
            sequence_running=self._sequence_running,
            last_transition_reason=self._last_transition_reason,
        )

    def _result_locked(
        self,
        accepted: bool,
        reason: str,
    ) -> ModeRequestResult:
        return ModeRequestResult(
            accepted=accepted,
            active_mode=self._active_mode,
            active_owner=self._active_owner,
            reason=reason,
        )
