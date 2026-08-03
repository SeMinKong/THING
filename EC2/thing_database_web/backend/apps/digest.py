# backend/apps/digest.py
"""
content_digest 계산.

요구사항 명세서 6.5절 metadata JSON schema v1:
    `content_digest`는 `exported_at`과 `content_digest` 자신을 제외한 metadata의
    의미 필드 전체를 key 오름차순·공백 없는 UTF-8 canonical JSON으로 직렬화한 뒤
    SHA-256으로 계산한다. 두 CSV의 filename·size_bytes·row_count·sha256은
    계산 대상에 포함한다.

이 규칙은 **로봇 측 exporter와 바이트 단위로 동일해야** 한다. 한 쪽이라도 직렬화가
다르면 서버가 재계산한 digest가 요청값과 어긋나 모든 업로드가 409로 거부된다.
따라서 아래 네 가지 결정을 양측이 같이 지켜야 하며, TEST_VECTOR로 교차 검증한다.

  1. key 오름차순      json.dumps(sort_keys=True) — 중첩 dict에도 재귀 적용된다
  2. 공백 없음         separators=(',', ':')
  3. UTF-8             ensure_ascii=False 로 직렬화한 뒤 .encode('utf-8')
                       (ensure_ascii=True 면 비ASCII가 \\uXXXX 로 escape되어 달라진다)
  4. 제외 필드         exported_at, content_digest — 최상위에서만 제거한다

`exported_at`을 제외하므로 같은 rosbag2를 수동 재생성해도 digest가 같다.
이것이 멱등성(NFR-26)의 기준이다.
"""
import hashlib
import json

#: content_digest 계산에서 제외하는 최상위 키
EXCLUDED_KEYS = ("exported_at", "content_digest")

#: digest 문자열 접두사
DIGEST_PREFIX = "sha256:"


def canonical_json(metadata):
    """metadata를 canonical JSON 바이트열로 직렬화한다.

    exported_at·content_digest를 제외하고, key 오름차순·공백 없는 UTF-8로 만든다.
    입력 dict는 변경하지 않는다.
    """
    payload = {k: v for k, v in metadata.items() if k not in EXCLUDED_KEYS}
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,          # NaN·Infinity 금지 (명세서 CSV 규칙과 동일 취지)
    ).encode("utf-8")


def compute_content_digest(metadata):
    """metadata로부터 "sha256:<hex64>" 형식의 content_digest를 계산한다."""
    return DIGEST_PREFIX + hashlib.sha256(canonical_json(metadata)).hexdigest()


def digests_match(expected, metadata):
    """요청이 주장하는 digest와 서버 재계산 결과가 일치하는지 대소문자 무시 비교."""
    if not expected:
        return False
    return compute_content_digest(metadata).lower() == str(expected).strip().lower()


def sha256_file(path, chunk_size=1024 * 1024):
    """파일의 SHA-256 hex를 스트리밍으로 계산한다. 대용량 CSV를 메모리에 올리지 않는다."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data):
    """바이트열의 SHA-256 hex."""
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# 교차 검증용 테스트 벡터
#
# 로봇 측 exporter 구현자는 아래 metadata로 EXPECTED_DIGEST가 나오는지 확인한다.
# 값이 다르면 직렬화 규칙(위 1~4번)이 어긋난 것이다.
#
#   python -c "from apps.digest import verify_test_vector; verify_test_vector()"
# ---------------------------------------------------------------------------

TEST_VECTOR = {
    "schema_version": 1,
    "data_version": 1,
    "robot_id": "THING-001",
    "session_id": "123456789012345678",
    "started_at": "2026-07-29T00:00:00.000Z",
    "ended_at": "2026-07-29T00:01:00.000Z",
    "exported_at": "2026-07-29T00:01:05.000Z",   # 제외 대상
    "result": "SUCCESS",
    "interface_commit": "70dfdab8d555dfbfdd471c5acca4f30a8a8fc3ec",
    "time_sync": True,
    "content_digest": "sha256:" + "0" * 64,       # 제외 대상
    "files": {
        "hand_command": {
            "filename": "session_123456789012345678_hand_command.csv",
            "size_bytes": 1234,
            "row_count": 1200,
            "sha256": "a" * 64,
        },
        "motor_status": {
            "filename": "session_123456789012345678_motor_status.csv",
            "size_bytes": 5678,
            "row_count": 4200,
            "sha256": "b" * 64,
        },
    },
}


def verify_test_vector():
    """테스트 벡터의 canonical JSON과 digest를 출력한다."""
    blob = canonical_json(TEST_VECTOR)
    digest = compute_content_digest(TEST_VECTOR)
    print("canonical JSON :", blob.decode("utf-8"))
    print("byte length    :", len(blob))
    print("content_digest :", digest)
    return digest
