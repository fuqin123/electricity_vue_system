"""数据接口。

分两组，前缀不同、权限要求也不同：

**能源只读**（大屏用，结构与 `frontend/packages/shared/src/types.ts` 对齐）
    GET  /api/energy/overview              大屏总览（EnergySnapshot 同构）
    GET  /api/energy/line/{code}           单线详情（LineData 同构，含 monthly12 / yearly5）
    GET  /api/energy/hourly                小时用量明细（可导出 / 复核）
    GET  /api/energy/meters                计量点台账 + 最近读数 + 今日用量
    GET  /api/energy/collect-logs          采集日志
    GET  /api/energy/collect-summary       采集总览（巡检 / 首页卡片）
    GET  /api/energy/period-summary        日 / 月 / 年总计（读 eep_period_summary）
    GET  /api/energy/range                 ★ 按时间段查询用量（大屏与后台共用，支持时/日/月粒度）
    GET  /api/energy/production            产量（当前未接入，如实返回 null + available=false）

**后台配置**（管理界面用，与 `/api/energy/*` 读**同一份库**）
    GET         /api/admin/overview        首页看板
    GET         /api/admin/lines           产线主数据
    GET/POST    /api/admin/gateways        网关列表 / 新增
    PUT/DELETE  /api/admin/gateways/{id}   修改 / 删除
    POST        /api/admin/gateways/{id}/test        连接测试
    GET/POST    /api/admin/meters          计量点列表 / 新增
    PUT/DELETE  /api/admin/meters/{id}     修改 / 删除
    POST        /api/admin/meters/{id}/test          试采一次（**返回真实读数**）
    GET         /api/admin/collect-logs    采集日志（后台分页结构）
    GET         /api/admin/quality         数据质量（按产线）

设计要求（改之前先看）：
  · **后台与大屏读同一张表、同一套口径** —— 这是「两边数字必须一致」的实现方式，
    不要为了省事在大屏侧另算一遍（历史教训：两处口径分叉，现场对不上账）。
  · 写操作统一把 `source` 置为 `admin`，这样 `seed_registry` 默认不会把后台的修改覆盖掉。
  · 试采接口是现场调试的主力工具：改完从站号/寄存器/倍率，不用等下一轮采集，
    点一下就能看到真实读数和原始报文。
"""

from __future__ import annotations

import json
import logging
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Count, Max, Sum
from django.http import HttpRequest, JsonResponse
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .collectors import build_collector
from .models import CollectLog, Gateway, HourlyProduction, HourlyUsage, Line, Meter, PeriodSummary
from .services import ranges, slots
from .services import summary as S
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
    """统一 JSON 出口。

    ⚠ 列表也要能直接返回（网关列表 / 数据质量等接口给的是数组）——
      JsonResponse 默认 safe=True 只允许 dict，传 list 会 500。
      之前 /api/admin/gateways、/api/admin/quality 一直因此 500，
      后台网关页拿不到真实数据（v1.2.1 修复）。
    """
    return JsonResponse(
        data, encoder=EepJSONEncoder, json_dumps_params={"ensure_ascii": False}, safe=False, **kwargs
    )


def json_err(message: str, status: int = 400, **extra) -> JsonResponse:
    return json_ok({"detail": message, **extra}, status=status)


# ══════════════════════════════════════════════════════════════
#  通用小工具
# ══════════════════════════════════════════════════════════════


def _query_date(request: HttpRequest) -> date_cls:
    raw = request.GET.get("date")
    if raw:
        parsed = parse_date(raw)
        if parsed:
            return parsed
    return datetime.now().date()


def _now_for(day: date_cls) -> datetime:
    """按指定日期构造「现在」—— 决定接口把哪些小时算作「已过去」。

    · 今天     → 真实当前时刻（当天的 24 小时里只算到当前小时）
    · 历史日期 → 当天 23:59:59（**整天都已经结束**，24 小时全部计入）
    · 未来日期 → 当天 00:00:00（什么都没有，避免把未来数据算进来）

    历史日期这里必须用「当天末」而不是「当天 + 当前时分」：
    否则上午 10 点去看昨天的数据，昨天 11—23 点会被当成「还没到」而全部留空，
    现场会以为昨天的数据丢了。
    """
    today = datetime.now().date()
    if day == today:
        return datetime.now()
    if day < today:
        return datetime.combine(day, time(23, 59, 59))
    return datetime.combine(day, time(0, 0, 0))


def _body(request: HttpRequest) -> dict:
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"请求体不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return data


