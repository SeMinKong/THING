# backend/config/urls.py
from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from apps.views import MotorDataUploadView, MotorDataDownloadView, MotorDataListView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/motor-data/upload/', MotorDataUploadView.as_view(), name='motor-upload'),
    path('api/motor-data/download/<int:file_id>/', MotorDataDownloadView.as_view(), name='motor-download'),
    path('api/motor-data/files/', MotorDataListView.as_view(), name='motor-list'),
]

urlpatterns += static('/media/', document_root=settings.MEDIA_ROOT)