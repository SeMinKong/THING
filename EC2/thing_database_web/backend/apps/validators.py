# backend/apps/validators.py
"""
metadata JSON schema v1 과 CSV 규칙 검증.

요구사항 명세서 6.5절.

metadata:
    Session ID는 ROS uint64지만 JSON·API·EC2 SQLite에서는 문자열이다.
    시간은 RFC 3339 UTC `Z`, ended_at>started_at; result는 SUCCESS 또는 FAILURE다.
    schema_version과 data_version은 이번 MVP에서 1로 고정한다.

CSV:
    UTF-8 without BOM, RFC 4180 quoting, comma, header 1행, LF, 소수점 `.`,
    bool `true|false`를 사용한다. NaN·Infinity를 금지하고 읽기 실패 숫자는 빈 칸으로
    둔다. 각 행은 Session ID, UTC Unix stamp와 `elapsed_ms`를 포함하며 timestamp는
    비감소여야 한다.

외부 의존성(jsonschema 등)을 추가하지 않고 손으로 검증한다. 서버에 pip 설치를
늘리지 않으려는 의도이며, 스키마가 고정 1버전이라 손검증으로 충분하다.
"""
from . import landmark_contract, limits
import csv
import io
import json
import math
import re
from datetime import datetime, timezone

from .errors import MalformedRequest, ValidationFailed

# ── 상수 ──

MiB = 1024 * 1024
KiB = 1024

#: part별 크기 상한 (명세서 6.5절 multipart 업로드)
#: apps/limits.py 가 단일 출처다. 여기서 다시 적지 않는다.
PART_MAX_BYTES = limits.PART_MAX_BYTES

TOTAL_MAX_BYTES = limits.TOTAL_MAX_BYTES

#: part별 허용 content type
PART_CONTENT_TYPES = {
    "metadata": ("application/json",),
    "hand_command": ("text/csv",),
    "motor_status": ("text/csv",),
    landmark_contract.KIND: landmark_contract.CONTENT_TYPES,
}

#: HandCommand CSV header (명세서 6.5절 CSV 규칙)
HAND_COMMAND_HEADER = [
    "session_id", "stamp_sec", "stamp_nanosec", "elapsed_ms", "sequence", "source",
    "thumb_flex", "thumb_opp", "thumb_abd", "index_flex", "middle_flex", "ring_flex",
    "little_flex", "speed_limit", "confidence",
]

#: MotorStatus CSV header
MOTOR_STATUS_HEADER = [
    "session_id", "stamp_sec", "stamp_nanosec", "elapsed_ms", "frame_id", "motor_id",
    "actuator_name", "goal_position_raw", "present_position_raw", "goal_position_rad",
    "present_position_rad", "velocity_rad_s", "current_ampere", "voltage_volt",
    "temperature_celsius", "torque_enabled", "hardware_error", "communication_result",
    "communication_ok", "bus_communication_ok", "failed_read_count",
]

CSV_HEADERS = {
    "hand_command": HAND_COMMAND_HEADER,
    "motor_status": MOTOR_STATUS_HEADER,
}

_SESSION_ID_RE = re.compile(r"^[0-9]{1,20}$")
_ROBOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,49}$")
_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SHA1_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_RFC3339_Z_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$"
)

#: NaN·Infinity 표기. CSV에서 금지한다.
_FORBIDDEN_NUMERICS = {"nan", "-nan", "+nan", "inf", "-inf", "+inf",
                       "infinity", "-infinity", "+infinity"}


# ── metadata ──

def parse_rfc3339_utc(value, field):
    """RFC 3339 UTC `Z` 문자열을 timezone-aware datetime으로 바꾼다."""
    if not isinstance(value, str) or not _RFC3339_Z_RE.match(value):
        raise ValidationFailed(details=[f"{field}: RFC 3339 UTC 'Z' 형식이어야 한다"])
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def load_metadata(raw_bytes):
    """metadata part를 JSON으로 파싱한다. BOM과 잘못된 JSON을 거부한다."""
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raise ValidationFailed(details=["metadata: UTF-8 BOM은 허용하지 않는다"])
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ValidationFailed(details=["metadata: UTF-8로 디코딩할 수 없다"])
    try:
        parsed = json.loads(text, parse_constant=_reject_constant)
    except ValueError as exc:
        raise MalformedRequest(details=["metadata: JSON 파싱 실패"], log_detail=str(exc))
    if not isinstance(parsed, dict):
        raise ValidationFailed(details=["metadata: 최상위는 객체여야 한다"])
    return parsed


