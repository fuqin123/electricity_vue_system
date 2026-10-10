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
    POST        /api/admin/meters/{id}/test          试采一次（**返回真实读数**，会更新最近读数）
    POST        /api/admin/meters/{id}/current       ★ 读取当前值（只读，**一处都不落库**）
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

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Count, Max, Sum
from django.http import HttpRequest, JsonResponse
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from eep import conf

from .collectors import build_collector
from .models import (
    EnergyYearly,
    EnergyMonthly,
    EnergyDaily,
    EnergyCollectLog,
    EnergyGateway,
    EnergyHourly,
    EnergyMeter,
    EnergyRaw,
    EnergyType,
    PeriodSummary,
    ProductionHourly,
    ProductionLine,
    SystemParam,
)
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


def _is_closed(day: date_cls) -> bool:
    """这一天是否**已经整日结束** —— 只能问调用方要，不能从 `_now_for` 反推（B-522）。

    `_now_for(历史日)` 给的是那天 23:59:59，于是 `day == now.date()` 恒真，
    快照层就把第 23 小时当成「还没结束」，改用实时窗口取数；
    而实时窗口一度还没有时间上界，把那天 23:04 之后到此刻的全部读数减成一个数：
    实测查 09-27 时一线 23 点 = 85 kWh（真值 8.1）、三线 = 3285 kWh（真值 343.1），
    界面还给它打上「进行中」，日累计随之整体虚高。
    """
    return day < datetime.now().date()


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


def _flatten_validation(exc: DjangoValidationError) -> dict[str, str]:
    """把 Django 的 ValidationError 拍平成 {字段: 说明}。

    `exc.message_dict` 只在错误是「按字段报的」时才有；纯字符串错误（如
    `ValidationError("xxx")`）没有它，直接访问会抛 AttributeError —— 那就把
    一个 400 变成 500 了。这里两条路径都兜住。
    """
    if hasattr(exc, "message_dict"):
        return {k: "；".join(v) for k, v in exc.message_dict.items()}
    return {"__all__": "；".join(exc.messages)}


# ══════════════════════════════════════════════════════════════
#  序列化（DB → 前端契约）
# ══════════════════════════════════════════════════════════════

CATEGORY = {EnergyType.ELECTRIC: "电", EnergyType.WATER: "水", EnergyType.STEAM: "汽"}
#: 前端短码 → 库里的能源编码。对外契约保持 elec/water/steam 不变，
#: 库里统一用 ELECTRIC/WATER/STEAM（与 energy_type 表的 type_code 一致）。
KIND_TO_CODE = {"elec": EnergyType.ELECTRIC, "water": EnergyType.WATER, "steam": EnergyType.STEAM}


def _resolve_energy_type(value) -> EnergyType:
    """把前端传来的能源标识（短码 elec 或编码 ELECTRIC 均可）解析成 EnergyType。"""
    raw = str(value).strip()
    code = KIND_TO_CODE.get(raw.lower(), raw.upper())
    return EnergyType.objects.get(type_code=code)


def _workshop_line() -> ProductionLine:
    """车间哨兵行 —— 水/汽总表的归属。

    ⚠ 不能留空：日/月/年汇总表以 (line, energy_type, 周期) 建唯一键，
      而 MySQL 的唯一索引把多个 NULL 视为互不相同，留空会让唯一约束失效。
    """
    obj, _ = ProductionLine.objects.get_or_create(
        line_code=ProductionLine.WORKSHOP_CODE,
        defaults={
            "line_name": "车间（总表）",
            "line_type": ProductionLine.TYPE_WORKSHOP,
            "sort": 0,
        },
    )
    return obj
PROTOCOL = {EnergyGateway.KIND_MODBUS: "Modbus RTU over TCP", EnergyGateway.KIND_HTTP: "HTTP API"}


def gateway_offline_after(interval_seconds: int | None = None) -> timedelta:
    """网关心跳多久没动就算离线。

    ⚠ 这个阈值**必须跟采集节奏走**，不能写死 5 分钟。
      现场节奏是 `hourly`（每小时整点后 offset 秒采一次），心跳间隔天然就是一个小时；
      用 5 分钟判定会让网关在约 92% 的时间里显示「离线」——
      实测：最后心跳 10-09 15:04，当时 15:20，计量点位全部在线，
      但网关列表一片「离线」（v1.4.4 修复）。

    取「一个采样周期 + 宽限」：
      · hourly → (整点偏移 + 60 分钟) × 1.5、保底 90 分钟
        （真实一跳最长是「上轮刚过 offset」到「下轮 offset 后不久」≈ offset + 60min；
          再给 1.5 倍宽限，容忍计划任务晚点/重试）
      · minute → max(5 分钟, 3 × interval_seconds)
    需要收紧/放宽时配 `collect.gateway_offline_minutes`（分钟，优先级最高）。

    `interval_seconds` 传单台网关自己的采样周期；不传时按全局节奏算
    （v1.5.1 起网关可逐台配置周期，用全局值判会让「慢网关」被误判离线）。
    """
    override = conf.get("collect.gateway_offline_minutes")
    if override:
        return timedelta(minutes=max(1, int(override)))
    if slots.cadence() == slots.CADENCE_HOURLY:
        offset = int(conf.get("collect.hourly_offset_seconds", 60) or 0)
        return timedelta(minutes=max(90, int((offset + 3600) / 60 * 1.5)))
    seconds = interval_seconds or slots.minute_interval_seconds()
    return timedelta(seconds=max(300, seconds * 3))


def _gateway_last_heartbeat() -> dict:
    """用采集日志的最近一次开始时间当作网关心跳。"""
    return {
        row["gateway_id"]: row["last"]
        for row in EnergyCollectLog.objects.exclude(gateway=None)
        .values("gateway_id")
        .annotate(last=Max("started_at"))
    }


