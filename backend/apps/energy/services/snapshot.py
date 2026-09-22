"""把数据库里的小时用量组装成前端契约结构（`frontend/packages/shared/src/types.ts`）。

原则：
  · **产量（output）一律为 null / 0** —— 产量采集尚未改造，本次接口先留空。
    不拿电量去反推产量，也不填演示数据，避免大屏上出现看起来像真的假数字。
  · 缺数就是缺数：某个小时任何一块表缺数据，该小时的产线合计就返回 null，
    而不是「有几块算几块」的偏小值。宁可空一格，也不要一个偏低的数被当成真实用电。
  · 电量展示口径沿用旧版约定：**小于 20 kWh 保留 1 位小数**，否则取整。
    刚过整点时电量本来就小，取整会让单位电耗在刷新之间跳动。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Sum

from eep import conf

from ..models import HourlyUsage, Line, Meter
from .aggregate import current_hour_usage

logger = logging.getLogger(__name__)

#: 与前端 HourPoint 对齐的「未采集 / 未来时段」占位
NULL = None


def round_kwh(value: Decimal | None) -> float | None:
    """电量展示取整规则：< 20 kWh 保留 1 位小数，其余取整。"""
    if value is None:
        return None
    v = float(value)
    if abs(v) < 20:
        return round(v, 1)
    return round(v)


def round_any(value: Decimal | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


class HourCell:
    """一个「小时 × 一组计量点」的聚合单元格。"""

    __slots__ = ("value", "quality", "future", "partial", "progress")

    def __init__(
        self,
        value: Decimal | None,
        quality: str,
        *,
        future: bool = False,
        partial: bool = False,
        progress: float | None = None,
    ):
        self.value = value
        self.quality = quality
        self.future = future
        self.partial = partial
        self.progress = progress


def hourly_cells(meters: list[Meter], day: date_cls, now: datetime | None = None) -> dict[int, HourCell]:
    """算出某天 0—23 点、这组计量点的小时用量。

    区分的三种情况：
      · 已经过去的整点：读 HourlyUsage（聚合任务的产物）
      · 正在进行的小时：用「最新读数 − 上小时末读数」给参考值，标 partial + progress
      · 还没到的时段：value = null、future = true（前端画虚线，**绝不补 0**）
    """
    now = now or datetime.now()
    meters = list(meters)
    cells: dict[int, HourCell] = {}

    if not meters:
        return {h: HourCell(None, HourlyUsage.Q_MISSING, future=(h > now.hour and day == now.date())) for h in range(24)}

    rows = HourlyUsage.objects.filter(meter__in=meters, date=day)
    grouped: dict[int, list[HourlyUsage]] = defaultdict(list)
    for row in rows:
        grouped[row.hour].append(row)

    is_today = day == now.date()
    for hour in range(24):
        if is_today and hour > now.hour:
            cells[hour] = HourCell(None, "future", future=True)
            continue
        if day > now.date():
            cells[hour] = HourCell(None, "future", future=True)
            continue

        if is_today and hour == now.hour:
            cells[hour] = _current_hour_cell(meters, now)
            continue

        cells[hour] = _finished_hour_cell(meters, grouped.get(hour, []))

    return cells


def _finished_hour_cell(meters: list[Meter], rows: list[HourlyUsage]) -> HourCell:
    if not rows:
        return HourCell(None, HourlyUsage.Q_MISSING)

    if len(rows) < len(meters):
        # 有表没算出结果（比如聚合任务还没跑这个小时）→ 整个小时按缺数处理
        return HourCell(None, HourlyUsage.Q_MISSING)

    bad = [r for r in rows if r.quality in (HourlyUsage.Q_MISSING, HourlyUsage.Q_RESET)]
    if bad:
        # 任何一块表缺数/换表，合计就不成立 —— 不返回偏小的值
        return HourCell(None, bad[0].quality)

    usable = [r for r in rows if r.quality in HourlyUsage.TRUSTED]
    total = sum((r.usage or Decimal(0) for r in usable), Decimal(0))
    partial = len(usable) != len(rows)  # 有 spike / partial 被排除在外
    return HourCell(total, HourlyUsage.Q_PARTIAL if partial else HourlyUsage.Q_OK, partial=partial)


def _current_hour_cell(meters: list[Meter], now: datetime) -> HourCell:
    """进行中的小时：取各表参考值之和，并带上实际覆盖的占比。

    ⚠ progress 必须取各表算出来的值，**不能**用「本小时已过时间占比」：
    刚部署或采集中断后，参考值的基线是本小时的第一条读数，
    这时它只覆盖了很短的窗口，用「已过时间占比」会让人以为这是整小时的平均水平
    （曾经这么错过，现场看到 41 分钟用了 0.8 kWh 会以为产线停了）。
    """
    total = Decimal(0)
    got = 0
    best_progress: float | None = None
    for meter in meters:
        res = current_hour_usage(meter, now)
        if res and res.get("usage") is not None:
            total += res["usage"]
            got += 1
            if res.get("progress") is not None:
                best_progress = max(best_progress or 0.0, float(res["progress"]))

    if got == 0:
        return HourCell(None, HourlyUsage.Q_MISSING, partial=True, progress=best_progress)
    return HourCell(total, HourlyUsage.Q_PARTIAL, partial=True, progress=best_progress)


# ══════════════════════════════════════════════════════════════
#  组装前端结构
# ══════════════════════════════════════════════════════════════


def _hour_point(hour: int, cell: HourCell) -> dict:
    """→ HourPoint（output / unitKwh 固定为 null，产量侧未改造）。"""
    return {
        "hour": hour,
        "output": NULL,
        "kwh": round_kwh(cell.value),
        "unitKwh": NULL,
        "future": cell.future or None,
        "partial": cell.partial or None,
        "progress": round(cell.progress, 4) if cell.progress is not None else None,
        # 采集侧附加字段（前端可忽略）：数据质量，便于排障
        "quality": cell.quality,
    }


def _line_kwh_cells(line: Line, day: date_cls, now: datetime) -> dict[int, HourCell]:
    meters = [m for m in line.meters.all() if m.kind == Meter.KIND_ELEC and m.enabled]
    return hourly_cells(meters, day, now)


def _workshop_kwh_cells(day: date_cls, now: datetime) -> dict[int, HourCell]:
    meters = list(Meter.objects.filter(kind=Meter.KIND_ELEC, enabled=True))
    return hourly_cells(meters, day, now)


def _utility_cells(kind: str, day: date_cls, now: datetime) -> dict[int, HourCell]:
    meters = list(Meter.objects.filter(kind=kind, scope=Meter.SCOPE_WORKSHOP, enabled=True))
    return hourly_cells(meters, day, now)


def _sum_cells(cells: dict[int, HourCell], *, upto: int | None = None) -> Decimal | None:
    """把若干小时的用量加起来；只要有一个该有数的格子缺数，就返回 None。"""
    total = Decimal(0)
    for hour, cell in cells.items():
        if upto is not None and hour > upto:
            continue
        if cell.value is None:
            if cell.future:
                continue
            return None
        total += cell.value
    return total


def _trend_days(meters_fn, days: int, now: datetime) -> list[dict]:
    """近 N 天的日用量趋势（产量留空）。"""
    out = []
    for back in range(days - 1, -1, -1):
        day = (now - timedelta(days=back)).date()
        cells = meters_fn(day)
        total = _sum_cells({h: c for h, c in cells.items() if not c.future}, upto=23 if day != now.date() else now.hour)
        out.append({"label": day.strftime("%m-%d"), "output": 0, "kwh": round_kwh(total), "unitKwh": NULL})
    return out


def build_line(line: Line, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    today = now.date()
    yesterday = today - timedelta(days=1)

    cells = _line_kwh_cells(line, today, now)
    y_cells = _line_kwh_cells(line, yesterday, now)

    hours = [_hour_point(h, cells[h]) for h in range(24)]
    today_kwh = _sum_cells(cells, upto=now.hour)
    y_kwh = _sum_cells(y_cells, upto=23)

    # 状态：产量未接入，暂按「本小时电量是否明显大于 0」粗判，待产量改造后改为按产量判定
    current = cells.get(now.hour)
    running = bool(current and current.value and current.value > 0)
    status = "running" if running else "stopped"

    return {
        "id": line.code,
        "name": line.name,
        "rated": line.rated or 0,
        "product": line.product or "",
        "status": status,
        "note": "" if line.rated else "额定产能待产量侧接口接入后回填",
        "hours": hours,
        # ⚠ 产量相关字段一律留空（本次不采集产量，也不反推）
        # 产量相关字段一律留空（本次不采集产量，也不反推）：
        # output/plan/rate 为 0 表示「未接入」，unitKwh 为 null 表示「算不出来」。
        # 电量拿不到时给 null 而不是 0 —— 0 会被读成「今天没用电」。
        "today": {
            "output": 0,
            "kwh": round_kwh(today_kwh),
            "unitKwh": NULL,
            "plan": 0,
            "planFull": 0,
            "rate": 0,
        },
        "yesterday": {
            "output": 0,
            "kwh": round_kwh(y_kwh),
            "unitKwh": NULL,
            "fullOutput": 0,
            "fullKwh": 0,
        },
        "daily30": _trend_days(lambda d: _line_kwh_cells(line, d, now), 30, now),
        "heat": [[NULL for _ in range(24)] for _ in range(7)],  # 产量热力图：待产量接入
        "heatDays": [(now - timedelta(days=6 - i)).strftime("%m-%d") for i in range(7)],
        "month": {"output": 0, "kwh": _month_kwh(line, now)},
        "year": {"output": 0, "kwh": _year_kwh(line, now), "full": 0, "fullKwh": 0},
        "dayOfYear": now.timetuple().tm_yday,
    }


def _month_kwh(line: Line, now: datetime) -> float | None:
    meters = [m for m in line.meters.all() if m.kind == Meter.KIND_ELEC and m.enabled]
    agg = HourlyUsage.objects.filter(
        meter__in=meters,
        date__year=now.year,
        date__month=now.month,
        quality__in=HourlyUsage.TRUSTED,
    ).aggregate(total=Sum("usage"))
    return round_kwh(agg["total"])


def _year_kwh(line: Line, now: datetime) -> float | None:
    meters = [m for m in line.meters.all() if m.kind == Meter.KIND_ELEC and m.enabled]
    agg = HourlyUsage.objects.filter(
        meter__in=meters, date__year=now.year, quality__in=HourlyUsage.TRUSTED
    ).aggregate(total=Sum("usage"))
    return round_kwh(agg["total"])


def _utility_metric(kind: str, now: datetime, unit: str) -> dict:
    today = now.date()
    yesterday = today - timedelta(days=1)

    cells = _utility_cells(kind, today, now)
    y_cells = _utility_cells(kind, yesterday, now)

    trend = []
    for h in range(24):
        cell = cells[h]
        trend.append(round_any(cell.value, 2) if cell.value is not None else NULL)

    today_total = _sum_cells(cells, upto=now.hour)
    y_total = _sum_cells(y_cells, upto=23)

    # 瞬时流量：设备响应里的瞬时字段（var2/var3/var4）含义尚未与现场核对，
    # 先用「当前小时用量 ÷ 已覆盖时长」作为平均流量（t/h）的估算值。
    rate_now = 0.0
    current = cells.get(now.hour)
    if current and current.value is not None and current.progress:
        rate_now = round(float(current.value) / max(current.progress, 1e-6), 2)

    return {
        # 取不到就是 null（未知 ≠ 0）。前端若切到 api 模式，
        # 这几个字段的类型需从 number 放宽为 number | null。
        "today": round_any(today_total, 2),
        "yesterday": round_any(y_total, 2),
        "rateNow": rate_now,
        "unit": unit,
        "trend": trend,
    }


def build_workshop(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    lines = [build_line(ln, now) for ln in Line.objects.filter(enabled=True)]

    w_cells = _workshop_kwh_cells(now.date(), now)
    w_hours = [_hour_point(h, w_cells[h]) for h in range(24)]
    w_today = _sum_cells(w_cells, upto=now.hour)
    w_yesterday = _sum_cells(_workshop_kwh_cells(now.date() - timedelta(days=1), now), upto=23)

    water = _utility_metric(Meter.KIND_WATER, now, "t")
    steam = _utility_metric(Meter.KIND_STEAM, now, "t")

    # 车间告警：本期只保留「采集异常」提示（告警引擎后续接入）
    alerts = []
    for meter in Meter.objects.filter(enabled=True, last_ok=False).exclude(last_at=None)[:10]:
        alerts.append(
            {
                "level": "err",
                "text": f"{meter.name} 采集失败：{meter.last_error or '未知原因'}",
                "time": meter.last_at.strftime("%H:%M:%S") if meter.last_at else "--:--:--",
            }
        )

    return {
        "lines": lines,
        "today": {
            "output": 0,
            "kwh": round_kwh(w_today),
            "unitKwh": NULL,
            "plan": 0,
            "waterT": water["today"],
            "steamT": steam["today"],
        },
        "yesterday": {
            "output": 0,
            "kwh": round_kwh(w_yesterday),
            "unitKwh": NULL,
            "waterT": water["yesterday"],
            "steamT": steam["yesterday"],
        },
        "month": {"output": 0, "kwh": _workshop_range_kwh(now.replace(day=1).date(), now.date())},
        "year": {"output": 0, "kwh": _workshop_range_kwh(now.replace(month=1, day=1).date(), now.date())},
        "hours": w_hours,
        "daily30": _trend_days(lambda d: _workshop_kwh_cells(d, now), 30, now),
        "monthly12": _monthly_trend(now, 12),
        "yearly5": [],
        "utility": {"water": water, "steam": steam},
        "targetUnitKwh": float(conf.get("energy_defaults.target_unit_kwh", 0) or 0),
        "alerts": alerts,
        "currentHour": now.hour,
    }


def _workshop_range_kwh(start: date_cls, end: date_cls) -> float | None:
    agg = HourlyUsage.objects.filter(
        meter__kind=Meter.KIND_ELEC, date__gte=start, date__lte=end, quality__in=HourlyUsage.TRUSTED
    ).aggregate(total=Sum("usage"))
    return round_kwh(agg["total"])


def _monthly_trend(now: datetime, months: int) -> list[dict]:
    out = []
    for back in range(months - 1, -1, -1):
        # 从本月往前推 back 个月
        year = now.year
        month = now.month - back
        while month <= 0:
            month += 12
            year -= 1
        agg = HourlyUsage.objects.filter(
            meter__kind=Meter.KIND_ELEC, date__year=year, date__month=month, quality__in=HourlyUsage.TRUSTED
        ).aggregate(total=Sum("usage"))
        out.append({"label": f"{year}-{month:02d}", "output": 0, "kwh": round_kwh(agg["total"]), "unitKwh": NULL})
    return out


def build_overview(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    return {"now": now.strftime("%Y-%m-%d %H:%M:%S"), "workshop": build_workshop(now)}
