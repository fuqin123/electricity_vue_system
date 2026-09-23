"""时间段查询 —— 大屏与后台共用的「选择时间端的数据」实现。

三个粒度，各有各的数据来源，**不要混用**：

| 粒度 | 数据来源 | 说明 |
| :--- | :--- | :--- |
| hour  | `eep_hourly_usage` | 小时差值的真值表。用「全部计量点都有可信值才算数」的口径聚合 |
| day   | `eep_period_summary` | 与后台日汇总同源，缺行时现算（并记 warning） |
| month | `eep_period_summary` | 同上 |

「小时」为什么不读 period_summary：小时数据量本来就小（14 点 × 24 时 × 天数），
直接查真值表更快也更准；而日 / 月必须走汇总表，才能保证与大屏 KPI、后台日汇总
三处数字完全一致（这是项目里反复强调的一条：同一指标只能有一个来源）。

产量（output）在产量侧接入前一律为 0，接口里带 `productionAvailable: false`
显式标注，不让人误以为「今天真的没产量」。
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Sum

from ..models import HourlyProduction, HourlyUsage, Line, Meter, PeriodSummary
from . import summary as S

logger = logging.getLogger(__name__)

DEMO_SOURCES = (HourlyProduction.SOURCE_DEMO, HourlyProduction.SOURCE_NONE)


def parse_range(request) -> tuple[date_cls, date_cls]:
    """从请求里取 from / to，缺省为「今天」。"""
    today = datetime.now().date()
    f = request.GET.get("from")
    t = request.GET.get("to")
    start = datetime.strptime(f, "%Y-%m-%d").date() if f else today
    end = datetime.strptime(t, "%Y-%m-%d").date() if t else today
    if end < start:
        start, end = end, start
    # 上限保护：一次最多查 366 天，避免把库和内存拖垮
    if (end - start).days > 366:
        start = end - timedelta(days=366)
    return start, end


def _lines_scope(line_code: str | None) -> list[Line]:
    if line_code:
        ln = Line.objects.filter(code=line_code.upper()).first()
        return [ln] if ln else []
    return list(Line.objects.filter(enabled=True).order_by("sort", "code"))


def _line_meters(line: Line) -> list[Meter]:
    return [m for m in line.meters.all() if m.kind == Meter.KIND_ELEC and m.enabled]


def _workshop_meters() -> list[Meter]:
    return list(Meter.objects.filter(kind=Meter.KIND_ELEC, enabled=True))


# ══════════════════════════════════════════════════════════════
#  小时粒度
# ══════════════════════════════════════════════════════════════


def _hour_labels(start: date_cls, end: date_cls, now: datetime) -> list[tuple[date_cls, int]]:
    """生成 [起点, 终点] 之间的全部（日期, 小时）桶，末日截止到当前小时。"""
    out: list[tuple[date_cls, int]] = []
    d = start
    while d <= end:
        last_hour = now.hour if d == now.date() else 23
        if d == now.date() and now.hour == 0:
            last_hour = 0
        for h in range(last_hour + 1):
            out.append((d, h))
        d += timedelta(days=1)
    return out


def _hour_matrix(meters: list[Meter], buckets: list[tuple[date_cls, int]], start: date_cls, end: date_cls):
    """返回 {bucket: (合计|None, quality)}。

    口径与小时接口一致：**任一计量点缺数/换表，该小时合计就不成立（给 None）**，
    而不是「有几块算几块」的偏小值。
    """
    rows = HourlyUsage.objects.filter(
        meter__in=meters, date__gte=start, date__lte=end
    ).values("date", "hour", "usage", "quality", "meter_id")

    grouped: dict[tuple[date_cls, int], list[dict]] = {}
    for r in rows:
        grouped.setdefault((r["date"], r["hour"]), []).append(r)

    need = len(meters)
    out: dict[tuple[date_cls, int], tuple[Decimal | None, str]] = {}
    for b in buckets:
        got = grouped.get(b, [])
        if len(got) < need:
            out[b] = (None, HourlyUsage.Q_MISSING)
            continue
        bad = [r for r in got if r["quality"] in (HourlyUsage.Q_MISSING, HourlyUsage.Q_RESET)]
        if bad:
            out[b] = (None, bad[0]["quality"])
            continue
        usable = [r for r in got if r["quality"] in HourlyUsage.TRUSTED]
        if not usable:
            # 同 snapshot.py：一块表都没给出可信值时，合计是 None 而不是 0
            out[b] = (None, HourlyUsage.Q_PARTIAL)
            continue
        total = sum((r["usage"] or Decimal(0) for r in usable), Decimal(0))
        quality = HourlyUsage.Q_OK if len(usable) == len(got) else HourlyUsage.Q_PARTIAL
        out[b] = (total, quality)
    return out


# ══════════════════════════════════════════════════════════════
#  日 / 月粒度（读 period_summary）
# ══════════════════════════════════════════════════════════════


def _summary_series(dim: str, scope: str, line: Line | None, keys: list[str]) -> dict[str, PeriodSummary]:
    qs = PeriodSummary.objects.filter(dim=dim, scope=scope, period_key__in=keys)
    qs = qs.filter(line=line) if line is not None else qs.filter(line__isnull=True)
    return {r.period_key: r for r in qs}


def _period_keys(dim: str, start: date_cls, end: date_cls, now: datetime) -> list[tuple[str, str]]:
    """返回 [(period_key, label)]。进行中的周期只到「现在」为止。"""
    out: list[tuple[str, str]] = []
    if dim == PeriodSummary.DIM_DAY:
        d = start
        while d <= end:
            if d > now.date():
                break
            out.append((d.strftime("%Y-%m-%d"), d.strftime("%m-%d")))
            d += timedelta(days=1)
        return out

    # 月：从起始月枚举到结束月
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((f"{y}-{m:02d}", f"{y}-{m:02d}"))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _range_total_kwh(meters: list[Meter], start: date_cls, end: date_cls) -> Decimal | None:
    agg = HourlyUsage.objects.filter(
        meter__in=meters, date__gte=start, date__lte=end, quality__in=HourlyUsage.TRUSTED
    ).aggregate(t=Sum("usage"))
    return agg["t"]


def _range_output(lines: list[Line], start: date_cls, end: date_cls) -> Decimal:
    total = (
        HourlyProduction.objects.filter(
            line__in=lines, date__gte=start, date__lte=end, quality__in=HourlyProduction.TRUSTED
        )
        .exclude(source__in=DEMO_SOURCES)
        .aggregate(t=Sum("output"))["t"]
    )
    return Decimal(total) if total is not None else Decimal(0)


def _utility_series(
    kind: str, dim: str, start: date_cls, end: date_cls, now: datetime, keys: list[tuple[str, str]]
) -> list[float | None]:
    """车间水 / 汽：只有总表，不拆分到线别。"""
    meters = list(Meter.objects.filter(kind=kind, enabled=True))
    if not meters:
        return [None for _ in keys]

    if dim == PeriodSummary.DIM_DAY:
        rows = _summary_series(PeriodSummary.DIM_DAY, PeriodSummary.SCOPE_WORKSHOP, None, [k for k, _ in keys])
        field = "water_t" if kind == Meter.KIND_WATER else "steam_t"
        out: list[float | None] = []
        for k, _lab in keys:
            row = rows.get(k)
            val = getattr(row, field) if row is not None else None
            out.append(float(val) if val is not None else None)
        return out

    out: list[float | None] = []
    for key, _label in keys:
        s, e = S.period_bounds(dim, key)
        row = _summary_series(dim, PeriodSummary.SCOPE_WORKSHOP, None, [key]).get(key)
        if row is not None:
            val = row.water_t if kind == Meter.KIND_WATER else row.steam_t
            out.append(float(val) if val is not None else None)
            continue
        agg = HourlyUsage.objects.filter(
            meter__in=meters, date__gte=s, date__lte=min(e, now.date()), quality__in=HourlyUsage.TRUSTED
        ).aggregate(t=Sum("usage"))
        out.append(float(agg["t"]) if agg["t"] is not None else None)
    return out


# ══════════════════════════════════════════════════════════════
#  对外主函数
# ══════════════════════════════════════════════════════════════


def usage_range(
    start: date_cls,
    end: date_cls,
    *,
    granularity: str = "day",
    scope: str = "all",
    line_code: str | None = None,
    now: datetime | None = None,
) -> dict:
    """按时间段取用量。

    :param scope: 'workshop' 仅车间 / 'line' 指定单线 / 'all' 车间 + 各线
    :param granularity: 'hour' | 'day' | 'month'
    """
    now = now or datetime.now()
    if granularity == "hour":
        dim = None
    elif granularity == "month":
        dim = PeriodSummary.DIM_MONTH
    else:
        granularity = "day"
        dim = PeriodSummary.DIM_DAY

    labels: list[str] = []
    series: list[dict] = []
    quality_series: list[str] | None = None

    # ── 车间 ──
    want_workshop = scope in ("workshop", "all")
    want_lines = scope in ("line", "all")
    target_lines = _lines_scope(line_code) if want_lines else []

    if granularity == "hour":
        buckets = _hour_labels(start, end, now)
        labels = [f"{d:%m-%d} {h:02d}" for d, h in buckets]
        wmat = None
        if want_workshop:
            # 只算一次：既要值也要质量，重复计算会白白多扫一遍库
            wmat = _hour_matrix(_workshop_meters(), buckets, start, end)
            series.append(
                {
                    "key": "workshop",
                    "name": "车间合计",
                    "unit": "kWh",
                    "values": [_round(wmat[b][0]) for b in buckets],
                }
            )
        for ln in target_lines:
            mat = _hour_matrix(_line_meters(ln), buckets, start, end)
            series.append(
                {
                    "key": ln.code,
                    "name": ln.name,
                    "unit": "kWh",
                    "values": [_round(mat[b][0]) for b in buckets],
                }
            )
        if wmat is not None:
            quality_series = [wmat[b][1] for b in buckets]
        util = {
            "water": _utility_hour_series(Meter.KIND_WATER, buckets, start, end),
            "steam": _utility_hour_series(Meter.KIND_STEAM, buckets, start, end),
        }
    else:
        keys = _period_keys(dim, start, end, now)
        labels = [lab for _k, lab in keys]
        klist = [k for k, _ in keys]

        if want_workshop:
            rows = _summary_series(dim, PeriodSummary.SCOPE_WORKSHOP, None, klist)
            vals: list[float | None] = []
            for k in klist:
                if k in rows:
                    # 汇总行存在但 kwh 为 NULL = 该周期没有任何可信数据 → 传 None（不是 0）
                    vals.append(float(rows[k].kwh) if rows[k].kwh is not None else None)
                    continue
                s, e = S.period_bounds(dim, k)
                vals.append(_round(_range_total_kwh(_workshop_meters(), s, min(e, now.date()))))
            series.append({"key": "workshop", "name": "车间合计", "unit": "kWh", "values": vals})
            quality_series = [rows[k].quality if k in rows else "computed" for k in klist]

        for ln in target_lines:
            rows = _summary_series(dim, PeriodSummary.SCOPE_LINE, ln, klist)
            vals = []
            for k in klist:
                if k in rows:
                    vals.append(float(rows[k].kwh) if rows[k].kwh is not None else None)
                    continue
                s, e = S.period_bounds(dim, k)
                vals.append(_round(_range_total_kwh(_line_meters(ln), s, min(e, now.date()))))
            series.append({"key": ln.code, "name": ln.name, "unit": "kWh", "values": vals})

        util = {
            "water": _utility_series(Meter.KIND_WATER, dim, start, end, now, keys),
            "steam": _utility_series(Meter.KIND_STEAM, dim, start, end, now, keys),
        }

    # ── 合计 ──
    all_lines = list(Line.objects.filter(enabled=True))
    if scope == "line" and line_code:
        all_lines = [ln for ln in all_lines if ln.code == line_code.upper()]
    meters = _workshop_meters() if scope in ("workshop", "all") else (
        _line_meters(all_lines[0]) if all_lines else []
    )
    kwh_total = _range_total_kwh(meters, start, min(end, now.date()))
    out_total = _range_output(all_lines, start, min(end, now.date()))
    prod_real = (
        HourlyProduction.objects.filter(
            line__in=all_lines, date__gte=start, date__lte=min(end, now.date())
        )
        .exclude(source__in=DEMO_SOURCES)
        .exists()
    )

    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "granularity": granularity,
        "scope": scope,
        "line": line_code.upper() if line_code else None,
        "labels": labels,
        "series": series,
        "quality": quality_series,
        "utility": util,
        "totals": {
            "kwh": _round(kwh_total),
            "output": int(out_total) if prod_real else 0,
            "waterT": _sum_optional(util["water"]),
            "steamT": _sum_optional(util["steam"]),
        },
        "productionAvailable": prod_real,
        "bucketCount": len(labels),
    }


def _utility_hour_series(kind: str, buckets, start: date_cls, end: date_cls) -> list[float | None]:
    meters = list(Meter.objects.filter(kind=kind, enabled=True))
    if not meters:
        return [None for _ in buckets]
    mat = _hour_matrix(meters, buckets, start, end)
    return [_round(mat[b][0]) for b in buckets]


def _round(v: Decimal | None, digits: int = 3) -> float | None:
    if v is None:
        return None
    f = float(v)
    return round(f, digits)


def _sum_optional(values) -> float | None:
    """合计可空序列：全为 None 时返回 None（未知 ≠ 0）。"""
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums), 3)
