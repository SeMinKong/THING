"""
명령원 제어권을 결정하는 ROS 독립 상태기.

Web mimic, Web manual, local teleop이 동시에 명령을 보내더라도 실제로는 한 owner만
활성화되어야 한다. 이 코어는 mode·owner 조합, lease, 녹화/sequence 상태를 판정하고,
``command_manager.py``는 ROS service/topic을 이 정책에 연결한다.
"""

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

SAFETY_INIT = 0
SAFETY_READY = 1
SAFETY_RUN = 2
SAFETY_HOLD = 3
SAFETY_RESET = 7

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
_SOURCE_SELECTION_SAFETY_STATES = frozenset(
    (SAFETY_READY, SAFETY_RUN, SAFETY_HOLD)
)
_ACTIVE_RECORDING_STATES = frozenset(
    (RECORDING_STARTING, RECORDING_RECORDING, RECORDING_STOPPING)
)
_ALLOWED_SOURCES = {
    # mode 이름만 믿지 않고 실제 HandCommand.source까지 다시 맞춘다. producer가 잘못된
    # topic에 publish해도 다른 mode의 명령으로 통과시키지 않기 위함이다.
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
    """mode, owner lease와 source 선택을 한 critical section에서 관리한다."""

    def __init__(
        self,
        owner_lease_timeout_ms: int = 3000,
        stop_reacquire_delay_ms: int = 500,
        monotonic_ns: Callable[[], int] = monotonic_ns,
    ) -> None:
        if (
            isinstance(owner_lease_timeout_ms, bool)
            or not isinstance(owner_lease_timeout_ms, int)
            or owner_lease_timeout_ms <= 0
        ):
            raise ValueError('owner_lease_timeout_ms must be a positive integer')
        if owner_lease_timeout_ms > 3000:
            raise ValueError('owner_lease_timeout_ms cannot exceed 3000')
        if (
            isinstance(stop_reacquire_delay_ms, bool)
            or not isinstance(stop_reacquire_delay_ms, int)
            or stop_reacquire_delay_ms <= 0
        ):
            raise ValueError('stop_reacquire_delay_ms must be a positive integer')
        if stop_reacquire_delay_ms > 500:
            raise ValueError('stop_reacquire_delay_ms cannot exceed 500')

        self._lock = RLock()
        self._monotonic_ns = monotonic_ns
        self._lease_timeout_ns = owner_lease_timeout_ms * 1_000_000
        self._stop_reacquire_delay_ns = (
            stop_reacquire_delay_ms * 1_000_000
        )
        self._lease_deadline_ns: Optional[int] = None
        self._stop_blocked_until_ns: Optional[int] = None
        self._stop_recovery_pending = False
        self._stop_source_stamp_boundary_ns: Optional[int] = None
        self._stop_recovery_epoch_stamp_ns: Optional[int] = None
        self._last_safety_stamp_ns: Optional[int] = None

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
        stop_source_stamp_ns: Optional[int] = None,
    ) -> ModeRequestResult:
        """
        제어권 획득·갱신·해제를 한 critical section에서 처리한다.

        활성 mode 요청은 READY에서만 새로 획득할 수 있고, 같은 owner의 같은 mode 요청은
        lease heartbeat로 취급한다. ``DISABLED/NONE``은 일반 mode가 아니라 명시적
        STOP이며 owner와 실행 상태를 즉시 버린다.
        """
        with self._lock:
            if self._expire_lease_locked():
                # 이 요청 안에서 만료와 재획득을 합치면 node가 DISABLED/NONE 전이를
                # 발행할 기회를 잃는다. 현재 요청은 닫고 호출자가 새 상태를 본 뒤
                # 명시적으로 재시도하게 한다.
                return self._result_locked(False, 'owner_lease_expired')

            if not self._valid_mode_owner_pair(
                requested_mode,
                requested_owner,
            ):
                return self._result_locked(False, 'invalid_mode')

            if requested_mode == MODE_DISABLED:
                if self._safety_state in (SAFETY_INIT, SAFETY_RESET):
                    return self._result_locked(
                        False,
                        'stop_not_allowed_in_safety_state',
                    )
                # STOP 뒤 500 ms 동안 재획득을 막아, Guard latch와 Safety RESET이 닫히기
                # 전에 새 owner가 제어권을 다시 잡는 race를 줄인다.
                self._stop_blocked_until_ns = (
                    self._monotonic_ns() + self._stop_reacquire_delay_ns
                )
                self._stop_recovery_pending = (
                    self._safety_state in _SOURCE_SELECTION_SAFETY_STATES
                )
                self._stop_source_stamp_boundary_ns = (
                    stop_source_stamp_ns
                    if stop_source_stamp_ns is not None
                    and stop_source_stamp_ns > 0
                    else None
                )
                self._stop_recovery_epoch_stamp_ns = None
                self._release_locked('accepted')
                return self._result_locked(True, 'accepted')

            if (
                requested_mode == self._active_mode
                and requested_owner == self._active_owner
                and self._owner_alive
            ):
                if self._safety_state not in (
                    *_NORMAL_SAFETY_STATES,
                    SAFETY_HOLD,
                ):
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
        """
        현재 살아 있는 owner·mode·SafetyState와 source가 모두 맞는지 확인한다.

        여기서는 명령의 숫자 범위까지 검사하지 않는다. Manager는 '누가 말할 수 있는지',
        Guard는 '그 말의 내용이 안전한지'를 책임지는 경계다.
        """
        with self._lock:
            if not self._owner_alive:
                return False
            if (
                self._lease_deadline_ns is not None
                and self._monotonic_ns() >= self._lease_deadline_ns
            ):
                # 상태 mutation은 check_lease() 한 곳에서만 수행해 node가 만료
                # ControlState를 반드시 발행할 수 있게 한다.
                return False
            if self._safety_state not in _SOURCE_SELECTION_SAFETY_STATES:
                return False
            return source in _ALLOWED_SOURCES.get(
                self._active_mode,
                frozenset(),
            )

    def check_lease(self) -> bool:
        """Expire the owner lease if due and report whether state changed."""
        with self._lock:
            return self._expire_lease_locked()

    def update_safety_state(
        self,
        safety_state: int,
        source_stamp_ns: int,
    ) -> bool:
        """Apply only canonical, ordered SafetyState samples from the ROS adapter."""
        with self._lock:
            if (
                isinstance(source_stamp_ns, bool)
                or not isinstance(source_stamp_ns, int)
                or source_stamp_ns <= 0
            ):
                return False
            if self._last_safety_stamp_ns is not None:
                if source_stamp_ns < self._last_safety_stamp_ns:
                    return False
                if source_stamp_ns == self._last_safety_stamp_ns:
                    # 같은 transition stamp의 heartbeat만 허용한다. 같은 stamp로 enum을
                    # 바꾼 표본은 malformed/replayed state로 보고 무시한다.
                    return False

            self._last_safety_stamp_ns = source_stamp_ns
            self._safety_state = safety_state
            if self._stop_recovery_pending:
                boundary_ns = self._stop_source_stamp_boundary_ns
                if (
                    boundary_ns is not None
                    and source_stamp_ns > boundary_ns
                    and safety_state in (SAFETY_RESET, SAFETY_INIT)
                ):
                    self._stop_recovery_epoch_stamp_ns = source_stamp_ns
                elif (
                    safety_state == SAFETY_READY
                    and self._stop_recovery_epoch_stamp_ns is not None
                    and source_stamp_ns > self._stop_recovery_epoch_stamp_ns
                ):
                    self._stop_recovery_pending = False
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
        if self._stop_recovery_pending:
            return True
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
        # lease는 owner가 사라진 뒤 영구히 제어권을 쥐는 것을 막는 heartbeat 계약이다.
        # 만료 시 명령 source를 먼저 닫고 ControlState reason으로 Safety Manager에 알린다.
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
