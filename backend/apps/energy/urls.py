"""能源数据接口路由。"""

from django.urls import path

from . import api

urlpatterns = [
    path("overview", api.overview, name="energy-overview"),
    path("line/<str:code>", api.line_detail, name="energy-line"),
    path("hourly", api.hourly, name="energy-hourly"),
    path("meters", api.meters, name="energy-meters"),
    path("collect-logs", api.collect_logs, name="energy-collect-logs"),
    path("collect-summary", api.collect_summary, name="energy-collect-summary"),
    path("production", api.production, name="energy-production"),
]
