# backend/apps/models.py
"""세션 데이터 계약 모델 (요구사항 명세서 6.5절)."""
from django.db import models


class Session(models.Model):
    """[FR-46/47] 로봇이 업로드한 완료 세션 1건.

    요구사항 명세서 6.5절 데이터 계약. unique key는 (robot_id, session_id)이며
    data_version은 MVP에서 1 고정이다. 공개 API는 status=READY만 노출한다.
    """

    class Result(models.TextChoices):
        SUCCESS = "SUCCESS", "SUCCESS"
        FAILURE = "FAILURE", "FAILURE"

    class Status(models.TextChoices):
        STAGING = "STAGING", "STAGING"   # 업로드 검증 중. 공개하지 않는다
        READY = "READY", "READY"         # 검증 완료. 공개 대상
        FAILED = "FAILED", "FAILED"      # 검증 실패. 정리 대상

    # ── 식별 ──
    robot_id = models.CharField(max_length=50)
    # Session ID는 ROS uint64지만 JSON·API·SQLite에서는 문자열이다 (최대 20자리)
    session_id = models.CharField(max_length=20)

    # ── 버전 (MVP 고정 1) ──
    schema_version = models.IntegerField(default=1)
    data_version = models.IntegerField(default=1)

    # ── 시각 (모두 UTC 저장, 응답 시 RFC 3339 Z 로 직렬화) ──
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    uploaded_at = models.DateTimeField()

    # ── 판정 ──
    result = models.CharField(max_length=7, choices=Result.choices)
    duration_ms = models.BigIntegerField()

    # ── 출처 검증 ──
    interface_commit = models.CharField(max_length=40)
    time_sync = models.BooleanField()

    # ── 멱등성 기준 (NFR-26) ──
    # "sha256:" + hex64 = 71자
    content_digest = models.CharField(max_length=71)

    # ── 공개 상태 ──
    status = models.CharField(max_length=7, choices=Status.choices, default=Status.STAGING)

    # ── 파일 메타 ──
    # row_counts   {"hand_command": int, "motor_status": int}
    # file_sizes   {"metadata": int, "hand_command": int, "motor_status": int}
    # file_hashes  {"metadata": hex64, "hand_command": hex64, "motor_status": hex64}
    row_counts = models.JSONField(default=dict)
    file_sizes = models.JSONField(default=dict)
    file_hashes = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["robot_id", "session_id"], name="uq_session_robot_session"
            ),
        ]
        indexes = [
            # 목록 기본 정렬: started_at DESC, session_id DESC
            models.Index(fields=["-started_at", "-session_id"], name="idx_session_listing"),
            models.Index(fields=["status"], name="idx_session_status"),
        ]
        ordering = ["-started_at", "-session_id"]

    def __str__(self):
        return f"{self.robot_id}/{self.session_id} ({self.status})"

    def save(self, *args, **kwargs):
        # duration_ms 는 metadata에 없고 서버가 파생한다 (목록 응답 필드)
        if self.started_at and self.ended_at:
            delta = self.ended_at - self.started_at
            self.duration_ms = int(delta.total_seconds() * 1000)
        super().save(*args, **kwargs)

    @property
    def is_public(self):
        return self.status == self.Status.READY
