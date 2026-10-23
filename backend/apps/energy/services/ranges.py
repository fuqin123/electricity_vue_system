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

⚠ v1.5.2 修复（B-524 同类）：本模块此前用 `_round()`（默认 3 位小数）输出电量，
  而大屏走 `snapshot.round_kwh()`（**1 位小数**）。同一格 24.812 与 24.8 会让
  现场核对时先怀疑「系统数字对不上」。电量一律改走 `round_kwh`，
  本文件的 `_round` 只留给非电量（水/汽吨数等）。
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q, Sum

from ..models import (
    EnergyHourly,
    EnergyMeter,
    EnergyType,
    PeriodSummary,
    ProductionHourly,
    ProductionLine,
)
from . import business_day as B
from . import cost
from . import coverage as C
from . import summary as S
from .coverage import (
    Q_UNCONNECTED,
    expected_ids,
    first_data_map,
    split_by_kind,
    unit_of,
)
# 电量展示口径与大屏同源（1 位小数）：见 snapshot.round_kwh 的说明（B-524）
from .snapshot import round_kwh

logger = logging.getLogger(__name__)

DEMO_SOURCES = (ProductionHourly.SOURCE_DEMO, ProductionHourly.SOURCE_NONE)


def parse_range(request) -> tuple[date_cls, date_cls]:
    """从请求里取 from / to，缺省为「今天」。"""
    start, end = parse_hours(request)
    return start.date(), end.date()


#: 前端时间控件可能给出的几种写法（精确到小时是 v1.4.0 的新要求）
_HOUR_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H")


def parse_hour(raw: str | None, *, default: datetime) -> datetime:
    """把 `YYYY-MM-DD[ HH[:mm[:ss]]]` 解析成本地时间；纯日期取该日 00:00。

    ⚠ 不要用 `datetime.fromisoformat` 之外的时区猜测：本项目全部按本地时间处理，
    前端也统一用本地时间拼字符串（`toISOString` 会得到昨天，见 B-508 那条教训）。
    """
    if not raw:
        return default
    # 统一成空格分隔再解析：前端可能给 `2026-09-24T08:00`，也可能给 `2026-09-24 08:00`
    text = raw.strip().replace("T", " ")
    if len(text) == 10:  # 纯日期
        return datetime.strptime(text, "%Y-%m-%d")
    for fmt in _HOUR_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"时间格式无法解析：{raw}（期望 YYYY-MM-DD 或 YYYY-MM-DD HH）")


def parse_hours(
    request,
    *,
    default_start: datetime | None = None,
    default_end: datetime | None = None,
) -> tuple[datetime, datetime]:
    """取**精确到小时**的起止时点，缺省为「今天 00:00 → 现在」。

    与 `parse_range` 的区别只在精度：这里返回 datetime，调用方可以按小时裁桶。
    起止会被规范到整点（分秒归零），因为本项目的最小时间桶就是 1 小时。
    用量查询会显式传入「当前生产日 06:00」作为缺省起点；仪表读数等
    与生产日无关的查询仍沿用自然日缺省。
    """
    now = datetime.now()
    default_start = default_start or datetime.combine(now.date(), time.min)
    default_end = default_end or now.replace(minute=0, second=0, microsecond=0)
    try:
        start = parse_hour(request.GET.get("from"), default=default_start)
        end = parse_hour(request.GET.get("to"), default=default_end)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    start = start.replace(minute=0, second=0, microsecond=0)
    end = end.replace(minute=0, second=0, microsecond=0)
    if end < start:
        start, end = end, start
    # 上限保护：一次最多查 366 天，避免把库和内存拖垮
    if (end - start).days > 366:
        start = end - timedelta(days=366)
    return start, end


def _lines_scope(line_code: str | None) -> list[ProductionLine]:
    if line_code:
        ln = ProductionLine.objects.filter(line_code=line_code.upper()).first()
        return [ln] if ln else []
    return list(ProductionLine.active_lines_qs().order_by("sort", "line_code"))


def _line_meters(line: ProductionLine) -> list[EnergyMeter]:
    """挂在某节点上的电表（节点可以是产线，也可以是辅助单元）。

    v1.5.2：原来叫 `_line_meters` 但只按 `line` 过滤能源为电 —— 语义没变，
    调用方现在也会传 aux 节点进来（辅机 / 瓶胚 / 调配），因为车间电力合计
    要把辅机电表算进去。
    """
    return [
        m
        for m in line.meters.all()
        if m.energy_type.type_code == EnergyType.ELECTRIC and m.enabled
    ]


