"""
격리 uploader 데몬 (S15P11C103-125).

logger와 **별도 프로세스**로 Jetson에서 기동한다. exporter가 private Unix socket으로 넘긴
완료 canonical 4파일 manifest만 받아, 파일을 읽기 전용으로 재검증한 뒤 EC2 Django 업로드
API로 한 번 HTTPS POST한다. rclpy·rosbag2·exporter를 import하지 않는 순수 Python 프로세스다.

흐름: 소켓 수신 → manifest 파싱 → 파일 재검증(격리) → metadata 파싱 → EC2 업로드 →
      응답 대조 → ACK. 실행 중 상태는 메모리에만, 재시도·영속 없음(FR-46/NFR-28/29).

console_scripts: `uploader = thing_logger.uploader:main`
계약·설정·예외는 thing_logger.uploader_contract 참조.
"""
from __future__ import annotations

import enum
import hashlib
import json
import logging
import os
import signal
import socket
import sys

from thing_logger.uploader_contract import (
    Ack,
    Config,
    ConfigError,
    EXPECTED_FILE_KINDS,
    Manifest,
    ManifestError,
    UploaderError,
    UploadRejected,
    VerificationError,
    load_config,
    parse_manifest,
    serialize_ack,
)

log = logging.getLogger("thing_logger.uploader")

_SHA_CHUNK = 1024 * 1024
_METADATA_MAX_BYTES = 256 * 1024  # 6.5절 metadata part 상한
_RECV_CHUNK = 65536
_MAX_LINE = 1024 * 1024

#: kind → multipart content-type. EC2 _check_content_types와 일치해야 415가 안 난다.
CONTENT_TYPES = {
    "metadata": "application/json",
    "hand_command": "text/csv",
    "motor_status": "text/csv",
    "landmark": "application/json",
}


# ══════════════════════════════════════════════════════════════════════════
# 1) 파일 재검증 (격리 보안 경계) + metadata 파싱
# ══════════════════════════════════════════════════════════════════════════
# uploader는 exporter를 신뢰하지 않고 독립 재검증한다. 디렉터리는 읽기 전용으로만 접근하며
# 어떤 파일도 수정·삭제하지 않는다(임시 파일 정리는 exporter 책임).

def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_SHA_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest_files(manifest: Manifest) -> None:
    """정확히 4종인지 + 각 파일의 정규성·디렉터리 이탈·크기·SHA-256을 검증한다."""
    if set(manifest.files) != set(EXPECTED_FILE_KINDS):
        raise VerificationError(
            f"정확히 4개 파일이어야 한다: {sorted(EXPECTED_FILE_KINDS)}"
        )

    directory = manifest.directory
    if not os.path.isabs(directory):
        raise VerificationError("directory가 절대경로가 아니다")
    if os.path.islink(directory) or not os.path.isdir(directory):
        raise VerificationError("directory가 정상 디렉터리가 아니다")
    real_dir = os.path.realpath(directory)

    for kind, mf in manifest.files.items():
        path = os.path.join(directory, mf.filename)
        if os.path.islink(path) or not os.path.isfile(path):
            raise VerificationError(f"{kind}: 정규 파일이 아니다")
        if os.path.realpath(path) != os.path.join(real_dir, mf.filename):
            raise VerificationError(f"{kind}: 완료 디렉터리를 벗어났다")
        if os.path.getsize(path) != mf.size_bytes:
            raise VerificationError(f"{kind}: 크기 불일치")
        if sha256_file(path).lower() != mf.sha256.lower():
            raise VerificationError(f"{kind}: SHA-256 불일치")


