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

from . import api, production_api

#: 大屏只读组（挂在 /api/energy/ 下）
energy_urlpatterns = [
    path("overview", api.overview, name="energy-overview"),
    path("line/<str:code>", api.line_detail, name="energy-line"),
    path("hourly", api.hourly, name="energy-hourly"),
    path("meters", api.meters, name="energy-meters"),
    # 分类树（车间 → 用能类型 → 设备）—— 后台筛选下拉用，与下面的 usage-range 同源
    path("meter-categories", api.meter_categories, name="energy-meter-categories"),
    path("collect-logs", api.collect_logs, name="energy-collect-logs"),
    path("collect-summary", api.collect_summary, name="energy-collect-summary"),
    path("period-summary", api.period_summary, name="energy-period-summary"),
    path("range", api.usage_range, name="energy-range"),
    path("dashboard", api.dashboard, name="energy-dashboard"),
    path("production", api.production, name="energy-production"),
    # 视频监控配置（大屏启动时读取；不含 RTSP 账号信息）
    path("video-config", api.energy_video_config, name="energy-video-config"),
]

#: 后台配置组：原始三元组 (route, view, name)，用于挂到两个前缀
_ADMIN_ROUTES = [
    ("overview", api.admin_overview, "admin-overview"),
    ("lines", api.admin_lines, "admin-lines"),
    ("quality", api.admin_quality, "admin-quality"),
    ("collect-logs", api.admin_collect_logs, "admin-collect-logs"),
    ("readings", api.admin_readings, "admin-readings"),
    ("alarms", api.admin_alarms, "admin-alarms"),
    ("alarms/<int:pk>/ack", api.admin_alarm_ack, "admin-alarm-ack"),
    ("params", api.admin_params, "admin-params"),
    # 视频监控配置（v1.7.5）：网关 IP / 端口 + 各产线 RTSP 源
    ("video-config", api.admin_video_config, "admin-video-config"),
    ("params/<int:pk>", api.admin_param_detail, "admin-param-detail"),
    ("standard-units", api.admin_standard_units, "admin-standard-units"),
    ("energy-prices", api.admin_energy_prices, "admin-energy-prices"),
    ("gateways", api.admin_gateways, "admin-gateways"),
    ("gateways/<int:pk>", api.admin_gateway_detail, "admin-gateway-detail"),
    ("gateways/<int:pk>/test", api.admin_gateway_test, "admin-gateway-test"),
    ("meters", api.admin_meters, "admin-meters"),
    ("meters/<int:pk>", api.admin_meter_detail, "admin-meter-detail"),
    ("meters/<int:pk>/test", api.admin_meter_test, "admin-meter-test"),
    # 计量绑定树（v1.7.4）：左侧车间/类型/设备 + 右侧计量点分配
    ("meter-bindings/tree", api.admin_binding_tree, "admin-binding-tree"),
    ("meter-binding-workshops", api.admin_binding_workshops, "admin-binding-workshops"),
    ("meter-binding-workshops/<int:pk>", api.admin_binding_workshop_detail, "admin-binding-workshop-detail"),
    ("meter-binding-types", api.admin_binding_types, "admin-binding-types"),
    ("meter-binding-types/<int:pk>", api.admin_binding_type_detail, "admin-binding-type-detail"),
    ("meter-binding-devices", api.admin_binding_devices, "admin-binding-devices"),
    ("meter-binding-devices/<int:pk>", api.admin_binding_device_detail, "admin-binding-device-detail"),
    ("meter-binding-devices/<int:pk>/meters", api.admin_binding_device_meters, "admin-binding-device-meters"),
    # 读取当前值：只读、不落库（与 /test 的区别见 api.admin_meter_current 的说明）
    ("meters/<int:pk>/current", api.admin_meter_current, "admin-meter-current"),
    # PLC 产量采集（v1.8.0）：设备 / 点位 / 试读
    ("plc-devices", production_api.admin_plc_devices, "admin-plc-devices"),
    ("plc-devices/create", production_api.admin_plc_device_create, "admin-plc-device-create"),
    ("plc-devices/<int:pk>", production_api.admin_plc_device_detail, "admin-plc-device-detail"),
    ("plc-devices/<int:pk>/test", production_api.admin_plc_device_test, "admin-plc-device-test"),
    ("plc-points", production_api.admin_plc_points, "admin-plc-points"),
    ("plc-points/create", production_api.admin_plc_point_create, "admin-plc-point-create"),
    ("plc-points/<int:pk>", production_api.admin_plc_point_detail, "admin-plc-point-detail"),
    # 产量计量绑定（v1.8.0）：车间 → 类型 → 设备 → PLC 点位
    ("production-bindings/tree", production_api.admin_production_binding_tree, "admin-production-binding-tree"),
    ("production-binding-workshops", production_api.admin_prod_binding_workshops, "admin-prod-binding-workshops"),
    ("production-binding-workshops/<int:pk>", production_api.admin_prod_binding_workshop_detail, "admin-prod-binding-workshop-detail"),
    ("production-binding-types", production_api.admin_prod_binding_types, "admin-prod-binding-types"),
    ("production-binding-types/<int:pk>", production_api.admin_prod_binding_type_detail, "admin-prod-binding-type-detail"),
    ("production-binding-devices", production_api.admin_prod_binding_devices, "admin-prod-binding-devices"),
    ("production-binding-devices/<int:pk>", production_api.admin_prod_binding_device_detail, "admin-prod-binding-device-detail"),
    ("production-binding-devices/<int:pk>/points", production_api.admin_prod_binding_device_points, "admin-prod-binding-device-points"),
]

#: 后台配置组（挂在 /api/admin/ 下）
admin_urlpatterns = [path(route, view, name=name) for route, view, name in _ADMIN_ROUTES]

urlpatterns = energy_urlpatterns + [
    # 兼容旧前缀 /api/energy/admin/…（同 view 换名再注册一遍）
    path(f"admin/{route}", view, name=f"legacy-{name}")
    for route, view, name in _ADMIN_ROUTES
]
