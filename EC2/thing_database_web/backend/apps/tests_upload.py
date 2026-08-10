# backend/apps/tests_upload.py
"""
Stage 2 테스트: POST /api/v1/uploads/sessions

명세서 8.3-8 "401 / 405 / 413 / 422 / 409 및 STAGING crash 복구" 대응.
"""
import copy
import json
import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps import storage
from apps.device_auth import hash_token
from apps.digest import compute_content_digest, sha256_bytes
from apps.models import Session
from apps.validators import HAND_COMMAND_HEADER, MOTOR_STATUS_HEADER

TEMP_DATA = tempfile.mkdtemp(prefix="test-upload-data-")

ROBOT = "THING-001"
SID = "123456789012345678"
PLAIN_TOKEN = "test-device-token-at-least-32-bytes-long-xxxx"
TOKENS = f"{ROBOT}:{hash_token(PLAIN_TOKEN)}"

URL = "/api/v1/uploads/sessions"


def hand_command_csv(session_id=SID, rows=2, decreasing=False, nan=False):
    lines = [",".join(HAND_COMMAND_HEADER)]
    for i in range(rows):
        sec = 1785283200 + (rows - i if decreasing else i)
        confidence = "nan" if (nan and i == 0) else "0.92"
        lines.append(
            f"{session_id},{sec},0,{i * 10},{i + 1},VISION,"
            f"0.1,0.2,0.0,0.3,0.3,0.2,0.2,0.5,{confidence}"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def motor_status_csv(session_id=SID, rows=2):
    lines = [",".join(MOTOR_STATUS_HEADER)]
    for i in range(rows):
        sec = 1785283200 + i
        lines.append(
            f"{session_id},{sec},0,{i * 10},base_link,{11 + i},thumb_flex,"
            f"2048,2050,0.0,0.01,0.0,0.05,11.9,32,true,0,0,true,true,0"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_metadata(hc, ms, session_id=SID, robot_id=ROBOT, result="SUCCESS", rows=None):
    rows = rows or {}
    meta = {
        "schema_version": 1,
        "data_version": 1,
        "robot_id": robot_id,
        "session_id": session_id,
        "started_at": "2026-07-29T00:00:00.000Z",
        "ended_at": "2026-07-29T00:01:00.000Z",
        "exported_at": "2026-07-29T00:01:05.000Z",
        "result": result,
        "interface_commit": "626c59e09f108e6e5eb6d2313efe28bf0e51ed03",
        "time_sync": True,
        "files": {
            "hand_command": {
                "filename": f"session_{session_id}_hand_command.csv",
                "size_bytes": len(hc),
                "row_count": rows.get("hand_command", hc.count(b"\n") - 1),
                "sha256": sha256_bytes(hc),
            },
            "motor_status": {
                "filename": f"session_{session_id}_motor_status.csv",
                "size_bytes": len(ms),
                "row_count": rows.get("motor_status", ms.count(b"\n") - 1),
                "sha256": sha256_bytes(ms),
            },
        },
    }
    meta["content_digest"] = compute_content_digest(meta)
    return meta


def as_parts(meta, hc, ms):
    body = json.dumps(meta).encode("utf-8")
    return {
        "metadata": SimpleUploadedFile("m.json", body, content_type="application/json"),
        "hand_command": SimpleUploadedFile("h.csv", hc, content_type="text/csv"),
        "motor_status": SimpleUploadedFile("s.csv", ms, content_type="text/csv"),
    }


@override_settings(
    EC2_DATA_DIR=TEMP_DATA,
    DEVICE_TOKENS=TOKENS,
    SECURE_SSL_REDIRECT=False,
)
class UploadBaseTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        storage.ensure_layout()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_DATA, ignore_errors=True)
        super().tearDownClass()

    def tearDown(self):
        shutil.rmtree(Path(TEMP_DATA) / "staging", ignore_errors=True)
        shutil.rmtree(Path(TEMP_DATA) / "sessions", ignore_errors=True)

    def post(self, parts, token=PLAIN_TOKEN, idempotency=None, **extra):
        headers = {}
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        if idempotency:
            headers["HTTP_IDEMPOTENCY_KEY"] = idempotency
        headers.update(extra)
        return self.client.post(URL, parts, **headers)

    def valid_upload(self, **kwargs):
        hc = hand_command_csv()
        ms = motor_status_csv()
        meta = build_metadata(hc, ms, **kwargs)
        return meta, as_parts(meta, hc, ms)

    def assertErrorCode(self, response, code):
        body = response.json()
        self.assertIn("error", body, body)
        self.assertEqual(body["error"]["code"], code, body)
        self.assertIn("request_id", body)


class UploadSuccessTests(UploadBaseTest):
    """[FR-46] 정상 업로드와 응답 규격."""

    def test_metadata_without_exported_at_is_accepted(self):
        """로봇 exporter(thing_logger)는 exported_at 을 내보내지 않는다.

        export_schema.METADATA_FIELDS 에 exported_at 이 없다. content_digest 는
        exported_at 을 제외하고 계산하므로, 없어도 같은 digest 로 통과해야 한다.
        """
        hc, ms = hand_command_csv(), motor_status_csv()
        meta = build_metadata(hc, ms)
        del meta["exported_at"]
        resp = self.post(as_parts(meta, hc, ms))
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_returns_201_and_ready(self):
        meta, parts = self.valid_upload()
        resp = self.post(parts)
        self.assertEqual(resp.status_code, 201, resp.content)

        body = resp.json()
        self.assertEqual(body["session_id"], SID)
        self.assertEqual(body["data_version"], 1)
        self.assertEqual(body["status"], "READY")
        self.assertEqual(body["content_digest"], meta["content_digest"])
        self.assertEqual(set(body["files"]), set(storage.FILE_KINDS))
        self.assertTrue(body["uploaded_at"].endswith("Z"))

    def test_persists_exactly_three_canonical_files(self):
        _, parts = self.valid_upload()
        self.post(parts)
        final = storage.final_dir(ROBOT, SID)
        names = sorted(p.name for p in final.iterdir())
        self.assertEqual(names, [
            f"session_{SID}_hand_command.csv",
            f"session_{SID}_metadata.json",
            f"session_{SID}_motor_status.csv",
        ])

    def test_staging_is_cleaned_after_success(self):
        _, parts = self.valid_upload()
        self.post(parts)
        self.assertFalse(storage.staging_dir(ROBOT, SID).exists())

    def test_session_row_is_ready_with_derived_duration(self):
        _, parts = self.valid_upload()
        self.post(parts)
        session = Session.objects.get(robot_id=ROBOT, session_id=SID)
        self.assertEqual(session.status, Session.Status.READY)
        self.assertEqual(session.duration_ms, 60000)
        self.assertEqual(session.row_counts, {"hand_command": 2, "motor_status": 2})

    def test_accepts_matching_idempotency_key(self):
        meta, parts = self.valid_upload()
        key = f"{ROBOT}:{SID}:1:{meta['content_digest']}"
        self.assertEqual(self.post(parts, idempotency=key).status_code, 201)

    def test_failure_result_is_accepted(self):
        _, parts = self.valid_upload(result="FAILURE")
        self.assertEqual(self.post(parts).status_code, 201)


class UploadAuthTests(UploadBaseTest):
    """[FR-51 / NFR-27] 401 인증."""

    def test_401_without_token(self):
        _, parts = self.valid_upload()
        resp = self.post(parts, token=None)
        self.assertEqual(resp.status_code, 401)
        self.assertErrorCode(resp, "UNAUTHORIZED")

    def test_401_with_wrong_token(self):
        _, parts = self.valid_upload()
        resp = self.post(parts, token="wrong-token")
        self.assertEqual(resp.status_code, 401)

    def test_401_when_token_robot_differs_from_metadata(self):
        _, parts = self.valid_upload(robot_id="THING-999")
        resp = self.post(parts)
        self.assertEqual(resp.status_code, 401)
        self.assertErrorCode(resp, "ROBOT_MISMATCH")

    @override_settings(DEVICE_TOKENS="")
    def test_401_when_no_tokens_configured(self):
        _, parts = self.valid_upload()
        self.assertEqual(self.post(parts).status_code, 401)

    def test_no_session_created_on_auth_failure(self):
        _, parts = self.valid_upload()
        self.post(parts, token=None)
        self.assertEqual(Session.objects.count(), 0)

    def test_response_does_not_leak_token_or_paths(self):
        _, parts = self.valid_upload()
        raw = self.post(parts, token="wrong-token").content.decode()
        self.assertNotIn(PLAIN_TOKEN, raw)
        self.assertNotIn(hash_token(PLAIN_TOKEN), raw)
        self.assertNotIn(TEMP_DATA, raw)
        self.assertNotIn("Traceback", raw)


class UploadPartTests(UploadBaseTest):
    """[6.5절] 400 part 구성, 415 content type."""

    def test_400_when_part_missing(self):
        hc, ms = hand_command_csv(), motor_status_csv()
        meta = build_metadata(hc, ms)
        parts = as_parts(meta, hc, ms)
        del parts["motor_status"]
        resp = self.post(parts)
        self.assertEqual(resp.status_code, 400)
        self.assertErrorCode(resp, "MALFORMED_REQUEST")

    def test_400_when_extra_part_present(self):
        hc, ms = hand_command_csv(), motor_status_csv()
        meta = build_metadata(hc, ms)
        parts = as_parts(meta, hc, ms)
        parts["rosbag2"] = SimpleUploadedFile("b.db3", b"x", content_type="application/octet-stream")
        resp = self.post(parts)
        self.assertEqual(resp.status_code, 400)
        self.assertErrorCode(resp, "UNEXPECTED_PART")

    def test_415_when_metadata_is_not_json(self):
        hc, ms = hand_command_csv(), motor_status_csv()
        meta = build_metadata(hc, ms)
        parts = as_parts(meta, hc, ms)
        parts["metadata"] = SimpleUploadedFile(
            "m.json", json.dumps(meta).encode(), content_type="text/plain"
        )
        resp = self.post(parts)
        self.assertEqual(resp.status_code, 415)
        self.assertErrorCode(resp, "UNSUPPORTED_MEDIA_TYPE")

    def test_415_when_csv_content_type_wrong(self):
        hc, ms = hand_command_csv(), motor_status_csv()
        meta = build_metadata(hc, ms)
        parts = as_parts(meta, hc, ms)
        parts["hand_command"] = SimpleUploadedFile(
            "h.csv", hc, content_type="application/octet-stream"
        )
        self.assertEqual(self.post(parts).status_code, 415)

    def test_413_when_metadata_exceeds_part_limit(self):
        hc, ms = hand_command_csv(), motor_status_csv()
        meta = build_metadata(hc, ms)
        oversized = json.dumps(meta).encode() + b" " * (256 * 1024 + 1)
        parts = as_parts(meta, hc, ms)
        parts["metadata"] = SimpleUploadedFile("m.json", oversized, content_type="application/json")
        resp = self.post(parts)
        self.assertEqual(resp.status_code, 413)
        self.assertErrorCode(resp, "PAYLOAD_TOO_LARGE")


class UploadValidationTests(UploadBaseTest):
    """[FR-51 / NFR-25] 422 schema·값 검증."""

    def _post_with_metadata(self, mutate):
        hc, ms = hand_command_csv(), motor_status_csv()
        meta = build_metadata(hc, ms)
        mutate(meta)
        return self.post(as_parts(meta, hc, ms))

    def test_422_when_schema_version_not_1(self):
        resp = self._post_with_metadata(lambda m: m.update(schema_version=2))
        self.assertEqual(resp.status_code, 422)

    def test_422_when_result_invalid(self):
        resp = self._post_with_metadata(lambda m: m.update(result="PARTIAL"))
        self.assertEqual(resp.status_code, 422)

    def test_422_when_ended_at_not_after_started_at(self):
        resp = self._post_with_metadata(
            lambda m: m.update(ended_at="2026-07-29T00:00:00.000Z")
        )
        self.assertEqual(resp.status_code, 422)

    def test_422_when_timestamp_not_rfc3339_z(self):
        resp = self._post_with_metadata(
            lambda m: m.update(started_at="2026-07-29T00:00:00+09:00")
        )
        self.assertEqual(resp.status_code, 422)

    def test_422_when_declared_sha256_wrong(self):
        """[NFR-25] metadata 가 주장한 hash 가 실제 파일과 달라야 거부된다."""
        def mutate(m):
            m["files"]["hand_command"]["sha256"] = "a" * 64
            m["content_digest"] = compute_content_digest(m)
        resp = self._post_with_metadata(mutate)
        self.assertEqual(resp.status_code, 422)

    def test_422_when_declared_row_count_wrong(self):
        def mutate(m):
            m["files"]["motor_status"]["row_count"] = 999
            m["content_digest"] = compute_content_digest(m)
        resp = self._post_with_metadata(mutate)
        self.assertEqual(resp.status_code, 422)

    def test_422_when_content_digest_wrong(self):
        resp = self._post_with_metadata(
            lambda m: m.update(content_digest="sha256:" + "0" * 64)
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(Session.objects.count(), 0)

    def test_422_when_idempotency_key_mismatches(self):
        meta, parts = self.valid_upload()
        resp = self.post(parts, idempotency=f"{ROBOT}:{SID}:1:sha256:" + "0" * 64)
        self.assertEqual(resp.status_code, 422)

    def test_422_when_csv_header_wrong(self):
        hc = b"wrong,header\n1,2\n"
        ms = motor_status_csv()
        meta = build_metadata(hc, ms, rows={"hand_command": 1})
        self.assertEqual(self.post(as_parts(meta, hc, ms)).status_code, 422)

    def test_422_when_csv_timestamp_decreases(self):
        hc = hand_command_csv(rows=3, decreasing=True)
        ms = motor_status_csv()
        meta = build_metadata(hc, ms)
        self.assertEqual(self.post(as_parts(meta, hc, ms)).status_code, 422)

    def test_422_when_csv_contains_nan(self):
        hc = hand_command_csv(rows=2, nan=True)
        ms = motor_status_csv()
        meta = build_metadata(hc, ms)
        self.assertEqual(self.post(as_parts(meta, hc, ms)).status_code, 422)

    def test_422_when_csv_session_id_differs(self):
        hc = hand_command_csv(session_id="999")
        ms = motor_status_csv()
        meta = build_metadata(hc, ms)
        self.assertEqual(self.post(as_parts(meta, hc, ms)).status_code, 422)

    def test_400_when_metadata_is_invalid_json(self):
        hc, ms = hand_command_csv(), motor_status_csv()
        meta = build_metadata(hc, ms)
        parts = as_parts(meta, hc, ms)
        parts["metadata"] = SimpleUploadedFile(
            "m.json", b"{not json", content_type="application/json"
        )
        self.assertEqual(self.post(parts).status_code, 400)

    def test_staging_removed_after_validation_failure(self):
        """[NFR-26] 실패한 시도가 staging 부분 파일을 남기지 않아야 한다."""
        self._post_with_metadata(lambda m: m.update(content_digest="sha256:" + "0" * 64))
        self.assertFalse(storage.staging_dir(ROBOT, SID).exists())
        self.assertFalse(storage.final_dir(ROBOT, SID).exists())


class UploadIdempotencyTests(UploadBaseTest):
    """[NFR-26] 201 / 200 / 409 멱등성."""

    def test_same_digest_returns_200_without_duplicate(self):
        meta, parts = self.valid_upload()
        self.assertEqual(self.post(parts).status_code, 201)

        _, parts2 = self.valid_upload()
        resp = self.post(parts2)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["content_digest"], meta["content_digest"])
        self.assertEqual(Session.objects.count(), 1)

    def test_same_digest_does_not_overwrite_stored_files(self):
        _, parts = self.valid_upload()
        self.post(parts)
        path = storage.final_path(ROBOT, SID, "hand_command")
        before = path.stat().st_mtime_ns

        _, parts2 = self.valid_upload()
        self.post(parts2)
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_different_digest_returns_409(self):
        _, parts = self.valid_upload()
        self.assertEqual(self.post(parts).status_code, 201)

        hc = hand_command_csv(rows=5)
        ms = motor_status_csv()
        meta2 = build_metadata(hc, ms)
        resp = self.post(as_parts(meta2, hc, ms))
        self.assertEqual(resp.status_code, 409)
        self.assertErrorCode(resp, "SESSION_CONTENT_CONFLICT")

    def test_409_does_not_overwrite_or_duplicate(self):
        _, parts = self.valid_upload()
        self.post(parts)
        original = storage.final_path(ROBOT, SID, "hand_command").read_bytes()

        hc = hand_command_csv(rows=5)
        ms = motor_status_csv()
        meta2 = build_metadata(hc, ms)
        self.post(as_parts(meta2, hc, ms))

        self.assertEqual(storage.final_path(ROBOT, SID, "hand_command").read_bytes(), original)
        self.assertEqual(Session.objects.count(), 1)
        self.assertFalse(storage.staging_dir(ROBOT, SID).exists())

    def test_reexport_with_new_exported_at_is_200(self):
        """수동 재생성 시 exported_at 만 달라지면 같은 세션으로 취급한다."""
        _, parts = self.valid_upload()
        self.post(parts)

        hc, ms = hand_command_csv(), motor_status_csv()
        meta2 = build_metadata(hc, ms)
        meta2["exported_at"] = "2099-01-01T00:00:00.000Z"
        resp = self.post(as_parts(meta2, hc, ms))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Session.objects.count(), 1)


class PublicWriteRejectionTests(UploadBaseTest):
    """[FR-50] 405 공개 쓰기."""

    def test_get_on_upload_endpoint_is_405(self):
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 405)
        self.assertErrorCode(resp, "METHOD_NOT_ALLOWED")

    def test_error_envelope_shape(self):
        body = self.client.get(URL).json()
        self.assertEqual(set(body), {"error", "request_id"})
        self.assertEqual(set(body["error"]), {"code", "message", "details"})
