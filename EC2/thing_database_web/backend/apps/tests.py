# backend/apps/tests.py
"""
공통 회귀 테스트: 업로드 크기 상한과 /health.

세션 데이터 계약 테스트는 아래 모듈에 있다.
    tests_session.py   Session 모델, 저장 레이아웃, content_digest
    tests_upload.py    POST /api/v1/uploads/sessions
    tests_read.py      공개 GET 조회 4종과 rate limit

실행:
    cd thing_database_web/backend
    source .venv/bin/activate
    python manage.py test apps
"""
import shutil
import tempfile

from django.conf import settings
from django.test import TestCase, override_settings

MiB = 1024 * 1024
TEMP_DATA = tempfile.mkdtemp(prefix="test-common-")


class UploadLimitTests(TestCase):
    """[FR-51] 업로드 크기 상한 체계의 Django 쪽.

    Nginx 쪽은 deploy/nginx_thing_database_web.conf 에 있고 두 값은 함께 움직여야
    한다. 6.5절의 고정 숫자(85MiB)는 landmark 를 뺀 시절의 값이라 숫자를 직접
    비교하지 않고 part 상한 합계를 담을 수 있는지만 본다.
    docs/pending-decisions.md P-2 참조.
    """

    def test_django_request_limit_holds_every_part(self):
        from apps.validators import TOTAL_MAX_BYTES

        self.assertGreaterEqual(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, TOTAL_MAX_BYTES)

    def test_large_body_spools_to_disk(self):
        """FILE_UPLOAD_MAX_MEMORY_SIZE 를 넘는 파일은 메모리에 다 올리지 않는다."""
        self.assertLess(
            settings.FILE_UPLOAD_MAX_MEMORY_SIZE,
            settings.DATA_UPLOAD_MAX_MEMORY_SIZE,
        )

    def test_nginx_conf_matches_part_limits(self):
        """Nginx client_max_body_size 가 part 상한 합계를 담아야 한다.

        Nginx 는 설정 파일이라 import 할 수 없다. 그래서 apps/limits.py 를 고치면
        이 시험이 실패하면서 conf 에 넣을 값을 알려 준다. 이 고리를 닫아 두지
        않으면 큰 업로드가 Django 에 닿기 전에 Nginx 에서 413 으로 끊기고,
        원인을 찾기 어렵다.
        """
        from pathlib import Path
        import re

        from apps import limits

        conf = (
            Path(__file__).resolve().parents[2]
            / "deploy" / "nginx_thing_database_web.conf"
        )
        text = conf.read_text(encoding="utf-8")
        found = re.search(r"client_max_body_size\s+(\d+)M\s*;", text)
        self.assertIsNotNone(found, "client_max_body_size 지시자를 찾지 못했다")

        declared_bytes = int(found.group(1)) * limits.MiB
        self.assertGreaterEqual(
            declared_bytes,
            limits.REQUEST_MAX_BYTES,
            f"nginx conf 의 client_max_body_size 를 "
            f"{limits.nginx_client_max_body_size()} 이상으로 고치세요",
        )

    def test_part_limits_sum_matches_spec(self):
        """part 상한 합계가 Django 요청 상한 안에 들어가야 한다.

        이 관계가 깨지면 큰 업로드가 Django 에 닿기 전에 거부된다.
        V7.1 §6.5 의 "합계 200.25MiB"(landmark 포함)는 값이 바뀔 수 있으므로 숫자를
        직접 비교하지 않고 관계만 검사한다.
        """
        from apps.validators import PART_MAX_BYTES, TOTAL_MAX_BYTES

        self.assertEqual(sum(PART_MAX_BYTES.values()), TOTAL_MAX_BYTES)
        self.assertLess(TOTAL_MAX_BYTES, settings.DATA_UPLOAD_MAX_MEMORY_SIZE)


@override_settings(EC2_DATA_DIR=TEMP_DATA, SECURE_SSL_REDIRECT=False)
class HealthTests(TestCase):
    """[FR-52] /health 가 의존 자원을 확인하고 경로·비밀정보를 노출하지 않는지 검증."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_DATA, ignore_errors=True)
        super().tearDownClass()

    def test_returns_200_when_healthy(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["checks"]["database"])
        self.assertTrue(body["checks"]["data_dir"])

    def test_does_not_leak_paths_or_secrets(self):
        """명세서 FR-52: 경로·비밀정보를 노출하지 않는다."""
        raw = self.client.get("/health").content.decode()
        for leak in (settings.EC2_DATA_DIR, str(settings.BASE_DIR),
                     settings.SECRET_KEY, "sqlite", "Traceback", "/home/"):
            self.assertNotIn(leak, raw)

    def test_no_store_cache_header(self):
        resp = self.client.get("/health")
        self.assertEqual(resp["Cache-Control"], "no-store")

    def test_returns_503_when_database_unavailable(self):
        """SQLite 접근이 안 되면 503 (명세서 6.5절 503 DB·디스크 불가)."""
        from unittest.mock import patch

        with patch("apps.health.HealthView._check_database", return_value=False):
            resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["status"], "unavailable")
        self.assertFalse(resp.json()["checks"]["database"])


# SECURE_SSL_REDIRECT=True 이면 URL 해석 전에 301 이 나가므로 404 를 확인할 수 없다.
@override_settings(SECURE_SSL_REDIRECT=False)
class LegacyRemovalTests(TestCase):
    """[FR-50] 레거시 무인증 쓰기 경로가 완전히 사라졌는지 검증.

    이 테스트가 실패하면 조회 전용 보안 경계가 다시 뚫린 것이다.
    """

    LEGACY_PATHS = (
        "/api/motor-data/upload/",
        "/api/motor-data/files/",
        "/api/motor-data/download/1/",
    )

    def test_legacy_endpoints_are_gone(self):
        for path in self.LEGACY_PATHS:
            self.assertEqual(self.client.get(path).status_code, 404, path)
            self.assertEqual(self.client.post(path, {}).status_code, 404, path)

    def test_legacy_models_are_removed(self):
        from django.apps import apps as django_apps

        names = {m.__name__ for m in django_apps.get_app_config("apps").get_models()}
        self.assertEqual(names, {"Session"})

    def test_admin_is_not_publicly_proxied(self):
        """nginx 설정에서 /admin/ 프록시가 제거되었는지 확인한다.

        Django 자체에는 admin 이 남아 있어 SSH 터널로 접근할 수 있다.
        공개 노출 여부는 배포 설정이 결정하므로 그 파일을 검사한다.
        """
        import pathlib

        conf = (
            pathlib.Path(settings.BASE_DIR).parent
            / "deploy"
            / "nginx_thing_database_web.conf"
        )
        if not conf.exists():
            self.skipTest("배포 설정 파일이 없는 환경")
        text = conf.read_text(encoding="utf-8")
        self.assertNotIn("proxy_pass http://127.0.0.1:8000/admin/", text)
