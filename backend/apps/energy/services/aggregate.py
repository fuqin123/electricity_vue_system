"""小时差值聚合 —— 把累计值读数变成「每小时用了多少」。

这是整套采集里最容易出错、也最影响数字可信度的一环，口径写死在这里，别在别处再算一遍。

## 差值口径

对某小时 H（如 14:00—15:00）：

    usage(H) = end(H) − end(H−1)
    end(H)   = 「时间 ≤ H:00:00 + 1h」的最后一条有效读数（即距 15:00 最近的一次采样）

用「上小时末读数」当起点而不是「本小时第一条读数」，好处是：
  · 相邻两小时首尾相接，不会漏计也不会重复计；
  · 采集中断一段时间后恢复，第一个人恢复出来的小时仍是正确的区间，不会把中断期间的
    用量算进某一个小时（它会被判成 partial，因为端点离整点太远）。

## 异常处理（每条都对应一次真实踩坑，别删）

| 情况 | 处理 | 为什么 |
| :--- | :--- | :--- |
| 缺读数 | usage = null，quality = missing | **绝不补 0**：补 0 在图上就是「产量暴跌」 |
| 差值 < 0 | usage = null，quality = reset | 换表 / 表归零会算出负数，任何负数都不能进聚合 |
| 差值 > max_hourly | 保留数值，quality = spike，**不计入聚合** | 断讯后补传、换表瞬间会出现尖峰 |
| 端点离整点 > 容差 | quality = partial，数值保留参考 | 采集中断过，这个「小时」实际跨了更长时间 |
| 只有 quality = ok | 才允许进入产线 / 车间合计 | 口径干净 |
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import transaction

from eep import conf

from ..models import HourlyUsage, Meter, Reading

logger = logging.getLogger(__name__)

#: 端点读数距离整点超过多少分钟就算「偏离」（采集中断过）
BOUNDARY_TOLERANCE = timedelta(minutes=int(conf.get("collect.boundary_tolerance_minutes", 20)))
#: 累计值回退超过该比例时，在说明里记为「疑似换表」
RESET_RATIO = Decimal(str(conf.get("collect.filter.reset_threshold_ratio", 0.5)))
#: 小数位（库字段是 3 位）
Q3 = Decimal("0.001")


def hour_window(day: date_cls, hour: int) -> tuple[datetime, datetime]:
    """返回某小时的 [起点, 终点] 边界时间。终点 = 下一小时整点（含）。"""
    start = datetime.combine(day, time(hour))
    return start, start + timedelta(hours=1)


def _last_reading_before(meter: Meter, upper: datetime) -> Reading | None:
    """取「采样时间 ≤ upper」的最后一条有效读数。"""
    return (
        Reading.objects.filter(meter=meter, ok=True, value__isnull=False, at__lte=upper)
        .order_by("-at")
        .first()
    )


def compute_hour(meter: Meter, day: date_cls, hour: int) -> dict:
    """算一个计量点在某小时的用量（不落库，纯计算，便于测试与复查）。"""
    hour_start, end_limit = hour_window(day, hour)

    end = _last_reading_before(meter, end_limit)
    if end is None:
        return {
            "usage": None,
            "quality": HourlyUsage.Q_MISSING,
            "note": f"{end_limit:%H:%M} 前无有效读数",
        }

    start = _last_reading_before(meter, hour_start)
    if start is None:
        return {
            "usage": None,
            "quality": HourlyUsage.Q_MISSING,
            "note": f"{hour_start:%H:%M} 前无有效读数（无法确定起点）",
            "end_at": end.at,
            "end_value": end.value,
        }

    base = {"start_at": start.at, "end_at": end.at, "start_value": start.value, "end_value": end.value}

    delta: Decimal = (end.value - start.value).quantize(Q3)

    if delta < 0:
        drop_ratio = abs(delta) / start.value if start.value else Decimal(0)
        guess = "疑似换表" if drop_ratio >= RESET_RATIO else "疑似表归零或回退"
        return {
            **base,
            "usage": None,
            "quality": HourlyUsage.Q_RESET,
            "note": f"累计值回退 {start.value}→{end.value}（{guess}，差值 {delta}），已丢弃",
        }

    if meter.max_hourly is not None and delta > Decimal(meter.max_hourly):
        return {
            **base,
            "usage": delta,
            "quality": HourlyUsage.Q_SPIKE,
            "note": f"差值 {delta} 超过上限 {meter.max_hourly}，判为异常尖峰，不计入合计",
        }

    # 端点偏离整点：采集中断过，这个「小时」实际覆盖了更久
    end_drift = abs(end_limit - end.at)
    start_drift = abs(hour_start - start.at)
    if end_drift > BOUNDARY_TOLERANCE or start_drift > BOUNDARY_TOLERANCE:
        return {
            **base,
            "usage": delta,
            "quality": HourlyUsage.Q_PARTIAL,
            "note": f"端点偏离整点（起点差 {int(start_drift.total_seconds() // 60)} 分、"
                    f"终点差 {int(end_drift.total_seconds() // 60)} 分），差值仅供参考",
        }

    return {**base, "usage": delta, "quality": HourlyUsage.Q_OK, "note": ""}


@transaction.atomic
def aggregate_hour(day: date_cls, hour: int, meters=None) -> int:
    """把某小时的结果写入 HourlyUsage（可重复执行，幂等）。返回写入条数。"""
    meters = meters if meters is not None else Meter.objects.filter(enabled=True)
    written = 0
    for meter in meters:
        res = compute_hour(meter, day, hour)
        HourlyUsage.objects.update_or_create(
            meter=meter,
            date=day,
            hour=hour,
            defaults={
                "usage": res.get("usage"),
                "unit": meter.unit or "kWh",
                "quality": res["quality"],
                "start_at": res.get("start_at"),
                "end_at": res.get("end_at"),
                "start_value": res.get("start_value"),
                "end_value": res.get("end_value"),
                "note": res.get("note", ""),
            },
        )
        written += 1
    return written


def aggregate_recent(hours: int = 3, now: datetime | None = None, meters=None) -> int:
    """重算最近 N 个**已结束**的小时。

    常态化用法：每小时整点后跑一次 `--recent 2`，既覆盖刚结束的小时，
    也顺手修掉上一小时因为采样晚到而算出的 partial 值。
    """
    now = now or datetime.now()
    total = 0
    for back in range(1, hours + 1):
        moment = now - timedelta(hours=back)
        total += aggregate_hour(moment.date(), moment.hour, meters=meters)
    return total


def aggregate_day(day: date_cls, hours: range | None = None, meters=None) -> int:
    """整天重算（补历史 / 换算法后重跑用）。"""
    hours = hours or range(24)
    return sum(aggregate_hour(day, h, meters=meters) for h in hours)


def current_hour_usage(meter: Meter, now: datetime | None = None) -> dict | None:
    """进行中的小时：用「最新读数 − 参考起点」给一个参考值。

    参考起点的取法（顺序很重要）：
      1. 本小时开始前最后一次读数 —— 正常情况，此时 progress = 本小时已过时间占比；
      2. 本小时内第一次读数 —— 刚部署 / 采集中断后的头一个小时没有「上小时末读数」，
         就用本小时的第一条读数当基线，此时 progress = 该基线覆盖到现在的占比。
         不这么兜底的话，系统刚上线的那一小时什么都看不到，现场会以为采集没通。

    只用于展示（前端画虚线的那个柱），**不写库** —— 小时还没结束，
    反复写会让历史值来回跳。
    """
    now = now or datetime.now()
    hour_start = datetime.combine(now.date(), time(now.hour))

    latest = (
        Reading.objects.filter(meter=meter, ok=True, value__isnull=False, at__gte=hour_start)
        .order_by("-at")
        .first()
    )
    if latest is None:
        return None

    start = _last_reading_before(meter, hour_start)
    if start is not None:
        progress = (now - hour_start).total_seconds() / 3600
    else:
        # 兜底：用本小时第一条读数作基线
        start = (
            Reading.objects.filter(meter=meter, ok=True, value__isnull=False, at__gte=hour_start)
            .order_by("at")
            .first()
        )
        if start is None or start.id == latest.id:
            return None
        progress = (now - start.at).total_seconds() / 3600

    delta = (latest.value - start.value).quantize(Q3)
    if delta < 0:  # 换表 / 归零
        return {"usage": None, "progress": progress, "reset": True}
    return {"usage": delta, "progress": max(0.0, min(progress, 1.0)), "reset": False}