def _gateway_meter_heartbeat() -> dict:
    """**点位级**心跳：该网关下最近一次「读取成功」的时刻。

    ⚠ 为什么网关在线不能只看采集日志（B-537）：
      采集日志是**整轮**采集才写的，节奏是 hourly —— 一个刚在后台配好、
      点「测试连接 / 试采」验证通过的新网关，要等下一轮整点采集才会有日志，
      在此之前网关页一律显示「离线」（最长约 60 分钟）。
      现场看到的就是「测试连接明明成功了，状态还是离线」，难免反复怀疑设备。

      而点位试采成功会写 `last_ok=True` + `last_at` —— 这一刻网关**确实**是通的
      （报文都收回来了），没有理由是「离线」。两个信号取**并集**即可。

    只认 `last_ok=True` 的 `last_at`，不能直接拿 `last_at` 当心跳：
      试采**失败**也会写 `last_at`（见 `admin_meter_test`），拿它当心跳等于
      「一次失败反而证明网关在线」。`last_at` 只在采集成功那一支被写成设备时刻，
      但为了不看字段语义的变化，这里仍显式要求 `last_ok=True` 兜一层。
    """
    return {
        row["gateway_id"]: row["last"]
        for row in EnergyMeter.objects.filter(last_ok=True, enabled=True)
        .exclude(last_at=None)
        .values("gateway_id")
        .annotate(last=Max("last_at"))
    }


def _gateway_heartbeats() -> dict:
    """网关级 + 点位级心跳取较新者（见 `_gateway_meter_heartbeat` 的说明）。"""
    beats = _gateway_last_heartbeat()
    for gid, ts in _gateway_meter_heartbeat().items():
        if ts and (gid not in beats or beats[gid] is None or ts > beats[gid]):
            beats[gid] = ts
    return beats


def _online_gateway_ids(now: datetime | None = None) -> set[int]:
    """心脏还在跳的网关 id 集合（与列表页 `gateway_json` 同一套判定）。

    ⚠ 曾经这里只判「有过任意一条采集日志」（即永远在线），而列表页判 5 分钟 ——
      同一个「在线网关」在数据中心与网关页给出两个答案。统一到本函数（v1.4.4）。
    """
    now = now or datetime.now()
    limit = gateway_offline_after()
    return {gid for gid, ts in _gateway_heartbeats().items() if ts and (now - ts) < limit}


def gateway_json(g: EnergyGateway, *, point_count: int = 0, last_heartbeat=None) -> dict:
    limit = gateway_offline_after(getattr(g, "interval_seconds", None))
    online = bool(last_heartbeat and (datetime.now() - last_heartbeat) < limit)
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


def meter_address(m: EnergyMeter) -> str:
    """计量点的「怎么读到它」摘要：地址 / 从站 / 寄存器（或接口地址）。

    列表页与「读取当前值」弹窗共用，避免两处各拼一遍导致看到的地址不一致。
    """
    parts = []
    if m.gateway.host:
        parts.append(f"{m.gateway.host}:{m.gateway.port}" if m.gateway.port else m.gateway.host)
    if m.gateway.base_url:
        parts.append(m.gateway.base_url)
    if m.slave is not None:
        parts.append(f"从站 {m.slave}")
    if m.register is not None:
        parts.append(f"寄存器 {m.register}×{m.registers}")
    return " / ".join(parts) or "—"


def meter_json(m: EnergyMeter, *, today_usage=None, today_hours: int = 0) -> dict:
    """计量点 → 前端结构。既供列表展示，也供后台表单回填（字段一一对应）。"""
    if not m.enabled:
        status = "offline"
    elif m.last_ok:
        status = "online"
    elif m.last_at is not None:
        status = "fault"
    else:
        status = "offline"

    return {
        # ── 列表展示 ──
        "id": m.id,
        "code": m.code,
        "name": m.name,
        "category": m.energy_type.type_name,
        "lineCode": m.line.line_code if m.line else "—",
        "protocol": PROTOCOL.get(m.gateway.kind, m.gateway.kind),
        "address": meter_address(m),
        "unit": m.unit,
        "factor": float(m.scale),
        "status": status,
        "lastAt": m.last_at.strftime("%Y-%m-%d %H:%M:%S") if m.last_at else "",

        # ── 表单回填（可编辑配置）──
        "kind": m.energy_type.frontend_code,
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


def line_json(ln: ProductionLine, *, meter_count: int = 0, counter: str = "") -> dict:
    return {
        "id": ln.id,
        "code": ln.line_code,
        "name": ln.line_name,
        "product": ln.product,
        "rated": ln.rated,
        "workshop": "灌装车间",
        "meterNo": f"{meter_count} 个电表点位",
        "counterNo": counter or "产量计数器待接入",
        # 产线状态：1 启用 / 0 停用（TINYINT，见 models.ProductionLine）
        "status": "running" if ln.status == ProductionLine.STATUS_ON else "maintenance",
        "commissionedAt": "",
    }


def collect_log_json(r: EnergyCollectLog) -> dict:
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
        for row in EnergyHourly.objects.filter(stat_date=today, quality__in=EnergyHourly.TRUSTED)
        .values("meter_id")
        .annotate(total=Sum("consumption"), hours=Count("id"))
    }


# ══════════════════════════════════════════════════════════════
#  能源只读接口
# ══════════════════════════════════════════════════════════════


@require_GET
def overview(request: HttpRequest) -> JsonResponse:
    """大屏总览。结构与 `types.ts` 的 EnergySnapshot 对齐。

    `?date=` 是**已经过完的一天**时必须带 `closed=True`（见 `_is_closed`）：
    否则该日 23 点会被当成「进行中」的实时窗口，数字大一个数量级。
    """
    day = _query_date(request)
    return json_ok(build_overview(_now_for(day), closed=_is_closed(day)))


