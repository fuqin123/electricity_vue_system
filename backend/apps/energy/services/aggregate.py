"""小时差值聚合 —— 把累计值读数变成「每小时用了多少」。

这是整套采集里最容易出错、也最影响数字可信度的一环，口径写死在这里，别在别处再算一遍。

## 差值口径

对某小时 H（如 14:00—15:00）：

    usage(H) = end(H) − end(H−1)
    end(H)   = 「时间 ≤ H:00:00 + 1h」的最后一条有效读数（即距 15:00 最近的一次采样）

端点取法（v1.2.2 起）：**距整点最近的、且在 ±20 分钟容差内的那条读数**。
容差是必须的 —— 没有它，本小时没采到样时会拿隔天的读数来相减并得到 0（见 B-503）。

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
| 端点离整点 > 容差 | **判缺数（usage = null）** | 采集中断过，这个「小时」实际跨了更长时间 |
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
from . import slots

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
    """取「采样时间 ≤ upper」的最后一条有效读数（仅作兜底参考，差值计算不用它）。"""
    return (
        Reading.objects.filter(meter=meter, ok=True, value__isnull=False, at__lte=upper)
        .order_by("-at")
        .first()
    )


def boundary_reading(meter: Meter, boundary: datetime) -> Reading | None:
    """取「整点 boundary」的读数 = **距该整点最近的一条有效读数**（容差 ±20 分钟）。

    三条规则各自换来的东西（都是踩过坑才定下来的，别改）：

    ① **用「最近」而不是「该整点之前的最后一条」。**
       同一个采样槽可能因为失败重试出现多条读数（`collect --if-due` 就是
       每 5 分钟重试直到本小时采完）。取最近的一条时，槽内第一条永远胜出；
       若取「之后最后一条」，一次 10:30 的补采会把 09:01→10:30 这 89 分钟
       当成「09 点这一小时」：09 点多算、10 点少算，两条区间又都会因为
       偏离 1 小时被判 partial 而不计入合计 —— 现场只看到「这几小时没数据」。

    ② **必须有容差下限（这是 v1.2.2 修掉的最严重的一个问题）。**
       老实现只写了 `at <= boundary` 这一个上界，于是当本小时**根本没采到样**时，
       它会一路往前找到几小时前、甚至隔天的读数来相减。
       两条陈旧读数相减通常正好得 0，而且不触发任何异常分支、只被标成 partial ——
       界面上一看就是「这一小时一点电都没用」。
       数字量级正常、没有报错、没有告警，这是最难发现也最危险的一类错。
       现在超出容差直接判**缺数（usage=None）**，界面显示 `--`：
       宁可让人看见「这里没数」，也不要给一个来源不明的数。

    ③ 容差同时覆盖两种采样节奏：
       密采样时最近的是整点前最后一条（差几十秒）；
       整点采样时最近的是采样槽内第一条（差几分钟）。两者都在容差内。
    """
    lo = boundary - BOUNDARY_TOLERANCE
    hi = boundary + BOUNDARY_TOLERANCE
    rows = Reading.objects.filter(
        meter=meter, ok=True, value__isnull=False, at__gte=lo, at__lte=hi
    ).only("id", "at", "value")

    best: Reading | None = None
    best_gap = None
    for r in rows:
        gap = abs((r.at - boundary).total_seconds())
        if best_gap is None or gap < best_gap:
            best, best_gap = r, gap
    return best


#: 区间长度与 1 小时的允许偏差，超过就标 partial（读数虽有，但这一小时不干净）
SPAN_TOLERANCE = timedelta(minutes=5)

#: 端点相对**采样时刻**的允许偏差。
#: `ok` 的含义是「这一小时的端点按期采到了」，而不只是「有个数能减出约 1 小时」。
#:
#: 为什么需要它（v1.2.2 补的一个口子）：
#:   只用「最近 + ±20 分钟容差 + 区间长约 1 小时」三条，仍会放过一种情况 ——
#:   两个端点都偏离采样时刻同样的方向、同样的幅度。
#:   例如某天的读数落在 14:49:37 与 15:50:23，区间 60.8 分钟，看起来一切正常，
#:   但这两个点其实各自偏了 11 分钟，这一小时的「窗口」整体平移了 10 分钟。
#:   数值不是错的（没有重复计、也没有陈旧值），但它不该被标成「干净」。
#:   这里按「相对采样时刻的偏差」判定，与 `collect.hourly_offset_seconds` 无关 ——
#:   偏移改成 600 秒也不会让所有小时都变 partial。
PHASE_TOLERANCE = timedelta(minutes=10)


def compute_hour(meter: Meter, day: date_cls, hour: int) -> dict:
    """算一个计量点在某小时的用量（不落库，纯计算，便于测试与复查）。"""
    hour_start, end_limit = hour_window(day, hour)

    # 起点 = 本小时整点的边界读数；终点 = 下个整点的边界读数
    start = boundary_reading(meter, hour_start)
    end = boundary_reading(meter, end_limit)

    if end is None:
        return {
            "usage": None,
            "quality": HourlyUsage.Q_MISSING,
            "note": f"{end_limit:%H:%M} 的采样槽内无有效读数",
        }

    if start is None:
        return {
            "usage": None,
            "quality": HourlyUsage.Q_MISSING,
            "note": f"{hour_start:%H:%M} 的采样槽内无有效读数（无法确定起点）",
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

    # 区间长度必须接近 1 小时：端点各自允许 ±20 分钟偏差，
    # 两者叠加最坏可到 ±40 分钟。偏差在 5 分钟以内认为这一小时是干净的；
    # 再大说明端点被挪过位置（例如补采落在很晚的位置），标 partial 但仍然给数。
    span = (end.at - start.at).total_seconds()
    if abs(span - 3600) > SPAN_TOLERANCE.total_seconds():
        return {
            **base,
            "usage": delta,
            "quality": HourlyUsage.Q_PARTIAL,
            "note": f"区间长度 {span / 60:.0f} 分钟（起 {start.at:%H:%M:%S} → 止 {end.at:%H:%M:%S}），"
                    f"偏离 1 小时，差值仅供参考",
        }

    # 相位判定：两个端点是否都按期采到了（见 PHASE_TOLERANCE 的说明）
    start_lag = abs((start.at - slots.slot_ready_at(hour_start)).total_seconds())
    end_lag = abs((end.at - slots.slot_ready_at(end_limit)).total_seconds())
    if max(start_lag, end_lag) > PHASE_TOLERANCE.total_seconds():
        return {
            **base,
            "usage": delta,
            "quality": HourlyUsage.Q_PARTIAL,
            "note": f"端点未按期采到（起点偏 {start_lag / 60:.0f} 分、终点偏 {end_lag / 60:.0f} 分），"
                    f"本小时窗口有平移，差值仅供参考",
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
    """进行中的小时：给一个**参考值**。只用于展示，**不写库**。

    ⚠ v1.2.2 修正：必须「本小时内有两条以上读数」才算得出参考值。

    老实现是「上小时末读数 → 本小时最新读数」，在密集采样下没问题；
    但采集改成**每小时一次**之后，本小时的第一条读数就是整点边界读数 ——
    它已经代表上一个小时结束了，减出来的其实是**上一个小时的完整用量**。
    如果照旧把它当成「本小时已用量」显示，界面上的数会整整错一个小时，
    而且因为量级正常，几乎没人能一眼看出来。

    采样不足两条时返回 usage=None，界面显示「待整点采集」——
    这比给一个错一小时的数要诚实得多。
    """
    now = now or datetime.now()
    hour_start = datetime.combine(now.date(), time(now.hour))

    in_hour = list(
        Reading.objects.filter(meter=meter, ok=True, value__isnull=False, at__gte=hour_start).order_by("at")
    )
    if len(in_hour) < 2:
        # 0 条：整点后还没采到样；1 条：只有边界基线，还不够做差
        return None

    first, latest = in_hour[0], in_hour[-1]
    progress = (latest.at - first.at).total_seconds() / 3600
    delta = (latest.value - first.value).quantize(Q3)
    if delta < 0:  # 换表 / 归零
        return {"usage": None, "progress": max(0.0, min(progress, 1.0)), "reset": True}
    return {"usage": delta, "progress": max(0.0, min(progress, 1.0)), "reset": False}


def explain_hour(meter: Meter, day: date_cls, hour: int) -> dict:
    """把一个小时的差值算给现场看：起点读数、终点读数、时间、差值、判定质量。

    为什么需要它：现场核对「数字对不对」时，光看 hourly_usage 里的一个数没法判断，
    必须能看到用的是哪两条读数、它们各自在什么时刻
    （`manage.py aggregate --verify` 就是打印这些）。
    """
    hour_start, end_limit = hour_window(day, hour)
    start = boundary_reading(meter, hour_start)
    end = boundary_reading(meter, end_limit)
    res = compute_hour(meter, day, hour)

    def fmt(reading):
        if reading is None:
            return "—"
        return f"{reading.at:%m-%d %H:%M:%S}={reading.value}"

    span = "—"
    if start is not None and end is not None:
        span = f"{(end.at - start.at).total_seconds() / 60:.1f} 分钟"

    return {
        "meter": meter.code,
        "name": meter.name,
        "date": day.isoformat(),
        "hour": hour,
        "start": fmt(start),
        "end": fmt(end),
        "spanMinutes": span,
        "usage": res.get("usage"),
        "quality": res["quality"],
        "note": res.get("note", ""),
    }
