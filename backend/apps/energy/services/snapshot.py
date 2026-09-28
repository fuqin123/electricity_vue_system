"""把数据库里的小时用量 / 小时产量组装成前端契约结构
（`frontend/packages/shared/src/types.ts`）。

原则：
  · **产量**优先读 `ProductionHourly`（产量侧接入后自动生效）。
    当前产量侧未改造，表里只有 `source=demo` 的占位 → 接口一律返回 **null**
    （v1.2.1 起占位统一为 null：0 会被读成「确实没产出」，与「还没接数据」是两回事），
    并且**不把它当作真实产量**（不据此判定停机、不算单位电耗）。
    绝不拿电量反推产量。
  · **缺数就是缺数**：某个小时任何一块表缺数据，该小时的产线合计就返回 null，
    而不是「有几块算几块」的偏小值。宁可空一格，也不要一个偏低的数被当成真实用电。
  · **日 / 月 / 年一律读 `eep_period_summary`**（与后台读同一张表）——
    这是保证「大屏与后台数字完全一致」的关键。表里缺行时才退化为现场现算。
  · 电量展示口径：**一律保留 1 位小数**（`round_kwh`，v1.4.1 起）。
    旧规则「≥20 取整」与后台表格的 1 位小数永远差 0.x，两边逐格核对必然被当成对不上。

查历史日期（接口带 `?date=`）时调用方必须传 `closed=True`：
这一天已经过完，24 个小时全部按聚合表结算，不能留一个「进行中」的实时窗口（B-522）。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as date_cls
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Max, Sum

from eep import conf

from ..models import (
    EnergyHourly,
    EnergyMeter,
    EnergyRaw,
    EnergyType,
    PeriodSummary,
    ProductionHourly,
    ProductionLine,
)
from .aggregate import current_hour_usage
from . import summary as S

logger = logging.getLogger(__name__)

#: 与前端 HourPoint 对齐的「未采集 / 未来时段」占位
NULL = None

#: 演示占位不算真实产量（见 ingest_production 的说明）
DEMO_SOURCES = (ProductionHourly.SOURCE_DEMO, ProductionHourly.SOURCE_NONE)


def round_kwh(value: Decimal | None) -> float | None:
    """电量展示口径：**统一保留 1 位小数**（v1.4.1，B-524）。

    旧规则是「< 20 kWh 保留 1 位、其余取整」。取整在大屏上看着干净，
    但后台的表格一直是 1 位小数，于是同一格「24.8 / 25」两边永远差一点 ——
    现场拿着两边核对时，第一个结论就是「系统数字对不上」，
    而真正的问题（缺数、延迟结算）反而被这种假差异淹掉了。
    两边同源同口径是这个项目的底线，宁可大屏多一位小数。
    """
    if value is None:
        return None
    return round(float(value), 1)


def round_any(value: Decimal | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


class HourCell:
    """一个「小时 × 一组计量点」的电量聚合单元格。"""

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


def hourly_cells(
    meters: list[EnergyMeter], day: date_cls, now: datetime | None = None, *, closed: bool = False
) -> dict[int, HourCell]:
    """算出某天 0—23 点、这组计量点的小时用量。

    区分的三种情况：
      · 已经过去的整点：读 EnergyHourly（聚合任务的产物）
      · 正在进行的小时：用「最新读数 − 上小时末读数」给参考值，标 partial + progress
      · 还没到的时段：value = null、future = true（前端画虚线，**绝不补 0**）

    `closed=True` 表示**这一天已经整日结束**（大屏/后台在查历史日期）。
    此时 24 个小时全部走聚合表，既没有「进行中」也没有「计划」。

    ⚠ 为什么这个参数必须显式传（B-522）：调用方为了表达「这一天过完了」，
    把 as-of 造成为该日的 23:59:59 —— 于是 `day == now.date()` 恒真，
    第 23 小时被误判成「进行中」，改走实时窗口。历史上实时窗口又没有上界，
    一格就装下了从那天 23:04 一直到此刻的全部用量（一线 8.1 → 85）。
    「这一天是否已经结束」是调用方的意图，不可能从时间戳里反推出来，只能说出来。
    """
    now = now or datetime.now()
    meters = list(meters)
    cells: dict[int, HourCell] = {}

    if not meters:
        return {
            h: HourCell(None, EnergyHourly.Q_MISSING, future=(not closed and h > now.hour and day == now.date()))
            for h in range(24)
        }

    rows = EnergyHourly.objects.filter(meter__in=meters, stat_date=day)
    grouped: dict[int, list[EnergyHourly]] = defaultdict(list)
    for row in rows:
        grouped[row.stat_hour].append(row)

    is_today = day == now.date()
    for hour in range(24):
        if closed:
            cells[hour] = _finished_hour_cell(meters, grouped.get(hour, []))
            continue
        if day > now.date():
            cells[hour] = HourCell(None, "future", future=True)
            continue
        if is_today and hour > now.hour:
            cells[hour] = HourCell(None, "future", future=True)
            continue

        if is_today and hour == now.hour:
            cells[hour] = _current_hour_cell(meters, now)
            continue

        cells[hour] = _finished_hour_cell(meters, grouped.get(hour, []))

    return cells


def _finished_hour_cell(meters: list[EnergyMeter], rows: list[EnergyHourly]) -> HourCell:
    if not rows:
        return HourCell(None, EnergyHourly.Q_MISSING)

    if len(rows) < len(meters):
        # 有表没算出结果（比如聚合任务还没跑这个小时）→ 整个小时按缺数处理
        return HourCell(None, EnergyHourly.Q_MISSING)

    bad = [r for r in rows if r.quality in (EnergyHourly.Q_MISSING, EnergyHourly.Q_RESET)]
    if bad:
        # 任何一块表缺数/换表，合计就不成立 —— 不返回偏小的值
        return HourCell(None, bad[0].quality)

    usable = [r for r in rows if r.quality in EnergyHourly.TRUSTED]
    if not usable:
        # ⚠ 一块表都没给出可信值（全是 partial / spike）时，合计必须是 None。
        #   sum(空) = 0 会让界面显示「这一小时 0 kWh」——
        #   和「产量未接入给 0」是同一类错误：0 会被当成「确实没用电」。
        #   宁可显示 `--`，也不要给一个看着正常的 0。
        return HourCell(None, EnergyHourly.Q_PARTIAL, partial=True)
    total = sum((r.consumption or Decimal(0) for r in usable), Decimal(0))
    partial = len(usable) != len(rows)  # 有 spike / partial 被排除在外
    return HourCell(total, EnergyHourly.Q_PARTIAL if partial else EnergyHourly.Q_OK, partial=partial)


def _current_hour_cell(meters: list[EnergyMeter], now: datetime) -> HourCell:
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
        return HourCell(None, EnergyHourly.Q_MISSING, partial=True, progress=best_progress)
    return HourCell(total, EnergyHourly.Q_PARTIAL, partial=True, progress=best_progress)


# ══════════════════════════════════════════════════════════════
#  产量（第二条腿）
# ══════════════════════════════════════════════════════════════


class ProdInfo:
    """某线某天的产量信息。

    `real` 为 False 表示**没有真实产量数据源**（表里只有演示占位或干脆没有），
    此时接口一律给 0，并且不据此判定停机、不算单位电耗。
    """

    __slots__ = ("by_hour", "real", "source", "quality")

    def __init__(self, by_hour: dict[int, Decimal | None], real: bool, source: str, quality: str):
        self.by_hour = by_hour
        self.real = real
        self.source = source
        self.quality = quality


def production_info(line: ProductionLine, day: date_cls) -> ProdInfo:
    rows = list(ProductionHourly.objects.filter(line=line, stat_date=day))
    if not rows:
        return ProdInfo({}, False, ProductionHourly.SOURCE_NONE, ProductionHourly.Q_NODATA)

    by_hour: dict[int, Decimal | None] = {}
    has_real = False
    src = ProductionHourly.SOURCE_NONE
    q = ProductionHourly.Q_NODATA
    for r in rows:
        if r.quality not in ProductionHourly.TRUSTED:
            continue
        by_hour[r.stat_hour] = r.output
        if r.source not in DEMO_SOURCES:
            has_real = True
            src = r.source
            q = r.quality
    if not has_real and rows:
        src = rows[0].source
        q = rows[0].quality
    return ProdInfo(by_hour, has_real, src, q)


def production_sum(line: ProductionLine, start: date_cls, end: date_cls) -> Decimal:
    """区间真实产量合计（演示占位不计入）。"""
    total = (
        ProductionHourly.objects.filter(
            line=line, stat_date__gte=start, stat_date__lte=end, quality__in=ProductionHourly.TRUSTED
        )
        .exclude(source__in=DEMO_SOURCES)
        .aggregate(t=Sum("output"))["t"]
    )
    return Decimal(total) if total is not None else Decimal(0)


# ══════════════════════════════════════════════════════════════
#  组装前端结构
# ══════════════════════════════════════════════════════════════


def _hour_point(hour: int, cell: HourCell, prod: Decimal | None = None, real_prod: bool = False) -> dict:
    """→ HourPoint。

    `real_prod=False`（产量未接入）时 output 给 **null**、unitKwh 给 null：
    null 是「还没接数据 / 算不出来」，0 是「确实没产出」—— 两者不能混。
    演示占位（source=demo / nodata）统一按 null 交付，等真实数据源接入后自动恢复数值。
    """
    output = (int(prod) if prod is not None else None) if real_prod else None
    if real_prod and prod is not None and cell.value is not None and prod > 0:
        unit = round(float(cell.value / prod * 1000), 2)
    else:
        unit = None
    return {
        "hour": hour,
        "output": output,
        "kwh": round_kwh(cell.value),
        "unitKwh": unit,
        "future": cell.future or None,
        "partial": cell.partial or None,
        "progress": round(cell.progress, 4) if cell.progress is not None else None,
        # 采集侧附加字段（前端可忽略）：数据质量，便于排障
        "quality": cell.quality,
    }


def _line_kwh_cells(line: ProductionLine, day: date_cls, now: datetime, closed: bool = False) -> dict[int, HourCell]:
    meters = [m for m in line.meters.all() if m.energy_type.type_code == EnergyType.ELECTRIC and m.enabled]
    return hourly_cells(meters, day, now, closed=closed)


def _workshop_kwh_cells(day: date_cls, now: datetime, closed: bool = False) -> dict[int, HourCell]:
    meters = list(EnergyMeter.objects.filter(energy_type__type_code=EnergyType.ELECTRIC, enabled=True))
    return hourly_cells(meters, day, now, closed=closed)


def _utility_cells(kind: str, day: date_cls, now: datetime, closed: bool = False) -> dict[int, HourCell]:
    meters = list(EnergyMeter.objects.filter(energy_type__type_code=kind, scope=EnergyMeter.SCOPE_WORKSHOP, enabled=True))
    return hourly_cells(meters, day, now, closed=closed)


def _sum_cells(cells: dict[int, HourCell], *, upto: int | None = None, running: bool = False):
    """把若干小时的用量加起来 → `(合计, 已覆盖小时数, 应有小时数)`。

    两种情况必须区别对待，否则要么瞒报、要么把界面搞成一片「--」：

    · **已结束的周期**（`running=False`，如昨日、历史某天）：
      只要有一个「该有数」的格子缺数，就返回 `None` —— 合计宁可空白，也不给偏小值。
      （偏小的数看着正常，最危险：曾经因此把整条线的用电少算了。）

    · **进行中的周期**（`running=True`，如今天、本小时所在的那一轮）：
      给「已有数据的部分合计」，**同时如实报告覆盖度**。
      现场出现过这个情况：系统当天才上线，0—14 点本来就没有数据，
      若按严格口径，整个大屏的「今日用电」全变成 `--`，值班人员以为系统坏了；
      若直接把 1 小时的数据当「今日累计」，又会看起来像「今天几乎没用电」。
      两个都不对 —— 正确做法是给数 + 标注「已采集 N/M 小时」（对应 docs/02 的 B-403）。
    """
    total = Decimal(0)
    covered = 0
    expected = 0
    for hour, cell in cells.items():
        if upto is not None and hour > upto:
            continue
        if cell.future:
            continue
        expected += 1
        if cell.value is None:
            continue
        total += cell.value
        covered += 1
    if running:
        return (total if covered else None), covered, expected
    if covered < expected:
        return None, covered, expected
    return total, covered, expected


# ── 周期序列：一律优先读日/月/年汇总表 ──────────────────────
# v1.3.0 起这些表按「产线 × 能源类型」分行，`S.period_rows` 会把它们拼回
# 「一个周期一行、电水汽齐备」的旧形状（PeriodRow），读者代码因此不用改。


def _summary_rows(dim: str, scope: str, line: ProductionLine | None, keys: list[str]):
    return S.period_rows(dim, scope, line, keys)


def _labels_for(dim: str, day: date_cls) -> str:
    if dim == PeriodSummary.DIM_DAY:
        return day.strftime("%m-%d")
    if dim == PeriodSummary.DIM_MONTH:
        return day.strftime("%Y-%m")
    return day.strftime("%Y年")


def _range_kwh(meters_qs, start: date_cls, end: date_cls) -> Decimal | None:
    """按日期区间直接汇总电量。

    未到的小时在 EnergyHourly 里**本来就没有行**，所以不需要再做时间截断，
    口径比「按小时累加再截断」更准（跨月时不会漏掉前几天晚上的小时）。
    """
    agg = EnergyHourly.objects.filter(
        meter__in=meters_qs, stat_date__gte=start, stat_date__lte=end, quality__in=EnergyHourly.TRUSTED
    ).aggregate(t=Sum("consumption"))
    return agg["t"]


def _period_series(
    dim: str,
    scope: str,
    line: ProductionLine | None,
    days: list[date_cls],
    *,
    prod_line: ProductionLine | None,
    now: datetime,
    real: bool = False,
) -> list[dict]:
    """生成周期趋势序列（近 N 日 / 近 12 月 / 近 5 年）。

    优先进 `eep_period_summary`（后台读的也是它 → 两边一致）；
    缺行时退化为现场现算，并在日志里留一条 warning（说明 summarize 没跑到）。

    `real=False`（产量未接入）时 output 一律给 null ——
    汇总表里的 0 只是「没有真实产量记录」的占位，不能当作「确实产出为 0」交付给界面。
    """
    keys = [S.make_key(dim, d) for d in days]
    rows = _summary_rows(dim, scope, line, keys)
    missing = [k for k in keys if k not in rows]
    if missing:
        logger.warning(
            "period_summary 缺行（%s/%s/%s）：%s —— 已退化为现场现算，建议补跑 manage.py summarize",
            dim, scope, line.line_code if line else "车间", ", ".join(missing[:5]),
        )

    out: list[dict] = []
    for d, key in zip(days, keys):
        row = rows.get(key)
        if row is not None:
            out.append(
                {
                    "label": _labels_for(dim, d),
                    # row.output 为 NULL = 该周期没有可信产量 → 留空
                    "output": int(row.output) if (real and row.output is not None) else (0 if real else NULL),
                    "kwh": round_kwh(row.kwh),
                    "unitKwh": round_any(row.unit_kwh, 2) if row.unit_kwh is not None else NULL,
                }
            )
            continue

        # ── 退化路径：现算（表里缺行时才会走到，日志已 warning）──
        if dim == PeriodSummary.DIM_DAY:
            start = end = d
        else:
            start, end = S.period_bounds(dim, key)
            if end > now.date():
                end = now.date()
        meters = (
            [m for m in line.meters.all() if m.energy_type.type_code == EnergyType.ELECTRIC and m.enabled]
            if line is not None
            else list(EnergyMeter.objects.filter(energy_type__type_code=EnergyType.ELECTRIC, enabled=True))
        )
        kwh = _range_kwh(meters, start, end)
        prodv = production_sum(prod_line, start, end) if prod_line else Decimal(0)
        unit = round(float(kwh / prodv * 1000), 2) if (kwh and prodv > 0) else NULL
        out.append(
            {
                "label": _labels_for(dim, d),
                "output": int(prodv) if real else NULL,
                "kwh": round_kwh(kwh),
                "unitKwh": unit,
            }
        )
    return out


def build_line(line: ProductionLine, now: datetime | None = None, *, closed: bool = False) -> dict:
    """组装单条产线的前端结构。`closed=True` = 查的是已经结束的整天（见 `hourly_cells`）。"""
    now = now or datetime.now()
    today = now.date()
    yesterday = today - timedelta(days=1)

    cells = _line_kwh_cells(line, today, now, closed)
    y_cells = _line_kwh_cells(line, yesterday, now, closed=True)
    prod = production_info(line, today)

    hours = [_hour_point(h, cells[h], prod.by_hour.get(h), prod.real) for h in range(24)]
    # 今天进行中：给部分合计 + 覆盖度；昨天已结束：严格口径
    today_kwh, cov, exp = _sum_cells(cells, upto=(23 if closed else now.hour), running=True)
    y_kwh, _, _ = _sum_cells(y_cells, upto=23)

    # ── 产量：只认真实数据源；演示占位与「无数据」都按未接入处理（null，不是 0）──
    today_out = production_sum(line, today, today) if prod.real else None
    y_out = production_sum(line, yesterday, yesterday) if prod.real else None

    # ── 日 / 月 / 年：优先读汇总表 ──
    day_rows = _summary_rows(PeriodSummary.DIM_DAY, PeriodSummary.SCOPE_LINE, line, [S.make_key("day", today)])
    today_row = day_rows.get(S.make_key("day", today))
    if today_row is not None:
        # 汇总表里今天是「进行中」的，直接用它的累计值
        month_kwh = _period_total(line, PeriodSummary.DIM_MONTH, now)
        year_kwh = _period_total(line, PeriodSummary.DIM_YEAR, now)
        month_out = _period_output(line, PeriodSummary.DIM_MONTH, now) if prod.real else None
        year_out = _period_output(line, PeriodSummary.DIM_YEAR, now) if prod.real else None
    else:
        month_kwh = _month_kwh(line, now)
        year_kwh = _year_kwh(line, now)
        month_out = None
        year_out = None

    # ── 状态判定 ──
    # 有真实产量 → 按产量判定（产能利用率）；否则按用电负荷判定。
    #
    # ⚠ 「用电负荷」必须取**最近一个已结束且有值的小时**，不能取当前小时（B-510）：
    #   每小时采样节奏下，当前小时的值要等下一个整点的边界采样做差才算得出来
    #   （见 slots.py）。取当前小时必然恒为 0，于是整点后的整整一个小时里，
    #   六条产线在大屏上会全部显示「停线」—— 而现场明明在用电
    #   （实测各线 6—44 kWh/h）。这类「口径没错但取值时机错」的问题，
    #   正是最容易被当成设备故障的一类。
    if prod.real:
        ref = int(prod.by_hour.get(now.hour) or 0)
        if ref <= 0:
            status = "stopped"
        elif line.rated and ref < line.rated * 0.75:
            status = "slow"
        else:
            status = "running"
    else:
        latest_hour = max(
            (h for h, c in cells.items() if h <= now.hour and c.value is not None),
            default=None,
        )
        load = cells[latest_hour].value if latest_hour is not None else Decimal(0)
        base = float(conf.get("energy_defaults.base_load_kwh_per_hour", 20) or 20)
        if load is None or load <= 0:
            status = "stopped"
        elif float(load) < base * 0.6:
            status = "slow"
        else:
            status = "running"

    # ── 周期趋势序列 ──
    d30 = S.recent_days(30, now)
    m12 = S.recent_months(12, now)
    y5 = S.recent_years(5, now)
    plan_now = _plan_for(line, today, now)

    return {
        "id": line.line_code,
        "name": line.line_name,
        "rated": line.rated or 0,
        "product": line.product or "",
        "status": status,
        "note": "" if prod.real else "产量数据源未接入",
        "hours": hours,
        "today": {
            "output": int(today_out) if today_out is not None else NULL,
            "kwh": round_kwh(today_kwh),
            "unitKwh": round(float(today_kwh / today_out * 1000), 2)
            if (today_kwh and today_out is not None and today_out > 0)
            else NULL,
            "plan": int(plan_now),
            "planFull": int(line.rated * 24 * 0.88) if line.rated else 0,
            "rate": round(float(today_out / plan_now * 100), 2)
            if (today_out is not None and plan_now > 0)
            else 0,
            # 今日用电的采集覆盖度 —— 界面必须显示，
            # 否则「今日累计」会在刚上线/采集中断时看起来像「今天几乎没用电」
            "kwhCoveredHours": cov,
            "kwhExpectedHours": exp,
        },
        "yesterday": {
            "output": int(y_out) if y_out is not None else NULL,
            "kwh": round_kwh(y_kwh),
            "unitKwh": round(float(y_kwh / y_out * 1000), 2)
            if (y_kwh and y_out is not None and y_out > 0)
            else NULL,
            "fullOutput": int(y_out) if y_out is not None else NULL,
            "fullKwh": round_kwh(y_kwh) or 0,
        },
        "daily30": _period_series(
            PeriodSummary.DIM_DAY, PeriodSummary.SCOPE_LINE, line, d30, prod_line=line, now=now, real=prod.real
        ),
        "monthly12": _period_series(
            PeriodSummary.DIM_MONTH, PeriodSummary.SCOPE_LINE, line, m12, prod_line=line, now=now, real=prod.real
        ),
        "yearly5": _period_series(
            PeriodSummary.DIM_YEAR, PeriodSummary.SCOPE_LINE, line, y5, prod_line=line, now=now, real=prod.real
        ),
        # 产量热力图：未接入时整片 null（≠ 0），前端渲染成「未上报」空格
        "heat": _heat(line, today, now, prod.real),
        "heatDays": [(today - timedelta(days=6 - i)).strftime("%m-%d") for i in range(7)],
        "month": {
            "output": int(month_out) if month_out is not None else NULL,
            "kwh": round_kwh(month_kwh),
        },
        "year": {
            "output": int(year_out) if year_out is not None else NULL,
            "kwh": round_kwh(year_kwh),
            "full": 0,
            "fullKwh": 0,
        },
        "dayOfYear": now.timetuple().tm_yday,
    }


def _heat(line: ProductionLine, today: date_cls, now: datetime, real: bool) -> list[list[int | None]]:
    """近 7 日 × 24 小时产量热力图，单位「百瓶」。未接入时整片 null。"""
    if not real:
        return [[NULL for _ in range(24)] for _ in range(7)]
    rows = ProductionHourly.objects.filter(
        line=line, stat_date__gte=today - timedelta(days=6), stat_date__lte=today, quality__in=ProductionHourly.TRUSTED
    ).exclude(source__in=DEMO_SOURCES)
    grid: list[list[int | None]] = [[NULL for _ in range(24)] for _ in range(7)]
    for r in rows:
        idx = 6 - (today - r.date).days
        if 0 <= idx < 7 and r.output is not None:
            grid[idx][r.stat_hour] = int(r.output / 100)
    return grid


def _plan_for(line: ProductionLine, day: date_cls, now: datetime) -> Decimal:
    """截至当前小时的应产（达成率分母，必须与统计窗口一致）。"""
    if day != now.date():
        hours = 24
    else:
        hours = now.hour + 1
    return Decimal((line.rated or 0) * 0.88 * hours)


def _period_total(line: ProductionLine, dim: str, now: datetime) -> Decimal | None:
    """从汇总表取某周期的电量；缺行则退化为现算。"""
    key = S.make_key(dim, now.date())
    row = _summary_rows(dim, PeriodSummary.SCOPE_LINE, line, [key]).get(key)
    if row is not None:
        return row.kwh  # 可能为 None（该周期无可信数据），由调用方按「未知」处理
    if dim == PeriodSummary.DIM_MONTH:
        return _month_kwh_raw(line, now)
    return _year_kwh_raw(line, now)


def _period_output(line: ProductionLine, dim: str, now: datetime) -> Decimal:
    key = S.make_key(dim, now.date())
    row = _summary_rows(dim, PeriodSummary.SCOPE_LINE, line, [key]).get(key)
    if row is not None:
        return row.output if row.output is not None else Decimal(0)
    return production_sum(line, *S.period_bounds(dim, key))


def _month_kwh(line: ProductionLine, now: datetime) -> float | None:
    return round_kwh(_month_kwh_raw(line, now))


def _month_kwh_raw(line: ProductionLine, now: datetime) -> Decimal | None:
    meters = [m for m in line.meters.all() if m.energy_type.type_code == EnergyType.ELECTRIC and m.enabled]
    agg = EnergyHourly.objects.filter(
        meter__in=meters, stat_date__year=now.year, stat_date__month=now.month, quality__in=EnergyHourly.TRUSTED
    ).aggregate(total=Sum("consumption"))
    return agg["total"]


def _year_kwh(line: ProductionLine, now: datetime) -> float | None:
    return round_kwh(_year_kwh_raw(line, now))


def _year_kwh_raw(line: ProductionLine, now: datetime) -> Decimal | None:
    meters = [m for m in line.meters.all() if m.energy_type.type_code == EnergyType.ELECTRIC and m.enabled]
    agg = EnergyHourly.objects.filter(
        meter__in=meters, stat_date__year=now.year, quality__in=EnergyHourly.TRUSTED
    ).aggregate(total=Sum("consumption"))
    return agg["total"]


def _utility_metric(kind: str, now: datetime, unit: str, closed: bool = False) -> dict:
    today = now.date()
    yesterday = today - timedelta(days=1)

    cells = _utility_cells(kind, today, now, closed)
    y_cells = _utility_cells(kind, yesterday, now, closed=True)

    trend = [round_any(cells[h].value, 2) if cells[h].value is not None else NULL for h in range(24)]

    today_total, cov, exp = _sum_cells(cells, upto=(23 if closed else now.hour), running=True)
    y_total, _, _ = _sum_cells(y_cells, upto=23)

    # 瞬时流量：设备响应里的瞬时字段（var2/var3/var4）含义尚未与现场核对，
    # 先用「当前小时用量 ÷ 已覆盖时长」作为平均流量（t/h）的估算值。
    # 查历史整日时没有「当前小时」，给 null —— 0 会被读成「此刻没有流量」。
    rate_now: float | None = None
    if not closed:
        current = cells.get(now.hour)
        if current and current.value is not None and current.progress:
            rate_now = round(float(current.value) / max(current.progress, 1e-6), 2)

    return {
        # 取不到就是 null（未知 ≠ 0）
        "today": round_any(today_total, 2),
        "yesterday": round_any(y_total, 2),
        "rateNow": rate_now,
        "unit": unit,
        "trend": trend,
        # 覆盖度：未采集的小时不补 0，但要让界面能说明「只统计了 N/M 小时」
        "coveredHours": cov,
        "expectedHours": exp,
    }


def build_workshop(now: datetime | None = None, *, closed: bool = False) -> dict:
    now = now or datetime.now()
    lines = list(ProductionLine.objects.filter(status=ProductionLine.STATUS_ON, line_type=ProductionLine.TYPE_LINE))
    line_dicts = [build_line(ln, now, closed=closed) for ln in lines]

    w_cells = _workshop_kwh_cells(now.date(), now, closed)
    w_hours = [_hour_point(h, w_cells[h]) for h in range(24)]
    w_today, w_cov, w_exp = _sum_cells(w_cells, upto=(23 if closed else now.hour), running=True)
    w_yesterday, _, _ = _sum_cells(_workshop_kwh_cells(now.date() - timedelta(days=1), now, closed=True), upto=23)

    water = _utility_metric(EnergyType.WATER, now, "t", closed)
    steam = _utility_metric(EnergyType.STEAM, now, "t", closed)

    # 车间产量 = 各线真实产量之和（演示占位已在上游被排除）
    # 是否有**真实**产量数据源（演示占位不算）—— 决定要不要挂「产量未接入」提示
    real_any = any(production_info(ln, now.date()).real for ln in lines)
    w_today_out = sum(int(ln["today"]["output"]) for ln in line_dicts) if real_any else None  # type: ignore[index]
    w_y_out = sum(int(ln["yesterday"]["output"]) for ln in line_dicts) if real_any else None  # type: ignore[index]

    # 车间告警：只保留「采集异常」提示（告警引擎后续接入）
    alerts = []
    if not real_any:
        alerts.append(
            {
                "level": "info",
                "text": "产量数据源未接入（灌装机计数器 / 装箱机 PLC / MES 班次报工待改造），本期产量统一显示为 --",
                "time": "--:--:--",
            }
        )
    for meter in EnergyMeter.objects.filter(enabled=True, last_ok=False).exclude(last_at=None)[:10]:
        alerts.append(
            {
                "level": "err",
                "text": f"{meter.name} 采集失败：{meter.last_error or '未知原因'}",
                "time": meter.last_at.strftime("%H:%M:%S") if meter.last_at else "--:--:--",
            }
        )

    # 采集整体停摆提示（B-511）。
    # 为什么单独做这一条：采集若整体停掉（计划任务被停、机器重启后没恢复、网络断），
    # 每个点位的 `last_ok` 都还停在上次成功时的 True —— 上面那条失败告警不会触发，
    # 大屏于是安静地显示**冻结的旧值**，现场只会觉得「今天的数怎么不动」。
    # 每小时节奏下最新读数最旧也是约 1 小时前，所以阈值给 2 小时足够宽松。
    stale_hours = int(conf.get("collect.stale_alert_hours", 2) or 2)
    newest = EnergyRaw.objects.filter(meter__enabled=True).aggregate(m=Max("collect_time"))["m"]
    if newest is None:
        alerts.append(
            {
                "level": "err",
                "text": "尚无任何采集数据 —— 请确认计划任务 EEP-Collect 是否在运行",
                "time": "--:--:--",
            }
        )
    elif (now - newest).total_seconds() > stale_hours * 3600:
        alerts.append(
            {
                "level": "err",
                "text": (
                    f"采集疑似中断：最新读数停留在 {newest:%m-%d %H:%M}"
                    f"（已超过 {stale_hours} 小时未更新），请检查计划任务 EEP-Collect"
                ),
                "time": newest.strftime("%H:%M:%S"),
            }
        )

    d30 = S.recent_days(30, now)
    m12 = S.recent_months(12, now)
    y5 = S.recent_years(5, now)
    workshop_total = lambda dim: _workshop_period(dim, now, real_any)  # noqa: E731

    month_row = workshop_total(PeriodSummary.DIM_MONTH)
    year_row = workshop_total(PeriodSummary.DIM_YEAR)
    w_plan = sum(int(ln["today"]["plan"]) for ln in line_dicts)  # type: ignore[index]

    return {
        "lines": line_dicts,
        "today": {
            "output": w_today_out,
            "kwh": round_kwh(w_today),
            "unitKwh": round(float(w_today / w_today_out * 1000), 2)
            if (w_today and w_today_out is not None and w_today_out > 0)
            else NULL,
            "plan": w_plan,
            "waterT": water["today"],
            "steamT": steam["today"],
            "kwhCoveredHours": w_cov,
            "kwhExpectedHours": w_exp,
        },
        "yesterday": {
            "output": w_y_out,
            "kwh": round_kwh(w_yesterday),
            "unitKwh": NULL,
            "waterT": water["yesterday"],
            "steamT": steam["yesterday"],
        },
        "month": {"output": month_row["output"], "kwh": round_kwh(month_row["kwh"])},
        "year": {"output": year_row["output"], "kwh": round_kwh(year_row["kwh"])},
        "hours": w_hours,
        "daily30": _period_series(
            PeriodSummary.DIM_DAY, PeriodSummary.SCOPE_WORKSHOP, None, d30, prod_line=None, now=now, real=real_any
        ),
        "monthly12": _period_series(
            PeriodSummary.DIM_MONTH, PeriodSummary.SCOPE_WORKSHOP, None, m12, prod_line=None, now=now, real=real_any
        ),
        "yearly5": _period_series(
            PeriodSummary.DIM_YEAR, PeriodSummary.SCOPE_WORKSHOP, None, y5, prod_line=None, now=now, real=real_any
        ),
        "utility": {"water": water, "steam": steam},
        "targetUnitKwh": float(conf.get("energy_defaults.target_unit_kwh", 0) or 0),
        "alerts": alerts,
        # currentHour = 排序与「第 N 点已出数」的锚点（查历史整日时 = 23）
        # ongoingHour = 真正「还没结束」的小时；查历史整日时为 null。
        # 两者必须分开（B-522，与 B-518 同源）：拿锚点当进行中，
        # 界面就会在 09-27 的 23 点写「进行中」，而那一格其实早就结算完了。
        "currentHour": now.hour,
        "ongoingHour": None if closed else now.hour,
        "dayClosed": closed or None,
    }


def _workshop_period(dim: str, now: datetime, real: bool) -> dict:
    """车间级某周期的汇总（产量 + 电量），优先读汇总表。

    `real=False`（产量未接入）时 output 给 null —— 与线级口径一致。
    """
    key = S.make_key(dim, now.date())
    row = _summary_rows(dim, PeriodSummary.SCOPE_WORKSHOP, None, [key]).get(key)
    if row is not None:
        # output 为 NULL 表示该周期没有可信产量；real=False（产量未接入）时统一留空
        return {
            "output": int(row.output) if (real and row.output is not None) else (0 if real else NULL),
            "kwh": row.kwh,
        }
    start, end = S.period_bounds(dim, key)
    agg = EnergyHourly.objects.filter(
        meter__energy_type__type_code=EnergyType.ELECTRIC, stat_date__gte=start, stat_date__lte=end, quality__in=EnergyHourly.TRUSTED
    ).aggregate(total=Sum("consumption"))
    out = sum(
        (production_sum(ln, start, end) for ln in ProductionLine.objects.filter(status=ProductionLine.STATUS_ON, line_type=ProductionLine.TYPE_LINE)), Decimal(0)
    )
    return {"output": int(out) if real else NULL, "kwh": agg["total"] or Decimal(0)}


def build_overview(now: datetime | None = None, *, closed: bool = False) -> dict:
    now = now or datetime.now()
    return {"now": now.strftime("%Y-%m-%d %H:%M:%S"), "workshop": build_workshop(now, closed=closed)}
