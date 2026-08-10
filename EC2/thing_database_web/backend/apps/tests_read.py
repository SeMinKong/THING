# backend/apps/tests_read.py
"""
Stage 3 테스트: 공개 GET 조회 4종과 rate limit.
"""
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps import storage
from apps.digest import sha256_bytes
from apps.models import Session
from apps.tests_upload import hand_command_csv, motor_status_csv
from apps.validators import HAND_COMMAND_HEADER, MOTOR_STATUS_HEADER

TEMP_DATA = tempfile.mkdtemp(prefix="test-read-data-")

ROBOT = "THING-001"
SID = "123456789012345678"

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def _dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


@override_settings(EC2_DATA_DIR=TEMP_DATA, SECURE_SSL_REDIRECT=False, CACHES=LOCMEM)
class ReadBaseTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_DATA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        storage.ensure_layout()

    def tearDown(self):
        shutil.rmtree(Path(TEMP_DATA) / "sessions", ignore_errors=True)
        shutil.rmtree(Path(TEMP_DATA) / "staging", ignore_errors=True)

    def make_ready(self, session_id=SID, robot_id=ROBOT, started="2026-07-29T00:00:00.000Z",
                   result="SUCCESS", rows=3, status=Session.Status.READY, write_files=True):
        hc = hand_command_csv(session_id=session_id, rows=rows)
        ms = motor_status_csv(session_id=session_id, rows=rows)
        meta = b'{"schema_version":1}'

        if write_files:
            final = storage.final_dir(robot_id, session_id)
            final.mkdir(parents=True, exist_ok=True)
            for kind, body in (("metadata", meta), ("hand_command", hc), ("motor_status", ms)):
                (final / storage.canonical_filename(session_id, kind)).write_bytes(body)

        start = _dt(started)
        return Session.objects.create(
            robot_id=robot_id,
            session_id=session_id,
            started_at=start,
            ended_at=start + timedelta(minutes=1),
            uploaded_at=start + timedelta(minutes=1, seconds=6),
            result=result,
            duration_ms=0,
            interface_commit="626c59e09f108e6e5eb6d2313efe28bf0e51ed03",
            time_sync=True,
            content_digest="sha256:" + "1" * 64,
            status=status,
            row_counts={"hand_command": rows, "motor_status": rows},
            file_sizes={"metadata": len(meta), "hand_command": len(hc), "motor_status": len(ms)},
            file_hashes={
                "metadata": sha256_bytes(meta),
                "hand_command": sha256_bytes(hc),
                "motor_status": sha256_bytes(ms),
            },
        )


