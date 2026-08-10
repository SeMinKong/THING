"""
격리 uploader의 계약·설정·예외 (S15P11C103-125).

여기 담긴 것:
  - 예외 계층
  - 환경변수 기반 Config
  - exporter ↔ uploader 소켓 계약(manifest 파싱 / ACK 직렬화)

소켓 계약의 단일 기준은 robot 쪽 클라이언트 thing_logger/uploader_handoff.py 다. 이 모듈은
격리를 위해 그 클라이언트(및 exporter·rosbag2)를 import하지 않고 계약을 최소로 다시 정의한다.
계약을 바꾸면 양쪽을 같이 바꿔야 한다.

파일 개수: **정확히 4종 확정** — metadata JSON, HandCommand CSV, MotorStatus CSV,
LandMark JSON. (팀 확정 2026-08-05)
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Mapping


# ══════════════════════════════════════════════════════════════════════════
# 예외
# ══════════════════════════════════════════════════════════════════════════
# 어떤 실패도 로봇 제어·안전·rosbag2를 중단시키지 않는다(FR-46/NFR-28). 대부분의 실패는
# FAILURE ACK와 사유로 귀결되고 진단 로그에만 남는다. 예외 메시지·로그에는 토큰이나
# 내부 경로 같은 민감정보를 넣지 않는다(FR-51/NFR-27).

class UploaderError(Exception):
    """uploader 최상위 예외."""


class ConfigError(UploaderError):
    """기동에 필요한 설정(env)이 없거나 잘못됨. 프로세스는 기동을 중단한다."""


class ManifestError(UploaderError):
    """소켓으로 받은 manifest가 계약 위반. session_id를 못 얻으면 ACK 없이 연결 종료."""


class VerificationError(UploaderError):
    """완료 파일 재검증 실패(파일 집합·파일명·symlink·크기·sha256·metadata 불일치)."""


class UploadRejected(UploaderError):
    """EC2 거부 또는 전송/응답 검증 실패(409/4xx/5xx/timeout/TLS 등). 재시도 없음."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ══════════════════════════════════════════════════════════════════════════
# 설정 (환경변수 주입 — 컨테이너가 제공)
# ══════════════════════════════════════════════════════════════════════════
# 주의(명세 미고정): 아래 환경변수 '이름'과 소켓 mode는 명세 확정값이 아니다(제안).
# 정민 님 컨테이너 설정(EnvironmentFile)과 같은 이름을 쓰도록 합의 후 확정한다.
# 명세 고정: 소켓 경로 기본값, 토큰을 0600 env로 주입, 외부는 Bearer Token HTTPS.

ENV_SOCKET_PATH = "THING_UPLOADER_SOCKET"
ENV_SOCKET_MODE = "THING_UPLOADER_SOCKET_MODE"
ENV_EC2_URL = "THING_EC2_UPLOAD_URL"
ENV_TOKEN = "THING_UPLOADER_TOKEN"
ENV_CONNECT_TIMEOUT = "THING_UPLOADER_CONNECT_TIMEOUT_S"
ENV_READ_TIMEOUT = "THING_UPLOADER_READ_TIMEOUT_S"
ENV_TLS_VERIFY = "THING_UPLOADER_TLS_VERIFY"
ENV_CONFIG_FILE = "THING_UPLOADER_ENV_FILE"

DEFAULT_SOCKET_PATH = "/run/thing-uploader/uploader.sock"
DEFAULT_SOCKET_MODE = "0660"  # 배포 선택값(명세 고정 아님) — exporter가 connect 가능해야
DEFAULT_CONFIG_FILE = "/etc/thing-uploader.env"
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class Config:
    socket_path: str
    socket_mode: int
    ec2_upload_url: str
    device_token: str
    connect_timeout_s: float
    read_timeout_s: float
    tls_verify: bool

    def redacted(self) -> dict:
        """진단 로그용. 토큰은 절대 노출하지 않는다(FR-51/NFR-27)."""
        return {
            "socket_path": self.socket_path,
            "socket_mode": oct(self.socket_mode),
            "ec2_upload_url": self.ec2_upload_url,
            "device_token": "***redacted***",
            "connect_timeout_s": self.connect_timeout_s,
            "read_timeout_s": self.read_timeout_s,
            "tls_verify": self.tls_verify,
        }