@require_GET
def line_detail(request: HttpRequest, code: str) -> JsonResponse:
    line = ProductionLine.objects.filter(line_code=code).first()
    if line is None:
        return json_err(f"产线不存在：{code}", 404)
    day = _query_date(request)
    return json_ok(build_line(line, _now_for(day), closed=_is_closed(day)))


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
        qs = EnergyHourly.objects.filter(stat_date__gte=start, stat_date__lte=end).select_related("meter", "meter__line")
        scope_desc = f"{start} ~ {end}"
    else:
        start = end = _query_date(request)
        qs = EnergyHourly.objects.filter(stat_date=start).select_related("meter", "meter__line")
        scope_desc = str(start)

    if request.GET.get("line"):
        qs = qs.filter(meter__line__line_code=request.GET["line"])
    if request.GET.get("meter"):
        qs = qs.filter(meter__code=request.GET["meter"])
    if request.GET.get("kind"):
        qs = qs.filter(meter__energy_type__type_code=KIND_TO_CODE.get(request.GET["kind"], request.GET["kind"].upper()))
    if request.GET.get("quality"):
        qs = qs.filter(quality=request.GET["quality"])

    qs = qs.order_by("-stat_date", "-stat_hour", "meter__code")
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
            "line": r.meter.line.line_code if r.meter.line else None,
            "kind": r.meter.energy_type.frontend_code,
            "date": r.stat_date.isoformat(),
            "hour": r.stat_hour,
            "usage": float(r.consumption) if r.consumption is not None else None,
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
    for m in EnergyMeter.objects.select_related("line", "gateway").order_by("energy_type__sort", "code"):
        agg = usage.get(m.id) or {}
        items.append(meter_json(m, today_usage=agg.get("total"), today_hours=agg.get("hours", 0)))
    return json_ok({"count": len(items), "items": items})


@require_GET
def collect_logs(request: HttpRequest) -> JsonResponse:
    limit = min(int(request.GET.get("limit", 50)), 500)
    rows = EnergyCollectLog.objects.select_related("gateway").order_by("-started_at")[:limit]
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
    agg = EnergyCollectLog.objects.filter(started_at__gte=since).aggregate(
        rounds=Count("id"), ok=Sum("ok_count"), fail=Sum("fail_count"), last=Max("started_at")
    )
    stale = EnergyMeter.objects.filter(enabled=True).exclude(last_ok=True).count()
    return json_ok(
        {
            "windowHours": 24,
            "rounds": agg["rounds"] or 0,
            "okCount": agg["ok"] or 0,
            "failCount": agg["fail"] or 0,
            "lastCollectAt": agg["last"].strftime("%Y-%m-%d %H:%M:%S") if agg["last"] else None,
            "abnormalMeters": stale,
            "meterTotal": EnergyMeter.objects.filter(enabled=True).count(),
            "hourlyUsageRows": EnergyHourly.objects.filter(stat_date=datetime.now().date()).count(),
            "productionRows": ProductionHourly.objects.filter(stat_date=datetime.now().date()).count(),
            "summaryRows": (
                EnergyDaily.objects.count() + EnergyMonthly.objects.count() + EnergyYearly.objects.count()
            ),
            # 当前采集节奏（现场核对「是不是每小时采一次」时看这个）
            "cadence": slots.describe(),
        }
    )


@require_GET
def period_summary(request: HttpRequest) -> JsonResponse:
    """日 / 月 / 年总计 —— 读 `energy_daily / energy_monthly / energy_yearly`。

    v1.3.0 起这三张表按「产线 × 能源类型」分行，`summary.period_rows()` 会把它们
    拼回「一个周期一行、电水汽齐备」的形状，**对外结构一字未改**。

    参数：dim=day|month|year（默认 day）、scope=line|workshop（默认 workshop）、
          line=A1、limit=N（默认 40，按周期倒序）
    """
    dim = request.GET.get("dim", PeriodSummary.DIM_DAY)
    scope = request.GET.get("scope", PeriodSummary.SCOPE_WORKSHOP)
    if dim not in dict(PeriodSummary.DIM_CHOICES):
        return json_err(f"dim 取值必须是 day / month / year，收到 {dim}")
    if scope not in dict(PeriodSummary.SCOPE_CHOICES):
        return json_err(f"scope 取值必须是 line / workshop，收到 {scope}")

    # ── 先取出库里已有的周期键（倒序、取 limit 个）──
    model, year_mode = {
        PeriodSummary.DIM_DAY: (EnergyDaily, False),
        PeriodSummary.DIM_MONTH: (EnergyMonthly, False),
        PeriodSummary.DIM_YEAR: (EnergyYearly, True),
    }[dim]
    field = {PeriodSummary.DIM_DAY: "stat_date", PeriodSummary.DIM_MONTH: "stat_month", PeriodSummary.DIM_YEAR: "stat_year"}[dim]

    line_obj = None
    qs = model.objects.all()
    if scope == PeriodSummary.SCOPE_LINE:
        code = request.GET.get("line")
        if not code:
            return json_err("scope=line 时必须给 line 参数，如 line=A1")
        line_obj = ProductionLine.objects.filter(line_code=str(code).upper()).first()
        if line_obj is None:
            return json_err(f"产线不存在：{code}")
        qs = qs.filter(line=line_obj)
    else:
        # 车间级：电力取全部真实产线，水汽取哨兵行 —— period_rows 里处理
        qs = qs.exclude(line__line_type=ProductionLine.TYPE_WORKSHOP) | qs.filter(
            line__line_type=ProductionLine.TYPE_WORKSHOP
        )

    limit = min(int(request.GET.get("limit", 40)), 400)
    keys_raw = sorted({v for v in qs.values_list(field, flat=True) if v is not None}, reverse=True)[:limit]
    keys = [str(k) if year_mode else (k.strftime("%Y-%m") if dim == PeriodSummary.DIM_MONTH else k.isoformat()) for k in keys_raw]

    rows = S.period_rows(dim, scope, line_obj, keys)
    line_for_plan = line_obj

    items = []
    for key in keys:
        row = rows[key]
        # 计划产量：汇总表里没有这一列（产量未接入），按额定产能 × 天数 现算，
        # 供界面显示参考；产量接入后应改为读真实计划。
        days = (row.end_date - row.start_date).days + 1
        rated = line_for_plan.rated if line_for_plan is not None else sum(
            l.rated for l in ProductionLine.objects.filter(line_type=ProductionLine.TYPE_LINE)
        )
        items.append(
            {
                "scope": row.scope,
                "line": row.line.line_code if row.line else None,
                "dim": row.dim,
                "periodKey": row.key,
                "startDate": row.start_date.isoformat(),
                "endDate": row.end_date.isoformat(),
                # ⚠ 不要写 float(x)：这些字段都可空（该周期没有可信数据时为 NULL，
                #   绝不补 0），float(None) 会让整个接口 500（B-509）。
                #   直接把 Decimal / None 交给 `EepJSONEncoder` 即可。
                "output": row.output,
                "kwh": row.kwh,
                "waterT": row.water_t,
                "steamT": row.steam_t,
                "planOutput": int(rated * 24 * 0.88 * days) if rated else 0,
                "unitKwh": row.unit_kwh,
                "coveredHours": row.covered_hours,
                "quality": row.quality,
                "computedAt": "",
            }
        )
    return json_ok({"dim": dim, "scope": scope, "count": len(items), "items": items})


