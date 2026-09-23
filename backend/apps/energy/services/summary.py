"""周期总计（日 / 月 / 年）的生成与查询。

为什么不每次现算（这是设计决策，别改回「查询时实时聚合」）：
  · 年 / 月聚合要扫数十万行小时数据，每次刷接口都现算会把大屏拖慢；
  · 大屏与后台必须拿到**完全相同**的数字，落表是唯一能保证一致的做法；
  · 历史月份封账后不再变化，落表后可直接作为报表与审计依据。

口径（与 `aggregate.py` 一脉相承，改之前先读那儿的注释）：
  · 电量：只累加 `quality = ok` 的小时用量。缺数的小时**不补 0**，只把整段标成 partial。
  · 产量：只累加 `quality ∈ (ok, estimated)` 的小时产量。产量未接入时全天为 0。
  · 单位电耗：产量为 0 或未接入时算出的是「除以零」，一律给 `null`，
    **不要用 0 兜底** —— 0 kWh/千瓶 会被读成「能效完美」，与事实相反。
  · 覆盖率（covered_hours / expected_hours）决定 quality：
      全部覆盖 → ok；一个都没有 → missing；其余 → partial。
    这是「合计宁可空白也不给偏小值」在周期级别上的落法：
    数字照给（它是可信部分的真实合计），但 quality 会如实告诉读者「这段不完整」。
"""

from __future__ import annotations

import calendar
import logging
from datetime import date as date_cls
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Sum

from ..models import HourlyProduction, HourlyUsage, Line, Meter, PeriodSummary

logger = logging.getLogger(__name__)

Q3 = Decimal("0.001")


# ══════════════════════════════════════════════════════════════
#  周期边界
# ══════════════════════════════════════════════════════════════


def period_bounds(dim: str, key: str) -> tuple[date_cls, date_cls]:
    """把 period_key 解析成 [start, end] 闭区间。"""
    if dim == PeriodSummary.DIM_DAY:
        d = datetime.strptime(key, "%Y-%m-%d").date()
        return d, d
    if dim == PeriodSummary.DIM_MONTH:
        y, m = (int(x) for x in key.split("-"))
        last = calendar.monthrange(y, m)[1]
        return date_cls(y, m, 1), date_cls(y, m, last)
    if dim == PeriodSummary.DIM_YEAR:
        y = int(key)
        return date_cls(y, 1, 1), date_cls(y, 12, 31)
    raise ValueError(f"未知维度：{dim}")


def make_key(dim: str, day: date_cls) -> str:
    if dim == PeriodSummary.DIM_DAY:
        return day.strftime("%Y-%m-%d")
    if dim == PeriodSummary.DIM_MONTH:
        return day.strftime("%Y-%m")
    return day.strftime("%Y")


def _hours_in(start: date_cls, end: date_cls, now: datetime) -> int:
    """周期内**应该**有多少个小时（进行中的周期只算已经过去的部分）。"""
    last = min(end, now.date())
    if last < start:
        return 0
    hours = (last - start).days * 24
    if last == now.date():
        hours += now.hour + 1
    else:
        hours += 24
    return hours


# ══════════════════════════════════════════════════════════════
#  单周期计算
# ══════════════════════════════════════════════════════════════


def _energy_part(meters: list[Meter], start: date_cls, end: date_cls) -> tuple[Decimal | None, int]:
    """区间电量合计与「齐全的小时数」。

    「齐全」= 该小时上这组计量点**每一个**都有可信的用量行。
    只要有一块表缺数，这个小时就不计入 covered —— 与小时级「宁可空白」的口径一致。
    """
    if not meters:
        return None, 0
    rows = HourlyUsage.objects.filter(
        meter__in=meters, date__gte=start, date__lte=end, quality__in=HourlyUsage.TRUSTED
    )
    total = rows.aggregate(t=Sum("usage"))["t"]
    if total is None:
        return None, 0
    covered = (
        rows.values("date", "hour").annotate(n=Count("id")).filter(n=len(meters)).count()
    )
    return Decimal(total), covered