def _read_env_file(path: str, *, required: bool) -> dict:
    """Read a simple KEY=VALUE file without executing it as shell code."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        if required:
            raise ConfigError("uploader env 파일을 찾을 수 없다") from None
        return {}
    except OSError as exc:
        raise ConfigError("uploader env 파일을 읽을 수 없다") from exc

    values = {}
    for line_number, original in enumerate(lines, start=1):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(
                f"uploader env 파일 {line_number}행 형식이 잘못됐다"
            )
        key, value = (part.strip() for part in line.split("=", 1))
        if not ENV_NAME_RE.fullmatch(key):
            raise ConfigError(
                f"uploader env 파일 {line_number}행 이름이 잘못됐다"
            )
        if value[:1] in {'"', "'"}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigError(
                    f"uploader env 파일 {line_number}행 따옴표가 잘못됐다"
                )
            value = value[1:-1]
        values[key] = value
    return values


def load_config(
    env: dict | None = None,
    *,
    env_file_path: str | None = None,
) -> Config:
    """환경변수와 선택된 env 파일에서 uploader 설정을 읽는다."""
    process_environment = env is None
    environment = dict(os.environ if process_environment else env)

    if env_file_path is not None:
        file_environment = _read_env_file(env_file_path, required=True)
    elif process_environment:
        configured_path = environment.get(
            ENV_CONFIG_FILE,
            DEFAULT_CONFIG_FILE,
        ).strip()
        file_environment = _read_env_file(
            configured_path,
            required=ENV_CONFIG_FILE in environment,
        )
    else:
        file_environment = {}

    # 컨테이너가 직접 주입한 환경변수가 파일보다 우선한다.
    file_environment.update(environment)
    env = file_environment

    token = env.get(ENV_TOKEN, "").strip()
    if not token:
        raise ConfigError(f"{ENV_TOKEN} 환경변수가 비어 있다")  # 값은 메시지에 넣지 않는다

    url = env.get(ENV_EC2_URL, "").strip()
    if not url:
        raise ConfigError(f"{ENV_EC2_URL} 환경변수가 비어 있다")

    try:
        socket_mode = int(env.get(ENV_SOCKET_MODE, DEFAULT_SOCKET_MODE), 8)
    except ValueError as exc:
        raise ConfigError(f"{ENV_SOCKET_MODE}는 8진수 문자열이어야 한다") from exc

    return Config(
        socket_path=env.get(ENV_SOCKET_PATH, DEFAULT_SOCKET_PATH),
        socket_mode=socket_mode,
        ec2_upload_url=url,
        device_token=token,
        connect_timeout_s=float(env.get(ENV_CONNECT_TIMEOUT, "5")),
        read_timeout_s=float(env.get(ENV_READ_TIMEOUT, "30")),
        tls_verify=env.get(ENV_TLS_VERIFY, "true").strip().lower() != "false",
    )


# ══════════════════════════════════════════════════════════════════════════
# 소켓 계약: manifest(요청) / ACK(응답)
# ══════════════════════════════════════════════════════════════════════════
# 요청 manifest (한 줄 JSON):
#   {"version":1,"session_id":"<10진 str>","content_digest":"sha256:<64hex>",
#    "directory":"<완료 디렉터리 절대경로>",
#    "files":{"<kind>":{"filename","size_bytes","row_count","sha256"}, ...}}
# 응답 ACK (한 줄 JSON + "\n"):
#   {"session_id":"<str>","accepted":true|false,"reason":"<str>"}  (content_digest 넣지 않음)

HANDOFF_VERSION = 1
MAX_LINE_BYTES = 1024 * 1024  # manifest 한 줄 상한(경로/해시만 담기므로 작다)
CONTENT_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: 정확히 이 4종 (팀 확정). uploader는 이 집합과 다르면 거부한다.
EXPECTED_FILE_KINDS = frozenset({"metadata", "hand_command", "motor_status", "landmark"})


@dataclass(frozen=True)
class ManifestFile:
    filename: str
    size_bytes: int
    row_count: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    version: int
    session_id: str
    content_digest: str
    directory: str
    files: Mapping[str, ManifestFile]


@dataclass(frozen=True)
class Ack:
    session_id: str
    accepted: bool
    reason: str


def parse_manifest(line: bytes) -> Manifest:
    """
    소켓으로 받은 한 줄 manifest를 구조로 파싱·형식 검증한다.

    형식만 본다. 파일 존재·해시 재계산·집합 검증은 uploader.verify 단계에서 한다.
    """
    if len(line) > MAX_LINE_BYTES:
        raise ManifestError("manifest가 너무 크다")
    try:
        obj = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("manifest가 올바른 UTF-8 JSON이 아니다") from exc
    if not isinstance(obj, dict):
        raise ManifestError("manifest 최상위가 object가 아니다")
    if obj.get("version") != HANDOFF_VERSION:
        raise ManifestError("지원하지 않는 manifest version")

    session_id = obj.get("session_id")
    if not isinstance(session_id, str) or not session_id.isdigit():
        raise ManifestError("session_id는 10진 문자열이어야 한다")

    content_digest = obj.get("content_digest", "")
    if not isinstance(content_digest, str) or not CONTENT_DIGEST_RE.fullmatch(content_digest):
        raise ManifestError("content_digest 형식이 잘못됐다")

    directory = obj.get("directory")
    if not isinstance(directory, str) or not directory:
        raise ManifestError("directory가 비었다")

    raw_files = obj.get("files")
    if not isinstance(raw_files, dict) or not raw_files:
        raise ManifestError("files가 비었다")
    files = {kind: _parse_file(kind, info) for kind, info in raw_files.items()}

    return Manifest(HANDOFF_VERSION, session_id, content_digest, directory, files)


def _parse_file(kind: str, info: object) -> ManifestFile:
    if not isinstance(info, dict):
        raise ManifestError(f"files.{kind} 형식이 잘못됐다")
    filename = info.get("filename")
    size_bytes = info.get("size_bytes")
    row_count = info.get("row_count")
    sha256 = info.get("sha256")
    if not isinstance(filename, str) or not filename or filename.endswith(".part"):
        raise ManifestError(f"files.{kind}.filename이 잘못됐다")
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
        raise ManifestError(f"files.{kind}.size_bytes가 잘못됐다")
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
        raise ManifestError(f"files.{kind}.row_count가 잘못됐다")
    if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
        raise ManifestError(f"files.{kind}.sha256이 잘못됐다")
    return ManifestFile(filename, size_bytes, row_count, sha256)


def serialize_ack(ack: Ack) -> bytes:
    """ACK를 robot 클라이언트가 기대하는 한 줄 JSON(+개행)으로 직렬화한다."""
    payload = {"session_id": ack.session_id, "accepted": ack.accepted, "reason": ack.reason}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
