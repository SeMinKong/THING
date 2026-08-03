"""
명령원 제어권을 결정하는 ROS 독립 상태기.

초보자용 간단 매뉴얼
---------------------
1. 역할
   Web mimic/manual과 local teleop 중 유효한 mode·owner 하나만 활성화하고 owner lease,
   Safety/Recording 상태, STOP 이후 재획득 조건을 한 critical section에서 판정한다.
2. 입력
   ``request_mode()``의 mode/owner/STOP timestamp, ``update_safety_state()``의 순서 있는
   Safety 표본, 녹화 상태, Gesture/Sequence executor의 동작 여부, command ``source``다.
3. 출력
   mode 요청 결과(``ModeRequestResult``), 외부 공개 snapshot(``CommandManagerState``),
   source 수용 여부와 상태 변경 여부를 반환한다. ROS 메시지를 직접 발행하지는 않는다.
4. 주요 실행 흐름
   요청 진입 시 lease 만료를 먼저 반영 → mode/owner 조합 검증 → STOP이면 즉시 release와
   재획득 gate 설정 → heartbeat이면 lease 갱신 → 새 획득이면 Safety·녹화·동작·owner
   충돌 검사 후 활성화한다. 명령마다 mode에 매핑된 source인지 별도로 확인한다.
5. 사용/실행 방법
   ``CommandManagerCore``를 생성하고 Safety/Recording 상태를 갱신한 뒤 ``request_mode()``,
   ``accepts_source()``, ``check_lease()``, ``snapshot()``을 호출한다. 테스트에서는
   ``monotonic_ns``에 가짜 시계를 주입해 실제 대기 없이 lease 경계를 재현할 수 있다.
6. 책임 경계와 하지 않는 일
   이 코어는 ROS topic/service, STOP ACK 대기, 메시지 timestamp 변환을 하지 않는다.
   또한 관절 명령 숫자의 안전성, Safety 상태 전이 자체, gesture/sequence 생성·실행을
   담당하지 않는다. 어댑터는 ``command_manager.py``, 값 검증은 Command Guard, 최종
   안전 상태 전이는 Safety Manager, 실제 gesture 실행은 executor의 책임이다.

동시성 핵심: 모든 공개 메서드는 재진입 mutex(``RLock``) 아래에서 상태를 읽고 쓴다.
유효성 판단 중 일부만 바뀐 중간 상태가 관찰되지 않도록 하고, 불확실하거나 오래된 입력은
기존 제어권을 확대하지 않는 fail-closed 방향으로 거부한다.
"""

from dataclasses import dataclass
from threading import RLock
from time import monotonic_ns
from typing import Callable, Optional


# 아래 정수는 ROS interface enum과 대응한다. 코어를 ROS 없이 테스트하기 위해 로컬에 둔다.
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

# 임의 조합을 허용하지 않고 제품 정책상 가능한 mode-owner 쌍만 명시적으로 연다.
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
    """외부 발행용으로 한 시점에 고정한 command manager 상태."""

    active_mode: int
    active_owner: int
    owner_alive: bool
    sequence_running: bool
    last_transition_reason: str


@dataclass(frozen=True)
class ModeRequestResult:
    """mode 요청의 승인 여부와 요청 처리 직후 실제 활성 상태."""

    accepted: bool
    active_mode: int
    active_owner: int
    reason: str


