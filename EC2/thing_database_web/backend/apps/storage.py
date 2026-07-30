# backend/apps/storage.py
"""
세션 파일 저장 레이아웃과 원자성.

요구사항 명세서 6.5절 EC2 저장·원자성:
    SQLite: /var/lib/thing-data/db.sqlite3
    파일:   /var/lib/thing-data/sessions/{robot_id}/{session_id}/
    staging에 streaming 저장하며 크기·content type·schema·header·행 수·SHA-256과
    content_digest를 검증한다. 검증 후 같은 파일시스템의 final 경로로 원자적
    rename하고 SQLite를 READY로 변경한다. 공개 API는 READY만 읽는다.

레이아웃

    <EC2_DATA_DIR>/
    ├─ db.sqlite3
    ├─ staging/<robot_id>/<session_id>/     ← 검증 중. 공개하지 않는다
    └─ sessions/<robot_id>/<session_id>/    ← READY. 공개 대상
        ├─ session_<session_id>_metadata.json
        ├─ session_<session_id>_hand_command.csv
        └─ session_<session_id>_motor_status.csv

staging과 sessions를 같은 EC2_DATA_DIR 아래 두는 이유는 os.replace()의 원자성이
동일 파일시스템 내에서만 보장되기 때문이다. 다른 마운트로 분리하면 rename이
copy+unlink로 대체되어 중간 상태가 노출될 수 있다.

robot_id·session_id는 경로 구성 요소가 되므로 반드시 화이트리스트 검증을 거친다.
클라이언트가 보낸 파일명은 저장에 사용하지 않는다 (FR-51).
"""
import os
import re
import shutil
from pathlib import Path

from django.conf import settings

#: 다운로드 가능한 파일 종류. 경로 입력을 받지 않는 enum이다 (FR-49)
FILE_KINDS = ("metadata", "hand_command", "motor_status")

#: 종류별 확장자
_EXTENSIONS = {
    "metadata": "json",
    "hand_command": "csv",
    "motor_status": "csv",
}

#: 경로 구성 요소 허용 문자. 상위 디렉터리 탈출과 구분자 주입을 원천 차단한다.
_ROBOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,49}$")
_SESSION_ID_RE = re.compile(r"^[0-9]{1,20}$")


class UnsafeIdentifier(ValueError):
    """robot_id 또는 session_id가 경로로 쓰기에 안전하지 않을 때."""


def validate_robot_id(robot_id):
    if not isinstance(robot_id, str) or not _ROBOT_ID_RE.match(robot_id):
        raise UnsafeIdentifier("robot_id")
    return robot_id


def validate_session_id(session_id):
    """Session ID는 ROS uint64의 10진 표기다. 문자열로 다룬다."""
    if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
        raise UnsafeIdentifier("session_id")
    if int(session_id) > 2**64 - 1:
        raise UnsafeIdentifier("session_id")
    return session_id


def validate_file_kind(file_kind):
    if file_kind not in FILE_KINDS:
        raise UnsafeIdentifier("file_kind")
    return file_kind


def data_root():
    """EC2_DATA_DIR. 배포는 /var/lib/thing-data, 개발·테스트는 설정으로 덮어쓴다."""
    return Path(settings.EC2_DATA_DIR)


def canonical_filename(session_id, file_kind):
    """공개 다운로드 파일명. 명세서 6.5절 '다운로드 파일'의 세 이름만 허용한다."""
    validate_session_id(session_id)
    validate_file_kind(file_kind)
    return f"session_{session_id}_{file_kind}.{_EXTENSIONS[file_kind]}"


def staging_dir(robot_id, session_id):
    validate_robot_id(robot_id)
    validate_session_id(session_id)
    return data_root() / "staging" / robot_id / session_id


def final_dir(robot_id, session_id):
    validate_robot_id(robot_id)
    validate_session_id(session_id)
    return data_root() / "sessions" / robot_id / session_id


def final_path(robot_id, session_id, file_kind):
    return final_dir(robot_id, session_id) / canonical_filename(session_id, file_kind)


def prepare_staging(robot_id, session_id):
    """staging 디렉터리를 비우고 새로 만든다.

    이전 시도가 crash로 남긴 부분 파일을 여기서 정리한다 (NFR-26 재조정).
    """
    path = staging_dir(robot_id, session_id)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def stream_to_staging(uploaded_file, robot_id, session_id, file_kind, chunk_size=1024 * 1024):
    """업로드 파일을 staging에 스트리밍 저장한다. 메모리에 전체를 올리지 않는다.

    저장 파일명은 서버가 canonical 규칙으로 정한다. 클라이언트 파일명은 쓰지 않는다.
    """
    target = staging_dir(robot_id, session_id) / canonical_filename(session_id, file_kind)
    written = 0
    with open(target, "wb") as out:
        for chunk in uploaded_file.chunks(chunk_size):
            out.write(chunk)
            written += len(chunk)
        out.flush()
        os.fsync(out.fileno())
    return target, written


def commit_staging(robot_id, session_id):
    """검증을 통과한 staging을 final 경로로 원자적으로 옮긴다.

    파일 단위 os.replace()를 사용한다. 같은 파일시스템이므로 각 rename이 원자적이다.
    디렉터리 자체를 rename하지 않는 이유는 final 디렉터리가 이미 존재할 때
    (같은 digest 재업로드 등) 처리가 단순해지기 때문이다.
    """
    src = staging_dir(robot_id, session_id)
    dst = final_dir(robot_id, session_id)
    dst.mkdir(parents=True, exist_ok=True)

    moved = []
    for file_kind in FILE_KINDS:
        name = canonical_filename(session_id, file_kind)
        source = src / name
        if not source.exists():
            raise FileNotFoundError(file_kind)
        os.replace(source, dst / name)
        moved.append(file_kind)

    # 디렉터리 엔트리 변경을 디스크에 반영한다
    _fsync_dir(dst)
    discard_staging(robot_id, session_id)
    return moved


def discard_staging(robot_id, session_id):
    """staging 디렉터리를 제거한다. 실패해도 예외를 올리지 않는다."""
    shutil.rmtree(staging_dir(robot_id, session_id), ignore_errors=True)


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def ensure_layout():
    """EC2_DATA_DIR 하위 디렉터리를 만든다. 배포 시 1회, /health가 쓰기 확인에 사용."""
    root = data_root()
    for sub in ("", "staging", "sessions"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root