@require_GET
def usage_range(request: HttpRequest) -> JsonResponse:
    """★ 按时间段查询用量 —— 大屏与后台共用。

    参数：
      from / to          **精确到小时**：`YYYY-MM-DD HH`（兼容纯日期，取当日 00:00）
                         缺省为今天；最长 366 天
      granularity        hour | day | month（默认 day）
      scope              workshop | line | all（默认 all：车间 + 各线）
      line               当 scope=line 时指定产线，如 A1

    返回：labels（时间轴）+ series（每条一组，车间/各线）+ utility（水汽，仅车间）
          + totals（区间合计）+ productionAvailable（产量是否已接入）。

    ⚠ 产量字段在产量侧接入前恒为 0，并带 `productionAvailable=false` 显式标注 ——
      界面据此显示「产量未接入」而不是让人以为「今天真没产量」。
    ⚠ 小时端点只在 `granularity=hour` 时生效；day / month 的桶是整天/整月，
      端点会被规范到所属周期边界（不做「半天」这种桶）。
    """
    try:
        start_at, end_at = ranges.parse_hours(request)
    except ValueError as exc:
        return json_err(str(exc))
    start, end = start_at.date(), end_at.date()
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
        start,
        end,
        granularity=granularity,
        scope=scope,
        line_code=line_code,
        now=now,
        start_at=start_at,
        end_at=end_at,
    )
    data["cadence"] = slots.describe()
    data["serverDate"] = day.isoformat()
    data["fromHour"] = start_at.strftime("%Y-%m-%d %H:%M")
    data["toHour"] = end_at.strftime("%Y-%m-%d %H:%M")
    return json_ok(data)


@require_GET
def dashboard(request: HttpRequest) -> JsonResponse:
    """★ 数据中心（后台首页）主数据 —— 按「电 / 水 / 汽」出累计序列 + 单箱变动费用。

    参数：
      from / to      **精确到小时**：`YYYY-MM-DD HH`（也兼容纯日期，取当日 00:00）
                     缺省为「今天 00:00 → 当前整点」
      granularity    hour | day（默认 hour；day 时按天累加）
      line           可选，如 A1。给定后水/汽序列一律 null ——
                     车间只有总表，**不存在线别的水/汽**，界面据此禁用勾选，
                     而不是画一条全零线（零会被读成「这条线没用过水」）

    与 `/api/energy/range` 同源同口径；缺算料时相关字段为 null 并在
    `cost.blockers` 里写清原因，界面必须把原因显示出来。
    """
    try:
        start_at, end_at = ranges.parse_hours(request)
    except ValueError as exc:
        return json_err(str(exc))

    granularity = request.GET.get("granularity", "hour")
    if granularity not in ("hour", "day"):
        return json_err(f"granularity 取值必须是 hour / day，收到 {granularity}")

    line_code = request.GET.get("line")
    if line_code and not ProductionLine.objects.filter(line_code=line_code.upper()).exists():
        return json_err(f"产线不存在：{line_code}", 404)

    now = datetime.now()
    # 查历史区间时把「现在」推到区间末，否则区间尾部会被当成「还没到」而留空（同 B-507）
    ref_now = now if end_at.date() >= now.date() else end_at.replace(hour=23, minute=59, second=59)

    data = ranges.dashboard_range(
        start_at, end_at, granularity=granularity, line_code=line_code.upper() if line_code else None, now=ref_now
    )
    data["cadence"] = slots.describe()
    return json_ok(data)


@require_GET
def admin_params(request: HttpRequest) -> JsonResponse:
    """系统参数列表（成本单价 / 箱规 / 标准变动费用等）。

    这些参数是成本折算与告警判定的唯一真值来源（ADR-007），由后台「系统参数」页维护，
    不再散落到配置文件 —— 改一次价不该发一次版。
    """
    qs = SystemParam.objects.all()
    group = request.GET.get("group")
    if group:
        qs = qs.filter(group=group)
    rows = [
        {
            "id": p.id,
            "key": p.key,
            "name": p.name,
            # value 为 NULL = 未标定。这里必须原样给 null：
            # 给 0 会让「每月标准变动费用」画出一条贴在零线上的假基准（B-505 同源）
            "value": p.value,
            "valueText": "未标定" if p.value is None else f"{p.value.normalize():f}",
            "unit": p.unit,
            "group": p.group,
            "remark": p.remark,
            "updatedAt": p.updated_at,
            "updatedBy": p.updated_by,
        }
        for p in qs
    ]
    return json_ok(_paged(request, rows))


@csrf_exempt
@require_http_methods(["PUT", "PATCH"])
def admin_param_detail(request: HttpRequest, pk: int) -> JsonResponse:
    """修改一个系统参数（只允许改值/名称/单位/说明，参数键不可改）。

    ⚠ 参数键是代码里的取值凭据，改键等于把参数删掉 —— 要换键请走 seed_params 命令。
    """
    obj = SystemParam.objects.filter(pk=pk).first()
    if obj is None:
        return json_err(f"参数不存在：{pk}", 404)

    body = _body(request)
    if "value" in body:
        raw = body["value"]
        if raw in (None, "", "null"):
            obj.value = None  # 主动清空 = 未标定（区别于 0）
        else:
            try:
                obj.value = Decimal(str(raw))
            except (InvalidOperation, ValueError):
                return json_err(f"参数值必须是数字或留空，收到 {raw}")
    for field in ("name", "unit", "remark", "group"):
        if field in body:
            setattr(obj, field, str(body[field]))
    obj.updated_by = str(body.get("updatedBy") or "admin")
    obj.save()
    return json_ok({"id": obj.id, "key": obj.key, "value": obj.value, "updatedAt": obj.updated_at})