class CommandManagerCore:
    """
    mode, owner lease와 source 선택을 한 critical section에서 관리한다.

    주요 불변식은 (1) 활성 mode와 owner는 허용된 쌍이고, (2) owner가 없으면 mode는
    DISABLED이며, (3) lease/STOP/Safety 실패 시 제어권을 더 열지 않는 것이다. 공개
    메서드끼리 중첩 호출될 수 있어 ``RLock``을 사용하며 ``*_locked`` helper는 그 lock을
    이미 가진 상태에서만 호출한다.
    """

    def __init__(
        self,
        owner_lease_timeout_ms: int = 3000,
        stop_reacquire_delay_ms: int = 500,
        monotonic_ns: Callable[[], int] = monotonic_ns,
    ) -> None:
        """
        안전 시간 상한을 검증하고 제어권이 닫힌 초기 상태를 만든다.

        ``monotonic_ns``는 시스템 시각 보정의 영향을 받지 않는 lease용 시계다. callable
        주입은 테스트가 시간을 결정적으로 전진시킬 수 있게 한다.
        """
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

        # mode/owner/lease/recovery 필드는 의미가 묶여 있으므로 같은 mutex로 보호한다.
        # 예를 들어 mode만 바뀌고 owner는 이전 값인 중간 순간이 노출되면 안 된다.
        self._lock = RLock()
        self._monotonic_ns = monotonic_ns
        self._lease_timeout_ns = owner_lease_timeout_ms * 1_000_000
        self._stop_reacquire_delay_ns = (
            stop_reacquire_delay_ms * 1_000_000
        )
        # lease deadline은 owner heartbeat의 생존 한계다. None은 활성 lease가 없다는 뜻이다.
        self._lease_deadline_ns: Optional[int] = None
        # STOP 재획득 gate는 시간 조건과 Safety recovery 조건을 모두 만족해야 열린다.
        # boundary/epoch는 STOP 전의 오래된 Safety 표본이 gate를 여는 것을 막는다.
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
        """만료 lease를 먼저 정리하고 서로 일관된 현재 상태 복사본을 반환한다."""
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
        STOP이며 owner와 실행 상태를 즉시 버린다. STOP 뒤 재획득은 최소 지연 시간뿐
        아니라 STOP 이후 timestamp의 RESET/INIT을 거쳐 그보다 최신 READY가 관찰되어야
        열리는 gate로 보호된다.
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
                # STOP 뒤 설정된 지연 동안 재획득을 막아 Guard latch가 닫히기 전에 새
                # owner가 제어권을 잡는 race를 줄인다. STOP 당시 Safety가 source 선택
                # 가능 상태였다면 시간만 지나서는 부족하고 RESET/INIT→READY도 요구한다.
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
                # 같은 mode/owner 요청은 소유권 변경이 아니라 heartbeat다. HOLD에서도
                # 기존 owner의 생존 갱신은 허용하지만 새 owner 획득은 READY만 허용한다.
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

            # Gesture/Sequence executor가 실제 motion 중이면 mode를 갈아끼우지 않는다.
            # STOP은 이 검사보다 앞에서 처리되므로 언제나 실행 상태를 닫을 수 있다.
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

        lease가 이미 지났다면 여기서는 즉시 거부만 하고 상태 mutation은 하지 않는다.
        어댑터가 ``check_lease()``로 변경을 소비해 DISABLED 상태 발행을 놓치지 않게 하기
        위한 분리다.
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
        """기한이 지난 owner lease를 해제하고 실제 상태 변경 여부를 반환한다."""
        with self._lock:
            return self._expire_lease_locked()

    def update_safety_state(
        self,
        safety_state: int,
        source_stamp_ns: int,
    ) -> bool:
        """
        유효하며 시간 순서가 증가하는 SafetyState 표본만 적용한다.

        STOP recovery 중에는 STOP 경계보다 최신 RESET/INIT과 그 recovery epoch보다 최신
        READY가 순서대로 와야 gate를 연다. 오래되거나 같은 timestamp인 표본은 replay 또는
        malformed 입력으로 보고 무시해 과거 READY가 새 제어권을 열지 못하게 한다.
        """
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
                    # 같은 stamp의 표본은 heartbeat인지 payload 변경인지 구분하지 않고
                    # 모두 적용하지 않는다. 따라서 같은 stamp로 상태 enum을 바꿔도
                    # 현재 Safety 상태와 STOP recovery gate에는 반영되지 않는다.
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
        """새 일반 mode 획득을 막아야 하는 녹화 진행/결과 대기 상태를 보관한다."""
        with self._lock:
            self._recording_state = recording_state
            self._result_pending = result_pending

    def set_sequence_running(self, running: bool) -> bool:
        """
        Gesture/Sequence executor의 동작 여부를 반영하고 변경 여부를 반환한다.

        동작 시작(true)은 살아 있는 MANUAL owner가 있을 때만 수용한다. 반대로 false는
        executor의 완료/중단 통지이므로 허용한다. 이 flag는 새 mode 획득을 막으며 STOP,
        lease 만료, Safety 해제 시 ``_release_locked()``에서 제어권과 함께 초기화된다.
        """
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
        """정책에 명시된 mode-owner 조합인지 확인한다."""
        return (mode, owner) in _VALID_MODE_OWNER_PAIRS

    def _stop_reacquire_blocked_locked(self) -> bool:
        """
        Safety recovery와 최소 지연 중 하나라도 남았으면 재획득을 닫는다.

        ``_stop_recovery_pending``은 Safety 표본 순서가 해제하고, monotonic deadline은 이
        helper가 시간이 지난 뒤 ``None``으로 소비한다. 둘은 독립 조건이므로 recovery가
        남아 있으면 시간 deadline부터 확인하지 않고 즉시 닫는다.
        """
        if self._stop_recovery_pending:
            return True
        if self._stop_blocked_until_ns is None:
            return False
        if self._monotonic_ns() < self._stop_blocked_until_ns:
            return True
        self._stop_blocked_until_ns = None
        return False

    def _recording_active_locked(self) -> bool:
        """녹화 전환 중이거나 결과 처리가 남아 새 제어권을 막아야 하는지 반환한다."""
        return (
            self._recording_state in _ACTIVE_RECORDING_STATES
            or self._result_pending
        )

    def _renew_lease_locked(self) -> None:
        """현재 monotonic 시각 기준으로 owner heartbeat 마감 시각을 갱신한다."""
        self._lease_deadline_ns = (
            self._monotonic_ns() + self._lease_timeout_ns
        )

    def _expire_lease_locked(self) -> bool:
        """lease가 기한을 넘겼다면 fail-closed release를 수행한다."""
        # lease는 owner가 사라진 뒤 영구히 제어권을 쥐는 것을 막는 heartbeat 계약이다.
        # 만료 시 명령 source를 먼저 닫고 ControlState reason으로 Safety Manager에 알린다.
        if self._lease_deadline_ns is None or not self._owner_alive:
            return False
        if self._monotonic_ns() < self._lease_deadline_ns:
            return False
        return self._release_locked('owner_lease_expired')

    def _release_locked(self, reason: str) -> bool:
        """
        mode·owner·motion·lease를 원자적으로 닫고 실제 변경 여부를 반환한다.

        release는 일부 필드만 끄지 않는다. producer 허용의 근거가 되는 모든 필드를 같은
        critical section에서 초기화해야 다음 callback이 오래된 motion/owner를 보지 않는다.
        """
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
        """lock으로 보호 중인 필드들을 불변 공개 상태 객체로 복사한다."""
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
        """요청 결과에 현재 mode/owner를 함께 담아 호출자의 추측을 없앤다."""
        return ModeRequestResult(
            accepted=accepted,
            active_mode=self._active_mode,
            active_owner=self._active_owner,
            reason=reason,
        )
