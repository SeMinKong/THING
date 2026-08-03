# backend/apps/serializers_v1.py
"""
공개 GET 응답 JSON schema v1 직렬화.

요구사항 명세서 6.5절:
    모든 Session ID는 문자열, 모든 시각은 RFC 3339 UTC `Z`다.
    `next_cursor`는 서버가 발급하는 opaque 문자열이며 다음 페이지가 없으면 `null`이다.
    클라이언트는 cursor 내부 형식을 해석하지 않는다.
    ... 정수·실수·boolean은 JSON 기본 타입을 사용하고 읽기 실패 숫자는 `null`이다.
"""
import base64
import binascii
import csv
import json

from .errors import MalformedRequest
from .validators import HAND_COMMAND_HEADER, MOTOR_STATUS_HEADER

# ── 시각 ──

def rfc3339_z(value):
    """timezone-aware datetime을 RFC 3339 UTC 'Z' (밀리초 3자리)로 만든다.

    Django는 USE_TZ=True 로 UTC 저장하므로 값은 이미 UTC aware 다.
    isoformat() 은 '+00:00' 을 붙이므로 직접 포맷한다.
    """
    if value is None:
        return None
    return (
        value.strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{value.microsecond // 1000:03d}Z"
    )


# ── cursor ──

def encode_cursor(payload):
    """dict를 opaque base64url 문자열로 만든다. 클라이언트는 해석하지 않는다."""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor):
    """cursor를 dict로 되돌린다. 손상된 값은 400으로 거부한다."""
    if not cursor:
        return None
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise MalformedRequest(details=["cursor: 형식이 올바르지 않다"])
    if not isinstance(payload, dict):
        raise MalformedRequest(details=["cursor: 형식이 올바르지 않다"])
    return payload


# ── 세션 ──

def session_list_item(session):
    """목록 GET /api/v1/sessions 의 items 원소."""
    return {
        "session_id": session.session_id,
        "robot_id": session.robot_id,
        "started_at": rfc3339_z(session.started_at),
        "ended_at": rfc3339_z(session.ended_at),
        "uploaded_at": rfc3339_z(session.uploaded_at),
        "result": session.result,
        "duration_ms": session.duration_ms,
        "row_counts": session.row_counts,
        "file_sizes": session.file_sizes,
    }


def session_detail(session):
    """상세 GET /api/v1/sessions/{session_id}."""
    base = f"/api/v1/sessions/{session.session_id}/download"
    return {
        "session_id": session.session_id,
        "robot_id": session.robot_id,
        "schema_version": session.schema_version,
        "data_version": session.data_version,
        "started_at": rfc3339_z(session.started_at),
        "ended_at": rfc3339_z(session.ended_at),
        "uploaded_at": rfc3339_z(session.uploaded_at),
        "result": session.result,
        "duration_ms": session.duration_ms,
        "interface_commit": session.interface_commit,
        "time_sync": session.time_sync,
        "content_digest": session.content_digest,
        "row_counts": session.row_counts,
        "file_sizes": session.file_sizes,
        "downloads": {
            "metadata": f"{base}/metadata",
            "hand_command": f"{base}/hand_command",
            "motor_status": f"{base}/motor_status",
        },
    }


# ── 시계열 ──

#: 명세서의 hand_command 예시 columns 에는 session_id 가 없다.
#: session_id 는 응답 최상위에 이미 있어 행마다 반복하지 않는다.
#: motor_status 도 같은 규칙을 적용한다. 포함하려면 이 값만 False 로 바꾸면 된다.
OMIT_SESSION_ID_COLUMN = True

#: 문자열로 그대로 내보낼 컬럼. 나머지는 숫자 또는 boolean 이다.
_STRING_COLUMNS = {"source", "frame_id", "actuator_name"}

#: boolean 컬럼 (CSV 에서 true|false)
_BOOL_COLUMNS = {"communication_ok", "bus_communication_ok"}

#: 정수 컬럼. 나머지 숫자는 실수로 취급한다.
_INT_COLUMNS = {
    "stamp_sec", "stamp_nanosec", "elapsed_ms", "sequence", "motor_id",
    "goal_position_raw", "present_position_raw", "temperature_celsius",
    "hardware_error", "communication_result", "failed_read_count",
}

DATASET_HEADERS = {
    "hand_command": HAND_COMMAND_HEADER,
    "motor_status": MOTOR_STATUS_HEADER,
}


def dataset_columns(dataset):
    header = DATASET_HEADERS[dataset]
    if OMIT_SESSION_ID_COLUMN:
        return [c for c in header if c != "session_id"]
    return list(header)


def _convert(column, raw):
    """CSV 셀을 JSON 타입으로 바꾼다. 빈 칸은 null 이다."""
    value = raw.strip()
    if column in _STRING_COLUMNS:
        return value
    if value == "":
        # 명세서: 읽기 실패 숫자는 CSV 에서 빈 칸, JSON 에서 null
        return None
    if column in _BOOL_COLUMNS:
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return None
    try:
        if column in _INT_COLUMNS:
            return int(value)
        return float(value)
    except ValueError:
        return None


def read_dataset_rows(path, dataset, offset, limit):
    """CSV 에서 offset 부터 limit 행을 읽어 JSON row 목록으로 만든다.

    파일 전체를 메모리에 올리지 않고 필요한 구간만 순차 소비한다.
    반환값은 (rows, next_offset) 이며 다음 페이지가 없으면 next_offset 은 None 이다.
    """
    header = DATASET_HEADERS[dataset]
    columns = dataset_columns(dataset)
    indices = [(c, header.index(c)) for c in columns]

    rows = []
    next_offset = None

    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)                       # header 행 건너뛰기

        for index, raw_row in enumerate(reader):
            if index < offset:
                continue
            if len(rows) >= limit:
                next_offset = offset + limit     # 더 남았다
                break
            if len(raw_row) != len(header):
                continue                         # 손상 행은 건너뛴다
            rows.append({name: _convert(name, raw_row[pos]) for name, pos in indices})

    return rows, next_offset