@require_GET
def admin_readings(request: HttpRequest) -> JsonResponse:
    """★ 仪表读数查询 —— 按时间 / 网关 / 仪表查 `energy_raw` 的**原始累计读数**。

    参数：
      from / to    精确到小时（`YYYY-MM-DD HH`），缺省为今天
      gateway      网关 id 或编码
      meter        仪表 id 或编码
      line         产线编码（如 A1）
      energy       能源类型编码（ELECTRIC / WATER / STEAM）
      ok           1 只看成功 / 0 只看失败
      page/pageSize 分页（默认 50，上限 500）

    为什么单独开这个接口：`/api/energy/hourly` 给的是**差值后的用量**，
    而现场排查「这块表读到的原始数对不对、倍率有没有错」必须看累计读数本身
    与原始报文 —— 用量表里已经把这两样都加工掉了。
    """
    try:
        start_at, end_at = ranges.parse_hours(request)
    except ValueError as exc:
        return json_err(str(exc))

    qs = EnergyRaw.objects.filter(
        collect_time__gte=start_at, collect_time__lte=end_at
    ).select_related("meter", "line", "energy_type", "meter__gateway")

    gw = request.GET.get("gateway")
    if gw:
        qs = qs.filter(meter__gateway__id=int(gw)) if gw.isdigit() else qs.filter(meter__gateway__code=gw)
    meter = request.GET.get("meter")
    if meter:
        qs = qs.filter(meter__id=int(meter)) if meter.isdigit() else qs.filter(meter__code=meter)
    line = request.GET.get("line")
    if line:
        qs = qs.filter(line__line_code=line.upper())
    energy = request.GET.get("energy")
    if energy:
        qs = qs.filter(energy_type__type_code=energy.upper())
    ok = request.GET.get("ok")
    if ok in ("0", "1"):
        qs = qs.filter(ok=(ok == "1"))

    qs = qs.order_by("-collect_time", "meter__code")
    total = qs.count()
    page = max(1, int(request.GET.get("page", 1) or 1))
    size = max(1, min(int(request.GET.get("pageSize", 50) or 50), 500))
    rows = qs[(page - 1) * size : page * size]

    items = [
        {
            "id": r.id,
            "collectTime": r.collect_time,
            "meter": r.meter.code,
            "meterName": r.meter.name,
            "meterId": r.meter_id,
            "gateway": r.meter.gateway.name,
            "gatewayId": r.meter.gateway_id,
            "line": r.line.line_code,
            "lineName": r.line.line_name,
            "energy": r.energy_type.type_code,
            "energyName": r.energy_type.type_name,
            # 读数可空：采集失败时是 NULL（不是 0）—— 0 会被差值当成真实读数（B-502）
            "reading": r.reading,
            "unit": r.meter.unit,
            "ok": r.ok,
            "raw": r.raw,
            "error": r.error,
        }
        for r in rows
    ]
    return json_ok({"list": items, "total": total, "page": page, "pageSize": size,
                    "from": start_at.strftime("%Y-%m-%d %H:%M"), "to": end_at.strftime("%Y-%m-%d %H:%M")})


@require_GET
def production(request: HttpRequest) -> JsonResponse:
    """产量接口。

    ⚠ 产量侧（灌装机计数器 / 装箱机 PLC / MES 班次报工）**尚未改造**，
      因此 `available = false`、`output` 一律为 **null**（v1.2.1 起统一为 null，
      0 会被读成「确实没产出」，与「还没接数据」是两回事）。

    与 v1.2.0 的差别：那时返回 501（接口不存在）。现在表结构已经建好
      （`production_hourly`），接口**如实返回 null** 并用 `available` 明确标注
      「这不是真实产量」，比直接 501 更好用 —— 前端不必为「产量未接入」写特殊分支，
      只要看 `available` 就知道该显示数值还是显示 `--`。
    """
    day = _query_date(request)
    rows = ProductionHourly.objects.filter(stat_date=day).select_related("line").order_by("line__line_code", "stat_hour")
    real = rows.exclude(source__in=(ProductionHourly.SOURCE_DEMO, ProductionHourly.SOURCE_NONE))

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
                    "line": r.line.line_code,
                    "hour": r.stat_hour,
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
    expect = EnergyMeter.objects.filter(enabled=True, energy_type__type_code=EnergyType.ELECTRIC).count() * (now.hour + 1)
    received = sum(a.get("hours", 0) for a in usage.values())
    online_ids = _online_gateway_ids(now)

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
                # 与 /api/admin/gateways 列表页**同一套判定**（心跳超时阈值按采集节奏取）
                "onlineGateways": len(
                    [
                        g
                        for g in EnergyGateway.objects.filter(enabled=True)
                        if g.id in online_ids
                    ]
                ),
                "totalGateways": EnergyGateway.objects.filter(enabled=True).count(),
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
                    ProductionHourly.objects.filter(stat_date=now.date())
                    .exclude(source__in=(ProductionHourly.SOURCE_DEMO, ProductionHourly.SOURCE_NONE))
                    .exists()
                    for _ in [0]
                ),
                "collectRounds24h": EnergyCollectLog.objects.filter(started_at__gte=since).count(),
            },
        }
    )


@require_GET
def admin_lines(request: HttpRequest) -> JsonResponse:
    rows = []
    for ln in ProductionLine.objects.order_by("sort", "line_code"):
        cnt = ln.meters.filter(energy_type__type_code=EnergyType.ELECTRIC, enabled=True).count()
        rows.append(line_json(ln, meter_count=cnt))
    return json_ok(_paged(request, rows))