def _paged(request: HttpRequest, rows: list[dict]) -> dict:
    """内存分页。数据量都在千级以内（计量点、网关、采集日志），不值得为它上分页库。"""
    page = max(1, int(request.GET.get("page", 1) or 1))
    size = max(1, min(int(request.GET.get("pageSize", 20) or 20), 200))
    keyword = str(request.GET.get("keyword", "") or "").strip().lower()
    if keyword:
        rows = [r for r in rows if keyword in json.dumps(r, ensure_ascii=False).lower()]
    start = (page - 1) * size
    return {"list": rows[start : start + size], "total": len(rows), "page": page, "pageSize": size}


def _dec(raw, default=None):
    if raw is None or raw == "":
        return default
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return default


# ══════════════════════════════════════════════════════════════
#  序列化（DB → 前端契约）
# ══════════════════════════════════════════════════════════════

CATEGORY = {Meter.KIND_ELEC: "电", Meter.KIND_WATER: "水", Meter.KIND_STEAM: "汽"}
PROTOCOL = {Gateway.KIND_MODBUS: "Modbus RTU over TCP", Gateway.KIND_HTTP: "HTTP API"}


def gateway_json(g: Gateway, *, point_count: int = 0, last_heartbeat=None) -> dict:
    online = bool(last_heartbeat and (datetime.now() - last_heartbeat) < timedelta(minutes=5))
    return {
        "id": g.id,
        "code": g.code,
        "name": g.name,
        "kind": g.kind,
        "protocol": PROTOCOL.get(g.kind, g.kind),
        "ip": g.host or "",
        "port": g.port,
        "baseUrl": g.base_url or "",
        "endpoint": g.endpoint,
        "pointCount": point_count,
        "status": "online" if online else "offline",
        "lastHeartbeat": last_heartbeat.strftime("%Y-%m-%d %H:%M:%S") if last_heartbeat else "",
        "timeoutMs": g.timeout_ms,
        "intervalSeconds": g.interval_seconds,
        "enabled": g.enabled,
        "source": g.source,
        "remark": g.remark,
    }


def meter_json(m: Meter, *, today_usage=None, today_hours: int = 0) -> dict:
    """计量点 → 前端结构。既供列表展示，也供后台表单回填（字段一一对应）。"""
    if not m.enabled:
        status = "offline"
    elif m.last_ok:
        status = "online"
    elif m.last_at is not None:
        status = "fault"
    else:
        status = "offline"

    addr_parts = []
    if m.gateway.host:
        addr_parts.append(f"{m.gateway.host}:{m.gateway.port}" if m.gateway.port else m.gateway.host)
    if m.gateway.base_url:
        addr_parts.append(m.gateway.base_url)
    if m.slave is not None:
        addr_parts.append(f"从站 {m.slave}")
    if m.register is not None:
        addr_parts.append(f"寄存器 {m.register}×{m.registers}")

    return {
        # ── 列表展示 ──
        "id": m.id,
        "code": m.code,
        "name": m.name,
        "category": CATEGORY.get(m.kind, m.kind),
        "lineCode": m.line.code if m.line else "—",
        "protocol": PROTOCOL.get(m.gateway.kind, m.gateway.kind),
        "address": " / ".join(addr_parts) or "—",
        "unit": m.unit,
        "factor": float(m.scale),
        "status": status,
        "lastAt": m.last_at.strftime("%Y-%m-%d %H:%M:%S") if m.last_at else "",

        # ── 表单回填（可编辑配置）──
        "kind": m.kind,
        "scope": m.scope,
        "role": m.role,
        "gatewayId": m.gateway_id,
        "gatewayCode": m.gateway.code,
        "slave": m.slave,
        "register": m.register,
        "registers": m.registers,
        "funcCode": m.func_code,
        "wordOrder": m.word_order,
        "urlPath": m.url_path,
        "params": m.params or {},
        "valuePath": m.value_path,
        "timePath": m.time_path,
        "maxHourly": float(m.max_hourly) if m.max_hourly is not None else None,
        "enabled": m.enabled,
        "source": m.source,
        "remark": m.remark,
        "assetNo": m.asset_no,
        "installDate": m.install_date.isoformat() if m.install_date else "",

        # ── 最近状态 ──
        "lastValue": float(m.last_value) if m.last_value is not None else None,
        "lastOk": m.last_ok,
        "lastError": m.last_error,
        "todayUsage": float(today_usage) if today_usage is not None else None,
        "todayHours": today_hours,
    }


def line_json(ln: Line, *, meter_count: int = 0, counter: str = "") -> dict:
    return {
        "id": ln.id,
        "code": ln.code,
        "name": ln.name,
        "product": ln.product,
        "rated": ln.rated,
        "workshop": "灌装车间",
        "meterNo": f"{meter_count} 个电表点位",
        "counterNo": counter or "产量计数器待接入",
        "status": "running" if ln.enabled else "maintenance",
        "commissionedAt": "",
    }


