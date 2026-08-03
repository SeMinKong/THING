# backend/apps/upload_views.py
"""
POST /api/v1/uploads/sessions

요구사항 명세서 FR-46 / FR-51 / NFR-25 / NFR-26, 6.5절.

검증 순서. 각 단계에서 지정 상태 코드로 즉시 중단한다.

    Bearer token 검증                        → 401
    part 3개 정확히 / 추가 part 거부          → 400
    content type                             → 415
    part별·합계 크기 상한                     → 413
    staging 스트리밍 저장 + fsync
    metadata JSON schema v1                  → 422
    CSV header·행 수·유한값·비감소 timestamp  → 422
    파일별 SHA-256 == metadata 값             → 422
    content_digest 재계산 == 요청값           → 422
    token robot == metadata.robot_id         → 401
    (robot_id, session_id) 조회
      ├ 없음               → atomic rename + READY  → 201
      ├ 있음 & 같은 digest → 덮어쓰기 금지, 기존 유지 → 200
      └ 있음 & 다른 digest → staging 정리            → 409

크기 상한을 staging 저장보다 먼저 두는 이유는 디스크를 낭비하지 않기 위해서다.
Django가 5MiB 초과분을 임시파일로 스풀하므로 이 시점에 메모리는 안전하다.
"""
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import storage
from .device_auth import authenticate_device
from .digest import compute_content_digest, sha256_file
from .errors import (
    MalformedRequest,
    PayloadTooLarge,
    RobotMismatch,
    SessionContentConflict,
    UnexpectedPart,
    UnsupportedMediaType,
    ValidationFailed,
)
from .models import Session
from .throttles import DeviceUploadThrottle
from .validators import (
    PART_CONTENT_TYPES,
    PART_MAX_BYTES,
    TOTAL_MAX_BYTES,
    load_metadata,
    validate_csv,
    validate_metadata,
)

logger = logging.getLogger(__name__)

REQUIRED_PARTS = ("metadata", "hand_command", "motor_status")


