"""URL 总表。"""

from django.http import JsonResponse
from django.urls import include, path


def healthz(_request):
    """健康探测：部署脚本与巡检脚本都打这个口。"""
    return JsonResponse({"ok": True, "service": "eep-backend", "version": "1.2.0"})


urlpatterns = [
    path("healthz", healthz),
    path("api/energy/", include("apps.energy.urls")),
]