def collect_log_json(r: CollectLog) -> dict:
    return {
        "id": r.id,
        "taskName": f"轮询采集（{r.gateway.code}）" if r.gateway else "轮询采集",
        "gateway": r.gateway.name if r.gateway else "-",
        "pointCount": r.total,
        "rows": r.ok_count,
        "durationMs": r.duration_ms,
        "result": "success" if r.fail_count == 0 else ("partial" if r.ok_count else "failed"),
        "message": r.message,
        "startedAt": r.started_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _meter_today_usage() -> dict:
    today = datetime.now().date()
    return {
        row["meter_id"]: row
        for row in HourlyUsage.objects.filter(date=today, quality__in=HourlyUsage.TRUSTED)
        .values("meter_id")
        .annotate(total=Sum("usage"), hours=Count("id"))
    }


def _gateway_last_heartbeat() -> dict:
    """用采集日志的最近一次开始时间当作网关心跳。"""
    return {
        row["gateway_id"]: row["last"]
        for row in CollectLog.objects.exclude(gateway=None)
        .values("gateway_id")
        .annotate(last=Max("started_at"))
    }


# ══════════════════════════════════════════════════════════════
#  能源只读接口
# ══════════════════════════════════════════════════════════════


@require_GET
def overview(request: HttpRequest) -> JsonResponse:
    """大屏总览。结构与 `types.ts` 的 EnergySnapshot 对齐。"""
    day = _query_date(request)
    return json_ok(build_overview(_now_for(day)))


@require_GET
def line_detail(request: HttpRequest, code: str) -> JsonResponse:
    line = Line.objects.filter(code=code).first()
    if line is None:
        return json_err(f"产线不存在：{code}", 404)
    day = _query_date(request)
    return json_ok(build_line(line, _now_for(day)))


@require_GET
def hourly(request: HttpRequest) -> JsonResponse:
    """小时用量明细（可导出 / 复核）。

    时间范围的两种写法：
      · `date=2026-09-22`                 —— 单日（大屏 / 简单场景）
      · `from=2026-09-01&to=2026-09-22`   —— 区间（后台查询页）

    带 `page`/`pageSize` 时分页返回，否则最多返回 5000 条（避免一次拖太多）。
    """
    from_raw, to_raw = request.GET.get("from"), request.GET.get("to")
    if from_raw or to_raw:
        start, end = ranges.parse_range(request)
        qs = HourlyUsage.objects.filter(date__gte=start, date__lte=end).select_related("meter", "meter__line")
        scope_desc = f"{start} ~ {end}"
    else:
        start = end = _query_date(request)
        qs = HourlyUsage.objects.filter(date=start).select_related("meter", "meter__line")
        scope_desc = str(start)

    if request.GET.get("line"):
        qs = qs.filter(meter__line__code=request.GET["line"])
    if request.GET.get("meter"):
        qs = qs.filter(meter__code=request.GET["meter"])
    if request.GET.get("kind"):
        qs = qs.filter(meter__kind=request.GET["kind"])
    if request.GET.get("quality"):
        qs = qs.filter(quality=request.GET["quality"])

    qs = qs.order_by("-date", "-hour", "meter__code")
    total = qs.count()

    paged = request.GET.get("page") is not None
    if paged:
        page = max(1, int(request.GET.get("page", 1) or 1))
        size = max(1, min(int(request.GET.get("pageSize", 50) or 50), 500))
        qs = qs[(page - 1) * size : page * size]
    else:
        qs = qs[:5000]

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
        for r in qs
    ]
    return json_ok(
        {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "scope": scope_desc,
            "count": len(rows),
            "total": total,
            "items": rows,
        }
    )


@require_GET
def meters(request: HttpRequest) -> JsonResponse:
    """计量点台账：给后台「计量点位」「数据质量」页用。"""
    usage = _meter_today_usage()
    items = []
    for m in Meter.objects.select_related("line", "gateway").order_by("kind", "code"):
        agg = usage.get(m.id) or {}
        items.append(meter_json(m, today_usage=agg.get("total"), today_hours=agg.get("hours", 0)))
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
            "hourlyUsageRows": HourlyUsage.objects.filter(date=datetime.now().date()).count(),
            "productionRows": HourlyProduction.objects.filter(date=datetime.now().date()).count(),
            "summaryRows": PeriodSummary.objects.count(),
            # 当前采集节奏（现场核对「是不是每小时采一次」时看这个）
            "cadence": slots.describe(),
        }
    )