def _reject_constant(name):
    raise ValueError(f"forbidden JSON constant: {name}")


def validate_metadata(meta):
    """metadata JSON schema v1을 검증하고 정규화된 값을 반환한다."""
    errors = []

    def require(key):
        if key not in meta:
            errors.append(f"{key}: 필수 필드가 없다")
            return None
        return meta[key]

    # ── 고정 버전 ──
    for key in ("schema_version", "data_version"):
        value = require(key)
        if value is not None and value != 1:
            errors.append(f"{key}: MVP에서는 1로 고정이다")

    # ── 식별자 ──
    robot_id = require("robot_id")
    if robot_id is not None and not (isinstance(robot_id, str) and _ROBOT_ID_RE.match(robot_id)):
        errors.append("robot_id: 허용 문자와 길이를 벗어났다")

    session_id = require("session_id")
    if session_id is not None:
        if not (isinstance(session_id, str) and _SESSION_ID_RE.match(session_id)):
            errors.append("session_id: uint64 10진 문자열이어야 한다")
        elif int(session_id) > 2**64 - 1:
            errors.append("session_id: uint64 범위를 넘었다")

    # ── 판정 ──
    result = require("result")
    if result is not None and result not in ("SUCCESS", "FAILURE"):
        errors.append("result: SUCCESS 또는 FAILURE 여야 한다")

    # ── 출처 ──
    commit = require("interface_commit")
    if commit is not None and not (isinstance(commit, str) and _SHA1_RE.match(commit)):
        errors.append("interface_commit: 40자 hex 여야 한다")

    time_sync = require("time_sync")
    if time_sync is not None and not isinstance(time_sync, bool):
        errors.append("time_sync: boolean 이어야 한다")

    # ── digest 형식 ──
    digest = require("content_digest")
    if digest is not None:
        if not (isinstance(digest, str) and digest.startswith("sha256:")
                and _HEX64_RE.match(digest[7:])):
            errors.append("content_digest: 'sha256:' + 64자 hex 여야 한다")

    if errors:
        raise ValidationFailed(details=errors)

    # ── 시각 ──
    started_at = parse_rfc3339_utc(meta.get("started_at"), "started_at")
    ended_at = parse_rfc3339_utc(meta.get("ended_at"), "ended_at")
    # exported_at 은 선택이다. 로봇 exporter(thing_logger/export_schema.METADATA_FIELDS)는
    # 이 필드를 내보내지 않는다. content_digest 계산에서도 제외되므로(digest.py) 없어도
    # 안전하고, 있으면 형식만 검사한다.
    if meta.get("exported_at") is not None:
        parse_rfc3339_utc(meta.get("exported_at"), "exported_at")
    if ended_at <= started_at:
        raise ValidationFailed(details=["ended_at: started_at 보다 커야 한다"])

    # ── files ──
    files = meta.get("files")
    if not isinstance(files, dict):
        raise ValidationFailed(details=["files: 객체여야 한다"])
    # FR-49 는 네 파일을 요구하지만 landmark 형식이 미정이고 7.6절·NFR-27 은
    # 여전히 세 개라고 적혀 있다. 그래서 두 CSV 는 필수, landmark 는
    # landmark_contract.REQUIRED 로 정한다. 그 밖의 키는 거부한다 —
    # 알 수 없는 항목이 digest 계산에 섞이면 멱등성이 흔들린다.
    LM = landmark_contract.KIND
    required_keys = {"hand_command", "motor_status"}
    if landmark_contract.REQUIRED:
        required_keys.add(LM)
    allowed_keys = {"hand_command", "motor_status", LM}

    present = set(files.keys())
    missing = required_keys - present
    unknown = present - allowed_keys
    if missing:
        raise ValidationFailed(
            details=[f"files: 필수 항목 누락 {', '.join(sorted(missing))}"]
        )
    if unknown:
        raise ValidationFailed(
            details=[f"files: 알 수 없는 항목 {', '.join(sorted(unknown))}"]
        )

    for kind in sorted(present):
        entry = files[kind]
        if not isinstance(entry, dict):
            errors.append(f"files.{kind}: 객체여야 한다")
            continue
        ext = landmark_contract.EXTENSION if kind == LM else "csv"
        expected_name = f"session_{meta['session_id']}_{kind}.{ext}"
        if entry.get("filename") != expected_name:
            errors.append(f"files.{kind}.filename: canonical 이름이어야 한다")
        # landmark 의 개수 필드 이름이 6.5절에 json_data 로 적혀 있으나 의미가
        # 불명이라(docs/pending-decisions.md P-4) 개수는 요구하지 않는다.
        numerics = ("size_bytes",) if kind == LM else ("size_bytes", "row_count")
        for numeric in numerics:
            value = entry.get(numeric)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"files.{kind}.{numeric}: 0 이상 정수여야 한다")
        sha = entry.get("sha256")
        if not (isinstance(sha, str) and _HEX64_RE.match(sha)):
            errors.append(f"files.{kind}.sha256: 64자 hex 여야 한다")

    if errors:
        raise ValidationFailed(details=errors)

    return {
        "robot_id": meta["robot_id"],
        "session_id": meta["session_id"],
        "schema_version": 1,
        "data_version": 1,
        "started_at": started_at,
        "ended_at": ended_at,
        "result": meta["result"],
        "interface_commit": meta["interface_commit"],
        "time_sync": meta["time_sync"],
        "content_digest": meta["content_digest"],
        "files": files,
    }