class SessionUploadView(APIView):
    """장치 인증이 필요한 유일한 쓰기 엔드포인트."""

    parser_classes = [MultiPartParser]
    authentication_classes = []          # 인증은 authenticate_device 가 담당한다
    permission_classes = []
    throttle_classes = [DeviceUploadThrottle]   # token 당 10/min

    def post(self, request):
        # 1) 인증 → 401
        robot_id = authenticate_device(request)

        # 2) part 구성 → 400
        self._check_parts(request)

        # 3) content type → 415
        self._check_content_types(request)

        # 4) 크기 상한 → 413
        self._check_sizes(request)

        # metadata 는 256KiB 이하이므로 이 시점에 메모리로 읽어도 안전하다
        metadata_part = request.data["metadata"]
        metadata_part.seek(0)
        raw_metadata = metadata_part.read()
        meta = validate_metadata(load_metadata(raw_metadata))

        session_id = meta["session_id"]

        # token 의 robot 과 metadata.robot_id 일치 → 401
        if meta["robot_id"] != robot_id:
            raise RobotMismatch(log_detail=f"token robot={robot_id}")

        # 5) staging 저장 후 내용 검증
        storage.prepare_staging(robot_id, session_id)
        try:
            sizes, hashes = self._stage_and_hash(request, robot_id, session_id, raw_metadata)
            self._verify_declared_hashes(meta, hashes, sizes)
            self._verify_csv_contents(meta, robot_id, session_id)
            self._verify_digest(request, meta, raw_metadata)

            return self._commit(request, meta, robot_id, session_id, sizes, hashes)
        except Exception:
            storage.discard_staging(robot_id, session_id)
            raise

    # ── 단계별 구현 ──

    @staticmethod
    def _check_parts(request):
        received = set(request.data.keys()) | set(request.FILES.keys())
        missing = [p for p in REQUIRED_PARTS if p not in request.FILES]
        if missing:
            raise MalformedRequest(
                details=[f"필수 part 누락: {', '.join(missing)}"]
            )
        extra = sorted(received - set(REQUIRED_PARTS))
        if extra:
            # rosbag2 등 추가 part 를 거부한다 (명세서 6.5절)
            raise UnexpectedPart(details=[f"허용되지 않은 part: {', '.join(extra)}"])

    @staticmethod
    def _check_content_types(request):
        for name in REQUIRED_PARTS:
            actual = (request.FILES[name].content_type or "").split(";")[0].strip().lower()
            allowed = PART_CONTENT_TYPES[name]
            if actual not in allowed:
                raise UnsupportedMediaType(
                    details=[f"{name}: {' 또는 '.join(allowed)} 여야 한다"]
                )

    @staticmethod
    def _check_sizes(request):
        total = 0
        for name in REQUIRED_PARTS:
            size = request.FILES[name].size or 0
            total += size
            if size > PART_MAX_BYTES[name]:
                raise PayloadTooLarge(
                    details=[f"{name}: 상한 {PART_MAX_BYTES[name] // 1024}KiB 초과"]
                )
        if total > TOTAL_MAX_BYTES:
            raise PayloadTooLarge(details=["세 part 합계 상한 80.25MiB 초과"])

    @staticmethod
    def _stage_and_hash(request, robot_id, session_id, raw_metadata):
        """세 part 를 staging 에 저장하고 실제 크기·SHA-256 을 계산한다."""
        sizes, hashes = {}, {}
        for name in REQUIRED_PARTS:
            part = request.FILES[name]
            part.seek(0)
            path, written = storage.stream_to_staging(part, robot_id, session_id, name)
            sizes[name] = written
            hashes[name] = sha256_file(path)
        return sizes, hashes

    @staticmethod
    def _verify_declared_hashes(meta, hashes, sizes):
        """[NFR-25] metadata 가 주장한 두 CSV 의 크기·SHA-256 이 실제와 일치해야 한다."""
        errors = []
        for kind in ("hand_command", "motor_status"):
            declared = meta["files"][kind]
            if declared["sha256"].lower() != hashes[kind].lower():
                errors.append(f"files.{kind}.sha256: 실제 파일과 다르다")
            if declared["size_bytes"] != sizes[kind]:
                errors.append(
                    f"files.{kind}.size_bytes: 실제 {sizes[kind]}, metadata {declared['size_bytes']}"
                )
        if errors:
            raise ValidationFailed(details=errors)

    @staticmethod
    def _verify_csv_contents(meta, robot_id, session_id):
        for kind in ("hand_command", "motor_status"):
            path = storage.staging_dir(robot_id, session_id) / storage.canonical_filename(
                session_id, kind
            )
            validate_csv(path, kind, session_id, meta["files"][kind]["row_count"])

    @staticmethod
    def _verify_digest(request, meta, raw_metadata):
        """[NFR-26] 서버가 content_digest 를 재계산해 metadata·헤더와 일치하는지 본다."""
        import json

        recomputed = compute_content_digest(json.loads(raw_metadata.decode("utf-8")))

        if recomputed.lower() != meta["content_digest"].lower():
            raise ValidationFailed(
                details=["content_digest: 서버 재계산 결과와 metadata 값이 다르다"],
                log_detail=f"recomputed={recomputed}",
            )

        # Idempotency-Key 는 <robot_id>:<session_id>:<data_version>:<content_digest>
        key = request.META.get("HTTP_IDEMPOTENCY_KEY", "").strip()
        if key:
            expected = (
                f"{meta['robot_id']}:{meta['session_id']}:{meta['data_version']}:"
                f"{meta['content_digest']}"
            )
            if key.lower() != expected.lower():
                raise ValidationFailed(
                    details=["Idempotency-Key: metadata 값과 일치하지 않는다"]
                )

    def _commit(self, request, meta, robot_id, session_id, sizes, hashes):
        """멱등성 판정 후 원자적으로 공개한다."""
        digest = meta["content_digest"]

        existing = Session.objects.filter(robot_id=robot_id, session_id=session_id).first()

        if existing is not None:
            if existing.content_digest.lower() != digest.lower():
                # 같은 key 다른 내용 → 409. 기존 파일을 덮어쓰지 않는다.
                storage.discard_staging(robot_id, session_id)
                raise SessionContentConflict(
                    details=[f"session_id {session_id} 는 다른 content_digest 로 존재한다"]
                )
            # 같은 digest 재전송 → 200. 기존 READY 를 유지하고 저장 파일을 덮어쓰지 않는다.
            storage.discard_staging(robot_id, session_id)
            return self._success(existing, status.HTTP_200_OK)

        row_counts = {
            kind: meta["files"][kind]["row_count"] for kind in ("hand_command", "motor_status")
        }

        try:
            with transaction.atomic():
                session = Session.objects.create(
                    robot_id=robot_id,
                    session_id=session_id,
                    schema_version=meta["schema_version"],
                    data_version=meta["data_version"],
                    started_at=meta["started_at"],
                    ended_at=meta["ended_at"],
                    uploaded_at=timezone.now(),
                    result=meta["result"],
                    duration_ms=0,                     # save() 가 파생한다
                    interface_commit=meta["interface_commit"],
                    time_sync=meta["time_sync"],
                    content_digest=digest,
                    status=Session.Status.STAGING,
                    row_counts=row_counts,
                    file_sizes=sizes,
                    file_hashes=hashes,
                )
                # 검증 통과 → 같은 파일시스템 내 원자적 rename
                storage.commit_staging(robot_id, session_id)
                session.status = Session.Status.READY
                session.save(update_fields=["status", "updated_at", "duration_ms"])
        except IntegrityError:
            # 동시 업로드 경합. 다른 요청이 먼저 만들었으므로 재판정한다.
            storage.discard_staging(robot_id, session_id)
            other = Session.objects.filter(robot_id=robot_id, session_id=session_id).first()
            if other and other.content_digest.lower() == digest.lower():
                return self._success(other, status.HTTP_200_OK)
            raise SessionContentConflict(
                details=[f"session_id {session_id} 는 다른 content_digest 로 존재한다"]
            )

        return self._success(session, status.HTTP_201_CREATED)

    @staticmethod
    def _success(session, http_status):
        """명세서 6.5절 성공 응답. uploader 가 이 네 값을 요청과 대조한다."""
        return Response(
            {
                "session_id": session.session_id,
                "data_version": session.data_version,
                "status": session.status,
                "content_digest": session.content_digest,
                "files": {
                    kind: {
                        "sha256": session.file_hashes.get(kind),
                        "size_bytes": session.file_sizes.get(kind),
                    }
                    for kind in storage.FILE_KINDS
                },
                "uploaded_at": session.uploaded_at.strftime("%Y-%m-%dT%H:%M:%S.")
                + f"{session.uploaded_at.microsecond // 1000:03d}Z",
            },
            status=http_status,
        )
