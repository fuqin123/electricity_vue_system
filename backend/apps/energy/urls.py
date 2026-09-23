"""能源数据接口路由。

分两组前缀（见 api.py 顶部说明）：
  · `/api/energy/*` —— 只读，大屏消费；
  · `/api/admin/*`  —— 后台配置，含读与写（网关 / 计量点的增删改与试采）。

⚠ v1.2.1 修复：admin 组此前误挂在 `/api/energy/admin/*` 下，而后台前端
  （shared/api）按约定调用 `/api/admin/*`，导致后台所有真实接口 404、
  页面与真实库断连（大屏读真库、后台拿不到数据，两边必然对不上）。
  现在两组路由**分别导出**，由 eep/urls.py 挂到各自前缀：
  `energy_urlpatterns` → /api/energy/…，`admin_urlpatterns` → /api/admin/…
  （/api/energy/admin/… 的旧地址仍兼容保留，防外部脚本断链）。
"""

from django.urls import path

from . import api

#: 大屏只读组（挂在 /api/energy/ 下）
energy_urlpatterns = [
    path("overview", api.overview, name="energy-overview"),
    path("line/<str:code>", api.line_detail, name="energy-line"),
    path("hourly", api.hourly, name="energy-hourly"),
    path("meters", api.meters, name="energy-meters"),
    path("collect-logs", api.collect_logs, name="energy-collect-logs"),
    path("collect-summary", api.collect_summary, name="energy-collect-summary"),
    path("period-summary", api.period_summary, name="energy-period-summary"),
    path("range", api.usage_range, name="energy-range"),
    path("production", api.production, name="energy-production"),
]

#: 后台配置组：原始三元组 (route, view, name)，用于挂到两个前缀
_ADMIN_ROUTES = [
    ("overview", api.admin_overview, "admin-overview"),
    ("lines", api.admin_lines, "admin-lines"),
    ("quality", api.admin_quality, "admin-quality"),
    ("collect-logs", api.admin_collect_logs, "admin-collect-logs"),
    ("gateways", api.admin_gateways, "admin-gateways"),
    ("gateways/<int:pk>", api.admin_gateway_detail, "admin-gateway-detail"),
    ("gateways/<int:pk>/test", api.admin_gateway_test, "admin-gateway-test"),
    ("meters", api.admin_meters, "admin-meters"),
    ("meters/<int:pk>", api.admin_meter_detail, "admin-meter-detail"),
    ("meters/<int:pk>/test", api.admin_meter_test, "admin-meter-test"),
]

#: 后台配置组（挂在 /api/admin/ 下）
admin_urlpatterns = [path(route, view, name=name) for route, view, name in _ADMIN_ROUTES]

urlpatterns = energy_urlpatterns + [
    # 兼容旧前缀 /api/energy/admin/…（同 view 换名再注册一遍）
    path(f"admin/{route}", view, name=f"legacy-{name}")
    for route, view, name in _ADMIN_ROUTES
]