def meters_of(
    *,
    kind: str | None = None,
    workshop: str | None = None,
    usage_type: str | None = None,
    device: str | None = None,
    node_code: str | None = None,
) -> list[EnergyMeter]:
    """按「车间 → 用能类型 → 设备」三级维度取计量点。

    这是后台各级筛选的**唯一取数入口**：三级下拉选出来的东西最终都归到这里，
    避免前端把不同维度的过滤散在各处拼 SQL（那样两处口径迟早不一致）。

      workshop   车间编码（WS-CARB）—— 含该车间下的产线与辅助单元
      usage_type 用能类型（生产线用电 / 辅机用电 …）
      device     设备名，与 `node_code` 一起构成第三级
      node_code  组织节点编码（产线 W103 / 辅助单元 AUX-CARB-AIR / 车间 WS-CARB）
      kind       能源短码或编码；不传则不限

    ⚠ 任何一个维度都不传时就是不限（而不是「匹配空串」）—— 否则界面清空下拉
      会得到空结果，看起来像「没有数据」。
    """
    qs = EnergyMeter.objects.select_related("line", "line__parent", "energy_type")
    if kind:
        qs = qs.filter(energy_type__type_code=_type_code(kind))
    if node_code:
        qs = qs.filter(line__line_code=node_code)
    elif workshop:
        # 车间维度：匹配「挂在本车间下的节点」或「车间节点自己」（总表）
        qs = qs.filter(
            Q(line__line_code=workshop)
            | Q(line__parent__line_code=workshop)
        )
    if usage_type:
        qs = qs.filter(usage_type=usage_type)
    if device:
        qs = qs.filter(device_name=device)
    return list(qs.filter(enabled=True).order_by("code"))


def _type_code(kind: str) -> str:
    """能源短码 → 库内编码（elec → ELECTRIC）。"""
    return {"elec": "ELECTRIC", "water": "WATER", "steam": "STEAM"}.get(
        str(kind).lower(), str(kind).upper()
    )


def _utility_hour_series_for(
    kind: str, meters: list[EnergyMeter], buckets, start: date_cls, end: date_cls
) -> list[float | None]:
    """给定一组计量点的小时序列（水/汽用）。"""
    if not meters:
        return [None for _ in buckets]
    mat = _hour_matrix(meters, buckets)
    return [_round(mat[b][0]) for b in buckets]


def _workshop_meters(workshop: str | None = None) -> list[EnergyMeter]:
    """车间电力合计口径下的全部电表 = 产线电表 + 辅助单元电表。

    ⚠ v1.5.2 口径变更：旧实现返回**全部电表**（当时只有 13 块产线表，等价）；
      现在表里有辅机 / 瓶胚 / 调配电表，若还按「全部」算，车间合计里的
      辅机部分就变成了「碰巧包含」而不是显式约定。这里显式排除车间总表本身
      （车间没有电力总表），把口径写死。

    ⚠ 本次补上 `workshop` 过滤：后台电计量页会带车间参数走常规通道，
      此前该参数被静默忽略 —— 界面上写着「碳水车间」，算的却是全厂数字。
      只有一个车间时两者恰好相等，所以一直没暴露；第二个车间一接入就会错。
    """
    qs = EnergyMeter.objects.filter(
        energy_type__type_code=EnergyType.ELECTRIC,
        enabled=True,
        line__node_type__in=(ProductionLine.NODE_LINE, ProductionLine.NODE_AUX),
    )
    if workshop:
        code = workshop.upper()
        qs = qs.filter(
            Q(line__line_code=code) | Q(line__parent__line_code=code)
        )
    return list(qs)


# ══════════════════════════════════════════════════════════════
#  小时粒度
# ══════════════════════════════════════════════════════════════


def _hour_labels(
    start: date_cls,
    end: date_cls,
    now: datetime,
    *,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    closed_only: bool = False,
) -> list[tuple[date_cls, int]]:
    """生成 [起点, 终点] 之间的全部（日期, 小时）桶，末日截止到当前小时。

    `start_at` / `end_at` 给定时按**整点闭区间**裁剪（v1.4.0 的「年月日时 → 年月日时」筛选），
    不给时沿用旧的「整日」行为，保证大屏与既有调用方口径不变。

    `closed_only=True` 时**排除正在走的这一个小时**（`hour < now.hour`）。

    ⚠ 为什么需要它：本表的小时桶语义是「H 这一小时用了多少」，而
      `usage(H) = read(H+1:00) − read(H:00)`，要等下一个整点采到端点读数才成立。
      而 `energy_hourly` 也只写已结束的小时（聚合在整点后跑）。
      所以把「现在」所在的这个小时放进桶里，得到的一定是空值 —— 它会在用量查询页
      表现成「共 N 个时段，其中 1 个时段缺数」，让人以为采集漏了一个小时（v1.4.4 修复）。
      15 点时最大可查的时段是 14 点。
    """
    out: list[tuple[date_cls, int]] = []
    d = start
    while d <= end:
        if d > now.date():
            break
        last_hour = now.hour if d == now.date() else 23
        if d == now.date() and now.hour == 0:
            last_hour = 0
        if closed_only and d == now.date():
            last_hour = now.hour - 1  # 当前小时尚未结算 → 不生成桶
        if end_at is not None and d == end_at.date():
            last_hour = min(last_hour, end_at.hour)
        first_hour = 0
        if start_at is not None and d == start_at.date():
            first_hour = start_at.hour
        for h in range(first_hour, last_hour + 1):
            out.append((d, h))
        d += timedelta(days=1)
    return out