# ── CSV ──

def validate_csv(path, dataset, session_id, expected_row_count):
    """CSV 파일의 header·행 수·session_id·timestamp 비감소·금지값을 검증한다.

    스트리밍으로 읽어 대용량 파일을 메모리에 올리지 않는다.
    """
    expected_header = CSV_HEADERS[dataset]
    errors = []

    with open(path, "rb") as raw:
        head = raw.read(3)
        if head == b"\xef\xbb\xbf":
            raise ValidationFailed(details=[f"{dataset}: UTF-8 BOM은 허용하지 않는다"])

    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            raise ValidationFailed(details=[f"{dataset}: header 행이 없다"])
        except UnicodeDecodeError:
            raise ValidationFailed(details=[f"{dataset}: UTF-8로 디코딩할 수 없다"])

        if header != expected_header:
            raise ValidationFailed(
                details=[f"{dataset}: header가 규격과 다르다 (컬럼 {len(expected_header)}개)"]
            )

        sid_index = expected_header.index("session_id")
        sec_index = expected_header.index("stamp_sec")
        nsec_index = expected_header.index("stamp_nanosec")
        width = len(expected_header)

        row_count = 0
        previous_stamp = None

        try:
            for row in reader:
                row_count += 1
                if len(row) != width:
                    errors.append(f"{dataset}: {row_count}행의 컬럼 수가 다르다")
                    break

                if row[sid_index] != session_id:
                    errors.append(f"{dataset}: {row_count}행의 session_id가 metadata와 다르다")
                    break

                stamp = _parse_stamp(row, sec_index, nsec_index, dataset, row_count, errors)
                if stamp is None:
                    break
                if previous_stamp is not None and stamp < previous_stamp:
                    errors.append(f"{dataset}: {row_count}행의 timestamp가 감소했다")
                    break
                previous_stamp = stamp

                bad = _find_forbidden_numeric(row)
                if bad is not None:
                    errors.append(f"{dataset}: {row_count}행에 NaN·Infinity가 있다")
                    break
        except UnicodeDecodeError:
            errors.append(f"{dataset}: UTF-8로 디코딩할 수 없다")
        except csv.Error as exc:
            errors.append(f"{dataset}: CSV 파싱 실패")

    if errors:
        raise ValidationFailed(details=errors)

    if row_count != expected_row_count:
        raise ValidationFailed(
            details=[f"{dataset}: 행 수가 metadata와 다르다 "
                     f"(실제 {row_count}, metadata {expected_row_count})"]
        )

    return row_count


def _parse_stamp(row, sec_index, nsec_index, dataset, row_number, errors):
    """(stamp_sec, stamp_nanosec) 정수 튜플. 비교용이므로 빈 값을 허용하지 않는다."""
    try:
        sec = int(row[sec_index])
        nsec = int(row[nsec_index])
    except (TypeError, ValueError):
        errors.append(f"{dataset}: {row_number}행의 stamp_sec·stamp_nanosec가 정수가 아니다")
        return None
    if sec < 0 or not (0 <= nsec < 1_000_000_000):
        errors.append(f"{dataset}: {row_number}행의 timestamp 범위가 잘못됐다")
        return None
    return (sec, nsec)


def _find_forbidden_numeric(row):
    """NaN·Infinity 표기를 찾는다. 읽기 실패 숫자는 빈 칸이어야 한다."""
    for index, cell in enumerate(row):
        token = cell.strip().lower()
        if token in _FORBIDDEN_NUMERICS:
            return index
    return None
