"""완성된 canonical 파일을 격리 uploader에 인계한다."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import socket
from typing import Mapping

from thing_logger.export_schema import canonical_filenames
from thing_logger.exporter import ExportResult


HANDOFF_VERSION = 1
REQUIRED_FILE_KINDS = frozenset({
    'metadata',
    'hand_command',
    'motor_status',
    'landmark',
})
CONTENT_DIGEST_PATTERN = re.compile(r'^sha256:[0-9a-f]{64}$')
MAX_ACK_BYTES = 64 * 1024


class UploaderHandoffError(RuntimeError):
    """격리 uploader 인계 또는 ACK 검증 실패를 나타낸다."""


@dataclass(frozen=True)
class UploadAck:
    """격리 uploader가 반환한 작업 수락 결과를 나타낸다."""

    session_id: int
    accepted: bool
    reason: str


class UnixSocketUploaderClient:
    """private Unix socket으로 완료 파일 manifest만 전달한다."""

    def __init__(self, socket_path: str, timeout_seconds: float = 10.0):
        """Socket 경로와 응답 제한시간을 설정한다."""
        if not socket_path or not Path(socket_path).is_absolute():
            raise UploaderHandoffError(
                'uploader socket path must be absolute'
            )
        if timeout_seconds <= 0:
            raise UploaderHandoffError('uploader timeout must be positive')
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    def handoff(self, result: ExportResult) -> UploadAck:
        """검증된 manifest를 보내고 같은 Session ID의 ACK를 받는다."""
        manifest = build_upload_manifest(result)
        payload = json.dumps(
            manifest,
            ensure_ascii=False,
            separators=(',', ':'),
        ).encode('utf-8') + b'\n'

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(self.timeout_seconds)
                client.connect(self.socket_path)
                client.sendall(payload)
                response = _receive_line(client)
        except (OSError, TimeoutError) as error:
            raise UploaderHandoffError(
                f'uploader socket request failed: {error}'
            ) from error

        ack = _parse_ack(response)
        if ack.session_id != result.session_id:
            raise UploaderHandoffError('uploader ACK session ID mismatch')
        if not ack.accepted:
            raise UploaderHandoffError(
                f'uploader rejected export: {ack.reason}'
            )
        return ack


def build_upload_manifest(result: ExportResult) -> dict:
    """완료된 네 파일만 포함하는 uploader manifest를 만든다."""
    if not isinstance(result, ExportResult):
        raise UploaderHandoffError('export result type is invalid')
    if result.session_id <= 0 or result.session_id >= 2**63:
        raise UploaderHandoffError('export session ID is invalid')
    if not CONTENT_DIGEST_PATTERN.fullmatch(result.content_digest):
        raise UploaderHandoffError('content digest is invalid')
    if set(result.files) != REQUIRED_FILE_KINDS:
        raise UploaderHandoffError(
            'export result must contain exactly four canonical files'
        )

    directory_path = Path(result.directory)
    if directory_path.is_symlink() or not directory_path.is_dir():
        raise UploaderHandoffError('export directory is invalid')
    directory = directory_path.resolve()

    expected_filenames = canonical_filenames(result.session_id)
    ordered_files = {}
    for file_kind in (
        'metadata', 'hand_command', 'motor_status', 'landmark',
    ):
        info = result.files[file_kind]
        path = Path(info.path)
        if path.name.endswith('.part'):
            raise UploaderHandoffError('.part file cannot be handed off')
        if not path.is_absolute():
            raise UploaderHandoffError('export file path must be absolute')
        if path.is_symlink() or not path.is_file():
            raise UploaderHandoffError('export file is not a regular file')
        if info.filename != expected_filenames[file_kind]:
            raise UploaderHandoffError('export filename is not canonical')
        resolved = path.resolve()
        if resolved.parent != directory:
            raise UploaderHandoffError(
                'export file escapes the completed directory'
            )
        if resolved.name != info.filename:
            raise UploaderHandoffError('export filename mismatch')
        if resolved.stat().st_size != info.size_bytes:
            raise UploaderHandoffError('export file size mismatch')
        if (
            not isinstance(info.row_count, int)
            or isinstance(info.row_count, bool)
            or info.row_count < 0
        ):
            raise UploaderHandoffError('export row count is invalid')
        if not re.fullmatch(r'[0-9a-f]{64}', info.sha256):
            raise UploaderHandoffError('export file SHA-256 is invalid')

        ordered_files[file_kind] = {
            'filename': info.filename,
            'size_bytes': info.size_bytes,
            'row_count': info.row_count,
            'sha256': info.sha256,
        }

    return {
        'version': HANDOFF_VERSION,
        'session_id': str(result.session_id),
        'content_digest': result.content_digest,
        'directory': str(directory),
        'files': ordered_files,
    }


def _receive_line(client: socket.socket) -> bytes:
    """크기가 제한된 newline 종료 ACK 한 개를 수신한다."""
    chunks = bytearray()
    while len(chunks) <= MAX_ACK_BYTES:
        chunk = client.recv(min(4096, MAX_ACK_BYTES + 1 - len(chunks)))
        if not chunk:
            break
        chunks.extend(chunk)
        newline_index = chunks.find(b'\n')
        if newline_index >= 0:
            return bytes(chunks[:newline_index])
    if len(chunks) > MAX_ACK_BYTES:
        raise UploaderHandoffError('uploader ACK is too large')
    raise UploaderHandoffError('uploader ACK is incomplete')


def _parse_ack(payload: bytes) -> UploadAck:
    """JSON ACK의 필드·타입·Session ID를 검증한다."""
    try:
        parsed: Mapping = json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UploaderHandoffError('uploader ACK is not valid JSON') from error
    if not isinstance(parsed, dict) or set(parsed) != {
        'session_id', 'accepted', 'reason',
    }:
        raise UploaderHandoffError('uploader ACK fields are invalid')
    if not isinstance(parsed['session_id'], str):
        raise UploaderHandoffError('uploader ACK session ID is invalid')
    try:
        session_id = int(parsed['session_id'])
    except ValueError as error:
        raise UploaderHandoffError(
            'uploader ACK session ID is invalid'
        ) from error
    if not isinstance(parsed['accepted'], bool):
        raise UploaderHandoffError('uploader ACK accepted is invalid')
    if not isinstance(parsed['reason'], str):
        raise UploaderHandoffError('uploader ACK reason is invalid')
    return UploadAck(session_id, parsed['accepted'], parsed['reason'])
