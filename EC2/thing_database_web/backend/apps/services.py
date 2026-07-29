# backend/apps/services.py
import os
from pathlib import Path

from django.conf import settings


class S3Service:
    def __init__(self):
        # AWS 대신 로컬 프로젝트 내부의 'media' 폴더를 저장소로 활용합니다.
        self.upload_dir = Path(getattr(settings, "MEDIA_ROOT", Path(settings.BASE_DIR) / "media"))
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def upload_file(self, file_obj, s3_key):
        """[로컬 대체] S3 대신 로컬 미디어 폴더에 로봇 파일 저장"""
        try:
            filename = os.path.basename(s3_key)
            full_path = self.upload_dir / filename

            with full_path.open("wb+") as destination:
                for chunk in file_obj.chunks():
                    destination.write(chunk)
            return True
        except Exception as exc:
            print(f"로컬 파일 저장 에러: {exc}")
            return False

    def generate_download_url(self, s3_key, expires_in=300):
        """[로컬 대체] S3 Presigned URL 대신 Django가 서빙하는 로컬 다운로드 링크 반환"""
        filename = os.path.basename(s3_key)
        media_url = getattr(settings, "MEDIA_URL", "/media/")
        base_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000")
        return f"{base_url.rstrip('/')}{media_url}{filename}"
