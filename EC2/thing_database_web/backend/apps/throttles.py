# backend/apps/throttles.py
"""
Rate limit.

요구사항 명세서 6.5절:
    public GET은 IP당 분당 120회, upload POST는 token당 분당 10회를 기본으로
    제한하고 초과 시 `429`와 `Retry-After`를 반환한다.

캐시 백엔드
    gunicorn 워커가 3개이므로 LocMemCache 를 쓰면 워커마다 독립 카운터가 생겨
    실효 한도가 약 3배가 된다. 명세서가 숫자를 명시했으므로 DatabaseCache 를
    사용해 워커 간 카운터를 공유한다. 새 인프라 없이 SQLite 로 처리된다.

    settings.CACHES['default'] = DatabaseCache('thing_cache_table')
    배포 시 `python manage.py createcachetable` 을 한 번 실행한다.

Retry-After
    DRF SimpleRateThrottle 은 wait() 를 제공하고 Throttled 예외가 헤더를 채운다.
    errors.api_exception_handler 가 envelope 으로 바꿀 때 Retry-After 를 보존한다.
"""
from rest_framework.throttling import SimpleRateThrottle

from .device_auth import extract_bearer, hash_token


class PublicReadThrottle(SimpleRateThrottle):
    """공개 GET: IP 당 120/min."""

    scope = "public"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class DeviceUploadThrottle(SimpleRateThrottle):
    """업로드 POST: token 당 10/min.

    token 평문을 캐시 키에 넣지 않는다. hash 를 쓰므로 캐시가 유출되어도
    token 을 복원할 수 없다. token 이 없으면 인증 단계에서 401 이 나므로
    여기서는 IP 로 대체해 무인증 요청의 반복도 제한한다.
    """

    scope = "upload"

    def get_cache_key(self, request, view):
        plaintext = extract_bearer(request)
        ident = hash_token(plaintext) if plaintext else f"anon-{self.get_ident(request)}"
        return self.cache_format % {"scope": self.scope, "ident": ident}
