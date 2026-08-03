# backend/apps/read_views.py
"""
공개 GET 조회 API.

    GET /api/v1/sessions
    GET /api/v1/sessions/{session_id}
    GET /api/v1/sessions/{session_id}/data?dataset=&cursor=&limit=&offset=
    GET /api/v1/sessions/{session_id}/series?type=&cursor=&limit=&offset=   (별칭)
    GET /api/v1/sessions/{session_id}/download/{file_kind}

요구사항 명세서 FR-47 / FR-49 / NFR-25, 6.5절.

시계열 엔드포인트는 두 이름을 모두 받는다.
명세서 6.5절은 `/data?dataset=`, 스프린트 티켓은 `/series?type=` 으로 적혀 있어
어느 쪽으로 호출해도 같은 응답을 주도록 별칭을 둔다. 응답 본문은 동일하다.
파라미터도 `dataset` 과 `type`, `cursor` 와 `offset` 을 모두 허용한다.

    GET은 로그인 없이 READY만 읽기 전용으로 제공한다.
    목록 기본 20개·최대 100개, `started_at DESC, session_id DESC`
    exact Session ID 검색은 Must
    data 기본 1000행·최대 5000행, timestamp 오름차순; 결측 숫자는 JSON `null`
    file_kind는 metadata|hand_command|motor_status enum이며 경로 입력을 받지 않는다.
    파일 누락·hash 불일치는 정상 다운로드로 제공하지 않는다.
"""
import logging

from django.db.models import Q
from django.http import FileResponse
from rest_framework.response import Response
from rest_framework.views import APIView

from . import storage
from .digest import sha256_file
from .errors import MalformedRequest, NotFound
from .models import Session
from .serializers_v1 import (
    dataset_columns,
    decode_cursor,
    encode_cursor,
    read_dataset_rows,
    session_detail,
    session_list_item,
)
from .throttles import PublicReadThrottle

logger = logging.getLogger(__name__)

LIST_DEFAULT_LIMIT = 20
LIST_MAX_LIMIT = 100
DATA_DEFAULT_LIMIT = 1000
DATA_MAX_LIMIT = 5000

DATASETS = ("hand_command", "motor_status")


class PublicReadView(APIView):
    """공개 조회 공통. 인증 없이 읽기만 허용하고 IP 당 120/min 으로 제한한다."""

    authentication_classes = []
    permission_classes = []
    throttle_classes = [PublicReadThrottle]

    @staticmethod
    def ready_queryset():
        """[FR-47] 공개 API 는 READY 만 읽는다. STAGING·FAILED 는 존재하지 않는 것처럼 취급."""
        return Session.objects.filter(status=Session.Status.READY)

    def get_ready_session(self, session_id):
        try:
            storage.validate_session_id(session_id)
        except storage.UnsafeIdentifier:
            # 경로 주입 시도도 단순 404 로 처리해 내부 규칙을 노출하지 않는다
            raise NotFound()
        session = self.ready_queryset().filter(session_id=session_id).first()
        if session is None:
            raise NotFound()
        return session

    @staticmethod
    def parse_limit(request, default, maximum):
        raw = request.query_params.get("limit")
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError:
            raise MalformedRequest(details=["limit: 정수여야 한다"])
        if value < 1:
            raise MalformedRequest(details=["limit: 1 이상이어야 한다"])
        return min(value, maximum)

    @staticmethod
    def parse_offset(request):
        """offset 파라미터. cursor 와 함께 쓰이면 cursor 가 우선한다."""
        raw = request.query_params.get("offset")
        if raw is None:
            return None
        try:
            value = int(raw)
        except ValueError:
            raise MalformedRequest(details=["offset: 정수여야 한다"])
        if value < 0:
            raise MalformedRequest(details=["offset: 0 이상이어야 한다"])
        return value


