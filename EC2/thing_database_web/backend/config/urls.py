# backend/config/urls.py
from django.contrib import admin
from django.urls import path
from apps.health import HealthView
from apps.read_views import (
    SessionDataView,
    SessionDetailView,
    SessionDownloadView,
    SessionListView,
)
from apps.upload_views import SessionUploadView

urlpatterns = [
    path('admin/', admin.site.urls),
    # ── v1 데이터 계약 (명세서 6.5절) ──
    path('api/v1/uploads/sessions', SessionUploadView.as_view(), name='v1-upload'),
    path('api/v1/sessions', SessionListView.as_view(), name='v1-session-list'),
    path('api/v1/sessions/<str:session_id>', SessionDetailView.as_view(), name='v1-session-detail'),
    path('api/v1/sessions/<str:session_id>/data', SessionDataView.as_view(), name='v1-session-data'),
    # 스프린트 티켓의 /series?type= 별칭. 같은 뷰가 응답한다.
    path('api/v1/sessions/<str:session_id>/series', SessionDataView.as_view(), name='v1-session-series'),
    path('api/v1/sessions/<str:session_id>/download/<str:file_kind>',
         SessionDownloadView.as_view(), name='v1-session-download'),

    # 명세서 FR-52. 슬래시 없는 정확히 '/health' 경로다.
    path('health', HealthView.as_view(), name='health'),
]
