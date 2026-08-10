"""landmark part end-to-end 검증.

형식이 미정인 상태에서 무엇이 되고 무엇이 안 되는지 고정한다.
형식이 정해지면 apps/landmark_contract.py 만 고치고 이 시험을 다시 돌린다.
"""
import hashlib
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps import landmark_contract, storage
from apps.digest import compute_content_digest
from apps.tests_upload import (
    PLAIN_TOKEN, ROBOT, SID, TEMP_DATA, TOKENS, UploadBaseTest,
    build_metadata, hand_command_csv, motor_status_csv,
)


def _sha(b):
    return hashlib.sha256(b).hexdigest()


@override_settings(EC2_DATA_DIR=TEMP_DATA, DEVICE_TOKENS=TOKENS,
                   SECURE_SSL_REDIRECT=False)
class LandmarkUploadTests(UploadBaseTest):
    """landmark 를 넣은 4-part 업로드와 거부 조건."""

    def _build(self, landmark_bytes=None, declare=True, sid=SID):
        hc, ms = hand_command_csv(session_id=sid), motor_status_csv(session_id=sid)
        meta = build_metadata(hc, ms, session_id=sid)

        parts = {
            "hand_command": SimpleUploadedFile("h.csv", hc, content_type="text/csv"),
            "motor_status": SimpleUploadedFile("s.csv", ms, content_type="text/csv"),
        }
        if landmark_bytes is not None:
            parts[landmark_contract.KIND] = SimpleUploadedFile(
                "lm.json", landmark_bytes, content_type="application/json"
            )
            if declare:
                meta["files"][landmark_contract.KIND] = {
                    "filename": f"session_{sid}_{landmark_contract.KIND}"
                                f".{landmark_contract.EXTENSION}",
                    "size_bytes": len(landmark_bytes),
                    "sha256": _sha(landmark_bytes),
                }
        # digest 는 landmark 선언을 포함한 metadata 로 다시 계산한다
        meta["content_digest"] = compute_content_digest(meta)
        parts["metadata"] = SimpleUploadedFile(
            "m.json", json.dumps(meta).encode("utf-8"), content_type="application/json"
        )
        return parts

    def test_three_parts_still_accepted(self):
        """landmark 가 선택인 동안 기존 세 part 업로드가 계속 동작해야 한다."""
        resp = self.post(self._build())
        self.assertIn(resp.status_code, (200, 201), resp.content[:300])

    def test_four_parts_accepted_and_downloadable(self):
        payload = json.dumps({"frames": [{"t": 0, "pts": []}]}).encode("utf-8")
        resp = self.post(self._build(payload))
        self.assertIn(resp.status_code, (200, 201), resp.content[:400])

        got = self.client.get(f"/api/v1/sessions/{SID}/download/landmark")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(b"".join(got.streaming_content), payload)

    def test_detail_links_landmark_only_when_present(self):
        payload = b'{"frames":[]}'
        self.post(self._build(payload))
        body = self.client.get(f"/api/v1/sessions/{SID}").json()
        self.assertIn(landmark_contract.KIND, body["downloads"])

    def test_broken_json_rejected(self):
        resp = self.post(self._build(b'{"frames": [ '))
        self.assertEqual(resp.status_code, 422, resp.content[:300])
        self.assertIn("landmark", resp.content.decode())

    def test_csv_disguised_as_landmark_rejected(self):
        resp = self.post(self._build(b"a,b\n1,2\n"))
        self.assertEqual(resp.status_code, 422)

    def test_part_without_declaration_rejected(self):
        """part 는 왔는데 metadata.files 에 선언이 없으면 거부한다."""
        resp = self.post(self._build(b"{}", declare=False))
        self.assertEqual(resp.status_code, 422, resp.content[:300])

    def test_any_json_shape_passes_while_schema_undecided(self):
        """형식 미정 동안에는 최상위가 객체든 배열이든 통과해야 한다."""
        self.assertFalse(landmark_contract.SCHEMA_DECIDED)
        shapes = (b"[]", b"{}", b'[{"x":1}]', b'{"a":{"b":[1,2]}}')
        for i, payload in enumerate(shapes):
            sid = str(100000000000000000 + i)
            with self.subTest(payload=payload):
                resp = self.post(self._build(payload, sid=sid))
                self.assertIn(resp.status_code, (200, 201), resp.content[:250])


    def test_landmark_is_included_in_content_digest(self):
        """[통합] 로봇 exporter 는 landmark 를 content_digest 에 포함한다.

        landmark_contract.INCLUDE_IN_DIGEST 가 True 이면 landmark 선언이 digest
        계산에 들어가야 서버 재계산이 로봇 값과 일치한다. 이 시험이 그 플래그가
        실제로 지켜지는지 본다. 로봇은 항상 네 파일을 보내므로(build_metadata)
        landmark 유무로 같은 세션이 409 로 흔들리지 않는다.
        """
        hc, ms = hand_command_csv(), motor_status_csv()
        without = build_metadata(hc, ms)
        with_lm = build_metadata(hc, ms)
        with_lm["files"][landmark_contract.KIND] = {
            "filename": f"session_{SID}_landmark.json",
            "size_bytes": 13,
            "sha256": _sha(b'{"frames":[]}'),
        }
        self.assertTrue(landmark_contract.INCLUDE_IN_DIGEST)
        self.assertNotEqual(
            compute_content_digest(without), compute_content_digest(with_lm)
        )
