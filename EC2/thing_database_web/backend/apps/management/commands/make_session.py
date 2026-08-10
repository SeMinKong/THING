# backend/apps/management/commands/make_session.py
"""시험용 세션 네 파일을 만든다.

프로젝트에 시험 데이터를 만드는 장치가 없었다. 데이터 생성 코드가 테스트 픽스처
안에만 있어서 운영 서버에서 "landmark JSON 이 실제로 올라가고 내려오는가" 를
확인할 방법이 없었다. 이 명령이 그 자리를 채운다.

만드는 것
    session_{id}_metadata.json     실제 sha256·content_digest 로 채운다
    session_{id}_hand_command.csv  6.5절 header 와 규칙을 지킨다
    session_{id}_motor_status.csv  같음. 시각마다 7모터
    session_{id}_landmark.json     landmark_contract.sample_payload() 가 만든다

**검증을 우회하지 않는다.** 파일만 만들고 실제 업로드 endpoint 로 보내므로
인증·part·schema·hash·digest 검사를 모두 통과해야 성공한다. `--post` 를 쓰면
Nginx 까지 지나가므로 `client_max_body_size` 반영 여부도 함께 확인된다.

예시
    # 파일만 만들고 curl 명령을 출력
    python manage.py make_session --out /tmp/s1

    # 만들면서 바로 업로드
    python manage.py make_session --out /tmp/s1 \\
        --post https://example.com/api/v1/uploads/sessions --token "$TOKEN"

    # 큰 landmark 로 Nginx 상한을 시험
    python manage.py make_session --out /tmp/big --landmark-frames 20000
"""
import hashlib
import json
import math
import mimetypes
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps import landmark_contract, limits
from apps.digest import compute_content_digest
from apps.validators import HAND_COMMAND_HEADER, MOTOR_STATUS_HEADER

#: FR-30 이 동결 기준으로 정한 커밋. metadata 가 요구한다.
INTERFACE_COMMIT = "626c59e09f108e6e5eb6d2313efe28bf0e51ed03"