@require_GET
def admin_quality(request: HttpRequest) -> JsonResponse:
    """数据质量（按产线）：应报 / 实收 / 缺报。"""
    today = datetime.now().date()
    expect = datetime.now().hour + 1
    rows = []
    for ln in ProductionLine.objects.filter(status=ProductionLine.STATUS_ON, line_type=ProductionLine.TYPE_LINE).order_by("sort", "line_code"):
        meters = [m for m in ln.meters.all() if m.energy_type.type_code == EnergyType.ELECTRIC and m.enabled]
        ok_rows = EnergyHourly.objects.filter(
            meter__in=meters, stat_date=today, quality__in=EnergyHourly.TRUSTED
        )
        received = ok_rows.values("stat_hour").distinct().count()
        missing = sorted(set(range(expect)) - set(ok_rows.values_list("stat_hour", flat=True)))
        last = ok_rows.aggregate(last=Max("computed_at"))["last"]
        rows.append(
            {
                "lineCode": ln.line_code,
                "lineName": ln.line_name,
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
    rows = [collect_log_json(r) for r in EnergyCollectLog.objects.select_related("gateway").order_by("-started_at")[:500]]
    return json_ok(_paged(request, rows))


# ── 网关读写 ──────────────────────────────────────────────────

GATEWAY_FIELDS = ("code", "name", "kind", "host", "port", "base_url", "timeout_ms", "interval_seconds", "enabled", "remark")
#: 前端字段名 → 模型字段名（前端统一 camelCase）
#:
#: ⚠ **漏登记一个别名不会报错，只会静默丢字段。**（v1.5.1 修的 B-535）
#:   前端 `GatewayView.vue` 提交的是 `ip`（因为它展示的那一列就叫「地址 IP」），
#:   而模型字段名是 `host`；`_apply()` 拿 `ip` 在 `GATEWAY_FIELDS` 里找不到，
#:   直接跳过 —— 于是新增网关时 **IP 写不进去、端口却正常**（端口前后端同名）。
#:   现场表现是「IP 那一格永远是空的」，换谁都以为是设备没填对，
#:   实际上是接口把值扔了。改字段名时务必同时改这里，`_assert_alias_covers_frontend()`
#:   会在应用启动时把这类漂移喊出来。
GATEWAY_ALIAS = {
    "ip": "host",
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


#: 前端契约里会往网关 / 计量点提交的全部字段名（camelCase 与同名混用）。
#: 与 `frontend/packages/shared/src/types.ts` 的 Gateway / MeterPoint 对齐。
#: ⚠ 前端加字段时这里也要加 —— 下面的 `assert_alias_covers_frontend()` 靠它兜底。
FRONTEND_GATEWAY_KEYS = (
    "code", "name", "kind", "ip", "port", "baseUrl", "timeoutMs", "intervalSeconds",
    "enabled", "remark",
)
FRONTEND_METER_KEYS = (
    "code", "name", "kind", "scope", "role", "lineCode", "gatewayId",
    "slave", "register", "registers", "funcCode", "wordOrder", "urlPath", "params",
    "valuePath", "timePath", "factor", "unit", "maxHourly", "enabled", "remark",
    "assetNo", "installDate",
)


def assert_alias_covers_frontend() -> list[str]:
    """自检：前端会提交的字段，是否都能落到某个模型字段上。

    ⚠ 为什么需要它：`_apply()` 对**认不出来的字段是静默跳过的**，不报错、不告警 ——
      前端写 `ip`、模型叫 `host`，漏登记别名就会把值整个扔进垃圾桶，
      而响应照样 201、端口因为前后端同名反而正常，现场只看到「IP 那格是空的」（B-535）。
      这类「字段名漂移」靠人眼 review 挡不住，因此在这里显式对一遍。

    返回认不出的字段名列表（空列表 = 契约完好）。应用启动时调用一次，把漂移喊进日志。
    """
    missing: list[str] = []
    # 网关：别名 + 同名 都能落到 GATEWAY_FIELDS 才算登记完整
    for key in FRONTEND_GATEWAY_KEYS:
        mapped = GATEWAY_ALIAS.get(key, key)
        if key != "kind" and mapped not in GATEWAY_FIELDS:
            missing.append(f"gateway.{key} → {mapped}")
    # 计量点：同上。scope/role 等由 _save_meter 直接读 data，不走 _apply，故豁免。
    DIRECT_READ = {"kind", "gatewayId", "lineCode"}
    for key in FRONTEND_METER_KEYS:
        if key in DIRECT_READ:
            continue
        mapped = METER_ALIAS.get(key, key)
        if mapped not in METER_FIELDS:
            missing.append(f"meter.{key} → {mapped}")
    return missing


@require_http_methods(["GET", "POST"])
@csrf_exempt
def admin_gateways(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        hb = _gateway_heartbeats()
        counts = {
            row["gateway_id"]: row["n"]
            for row in EnergyMeter.objects.values("gateway_id").annotate(n=Count("id"))
        }
        rows = [
            gateway_json(g, point_count=counts.get(g.id, 0), last_heartbeat=hb.get(g.id))
            for g in EnergyGateway.objects.order_by("code")
        ]
        return json_ok(rows)

    try:
        data = _body(request)
    except ValueError as exc:
        return json_err(str(exc))
    if not data.get("code") or not data.get("name"):
        return json_err("网关编号与名称为必填项")
    if EnergyGateway.objects.filter(code=data["code"]).exists():
        return json_err(f"网关编号已存在：{data['code']}", 409)

    fields = _apply(data, GATEWAY_FIELDS, GATEWAY_ALIAS)
    fields.setdefault("kind", EnergyGateway.KIND_MODBUS)
    fields["source"] = EnergyGateway.SOURCE_ADMIN
    gw = EnergyGateway.objects.create(**fields)
    return json_ok(gateway_json(gw), status=201)


@require_http_methods(["GET", "PUT", "DELETE"])
@csrf_exempt
def admin_gateway_detail(request: HttpRequest, pk: int) -> JsonResponse:
    gw = EnergyGateway.objects.filter(pk=pk).first()
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
    gw.source = EnergyGateway.SOURCE_ADMIN  # 后台改过 → 归后台维护，seed_registry 不再覆盖
    gw.save()
    return json_ok(gateway_json(gw, point_count=gw.meters.count()))


@require_http_methods(["POST"])
@csrf_exempt
def admin_gateway_test(request: HttpRequest, pk: int) -> JsonResponse:
    """网关连接测试：对旗下每个计量点试读一次，返回真实读数与错误。"""
    gw = EnergyGateway.objects.filter(pk=pk).first()
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
    # ⚠ 不含 "kind"：能源类型已改成外键，由 _save_meter 单独解析前端短码
    "code", "name", "scope", "role", "slave", "register", "registers", "func_code",
    "word_order", "url_path", "params", "value_path", "time_path", "scale", "unit",
    "max_hourly", "enabled", "remark", "asset_no", "install_date",
)
METER_ALIAS = {
    # ⚠ 前端契约里叫 factor（那一列的表头就是「倍率」），模型字段叫 scale。
    #   漏了这一行，新增 / 编辑点位时**倍率会被静默改成默认值 1**（与 B-535 同源）。
    #   这个比 IP 更隐蔽：界面照常保存成功，只是电表读数从此差一个量级。
    "factor": "scale",
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
        for m in EnergyMeter.objects.select_related("line", "gateway").order_by("energy_type__sort", "code"):
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
    if EnergyMeter.objects.filter(code=data["code"]).exists():
        return json_err(f"点位编号已存在：{data['code']}", 409)

    try:
        m = _save_meter(None, data)
    except ValueError as exc:
        return json_err(str(exc))
    return json_ok(meter_json(m), status=201)


@require_http_methods(["GET", "PUT", "DELETE"])
@csrf_exempt
def admin_meter_detail(request: HttpRequest, pk: int) -> JsonResponse:
    m = EnergyMeter.objects.filter(pk=pk).first()
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


def _save_meter(m: EnergyMeter | None, data: dict) -> EnergyMeter:
    """新增或更新计量点。字段校验集中在这里，供 POST 与 PUT 共用。"""
    is_new = m is None
    if is_new:
        m = EnergyMeter(code=str(data["code"]).strip(), name=str(data["name"]).strip(), gateway_id=int(data["gatewayId"]))

    if "code" in data:
        m.code = str(data["code"]).strip()
    if "name" in data:
        m.name = str(data["name"]).strip()

    # 能源类型（前端短码 → energy_type 外键）
    if data.get("kind"):
        m.energy_type = _resolve_energy_type(data["kind"])
    elif is_new:
        m.energy_type = _resolve_energy_type(EnergyType.ELECTRIC)

    for field, value in _apply(data, METER_FIELDS, METER_ALIAS).items():
        if field in ("gateway", "line"):
            continue
        setattr(m, field, value)

    if "gatewayId" in data or "gateway" in data:
        gw_id = data.get("gatewayId", data.get("gateway"))
        gw = EnergyGateway.objects.filter(pk=gw_id).first()
        if gw is None:
            raise ValueError(f"网关不存在：{gw_id}")
        m.gateway = gw

    # 产线引用
    if "lineCode" in data:
        code = data["lineCode"]
        if code in ("", "—", None):
            m.line = _workshop_line()
        else:
            ln = ProductionLine.objects.filter(line_code=str(code).upper()).first()
            if ln is None:
                raise ValueError(f"产线不存在：{code}")
            m.line = ln

    # 车间总表挂车间哨兵行（不是留空 —— 汇总表的唯一键需要它）
    if m.scope == EnergyMeter.SCOPE_WORKSHOP:
        m.line = _workshop_line()

    # 校准数值字段：前端可能传字符串
    for f in ("slave", "register", "registers", "func_code"):
        if hasattr(m, f):
            v = getattr(m, f)
            if isinstance(v, str):
                setattr(m, f, int(v) if v.strip() else None)

    if isinstance(m.scale, str):
        m.scale = _dec(m.scale, Decimal("1"))
    if isinstance(m.max_hourly, str):
        m.max_hourly = _dec(m.max_hourly)

    # 可空日期：表单里没填时给的是空串，而 Django 的 DateField
    # 「什么都接受就是不接受 ""」—— 空串会直接抛 ValidationError（HTTP 500）。
    # 前端新增点位时 `installDate` 默认就是 ""，所以**任何**点位都会撞上，
    # 与 scope/role 无关；现场只看到「加车间总表/辅机报 500」。
    # 空串的语义是「没填」→ 归一为 None（可空字段），而不是一个非法日期。
    if m.install_date is not None and not isinstance(m.install_date, date_cls):
        raw = str(m.install_date).strip()
        m.install_date = parse_date(raw) if raw else None
        if raw and m.install_date is None:
            raise ValueError(f"安装日期格式不对：{raw!r}，应为 YYYY-MM-DD")

    # 采集方式与必填项校验：串口类必须有从站号与寄存器，否则采不了
    if m.gateway.kind == EnergyGateway.KIND_MODBUS:
        if m.slave is None:
            raise ValueError("串口服务器类型的点位必须填从站号")
        if m.register is None:
            raise ValueError("串口服务器类型的点位必须填寄存器起始地址")
    if m.gateway.kind == EnergyGateway.KIND_HTTP and not m.url_path:
        raise ValueError("HTTP API 类型的点位必须填接口路径")

    m.source = EnergyMeter.SOURCE_ADMIN  # 后台改过 → 归后台维护
    try:
        m.save()
    except DjangoValidationError as exc:
        # 模型层校验失败（非法日期 / 超长 / 越界）不能变成 500：
        # 前端把它当网络故障，现场拿不到「到底哪一格填错了」。
        messages = "；".join(f"{k}: {v}" for k, v in _flatten_validation(exc).items())
        raise ValueError(f"字段校验未通过 —— {messages}") from exc
    return m


@require_http_methods(["POST"])
@csrf_exempt
def admin_meter_test(request: HttpRequest, pk: int) -> JsonResponse:
    """试采一次，返回**真实读数**与原始报文。

    现场调参主力：改完从站号 / 寄存器 / 倍率，不用等下一轮采集，点一下就知道对不对。
    """
    m = EnergyMeter.objects.select_related("gateway").filter(pk=pk).first()
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
        # 顺手更新最近读数，让列表页立刻能看到结果（真实采集仍由 collect 负责落 EnergyRaw）
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


@require_http_methods(["POST"])
@csrf_exempt
def admin_meter_current(request: HttpRequest, pk: int) -> JsonResponse:
    """★ 读取某个计量点的**当前值**（v1.4.4 新增）。

    与「试采」(`/test`) 的区别**只有一条，但很关键**：

    | | `/test` 试采 | `/current` 读取当前值 |
    | :--- | :--- | :--- |
    | 落库 | ✍ 会写 `last_value / last_at / last_ok / last_error` | ❌ **一处都不写** |
    | 停用的点位 | 拒绝（409） | 允许（手动排障更需要读停用表） |
    | 用途 | 调参后确认配置对不对 | 只想看一眼现在的数，不留痕 |

    为什么要有「不落库」这一档：`last_value / last_at / last_ok` 是采集链路的健康信号，
    「仪表读数查询」页的排障完全依赖它。如果随手点一下就把 `last_ok` 刷成 `True`、
    `last_at` 刷成此刻，**断讯表会被人工操作「治好」**，之后谁也说不清是链路恢复了
    还是有人点过按钮。所以这个接口设计成纯读：不出现在 `energy_raw`、不碰 `energy_meter`、
    不写任何日志表，返回体里带 `persisted=false` 明示这一点。

    超时与重试（与采集同一套实现 `_try_read` → `BaseCollector.read_one_with_retry`）：
      · `timeoutMs`  单次读写超时，默认取网关配置（`energy_gateway.timeout_ms`），再退回 1500
      · `retryTimes` 失败后的额外重试次数，默认取 `collect.retry_times`
      · 两个都可以在请求体里覆盖（现场排障要试「是偶发还是必现」），但有上下限裁剪，
        避免一个请求把 Web 工作进程占住：单次超时 200—10000ms、重试 0—5 次。
    超时预算 = `timeoutMs × (1 + retryTimes)`，最坏 10s × 6 = 60s，与采集侧的口径一致。
    """
    m = EnergyMeter.objects.select_related("gateway", "energy_type", "line").filter(pk=pk).first()
    if m is None:
        return json_err("计量点不存在", 404)

    try:
        body = _body(request)
    except ValueError as exc:
        return json_err(str(exc))

    def _clamp(raw, default: int, lo: int, hi: int) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return max(lo, min(value, hi))

    default_timeout = int(m.gateway.timeout_ms or conf.get("collect.timeout_ms", 1500) or 1500)
    default_retry = int(conf.get("collect.retry_times", 1) or 0)
    timeout_ms = _clamp(body.get("timeoutMs"), default_timeout, 200, 10000)
    retry_times = _clamp(body.get("retryTimes"), default_retry, 0, 5)

    try:
        result = _try_read(m.gateway, [m], timeout_ms=timeout_ms, retry_times=retry_times)
    except Exception as exc:  # noqa: BLE001 —— 排障接口不能把 500 抛给前端
        logger.exception("读取当前值失败：%s", m.code)
        return json_ok(
            {
                "meterId": m.id,
                "meter": m.code,
                "name": m.name,
                "ok": False,
                "value": None,
                "raw": "",
                "error": f"{type(exc).__name__}: {exc}",
                "unit": m.unit,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "durationMs": 0,
                "attempts": 0,
                "timeoutMs": timeout_ms,
                "retryTimes": retry_times,
                "persisted": False,
            }
        )

    item = result["items"][0]
    return json_ok(
        {
            # ── 这个点位是谁、怎么读到的 ──
            "meterId": m.id,
            "meter": m.code,
            "name": m.name,
            "category": m.energy_type.type_name,
            "lineCode": m.line.line_code if m.line else "—",
            "gatewayCode": m.gateway.code,
            "gatewayName": m.gateway.name,
            "protocol": PROTOCOL.get(m.gateway.kind, m.gateway.kind),
            "address": meter_address(m),
            "unit": m.unit,
            "factor": float(m.scale),
            "enabled": m.enabled,
            # ── 这一次读到的结果 ──
            "ok": item["ok"],
            "value": item["value"],
            "raw": item["raw"],
            "error": item["error"],
            "extra": item.get("extra") or {},
            "attempts": item.get("attempts", 0),
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "durationMs": result["durationMs"],
            # ── 超时与重试参数（让界面能如实写出「按什么条件读的」）──
            "timeoutMs": timeout_ms,
            "retryTimes": retry_times,
            # ── 明确声明：本次读取不留痕 ──
            "persisted": False,
            "note": "本次读取只发往现场设备一次，不写数据库、不写任何日志，也不更新点位的最近读数",
        }
    )


def _try_read(
    gateway: EnergyGateway,
    meters: list[EnergyMeter],
    *,
    timeout_ms: int | None = None,
    retry_times: int | None = None,
    retry_backoff: float | None = None,
) -> dict:
    """共用的试读实现：一次连接读多个点位，返回逐点结果。**只读，不落库。**

    超时与重试：
      · `timeout_ms`    单次读写超时；空则用网关自己配的，再空则 1500ms
      · `retry_times`   失败后的**额外**尝试次数（总尝试 = retry_times + 1，
                        由 `BaseCollector._read_with_retry` 统一执行）
      · `retry_backoff` 两次尝试之间的等待秒数

    三者留空时保持历史行为（1500ms / 1 次 / 0.2s），所以既有的「测试连接」「试采」
    调用点不受影响；后台「读取当前值」可以按需覆盖。
    超时预算 = timeout_ms ×（1 + retry_times），调用方给的值要在**单次 HTTP 请求**
    能承受的范围内（见 admin_meter_current 的上下限裁剪）。
    """
    import time

    t0 = time.monotonic()
    collector = build_collector(
        gateway,
        timeout_ms=timeout_ms or gateway.timeout_ms or 1500,
        retry_times=1 if retry_times is None else retry_times,
        retry_backoff=0.2 if retry_backoff is None else retry_backoff,
    )
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
            # ⚠ 走 read_one_with_retry：直调 read_one 会绕开重试（v1.4.4 前就是这么写的）
            res = collector.read_one_with_retry(m)
            items.append(
                {
                    "meter": m.code,
                    "name": m.name,
                    "ok": bool(res.ok),
                    "value": float(res.value) if res.value is not None else None,
                    "raw": res.raw,
                    "error": res.error,
                    "attempts": res.attempts,
                    "extra": res.extra or {},
                }
            )
    finally:
        collector.close()
    return {"durationMs": int((time.monotonic() - t0) * 1000), "items": items}