def _production_part(lines: list[Line], start: date_cls, end: date_cls) -> tuple[Decimal, int]:
    """区间产量合计与有产量记录的小时数。未接入时返回 (0, 0)。"""
    if not lines:
        return Decimal(0), 0
    rows = HourlyProduction.objects.filter(
        line__in=lines, date__gte=start, date__lte=end, quality__in=HourlyProduction.TRUSTED
    )
    total = rows.aggregate(t=Sum("output"))["t"]
    covered = rows.values("date", "hour").annotate(n=Count("id")).filter(n=len(lines)).count()
    return (Decimal(total) if total is not None else Decimal(0)), covered


def _quality_from(covered: int, expected: int) -> str:
    if expected <= 0:
        return PeriodSummary.Q_MISSING
    if covered <= 0:
        return PeriodSummary.Q_MISSING
    if covered >= expected:
        return PeriodSummary.Q_OK
    return PeriodSummary.Q_PARTIAL


def _unit_kwh(kwh: Decimal | None, output: Decimal) -> Decimal | None:
    """单位电耗 = 电量 ÷ 产量 × 1000。产量为 0 时给 None，不给 0。"""
    if kwh is None or output <= 0:
        return None
    return (kwh / output * 1000).quantize(Q3)


def _plan_output(lines: list[Line], start: date_cls, end: date_cls, now: datetime) -> Decimal:
    """计划产量 = Σ(额定 × 已过小时数)。这是达成率的分母，必须与统计窗口一致。"""
    last = min(end, now.date())
    if last < start or not lines:
        return Decimal(0)
    hours = _hours_in(start, last, now)
    rated = sum(l.rated or 0 for l in lines)
    return Decimal(rated * hours)


def summarize_period(
    dim: str, key: str, *, scope: str, line: Line | None, meters: list[Meter], lines: list[Line],
    now: datetime | None = None,
) -> dict:
    """算一个 (层级, 维度, 周期) 的总计，返回可直接写库的字典。"""
    now = now or datetime.now()
    start, end = period_bounds(dim, key)

    kwh, e_covered = _energy_part(meters, start, end)
    output, _p_covered = _production_part(lines, start, end)

    expected = _hours_in(start, end, now)
    quality = _quality_from(e_covered, expected)

    # 水 / 汽：只有车间总表，不拆分到线别
    water: Decimal | None = None
    steam: Decimal | None = None
    if scope == PeriodSummary.SCOPE_WORKSHOP:
        w_meters = list(Meter.objects.filter(kind=Meter.KIND_WATER, enabled=True))
        s_meters = list(Meter.objects.filter(kind=Meter.KIND_STEAM, enabled=True))
        water = _energy_part(w_meters, start, end)[0]
        steam = _energy_part(s_meters, start, end)[0]

    # ⚠ 没有任何可信数据时写 NULL，不写 0。
    #   「空合计兜底成 0」是这个项目里反复出现的一类错误（B-502 / B-505 / B-508）：
    #   0 在界面上是「确实没用」，与「没采到」完全相反。
    has_energy = kwh is not None
    has_output = output > 0
    return {
        "scope": scope,
        "line": line,
        "dim": dim,
        "period_key": key,
        "start_date": start,
        "end_date": end,
        "output": output.quantize(Decimal("0.1")) if has_output else None,
        "kwh": kwh.quantize(Q3) if has_energy else None,
        "water_t": Decimal(water).quantize(Q3) if water is not None else None,
        "steam_t": Decimal(steam).quantize(Q3) if steam is not None else None,
        "plan_output": _plan_output(lines, start, end, now).quantize(Decimal("0.1")),
        "unit_kwh": _unit_kwh(kwh, output),
        "covered_hours": e_covered,
        "quality": quality,
    }


def upsert(payload: dict) -> PeriodSummary:
    """按唯一键写入或更新。"""
    key = {
        "scope": payload["scope"],
        "line": payload["line"],
        "dim": payload["dim"],
        "period_key": payload["period_key"],
    }
    obj, created = PeriodSummary.objects.update_or_create(defaults=payload, **key)
    return obj


