# backend/apps/tests_session.py
"""
Stage 1 회귀 테스트: Session 모델, 저장 레이아웃, content_digest.

실행:
    python manage.py test apps
"""
import copy
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from apps import landmark_contract, storage
from apps.digest import (
    TEST_VECTOR,
    canonical_json,
    compute_content_digest,
    digests_match,
    sha256_bytes,
    sha256_file,
)
from apps.models import Session

TEMP_DATA = tempfile.mkdtemp(prefix="test-thing-data-")

ROBOT = "THING-001"
SID = "123456789012345678"


def _dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def make_session(**overrides):
    defaults = dict(
        robot_id=ROBOT,
        session_id=SID,
        started_at=_dt("2026-07-29T00:00:00.000Z"),
        ended_at=_dt("2026-07-29T00:01:00.000Z"),
        uploaded_at=_dt("2026-07-29T00:01:06.000Z"),
        result=Session.Result.SUCCESS,
        duration_ms=0,
        interface_commit="626c59e09f108e6e5eb6d2313efe28bf0e51ed03",
        time_sync=True,
        content_digest=compute_content_digest(TEST_VECTOR),
        status=Session.Status.READY,
        row_counts={"hand_command": 1200, "motor_status": 4200},
        file_sizes={"metadata": 980, "hand_command": 1234, "motor_status": 5678},
        file_hashes={"metadata": "c" * 64, "hand_command": "a" * 64, "motor_status": "b" * 64},
    )
    defaults.update(overrides)
    return Session.objects.create(**defaults)


class ContentDigestTests(TestCase):
    """[NFR-26] content_digest 계산 규칙. 로봇 exporter와 일치해야 한다."""

    #: 로봇 측 구현이 이 값을 재현해야 한다. 규칙 변경 시 양측 동시 갱신 필요.
    EXPECTED = "sha256:90f382c974222a860d54985629a9a135c3e360bbb343a5b07ff2316fc4bfd8f2"

    def test_test_vector_digest_is_stable(self):
        """교차 검증용 고정 벡터. 이 값이 바뀌면 로봇 측과 계약이 깨진다."""
        self.assertEqual(compute_content_digest(TEST_VECTOR), self.EXPECTED)

    def test_canonical_json_has_no_whitespace_and_sorted_keys(self):
        blob = canonical_json(TEST_VECTOR).decode("utf-8")
        self.assertNotIn(" ", blob)
        self.assertNotIn("\n", blob)
        parsed = json.loads(blob)
        self.assertEqual(list(parsed.keys()), sorted(parsed.keys()))
        self.assertEqual(
            list(parsed["files"]["hand_command"].keys()),
            sorted(parsed["files"]["hand_command"].keys()),
        )

    def test_excludes_exported_at_and_self(self):
        blob = json.loads(canonical_json(TEST_VECTOR).decode("utf-8"))
        self.assertNotIn("exported_at", blob)
        self.assertNotIn("content_digest", blob)

    def test_reexport_with_different_exported_at_yields_same_digest(self):
        """같은 rosbag2 수동 재생성 시 digest가 같아야 한다 (멱등성 기준)."""
        later = dict(TEST_VECTOR, exported_at="2099-01-01T00:00:00.000Z")
        self.assertEqual(compute_content_digest(later), self.EXPECTED)

    def test_content_change_is_detected(self):
        changed = copy.deepcopy(TEST_VECTOR)
        changed["files"]["hand_command"]["row_count"] = 1201
        self.assertNotEqual(compute_content_digest(changed), self.EXPECTED)

    def test_csv_hash_change_is_detected(self):
        changed = copy.deepcopy(TEST_VECTOR)
        changed["files"]["motor_status"]["sha256"] = "d" * 64
        self.assertNotEqual(compute_content_digest(changed), self.EXPECTED)

    def test_digests_match_is_case_insensitive(self):
        self.assertTrue(digests_match(self.EXPECTED.upper(), TEST_VECTOR))
        self.assertFalse(digests_match("sha256:" + "0" * 64, TEST_VECTOR))
        self.assertFalse(digests_match(None, TEST_VECTOR))

    def test_rejects_nan(self):
        with self.assertRaises(ValueError):
            canonical_json({"robot_id": ROBOT, "value": float("nan")})

    def test_sha256_file_matches_bytes(self):
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(b"session_id,stamp_sec\n1,2\n")
            name = fh.name
        try:
            self.assertEqual(sha256_file(name), sha256_bytes(b"session_id,stamp_sec\n1,2\n"))
        finally:
            os.unlink(name)


