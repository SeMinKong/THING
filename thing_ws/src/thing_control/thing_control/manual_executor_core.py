"""
Gesture와 Sequence를 하나의 MANUAL 실행 슬롯에서 관리하는 ROS 독립 상태기.

초보자용 간단 매뉴얼
---------------------
1. 역할
   Gesture service 요청과 Sequence action 요청을 같은 ``_active`` 자리에 세워 한 번에
   하나만 실행한다. 쉽게 말해 ROS 메시지를 모르는 '동작 순서표 담당자'이며, 두 요청이
   서로 다른 publisher에서 충돌하지 않도록 시작·진행·종료 판단을 한곳에 모은다.
2. 입력
   gesture별 7축 pose와 duration, 이름 있는 sequence step 목록, 최신 ControlState와
   SafetyState의 로컬 수신 시각, 시작할 이름·speed limit, timer의 monotonic 시각을 받는다.
3. 출력
   시작 가능 여부(``StartResult``), 한 tick에서 보낼 pose(``CommandFrame``), Sequence
   진행 정보와 완료·취소 결과(``TickResult``/``MotionOutcome``)를 반환한다. ROS message를
   직접 만들거나 publish하지 않는다.
4. 주요 실행 흐름
   시작 요청 → 이름·축·속도·단일 실행 여부 검사 → MANUAL/WEB 제어권과 READY/RUN 상태의
   freshness 확인 → generation을 부여해 실행 시작 → tick마다 현재 pose와 step을 반환 →
   duration 완료 후 실행 슬롯은 비우되 마지막 pose를 다음 요청까지 계속 반환한다. 새 요청은
   즉시 그 pose를 교체하고, STOP/cancel/제어권·Safety 상실 시 retained pose까지 닫는다.
5. 사용/실행 방법
   pose·duration·sequence 설정으로 ``ManualExecutorCore``를 만들고, 상태 callback에서는
   ``update_*_state()``, 요청에서는 ``start_gesture()``/``start_sequence()``, 주기 실행에서는
   ``tick()``을 호출한다. 단위 테스트는 ``now_ns``를 직접 넣어 실제 대기 없이 경계를 만든다.
6. 책임 경계와 하지 않는 일
   이 코어는 ROS topic/service/action, callback group, QoS, STOP barrier latch를 모른다.
   mode·owner를 부여하지 않고, 7축 값의 최종 하드웨어 안전성 검사·보간·Safety 상태 전이도
   하지 않는다. ROS 어댑터는 ``manual_executor.py``, 중재는 Command Manager, 최종 값
   검사는 Command Guard, 물리 동작은 hardware node의 책임이다.

시간·동시성 핵심: duration과 freshness는 호출자가 주입한 monotonic ``now_ns``만 사용한다.
Control/Safety 표본이 없거나 timeout을 넘으면 새 요청과 진행 중 동작을 모두 fail-closed로
거부·취소하며, 각 수락 동작에는 generation을 부여해 이전 결과와 섞이지 않게 한다. 코어
자체에는 mutex가 없으므로 ROS 어댑터가 모든 접근을 하나의 ``Condition(RLock)``로 감싼다.
"""

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping, Optional, Sequence


_NS_PER_MS = 1_000_000


@dataclass(frozen=True)
class SequenceStep:
    """이름 있는 sequence를 구성하는 pose 이름과 유지 시간 한 단계."""

    gesture_name: str
    duration_ms: int


@dataclass(frozen=True)
class StartResult:
    """단일 MANUAL 실행 슬롯의 시작 승인 여부와 새 동작 generation."""

    accepted: bool
    reason: str
    generation: int = 0


@dataclass(frozen=True)
class CommandFrame:
    """한 번의 core tick이 ROS 어댑터에 넘기는 pose 명령 값."""

    gesture_name: str
    axes: tuple[float, ...]
    speed_limit: float
    source: int