# ══════════════════════════════════════════════════════════════
#  批量生成
# ══════════════════════════════════════════════════════════════


def summarize_days(day: date_cls, now: datetime | None = None) -> list[PeriodSummary]:
    """生成某一天的产线级 + 车间级总计。"""
    now = now or datetime.now()
    out: list[PeriodSummary] = []
    enabled_lines = list(Line.objects.filter(enabled=True))

    for line in enabled_lines:
        meters = [m for m in line.meters.all() if m.kind == Meter.KIND_ELEC and m.enabled]
        payload = summarize_period(
            PeriodSummary.DIM_DAY,
            make_key(PeriodSummary.DIM_DAY, day),
            scope=PeriodSummary.SCOPE_LINE,
            line=line,
            meters=meters,
            lines=[line],
            now=now,
        )
        out.append(upsert(payload))

    all_meters = list(Meter.objects.filter(kind=Meter.KIND_ELEC, enabled=True))
    out.append(
        upsert(
            summarize_period(
                PeriodSummary.DIM_DAY,
                make_key(PeriodSummary.DIM_DAY, day),
                scope=PeriodSummary.SCOPE_WORKSHOP,
                line=None,
                meters=all_meters,
                lines=enabled_lines,
                now=now,
            )
        )
    )
    return out


def summarize_periods(dim: str, days: list[date_cls], now: datetime | None = None) -> list[PeriodSummary]:
    """生成某维度的全部周期（传入该维度覆盖到的若干天即可去重）。"""
    now = now or datetime.now()
    keys: list[str] = []
    for d in days:
        k = make_key(dim, d)
        if k not in keys:
            keys.append(k)

    out: list[PeriodSummary] = []
    enabled_lines = list(Line.objects.filter(enabled=True))
    all_meters = list(Meter.objects.filter(kind=Meter.KIND_ELEC, enabled=True))

    for key in keys:
        for line in enabled_lines:
            meters = [m for m in line.meters.all() if m.kind == Meter.KIND_ELEC and m.enabled]
            out.append(
                upsert(
                    summarize_period(
                        dim, key, scope=PeriodSummary.SCOPE_LINE, line=line, meters=meters, lines=[line], now=now
                    )
                )
            )
        out.append(
            upsert(
                summarize_period(
                    dim,
                    key,
                    scope=PeriodSummary.SCOPE_WORKSHOP,
                    line=None,
                    meters=all_meters,
                    lines=enabled_lines,
                    now=now,
                )
            )
        )
    return out


# ── 趋势桶序列：一律**按时间正序**（最旧 → 最新）────────────────
# 这三个函数同时喂给「落表」（summarize_periods，顺序无关）和
# 「趋势图」（snapshot 的 daily30 / monthly12 / yearly5，顺序就是 X 轴）。
# 倒序会让当前日期落在 X 轴最左边，看的人得把图反过来读（v1.2.3 B-512）；
# 前端也按「末项 = 本期」取数（如总览的「去年」取 yearly5[-2]）。


def recent_days(count: int, now: datetime | None = None) -> list[date_cls]:
    """近 count 个自然日，正序，末项为今天。"""
    now = now or datetime.now()
    base = now.date()
    return [base - timedelta(days=i) for i in range(count - 1, -1, -1)]


def recent_months(count: int, now: datetime | None = None) -> list[date_cls]:
    """近 count 个月（每月取 1 号用于提取月份 key），正序，末项为当月。"""
    now = now or datetime.now()
    out: list[date_cls] = []
    for back in range(count - 1, -1, -1):
        y = now.year
        m = now.month - back
        while m <= 0:
            m += 12
            y -= 1
        out.append(date_cls(y, m, 1))
    return out


def recent_years(count: int, now: datetime | None = None) -> list[date_cls]:
    """近 count 个自然年（每年取 1 月 1 日），正序，末项为今年。"""
    now = now or datetime.now()
    return [date_cls(now.year - i, 1, 1) for i in range(count - 1, -1, -1)]
