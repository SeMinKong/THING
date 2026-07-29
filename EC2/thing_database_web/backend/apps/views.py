# backend/apps/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import MotorLogFile
from .services import S3Service

class MotorDataUploadView(APIView):
    def post(self, request):
        robot_id = request.data.get('robot_id')
        file_obj = request.FILES.get('file')

        if not robot_id or not file_obj:
            return Response({"error": "데이터가 누락되었습니다."}, status=status.HTTP_400_BAD_REQUEST)

        s3_key = f"robots/{robot_id}/{file_obj.name}"
        s3_service = S3Service()

        if s3_service.upload_file(file_obj, s3_key):
            MotorLogFile.objects.create(robot_id=robot_id, file_name=file_obj.name, s3_key=s3_key)
            return Response({"message": "S3 업로드 완료"}, status=status.HTTP_201_CREATED)
        return Response({"error": "S3 업로드 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class MotorDataDownloadView(APIView):
    def get(self, request, file_id):
        try:
            log_file = MotorLogFile.objects.get(id=file_id)
        except MotorLogFile.DoesNotExist:
            return Response({"error": "파일을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        s3_service = S3Service()
        url = s3_service.generate_download_url(log_file.s3_key)
        if url:
            return Response({"download_url": url}, status=status.HTTP_200_OK)
        return Response({"error": "URL 생성 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class MotorDataListView(APIView):
    def get(self, request):
        files = MotorLogFile.objects.all().order_by('-created_at')
        data_list = [
            {
                "id": f.id,
                "robot_id": f.robot_id,
                "file_name": f.file_name,
                "created_at": f.created_at.isoformat()
            }
            for f in files
        ]
        return Response(data_list, status=status.HTTP_200_OK)