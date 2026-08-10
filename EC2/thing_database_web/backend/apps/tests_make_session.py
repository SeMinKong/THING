"""make_session 이 만든 파일이 실제 업로드 검증을 통과하는지 본다.

생성기가 만든 파일이 검증기에 막히면 시험 데이터로 쓸 수 없다.
그래서 명령을 실행해 파일을 만들고, 그 파일을 진짜 endpoint 로 보낸다.
"""
import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings

from apps import landmark_contract
from apps.tests_upload import PLAIN_TOKEN, ROBOT, TEMP_DATA, TOKENS, UploadBaseTest

CTYPE = {"csv": "text/csv", "json": "application/json"}


@override_settings(EC2_DATA_DIR=TEMP_DATA, DEVICE_TOKENS=TOKENS,
                   SECURE_SSL_REDIRECT=False)
class MakeSessionTests(UploadBaseTest):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp(prefix="make-session-"))
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)

    def _generate(self, **kw):
        opts = dict(out=str(self.out), robot_id=ROBOT, samples=5,
                    landmark_frames=5, verbosity=0)
        opts.update(kw)
        call_command("make_session", **opts)
        parts = {}
        for path in sorted(self.out.iterdir()):
            kind = path.stem.split("_", 2)[2]          # session_{id}_{kind}
            ext = path.suffix.lstrip(".")
            parts[kind] = SimpleUploadedFile(
                path.name, path.read_bytes(), content_type=CTYPE[ext]
            )
        return parts

    def test_generated_four_files_pass_real_upload(self):
        parts = self._generate()
        self.assertEqual(set(parts), {"metadata", "hand_command",
                                      "motor_status", "landmark"})
        resp = self.post(parts)
        self.assertIn(resp.status_code, (200, 201), resp.content[:500])

    def test_generated_session_is_downloadable(self):
        parts = self._generate()
        sid = next(iter(parts.values())).name.split("_")[1]
        self.assertIn(self.post(parts).status_code, (200, 201))

        for kind in ("metadata", "hand_command", "motor_status", "landmark"):
            with self.subTest(kind=kind):
                got = self.client.get(f"/api/v1/sessions/{sid}/download/{kind}")
                self.assertEqual(got.status_code, 200, kind)

        body = self.client.get(f"/api/v1/sessions/{sid}").json()
        self.assertIn(landmark_contract.KIND, body["downloads"])

    def test_downloaded_landmark_is_valid_json(self):
        parts = self._generate(landmark_frames=12)
        sid = next(iter(parts.values())).name.split("_")[1]
        self.post(parts)

        got = self.client.get(f"/api/v1/sessions/{sid}/download/landmark")
        blob = b"".join(got.streaming_content)
        parsed, count = landmark_contract.validate_payload(blob)
        self.assertEqual(len(parsed["frames"]), 12)
        self.assertEqual(len(parsed["frames"][0]["landmarks"]), 21)

    def test_no_landmark_flag_makes_three_files(self):
        parts = self._generate(no_landmark=True)
        self.assertEqual(set(parts), {"metadata", "hand_command", "motor_status"})
        self.assertIn(self.post(parts).status_code, (200, 201))