@override_settings(EC2_DATA_DIR=TEMP_DATA)
class StorageLayoutTests(TestCase):
    """[FR-52 / NFR-29] 저장 경로 규격과 경로 주입 방어."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_DATA, ignore_errors=True)
        super().tearDownClass()

    def test_final_dir_matches_spec_layout(self):
        path = storage.final_dir(ROBOT, SID)
        self.assertEqual(path, Path(TEMP_DATA) / "sessions" / ROBOT / SID)

    def test_staging_and_final_share_filesystem(self):
        """os.replace() 원자성은 동일 파일시스템에서만 보장된다."""
        self.assertEqual(
            os.stat(storage.ensure_layout() / "staging").st_dev,
            os.stat(storage.ensure_layout() / "sessions").st_dev,
        )

    def test_canonical_filenames(self):
        self.assertEqual(
            storage.canonical_filename(SID, "metadata"), f"session_{SID}_metadata.json"
        )
        self.assertEqual(
            storage.canonical_filename(SID, "hand_command"), f"session_{SID}_hand_command.csv"
        )
        self.assertEqual(
            storage.canonical_filename(SID, "motor_status"), f"session_{SID}_motor_status.csv"
        )

    def test_rejects_path_traversal_in_identifiers(self):
        for bad in ("../etc", "a/b", "a\\b", "", ".hidden", "a" * 51, "robot;rm"):
            with self.assertRaises(storage.UnsafeIdentifier, msg=bad):
                storage.final_dir(bad, SID)

    def test_rejects_non_numeric_session_id(self):
        for bad in ("../1", "12a", "", "1" * 21, "-1"):
            with self.assertRaises(storage.UnsafeIdentifier, msg=bad):
                storage.final_dir(ROBOT, bad)

    def test_rejects_unknown_file_kind(self):
        for bad in ("rosbag2", "../metadata", "db", ""):
            with self.assertRaises(storage.UnsafeIdentifier, msg=bad):
                storage.canonical_filename(SID, bad)


@override_settings(EC2_DATA_DIR=TEMP_DATA)
class AtomicCommitTests(TestCase):
    """[NFR-26] staging -> final 원자적 이동과 crash 재조정."""

    def setUp(self):
        storage.ensure_layout()
        storage.prepare_staging(ROBOT, SID)

    def tearDown(self):
        shutil.rmtree(Path(TEMP_DATA) / "staging", ignore_errors=True)
        shutil.rmtree(Path(TEMP_DATA) / "sessions", ignore_errors=True)

    def _stage_all(self):
        payloads = {
            "metadata": b'{"schema_version":1}',
            "hand_command": b"session_id,stamp_sec\n1,2\n",
            "motor_status": b"session_id,motor_id\n1,11\n",
            landmark_contract.KIND: b'{"frames":[]}',
        }
        for kind, body in payloads.items():
            upload = SimpleUploadedFile("client-name-ignored.bin", body)
            storage.stream_to_staging(upload, ROBOT, SID, kind)
        return payloads

    def test_stream_uses_server_generated_filename(self):
        """[FR-51] 클라이언트 파일명을 저장에 쓰지 않는다."""
        upload = SimpleUploadedFile("../../evil.sh", b"x")
        target, written = storage.stream_to_staging(upload, ROBOT, SID, "metadata")
        self.assertEqual(target.name, f"session_{SID}_metadata.json")
        self.assertEqual(written, 1)

    def test_commit_moves_every_staged_file(self):
        payloads = self._stage_all()
        moved = storage.commit_staging(ROBOT, SID)
        self.assertEqual(sorted(moved), sorted(storage.FILE_KINDS))

        final = storage.final_dir(ROBOT, SID)
        self.assertEqual(len(list(final.iterdir())), len(storage.FILE_KINDS))
        for kind, body in payloads.items():
            self.assertEqual(storage.final_path(ROBOT, SID, kind).read_bytes(), body)

    def test_commit_skips_absent_optional_landmark(self):
        """landmark 는 형식 미정이라 선택 part 다. 없어도 commit 이 성공해야 한다."""
        for kind in ("metadata", "hand_command", "motor_status"):
            storage.stream_to_staging(
                SimpleUploadedFile("x.bin", b"x"), ROBOT, SID, kind
            )
        moved = storage.commit_staging(ROBOT, SID)
        self.assertNotIn(landmark_contract.KIND, moved)
        self.assertEqual(len(moved), 3)

    def test_staging_removed_after_commit(self):
        self._stage_all()
        storage.commit_staging(ROBOT, SID)
        self.assertFalse(storage.staging_dir(ROBOT, SID).exists())

    def test_commit_fails_when_a_part_is_missing(self):
        upload = SimpleUploadedFile("x", b"{}")
        storage.stream_to_staging(upload, ROBOT, SID, "metadata")
        with self.assertRaises(FileNotFoundError):
            storage.commit_staging(ROBOT, SID)
        # final 경로에 부분 파일이 남지 않아야 한다
        self.assertFalse(any(storage.final_dir(ROBOT, SID).glob("*hand_command*")))

    def test_prepare_staging_clears_crashed_partial_files(self):
        """crash 뒤 재시도 시 이전 부분 파일이 정리되어야 한다."""
        leftover = storage.staging_dir(ROBOT, SID) / "leftover.part"
        leftover.write_bytes(b"partial")
        storage.prepare_staging(ROBOT, SID)
        self.assertFalse(leftover.exists())

    def test_discard_staging_is_safe_when_absent(self):
        storage.discard_staging(ROBOT, "999")  # 예외 없이 통과해야 한다


class SessionModelTests(TestCase):
    """[FR-46/47] unique key와 파생 필드."""

    def test_unique_robot_session(self):
        make_session()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_session()

    def test_same_session_id_allowed_for_other_robot(self):
        make_session()
        make_session(robot_id="THING-002")
        self.assertEqual(Session.objects.count(), 2)

    def test_duration_ms_is_derived_from_timestamps(self):
        session = make_session()
        self.assertEqual(session.duration_ms, 60000)

    def test_default_status_is_staging(self):
        session = Session(
            robot_id=ROBOT,
            session_id="1",
            started_at=_dt("2026-07-29T00:00:00.000Z"),
            ended_at=_dt("2026-07-29T00:00:01.000Z"),
            uploaded_at=_dt("2026-07-29T00:00:02.000Z"),
            result=Session.Result.SUCCESS,
            duration_ms=0,
            interface_commit="a" * 40,
            time_sync=True,
            content_digest="sha256:" + "0" * 64,
        )
        self.assertEqual(session.status, Session.Status.STAGING)
        self.assertFalse(session.is_public)

    def test_default_ordering_is_started_at_then_session_id_desc(self):
        make_session(session_id="100", started_at=_dt("2026-07-29T00:00:00.000Z"))
        make_session(session_id="200", started_at=_dt("2026-07-30T00:00:00.000Z"))
        make_session(session_id="300", started_at=_dt("2026-07-29T00:00:00.000Z"))
        ids = list(Session.objects.values_list("session_id", flat=True))
        self.assertEqual(ids, ["200", "300", "100"])

    def test_is_public_only_for_ready(self):
        self.assertTrue(make_session(status=Session.Status.READY).is_public)
        self.assertFalse(
            make_session(session_id="999", status=Session.Status.STAGING).is_public
        )
