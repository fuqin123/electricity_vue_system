"""生产日（06:00—次日 06:00）的唯一口径。

现场按「每天 06:00 到次日 06:00」统计一个生产日，标签用开始日期：

    2026-10-22 生产日 = [2026-10-22 06:00, 2026-10-23 06:00)

所有「今天 / 昨天 / 本月 / 本年」、日汇总、结算与告警都必须经过本模块，
不要在别处再写 `now.date()` / `timedelta(days=1)`：那会把 00:00—06:00
的数据算到错误的日期，且大屏、后台、告警会各错一套。
"""

from __future__ import annotations

import calendar
from datetime import date as date_cls
from datetime import datetime, time, timedelta

from django.db.models import Q

#: 生产日的开始小时。全年固定（含节假日），不做节假日日历。
DAY_START_HOUR = 6


def production_day(value: datetime | date_cls) -> date_cls:
    """把时刻归到所属生产日的**开始日期**。

    - `datetime(2026-10-23 03:00)` → `2026-10-22`
    - `datetime(2026-10-23 06:00)` → `2026-10-23`
    - 纯 `date` 原样返回：接口里的 `date=2026-10-22` 已经表示生产日开始日。
    """
    if isinstance(value, datetime):
        if value.hour < DAY_START_HOUR:
            return value.date() - timedelta(days=1)
        return value.date()
    return value


def day_start(day: date_cls) -> datetime:
    return datetime.combine(day, time(DAY_START_HOUR))


def day_end(day: date_cls) -> datetime:
    """生产日结束时刻（次日 06:00，不含）。"""
    return day_start(day + timedelta(days=1))


def day_hours(day: date_cls) -> list[tuple[date_cls, int]]:
    """生产日内的 24 个（自然日期, 时钟小时），按生产日顺序。"""
    return [(day, h) for h in range(DAY_START_HOUR, 24)] + [
        (day + timedelta(days=1), h) for h in range(0, DAY_START_HOUR)
    ]


def hour_bucket(day: date_cls, index: int) -> tuple[date_cls, int]:
    """生产日第 `index` 格（0 = 06:00）对应的（自然日期, 时钟小时）。"""
    if not 0 <= index <= 23:
        raise ValueError(f"生产日小时序号必须是 0—23，收到 {index}")
    clock = (DAY_START_HOUR + index) % 24
    return (day if clock >= DAY_START_HOUR else day + timedelta(days=1), clock)


def hour_index(day: date_cls, stat_date: date_cls, stat_hour: int) -> int | None:
    """（自然日期, 时钟小时）→ 生产日序号；不属于该生产日时返回 None。"""
    if stat_date == day and stat_hour >= DAY_START_HOUR:
        return stat_hour - DAY_START_HOUR
    if stat_date == day + timedelta(days=1) and stat_hour < DAY_START_HOUR:
        return (24 - DAY_START_HOUR) + stat_hour
    return None


def day_of_hour(stat_date: date_cls, stat_hour: int) -> date_cls:
    """某个自然小时属于哪个生产日（按小时开始时刻归集）。"""
    return production_day(datetime.combine(stat_date, time(stat_hour)))


def is_current(day: date_cls, now: datetime) -> bool:
    return day_start(day) <= now < day_end(day)


def hour_index_at(day: date_cls, now: datetime) -> int | None:
    """`now` 落在生产日的第几格（0—23）；不在该生产日内返回 None。"""
    if not is_current(day, now):
        return None
    return min(23, int((now - day_start(day)).total_seconds() // 3600))


def started_hours(day: date_cls, now: datetime) -> int:
    """生产日到 `now` 为止已经**开始**的小时数（含正在走的这一小时，封顶 24）。"""
    if now <= day_start(day):
        return 0
    if now >= day_end(day):
        return 24
    return min(24, int((now - day_start(day)).total_seconds() // 3600) + 1)


def window_hours(start: datetime, end: datetime) -> list[tuple[date_cls, int]]:
    """[start, end) 内的全部小时桶（按时间正序），两端都应对齐整点。"""
    out: list[tuple[date_cls, int]] = []
    cur = start
    while cur < end:
        out.append((cur.date(), cur.hour))
        cur += timedelta(hours=1)
    return out


def hour_q(start: datetime, end: datetime) -> Q:
    """筛选 `EnergyHourly` / `ProductionHourly` 中开始时刻落在 [start, end) 的桶。

    两张表都用 `stat_date + stat_hour` 表示小时开始，因此不能简单用
    `stat_date__range` —— 生产日的 00—05 属于次日，跨日边界会漏掉或串日。
    """
    if end <= start:
        return Q(pk__in=[])
    first, last = start.date(), end.date()
    if first == last:
        return Q(stat_date=first, stat_hour__gte=start.hour, stat_hour__lt=end.hour)
    q = Q(stat_date=first, stat_hour__gte=start.hour) | Q(
        stat_date__gt=first, stat_date__lt=last
    )
    if end.hour:
        q |= Q(stat_date=last, stat_hour__lt=end.hour)
    return q


def period_window(dim: str, key: str) -> tuple[datetime, datetime]:
    """周期键 → 按生产日口径的实际起止时刻（[start, end)）。

    日：D 06:00 → D+1 06:00
    月：当月 1 日 06:00 → 次月 1 日 06:00
    年：当年 1 月 1 日 06:00 → 次年 1 月 1 日 06:00

    注意：月 / 年汇总行仍按 `stat_date` 落在自然月 / 自然年内的**生产日**
    累加（见 `summary.summarize_periods`），与这里的窗口等价。
    """
    if dim == "day":
        d = datetime.strptime(key, "%Y-%m-%d").date()
        return day_start(d), day_end(d)
    if dim == "month":
        y, m = (int(x) for x in key.split("-"))
        start = date_cls(y, m, 1)
        last = calendar.monthrange(y, m)[1]
        return day_start(start), day_end(start + timedelta(days=last - 1))
    if dim == "year":
        y = int(key)
        return day_start(date_cls(y, 1, 1)), day_end(date_cls(y, 12, 31))
    raise ValueError(f"未知维度：{dim}")
