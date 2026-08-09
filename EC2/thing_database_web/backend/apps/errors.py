# backend/apps/errors.py
"""
오류 응답 envelope.

요구사항 명세서 6.5절:
    {"error":{"code":"SESSION_CONTENT_CONFLICT","message":"...","details":[]},
     "request_id":"request-uuid"}

    400 malformed, 401 인증, 404 없음, 405 공개 쓰기, 409 충돌, 413 크기,
    415 media type, 422 schema·값, 429 rate limit, 500 내부, 503 DB·디스크 불가
    를 사용한다. 경로·토큰·stack trace를 응답에 넣지 않는다.

마지막 문장이 이 모듈의 존재 이유다. 검증 실패 사유는 클라이언트가 고칠 수 있을
만큼만 알려주고, 서버 경로·token·traceback은 로그에만 남긴다.
`details`에 담는 문자열도 필드명과 기대값 수준으로 제한한다.
"""
import logging
import uuid

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """명세서 오류 코드를 갖는 API 예외.

    message는 그대로 클라이언트에 전달되므로 경로·비밀정보를 넣지 않는다.
    내부 원인은 log_detail로 넘겨 서버 로그에만 기록한다.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    code = "BAD_REQUEST"
    message = "The request could not be processed."

    def __init__(self, message=None, details=None, log_detail=None):
        self.message = message or self.message
        self.details = list(details or [])
        self.log_detail = log_detail
        super().__init__(self.message)


# ── 400 malformed ──
class MalformedRequest(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "MALFORMED_REQUEST"
    message = "The request body is malformed."


class UnexpectedPart(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "UNEXPECTED_PART"
    message = "The request contains parts that are not accepted."


# ── 401 인증 ──
class Unauthorized(ApiError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHORIZED"
    message = "A valid device token is required."


class RobotMismatch(ApiError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "ROBOT_MISMATCH"
    message = "The token is not authorized for this robot."


# ── 404 없음 ──
class NotFound(ApiError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"
    message = "The requested resource does not exist."


# ── 405 공개 쓰기 ──
class MethodNotAllowed(ApiError):
    status_code = status.HTTP_405_METHOD_NOT_ALLOWED
    code = "METHOD_NOT_ALLOWED"
    message = "This endpoint is read-only."


# ── 409 충돌 ──
class SessionContentConflict(ApiError):
    status_code = status.HTTP_409_CONFLICT
    code = "SESSION_CONTENT_CONFLICT"
    message = "The session already exists with different content."


# ── 413 크기 ──
class PayloadTooLarge(ApiError):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    code = "PAYLOAD_TOO_LARGE"
    message = "The upload exceeds the allowed size."


# ── 415 media type ──
class UnsupportedMediaType(ApiError):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    code = "UNSUPPORTED_MEDIA_TYPE"
    message = "A part has an unsupported content type."


# ── 422 schema·값 ──
class ValidationFailed(ApiError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "VALIDATION_FAILED"
    message = "The uploaded content failed validation."


class DigestMismatch(ApiError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "DIGEST_MISMATCH"
    message = "The recomputed content digest does not match the request."


# ── 503 DB·디스크 불가 ──
class ServiceUnavailable(ApiError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "SERVICE_UNAVAILABLE"
    message = "A required resource is unavailable."


def new_request_id():
    return str(uuid.uuid4())


def envelope(code, message, details=None, request_id=None):
    """명세서 형식의 오류 본문을 만든다."""
    return {
        "error": {
            "code": code,
            "message": message,
            "details": list(details or []),
        },
        "request_id": request_id or new_request_id(),
    }


def error_response(exc, request=None):
    """ApiError를 Response로 변환한다."""
    request_id = getattr(request, "api_request_id", None) or new_request_id()
    if exc.log_detail:
        logger.warning("api error %s [%s]: %s", exc.code, request_id, exc.log_detail)
    return Response(
        envelope(exc.code, exc.message, exc.details, request_id),
        status=exc.status_code,
    )


#: DRF 기본 예외를 명세서 코드로 매핑한다
_DRF_CODE_MAP = {
    status.HTTP_400_BAD_REQUEST: "MALFORMED_REQUEST",
    status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
    status.HTTP_403_FORBIDDEN: "UNAUTHORIZED",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
    status.HTTP_406_NOT_ACCEPTABLE: "NOT_ACCEPTABLE",
    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "PAYLOAD_TOO_LARGE",
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "UNSUPPORTED_MEDIA_TYPE",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "VALIDATION_FAILED",
    status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
    status.HTTP_503_SERVICE_UNAVAILABLE: "SERVICE_UNAVAILABLE",
}

#: 상태 코드별 안전한 기본 메시지. DRF 기본 문구가 내부 정보를 담을 수 있어 대체한다.
_DEFAULT_MESSAGES = {
    status.HTTP_400_BAD_REQUEST: "The request body is malformed.",
    status.HTTP_401_UNAUTHORIZED: "A valid device token is required.",
    status.HTTP_403_FORBIDDEN: "A valid device token is required.",
    status.HTTP_404_NOT_FOUND: "The requested resource does not exist.",
    status.HTTP_405_METHOD_NOT_ALLOWED: "This endpoint is read-only.",
    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "The upload exceeds the allowed size.",
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "A part has an unsupported content type.",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "The uploaded content failed validation.",
    status.HTTP_429_TOO_MANY_REQUESTS: "Too many requests.",
    status.HTTP_503_SERVICE_UNAVAILABLE: "A required resource is unavailable.",
}


def api_exception_handler(exc, context):
    """DRF EXCEPTION_HANDLER. 모든 오류를 명세서 envelope으로 통일한다."""
    request = context.get("request")
    request_id = getattr(request, "api_request_id", None) or new_request_id()

    if isinstance(exc, ApiError):
        return error_response(exc, request)

    response = drf_exception_handler(exc, context)
    if response is None:
        # 처리되지 않은 예외. traceback은 로그에만 남기고 응답은 최소 정보만.
        logger.exception("unhandled api exception [%s]", request_id)
        return Response(
            envelope("INTERNAL_ERROR", "An internal error occurred.", request_id=request_id),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    code = _DRF_CODE_MAP.get(response.status_code, "ERROR")
    message = _DEFAULT_MESSAGES.get(response.status_code, "The request could not be processed.")
    new_body = envelope(code, message, request_id=request_id)

    # 429는 Retry-After 를 유지해야 한다 (명세서 6.5절)
    retry_after = response.headers.get("Retry-After") if hasattr(response, "headers") else None
    response.data = new_body
    if retry_after:
        response["Retry-After"] = retry_after
    return response