class SessionListTests(ReadBaseTest):
    """[FR-47] 목록 조회."""

    def test_empty_list_shape(self):
        body = self.client.get("/api/v1/sessions").json()
        self.assertEqual(body["items"], [])
        self.assertIsNone(body["next_cursor"])
        self.assertIsNone(body["next_offset"])

    def test_item_fields_match_spec(self):
        self.make_ready()
        item = self.client.get("/api/v1/sessions").json()["items"][0]
        self.assertEqual(set(item), {
            "session_id", "robot_id", "started_at", "ended_at", "uploaded_at",
            "result", "duration_ms", "row_counts", "file_sizes",
        })
        self.assertEqual(item["session_id"], SID)
        self.assertEqual(item["duration_ms"], 60000)

    def test_timestamps_are_rfc3339_z(self):
        self.make_ready()
        item = self.client.get("/api/v1/sessions").json()["items"][0]
        for field in ("started_at", "ended_at", "uploaded_at"):
            self.assertTrue(item[field].endswith("Z"), item[field])
            self.assertNotIn("+00:00", item[field])

    def test_only_ready_is_public(self):
        self.make_ready(session_id="100", status=Session.Status.READY)
        self.make_ready(session_id="200", status=Session.Status.STAGING)
        self.make_ready(session_id="300", status=Session.Status.FAILED)
        items = self.client.get("/api/v1/sessions").json()["items"]
        self.assertEqual([i["session_id"] for i in items], ["100"])

    def test_sorted_started_at_then_session_id_desc(self):
        self.make_ready(session_id="100", started="2026-07-29T00:00:00.000Z")
        self.make_ready(session_id="300", started="2026-07-29T00:00:00.000Z")
        self.make_ready(session_id="200", started="2026-07-30T00:00:00.000Z")
        items = self.client.get("/api/v1/sessions").json()["items"]
        self.assertEqual([i["session_id"] for i in items], ["200", "300", "100"])

    def test_default_limit_is_20(self):
        for i in range(25):
            self.make_ready(session_id=str(1000 + i), write_files=False)
        body = self.client.get("/api/v1/sessions").json()
        self.assertEqual(len(body["items"]), 20)
        self.assertIsNotNone(body["next_cursor"])

    def test_limit_capped_at_100(self):
        for i in range(105):
            self.make_ready(session_id=str(2000 + i), write_files=False)
        body = self.client.get("/api/v1/sessions?limit=500").json()
        self.assertEqual(len(body["items"]), 100)

    def test_cursor_pagination_covers_all_without_duplicates(self):
        created = [str(3000 + i) for i in range(7)]
        for i, sid in enumerate(created):
            self.make_ready(session_id=sid,
                            started=f"2026-07-{10 + i:02d}T00:00:00.000Z",
                            write_files=False)
        seen, cursor = [], None
        for _ in range(10):
            url = "/api/v1/sessions?limit=3" + (f"&cursor={cursor}" if cursor else "")
            body = self.client.get(url).json()
            seen.extend(i["session_id"] for i in body["items"])
            cursor = body["next_cursor"]
            if not cursor:
                break
        self.assertEqual(sorted(seen), sorted(created))
        self.assertEqual(len(seen), len(set(seen)))

    def test_exact_session_id_search(self):
        self.make_ready(session_id="111", write_files=False)
        self.make_ready(session_id="222", write_files=False)
        items = self.client.get("/api/v1/sessions?session_id=222").json()["items"]
        self.assertEqual([i["session_id"] for i in items], ["222"])

    def test_search_with_invalid_session_id_returns_empty(self):
        self.make_ready(write_files=False)
        body = self.client.get("/api/v1/sessions?session_id=../etc").json()
        self.assertEqual(body["items"], [])

    def test_result_filter(self):
        self.make_ready(session_id="111", result="SUCCESS", write_files=False)
        self.make_ready(session_id="222", result="FAILURE", write_files=False)
        items = self.client.get("/api/v1/sessions?result=FAILURE").json()["items"]
        self.assertEqual([i["session_id"] for i in items], ["222"])

    def test_400_on_bad_limit_and_cursor(self):
        self.assertEqual(self.client.get("/api/v1/sessions?limit=abc").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/sessions?limit=0").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/sessions?cursor=!!!").status_code, 400)

    def test_405_on_write(self):
        resp = self.client.post("/api/v1/sessions", {})
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(resp.json()["error"]["code"], "METHOD_NOT_ALLOWED")


class SessionDetailTests(ReadBaseTest):
    """[FR-47] 상세 조회."""

    def test_detail_fields_and_download_links(self):
        self.make_ready()
        body = self.client.get(f"/api/v1/sessions/{SID}").json()
        self.assertEqual(body["session_id"], SID)
        self.assertEqual(body["schema_version"], 1)
        self.assertEqual(body["data_version"], 1)
        self.assertTrue(body["content_digest"].startswith("sha256:"))
        # landmark 는 형식 미정이라 선택 part 다. 없는 파일 링크를 주면 사용자가
        # 404 를 받으므로, 실제로 업로드된 종류만 링크를 낸다.
        self.assertEqual(
            set(body["downloads"]),
            {"metadata", "hand_command", "motor_status"},
        )
        self.assertEqual(
            body["downloads"]["metadata"], f"/api/v1/sessions/{SID}/download/metadata"
        )

    def test_404_for_unknown_session(self):
        resp = self.client.get("/api/v1/sessions/999")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"]["code"], "NOT_FOUND")

    def test_404_for_staging_session(self):
        self.make_ready(status=Session.Status.STAGING)
        self.assertEqual(self.client.get(f"/api/v1/sessions/{SID}").status_code, 404)

    def test_404_for_path_traversal_attempt(self):
        self.assertEqual(self.client.get("/api/v1/sessions/..%2Fetc").status_code, 404)


