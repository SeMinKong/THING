"""Exporter와 격리 uploader 사이의 Unix socket 계약을 검증한다."""

import hashlib
import json
from pathlib import Path

import pytest

from thing_logger.exporter import ExportFileInfo
from thing_logger.exporter import ExportResult
from thing_logger.uploader_handoff import build_upload_manifest
from thing_logger.uploader_handoff import UnixSocketUploaderClient
from thing_logger.uploader_handoff import UploaderHandoffError


def make_export_result(tmp_path):
    """실제 canonical 파일을 가진 ExportResult 테스트 객체를 만든다."""
    directory = tmp_path / '123'
    directory.mkdir(parents=True)
    files = {}
    extensions = {
        'metadata': 'json',
        'hand_command': 'csv',
        'motor_status': 'csv',
        'landmark': 'json',
    }
    for file_kind, extension in extensions.items():
        filename = f'session_123_{file_kind}.{extension}'
        path = directory / filename
        content = file_kind.encode('utf-8')
        path.write_bytes(content)
        files[file_kind] = ExportFileInfo(
            path=str(path),
            filename=filename,
            size_bytes=len(content),
            row_count=1,
            sha256=hashlib.sha256(content).hexdigest(),
        )
    return ExportResult(
        session_id=123,
        directory=str(directory),
        content_digest='sha256:' + 'a' * 64,
        files=files,
    )


class FakeClientSocket:
    """Unix socket client의 송수신 동작을 메모리에서 흉내 낸다."""

    def __init__(self, ack):
        """ACK와 호출 결과 저장 공간을 준비한다."""
        self.response = json.dumps(ack).encode('utf-8') + b'\n'
        self.timeout = None
        self.socket_path = None
        self.sent = None

    def __enter__(self):
        """Context manager 진입 시 자신을 반환한다."""
        return self

    def __exit__(self, exception_type, exception, traceback):
        """Context manager 종료 시 별도 자원을 해제하지 않는다."""
        return False

    def settimeout(self, timeout):
        """설정된 제한시간을 기록한다."""
        self.timeout = timeout

    def connect(self, socket_path):
        """연결 대상 경로를 기록한다."""
        self.socket_path = socket_path

    def sendall(self, payload):
        """전송된 manifest bytes를 기록한다."""
        self.sent = payload

    def recv(self, _size):
        """준비된 ACK를 한 번 반환한다."""
        response, self.response = self.response, b''
        return response


def install_fake_socket(monkeypatch, ack):
    """Socket 생성 함수를 가짜 client로 바꾸고 객체를 반환한다."""
    client = FakeClientSocket(ack)
    monkeypatch.setattr(
        'thing_logger.uploader_handoff.socket.socket',
        lambda *_args: client,
    )
    return client


def test_build_upload_manifest_contains_exactly_four_final_files(tmp_path):
    """manifest에 검증된 네 canonical 파일 정보만 포함한다."""
    result = make_export_result(tmp_path)

    manifest = build_upload_manifest(result)

    assert manifest['version'] == 1
    assert manifest['session_id'] == '123'
    assert set(manifest['files']) == {
        'metadata', 'hand_command', 'motor_status', 'landmark',
    }
    assert not any(
        file_info['filename'].endswith('.part')
        for file_info in manifest['files'].values()
    )


def test_build_upload_manifest_rejects_part_and_symlink(tmp_path):
    """미완성 파일과 symlink를 uploader에 전달하지 않는다."""
    result = make_export_result(tmp_path)
    metadata = result.files['metadata']
    part_info = ExportFileInfo(
        path=metadata.path + '.part',
        filename=metadata.filename + '.part',
        size_bytes=metadata.size_bytes,
        row_count=metadata.row_count,
        sha256=metadata.sha256,
    )
    Path(part_info.path).write_bytes(b'metadata')
    part_files = dict(result.files)
    part_files['metadata'] = part_info

    with pytest.raises(UploaderHandoffError, match=r'\.part'):
        build_upload_manifest(ExportResult(
            result.session_id,
            result.directory,
            result.content_digest,
            part_files,
        ))

    Path(part_info.path).unlink()
    symlink = Path(result.directory) / 'metadata-link.json'
    symlink.symlink_to(metadata.path)
    symlink_info = ExportFileInfo(
        path=str(symlink),
        filename=symlink.name,
        size_bytes=metadata.size_bytes,
        row_count=metadata.row_count,
        sha256=metadata.sha256,
    )
    symlink_files = dict(result.files)
    symlink_files['metadata'] = symlink_info
    with pytest.raises(UploaderHandoffError, match='regular file'):
        build_upload_manifest(ExportResult(
            result.session_id,
            result.directory,
            result.content_digest,
            symlink_files,
        ))


def test_build_upload_manifest_rejects_noncanonical_filename(tmp_path):
    """파일 종류가 맞아도 canonical 파일명이 아니면 거부한다."""
    result = make_export_result(tmp_path)
    metadata = result.files['metadata']
    renamed_path = Path(result.directory) / 'other_metadata.json'
    Path(metadata.path).rename(renamed_path)
    renamed_files = dict(result.files)
    renamed_files['metadata'] = ExportFileInfo(
        path=str(renamed_path),
        filename=renamed_path.name,
        size_bytes=metadata.size_bytes,
        row_count=metadata.row_count,
        sha256=metadata.sha256,
    )

    with pytest.raises(UploaderHandoffError, match='not canonical'):
        build_upload_manifest(ExportResult(
            result.session_id,
            result.directory,
            result.content_digest,
            renamed_files,
        ))


def test_unix_socket_client_sends_manifest_and_accepts_matching_ack(
    tmp_path, monkeypatch,
):
    """완료 manifest를 보내고 같은 Session ID의 성공 ACK를 수락한다."""
    socket_path = tmp_path / 'uploader.sock'
    fake_socket = install_fake_socket(monkeypatch, {
        'session_id': '123',
        'accepted': True,
        'reason': '',
    })
    result = make_export_result(tmp_path / 'export')
    client = UnixSocketUploaderClient(str(socket_path), 1.0)

    ack = client.handoff(result)

    assert ack.session_id == 123
    assert ack.accepted is True
    received = json.loads(fake_socket.sent.decode('utf-8'))
    assert received['content_digest'] == result.content_digest
    assert set(received['files']) == {
        'metadata', 'hand_command', 'motor_status', 'landmark',
    }
    assert fake_socket.socket_path == str(socket_path)
    assert fake_socket.timeout == 1.0


def test_unix_socket_client_rejects_negative_or_mismatched_ack(
    tmp_path, monkeypatch,
):
    """Uploader 거부와 다른 Session ID 응답을 성공으로 처리하지 않는다."""
    result = make_export_result(tmp_path / 'first-export')
    rejected_path = tmp_path / 'rejected.sock'
    install_fake_socket(monkeypatch, {
        'session_id': '123',
        'accepted': False,
        'reason': 'upload_failed',
    })
    with pytest.raises(UploaderHandoffError, match='upload_failed'):
        UnixSocketUploaderClient(str(rejected_path), 1.0).handoff(result)

    mismatch_path = tmp_path / 'mismatch.sock'
    install_fake_socket(monkeypatch, {
        'session_id': '999',
        'accepted': True,
        'reason': '',
    })
    with pytest.raises(UploaderHandoffError, match='session ID mismatch'):
        UnixSocketUploaderClient(str(mismatch_path), 1.0).handoff(result)
