"""URL 总表。

两组业务前缀（v1.2.1 起拆分，见 apps/energy/urls.py 的说明）：
  · /api/energy/* —— 大屏只读；
  · /api/admin/*  —— 后台配置（与前端 shared/api 的调用约定一致）。
"""

from django.http import JsonResponse
from django.urls import include, path

from apps.energy.urls import admin_urlpatterns, energy_urlpatterns


def healthz(_request):
    """健康探测：部署脚本与巡检脚本都打这个口。"""
    return JsonResponse({"ok": True, "service": "eep-backend", "version": "1.2.1"})


urlpatterns = [
    path("healthz", healthz),
    path("api/energy/", include((energy_urlpatterns, "energy"))),
    path("api/admin/", include((admin_urlpatterns, "energy_admin"))),
]