class SessionListView(PublicReadView):
    """GET /api/v1/sessions"""

    def get(self, request):
        limit = self.parse_limit(request, LIST_DEFAULT_LIMIT, LIST_MAX_LIMIT)
        queryset = self.ready_queryset()

        # exact Session ID 검색 (Must)
        session_id = request.query_params.get("session_id")
        if session_id:
            try:
                storage.validate_session_id(session_id)
            except storage.UnsafeIdentifier:
                # 형식이 틀리면 결과 없음으로 처리한다
                return Response({"items": [], "next_cursor": None})
            queryset = queryset.filter(session_id=session_id)

        # robot_id 필터 (Should)
        robot_id = request.query_params.get("robot_id")
        if robot_id:
            queryset = queryset.filter(robot_id=robot_id)

        # result 필터 (Should)
        result = request.query_params.get("result")
        if result:
            if result not in (Session.Result.SUCCESS, Session.Result.FAILURE):
                raise MalformedRequest(details=["result: SUCCESS 또는 FAILURE 여야 한다"])
            queryset = queryset.filter(result=result)

        # offset pagination. cursor 가 있으면 cursor 가 우선한다.
        offset = self.parse_offset(request)

        # cursor pagination (Should). 정렬은 started_at DESC, session_id DESC
        cursor = decode_cursor(request.query_params.get("cursor"))
        if cursor:
            started_at = cursor.get("started_at")
            last_session_id = cursor.get("session_id")
            if not started_at or not last_session_id:
                raise MalformedRequest(details=["cursor: 형식이 올바르지 않다"])
            queryset = queryset.filter(
                Q(started_at__lt=started_at)
                | Q(started_at=started_at, session_id__lt=last_session_id)
            )

        # 다음 페이지 존재 여부를 알기 위해 한 건 더 읽는다
        ordered = queryset.order_by("-started_at", "-session_id")
        start = offset if (cursor is None and offset is not None) else 0
        rows = list(ordered[start: start + limit + 1])
        has_more = len(rows) > limit
        rows = rows[:limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor({
                "started_at": last.started_at.isoformat(),
                "session_id": last.session_id,
            })

        next_offset = start + limit if has_more else None

        return Response({
            "items": [session_list_item(s) for s in rows],
            "next_cursor": next_cursor,
            # offset 으로 호출한 클라이언트를 위한 값. cursor 사용 시 null 이다.
            "next_offset": next_offset if (cursor is None and offset is not None) else None,
        })


class SessionDetailView(PublicReadView):
    """GET /api/v1/sessions/{session_id}"""

    def get(self, request, session_id):
        return Response(session_detail(self.get_ready_session(session_id)))


class SessionDataView(PublicReadView):
    """GET /api/v1/sessions/{session_id}/data?dataset=&cursor=&limit="""

    def get(self, request, session_id):
        session = self.get_ready_session(session_id)

        # 명세서는 dataset, 스프린트 티켓은 type. 둘 다 받는다.
        dataset = request.query_params.get("dataset") or request.query_params.get("type")
        if dataset not in DATASETS:
            raise MalformedRequest(
                details=[f"dataset(또는 type): {' 또는 '.join(DATASETS)} 여야 한다"]
            )

        limit = self.parse_limit(request, DATA_DEFAULT_LIMIT, DATA_MAX_LIMIT)

        cursor = decode_cursor(request.query_params.get("cursor"))
        if cursor:
            offset = cursor.get("offset")
            if not isinstance(offset, int) or offset < 0:
                raise MalformedRequest(details=["cursor: 형식이 올바르지 않다"])
        else:
            offset = self.parse_offset(request) or 0

        path = storage.final_path(session.robot_id, session.session_id, dataset)
        if not path.exists():
            logger.error("READY 세션의 파일이 없다: %s/%s %s",
                         session.robot_id, session.session_id, dataset)
            raise NotFound()

        rows, next_offset = read_dataset_rows(path, dataset, offset, limit)

        return Response({
            "session_id": session.session_id,
            "dataset": dataset,
            # 티켓이 type 으로 부르는 값. dataset 과 항상 같다.
            "type": dataset,
            "columns": dataset_columns(dataset),
            "rows": rows,
            "next_cursor": encode_cursor({"offset": next_offset}) if next_offset else None,
            "next_offset": next_offset,
        })


class SessionDownloadView(PublicReadView):
    """GET /api/v1/sessions/{session_id}/download/{file_kind}

    [NFR-25] 저장된 SHA-256 과 실제 파일이 일치하는 READY 만 제공한다.
    반복 GET 은 원본을 바꾸지 않는다.
    """

    def get(self, request, session_id, file_kind):
        session = self.get_ready_session(session_id)

        try:
            storage.validate_file_kind(file_kind)
        except storage.UnsafeIdentifier:
            raise NotFound()

        path = storage.final_path(session.robot_id, session.session_id, file_kind)
        if not path.exists():
            logger.error("READY 세션의 파일이 없다: %s/%s %s",
                         session.robot_id, session.session_id, file_kind)
            raise NotFound()

        if not self._integrity_ok(session, path, file_kind):
            # 무결성 실패는 정상 다운로드로 제공하지 않는다
            raise NotFound()

        response = FileResponse(
            open(path, "rb"),
            as_attachment=True,
            filename=storage.canonical_filename(session.session_id, file_kind),
        )
        response["Cache-Control"] = "no-transform"
        return response

    @staticmethod
    def _integrity_ok(session, path, file_kind):
        """크기를 먼저 보고, 통과하면 SHA-256 을 검증한다.

        크기 비교가 훨씬 싸므로 먼저 걸러낸다. hash 검증은 파일을 한 번 더 읽지만
        명세서가 '일치하는 READY 만 제공'을 요구하므로 생략하지 않는다.
        """
        expected_size = session.file_sizes.get(file_kind)
        if expected_size is not None and path.stat().st_size != expected_size:
            logger.error("다운로드 크기 불일치: %s/%s %s",
                         session.robot_id, session.session_id, file_kind)
            return False

        expected_hash = session.file_hashes.get(file_kind)
        if not expected_hash:
            logger.error("저장된 hash 가 없다: %s/%s %s",
                         session.robot_id, session.session_id, file_kind)
            return False

        if sha256_file(path).lower() != expected_hash.lower():
            logger.error("다운로드 hash 불일치: %s/%s %s",
                         session.robot_id, session.session_id, file_kind)
            return False

        return True
