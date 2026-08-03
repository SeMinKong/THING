# backend/apps/device_auth.py
"""
장치 token 인증.

요구사항 명세서 FR-51:
    장치 token은 최소 32바이트 CSPRNG로 생성하고 EC2에는 hash만 비교 가능한
    형태로 보관한다. 유출 시 교체한다.
    ... token의 robot과 `robot_id`가 일치해야 한다.

따라서 서버는 **평문 token을 저장하지 않는다.** `.env`에 SHA-256 hex만 둔다.

설정 형식 (robot_id:sha256hex, 쉼표로 여러 개)

    DEVICE_TOKENS=THING-001:9f2b...64자hex

token 생성 절차

    # 로봇에 넣을 평문 (32바이트 CSPRNG)
    python3 -c "import secrets; print(secrets.token_urlsafe(32))"

    # 서버 .env 에 넣을 hash
    python3 -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <평문>

평문은 로봇에만, hash는 서버에만 둔다. 서버가 유출되어도 token을 복원할 수 없다.
비교는 hmac.compare_digest로 상수시간 처리해 타이밍 공격을 막는다.
"""
import hashlib
import hmac
import logging
import re

from django.conf import settings

from .errors import Unauthorized

logger = logging.getLogger(__name__)

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_BEARER = re.compile(r"^Bearer\s+(\S+)$")


def _parse_config(raw):
    """DEVICE_TOKENS 문자열을 {sha256hex: robot_id} 로 파싱한다.

    형식이 잘못된 항목은 건너뛰고 경고만 남긴다. 시작 자체를 막으면 설정 오타
    하나로 서비스 전체가 내려가므로, 해당 token만 무효가 되게 한다.
    """
    mapping = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        robot_id, _, token_hash = entry.rpartition(":")
        robot_id = robot_id.strip()
        token_hash = token_hash.strip().lower()
        if not robot_id or not _HEX64.match(token_hash):
            logger.warning("DEVICE_TOKENS: 형식이 잘못된 항목을 무시했다 (robot_id=%r)", robot_id)
            continue
        mapping[token_hash] = robot_id
    return mapping


def configured_tokens():
    """설정된 {hash: robot_id} 매핑. 매 호출마다 읽어 override_settings로 테스트 가능."""
    return _parse_config(getattr(settings, "DEVICE_TOKENS", ""))


def hash_token(plaintext):
    """평문 token의 SHA-256 hex."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def extract_bearer(request):
    """Authorization 헤더에서 Bearer token 평문을 꺼낸다."""
    header = request.META.get("HTTP_AUTHORIZATION", "")
    match = _BEARER.match(header.strip())
    return match.group(1) if match else None


def authenticate_device(request):
    """요청을 인증하고 robot_id를 반환한다. 실패하면 Unauthorized를 올린다.

    로그에 token 평문이나 hash를 남기지 않는다.
    """
    tokens = configured_tokens()
    if not tokens:
        # 설정 누락은 서버 문제지만, 응답으로는 인증 실패와 구분하지 않는다.
        raise Unauthorized(log_detail="DEVICE_TOKENS 가 설정되지 않았다")

    plaintext = extract_bearer(request)
    if not plaintext:
        raise Unauthorized(log_detail="Authorization Bearer 헤더 없음")

    candidate = hash_token(plaintext)

    # 상수시간 비교. dict 조회는 빠르지만 타이밍 차이를 만들 수 있어 전수 비교한다.
    matched_robot = None
    for known_hash, robot_id in tokens.items():
        if hmac.compare_digest(candidate, known_hash):
            matched_robot = robot_id

    if matched_robot is None:
        raise Unauthorized(log_detail="일치하는 장치 token 없음")

    return matched_robot
