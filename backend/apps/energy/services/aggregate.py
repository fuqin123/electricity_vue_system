"""小时差值聚合 —— 把累计值读数变成「每小时用了多少」。

这是整套采集里最容易出错、也最影响数字可信度的一环，口径写死在这里，别在别处再算一遍。

## 差值口径

对某小时 H（如 14:00—15:00）：

    usage(H) = read(15:00) − read(14:00)
    read(B)  = `slot_boundary = B` 的采样槽里、离预定采样时刻最近的一条有效读数

端点取法（v1.7.3 起）：**优先按 `EnergyRaw.slot_boundary` 精确认领**，同一槽内取
离预定采样时刻最近的一条；没有槽标记的旧数据才退回「距整点最近且在容差内」。
容差默认 20 分钟，后台「系统参数 → 数据质量 → 异常时段允许偏差」可调整端点/区间
时长的判定阈值。容差是必须的 —— 没有它，本小时没采到样时会拿隔天的读数来相减
并得到 0（见 B-503）。

⚠ 不能只按「距整点最近」取端点：现场每 5 分钟补采失败点位时，上一槽的最后一条
  补采（如 14:55）与本槽第一条（15:05）到 15:00 的距离几乎相等，谁胜出取决于
  秒级抖动。串槽后 14 点会被算成 55 分钟、15 点被算成 65 分钟。按槽归属后，
  14:55 属于 14:00 槽，绝不会污染 15:00 的端点。

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

from ..models import EnergyHourly, EnergyMeter, EnergyRaw
from . import data_quality as DQ
from . import slots

logger = logging.getLogger(__name__)

#: 端点读数搜索窗口的基础值；实际取 max(本值, 后台配置的异常时段偏差)
BOUNDARY_TOLERANCE = timedelta(minutes=int(conf.get("collect.boundary_tolerance_minutes", 20)))


def _boundary_tolerance() -> timedelta:
    return timedelta(minutes=max(BOUNDARY_TOLERANCE.total_seconds() / 60, DQ.deviation_minutes()))
#: 累计值回退超过该比例时，在说明里记为「疑似换表」
RESET_RATIO = Decimal(str(conf.get("collect.filter.reset_threshold_ratio", 0.5)))
#: 小数位（库字段是 3 位）
Q3 = Decimal("0.001")


def hour_window(day: date_cls, hour: int) -> tuple[datetime, datetime]:
    """返回某小时的 [起点, 终点] 边界时间。终点 = 下一小时整点（含）。"""
    start = datetime.combine(day, time(hour))
    return start, start + timedelta(hours=1)


def boundary_reading(meter: EnergyMeter, boundary: datetime) -> EnergyRaw | None:
    """取「整点 boundary」的读数。

    优先查 `slot_boundary == boundary` 的读数，并在槽内取**离预定采样时刻最近**
    的一条。槽归属由采集时写入，能区分「上一槽的迟到补采」与「本槽的第一条采样」——
    两者到整点的距离可能只差几秒，只按时间就近必然偶尔串槽（B-555）。

    没有槽标记的是 v1.7.3 之前的旧数据，按老规则回退：
    **距整点最近、且落在 ±容差内的那条读数**。三条老规则仍然有效：

    ① **用「最近」而不是「该整点之前的最后一条」**：槽内补采不会把区间拉长；
    ② **必须有容差下限**：本小时没采到样时，绝不能拿几小时前甚至隔天的读数
       相减（两条陈旧读数常常正好得 0，界面显示「这一小时一点电都没用」）；
    ③ 容差同时覆盖密采样（整点前几十秒）与整点采样（槽内几分钟）两种节奏。

    新槽数据不再套用容差上下限：一条读数属于哪个槽由采集时决定，即使迟到
    也仍然属于该槽；它离预定时刻多远，会由 `compute_hour` 判成 partial 并进入
    人工复核，而不是被静默丢弃。
    """
    slot_rows = list(
        EnergyRaw.objects.filter(
            meter=meter, ok=True, reading__isnull=False, slot_boundary=boundary
        )
        .order_by("collect_time")
        .only("id", "collect_time", "reading")
    )
    if slot_rows:
        target = slots.slot_ready_at(boundary)
        return min(slot_rows, key=lambda r: abs((r.collect_time - target).total_seconds()))

    tolerance = _boundary_tolerance()
    lo = boundary - tolerance
    hi = boundary + tolerance
    rows = EnergyRaw.objects.filter(
        meter=meter,
        ok=True,
        reading__isnull=False,
        slot_boundary__isnull=True,
        collect_time__gte=lo,
        collect_time__lte=hi,
    ).only("id", "collect_time", "reading")

    best: EnergyRaw | None = None
    best_gap = None
    for r in rows:
        gap = abs((r.collect_time - boundary).total_seconds())
        if best_gap is None or gap < best_gap:
            best, best_gap = r, gap
    return best


def compute_hour(meter: EnergyMeter, day: date_cls, hour: int) -> dict:
    """算一个计量点在某小时的用量（不落库，纯计算，便于测试与复查）。"""
    hour_start, end_limit = hour_window(day, hour)

    # 起点 = 本小时整点的边界读数；终点 = 下个整点的边界读数
    start = boundary_reading(meter, hour_start)
    end = boundary_reading(meter, end_limit)

    if end is None:
        return {
            "usage": None,
            "quality": EnergyHourly.Q_MISSING,
            "note": f"{end_limit:%H:%M} 的采样槽内无有效读数",
        }

    if start is None:
        return {
            "usage": None,
            "quality": EnergyHourly.Q_MISSING,
            "note": f"{hour_start:%H:%M} 的采样槽内无有效读数（无法确定起点）",
            "end_at": end.collect_time,
            "end_reading": end.reading,
        }

    base = {"start_at": start.collect_time, "end_at": end.collect_time, "start_reading": start.reading, "end_reading": end.reading}

    delta: Decimal = (end.reading - start.reading).quantize(Q3)

    if delta < 0:
        drop_ratio = abs(delta) / start.reading if start.reading else Decimal(0)
        guess = "疑似换表" if drop_ratio >= RESET_RATIO else "疑似表归零或回退"
        return {
            **base,
            "usage": None,
            "quality": EnergyHourly.Q_RESET,
            "note": f"累计值回退 {start.reading}→{end.reading}（{guess}，差值 {delta}），已丢弃",
        }

    if meter.max_hourly is not None and delta > Decimal(meter.max_hourly):
        return {
            **base,
            "usage": delta,
            "quality": EnergyHourly.Q_SPIKE,
            "note": f"差值 {delta} 超过上限 {meter.max_hourly}，判为异常尖峰，不计入合计",
        }

    # 区间长度必须接近 1 小时：端点各自允许 ±20 分钟偏差，
    # 两者叠加最坏可到 ±40 分钟。偏差在 5 分钟以内认为这一小时是干净的；
    # 再大说明端点被挪过位置（例如补采落在很晚的位置），标 partial 但仍然给数。
    tolerance = timedelta(minutes=DQ.deviation_minutes())
    span = (end.collect_time - start.collect_time).total_seconds()
    if abs(span - 3600) > tolerance.total_seconds():
        return {
            **base,
            "usage": delta,
            "quality": EnergyHourly.Q_PARTIAL,
            "note": f"区间长度 {span / 60:.0f} 分钟（起 {start.collect_time:%H:%M:%S} → 止 {end.collect_time:%H:%M:%S}），"
                    f"偏离 1 小时，差值仅供参考",
        }

    # 相位判定：两个端点是否都落在可配置的偏差范围内
    start_lag = abs((start.collect_time - slots.slot_ready_at(hour_start)).total_seconds())
    end_lag = abs((end.collect_time - slots.slot_ready_at(end_limit)).total_seconds())
    if max(start_lag, end_lag) > tolerance.total_seconds():
        return {
            **base,
            "usage": delta,
            "quality": EnergyHourly.Q_PARTIAL,
            "note": f"端点未按期采到（起点偏 {start_lag / 60:.0f} 分、终点偏 {end_lag / 60:.0f} 分），"
                    f"本小时窗口有平移，差值仅供参考",
        }

    return {**base, "usage": delta, "quality": EnergyHourly.Q_OK, "note": ""}


@transaction.atomic
def aggregate_hour(day: date_cls, hour: int, meters=None) -> int:
    """把某小时的结果写入 EnergyHourly（可重复执行，幂等）。返回写入条数。"""
    meters = meters if meters is not None else EnergyMeter.objects.filter(enabled=True)
    written = 0
    for meter in meters:
        res = compute_hour(meter, day, hour)
        EnergyHourly.objects.update_or_create(
            meter=meter,
            stat_date=day,
            stat_hour=hour,
            defaults={
                # 产线与能源类型在入库时「快照」一份：历史归属不随产线调整而变，
                # 汇总时也能直接按 (line, energy_type) 分组，不用连表。
                "line": meter.line,
                "energy_type": meter.energy_type,
                "consumption": res.get("usage"),
                "unit": meter.unit or "kWh",
                "quality": res["quality"],
                "start_at": res.get("start_at"),
                "end_at": res.get("end_at"),
                "start_reading": res.get("start_reading"),
                "end_reading": res.get("end_reading"),
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


def current_hour_usage(meter: EnergyMeter, now: datetime | None = None) -> dict | None:
    """进行中的小时：给一个**参考值**。只用于展示，**不写库**。

    ⚠ v1.2.2 修正：必须「本小时内有两条以上读数」才算得出参考值。

    老实现是「上小时末读数 → 本小时最新读数」，在密集采样下没问题；
    但采集改成**每小时一次**之后，本小时的第一条读数就是整点边界读数 ——
    它已经代表上一个小时结束了，减出来的其实是**上一个小时的完整用量**。
    如果照旧把它当成「本小时已用量」显示，界面上的数会整整错一个小时，
    而且因为量级正常，几乎没人能一眼看出来。

    采样不足两条时返回 usage=None，界面显示「待整点采集」——
    这比给一个错一小时的数要诚实得多。

    ⚠ v1.4.1 修正（B-522）：窗口**必须有上界**。
    原来只写 `collect_time__gte=hour_start`，在实时用法下相安无事（未来还没有读数），
    可一旦 `now` 是** fabricated 的观测时刻**（大屏查历史日时 `_now_for()` 给的是
    那天 23:59:59），这个开区间就把「该小时之后直到今天最新」的全部读数都算进来：
    实测查 09-27 时，一线 23 点显示 85 kWh（真值 8.1）、三线显示 3285 kWh（真值 343.1）
    —— 相当于把 10 个小时的用量塞进一格，还把这一格标成「进行中」。
    数字大到没有人会怀疑它是补出来的，所以危害是把日累计和单耗一起带偏。
    """
    now = now or datetime.now()
    hour_start = datetime.combine(now.date(), time(now.hour))
    hour_end = hour_start + timedelta(hours=1)

    in_hour = list(
        EnergyRaw.objects.filter(
            meter=meter, ok=True, reading__isnull=False, collect_time__gte=hour_start, collect_time__lt=hour_end
        ).order_by("collect_time")
    )
    if len(in_hour) < 2:
        # 0 条：整点后还没采到样；1 条：只有边界基线，还不够做差
        return None

    first, latest = in_hour[0], in_hour[-1]
    progress = (latest.collect_time - first.collect_time).total_seconds() / 3600
    delta = (latest.reading - first.reading).quantize(Q3)
    if delta < 0:  # 换表 / 归零
        return {"usage": None, "progress": max(0.0, min(progress, 1.0)), "reset": True}
    return {"usage": delta, "progress": max(0.0, min(progress, 1.0)), "reset": False}


def explain_hour(meter: EnergyMeter, day: date_cls, hour: int) -> dict:
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
        return f"{reading.collect_time:%m-%d %H:%M:%S}={reading.reading}"

    span = "—"
    if start is not None and end is not None:
        span = f"{(end.collect_time - start.collect_time).total_seconds() / 60:.1f} 分钟"

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
