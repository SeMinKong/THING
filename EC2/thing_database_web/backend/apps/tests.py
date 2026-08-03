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
    """[FR-51] 업로드 크기 상한이 명세서 6.5절 값으로 고정되어 있는지 검증.

    Nginx 90M / Django 85MiB 로 이루어진 상한 체계의 Django 쪽이다.
    Nginx 쪽은 deploy/nginx_thing_database_web.conf 에 있다.
    """

    def test_django_request_limit_is_85_mib(self):
        self.assertEqual(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, 85 * MiB)

    def test_large_body_spools_to_disk(self):
        """FILE_UPLOAD_MAX_MEMORY_SIZE 를 넘는 파일은 메모리에 다 올리지 않는다."""
        self.assertLess(
            settings.FILE_UPLOAD_MAX_MEMORY_SIZE,
            settings.DATA_UPLOAD_MAX_MEMORY_SIZE,
        )

    def test_part_limits_sum_matches_spec(self):
        """part 상한 합계가 80.25MiB 이고 Django 상한 이하여야 한다."""
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
