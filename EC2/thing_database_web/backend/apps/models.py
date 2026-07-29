# backend/apps/models.py
from django.db import models
from django.contrib.auth.models import User

# [기존 모델 유지]
class MotorLogFile(models.Model):
    robot_id = models.CharField(max_length=50, help_text="로봇 식별 ID")
    file_name = models.CharField(max_length=255, help_text="파일명")
    s3_key = models.CharField(max_length=500, help_text="S3 내부 저장 경로")
    created_at = models.DateTimeField(auto_now_add=True, help_text="업로드 일시")

    def __str__(self):
        return f"[{self.robot_id}] {self.file_name}"


# 🆕 [신규 모델 추가] 회원 상세 프로필 확장 테이블
class UserProfile(models.Model):
    # Django 기본 유저(아이디, 비밀번호, 이메일 포함)와 1:1 관계 연결
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    
    # 추가로 수집할 항목들
    full_name = models.CharField(max_length=100, help_text="이름")
    purpose = models.TextField(help_text="자료 사용 목적")
    registered_at = models.DateTimeField(auto_now_add=True, help_text="가입 일시")

    def __str__(self):
        return f"{self.full_name} ({self.user.username})"