@require_GET
def period_summary(request: HttpRequest) -> JsonResponse:
    """日 / 月 / 年总计 —— 直接读 `eep_period_summary`（后台读的也是它，两边必然一致）。

    参数：dim=day|month|year（默认 day）、scope=line|workshop（默认 workshop）、
          line=A1、limit=N（默认 40，按周期倒序）
    """
    dim = request.GET.get("dim", PeriodSummary.DIM_DAY)
    scope = request.GET.get("scope", PeriodSummary.SCOPE_WORKSHOP)
    if dim not in dict(PeriodSummary.DIM_CHOICES):
        return json_err(f"dim 取值必须是 day / month / year，收到 {dim}")
    if scope not in dict(PeriodSummary.SCOPE_CHOICES):
        return json_err(f"scope 取值必须是 line / workshop，收到 {scope}")

    qs = PeriodSummary.objects.filter(dim=dim, scope=scope).select_related("line")
    if scope == PeriodSummary.SCOPE_LINE:
        code = request.GET.get("line")
        if not code:
            return json_err("scope=line 时必须给 line 参数，如 line=A1")
        qs = qs.filter(line__code=code)
    else:
        qs = qs.filter(line__isnull=True)

    limit = min(int(request.GET.get("limit", 40)), 400)
    items = [
        {
            "scope": r.scope,
            "line": r.line.code if r.line else None,
            "dim": r.dim,
            "periodKey": r.period_key,
            "startDate": r.start_date.isoformat(),
            "endDate": r.end_date.isoformat(),
            # ⚠ 这里**不要**写 float(r.xxx)：`eep_period_summary` 的数值字段是可空的
            #   ——「该周期一条可信数据都没有」写 NULL（绝不补 0），
            #   而 float(None) 会抛 TypeError 让整个接口 500（B-509）。
            #   直接把 Decimal / None 交给 `EepJSONEncoder`：
            #   Decimal 统一转 float，None 原生就是 JSON null。
            "output": r.output,
            "kwh": r.kwh,
            "waterT": r.water_t,
            "steamT": r.steam_t,
            "planOutput": r.plan_output,
            "unitKwh": r.unit_kwh,
            "coveredHours": r.covered_hours,
            "quality": r.quality,
            "computedAt": r.computed_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for r in qs.order_by("-period_key")[:limit]
    ]
    return json_ok({"dim": dim, "scope": scope, "count": len(items), "items": items})


@require_GET
def usage_range(request: HttpRequest) -> JsonResponse:
    """★ 按时间段查询用量 —— 大屏与后台共用。

    参数：
      from / to          日期区间（YYYY-MM-DD，缺省为今天；最长 366 天）
      granularity        hour | day | month（默认 day）
      scope              workshop | line | all（默认 all：车间 + 各线）
      line               当 scope=line 时指定产线，如 A1

    返回：labels（时间轴）+ series（每条一组，车间/各线）+ utility（水汽，仅车间）
          + totals（区间合计）+ productionAvailable（产量是否已接入）。

    ⚠ 产量字段在产量侧接入前恒为 0，并带 `productionAvailable=false` 显式标注 ——
      界面据此显示「产量未接入」而不是让人以为「今天真没产量」。
    """
    start, end = ranges.parse_range(request)
    granularity = request.GET.get("granularity", "day")
    scope = request.GET.get("scope", "all")
    if granularity not in ("hour", "day", "month"):
        return json_err(f"granularity 取值必须是 hour / day / month，收到 {granularity}")
    if scope not in ("workshop", "line", "all"):
        return json_err(f"scope 取值必须是 workshop / line / all，收到 {scope}")

    line_code = request.GET.get("line")
    if scope == "line" and not line_code:
        return json_err("scope=line 时必须给 line 参数，如 line=A1")

    day = _query_date(request) if request.GET.get("date") else datetime.now().date()
    now = datetime.now() if end >= datetime.now().date() else datetime.combine(end, datetime.max.time())

    data = ranges.usage_range(
        start, end, granularity=granularity, scope=scope, line_code=line_code, now=now
    )
    data["cadence"] = slots.describe()
    data["serverDate"] = day.isoformat()
    return json_ok(data)


@require_GET
def production(request: HttpRequest) -> JsonResponse:
    """产量接口。

    ⚠ 产量侧（灌装机计数器 / 装箱机 PLC / MES 班次报工）**尚未改造**，
      因此 `available = false`、`output` 一律为 **null**（v1.2.1 起统一为 null，
      0 会被读成「确实没产出」，与「还没接数据」是两回事）。

    与 v1.2.0 的差别：那时返回 501（接口不存在）。现在表结构已经建好
      （`eep_hourly_production`），接口**如实返回 null** 并用 `available` 明确标注
      「这不是真实产量」，比直接 501 更好用 —— 前端不必为「产量未接入」写特殊分支，
      只要看 `available` 就知道该显示数值还是显示 `--`。
    """
    day = _query_date(request)
    rows = HourlyProduction.objects.filter(date=day).select_related("line").order_by("line__code", "hour")
    real = rows.exclude(source__in=(HourlyProduction.SOURCE_DEMO, HourlyProduction.SOURCE_NONE))

    return json_ok(
        {
            "date": day.isoformat(),
            "available": real.exists(),
            "status": "pending" if not real.exists() else "ok",
            "reason": "产量侧取数方式尚未改造（灌装机计数器 / 装箱机 PLC / MES 班报工）",
            "total": int(sum((r.output or 0) for r in real)),
            "count": rows.count(),
            "items": [
                {
                    "line": r.line.code,
                    "hour": r.hour,
                    "output": float(r.output) if r.output is not None else None,
                    "quality": r.quality,
                    "source": r.source,
                    "note": r.note,
                }
                for r in rows
            ],
            "pending": [
                "灌装机计数器 / 装箱机 PLC 取数方式确认",
                "MES 班次报工对接（含品项、班次、停机原因）",
                "产量与电量的时间口径对齐（整点对齐后方可算单位电耗）",
            ],
        }
    )


# ══════════════════════════════════════════════════════════════
#  后台配置接口
# ══════════════════════════════════════════════════════════════


@require_GET
def admin_overview(request: HttpRequest) -> JsonResponse:
    """后台首页看板。**与 /api/energy/overview 同源**（都读 period_summary + hourly_usage）。"""
    now = datetime.now()
    ws = build_overview(now)["workshop"]
    since = now - timedelta(hours=24)

    usage = _meter_today_usage()
    expect = Meter.objects.filter(enabled=True, kind=Meter.KIND_ELEC).count() * (now.hour + 1)
    received = sum(a.get("hours", 0) for a in usage.values())

    monthly12 = [
        {"label": p["label"], "output": p["output"], "kwh": p["kwh"] or 0, "unitKwh": p["unitKwh"]}
        for p in ws["monthly12"]
    ]

    return json_ok(
        {
            "kpi": {
                "todayOutput": ws["today"]["output"],
                "todayKwh": ws["today"]["kwh"] or 0,
                "unitKwh": ws["today"]["unitKwh"],
                "activeAlarms": len([a for a in ws["alerts"] if a["level"] == "err"]),
                "onlineGateways": len(
                    [
                        g
                        for g in Gateway.objects.filter(enabled=True)
                        if (CollectLog.objects.filter(gateway=g).order_by("-started_at").first())
                    ]
                ),
                "totalGateways": Gateway.objects.filter(enabled=True).count(),
                "dataQualityRate": round(min(received / expect, 1) * 100, 2) if expect else 0,
            },
            "hourly": [
                {
                    "label": f"{h:02d}:00",
                    # 产量未接入时 output 为 null（不是 0），让后台看板与线显示 --
                    "output": w["output"],
                    "kwh": w["kwh"] or 0,
                    "unitKwh": w["unitKwh"],
                }
                for h, w in enumerate(ws["hours"])
            ],
            "daily30": [
                {"label": p["label"], "output": p["output"], "kwh": p["kwh"] or 0, "unitKwh": p["unitKwh"]}
                for p in ws["daily30"]
            ],
            "monthly12": monthly12,
            "lineUnitKwh": [
                {
                    "lineCode": ln["id"],
                    "lineName": ln["name"],
                    "unitKwh": ln["today"]["unitKwh"],
                    "output": ln["today"]["output"],
                }
                for ln in ws["lines"]
            ],
            # 告警引擎尚未接入，先如实给空数组（后台页面会显示「暂无」而不是假数据）
            "latestAlarms": [],
            "dataSource": {
                "productionAvailable": any(
                    HourlyProduction.objects.filter(date=now.date())
                    .exclude(source__in=(HourlyProduction.SOURCE_DEMO, HourlyProduction.SOURCE_NONE))
                    .exists()
                    for _ in [0]
                ),
                "collectRounds24h": CollectLog.objects.filter(started_at__gte=since).count(),
            },
        }
    )


@require_GET
def admin_lines(request: HttpRequest) -> JsonResponse:
    rows = []
    for ln in Line.objects.order_by("sort", "code"):
        cnt = ln.meters.filter(kind=Meter.KIND_ELEC, enabled=True).count()
        rows.append(line_json(ln, meter_count=cnt))
    return json_ok(_paged(request, rows))


@require_GET
def admin_quality(request: HttpRequest) -> JsonResponse:
    """数据质量（按产线）：应报 / 实收 / 缺报。"""
    today = datetime.now().date()
    expect = datetime.now().hour + 1
    rows = []
    for ln in Line.objects.filter(enabled=True).order_by("sort", "code"):
        meters = [m for m in ln.meters.all() if m.kind == Meter.KIND_ELEC and m.enabled]
        ok_rows = HourlyUsage.objects.filter(
            meter__in=meters, date=today, quality__in=HourlyUsage.TRUSTED
        )
        received = ok_rows.values("hour").distinct().count()
        missing = sorted(set(range(expect)) - set(ok_rows.values_list("hour", flat=True)))
        last = ok_rows.aggregate(last=Max("computed_at"))["last"]
        rows.append(
            {
                "lineCode": ln.code,
                "lineName": ln.name,
                "expectPoints": expect,
                "receivedPoints": received,
                "missingPoints": max(0, expect - received),
                "rate": round(received / expect * 100, 2) if expect else 0,
                "missingHours": "、".join(f"{h:02d}" for h in missing[:12]) or "—",
                "lastAt": last.strftime("%Y-%m-%d %H:%M:%S") if last else "",
            }
        )
    return json_ok(rows)


@require_GET
def admin_collect_logs(request: HttpRequest) -> JsonResponse:
    rows = [collect_log_json(r) for r in CollectLog.objects.select_related("gateway").order_by("-started_at")[:500]]
    return json_ok(_paged(request, rows))


# ── 网关读写 ──────────────────────────────────────────────────

GATEWAY_FIELDS = ("code", "name", "kind", "host", "port", "base_url", "timeout_ms", "interval_seconds", "enabled", "remark")
#: 前端字段名 → 模型字段名（前端统一 camelCase）
GATEWAY_ALIAS = {
    "baseUrl": "base_url",
    "timeoutMs": "timeout_ms",
    "intervalSeconds": "interval_seconds",
}


def _apply(data: dict, allowed: tuple, alias: dict) -> dict:
    out = {}
    for k, v in data.items():
        field = alias.get(k, k)
        if field in allowed:
            out[field] = v
    return out


@require_http_methods(["GET", "POST"])
@csrf_exempt
def admin_gateways(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        hb = _gateway_last_heartbeat()
        counts = {
            row["gateway_id"]: row["n"]
            for row in Meter.objects.values("gateway_id").annotate(n=Count("id"))
        }
        rows = [
            gateway_json(g, point_count=counts.get(g.id, 0), last_heartbeat=hb.get(g.id))
            for g in Gateway.objects.order_by("code")
        ]
        return json_ok(rows)

    try:
        data = _body(request)
    except ValueError as exc:
        return json_err(str(exc))
    if not data.get("code") or not data.get("name"):
        return json_err("网关编号与名称为必填项")
    if Gateway.objects.filter(code=data["code"]).exists():
        return json_err(f"网关编号已存在：{data['code']}", 409)

    fields = _apply(data, GATEWAY_FIELDS, GATEWAY_ALIAS)
    fields.setdefault("kind", Gateway.KIND_MODBUS)
    fields["source"] = Gateway.SOURCE_ADMIN
    gw = Gateway.objects.create(**fields)
    return json_ok(gateway_json(gw), status=201)


@require_http_methods(["GET", "PUT", "DELETE"])
@csrf_exempt
def admin_gateway_detail(request: HttpRequest, pk: int) -> JsonResponse:
    gw = Gateway.objects.filter(pk=pk).first()
    if gw is None:
        return json_err("网关不存在", 404)

    if request.method == "GET":
        return json_ok(gateway_json(gw, point_count=gw.meters.count()))

    if request.method == "DELETE":
        if gw.meters.exists():
            return json_err(f"该网关下还有 {gw.meters.count()} 个计量点，请先删除或改挂到其他网关", 409)
        gw.delete()
        return json_ok({"deleted": pk})

    try:
        data = _body(request)
    except ValueError as exc:
        return json_err(str(exc))
    for field, value in _apply(data, GATEWAY_FIELDS, GATEWAY_ALIAS).items():
        setattr(gw, field, value)
    gw.source = Gateway.SOURCE_ADMIN  # 后台改过 → 归后台维护，seed_registry 不再覆盖
    gw.save()
    return json_ok(gateway_json(gw, point_count=gw.meters.count()))


@require_http_methods(["POST"])
@csrf_exempt
def admin_gateway_test(request: HttpRequest, pk: int) -> JsonResponse:
    """网关连接测试：对旗下每个计量点试读一次，返回真实读数与错误。"""
    gw = Gateway.objects.filter(pk=pk).first()
    if gw is None:
        return json_err("网关不存在", 404)
    ms = [m for m in gw.meters.filter(enabled=True) if m.enabled]
    if not ms:
        return json_ok({"gateway": gw.code, "reachable": False, "detail": "该网关下没有启用中的计量点", "items": []})

    try:
        result = _try_read(gw, ms)
    except Exception as exc:  # noqa: BLE001
        logger.exception("网关测试失败：%s", gw.code)
        return json_ok({"gateway": gw.code, "reachable": False, "detail": f"{type(exc).__name__}: {exc}", "items": []})

    ok = sum(1 for r in result["items"] if r["ok"])
    return json_ok(
        {
            "gateway": gw.code,
            "endpoint": gw.endpoint,
            "reachable": ok > 0,
            "detail": f"{ok}/{len(result['items'])} 个点位读取成功，耗时 {result['durationMs']}ms",
            **result,
        }
    )


# ── 计量点读写 ────────────────────────────────────────────────

METER_FIELDS = (
    "code", "name", "kind", "scope", "role", "slave", "register", "registers", "func_code",
    "word_order", "url_path", "params", "value_path", "time_path", "scale", "unit",
    "max_hourly", "enabled", "remark", "asset_no", "install_date",
)
METER_ALIAS = {
    "funcCode": "func_code",
    "wordOrder": "word_order",
    "urlPath": "url_path",
    "valuePath": "value_path",
    "timePath": "time_path",
    "maxHourly": "max_hourly",
    "assetNo": "asset_no",
    "installDate": "install_date",
    "lineCode": "line",
    "gatewayId": "gateway",
}


@require_http_methods(["GET", "POST"])
@csrf_exempt
def admin_meters(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        usage = _meter_today_usage()
        rows = []
        for m in Meter.objects.select_related("line", "gateway").order_by("kind", "code"):
            agg = usage.get(m.id) or {}
            rows.append(meter_json(m, today_usage=agg.get("total"), today_hours=agg.get("hours", 0)))
        if request.GET.get("kind"):
            rows = [r for r in rows if r["kind"] == request.GET["kind"]]
        if request.GET.get("category"):
            rows = [r for r in rows if r["category"] == request.GET["category"]]
        if request.GET.get("gateway"):
            rows = [r for r in rows if r["gatewayCode"] == request.GET["gateway"]]
        return json_ok(_paged(request, rows))

    try:
        data = _body(request)
    except ValueError as exc:
        return json_err(str(exc))
    if not data.get("code") or not data.get("name"):
        return json_err("点位编号与名称为必填项")
    if not data.get("gatewayId"):
        return json_err("必须选择所属网关")
    if Meter.objects.filter(code=data["code"]).exists():
        return json_err(f"点位编号已存在：{data['code']}", 409)

    try:
        m = _save_meter(None, data)
    except ValueError as exc:
        return json_err(str(exc))
    return json_ok(meter_json(m), status=201)


@require_http_methods(["GET", "PUT", "DELETE"])
@csrf_exempt
def admin_meter_detail(request: HttpRequest, pk: int) -> JsonResponse:
    m = Meter.objects.filter(pk=pk).first()
    if m is None:
        return json_err("计量点不存在", 404)

    if request.method == "GET":
        usage = _meter_today_usage().get(m.id) or {}
        return json_ok(meter_json(m, today_usage=usage.get("total"), today_hours=usage.get("hours", 0)))

    if request.method == "DELETE":
        m.delete()
        return json_ok({"deleted": pk})

    try:
        data = _body(request)
        m = _save_meter(m, data)
    except ValueError as exc:
        return json_err(str(exc))
    return json_ok(meter_json(m))


def _save_meter(m: Meter | None, data: dict) -> Meter:
    """新增或更新计量点。字段校验集中在这里，供 POST 与 PUT 共用。"""
    is_new = m is None
    if is_new:
        m = Meter(code=str(data["code"]).strip(), name=str(data["name"]).strip(), gateway_id=int(data["gatewayId"]))
        m.kind = data.get("kind", Meter.KIND_ELEC)

    if "code" in data:
        m.code = str(data["code"]).strip()
    if "name" in data:
        m.name = str(data["name"]).strip()

    for field, value in _apply(data, METER_FIELDS, METER_ALIAS).items():
        if field == "gateway":
            continue
        setattr(m, field, value)

    if "gatewayId" in data or "gateway" in data:
        gw_id = data.get("gatewayId", data.get("gateway"))
        gw = Gateway.objects.filter(pk=gw_id).first()
        if gw is None:
            raise ValueError(f"网关不存在：{gw_id}")
        m.gateway = gw

    # 产线引用
    if "lineCode" in data:
        code = data["lineCode"]
        if code in ("", "—", None):
            m.line = None
        else:
            ln = Line.objects.filter(code=str(code).upper()).first()
            if ln is None:
                raise ValueError(f"产线不存在：{code}")
            m.line = ln

    # 车间总表不挂产线
    if m.scope == Meter.SCOPE_WORKSHOP:
        m.line = None

    # 校准数值字段：前端可能传字符串
    for f in ("slave", "register", "registers", "func_code", "port"):
        if hasattr(m, f):
            v = getattr(m, f)
            if isinstance(v, str):
                setattr(m, f, int(v) if v.strip() else None)

    if isinstance(m.scale, str):
        m.scale = _dec(m.scale, Decimal("1"))
    if isinstance(m.max_hourly, str):
        m.max_hourly = _dec(m.max_hourly)

    # 采集方式与必填项校验：串口类必须有从站号与寄存器，否则采不了
    if m.gateway.kind == Gateway.KIND_MODBUS:
        if m.slave is None:
            raise ValueError("串口服务器类型的点位必须填从站号")
        if m.register is None:
            raise ValueError("串口服务器类型的点位必须填寄存器起始地址")
    if m.gateway.kind == Gateway.KIND_HTTP and not m.url_path:
        raise ValueError("HTTP API 类型的点位必须填接口路径")

    m.source = Meter.SOURCE_ADMIN  # 后台改过 → 归后台维护
    m.save()
    return m


@require_http_methods(["POST"])
@csrf_exempt
def admin_meter_test(request: HttpRequest, pk: int) -> JsonResponse:
    """试采一次，返回**真实读数**与原始报文。

    现场调参主力：改完从站号 / 寄存器 / 倍率，不用等下一轮采集，点一下就知道对不对。
    """
    m = Meter.objects.select_related("gateway").filter(pk=pk).first()
    if m is None:
        return json_err("计量点不存在", 404)
    if not m.enabled:
        return json_err("该点位已停用，请先启用再试采", 409)
    try:
        result = _try_read(m.gateway, [m])
    except Exception as exc:  # noqa: BLE001
        logger.exception("试采失败：%s", m.code)
        return json_ok({"ok": False, "detail": f"{type(exc).__name__}: {exc}"})

    item = result["items"][0]
    if item["ok"] and item["value"] is not None:
        # 顺手更新最近读数，让列表页立刻能看到结果（真实采集仍由 collect 负责落 Reading）
        m.last_value = Decimal(str(item["value"]))
        m.last_at = datetime.now()
        m.last_ok = True
        m.last_error = ""
        m.save(update_fields=["last_value", "last_at", "last_ok", "last_error", "updated_at"])
    else:
        m.last_at = datetime.now()
        m.last_ok = False
        m.last_error = (item.get("error") or "未知错误")[:200]
        m.save(update_fields=["last_at", "last_ok", "last_error", "updated_at"])

    unit = m.unit
    if item["ok"]:
        detail = f"读取成功：{item['value']} {unit}（原始报文 {item['raw']}）"
    else:
        detail = f"读取失败：{item['error']}"
    return json_ok({**item, "durationMs": result["durationMs"], "unit": unit, "detail": detail})


def _try_read(gateway: Gateway, meters: list[Meter]) -> dict:
    """共用的试读实现：一次连接读多个点位，返回逐点结果。"""
    import time

    t0 = time.monotonic()
    collector = build_collector(gateway, timeout_ms=gateway.timeout_ms or 1500, retry_times=1, retry_backoff=0.2)
    items: list[dict] = []
    try:
        err = collector.prepare()
        if err:
            return {
                "durationMs": int((time.monotonic() - t0) * 1000),
                "items": [
                    {
                        "meter": m.code,
                        "name": m.name,
                        "ok": False,
                        "value": None,
                        "raw": "",
                        "error": err,
                    }
                    for m in meters
                ],
            }
        for m in meters:
            res = collector.read_one(m)
            items.append(
                {
                    "meter": m.code,
                    "name": m.name,
                    "ok": bool(res.ok),
                    "value": float(res.value) if res.value is not None else None,
                    "raw": res.raw,
                    "error": res.error,
                    "extra": res.extra or {},
                }
            )
    finally:
        collector.close()
    return {"durationMs": int((time.monotonic() - t0) * 1000), "items": items}