@dataclass(frozen=True)
class MotionOutcome:
    """승인된 한 generation이 완료·취소된 최종 결과."""

    generation: int
    kind: str
    success: bool
    reason: str


@dataclass(frozen=True)
class TickResult:
    """timer 경계에서 생성한 명령, 진행 정보, 선택적 종료 결과."""

    command: Optional[CommandFrame] = None
    outcome: Optional[MotionOutcome] = None
    current_step: int = 0
    total_steps: int = 0
    active_gesture: str = ''


@dataclass
class _ActiveMotion:
    """현재 실행 슬롯 하나에만 존재하는 Gesture 또는 Sequence의 가변 진행 상태."""

    kind: str
    name: str
    generation: int
    speed_limit: float
    started_ns: int
    step_index: int = 0
    step_started_ns: int = 0


class ManualExecutorCore:
    """
    Gesture와 Sequence의 시작·진행·종료를 단일 출력 차선에서 판정한다.

    주요 불변식은 (1) ``_active``는 최대 하나이고, (2) 명령은 최신 MANUAL/WEB 제어권과
    READY/RUN Safety가 모두 유효할 때만 생성되며, (3) 완료·취소 시 active 슬롯을 먼저
    비운 뒤 한 generation의 종료 결과를 반환하는 것이다.
    """

    MODE_DISABLED = 0
    MODE_MANUAL = 2
    OWNER_NONE = 0
    OWNER_WEB = 1

    SAFETY_INIT = 0
    SAFETY_READY = 1
    SAFETY_RUN = 2
    SAFETY_HOLD = 3
    SAFETY_SAFE = 4
    SAFETY_FAULT = 5
    SAFETY_ESTOP = 6
    SAFETY_RESET = 7

    SOURCE_GESTURE = 3
    SOURCE_SEQUENCE = 4

    MAX_CONTROL_STATE_TIMEOUT_MS = 1500
    MAX_SAFETY_STATE_TIMEOUT_MS = 300
    MAX_MOTION_DURATION_MS = 10_000
    MAX_SEQUENCE_STEPS = 100

    SERVICE_GESTURES = frozenset(
        {'open', 'fist', 'pinch', 'cylindrical_grasp'}
    )
    GESTURE_ALIASES = MappingProxyType(
        {'home': 'open', 'paper': 'open', 'rock': 'fist'}
    )

    def __init__(
        self,
        *,
        gestures: Mapping[str, Sequence[float]],
        gesture_durations_ms: Mapping[str, int],
        sequences: Mapping[str, Sequence[SequenceStep]],
        control_state_timeout_ms: int = 1500,
        safety_state_timeout_ms: int = 300,
    ) -> None:
        """설정 전체를 안전 상한 안에서 검증하고 실행 슬롯이 빈 초기 상태를 만든다."""
        # pose 설정은 startup 때 모두 고정한다. 잘못된 7축 수·NaN·범위 밖 값이 실행
        # 중간에 발견되어 일부 frame만 발행되는 상황을 막기 위한 fail-fast 경계다.
        self._gestures = {
            str(name): self._validated_axes(name, axes)
            for name, axes in gestures.items()
        }
        missing = self.SERVICE_GESTURES - self._gestures.keys()
        if missing:
            raise ValueError(
                'missing service gesture presets: ' + ', '.join(sorted(missing))
            )

        # service로 직접 호출 가능한 외부 gesture만 duration을 요구한다. sequence 전용
        # pose의 유지 시간은 각 SequenceStep이 별도로 소유한다.
        self._gesture_durations_ns = {}
        for name in self.SERVICE_GESTURES:
            duration_ms = self._positive_int(
                f'gesture duration {name}',
                gesture_durations_ms.get(name),
                maximum=self.MAX_MOTION_DURATION_MS,
            )
            self._gesture_durations_ns[name] = duration_ms * _NS_PER_MS

        # 모든 step 참조와 duration을 시작 전에 검증해 tick에서는 실행에만 집중한다.
        self._sequences = {}
        for sequence_name, configured_steps in sequences.items():
            steps = tuple(configured_steps)
            if not steps:
                raise ValueError(f'sequence {sequence_name} must not be empty')
            if len(steps) > self.MAX_SEQUENCE_STEPS:
                raise ValueError(
                    f'sequence {sequence_name} cannot exceed '
                    f'{self.MAX_SEQUENCE_STEPS} steps'
                )
            validated_steps = []
            for step in steps:
                if step.gesture_name not in self._gestures:
                    raise ValueError(
                        f'sequence {sequence_name} references unknown gesture '
                        f'{step.gesture_name}'
                    )
                duration_ms = self._positive_int(
                    f'sequence {sequence_name} step duration',
                    step.duration_ms,
                    maximum=self.MAX_MOTION_DURATION_MS,
                )
                validated_steps.append(
                    SequenceStep(step.gesture_name, duration_ms)
                )
            self._sequences[str(sequence_name)] = tuple(validated_steps)

        self._control_timeout_ns = self._positive_int(
            'control timeout',
            control_state_timeout_ms,
            maximum=self.MAX_CONTROL_STATE_TIMEOUT_MS,
        ) * _NS_PER_MS
        self._safety_timeout_ns = self._positive_int(
            'safety timeout',
            safety_state_timeout_ms,
            maximum=self.MAX_SAFETY_STATE_TIMEOUT_MS,
        ) * _NS_PER_MS

        # 상태 수신 전 None을 유지한다. 초기 enum 값을 추측해 첫 요청을 여는 것보다
        # 실제 표본을 받을 때까지 닫혀 있는 편이 안전하다.
        self._control_mode: Optional[int] = None
        self._control_owner: Optional[int] = None
        self._owner_alive = False
        self._control_received_ns: Optional[int] = None
        self._safety_state: Optional[int] = None
        self._safety_received_ns: Optional[int] = None
        self._active: Optional[_ActiveMotion] = None
        self._retained_command: Optional[CommandFrame] = None
        self._next_generation = 1

    @property
    def gestures(self) -> Mapping[str, tuple[float, ...]]:
        """진단과 테스트에 쓰도록 변경 불가능한 정규화 pose 목록을 반환한다."""
        return MappingProxyType(self._gestures)

    @property
    def motion_active(self) -> bool:
        """Gesture 또는 Sequence 하나가 현재 실행 슬롯을 점유 중인지 반환한다."""
        return self._active is not None

    @property
    def is_sequence_running(self) -> bool:
        """현재 실행 슬롯이 Action feedback이 필요한 Sequence인지 반환한다."""
        return self._active is not None and self._active.kind == 'sequence'

    @property
    def active_generation(self) -> int:
        """현재 동작의 correlation generation을, 비활성 상태에서는 0을 반환한다."""
        return self._active.generation if self._active is not None else 0

    def update_control_state(
        self,
        *,
        active_mode: int,
        active_owner: int,
        owner_alive: bool,
        now_ns: int,
    ) -> Optional[MotionOutcome]:
        """
        최신 제어권과 로컬 수신 시각을 저장하고 MANUAL/WEB 권한 상실 시 동작을 취소한다.

        반환값이 ``None``이면 진행 중 동작의 종료가 없었다는 뜻이다. ``MotionOutcome``이면
        어댑터는 motion 상태를 닫고, Sequence일 때만 해당 Action result까지 완료한다.
        Gesture service는 시작 승인만 응답하므로 사후 종료 결과를 다시 전달하지 않는다.
        """
        self._control_mode = int(active_mode)
        self._control_owner = int(active_owner)
        self._owner_alive = bool(owner_alive)
        self._control_received_ns = self._nonnegative_time(now_ns)
        if (
            self._active is not None or self._retained_command is not None
        ) and not self._has_manual_control():
            return self.cancel('control_lost')
        return None

    def update_safety_state(
        self,
        state: int,
        *,
        now_ns: int,
    ) -> Optional[MotionOutcome]:
        """최신 Safety와 수신 시각을 저장하고 READY/RUN 이탈 시 동작을 취소한다."""
        self._safety_state = int(state)
        self._safety_received_ns = self._nonnegative_time(now_ns)
        if (
            self._active is not None or self._retained_command is not None
        ) and state not in (
            self.SAFETY_READY,
            self.SAFETY_RUN,
        ):
            return self.cancel(self._safety_reason(int(state)))
        return None

    def validate_gesture(
        self,
        gesture_name: str,
        speed_limit: float,
        *,
        now_ns: int,
    ) -> StartResult:
        """동작 상태를 바꾸지 않고 Gesture 이름과 공통 시작 조건을 검사한다."""
        canonical = self._canonical_gesture(gesture_name)
        if canonical is None:
            return StartResult(False, 'invalid_gesture')
        return self._validate_common(speed_limit, now_ns=now_ns)

    def validate_sequence(
        self,
        sequence_name: str,
        speed_limit: float,
        *,
        now_ns: int,
    ) -> StartResult:
        """동작 상태를 바꾸지 않고 Sequence 이름과 공통 시작 조건을 검사한다."""
        if sequence_name not in self._sequences:
            return StartResult(False, 'invalid_sequence')
        return self._validate_common(speed_limit, now_ns=now_ns)

    def start_gesture(
        self,
        gesture_name: str,
        speed_limit: float,
        *,
        now_ns: int,
    ) -> StartResult:
        """별칭을 정식 Gesture 이름으로 바꾼 뒤 단일 실행 슬롯을 점유한다."""
        validation = self.validate_gesture(
            gesture_name,
            speed_limit,
            now_ns=now_ns,
        )
        if not validation.accepted:
            return validation
        canonical = self._canonical_gesture(gesture_name)
        assert canonical is not None
        return self._start(
            kind='gesture',
            name=canonical,
            speed_limit=float(speed_limit),
            now_ns=now_ns,
        )

    def start_sequence(
        self,
        sequence_name: str,
        speed_limit: float,
        *,
        now_ns: int,
    ) -> StartResult:
        """검증된 Sequence를 새 generation으로 단일 실행 슬롯에 시작한다."""
        validation = self.validate_sequence(
            sequence_name,
            speed_limit,
            now_ns=now_ns,
        )
        if not validation.accepted:
            return validation
        return self._start(
            kind='sequence',
            name=sequence_name,
            speed_limit=float(speed_limit),
            now_ns=now_ns,
        )

    def cancel(self, reason: str) -> Optional[MotionOutcome]:
        """active와 retained 출력을 닫고 active generation의 실패 결과만 한 번 반환한다."""
        self._retained_command = None
        if self._active is None:
            return None
        active = self._active
        self._active = None
        return MotionOutcome(
            generation=active.generation,
            kind=active.kind,
            success=False,
            reason=str(reason),
        )

    def tick(self, *, now_ns: int) -> TickResult:
        """
        한 timer 시점의 freshness·duration을 판정해 현재 pose 또는 종료 결과를 반환한다.

        Sequence는 executor 지연으로 step deadline을 여러 개 지났어도 pose를 건너뛰지
        않고, 처음 발행된 시각부터 각 step의 전체 유지 시간을 새로 보장한다.
        """
        now_ns = self._nonnegative_time(now_ns)
        active = self._active
        if active is None:
            if self._retained_command is None:
                return TickResult()
            runtime_issue = self._runtime_issue(now_ns)
            if runtime_issue is not None:
                self.cancel(runtime_issue)
                return TickResult()
            return TickResult(command=self._retained_command)

        runtime_issue = self._runtime_issue(now_ns)
        if runtime_issue is not None:
            return TickResult(outcome=self.cancel(runtime_issue))

        if active.kind == 'gesture':
            deadline_ns = (
                active.started_ns + self._gesture_durations_ns[active.name]
            )
            if now_ns >= deadline_ns:
                outcome = self._complete()
                return TickResult(
                    command=self._retained_command,
                    outcome=outcome,
                )
            return TickResult(
                command=self._frame(active.name, active.speed_limit, active.kind),
                current_step=1,
                total_steps=1,
                active_gesture=active.name,
            )

        steps = self._sequences[active.name]
        step = steps[active.step_index]
        deadline_ns = active.step_started_ns + step.duration_ms * _NS_PER_MS
        if now_ns >= deadline_ns:
            active.step_index += 1
            if active.step_index >= len(steps):
                outcome = self._complete()
                return TickResult(
                    command=self._retained_command,
                    outcome=outcome,
                )
            # 늦어진 tick은 현재 step을 길게 만들 수는 있어도 설정 pose를 건너뛰면 안 된다.
            # 다음 step은 처음 반환되는 바로 이 시각부터 전체 hold duration을 새로 받는다.
            active.step_started_ns = now_ns
            step = steps[active.step_index]
        return TickResult(
            command=self._frame(
                step.gesture_name,
                active.speed_limit,
                active.kind,
            ),
            current_step=active.step_index + 1,
            total_steps=len(steps),
            active_gesture=step.gesture_name,
        )

    def _start(
        self,
        *,
        kind: str,
        name: str,
        speed_limit: float,
        now_ns: int,
    ) -> StartResult:
        """검증이 끝난 동작에 새 generation을 부여하고 유일한 active 슬롯을 만든다."""
        generation = self._next_generation
        self._next_generation += 1
        now_ns = self._nonnegative_time(now_ns)
        # 새 동작이 수락되면 이전 idle heartbeat는 다시 살아나면 안 된다. 이후 cancel이나
        # 실패가 발생해도 새 동작 이전 pose로 되돌아가지 않도록 시작 시점에 폐기한다.
        self._retained_command = None
        self._active = _ActiveMotion(
            kind=kind,
            name=name,
            generation=generation,
            speed_limit=speed_limit,
            started_ns=now_ns,
            step_started_ns=now_ns,
        )
        return StartResult(True, 'accepted', generation)

    def _validate_common(self, speed_limit: float, *, now_ns: int) -> StartResult:
        """Gesture/Sequence가 공유하는 속도·상호 배제·상태 freshness 조건을 검사한다."""
        if (
            isinstance(speed_limit, bool)
            or not isinstance(speed_limit, (int, float))
            or not math.isfinite(float(speed_limit))
            or not 0.0 < float(speed_limit) <= 1.0
        ):
            return StartResult(False, 'invalid_speed_limit')
        if self._active is not None:
            return StartResult(False, 'motion_active')
        issue = self._request_state_issue(self._nonnegative_time(now_ns))
        if issue is not None:
            return StartResult(False, issue)
        return StartResult(True, 'accepted')

    def _request_state_issue(self, now_ns: int) -> Optional[str]:
        """새 요청 시 필요한 제어권과 Safety 표본의 존재·freshness·값을 순서대로 검사한다."""
        if self._control_received_ns is None:
            return 'control_state_unavailable'
        if now_ns - self._control_received_ns > self._control_timeout_ns:
            return 'control_state_stale'
        if not self._has_manual_control():
            return 'not_manual_mode'
        if self._safety_received_ns is None:
            return 'safety_state_unavailable'
        if now_ns - self._safety_received_ns > self._safety_timeout_ns:
            return 'safety_state_stale'
        if self._safety_state not in (self.SAFETY_READY, self.SAFETY_RUN):
            return 'safety_not_ready'
        return None

    def _runtime_issue(self, now_ns: int) -> Optional[str]:
        """실행 중 매 tick 다시 확인할 제어권·Safety 중단 사유를 반환한다."""
        if self._control_received_ns is None:
            return 'control_state_unavailable'
        if now_ns - self._control_received_ns > self._control_timeout_ns:
            return 'control_state_stale'
        if not self._has_manual_control():
            return 'control_lost'
        if self._safety_received_ns is None:
            return 'safety_state_unavailable'
        if now_ns - self._safety_received_ns > self._safety_timeout_ns:
            return 'safety_state_stale'
        if self._safety_state not in (self.SAFETY_READY, self.SAFETY_RUN):
            return self._safety_reason(self._safety_state)
        return None

    def _has_manual_control(self) -> bool:
        """Command Manager가 MANUAL/WEB owner를 살아 있는 상태로 승인했는지 본다."""
        return (
            self._control_mode == self.MODE_MANUAL
            and self._control_owner == self.OWNER_WEB
            and self._owner_alive
        )

    def _complete(self) -> MotionOutcome:
        """슬롯은 비우고 마지막 pose는 idle heartbeat용으로 보존한다."""
        active = self._active
        assert active is not None
        final_gesture = (
            active.name
            if active.kind == 'gesture'
            else self._sequences[active.name][-1].gesture_name
        )
        self._retained_command = self._frame(
            final_gesture,
            active.speed_limit,
            active.kind,
        )
        self._active = None
        return MotionOutcome(
            generation=active.generation,
            kind=active.kind,
            success=True,
            reason='completed',
        )

    def _frame(self, name: str, speed_limit: float, kind: str) -> CommandFrame:
        """현재 pose와 요청 종류에 맞는 HandCommand source 정보를 묶는다."""
        return CommandFrame(
            gesture_name=name,
            axes=self._gestures[name],
            speed_limit=speed_limit,
            source=(
                self.SOURCE_GESTURE
                if kind == 'gesture'
                else self.SOURCE_SEQUENCE
            ),
        )

    def _canonical_gesture(self, name: str) -> Optional[str]:
        """허용된 service 별칭을 정식 이름으로 바꾸고 외부 비공개 pose는 거부한다."""
        canonical = self.GESTURE_ALIASES.get(name, name)
        return canonical if canonical in self.SERVICE_GESTURES else None

    @staticmethod
    def _safety_reason(state: Optional[int]) -> str:
        """Safety enum을 호출자가 구분할 수 있는 안정된 종료 reason으로 바꾼다."""
        return {
            ManualExecutorCore.SAFETY_HOLD: 'safety_hold',
            ManualExecutorCore.SAFETY_SAFE: 'safety_safe',
            ManualExecutorCore.SAFETY_FAULT: 'safety_fault',
            ManualExecutorCore.SAFETY_ESTOP: 'safety_estop',
        }.get(state, 'safety_not_ready')

    @staticmethod
    def _validated_axes(name: str, axes: Sequence[float]) -> tuple[float, ...]:
        """pose가 정확히 7축이며 모든 값이 유한한 정규화 범위인지 검증한다."""
        values = tuple(float(value) for value in axes)
        if len(values) != 7:
            raise ValueError(f'gesture {name} must contain exactly 7 axes')
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in values):
            raise ValueError(f'gesture {name} axes must be finite values in [0, 1]')
        return values

    @staticmethod
    def _positive_int(name: str, value, *, maximum: Optional[int] = None) -> int:
        """bool을 제외한 양의 정수와 선택적 안전 상한을 검증한다."""
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f'{name} must be a positive integer')
        if maximum is not None and value > maximum:
            raise ValueError(f'{name} must be at most {maximum}')
        return value

    @staticmethod
    def _nonnegative_time(value: int) -> int:
        """주입 시각이 bool이 아닌 0 이상의 정수 나노초인지 검증한다."""
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError('now_ns must be a non-negative integer')
        return value
