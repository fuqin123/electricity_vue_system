"""周期汇总（日 / 月 / 年）—— **读写都在这里**，别在别处再算一遍。

v1.3.0 的库表把「一行含电/水/汽」拆成了「按能源类型分行」：
    eep_period_summary（旧，一维）  →  energy_daily / energy_monthly / energy_yearly
                                      每行 = (产线 × 能源类型 × 周期)

为了让上层（大屏 / 后台 / 对外接口）保持「一个周期一行、电水汽都有」的旧形状，
本模块提供 `period_rows()` 把拆开的行**拼回**成 `PeriodRow`。
接口契约因此一行没改，前端零改动。

口径（与 `aggregate.py` 一脉相承，改之前先读那儿的注释）：
  · 只累加 `quality = ok` 的小时用量；缺数的小时**不补 0**，只把整段标成 partial；
  · 单位电耗在产量为 0 或未接入时给 `null`，**不要用 0 兜底**
    （0 kWh/千瓶 会被读成「能效完美」，与事实相反）；
  · 覆盖率决定 quality：全覆盖 → ok、一个都没有 → missing、其余 → partial。
    这是「合计宁可空白也不给偏小值」在周期级别的落法。

两条容易踩的口径：
  1. **车间电力合计没有独立总表** —— 它是 6 条产线之和，不是某一行；
     （水、汽才是挂在车间哨兵行上的总表读数）
  2. **产量不在 energy_* 汇总表里** —— 产量不是一种能源；
     单位电耗是查询时用「电的合计 ÷ 产量」算出来的，两边各自可空。
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Sum

from ..models import (
    EnergyDaily,
    EnergyHourly,
    EnergyMeter,
    EnergyMonthly,
    EnergyType,
    EnergyYearly,
    PeriodSummary,
    ProductionHourly,
    ProductionLine,
)
from . import coverage as C

logger = logging.getLogger(__name__)

Q3 = Decimal("0.001")

DIM_DAY = PeriodSummary.DIM_DAY
DIM_MONTH = PeriodSummary.DIM_MONTH
DIM_YEAR = PeriodSummary.DIM_YEAR
SCOPE_LINE = PeriodSummary.SCOPE_LINE
SCOPE_WORKSHOP = PeriodSummary.SCOPE_WORKSHOP
Q_OK = PeriodSummary.Q_OK
Q_PARTIAL = PeriodSummary.Q_PARTIAL
Q_MISSING = PeriodSummary.Q_MISSING


# ══════════════════════════════════════════════════════════════
#  周期边界
# ══════════════════════════════════════════════════════════════


def period_bounds(dim: str, key: str) -> tuple[date_cls, date_cls]:
    """把 period_key 解析成 [start, end] 闭区间。"""
    if dim == DIM_DAY:
        d = datetime.strptime(key, "%Y-%m-%d").date()
        return d, d
    if dim == DIM_MONTH:
        y, m = (int(x) for x in key.split("-"))
        last = calendar.monthrange(y, m)[1]
        return date_cls(y, m, 1), date_cls(y, m, last)
    if dim == DIM_YEAR:
        y = int(key)
        return date_cls(y, 1, 1), date_cls(y, 12, 31)
    raise ValueError(f"未知维度：{dim}")


def make_key(dim: str, day: date_cls) -> str:
    if dim == DIM_DAY:
        return day.strftime("%Y-%m-%d")
    if dim == DIM_MONTH:
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


def _days_in(start: date_cls, end: date_cls, now: datetime) -> int:
    last = min(end, now.date())
    if last < start:
        return 0
    return (last - start).days + 1


# ══════════════════════════════════════════════════════════════
#  写：生成汇总行
# ══════════════════════════════════════════════════════════════


def _quality_from(covered: int, expected: int) -> str:
    if expected <= 0 or covered <= 0:
        return Q_MISSING
    if covered >= expected:
        return Q_OK
    return Q_PARTIAL


#: 维度 → (汇总模型, 周期字段名)
_SUMMARY_MODEL = {
    DIM_DAY: (EnergyDaily, "stat_date"),
    DIM_MONTH: (EnergyMonthly, "stat_month"),
    DIM_YEAR: (EnergyYearly, "stat_year"),
}


def _period_value(dim: str, start: date_cls) -> object:
    """周期字段的取值：日=日期、月=当月第一天、年=年份。"""
    if dim == DIM_DAY:
        return start
    if dim == DIM_MONTH:
        return date_cls(start.year, start.month, 1)
    return start.year


def _elec_metrics(lines: list[ProductionLine], start: date_cls, end: date_cls) -> tuple[Decimal | None, int]:
    """一组产线的电量合计，以及「所有电表都有可信数据」的小时数。

    「齐全」= 那个小时上这组产线**每一块电表**都有可信的小时行。
    只要有一块表缺数，这个小时就不算 covered —— 与小时级「宁可空白」的口径一致。
    """
    meter_qs = EnergyMeter.objects.filter(
        line__in=lines, energy_type__type_code=EnergyType.ELECTRIC, enabled=True
    )
    n_meters = meter_qs.count()
    if n_meters == 0:
        return None, 0
    rows = EnergyHourly.objects.filter(
        meter__in=meter_qs,
        stat_date__gte=start,
        stat_date__lte=end,
        quality__in=EnergyHourly.TRUSTED,
    )
    total = rows.aggregate(t=Sum("consumption"))["t"]
    covered = (
        rows.values("stat_date", "stat_hour").annotate(n=Count("id")).filter(n=n_meters).count()
    )
    return (Decimal(total) if total is not None else None), covered


def _one_type_metrics(meter: EnergyMeter, start: date_cls, end: date_cls) -> tuple[Decimal | None, int]:
    """单个计量点（车间水/汽总表）在某周期的合计与覆盖小时数。"""

    def _calc(s: date_cls, e: date_cls) -> tuple[Decimal | None, int]:
        rows = EnergyHourly.objects.filter(
            meter=meter, stat_date__gte=s, stat_date__lte=e, quality__in=EnergyHourly.TRUSTED
        )
        total = rows.aggregate(t=Sum("consumption"))["t"]
        return (Decimal(total) if total is not None else None), rows.count()

    return _calc(start, end)


def _upsert(model, line: ProductionLine, etype: EnergyType, dim: str, start: date_cls, **fields) -> str:
    """幂等写入一条汇总行（唯一键 = 产线 × 能源类型 × 周期）。

    返回**周期键**（'2026-09-24' / '2026-09' / '2026'）——
    汇总表里没有 period_key 这一列（周期由 stat_date / stat_month / stat_year 表达），
    而调用方要的是「写了哪些周期」，所以在返回时算出来。
    """
    lookup = {"line": line, "energy_type": etype, _SUMMARY_MODEL[dim][1]: _period_value(dim, start)}
    model.objects.update_or_create(defaults=fields, **lookup)
    return make_key(dim, start)


def summarize_days(day: date_cls, now: datetime | None = None) -> list[str]:
    """生成（或刷新）某一天的全部汇总行：每条产线 + 车间行 × 每种能源。

    数据源是 `energy_hourly`（只取 quality=ok）。
    **不是**「日末读数 − 日初读数」—— 那样会静默吞掉采集中断的小时。
    """
    now = now or datetime.now()
    # ⚠ 不能用 `.all()`：多车间后表里有 workshop / aux 节点，
    #   它们不是产线，逐条写「产线汇总行」会产出无意义的行（B-519 那类漏改）。
    #   车间级水/汽由下面的 workshop 分支单独处理。
    lines = list(ProductionLine.lines_qs())
    workshops = _workshops()
    types = {t.type_code: t for t in EnergyType.objects.all()}
    written: list[str] = []

    for day_key_start in (day,):
        # ── 车间节点：水 / 汽（总表口径）──
        for ws in workshops:
            for code in (EnergyType.WATER, EnergyType.STEAM):
                etype = types.get(code)
                if etype is None:
                    continue
                meters = EnergyMeter.objects.filter(line=ws, energy_type=etype, enabled=True)
                if not meters.exists():
                    continue
                # 一块总表时行为与旧实现一致；多块则求和（缺一块即整段 None）
                total = Decimal(0)
                covered = 0
                for m in meters:
                    sub_total, sub_covered = _one_type_metrics(m, day, day)
                    if sub_total is None:
                        total = None
                        covered = 0
                        break
                    total += sub_total
                    covered = max(covered, sub_covered)
                written.append(
                    _upsert(
                        EnergyDaily,
                        ws,
                        etype,
                        DIM_DAY,
                        day,
                        consumption=total,
                        unit=etype.unit,
                        covered_count=covered,
                        expected_count=24,
                        quality=_quality_from(covered, 24),
                    )
                )

        # ── 产线节点：电力 ──
        for line in lines:
            total, covered = _elec_metrics([line], day, day)
            etype = types.get(EnergyType.ELECTRIC)
            if etype is not None:
                written.append(
                    _upsert(
                        EnergyDaily,
                        line,
                        etype,
                        DIM_DAY,
                        day,
                        consumption=total,
                        unit=etype.unit,
                        covered_count=covered,
                        expected_count=24,
                        quality=_quality_from(covered, 24),
                    )
                )

        # ── 辅助计量单元：电力（辅机 / 瓶胚 / 调配）──
        # 挂到 aux 节点自己名下，这样「车间合计 = 产线 + 辅机」可以直接由
        # 本车间所有节点的日行相加得出，不必在查询时特判。
        for aux in ProductionLine.objects.filter(node_type=ProductionLine.NODE_AUX):
            total, covered = _elec_metrics([aux], day, day)
            etype = types.get(EnergyType.ELECTRIC)
            if etype is not None:
                written.append(
                    _upsert(
                        EnergyDaily,
                        aux,
                        etype,
                        DIM_DAY,
                        day,
                        consumption=total,
                        unit=etype.unit,
                        covered_count=covered,
                        expected_count=24,
                        quality=_quality_from(covered, 24),
                    )
                )
    return written


def summarize_periods(dim: str, days: list[date_cls], now: datetime | None = None) -> list[str]:
    """生成月 / 年汇总行：**由日行累加**，保证「月 = 该月各日之和」。

    为什么不直接扫小时表：一是省一次全表扫描，二是三者口径天然一致 ——
    不会出现「日加起来不等于月」这种最难查的对不上。
    """
    if dim not in (DIM_MONTH, DIM_YEAR):
        raise ValueError("summarize_periods 只处理 month / year；日汇总用 summarize_days")
    now = now or datetime.now()
    model = _SUMMARY_MODEL[dim][0]
    written: list[str] = []

    for anchor in days:
        start, end = period_bounds(dim, make_key(dim, anchor))
        expected = _days_in(start, end, now) if dim == DIM_MONTH else 0
        # 与 summarize_days 同源：产线 + 车间 + 辅助单元三类节点都要有月/年行，
        # 否则「车间合计」在查询时按节点相加会漏掉辅机（见 _workshop_scope_lines）。
        for node in ProductionLine.objects.all():
            for etype in EnergyType.objects.all():
                rows = EnergyDaily.objects.filter(
                    line=node, energy_type=etype, stat_date__gte=start, stat_date__lte=end
                )
                agg = rows.aggregate(t=Sum("consumption"), c=Sum("covered_count"))
                # 该周期有多少天是有可信数据的（用来算覆盖率）
                covered_days = rows.filter(quality__in=(Q_OK, Q_PARTIAL)).count()
                total = agg["t"]
                if dim == DIM_YEAR:
                    expected = _days_in(start, end, now)
                written.append(
                    _upsert(
                        model,
                        node,
                        etype,
                        dim,
                        start,
                        consumption=Decimal(total) if total is not None else None,
                        unit=etype.unit,
                        covered_count=covered_days,
                        expected_count=expected,
                        quality=_quality_from(covered_days, expected),
                    )
                )
    return written


def refresh_rollups(now: datetime | None = None) -> list[str]:
    """**每天一次**的周期结算窗口：昨天 + 今天 + 近 2 月 + 今年。

    入口是 `manage.py summarize --rollup`（建议配 00:30 的计划任务），
    采集侧不再每小时刷（v1.4.2）。

    ⚠ 为什么必须带上昨天（B-523）：昨天 23 点的差值要等**今天 00:04** 采到
    跨日边界读数才算得出来，而那一刻 `now.date()` 已经是今天了。
    以前只刷今天，每一天的日总计都永远停在 23 个小时上
    （实测 09-25/26/27 覆盖数恒为 23，日合计 377.4 对不上小时表的 385.5），
    月 / 年又是按日行累加的，于是三个尺度一起偏小 —— 而大屏读的正是汇总表，
    后台读的是小时表，两边就永远差最后一小时。

    返回写过的周期键列表（供命令打印条数）。
    """
    now = now or datetime.now()
    written: list[str] = []
    for d in (now.date() - timedelta(days=1), now.date()):
        written += summarize_days(d, now)
    written += summarize_periods(DIM_MONTH, recent_months(2, now), now)
    written += summarize_periods(DIM_YEAR, recent_years(1, now), now)
    return written


def recent_days(count: int, now: datetime | None = None) -> list[date_cls]:
    """近 count 天（含今天），**时间正序**（末项 = 今天）。

    ⚠ 顺序不能反：X 轴是「从左往右读时间」，倒序会把今天放在最左边（B-512）。
    """
    now = now or datetime.now()
    return [now.date() - timedelta(days=i) for i in reversed(range(count))]


def recent_months(count: int, now: datetime | None = None) -> list[date_cls]:
    """近 count 个月（含当月），返回每月第一天，**时间正序**。"""
    now = now or datetime.now()
    out: list[date_cls] = []
    y, m = now.year, now.month
    for _ in range(count):
        out.append(date_cls(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def recent_years(count: int, now: datetime | None = None) -> list[date_cls]:
    """近 count 年（含今年），返回每年 1 月 1 日，**时间正序**。"""
    now = now or datetime.now()
    return [date_cls(now.year - i, 1, 1) for i in reversed(range(count))]


# ══════════════════════════════════════════════════════════════
#  读：把拆分的汇总行拼回「一个周期一行」的旧形状
# ══════════════════════════════════════════════════════════════


@dataclass
class PeriodRow:
    """一个周期的通行视图（电 / 水 / 汽 / 产量 齐备）。

    字段名与旧的 `PeriodSummary` 行保持一致，**外部读者与接口契约因此不用改**。
    """

    dim: str
    key: str
    scope: str
    line: ProductionLine | None
    start_date: date_cls
    end_date: date_cls
    kwh: Decimal | None
    output: Decimal | None
    unit_kwh: Decimal | None
    water_t: Decimal | None
    steam_t: Decimal | None
    covered_hours: int
    expected_hours: int
    quality: str


def _line_scope_lines(line: ProductionLine | None) -> list[ProductionLine]:
    """scope=line 时参与合计的产线（就是它自己）。"""
    return [line] if line is not None else []


def _workshop_scope_lines(workshop: ProductionLine | None = None) -> list[ProductionLine]:
    """scope=workshop 时参与**电力**合计的节点。

    ⚠ v1.5.2 口径变更（旧实现只返回全部真实产线，已作废）：
      现场车间级电力**有实测总表**（辅机 / 瓶胚 / 调配等），所以车间电力合计 =
      本车间产线电表 + 本车间辅助单元（aux）电表。旧口径「车间电力 = 6 条产线之和、
      无独立总表」是辅机未接入时的临时状态，辅机接入后继续沿用会**漏掉辅机电量**。

    `workshop` 给定时只算该车间下的节点；不给时（兼容旧调用方）返回全部产线与
    辅助单元，语义等同「全厂」。
    """
    qs = ProductionLine.objects.filter(
        node_type__in=(ProductionLine.NODE_LINE, ProductionLine.NODE_AUX)
    )
    if workshop is not None:
        qs = qs.filter(parent=workshop)
    return list(qs)


def _workshops() -> list[ProductionLine]:
    """全部车间节点，按 sort 排序（多车间后**不要**再写 `.first()` 取唯一车间）。"""
    return list(ProductionLine.workshops_qs().order_by("sort", "line_code"))


def _day_not_settled(dim: str, start: date_cls, now: datetime) -> bool:
    """这个日级周期是否属于「可能还没被每日结算写完整」的那两天（今天 / 昨天）。

    每天 00:30 才结算昨天，所以 **00:00—00:30 这段时间里昨天的日行还是前天 00:30
    写下的残缺值**（大屏刚跨零点翻页，「昨天那一根」就会先空一晚）。
    只对今天与昨天开放「行不完整就现算」的回退，更早的周期一律读表 ——
    回退一次要扫小时表，把它限制在两个周期内，`daily30` 这种长序列的成本才不会涨。

    另一层用处：`EEP-Rollup` 计划任务没注册或跑失败时，这里是**退化成现算**
    （数字仍然正确，只是多扫一次小时表），而不是给界面一个残缺的旧值。
    """
    if dim != DIM_DAY:
        return False
    return start >= now.date() - timedelta(days=1)


def is_open_period(dim: str, key: str, now: datetime | None = None) -> bool:
    """周期是否仍在进行中（即它包含 `now` 所在的那一天）。

    ⚠ 进行中的周期**一律不读汇总表**（v1.4.2 口径）：日 / 月 / 年汇总改成
    「每天 00:30 结算一次」之后，今天的日行、本月的月行在当天一整天都是旧的
    （00:30 那一刻它几乎是空的）。若照旧「有行就读行」，大屏与后台的
    「今日 / 本月 / 本年」会整天不动，值班人员只会以为系统卡死。
    所以规则改成：**已结束的周期读汇总表，进行中的周期从小时表现算** ——
    未来的小时在 `energy_hourly` 里本来就没有行，现算出来的天然就是「截至此刻」。
    """
    now = now or datetime.now()
    return make_key(dim, now.date()) == key


def period_rows(
    dim: str,
    scope: str,
    line: ProductionLine | None,
    keys: list[str],
    now: datetime | None = None,
) -> dict[str, PeriodRow]:
    """批量取若干周期的通行视图（缺行的周期会现算补上）。

    进行中的周期（见 `is_open_period`）永远现算，不读汇总表。
    """
    now = now or datetime.now()
    model = _SUMMARY_MODEL[dim][0]
    period_field = _SUMMARY_MODEL[dim][1]
    out: dict[str, PeriodRow] = {}

    types = {t.type_code: t for t in EnergyType.objects.all()}
    elec = types.get(EnergyType.ELECTRIC)

    if scope == SCOPE_WORKSHOP:
        agg_lines = _workshop_scope_lines()
    else:
        agg_lines = _line_scope_lines(line)

    # 车间的水/汽取车间节点上的总表。
    # ⚠ v1.5.2：旧实现是无条件 `.first()`（全库唯一一条车间哨兵行）。
    #   多车间下这会永远取到同一个车间 —— 碳水车间的界面会显示制造车间的蒸汽。
    #   现在按「传入的 line 是哪个车间」解析；调用方没有指定车间时（大屏的
    #   全厂视图）汇总全部车间的水/汽总表。
    workshop_lines: list[ProductionLine] = []
    if scope == SCOPE_WORKSHOP:
        if line is not None and line.is_workshop:
            workshop_lines = [line]
        else:
            workshop_lines = _workshops()

    for key in keys:
        start, end = period_bounds(dim, key)
        open_period = is_open_period(dim, key, now)
        expected = _hours_in(start, end, now) if dim == DIM_DAY else _days_in(start, end, now)
        # 每日结算（00:30）还没把「昨天」写完整的那段时间里，昨天也按当期现算 ——
        # 否则大屏零点后「昨天那一根」会先空一晚（见 _day_not_settled 的说明）
        unsettled_yesterday = _day_not_settled(dim, start, now)

        # ── 电力 ──
        live_fallback = False
        if elec is None or not agg_lines:
            kwh, covered = None, 0
            quality = Q_MISSING
        elif open_period:
            # 当期：从小时表现算。已结束的周期才允许读汇总表（汇总表是每日结算的产物）
            kwh, covered = _elec_metrics(agg_lines, start, end)
            quality = _quality_from(covered, expected)
        else:
            if dim == DIM_DAY:
                rows = EnergyDaily.objects.filter(
                    line__in=agg_lines, energy_type=elec, stat_date=start
                )
            else:
                rows = model.objects.filter(
                    line__in=agg_lines, energy_type=elec, **{period_field: _period_value(dim, start)}
                )
            total = rows.aggregate(t=Sum("consumption"))["t"]
            kwh = Decimal(total) if total is not None else None
            if len(agg_lines) == 1:
                covered = rows.first().covered_count if rows.exists() else 0
            else:
                # 车间合计：只有在「每条产线都有数据」的那些天/时段才算覆盖
                covered = rows.filter(quality__in=(Q_OK, Q_PARTIAL)).count() // 1
                covered = min(
                    [r.covered_count for r in rows if r.covered_count] or [0]
                )
            # 昨天 / 今天还没被每日结算写完整 → 退回小时表现算（见 _day_not_settled）
            live_fallback = unsettled_yesterday and covered < expected
            if live_fallback:
                kwh, covered = _elec_metrics(agg_lines, start, end)
            quality = _quality_from(covered, expected)

        # ── 水 / 汽（只有车间级有）──
        # 逐车间累加，但**只累加登记了该类总表的车间**。
        # 旧口径是「任一车间缺 ⇒ 全厂该类 None」，而它压根没区分两种「缺」：
        #   · 该车间根本没登记这块表（新车间先接电表、后接水表）→ 不该否决别人；
        #   · 登记了却没出数 → 是真缺数，仍然要让本车间这一格不成立。
        # 写入侧对没表的车间根本不落行（见 _rollup_lines 的 exists() 判断），
        # 所以旧写法会让「碳水车间有水表」这件事被「制造车间没水表」拖成 None。
        water = steam = None
        if workshop_lines:
            for code, holder in ((EnergyType.WATER, "water"), (EnergyType.STEAM, "steam")):
                etype = types.get(code)
                if etype is None:
                    continue
                parts: list[Decimal] = []
                missing = False
                for ws in workshop_lines:
                    meters = list(
                        EnergyMeter.objects.filter(line=ws, energy_type=etype, enabled=True)
                    )
                    if not meters:
                        continue  # 本车间没登记这类总表：不参与，也不否决
                    val: Decimal | None = None
                    if open_period or live_fallback:
                        # 多块总表时求和（如车间进水 + 车间回用）；
                        # 从未接入的那块先排除，否则它会永久否决本车间
                        subtotal = None
                        for m in C.participating(meters):
                            v = _one_type_metrics(m, start, end)[0]
                            if v is None:
                                subtotal = None
                                break
                            subtotal = v if subtotal is None else subtotal + v
                        val = subtotal
                    elif dim == DIM_DAY:
                        rows = EnergyDaily.objects.filter(
                            line=ws, energy_type=etype, stat_date=start
                        )
                        vals = [r.consumption for r in rows]
                        val = (
                            sum(vals, Decimal(0))
                            if vals and all(v is not None for v in vals)
                            else None
                        )
                    else:
                        rows = model.objects.filter(
                            line=ws,
                            energy_type=etype,
                            **{period_field: _period_value(dim, start)},
                        )
                        vals = [r.consumption for r in rows]
                        val = (
                            sum(vals, Decimal(0))
                            if vals and all(v is not None for v in vals)
                            else None
                        )
                    if val is None:
                        missing = True
                        break
                    parts.append(val)
                if not missing and parts:
                    total_val = sum(parts, Decimal(0))
                    if holder == "water":
                        water = total_val
                    else:
                        steam = total_val

        # ── 产量（不在汇总表里，按区间现算；24 行/天/线，开销可忽略）──
        output = None
        if agg_lines:
            prod = ProductionHourly.objects.filter(
                line__in=agg_lines,
                stat_date__gte=start,
                stat_date__lte=end,
                quality__in=ProductionHourly.TRUSTED,
            ).aggregate(t=Sum("output"))["t"]
            # 没有**真实**产量记录时给 None（不是 0）：
            # 0 表示「确实产出为 0」，None 表示「还没接入/没有可信记录」，语义相反。
            output = Decimal(prod) if prod is not None else None

        unit_kwh = None
        if kwh is not None and output is not None and output > 0:
            unit_kwh = (kwh / output * 1000).quantize(Q3)

        out[key] = PeriodRow(
            dim=dim,
            key=key,
            scope=scope,
            line=line,
            start_date=start,
            end_date=end,
            kwh=kwh,
            output=output,
            unit_kwh=unit_kwh,
            water_t=water,
            steam_t=steam,
            covered_hours=covered,
            expected_hours=expected,
            quality=quality,
        )
    return out
