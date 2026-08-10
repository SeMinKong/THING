"""녹화 세션의 생명주기를 메모리에서 관리하는 모듈."""

from dataclasses import dataclass
from pathlib import Path
import secrets
from typing import Callable, Optional, Tuple

from thing_interfaces.msg import ControlState
from thing_interfaces.msg import RecordingState
from thing_interfaces.msg import SafetyState


def generate_session_id() -> int:
    return secrets.randbits(63)


@dataclass
class Session:
    # Logger가 생성한 0이 아닌 고유한 63-bit 양의 정수
    session_id: int

    # StartRecording 요청으로 전달받은 세션 label
    label: str

    # 이 세션의 로컬 rosbag2 저장 경로
    bag_path: str

    # 세션 기록 시작 UTC 시각을 나노초로 표현한 값
    started_at_ns: int

    # 세션 종료 UTC 시각
    # 기록 중이거나 아직 정상적으로 종료되지 않았다면 None
    ended_at_ns: Optional[int] = None

    # 모방 결과
    # 정상 종료 후 SUCCESS 또는 FAILURE가 결정되기 전까지 UNSET
    result: int = RecordingState.RESULT_UNSET


class SessionManager:
    """녹화 세션의 상태를 별도 파일에 저장하지 않고 메모리에서 관리한다."""

    def __init__(
        self,
        bag_root: str,
        session_id_factory: Callable[[], int] = generate_session_id,
    ) -> None:
        """세션 관리자를 IDLE 상태로 초기화한다."""
        # 모든 rosbag2 세션이 저장될 최상위 경로
        self.bag_root = Path(bag_root)

        # 테스트에서는 고정 ID 생성 함수를 주입할 수 있음
        self._session_id_factory = session_id_factory

        # 현재 RecordingState 상태
        self.state: int = RecordingState.IDLE

        # 현재 시작 또는 녹화·종료 중인 세션
        self.active_session: Optional[Session] = None

        # 가장 최근에 종료·중단·실패한 세션
        self.last_session: Optional[Session] = None

        # 정상 종료된 세션이 SUCCESS/FAILURE 판정을 기다리는지 표시
        self.result_pending: bool = False

        # RecordingState 등에 전달할 상태 설명
        self.message: str = 'idle'

    def can_start(
        self,
        active_mode: int,
        safety_state: int,
    ) -> Tuple[bool, str]:
        """현재 상태와 제어 모드에서 새 녹화를 시작할 수 있는지 확인한다."""
        # V7.0 명세: 기록은 MIMIC 모드에서만 허용
        if active_mode != ControlState.MODE_MIMIC:
            return False, 'not_mimic_mode'

        # 안전 복구가 끝나지 않은 상태에서는 새 녹화를 시작하지 않는다.
        # MIMIC 활성화 뒤 첫 명령으로 RUN이 될 수 있으므로 READY와 RUN을 허용한다.
        if safety_state not in (
            SafetyState.READY,
            SafetyState.RUN,
        ):
            return False, 'start_failed'

        # V7.0 명세: 이전 정상 세션의 결과 판정 전에는
        # 다음 정상 기록을 시작할 수 없음
        if self.result_pending:
            return False, 'result_pending'

        # 동시에 하나의 기록만 허용
        if (
            self.state != RecordingState.IDLE
            or self.active_session is not None
        ):
            return False, 'already_recording'

        return True, ''

    def begin_start(self, label: str, started_at_ns: int) -> Session:
        """고유한 세션을 생성하고 STARTING 상태로 전환한다."""
        if self.state != RecordingState.IDLE:
            raise RuntimeError('recording lifecycle is not idle')

        if self.active_session is not None:
            raise RuntimeError('an active session already exists')

        if self.result_pending:
            raise RuntimeError('a completed session still needs a result')

        # 0이 아닌 고유 Session ID와 충돌하지 않는 bag 경로 생성
        session_id, bag_path = self._create_unique_identity()

        session = Session(
            session_id=session_id,
            label=label,
            bag_path=str(bag_path),
            started_at_ns=started_at_ns,
        )

        self.active_session = session
        self.state = RecordingState.STARTING
        self.message = 'starting'

        return session

    def mark_recording(self) -> None:
        """rosbag2가 정상적으로 시작되면 RECORDING 상태로 전환한다."""
        if self.state != RecordingState.STARTING:
            raise RuntimeError(
                'recording can only follow the starting state'
            )

        if self.active_session is None:
            raise RuntimeError('active session is missing')

        self.state = RecordingState.RECORDING
        self.message = 'recording'

    def cancel_start(
        self,
        message: str = 'start failed',
    ) -> Session:
        """rosbag2 시작에 실패하면 STARTING 상태를 취소한다."""
        if self.state != RecordingState.STARTING:
            raise RuntimeError(
                'only a starting session can be cancelled'
            )

        if self.active_session is None:
            raise RuntimeError('active session is missing')

        cancelled_session = self.active_session

        self.active_session = None
        self.state = RecordingState.IDLE
        self.message = message

        # logger.py 또는 bag_recorder.py에서 시작 중 생성된
        # 불완전한 rosbag 경로를 정리할 수 있도록 반환
        return cancelled_session

    def can_stop(self, session_id: int) -> Tuple[bool, str]:
        """요청한 활성 세션을 종료할 수 있는지 확인한다."""
        if (
            self.state != RecordingState.RECORDING
            or self.active_session is None
        ):
            return False, 'not_recording'

        if self.active_session.session_id != session_id:
            return False, 'session_mismatch'

        return True, ''

    def mark_stopping(self) -> None:
        """rosbag2를 비우고 닫기 전에 STOPPING 상태로 전환한다."""
        if self.state != RecordingState.RECORDING:
            raise RuntimeError(
                'only a recording session can enter stopping'
            )

        if self.active_session is None:
            raise RuntimeError('active session is missing')

        self.state = RecordingState.STOPPING
        self.message = 'stopping'

    def complete(self, ended_at_ns: int) -> Session:
        """정상 종료를 완료하고 모방 결과 판정을 기다린다."""
        if self.state != RecordingState.STOPPING:
            raise RuntimeError(
                'session can only complete after stopping'
            )

        if self.active_session is None:
            raise RuntimeError('active session is missing')

        if ended_at_ns < self.active_session.started_at_ns:
            raise ValueError(
                'session end time cannot precede its start time'
            )

        completed_session = self.active_session
        completed_session.ended_at_ns = ended_at_ns

        self.active_session = None
        self.last_session = completed_session

        # V7.0 명세:
        # 정상 Stop 후 COMPLETED 상태에서 결과 판정을 기다림
        self.result_pending = True
        self.state = RecordingState.COMPLETED
        self.message = 'result pending'

        return completed_session

    def set_result(
        self,
        session_id: int,
        result: int,
    ) -> Tuple[bool, str]:
        """완료된 세션의 SUCCESS 또는 FAILURE 결과를 한 번만 받는다."""
        if self.active_session is not None:
            return False, 'recording_active'

        if result not in (
            RecordingState.RESULT_SUCCESS,
            RecordingState.RESULT_FAILURE,
        ):
            return False, 'invalid_result'

        if self.last_session is None:
            return False, 'session_not_found'

        if self.last_session.session_id != session_id:
            return False, 'session_not_found'

        if self.state in (
            RecordingState.INTERRUPTED,
            RecordingState.FAILED,
        ):
            return False, 'session_not_found'

        if not self.result_pending:
            return False, 'result_already_set'

        self.last_session.result = result
        self.result_pending = False

        # 결과가 수락되면 새 정상 기록을 받을 수 있도록 IDLE 전환
        # exporter 시작은 logger.py가 이 메서드의 성공 결과를 받은 직후 수행
        self.state = RecordingState.IDLE
        self.message = 'result accepted'

        return True, ''

    def interrupt(
        self,
        ended_at_ns: int,
        message: str = 'recording interrupted',
    ) -> Optional[Session]:
        """활성 세션을 결과 판정 대기 없이 중단한다."""
        if self.active_session is None:
            return None

        if self.state not in (
            RecordingState.STARTING,
            RecordingState.RECORDING,
            RecordingState.STOPPING,
        ):
            raise RuntimeError(
                'current session state cannot be interrupted'
            )

        if ended_at_ns < self.active_session.started_at_ns:
            raise ValueError(
                'session end time cannot precede its start time'
            )

        interrupted_session = self.active_session
        interrupted_session.ended_at_ns = ended_at_ns

        self.active_session = None
        self.last_session = interrupted_session
        self.result_pending = False
        self.state = RecordingState.INTERRUPTED
        self.message = message

        # 이 메서드는 상태만 변경함.
        # 중단된 rosbag2의 flush·종료·삭제는
        # logger.py와 bag_recorder.py가 담당해야 함.
        return interrupted_session

    def mark_failed(
        self,
        message: str,
        ended_at_ns: Optional[int] = None,
    ) -> Optional[Session]:
        """진행 중인 녹화 생명주기를 FAILED 상태로 전환한다."""
        if self.state not in (
            RecordingState.STARTING,
            RecordingState.RECORDING,
            RecordingState.STOPPING,
        ):
            raise RuntimeError(
                'only an active recording lifecycle can fail'
            )

        failed_session = self.active_session

        if failed_session is not None:
            if ended_at_ns is not None:
                if ended_at_ns < failed_session.started_at_ns:
                    raise ValueError(
                        'session end time cannot precede its start time'
                    )

                failed_session.ended_at_ns = ended_at_ns

            self.last_session = failed_session

        self.active_session = None
        self.result_pending = False
        self.state = RecordingState.FAILED
        self.message = message

        return failed_session

    def reset_to_idle(self) -> None:
        """INTERRUPTED 또는 FAILED 상태를 IDLE로 초기화한다."""
        if self.active_session is not None:
            raise RuntimeError(
                'an active session cannot be reset'
            )

        if self.result_pending:
            raise RuntimeError(
                'a result-pending session cannot be reset'
            )

        # 정상 COMPLETED 상태를 Reset으로 우회하지 못하도록 제한
        if self.state not in (
            RecordingState.INTERRUPTED,
            RecordingState.FAILED,
        ):
            raise RuntimeError(
                'only interrupted or failed states can be reset'
            )

        self.state = RecordingState.IDLE
        self.message = 'idle'

    def _create_unique_identity(self) -> Tuple[int, Path]:
        """0이 아닌 63-bit ID와 충돌하지 않는 bag 경로를 생성한다."""
        # 극히 드문 난수 충돌 또는 잘못된 테스트용 factory 값에 대비
        for _ in range(100):
            session_id = self._session_id_factory()

            # V7.0 명세:
            # CSPRNG로 만든 0이 아닌 63-bit 양의 정수
            if session_id <= 0 or session_id >= 2**63:
                continue

            bag_path = self.bag_root / str(session_id)

            # 기존 rosbag2 경로와 충돌하지 않는 경우에만 사용
            if not bag_path.exists():
                return session_id, bag_path

        raise RuntimeError(
            'failed to generate a unique session ID'
        )