def _hour_matrix(
    meters: list[EnergyMeter],
    buckets: list[tuple[date_cls, int]],
    *,
    first_ok: dict[int, datetime] | None = None,
):
    """返回 {bucket: (合计|None, quality)}。

    口径：**参与这一格的每一块表都有可信值，这一格才算数（给 None）**，
    而不是「有几块算几块」的偏小值。

    ⚠ 「参与这一格的表」不等于「组里登记的全部表」，见 coverage 模块的说明：
      · 从未出过可信值的表（登记了但没接线 / 没给 token）没有否决权 ——
        否则一块水表会把同组的电和汽一起抹成 `--`，且该类永久无数据；
        它们改由告警引擎如实报「未接入」。
      · 中途才接入的表只从自己首次出数的那一格起参与 —— 否则新装一块表
        会让上周的图表整体倒退成空值。
      · 接入之后某小时真的缺数，仍然整格 None（这是故障，不能悄悄少加一块表）。
    """
    if not buckets:
        return {}
    if first_ok is None:
        first_ok = first_data_map(meters)
    connected = {mid for mid in first_ok}
    if not connected:
        # 整组一块都没接入过：不是「缺数」，是「还没接」
        return {b: (None, Q_UNCONNECTED) for b in buckets}

    first_dt = datetime.combine(buckets[0][0], time(buckets[0][1]))
    last_dt = datetime.combine(buckets[-1][0], time(buckets[-1][1])) + timedelta(hours=1)
    rows = EnergyHourly.objects.filter(
        B.hour_q(first_dt, last_dt), meter_id__in=connected
    ).values("stat_date", "stat_hour", "consumption", "quality", "meter_id")

    grouped: dict[tuple[date_cls, int], list[dict]] = {}
    for r in rows:
        grouped.setdefault((r["stat_date"], r["stat_hour"]), []).append(r)

    out: dict[tuple[date_cls, int], tuple[Decimal | None, str]] = {}
    for b in buckets:
        exp = expected_ids(first_ok, meters, b)
        if not exp:
            # 这一格比组里任何表的接入时刻都早（区间前段还没装表）
            out[b] = (None, Q_UNCONNECTED)
            continue
        got = [r for r in grouped.get(b, []) if r["meter_id"] in exp]
        if len(got) < len(exp):
            out[b] = (None, EnergyHourly.Q_MISSING)
            continue
        bad = [r for r in got if r["quality"] in (EnergyHourly.Q_MISSING, EnergyHourly.Q_RESET)]
        if bad:
            out[b] = (None, bad[0]["quality"])
            continue
        usable = [r for r in got if r["quality"] in EnergyHourly.TRUSTED]
        if not usable:
            # 同 snapshot.py：一块表都没给出可信值时，合计是 None 而不是 0
            out[b] = (None, EnergyHourly.Q_PARTIAL)
            continue
        total = sum((r["consumption"] or Decimal(0) for r in usable), Decimal(0))
        quality = EnergyHourly.Q_OK if len(usable) == len(exp) else EnergyHourly.Q_PARTIAL
        out[b] = (total, quality)
    return out


# ══════════════════════════════════════════════════════════════
#  日 / 月粒度（读 period_summary）
# ══════════════════════════════════════════════════════════════


def _summary_series(dim: str, scope: str, line: ProductionLine | None, keys: list[str]):
    """日/月/年序列 —— 走 summary.period_rows（它把按能源类型分行的表拼回一行）。"""
    return S.period_rows(dim, scope, line, keys)


def _period_keys(dim: str, start: date_cls, end: date_cls, now: datetime) -> list[tuple[str, str]]:
    """返回 [(period_key, label)]。日 / 月键都按**生产日**，进行中的周期只到「现在」。"""
    out: list[tuple[str, str]] = []
    current = B.production_day(now)
    if dim == PeriodSummary.DIM_DAY:
        d = start
        while d <= end:
            if d > current:
                break
            out.append((d.strftime("%Y-%m-%d"), d.strftime("%m-%d")))
            d += timedelta(days=1)
        return out

    # 月：从起始月枚举到当前生产日所在月
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month) and (y, m) <= (current.year, current.month):
        out.append((f"{y}-{m:02d}", f"{y}-{m:02d}"))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _query_window(
    granularity: str,
    start: date_cls,
    end: date_cls,
    *,
    klist: list[str] | None = None,
    hour_buckets: list[tuple[date_cls, int]] | None = None,
) -> tuple[datetime, datetime]:
    """本次查询对应的生产日实际窗口（[start, end)）。"""
    if hour_buckets:
        first = datetime.combine(hour_buckets[0][0], time(hour_buckets[0][1]))
        last = datetime.combine(hour_buckets[-1][0], time(hour_buckets[-1][1])) + timedelta(hours=1)
        return first, last
    if granularity == "month" and klist:
        return B.period_window("month", klist[0])[0], B.period_window("month", klist[-1])[1]
    return B.day_start(start), B.day_end(end)


def _range_total_kwh(
    meters: list[EnergyMeter],
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    buckets: list[tuple[date_cls, int]] | None = None,
    first_ok: dict[int, datetime] | None = None,
) -> Decimal | None:
    """严格口径的区间电量合计 —— 与图表序列**同一条规则**。

    ⚠ 不能用 `Sum(consumption)`：那是「有几块算几块」的偏小值，
      而图表序列用的是 `_hour_matrix` 的「全部可信才算数」口径。
      两者并存时会出现「图是空的、合计却有 16000 kWh」——
      用户只能得出「系统算错了」的结论（这也是 B-524 同族的假差异）。

    做法：逐小时套用 `_hour_matrix`（每小时都得全表可信），
    任一小时不成立就整体返回 None；成立的小时相加。

    ⚠ **桶必须由调用方传入**（`buckets=`）才能保证与图表逐格对齐。
      自己调 `_hour_labels` 重建一遍必然对不齐：图表带 `closed_only=True`
      和 `start_at/end_at` 裁剪，而这里的默认重建会把「终点那天」当今天
      而多带一个小时，于是合计又比序列多出一格（这正是修这个函数时要防的坑）。
    """
    if not meters:
        return None
    if buckets is None:
        if start is None or end is None:
            return None
        buckets = B.window_hours(start, end)
    if not buckets:
        return None
    mat = _hour_matrix(meters, buckets, first_ok=first_ok)
    total = Decimal(0)
    for b in buckets:
        val, _q = mat[b]
        if val is None:
            # 有一个小时不成立 → 合计不成立（不返回偏小值）
            return None
        total += val
    return total