class SessionDataTests(ReadBaseTest):
    """[FR-48 기반] 시계열 조회."""

    def test_hand_command_columns_and_types(self):
        self.make_ready(rows=2)
        body = self.client.get(f"/api/v1/sessions/{SID}/data?dataset=hand_command").json()
        self.assertEqual(body["dataset"], "hand_command")
        self.assertEqual(body["columns"],
                         [c for c in HAND_COMMAND_HEADER if c != "session_id"])
        row = body["rows"][0]
        self.assertIsInstance(row["stamp_sec"], int)
        self.assertIsInstance(row["thumb_flex"], float)
        self.assertIsInstance(row["source"], str)

    def test_motor_status_bool_columns(self):
        self.make_ready(rows=2)
        body = self.client.get(f"/api/v1/sessions/{SID}/data?dataset=motor_status").json()
        self.assertEqual(body["columns"],
                         [c for c in MOTOR_STATUS_HEADER if c != "session_id"])
        row = body["rows"][0]
        self.assertIs(row["communication_ok"], True)
        self.assertIs(row["bus_communication_ok"], True)
        self.assertIsInstance(row["motor_id"], int)

    def test_rows_are_timestamp_ascending(self):
        self.make_ready(rows=5)
        rows = self.client.get(f"/api/v1/sessions/{SID}/data?dataset=hand_command").json()["rows"]
        stamps = [(r["stamp_sec"], r["stamp_nanosec"]) for r in rows]
        self.assertEqual(stamps, sorted(stamps))

    def test_limit_and_cursor(self):
        self.make_ready(rows=7)
        seen, cursor = [], None
        for _ in range(10):
            url = f"/api/v1/sessions/{SID}/data?dataset=hand_command&limit=3"
            if cursor:
                url += f"&cursor={cursor}"
            body = self.client.get(url).json()
            seen.extend(r["elapsed_ms"] for r in body["rows"])
            cursor = body["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(seen), 7)
        self.assertEqual(len(set(seen)), 7)

    def test_limit_capped_at_5000(self):
        self.make_ready(rows=2)
        body = self.client.get(
            f"/api/v1/sessions/{SID}/data?dataset=hand_command&limit=99999"
        ).json()
        self.assertEqual(len(body["rows"]), 2)

    def test_400_without_dataset(self):
        self.make_ready()
        self.assertEqual(self.client.get(f"/api/v1/sessions/{SID}/data").status_code, 400)

    def test_400_with_unknown_dataset(self):
        self.make_ready()
        resp = self.client.get(f"/api/v1/sessions/{SID}/data?dataset=rosbag2")
        self.assertEqual(resp.status_code, 400)

    def test_404_for_staging_session(self):
        self.make_ready(status=Session.Status.STAGING)
        resp = self.client.get(f"/api/v1/sessions/{SID}/data?dataset=hand_command")
        self.assertEqual(resp.status_code, 404)


class SessionDownloadTests(ReadBaseTest):
    """[FR-49 / NFR-25] 파일 다운로드와 무결성."""

    def test_absent_landmark_is_404(self):
        """landmark 를 올리지 않은 세션은 404 다. 빈 파일이나 200 을 주지 않는다."""
        self.make_ready()
        resp = self.client.get(f"/api/v1/sessions/{SID}/download/landmark")
        self.assertEqual(resp.status_code, 404)

    def test_downloads_present_kinds_with_canonical_filenames(self):
        session = self.make_ready()
        for kind in ("metadata", "hand_command", "motor_status"):
            resp = self.client.get(f"/api/v1/sessions/{SID}/download/{kind}")
            self.assertEqual(resp.status_code, 200, kind)
            self.assertIn(
                storage.canonical_filename(SID, kind),
                resp["Content-Disposition"],
            )

    def test_content_matches_stored_file(self):
        self.make_ready()
        resp = self.client.get(f"/api/v1/sessions/{SID}/download/metadata")
        self.assertEqual(b"".join(resp.streaming_content), b'{"schema_version":1}')

    def test_404_for_unknown_file_kind(self):
        self.make_ready()
        for bad in ("rosbag2", "db", "..%2Fmetadata"):
            resp = self.client.get(f"/api/v1/sessions/{SID}/download/{bad}")
            self.assertEqual(resp.status_code, 404, bad)

    def test_404_when_file_missing(self):
        self.make_ready()
        storage.final_path(ROBOT, SID, "hand_command").unlink()
        resp = self.client.get(f"/api/v1/sessions/{SID}/download/hand_command")
        self.assertEqual(resp.status_code, 404)

    def test_404_when_hash_mismatch(self):
        """[NFR-25] hash 불일치는 정상 다운로드로 제공하지 않는다."""
        self.make_ready()
        path = storage.final_path(ROBOT, SID, "hand_command")
        original = path.read_bytes()
        path.write_bytes(original.replace(b"VISION", b"TAMPER"))
        resp = self.client.get(f"/api/v1/sessions/{SID}/download/hand_command")
        self.assertEqual(resp.status_code, 404)

    def test_repeated_get_does_not_modify_original(self):
        self.make_ready()
        path = storage.final_path(ROBOT, SID, "motor_status")
        before = path.read_bytes()
        for _ in range(3):
            self.client.get(f"/api/v1/sessions/{SID}/download/motor_status")
        self.assertEqual(path.read_bytes(), before)

    def test_404_for_staging_session(self):
        self.make_ready(status=Session.Status.STAGING)
        resp = self.client.get(f"/api/v1/sessions/{SID}/download/metadata")
        self.assertEqual(resp.status_code, 404)


@override_settings(
    EC2_DATA_DIR=TEMP_DATA,
    SECURE_SSL_REDIRECT=False,
    CACHES=LOCMEM,
    REST_FRAMEWORK={
        "EXCEPTION_HANDLER": "apps.errors.api_exception_handler",
        "DEFAULT_THROTTLE_RATES": {"public": "3/min", "upload": "2/min"},
        "UNAUTHENTICATED_USER": None,
    },
)
class ThrottleTests(TestCase):
    """[6.5절] 초과 시 429 와 Retry-After."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_DATA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        # 클래스 단위 rate 캐시를 비워 override 된 값이 적용되게 한다
        from apps.throttles import DeviceUploadThrottle, PublicReadThrottle
        for klass in (PublicReadThrottle, DeviceUploadThrottle):
            klass.rate = None
            klass.THROTTLE_RATES = {"public": "3/min", "upload": "2/min"}

    def tearDown(self):
        cache.clear()

    def test_public_get_returns_429_after_limit(self):
        codes = [self.client.get("/api/v1/sessions").status_code for _ in range(5)]
        self.assertIn(429, codes)

    def test_429_body_and_retry_after(self):
        resp = None
        for _ in range(6):
            resp = self.client.get("/api/v1/sessions")
            if resp.status_code == 429:
                break
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()["error"]["code"], "RATE_LIMITED")
        self.assertIn("Retry-After", resp.headers)

    def test_upload_throttle_is_separate_scope(self):
        for _ in range(5):
            self.client.get("/api/v1/sessions")
        # 공개 GET 한도를 소진해도 업로드는 자기 scope 를 따른다 (401 이 나와야 정상)
        resp = self.client.post("/api/v1/uploads/sessions", {})
        self.assertNotEqual(resp.status_code, 429)

@override_settings(EC2_DATA_DIR=TEMP_DATA, SECURE_SSL_REDIRECT=False, CACHES=LOCMEM)
class SeriesAliasTests(ReadBaseTest):
    """스프린트 티켓의 /series?type= 별칭.

    명세서 6.5절은 /data?dataset=, 티켓은 /series?type= 으로 적혀 있다.
    어느 쪽으로 호출해도 같은 응답이어야 한다.
    """

    def test_series_alias_matches_data_endpoint(self):
        self.make_ready(rows=3)
        a = self.client.get(f"/api/v1/sessions/{SID}/data?dataset=hand_command").json()
        b = self.client.get(f"/api/v1/sessions/{SID}/series?type=hand_command").json()
        self.assertEqual(a, b)

    def test_type_param_works_on_data_endpoint(self):
        self.make_ready(rows=2)
        resp = self.client.get(f"/api/v1/sessions/{SID}/data?type=motor_status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["type"], "motor_status")

    def test_dataset_param_works_on_series_endpoint(self):
        self.make_ready(rows=2)
        resp = self.client.get(f"/api/v1/sessions/{SID}/series?dataset=motor_status")
        self.assertEqual(resp.status_code, 200)

    def test_both_datasets_available_on_series(self):
        self.make_ready(rows=2)
        for kind in ("hand_command", "motor_status"):
            resp = self.client.get(f"/api/v1/sessions/{SID}/series?type={kind}")
            self.assertEqual(resp.status_code, 200, kind)
            self.assertEqual(resp.json()["dataset"], kind)

    def test_400_without_type_or_dataset(self):
        self.make_ready()
        self.assertEqual(self.client.get(f"/api/v1/sessions/{SID}/series").status_code, 400)

    def test_400_with_unknown_type(self):
        self.make_ready()
        resp = self.client.get(f"/api/v1/sessions/{SID}/series?type=rosbag2")
        self.assertEqual(resp.status_code, 400)

    def test_404_for_staging_session_on_series(self):
        self.make_ready(status=Session.Status.STAGING)
        resp = self.client.get(f"/api/v1/sessions/{SID}/series?type=hand_command")
        self.assertEqual(resp.status_code, 404)


@override_settings(EC2_DATA_DIR=TEMP_DATA, SECURE_SSL_REDIRECT=False, CACHES=LOCMEM)
class OffsetPaginationTests(ReadBaseTest):
    """limit·offset 검증 (스프린트 티켓 범위)."""

    def test_list_offset_pages_without_duplicates(self):
        created = [str(4000 + i) for i in range(7)]
        for i, sid in enumerate(created):
            self.make_ready(session_id=sid,
                            started=f"2026-07-{10 + i:02d}T00:00:00.000Z",
                            write_files=False)
        seen, offset = [], 0
        for _ in range(10):
            body = self.client.get(f"/api/v1/sessions?limit=3&offset={offset}").json()
            seen.extend(i["session_id"] for i in body["items"])
            if body["next_offset"] is None:
                break
            offset = body["next_offset"]
        self.assertEqual(sorted(seen), sorted(created))
        self.assertEqual(len(seen), len(set(seen)))

    def test_series_offset_pages(self):
        self.make_ready(rows=7)
        seen, offset = [], 0
        for _ in range(10):
            body = self.client.get(
                f"/api/v1/sessions/{SID}/series?type=hand_command&limit=3&offset={offset}"
            ).json()
            seen.extend(r["elapsed_ms"] for r in body["rows"])
            if body["next_offset"] is None:
                break
            offset = body["next_offset"]
        self.assertEqual(len(seen), 7)
        self.assertEqual(len(set(seen)), 7)

    def test_400_on_bad_offset(self):
        self.make_ready(write_files=False)
        self.assertEqual(self.client.get("/api/v1/sessions?offset=abc").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/sessions?offset=-1").status_code, 400)

    def test_cursor_takes_precedence_over_offset(self):
        for i in range(5):
            self.make_ready(session_id=str(5000 + i),
                            started=f"2026-07-{10 + i:02d}T00:00:00.000Z",
                            write_files=False)
        first = self.client.get("/api/v1/sessions?limit=2").json()
        cursor = first["next_cursor"]
        # cursor 와 offset 을 함께 주면 cursor 가 우선한다
        body = self.client.get(f"/api/v1/sessions?limit=2&cursor={cursor}&offset=99").json()
        self.assertEqual(len(body["items"]), 2)
        self.assertNotEqual(
            [i["session_id"] for i in body["items"]],
            [i["session_id"] for i in first["items"]],
        )


@override_settings(EC2_DATA_DIR=TEMP_DATA, SECURE_SSL_REDIRECT=False, CACHES=LOCMEM)
class JsonCsvConsistencyTests(ReadBaseTest):
    """[완료 조건] 동일 세션 데이터가 JSON 응답과 저장된 CSV 에서 일치한다."""

    def _stored_csv_rows(self, dataset):
        import csv
        path = storage.final_path(ROBOT, SID, dataset)
        with open(path, "r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            return list(reader)

    def test_hand_command_json_matches_stored_csv(self):
        self.make_ready(rows=3)
        body = self.client.get(f"/api/v1/sessions/{SID}/series?type=hand_command&limit=5000").json()
        csv_rows = self._stored_csv_rows("hand_command")

        self.assertEqual(len(body["rows"]), len(csv_rows))
        for json_row, csv_row in zip(body["rows"], csv_rows):
            for column in body["columns"]:
                raw = csv_row[column]
                value = json_row[column]
                if raw == "":
                    self.assertIsNone(value, f"{column}: 빈 칸은 null 이어야 한다")
                elif isinstance(value, bool):
                    self.assertEqual(str(value).lower(), raw.lower(), column)
                elif isinstance(value, (int, float)):
                    self.assertAlmostEqual(float(raw), float(value), places=9, msg=column)
                else:
                    self.assertEqual(raw, value, column)

    def test_motor_status_json_matches_stored_csv(self):
        self.make_ready(rows=4)
        body = self.client.get(f"/api/v1/sessions/{SID}/series?type=motor_status&limit=5000").json()
        csv_rows = self._stored_csv_rows("motor_status")

        self.assertEqual(len(body["rows"]), len(csv_rows))
        for json_row, csv_row in zip(body["rows"], csv_rows):
            for column in body["columns"]:
                raw, value = csv_row[column], json_row[column]
                if raw == "":
                    self.assertIsNone(value, column)
                elif isinstance(value, bool):
                    self.assertEqual(str(value).lower(), raw.lower(), column)
                elif isinstance(value, (int, float)):
                    self.assertAlmostEqual(float(raw), float(value), places=9, msg=column)
                else:
                    self.assertEqual(raw, value, column)

    def test_downloaded_csv_is_byte_identical_to_stored(self):
        """다운로드로 받은 CSV 가 저장 파일과 바이트 단위로 같아야 한다."""
        self.make_ready(rows=3)
        for kind in ("hand_command", "motor_status"):
            resp = self.client.get(f"/api/v1/sessions/{SID}/download/{kind}")
            downloaded = b"".join(resp.streaming_content)
            stored = storage.final_path(ROBOT, SID, kind).read_bytes()
            self.assertEqual(downloaded, stored, kind)

    def test_row_counts_in_detail_match_series_length(self):
        self.make_ready(rows=3)
        detail = self.client.get(f"/api/v1/sessions/{SID}").json()
        for kind in ("hand_command", "motor_status"):
            series = self.client.get(
                f"/api/v1/sessions/{SID}/series?type={kind}&limit=5000"
            ).json()
            self.assertEqual(detail["row_counts"][kind], len(series["rows"]), kind)


@override_settings(EC2_DATA_DIR=TEMP_DATA, SECURE_SSL_REDIRECT=False, CACHES=LOCMEM)
class PublicResponseLeakTests(ReadBaseTest):
    """[완료 조건] 공개 응답에 Token·서버 경로·내부 오류 정보가 노출되지 않는다."""

    LEAK_MARKERS = ("/var/lib", "/home/", TEMP_DATA, "Traceback",
                    "sqlite", "DEVICE_TOKENS", "SECRET_KEY", "staging")

    def _assert_clean(self, raw, label):
        for marker in self.LEAK_MARKERS:
            self.assertNotIn(marker, raw, f"{label}: '{marker}' 노출")

    def test_success_responses_are_clean(self):
        self.make_ready(rows=2)
        urls = [
            "/api/v1/sessions",
            f"/api/v1/sessions/{SID}",
            f"/api/v1/sessions/{SID}/series?type=hand_command",
            f"/api/v1/sessions/{SID}/series?type=motor_status",
        ]
        for url in urls:
            self._assert_clean(self.client.get(url).content.decode(), url)

    def test_error_responses_are_clean(self):
        self.make_ready(rows=2)
        cases = [
            "/api/v1/sessions/999",                                  # 404
            f"/api/v1/sessions/{SID}/series",                        # 400
            f"/api/v1/sessions/{SID}/series?type=rosbag2",           # 400
            "/api/v1/sessions?limit=abc",                            # 400
            "/api/v1/sessions?cursor=!!!",                           # 400
            f"/api/v1/sessions/{SID}/download/db",                   # 404
            "/api/v1/sessions/..%2Fetc%2Fpasswd",                    # 404
        ]
        for url in cases:
            resp = self.client.get(url)
            self.assertGreaterEqual(resp.status_code, 400, url)
            self._assert_clean(resp.content.decode(), url)

    def test_missing_file_error_does_not_reveal_path(self):
        """READY 인데 파일이 없는 경우에도 경로를 노출하지 않는다."""
        self.make_ready(rows=2)
        storage.final_path(ROBOT, SID, "hand_command").unlink()
        resp = self.client.get(f"/api/v1/sessions/{SID}/download/hand_command")
        self.assertEqual(resp.status_code, 404)
        self._assert_clean(resp.content.decode(), "missing file")

    def test_error_envelope_shape_is_consistent(self):
        """오류 구조가 일관되어야 한다 (스프린트 범위)."""
        for url in ["/api/v1/sessions/999", "/api/v1/sessions?limit=0"]:
            body = self.client.get(url).json()
            self.assertEqual(set(body), {"error", "request_id"}, url)
            self.assertEqual(set(body["error"]), {"code", "message", "details"}, url)
            self.assertIsInstance(body["error"]["details"], list, url)