#: 시각마다 몇 모터를 기록하는가 (FR-07)
MOTOR_COUNT = 7
ACTUATORS = [
    "thumb_flex", "thumb_opp", "thumb_abd",
    "index_flex", "middle_flex", "ring_flex", "little_flex",
]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _rfc3339(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _new_session_id():
    """6.5절: 0 이 아닌 63-bit 양의 정수를 10진 문자열로."""
    value = secrets.randbits(63) or 1
    return str(value)


def _hand_command_csv(session_id, samples, hz):
    lines = [",".join(HAND_COMMAND_HEADER)]
    base_sec = 1785283200
    w = 2 * math.pi * 0.25          # 0.25Hz — 4초에 한 주기
    for i in range(samples):
        elapsed = int(i * 1000 / hz)
        # stamp 는 비감소여야 한다 (validate_csv)
        sec = base_sec + elapsed // 1000
        nsec = (elapsed % 1000) * 1_000_000
        t = elapsed / 1000.0
        # 7논리축을 서로 다른 위상의 사인파로 흔들어 시계열 곡선을 만든다 (0.05~0.95)
        axes = ",".join(
            f"{0.5 + 0.45 * math.sin(w * t + a * 2 * math.pi / 7):.4f}"
            for a in range(7)
        )
        confidence = 0.90 + 0.05 * math.sin(w * t)
        lines.append(
            f"{session_id},{sec},{nsec},{elapsed},{i + 1},MIMIC,"
            f"{axes},1.00,{confidence:.3f}"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _motor_status_csv(session_id, samples, hz):
    lines = [",".join(MOTOR_STATUS_HEADER)]
    base_sec = 1785283200
    w = 2 * math.pi * 0.25
    for i in range(samples):
        elapsed = int(i * 1000 / hz)
        sec = base_sec + elapsed // 1000
        nsec = (elapsed % 1000) * 1_000_000
        t = elapsed / 1000.0
        # temperature_celsius 는 정수 컬럼(serializers._INT_COLUMNS)이라 int 로 램프.
        # +1℃/2s, +15℃ 상한.
        temp = 30 + min(15, elapsed // 2000)
        for m in range(MOTOR_COUNT):
            phase = m * 2 * math.pi / MOTOR_COUNT     # 모터마다 위상차 → 7개 곡선 분리
            goal_rad = 0.5 + 0.40 * math.sin(w * t + phase)
            present_rad = 0.5 + 0.40 * math.sin(w * t + phase - 0.15)  # 목표 살짝 추종
            velocity = 0.40 * w * math.cos(w * t + phase - 0.15)       # present 의 미분
            current = 0.05 + 0.20 * abs(velocity)                      # 부하 ~ |속도|
            voltage = 11.5 + 0.25 * math.sin(w * t + phase)
            goal_raw = int(2048 + 1400 * math.sin(w * t + phase))
            present_raw = int(2048 + 1400 * math.sin(w * t + phase - 0.15))
            lines.append(
                f"{session_id},{sec},{nsec},{elapsed},base_link,{11 + m},"
                f"{ACTUATORS[m]},{goal_raw},{present_raw},"
                f"{goal_rad:.4f},{present_rad:.4f},{velocity:.4f},"
                f"{current:.4f},{voltage:.3f},{temp},true,0,0,true,true,0"
            )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _multipart(fields):
    """multipart/form-data 본문을 만든다. requests 없이 stdlib 만 쓴다."""
    boundary = "----thing" + secrets.token_hex(16)
    out = bytearray()
    for name, (filename, blob) in fields.items():
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if filename.endswith(".csv"):
            ctype = "text/csv"
        elif filename.endswith(".json"):
            ctype = "application/json"
        out += f"--{boundary}\r\n".encode()
        out += (
            f'Content-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\n'
        ).encode()
        out += f"Content-Type: {ctype}\r\n\r\n".encode()
        out += blob
        out += b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


class Command(BaseCommand):
    help = "시험용 세션 네 파일을 만들고, 원하면 업로드까지 한다"

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="파일을 쓸 디렉터리")
        parser.add_argument("--robot-id", default="THING-001")
        parser.add_argument("--session-id", default=None,
                            help="생략하면 63-bit 무작위 값을 만든다")
        parser.add_argument("--result", default="SUCCESS",
                            choices=["SUCCESS", "FAILURE"])
        parser.add_argument("--samples", type=int, default=200,
                            help="HandCommand 행 수. MotorStatus 는 이 값 × 7")
        parser.add_argument("--hz", type=int, default=20, help="발행 주기 (FR-11)")
        parser.add_argument("--landmark-frames", type=int, default=200,
                            help="landmark 프레임 수. 크게 주면 큰 파일이 된다")
        parser.add_argument("--no-landmark", action="store_true",
                            help="landmark 를 빼고 세 파일만 만든다")
        parser.add_argument("--post", default=None, help="업로드할 endpoint URL")
        parser.add_argument("--token", default=None, help="장치 Bearer 토큰")

    def handle(self, *args, **opt):
        out = Path(opt["out"]).expanduser()
        out.mkdir(parents=True, exist_ok=True)

        sid = opt["session_id"] or _new_session_id()
        robot = opt["robot_id"]
        samples, hz = opt["samples"], opt["hz"]
        if samples < 1 or hz < 1:
            raise CommandError("--samples 와 --hz 는 1 이상이어야 한다")

        hc = _hand_command_csv(sid, samples, hz)
        ms = _motor_status_csv(sid, samples, hz)

        started = datetime(2026, 7, 29, tzinfo=timezone.utc)
        ended = started + timedelta(milliseconds=int(samples * 1000 / hz) or 1)

        meta = {
            "schema_version": 1,
            "data_version": 1,
            "robot_id": robot,
            "session_id": sid,
            "started_at": _rfc3339(started),
            "ended_at": _rfc3339(ended),
            "exported_at": _rfc3339(ended + timedelta(seconds=5)),
            "result": opt["result"],
            "interface_commit": INTERFACE_COMMIT,
            "time_sync": True,
            "files": {
                "hand_command": {
                    "filename": f"session_{sid}_hand_command.csv",
                    "size_bytes": len(hc),
                    "row_count": samples,
                    "sha256": _sha(hc),
                },
                "motor_status": {
                    "filename": f"session_{sid}_motor_status.csv",
                    "size_bytes": len(ms),
                    "row_count": samples * MOTOR_COUNT,
                    "sha256": _sha(ms),
                },
            },
        }

        lm = None
        if not opt["no_landmark"]:
            payload = landmark_contract.sample_payload(opt["landmark_frames"], hz)
            lm = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if len(lm) > landmark_contract.MAX_BYTES:
                raise CommandError(
                    f"landmark 가 상한을 넘었다: {len(lm) // limits.MiB}MiB > "
                    f"{landmark_contract.MAX_BYTES // limits.MiB}MiB. "
                    "--landmark-frames 를 줄이거나 apps/limits.py 를 고치세요"
                )
            meta["files"][landmark_contract.KIND] = {
                "filename": f"session_{sid}_{landmark_contract.KIND}"
                            f".{landmark_contract.EXTENSION}",
                "size_bytes": len(lm),
                "sha256": _sha(lm),
            }

        # digest 는 landmark 선언까지 반영한 뒤 계산한다.
        # 실제 포함 여부는 landmark_contract.INCLUDE_IN_DIGEST 가 정한다.
        meta["content_digest"] = compute_content_digest(meta)
        raw_meta = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")

        files = {
            "metadata": (f"session_{sid}_metadata.json", raw_meta),
            "hand_command": (f"session_{sid}_hand_command.csv", hc),
            "motor_status": (f"session_{sid}_motor_status.csv", ms),
        }
        if lm is not None:
            files[landmark_contract.KIND] = (
                f"session_{sid}_{landmark_contract.KIND}.{landmark_contract.EXTENSION}",
                lm,
            )

        for name, blob in files.values():
            (out / name).write_bytes(blob)

        total = sum(len(b) for _, b in files.values())
        self.stdout.write(self.style.SUCCESS(f"세션 {sid} 파일 {len(files)}개 생성"))
        self.stdout.write(f"  위치      {out}")
        for kind, (name, blob) in files.items():
            self.stdout.write(f"  {kind:<14}{len(blob):>12,} bytes  {name}")
        self.stdout.write(f"  합계      {total:,} bytes ({total / limits.MiB:.2f} MiB)")
        self.stdout.write(f"  digest    {meta['content_digest']}")

        if opt["post"]:
            self._upload(opt["post"], opt["token"], files, sid)
        else:
            self._print_curl(out, files, sid)

    def _print_curl(self, out, files, sid):
        parts = " \\\n  ".join(
            f'-F "{kind}=@{out / name};type='
            f'{"text/csv" if name.endswith(".csv") else "application/json"}"'
            for kind, (name, _) in files.items()
        )
        self.stdout.write("\n업로드하려면:\n")
        self.stdout.write(
            f'curl -sS -X POST "$EC2_URL/api/v1/uploads/sessions" \\\n'
            f'  -H "Authorization: Bearer $TOKEN" \\\n  {parts}\n'
        )
        self.stdout.write(f'확인:  curl -sS "$EC2_URL/api/v1/sessions/{sid}"\n')

    def _upload(self, url, token, files, sid):
        if not token:
            raise CommandError("--post 를 쓰려면 --token 이 필요하다")
        body, ctype = _multipart(files)
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Content-Type": ctype,
                "Content-Length": str(len(body)),
                "Authorization": f"Bearer {token}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                self.stdout.write(self.style.SUCCESS(
                    f"업로드 {resp.status}"
                ))
                self.stdout.write(resp.read().decode("utf-8", "replace")[:800])
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:800]
            if exc.code == 413:
                self.stderr.write(self.style.ERROR(
                    "413 — Nginx client_max_body_size 가 반영되지 않았습니다. "
                    f"{limits.nginx_client_max_body_size()} 이상으로 고치고 "
                    "systemctl reload nginx 를 하세요."
                ))
            raise CommandError(f"업로드 실패 {exc.code}\n{detail}")
        except urllib.error.URLError as exc:
            raise CommandError(f"연결 실패: {exc.reason}")

        self.stdout.write(f"\n확인:  {url.rsplit('/api/', 1)[0]}/api/v1/sessions/{sid}")