def _range_output(lines: list[ProductionLine], start: datetime, end: datetime) -> Decimal:
    total = (
        ProductionHourly.objects.filter(
            B.hour_q(start, end),
            line__in=lines,
            quality__in=ProductionHourly.TRUSTED,
        )
        .exclude(source__in=DEMO_SOURCES)
        .aggregate(t=Sum("output"))["t"]
    )
    return Decimal(total) if total is not None else Decimal(0)


def _utility_series(
    kind: str, dim: str, start: date_cls, end: date_cls, now: datetime, keys: list[tuple[str, str]]
) -> list[float | None]:
    """车间水 / 汽：只有总表，不拆分到线别。"""
    meters = list(EnergyMeter.objects.filter(energy_type__type_code=kind, enabled=True))
    if not meters:
        return [None for _ in keys]

    if dim == PeriodSummary.DIM_DAY:
        rows = _summary_series(PeriodSummary.DIM_DAY, PeriodSummary.SCOPE_WORKSHOP, None, [k for k, _ in keys])
        field = "water_t" if kind == EnergyType.WATER else "steam_t"
        out: list[float | None] = []
        for k, _lab in keys:
            row = rows.get(k)
            val = getattr(row, field) if row is not None else None
            out.append(float(val) if val is not None else None)
        return out

    out: list[float | None] = []
    for key, _label in keys:
        start, end = B.period_window(dim, key)
        row = _summary_series(dim, PeriodSummary.SCOPE_WORKSHOP, None, [key]).get(key)
        if row is not None:
            val = row.water_t if kind == EnergyType.WATER else row.steam_t
            out.append(float(val) if val is not None else None)
            continue
        agg = EnergyHourly.objects.filter(
            B.hour_q(start, end), meter__in=meters, quality__in=EnergyHourly.TRUSTED
        ).aggregate(t=Sum("consumption"))
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
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    kind: str | None = None,
    workshop: str | None = None,
    usage_type: str | None = None,
    device: str | None = None,
    node_code: str | None = None,
) -> dict:
    """按时间段取用量。

    :param scope: 'workshop' 仅车间 / 'line' 指定单线 / 'all' 车间 + 各线
    :param granularity: 'hour' | 'day' | 'month'
    :param start_at / end_at: 精确到小时的端点（v1.4.0）。只影响 hour 粒度 ——
        day / month 的桶本身就是整天/整月，端点会被规范到所属周期边界。
    :param kind / workshop / usage_type / device / node_code:
        ★ v1.5.2 后台三级筛选（车间 → 用能类型 → 设备）。
        这三层任一项给定时，走「按计量点集合直接取数」的通道（见 `_meter_focus`），
        不再按「车间合计 + 各线」出多条序列 —— 用户已经明确要看某个设备，
        再叠一整套产线序列只会让图变成一锅粥。

    ⚠ hour 粒度**只到最后一个已结算整点**：区间终点落在当前小时时，当前小时不入桶
      （15 点时最大可查 14 点）。当前小时的用量在下个整点采到端点前无定义，
      放进来只会得到一个恒为空的时段并被当成「采集缺数」（v1.4.4）。
    """
    now = now or datetime.now()
    if granularity == "hour":
        dim = None
    elif granularity == "month":
        dim = PeriodSummary.DIM_MONTH
    else:
        granularity = "day"
        dim = PeriodSummary.DIM_DAY
    # 小时桶按绝对时钟时间走（生产日只是把 06—次日05 归到同一个日桶），
    # 所以起点/终点日期要取实际时间端点，而不是生产日键。
    hour_start = start_at.date() if start_at else start
    hour_end = end_at.date() if end_at else end

    # ── 焦点通道 ──
    # 两种情形都走「按点位集合直接取数、只出一条序列」：
    #   ① 选到了具体类型 / 设备 / 节点 —— 用户已经锁定点位；
    #   ② 能源类型不是电 —— 水/汽没有「各条产线」这种拆法，常规的
    #      「车间合计 + 各线」分支全是电表口径，硬套会给水/汽页面返回电的序列
    #      （面板显示电气数据，现场会直接按错的数字调度）。
    non_electric = bool(kind) and _type_code(kind) != EnergyType.ELECTRIC
    focused = bool(usage_type or device or node_code) or non_electric
    focus_meters: list[EnergyMeter] = []
    if focused:
        focus_meters = meters_of(
            kind=kind, workshop=workshop, usage_type=usage_type, device=device, node_code=node_code
        )

    labels: list[str] = []
    series: list[dict] = []
    quality_series: list[str] | None = None
    klist: list[str] = []

    # ── 车间 ──
    want_workshop = scope in ("workshop", "all") and not focused
    want_lines = scope in ("line", "all") and not focused
    target_lines = _lines_scope(line_code) if want_lines else []

    if focused:
        hour_buckets: list[tuple[date_cls, int]] | None = None
        buckets: list[tuple[date_cls, int]] = []
        klist: list[str] = []
        if granularity == "hour":
            buckets = _hour_labels(hour_start, hour_end, now, start_at=start_at, end_at=end_at, closed_only=True)
            hour_buckets = buckets
            labels = [f"{d:%m-%d} {h:02d}" for d, h in buckets]
        else:
            keys = _period_keys(dim, start, end, now)
            labels = [lab for _k, lab in keys]
            klist = [k for k, _ in keys]

        # ★ 按能源类型拆开算。三级筛选没带 kind 时 `meters_of` 是不限类型的，
        #   电表 + 水表 + 汽表会进同一个「全员可信才算数」的组 ——
        #   水缺数就把电和汽一起抹成 None，这正是本次要消灭的连坐。
        groups = split_by_kind(focus_meters)
        first_ok = first_data_map(focus_meters)
        util: dict[str, list[float | None]] = {"water": [], "steam": []}
        base_name = _focus_name(workshop, usage_type, device, node_code, kind)

        for code, gm in groups.items():
            # 单位按能源类型定，不看「排序后的第一块表」
            unit = unit_of(gm)
            if granularity == "hour":
                mat = _hour_matrix(gm, buckets, first_ok=first_ok)
                vals: list[float | None] = [_round_for(unit, mat[b][0]) for b in buckets]
                if code == EnergyType.ELECTRIC:
                    quality_series = [mat[b][1] for b in buckets]
            else:
                vals = []
                for k in klist:
                    start_dt, end_dt = B.period_window(dim, k)
                    vals.append(
                        _round_for(
                            unit,
                            _range_total_kwh(gm, start_dt, end_dt, first_ok=first_ok),
                        )
                    )
            series.append(
                {
                    "key": "focus" if len(groups) == 1 else f"focus:{code}",
                    "name": base_name if len(groups) == 1 else f"{base_name} · {_kind_label(code)}",
                    "unit": unit,
                    "values": vals,
                }
            )
            # 水/汽同时回填 utility.*：前端水页与汽页读的是 d.utility.water / .steam。
            # 此前焦点通道把它硬写成空数组 ⇒ 两页恒空，还被 emptyHint 一律说成
            # 「车间进水总表未接入」，把汽页也一起冤枉了；totals.waterT/steamT 也恒 None。
            if code == EnergyType.WATER:
                util["water"] = vals
            elif code == EnergyType.STEAM:
                util["steam"] = vals

        # 电量合计只按电表算 —— 把水/汽的 m³ 累加进 kWh 是量纲错误
        win_start, win_end = _query_window(
            granularity, start, end, klist=klist, hour_buckets=hour_buckets
        )
        elec_meters = groups.get(EnergyType.ELECTRIC, [])
        kwh_total = (
            _range_total_kwh(
                elec_meters,
                win_start,
                win_end,
                buckets=hour_buckets,
                first_ok=first_ok,
            )
            if elec_meters
            else None
        )
        out_total = Decimal(0)
        prod_real = False
        return _range_payload(
            start, end, granularity, scope, line_code, labels, series, quality_series,
            util, kwh_total, out_total, prod_real, focus_meters, win_start, win_end,
        )

    hour_buckets: list[tuple[date_cls, int]] | None = None
    if granularity == "hour":
        # closed_only：只到最后一个已结算整点 —— 正在走的这一小时算不出用量（见 _hour_labels）
        buckets = _hour_labels(hour_start, hour_end, now, start_at=start_at, end_at=end_at, closed_only=True)
        hour_buckets = buckets
        labels = [f"{d:%m-%d} {h:02d}" for d, h in buckets]
        wmat = None
        if want_workshop:
            # 只算一次：既要值也要质量，重复计算会白白多扫一遍库
            wmat = _hour_matrix(_workshop_meters(workshop), buckets)
            series.append(
                {
                    "key": "workshop",
                    "name": "车间合计",
                    "unit": "kWh",
                    "values": [round_kwh(wmat[b][0]) for b in buckets],
                }
            )
        for ln in target_lines:
            mat = _hour_matrix(_line_meters(ln), buckets)
            series.append(
                {
                    "key": ln.line_code,
                    "name": ln.line_name,
                    "unit": "kWh",
                    "values": [round_kwh(mat[b][0]) for b in buckets],
                }
            )
        if wmat is not None:
            quality_series = [wmat[b][1] for b in buckets]
        util = {
            "water": _utility_hour_series(EnergyType.WATER, buckets, start, end),
            "steam": _utility_hour_series(EnergyType.STEAM, buckets, start, end),
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
                    # ⚠ 走 round_kwh 而不是 float()：大屏对同一行也做 1 位小数，
                    #   这里若不处理，同一天在两处会显示不同位数（B-524）
                    vals.append(round_kwh(rows[k].kwh))
                    continue
                start_dt, end_dt = B.period_window(dim, k)
                vals.append(round_kwh(_range_total_kwh(_workshop_meters(workshop), start_dt, end_dt)))
            series.append({"key": "workshop", "name": "车间合计", "unit": "kWh", "values": vals})
            quality_series = [rows[k].quality if k in rows else "computed" for k in klist]

        for ln in target_lines:
            rows = _summary_series(dim, PeriodSummary.SCOPE_LINE, ln, klist)
            vals = []
            for k in klist:
                if k in rows:
                    vals.append(round_kwh(rows[k].kwh))
                    continue
                start_dt, end_dt = B.period_window(dim, k)
                vals.append(round_kwh(_range_total_kwh(_line_meters(ln), start_dt, end_dt)))
            series.append({"key": ln.line_code, "name": ln.line_name, "unit": "kWh", "values": vals})

        util = {
            "water": _utility_series(EnergyType.WATER, dim, start, end, now, keys),
            "steam": _utility_series(EnergyType.STEAM, dim, start, end, now, keys),
        }

    # ── 合计 ──
    # ⚠ 口径必须与系列完全一致，否则会出现「series 全空但 totals 有数」——
    #   界面上一张空图配一个非零合计，用户只能得出「系统算错了」的结论。
    #   焦点通道（三级筛选 / 水 / 汽）在上面已经按能源类型分别算完并 return 了，
    #   走到这里只剩常规通道：合计就是车间电力口径。
    all_lines = list(ProductionLine.active_lines_qs())
    if scope == "line" and line_code:
        all_lines = [ln for ln in all_lines if ln.line_code == line_code.upper()]
    meters = _workshop_meters(workshop) if scope in ("workshop", "all") else (
        _line_meters(all_lines[0]) if all_lines else []
    )
    win_start, win_end = _query_window(
        granularity, start, end, klist=klist, hour_buckets=hour_buckets
    )
    kwh_total = _range_total_kwh(meters, win_start, win_end, buckets=hour_buckets)
    out_total = _range_output(all_lines, win_start, win_end)
    prod_real = (
        ProductionHourly.objects.filter(B.hour_q(win_start, win_end), line__in=all_lines)
        .exclude(source__in=DEMO_SOURCES)
        .exists()
    )

    return _range_payload(
        start, end, granularity, scope, line_code, labels, series, quality_series,
        util, kwh_total, out_total, prod_real, meters, win_start, win_end,
    )


def _kind_label(type_code: str) -> str:
    """库内能源编码 → 中文短名（序列名与提示文案共用）。"""
    return {
        EnergyType.ELECTRIC: "电",
        EnergyType.WATER: "水",
        EnergyType.STEAM: "汽",
    }.get(str(type_code).upper(), str(type_code))


def _focus_name(
    workshop: str | None,
    usage_type: str | None,
    device: str | None,
    node_code: str | None,
    kind: str | None = None,
) -> str:
    """焦点序列名：能拼多细就拼多细（「碳水车间 · 辅机用电 · 冰水机和水塔」）。

    `kind` 只在没有更细维度时用来兜底 —— 水/汽页只传 workshop，
    不带上能源名就会得到一条叫「碳水车间」的线，看不出是水还是汽。
    """
    parts = []
    if workshop:
        ws = ProductionLine.objects.filter(line_code=workshop).first()
        parts.append(ws.line_name if ws else workshop)
    if usage_type:
        parts.append(usage_type)
    if device:
        parts.append(device)
    elif node_code:
        node = ProductionLine.objects.filter(line_code=node_code).first()
        parts.append(node.line_name if node else node_code)
    if kind and len(parts) <= 1:
        # 只选了能源/车间：把能源名补上（电/水/汽），否则同名序列分不清是哪一种
        parts.append({"elec": "电", "water": "水", "steam": "汽"}.get(str(kind).lower(), str(kind)))
    return " · ".join(parts) or "选定范围"


def _meter_coverage(meters: list[EnergyMeter], start: datetime, end: datetime) -> dict:
    """统计「本次查询应参与的计量点里，有几个在本区间真的出过数」。

    为什么要有它：车间合计按「参与取数的表都有可信值才算数」的口径下，
    一块表没数就会让整条合计变 `--`。这本身是对的（不返回偏小值），
    但界面只看到一条空线 + 一堆有数的产线，第一反应是「系统坏了」。
    所以把缺的是哪几块表如实报出来，并且**分成两类**——

      missing      已接入、本区间却没出数  → 真故障，要修要告警
      unconnected  从未出过可信值          → 还没接线/没给 token，不参与合计

    两类对现场的处理动作完全不同（修表 vs 去接线），混成一个列表就等于没说。
    """
    return C.coverage_of(meters, start, end)


def _range_payload(
    start, end, granularity, scope, line_code, labels, series, quality_series,
    util, kwh_total, out_total, prod_real, meters, window_start, window_end,
) -> dict:
    """统一的返回体组装 —— 两条取数通道（焦点 / 常规）共用，避免结构漂移。"""
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
            "kwh": round_kwh(kwh_total),
            "output": int(out_total) if prod_real else 0,
            "waterT": _sum_optional(util.get("water") or []),
            "steamT": _sum_optional(util.get("steam") or []),
        },
        "productionAvailable": prod_real,
        "bucketCount": len(labels),
        "meterCount": len(meters),
        # 生产日口径：日 / 月桶标签仍是开始日期，实际窗口是 06:00—次日 06:00
        "dayStartHour": B.DAY_START_HOUR,
        # 计量点覆盖度：界面据此解释「为什么合计是空的」
        "coverage": _meter_coverage(meters, window_start, window_end),
    }


