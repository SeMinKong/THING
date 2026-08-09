# backend/apps/health.py
"""
/health 엔드포인트.

요구사항 명세서 FR-52:
    `/health`는 Django, SQLite 접근과 데이터 디렉터리 쓰기 가능 시 200을 반환하되
    경로·비밀정보를 노출하지 않는다.

따라서 응답에는 검사 항목의 논리적 이름과 boolean만 담는다.
데이터 경로, DB 경로, 예외 메시지, stack trace는 응답에 넣지 않고 서버 로그에만 남긴다.
"""
import logging
import os
import uuid
from pathlib import Path

from django.conf import settings
from django.db import connections
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class HealthView(APIView):
    """의존 자원 상태를 확인한다. 인증 없이 접근 가능한 읽기 전용 엔드포인트."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        checks = {
            "database": self._check_database(),
            "data_dir": self._check_data_dir(),
        }
        healthy = all(checks.values())

        response = Response(
            {"status": "ok" if healthy else "unavailable", "checks": checks},
            status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        # 상태 점검 결과는 캐시되면 의미가 없다.
        response["Cache-Control"] = "no-store"
        return response

    @staticmethod
    def _check_database():
        """SQLite에 실제 쿼리를 보내 접근 가능한지 확인한다."""
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
        except Exception:
            # 경로·자격증명이 섞일 수 있으므로 응답이 아니라 로그로만 남긴다.
            logger.exception("health: database check failed")
            return False

    @staticmethod
    def _check_data_dir():
        """세션 데이터 루트에 실제로 파일을 써 보고 지운다.

        존재 확인만으로는 권한 문제를 잡지 못하므로 실제 write/read를 수행한다.
        대상은 EC2_DATA_DIR/staging 이다. systemd StateDirectory=thing-data 가
        루트를 생성·소유하므로 mkdir 는 하위 디렉터리에만 필요하다.
        """
        probe = None
        try:
            data_dir = Path(settings.EC2_DATA_DIR) / "staging"
            data_dir.mkdir(parents=True, exist_ok=True)
            probe = data_dir / f".health-{uuid.uuid4().hex}"
            probe.write_bytes(b"ok")
            return probe.read_bytes() == b"ok"
        except Exception:
            logger.exception("health: data directory check failed")
            return False
        finally:
            if probe is not None:
                try:
                    os.unlink(probe)
                except OSError:
                    pass
