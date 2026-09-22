"""只读数据接口。

接口清单（与 `backend/docs/api-contract.md` 对应）：

    GET /healthz                          健康探测
    GET /api/energy/overview              大屏总览（EnergySnapshot 同构）
    GET /api/energy/line/{code}           单线详情（LineData 同构）
    GET /api/energy/hourly                小时用量明细（可导出 / 复核）
    GET /api/energy/meters                计量点台账 + 最近读数 + 今日用量
    GET /api/energy/collect-logs          采集日志（后台「采集日志」页）
    GET /api/energy/production            ⬜ 产量接口 —— 本次留空，返回 501

说明：产量（瓶子数）的采集尚未改造，涉及产量的字段一律为 0 / null，
      不拿电量反推、也不填演示数据。
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Count, Max, Sum
from django.http import HttpRequest, JsonResponse
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET

from .models import CollectLog, HourlyUsage, Line, Meter
from .services.snapshot import build_line, build_overview

logger = logging.getLogger(__name__)


class EepJSONEncoder(DjangoJSONEncoder):
    """Decimal 统一转 float（前端要数字，不要字符串）。"""

    def default(self, o):  # noqa: D102
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            return o.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(o, date_cls):
            return o.isoformat()
        return super().default(o)


def json_ok(data, **kwargs) -> JsonResponse:
    return JsonResponse(data, encoder=EepJSONEncoder, json_dumps_params={"ensure_ascii": False}, **kwargs)


def _query_date(request: HttpRequest) -> date_cls:
    raw = request.GET.get("date")
    if raw:
        parsed = parse_date(raw)
        if parsed:
            return parsed
    return datetime.now().date()


@require_GET
def overview(request: HttpRequest) -> JsonResponse:
    """大屏总览。结构与 `types.ts` 的 EnergySnapshot 对齐。"""
    day = _query_date(request)
    now = datetime.combine(day, datetime.now().time()) if day != datetime.now().date() else datetime.now()
    return json_ok(build_overview(now))


@require_GET
def line_detail(request: HttpRequest, code: str) -> JsonResponse:
    line = Line.objects.filter(code=code).first()
    if line is None:
        return json_ok({"detail": f"产线不存在：{code}"}, status=404)
    day = _query_date(request)
    now = datetime.combine(day, datetime.now().time()) if day != datetime.now().date() else datetime.now()
    return json_ok(build_line(line, now))


@require_GET
def hourly(request: HttpRequest) -> JsonResponse:
    """小时用量明细。默认返回今天；带 `line` / `meter` / `date` 可缩小范围。"""
    day = _query_date(request)
    qs = HourlyUsage.objects.filter(date=day).select_related("meter", "meter__line")

    line_code = request.GET.get("line")
    if line_code:
        qs = qs.filter(meter__line__code=line_code)
    meter_code = request.GET.get("meter")
    if meter_code:
        qs = qs.filter(meter__code=meter_code)
    if request.GET.get("kind"):
        qs = qs.filter(meter__kind=request.GET["kind"])

    rows = [
        {
            "meter": r.meter.code,
            "meterName": r.meter.name,
            "line": r.meter.line.code if r.meter.line else None,
            "kind": r.meter.kind,
            "date": r.date.isoformat(),
            "hour": r.hour,
            "usage": float(r.usage) if r.usage is not None else None,
            "unit": r.unit,
            "quality": r.quality,
            "startAt": r.start_at.strftime("%Y-%m-%d %H:%M:%S") if r.start_at else None,
            "endAt": r.end_at.strftime("%Y-%m-%d %H:%M:%S") if r.end_at else None,
            "note": r.note,
        }
        for r in qs.order_by("meter__code", "hour")
    ]
    return json_ok({"date": day.isoformat(), "count": len(rows), "items": rows})


@require_GET
def meters(request: HttpRequest) -> JsonResponse:
    """计量点台账：给后台「计量点位」「数据质量」页用。"""
    today = datetime.now().date()
    today_usage = {
        row["meter_id"]: row
        for row in HourlyUsage.objects.filter(date=today, quality__in=HourlyUsage.TRUSTED)
        .values("meter_id")
        .annotate(total=Sum("usage"), hours=Count("id"))
    }
    items = []
    for m in Meter.objects.select_related("line", "gateway").order_by("kind", "code"):
        agg = today_usage.get(m.id) or {}
        items.append(
            {
                "code": m.code,
                "name": m.name,
                "kind": m.kind,
                "kindText": m.get_kind_display(),
                "scope": m.scope,
                "role": m.get_role_display(),
                "line": m.line.code if m.line else None,
                "lineName": m.line.name if m.line else "车间级",
                "gateway": m.gateway.code,
                "gatewayName": m.gateway.name,
                "endpoint": m.gateway.endpoint,
                "slave": m.slave,
                "register": m.register,
                "unit": m.unit,
                "scale": float(m.scale),
                "enabled": m.enabled,
                "remark": m.remark,
                "lastValue": float(m.last_value) if m.last_value is not None else None,
                "lastAt": m.last_at.strftime("%Y-%m-%d %H:%M:%S") if m.last_at else None,
                "lastOk": m.last_ok,
                "lastError": m.last_error,
                "todayUsage": float(agg["total"]) if agg.get("total") is not None else None,
                "todayHours": agg.get("hours", 0),
            }
        )
    return json_ok({"count": len(items), "items": items})


@require_GET
def collect_logs(request: HttpRequest) -> JsonResponse:
    limit = min(int(request.GET.get("limit", 50)), 500)
    rows = CollectLog.objects.select_related("gateway").order_by("-started_at")[:limit]
    items = [
        {
            "gateway": r.gateway.code if r.gateway else "-",
            "gatewayName": r.gateway.name if r.gateway else "",
            "startedAt": r.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "finishedAt": r.finished_at.strftime("%Y-%m-%d %H:%M:%S") if r.finished_at else None,
            "durationMs": r.duration_ms,
            "total": r.total,
            "okCount": r.ok_count,
            "failCount": r.fail_count,
            "status": "success" if r.fail_count == 0 else ("partial" if r.ok_count else "failed"),
            "message": r.message,
        }
        for r in rows
    ]
    return json_ok({"count": len(items), "items": items})


@require_GET
def collect_summary(request: HttpRequest) -> JsonResponse:
    """采集总览：给巡检 / 首页卡片用。"""
    since = datetime.now() - timedelta(hours=24)
    agg = CollectLog.objects.filter(started_at__gte=since).aggregate(
        rounds=Count("id"), ok=Sum("ok_count"), fail=Sum("fail_count"), last=Max("started_at")
    )
    stale = Meter.objects.filter(enabled=True).exclude(last_ok=True).count()
    return json_ok(
        {
            "windowHours": 24,
            "rounds": agg["rounds"] or 0,
            "okCount": agg["ok"] or 0,
            "failCount": agg["fail"] or 0,
            "lastCollectAt": agg["last"].strftime("%Y-%m-%d %H:%M:%S") if agg["last"] else None,
            "abnormalMeters": stale,
            "meterTotal": Meter.objects.filter(enabled=True).count(),
        }
    )


@require_GET
def production(request: HttpRequest) -> JsonResponse:
    """⬜ 产量接口 —— 本次留空。

    现场产量的取数方式（灌装机计数器 / 装箱机 PLC / MES 班次报工）尚未改造，
    因此这里明确返回 501，而不是给一个假结构 —— 免得前端以为已经接通。
    """
    return json_ok(
        {
            "detail": "产量采集尚未改造，接口预留未实现",
            "pending": [
                "灌装机计数器 / 装箱机 PLC 取数方式确认",
                "MES 班次报工对接（含品项、班次、停机原因）",
                "产量与电量的时间口径对齐（整点对齐后方可算单位电耗）",
            ],
        },
        status=501,
    )