def _utility_hour_series(kind: str, buckets, start: date_cls, end: date_cls) -> list[float | None]:
    meters = list(EnergyMeter.objects.filter(energy_type__type_code=kind, enabled=True))
    if not meters:
        return [None for _ in buckets]
    mat = _hour_matrix(meters, buckets)
    return [_round(mat[b][0]) for b in buckets]


def _round_for(unit: str, v: Decimal | None) -> float | None:
    """按单位选展示精度 —— 电量 1 位（与大屏同源），其余能源沿用 3 位。

    为什么必须按单位分：焦点通道同一段代码要服务水/汽（unit='t'），
    若一律按电量舍入，水/汽 0.004 t 会被抹成 0，看起来像「这小时没用汽」。
    """
    if unit == "kWh":
        return round_kwh(v)
    return _round(v)


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


# ══════════════════════════════════════════════════════════════
#  数据中心：按能源的「累计用量 / 累计产量 / 累计单耗」+ 变动费用
# ══════════════════════════════════════════════════════════════


def _day_list(start: date_cls, end: date_cls, now: datetime) -> list[date_cls]:
    """[start, end] 内**已经开始**的生产日（未来生产日没有数据，不生成桶）。"""
    out: list[date_cls] = []
    current = B.production_day(now)
    d = start
    while d <= end and d <= current:
        out.append(d)
        d += timedelta(days=1)
    return out