def read_metadata(manifest: Manifest) -> dict:
    """
    metadata.json에서 robot_id·session_id·data_version·content_digest를 얻는다.

    Idempotency-Key 구성과 EC2 응답 대조에 쓴다. manifest 값과 교차 확인한다.
    """
    mf = manifest.files["metadata"]  # verify에서 4종 보장됨
    path = os.path.join(manifest.directory, mf.filename)
    if mf.size_bytes > _METADATA_MAX_BYTES:
        raise VerificationError("metadata가 상한을 넘는다")
    try:
        with open(path, "rb") as fh:
            meta = json.loads(fh.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("metadata.json을 읽을 수 없다") from exc
    if not isinstance(meta, dict):
        raise VerificationError("metadata.json 최상위가 object가 아니다")
    for key in ("robot_id", "session_id", "data_version", "content_digest"):
        if key not in meta:
            raise VerificationError(f"metadata.{key} 누락")
    if str(meta["session_id"]) != manifest.session_id:
        raise VerificationError("metadata.session_id가 manifest와 다르다")
    if str(meta["content_digest"]).lower() != manifest.content_digest.lower():
        raise VerificationError("metadata.content_digest가 manifest와 다르다")
    return meta


# ══════════════════════════════════════════════════════════════════════════
# 2) EC2 업로드 (multipart POST + 응답 대조)
# ══════════════════════════════════════════════════════════════════════════
# 계약: EC2/thing_database_web/backend/apps/upload_views.py (POST /api/v1/uploads/sessions)
#   성공 201(신규)/200(멱등), 409 충돌, 4xx/5xx/timeout/TLS 실패(재시도 없음).
# 토큰은 로그·예외 메시지에 넣지 않는다.

def _idempotency_key(robot_id, session_id, data_version, content_digest) -> str:
    return f"{robot_id}:{session_id}:{data_version}:{content_digest}"


def upload_to_ec2(manifest: Manifest, meta: dict, config: Config) -> tuple[bool, str]:
    """4파일을 multipart로 POST하고 (accepted, reason)을 반환한다."""
    import requests  # lazy import: logger 노드는 requests가 필요 없다

    headers = {
        "Authorization": f"Bearer {config.device_token}",
        "Idempotency-Key": _idempotency_key(
            meta["robot_id"], meta["session_id"], meta["data_version"],
            manifest.content_digest,
        ),
    }

    opened = []
    try:
        multipart = {}
        for kind, mf in manifest.files.items():
            content_type = CONTENT_TYPES.get(kind)
            if content_type is None:
                raise UploadRejected(f"알 수 없는 파일 종류: {kind}")
            fh = open(os.path.join(manifest.directory, mf.filename), "rb")
            opened.append(fh)
            multipart[kind] = (mf.filename, fh, content_type)
        try:
            resp = requests.post(
                config.ec2_upload_url,
                headers=headers,
                files=multipart,
                timeout=(config.connect_timeout_s, config.read_timeout_s),
                verify=config.tls_verify,
            )
        except requests.RequestException as exc:
            raise UploadRejected(f"전송 실패: {type(exc).__name__}") from exc
    finally:
        for fh in opened:
            fh.close()

    return _judge_response(resp, manifest, meta)


def _judge_response(resp, manifest: Manifest, meta: dict) -> tuple[bool, str]:
    status = resp.status_code
    if status in (200, 201):
        try:
            body = resp.json()
        except ValueError as exc:
            raise UploadRejected(f"성공 응답이 JSON이 아니다 (HTTP {status})") from exc
        _verify_response_body(body, manifest, meta)
        return True, ("created" if status == 201 else "idempotent")
    if status == 409:
        return False, "content conflict (409)"
    return False, f"EC2 거부 (HTTP {status})"  # 401/413/415/422/429/5xx 등


def _verify_response_body(body, manifest: Manifest, meta: dict) -> None:
    """응답 핵심 값이 요청과 일치하는지 확인(티켓 125 완료조건)."""
    if not isinstance(body, dict):
        raise UploadRejected("성공 응답 형식이 잘못됐다")
    if str(body.get("session_id")) != manifest.session_id:
        raise UploadRejected("응답 session_id 불일치")
    if str(body.get("content_digest", "")).lower() != manifest.content_digest.lower():
        raise UploadRejected("응답 content_digest 불일치")
    if body.get("data_version") != meta["data_version"]:
        raise UploadRejected("응답 data_version 불일치")
    if body.get("status") != "READY":
        raise UploadRejected(f"응답 status가 READY가 아니다: {body.get('status')}")


# ══════════════════════════════════════════════════════════════════════════
# 3) 파이프라인 (수신 → 검증 → 업로드 → ACK)
# ══════════════════════════════════════════════════════════════════════════

class UploadState(str, enum.Enum):
    RECEIVED = "RECEIVED"
    VERIFYING = "VERIFYING"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    FAILED = "FAILED"


def handle_manifest_line(manifest_line: bytes, config: Config) -> bytes:
    """
    받은 manifest 한 줄을 처리해 ACK 한 줄을 만든다. 어떤 실패도 FAILURE ACK로 귀결한다.

    session_id를 못 얻으면(ManifestError) ACK를 만들 수 없어 예외를 위로 올린다
    → 서버가 ACK 없이 연결을 닫고, 클라이언트가 실패로 인지한다.
    """
    manifest = parse_manifest(manifest_line)  # ManifestError는 위로 전파
    session_id = manifest.session_id
    state = UploadState.RECEIVED
    try:
        state = UploadState.VERIFYING
        verify_manifest_files(manifest)
        meta = read_metadata(manifest)

        state = UploadState.UPLOADING
        accepted, reason = upload_to_ec2(manifest, meta, config)

        state = UploadState.UPLOADED if accepted else UploadState.FAILED
        ack = Ack(session_id, accepted, reason)
    except UploaderError as exc:
        state = UploadState.FAILED
        ack = Ack(session_id, False, str(exc))

    # 임시 파일 정리는 exporter 책임(uploader는 읽기 전용) — 여기서 삭제하지 않는다.
    log.info("session=%s state=%s accepted=%s", session_id, state.value, ack.accepted)
    return serialize_ack(ack)


# ══════════════════════════════════════════════════════════════════════════
# 4) AF_UNIX 소켓 서버
# ══════════════════════════════════════════════════════════════════════════
# MVP는 한 번에 하나씩 순차 처리한다(기록은 MIMIC에서 한 번에 하나만 허용 → 동시 세션 없음).

class UnixSocketServer:
    def __init__(self, config: Config):
        self._config = config
        self._sock: socket.socket | None = None

    def _bind(self) -> None:
        path = self._config.socket_path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)  # 보통 마운트로 이미 존재
        try:
            if os.path.exists(path):
                os.unlink(path)  # 이전 stale 소켓 제거
        except OSError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(path)
        os.chmod(path, self._config.socket_mode)
        sock.listen(8)
        self._sock = sock
        log.info("listening on %s (mode=%s)", path, oct(self._config.socket_mode))

    def serve_forever(self) -> None:
        if self._sock is None:
            self._bind()
        assert self._sock is not None
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break  # close()로 소켓이 닫힘
            with conn:
                self._serve_conn(conn)

    def _serve_conn(self, conn: socket.socket) -> None:
        conn.settimeout(self._config.read_timeout_s)
        try:
            line = self._recv_line(conn)
        except (OSError, ManifestError) as exc:
            log.warning("manifest 수신 실패: %s", type(exc).__name__)
            return
        try:
            ack = handle_manifest_line(line, self._config)
        except ManifestError:
            log.warning("잘못된 manifest — ACK 없이 종료")  # session_id 불명
            return
        try:
            conn.sendall(ack)
        except OSError as exc:
            log.warning("ACK 전송 실패: %s", type(exc).__name__)

    @staticmethod
    def _recv_line(conn: socket.socket) -> bytes:
        buf = bytearray()
        while len(buf) <= _MAX_LINE:
            chunk = conn.recv(_RECV_CHUNK)
            if not chunk:
                break
            buf.extend(chunk)
            newline = buf.find(b"\n")
            if newline >= 0:
                return bytes(buf[:newline])
        if len(buf) > _MAX_LINE:
            raise ManifestError("manifest 한 줄이 상한을 넘었다")
        return bytes(buf)  # 개행 없이 끝남 — 파서가 형식 검증

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            finally:
                try:
                    os.unlink(self._config.socket_path)
                except OSError:
                    pass


# ══════════════════════════════════════════════════════════════════════════
# 5) 진입점
# ══════════════════════════════════════════════════════════════════════════

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config()
    except ConfigError as exc:
        log.error("설정 오류로 기동 중단: %s", exc)
        return 2

    log.info("uploader 기동: %s", config.redacted())
    server = UnixSocketServer(config)

    def _shutdown(signum, _frame):
        log.info("신호 수신(%s) — 종료", signum)
        server.close()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        server.serve_forever()
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