def _dec(v: float | None) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def _cumulative(values: list[float | None]) -> tuple[list[float | None], list[int]]:
    """逐桶累加，并给出「累加到这一格时吃进了几格可信数据」。

    ⚠ 缺数格**跳过但如实计入 covered**：累计值本身仍然算得出来（它是已有部分的真实和），
    但读者必须能看出「这个累计只覆盖了 N/M 格」，否则中断过采集的区间会被当成完整区间。
    这是 B-403（进行中周期给覆盖度）在区间上的同款处理。
    """
    acc = 0.0
    covered = 0
    cum: list[float | None] = []
    covers: list[int] = []
    for v in values:
        if v is not None:
            acc += v
            covered += 1
        cum.append(round(acc, 3) if covered else None)
        covers.append(covered)
    return cum, covers


def _unit_series(cum_usage: list[float | None], cum_out: list[float | None], scale: float) -> list[float | None]:
    """累计单耗 = 累计用量 ÷（累计产量 ÷ scale）。产量缺 → None，绝不补 0。"""
    out: list[float | None] = []
    for u, o in zip(cum_usage, cum_out):
        if u is None or o is None or o <= 0:
            out.append(None)
            continue
        out.append(round(u / (o / scale), 3))
    return out


def _output_hour_series(lines: list[ProductionLine], buckets: list[tuple[date_cls, int]]) -> list[float | None]:
    """按（日期, 小时）取真实产量；未接入的桶给 None（不是 0）。"""
    if not lines or not buckets:
        return [None for _ in buckets]
    rows = (
        ProductionHourly.objects.filter(
            line__in=lines,
            stat_date__in=sorted({d for d, _h in buckets}),
            quality__in=ProductionHourly.TRUSTED,
        )
        .exclude(source__in=DEMO_SOURCES)
        .values("stat_date", "stat_hour")
        .annotate(t=Sum("output"))
    )
    got = {(r["stat_date"], r["stat_hour"]): r["t"] for r in rows}
    return [_round(got[b]) if b in got else None for b in buckets]


def _output_day_series(lines: list[ProductionLine], days: list[date_cls]) -> list[float | None]:
    """按生产日的真实产量（瓶）；未接入的生产日给 None。"""
    if not lines or not days:
        return [None for _ in days]
    return [_round(S.production_total(lines, B.day_start(d), B.day_end(d)), 0) for d in days]


def dashboard_range(
    start_at: datetime,
    end_at: datetime,
    *,
    granularity: str = "hour",
    line_code: str | None = None,
    now: datetime | None = None,
) -> dict:
    """数据中心主数据：电 / 水 / 汽三张累计趋势图 + 单箱变动费用主图。

    与 `/api/energy/range` 同源同口径（同一套 `_hour_matrix` 与汇总表），
    这样后台看板、用量查询页、大屏三处的数字必然一致。

    ⚠ 三条硬口径：
      · 水 / 汽只有车间总表 —— `line_code` 给定时它们的序列一律 None，并置
        `perLineAvailable=false`，前端据此**禁用勾选**，而不是画一条全零线；
      · 产量未接入 → 累计产量 / 累计单耗 / 单箱变动费用一律 None；
      · 单价或箱规未标定 → 费用整段 None，并在 `cost.blockers` 里写清缺哪一项。
        缺项**不按 0 计入** —— 那会产出一个偏小的、看起来完全正常的费用。
    """
    now = now or datetime.now()
    granularity = granularity if granularity in ("hour", "day") else "hour"
    start, end = start_at.date(), end_at.date()
    # 日粒度按生产日归集：结束时刻正好落在 06:00 时，06:00 属于**下一个**
    # 生产日，而查询区间 [start_at, end_at) 并不包含它，所以要回退一瞬。
    start_day = B.production_day(start_at)
    end_day = B.production_day(end_at - timedelta(microseconds=1))
    lines = _lines_scope(line_code)
    meters = _line_meters(lines[0]) if (line_code and lines) else _workshop_meters()
    per_line = line_code is not None

    if granularity == "hour":
        buckets = _hour_labels(start, end, now, start_at=start_at, end_at=end_at)
        labels = [f"{d:%m-%d} {h:02d}:00" for d, h in buckets]
        emat = _hour_matrix(meters, buckets) if buckets else {}
        kwh_vals = [round_kwh(emat[b][0]) for b in buckets]
        out_vals = _output_hour_series(lines, buckets)
        if per_line:
            water_vals = [None for _ in buckets]
            steam_vals = [None for _ in buckets]
        else:
            water_vals = _utility_hour_series(EnergyType.WATER, buckets, start, end)
            steam_vals = _utility_hour_series(EnergyType.STEAM, buckets, start, end)
    else:
        days = _day_list(start_day, end_day, now)
        labels = [d.strftime("%m-%d") for d in days]
        kwh_vals = [
            round_kwh(_range_total_kwh(meters, B.day_start(d), B.day_end(d))) for d in days
        ]
        out_vals = _output_day_series(lines, days)
        if per_line:
            water_vals = [None for _ in days]
            steam_vals = [None for _ in days]
        else:
            keys = [(d.strftime("%Y-%m-%d"), d.strftime("%m-%d")) for d in days]
            water_vals = _utility_series(EnergyType.WATER, PeriodSummary.DIM_DAY, start_day, end_day, now, keys)
            steam_vals = _utility_series(EnergyType.STEAM, PeriodSummary.DIM_DAY, start_day, end_day, now, keys)
        start, end = start_day, end_day

    cp = cost.load_params()
    prod_real = any(v is not None for v in out_vals)
    cum_out, _out_covers = _cumulative(out_vals) if prod_real else ([None] * len(labels), [0] * len(labels))

    def block(values: list[float | None], unit: str) -> dict:
        cum, covers = _cumulative(values)
        return {
            "unit": unit,
            "values": values,
            "cumulative": cum,
            "cumulativeCovered": covers,
            "outputCumulative": cum_out,
            "unitCumulative": _unit_series(cum, cum_out, 1000.0),
            "perLineAvailable": not per_line if unit == "t" else True,
        }

    energies = {
        "electric": block(kwh_vals, "kWh"),
        "water": block(water_vals, "t"),
        "steam": block(steam_vals, "t"),
    }

    # ── 变动费用（元）与单箱变动费用（元/箱）──
    cost_yuan: list[float | None] = []
    per_case: list[float | None] = []
    for k, w, s, o in zip(kwh_vals, water_vals, steam_vals, out_vals):
        kk, ww, ss = _dec(k), _dec(w), _dec(s)
        cost_yuan.append(_round(cost.variable_cost_yuan(kk, ww, ss, cp), 2))
        per_case.append(_round(cost.per_case_cost(kk, ww, ss, _dec(o), cp), 2))

    cum_cost, cost_covers = _cumulative(cost_yuan)
    per_case_cum: list[float | None] = []
    for c, o in zip(cum_cost, cum_out):
        if c is None or o is None or o <= 0 or not cp.bottles_per_case:
            per_case_cum.append(None)
            continue
        per_case_cum.append(round(c / (o / float(cp.bottles_per_case)), 2))

    # ── 算不出费用时，把原因说清楚（界面上直接显示，不让人以为系统坏了）──
    blockers: list[str] = []
    missing_prices = [
        label
        for label, v in (("电价", cp.electric), ("工业水价", cp.water), ("蒸汽单价", cp.steam), ("箱规", cp.bottles_per_case))
        if v is None or v <= 0
    ]
    if missing_prices:
        blockers.append("成本参数未标定：" + "、".join(missing_prices) + "（后台「系统参数」页维护）")
    if not prod_real:
        blockers.append("产量数据源未接入，折不出箱数")
    if not per_line:
        if all(v is None for v in water_vals):
            blockers.append("车间进水总表无数据")
        if all(v is None for v in steam_vals):
            blockers.append("蒸汽总表无数据")

    return {
        "from": start_at.strftime("%Y-%m-%d %H:%M"),
        "to": end_at.strftime("%Y-%m-%d %H:%M"),
        "granularity": granularity,
        "line": line_code.upper() if line_code else None,
        "labels": labels,
        "bucketCount": len(labels),
        "energies": energies,
        "cost": {
            "available": cp.complete and prod_real,
            "values": cost_yuan,
            "cumulative": cum_cost,
            "cumulativeCovered": cost_covers,
            "perCase": per_case,
            "perCaseCumulative": per_case_cum,
            "standardPerCase": float(cp.standard_per_case) if cp.standard_per_case is not None else None,
            "blockers": blockers,
        },
        "productionAvailable": prod_real,
        "dayStartHour": B.DAY_START_HOUR,
        "totals": {
            "kwh": _sum_optional(kwh_vals),
            "waterT": _sum_optional(water_vals),
            "steamT": _sum_optional(steam_vals),
            "output": int(sum(v for v in out_vals if v is not None)) if prod_real else None,
            "costYuan": _sum_optional(cost_yuan),
            "perCaseCost": per_case_cum[-1] if per_case_cum else None,
        },
    }
