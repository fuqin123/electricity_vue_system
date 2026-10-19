"""计量点「是否参与取数」的判定 —— 把「从未接入」与「接入后缺采」分开。

为什么需要这个模块
────────────────────────────────────────────────────────────
小时/日合计一直沿用「组内每块表都有可信值，这一格才算数」的严格口径
（偏小值比空值更危险，见 B-403）。但组成员此前一律由 `enabled=True` 现拼，
而 `enabled` 只有「启不启用」一层含义 —— 现场**登记了但还没接线/还没给 token**
的表同样是 `enabled=True`。

后果是：一块从未出过一个数的水表，会让它所在组的每一格都被否决。
按能源类型混组时（后台三级筛选未带 kind），电和汽会跟着一起变成 `--`；
即使不混组，也会让该类永久无数据 —— 而界面上看起来像「系统坏了」。

本模块给出的口径：

  · 从未接入（unconnected）：整库历史上没有一次成功原始读数的表
      → **不参与**合计计算，不否决别人；由告警引擎如实报「未接入」。
  · 接入后缺采（missing）：出过可信值、但本格没有
      → **仍然**整格 None。这是真故障，绝不能悄悄少加一块表。

「首次可信时刻」按小时记录，因此中途新装一块表不会让历史图表倒退：
它只从自己第一次出数的那一刻起参与合计。
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime, time

from django.db.models import Min

from ..models import EnergyHourly, EnergyRaw, EnergyType

logger = logging.getLogger(__name__)

#: 序列/格子上用的展示态：不属于 EnergyHourly.quality 的库内枚举，
#: 只出现在接口返回里，表示「这一格没有参采的表，因为表还没接入」。
Q_UNCONNECTED = "unconnected"

#: 判「可信」的标准与 EnergyHourly.TRUSTED 同源，不另立口径
TRUSTED = EnergyHourly.TRUSTED


def _hour_key(d: date_cls, h: int) -> datetime:
    """把 (日期, 小时) 变成可比较的 datetime。"""
    return datetime.combine(d, time.min).replace(hour=h)


def first_data_map(meters) -> dict[int, datetime]:
    """各计量点**第一次真正读到数**的时刻。

    不在返回字典里的表 = 从未成功采到过任何读数 = 未接入。

    ⚠ 判据是「有成功的原始读数」，不是「算得出可信的小时差值」。
      这两者的差别在重启后最要命：停机几天再拉起，本小时还没有上一秒的端点读数，
      所有小时行的 quality 都是 missing —— 若按「有过 ok 的小时差值」判，
      刚重启的系统会把 30 块正常在采的表全部误判成「未接入」并踢出合计，
      同时告警页被 30 条假「未接入」刷屏。那样告警系统本身就成了噪音源。

    两个来源取更早的一个：
      · `energy_raw.ok=True` 的最早 collect_time —— 设备可读的直接证据；
      · `energy_hourly` 里非空 consumption 的最早日期 —— 原始读数按
        `readings_retention_days` 会过期清理，只留小时表的历史仍应算「已接入」。
    """
    ids = [m.id for m in meters]
    if not ids:
        return {}

    out: dict[int, datetime] = {}

    # 来源①：成功的原始读数 —— 分钟精度，是「设备读得到数」最直接的证据
    for mid, t in EnergyRaw.objects.filter(
        meter_id__in=ids, ok=True, reading__isnull=False
    ).values_list("meter_id").annotate(t=Min("collect_time")):
        if t is not None:
            out[mid] = t

    # 来源②：小时表里最早出过数的那一天，再单独取那一天里最早的那一个小时。
    # ⚠ 不能只取日期 —— 那样会把「首次出数」当成当天 00:00，中途接入的表
    #   就会反过来否决它接入之前的小时，历史图表照样倒退（这正是本函数要防的）。
    first_date: dict[int, date_cls] = dict(
        EnergyHourly.objects.filter(meter_id__in=ids, consumption__isnull=False)
        .values_list("meter_id")
        .annotate(d=Min("stat_date"))
    )
    if first_date:
        rows = (
            EnergyHourly.objects.filter(
                consumption__isnull=False, stat_date__in=set(first_date.values())
            )
            .values("meter_id", "stat_date")
            .annotate(h=Min("stat_hour"))
        )
        for r in rows:
            mid = r["meter_id"]
            if first_date.get(mid) != r["stat_date"]:
                continue
            at = datetime.combine(r["stat_date"], time.min).replace(hour=int(r["h"] or 0))
            cur = out.get(mid)
            if cur is None or at < cur:
                out[mid] = at

    return out


def participating(meters, first_ok: dict[int, datetime] | None = None) -> list:
    """剔除「从未接入」的表，得到真正参与取数的集合。

    用于组范围比界面口径大的场合（如车间合计、水/汽总表序列）——
    这些地方的格子判定是「全员都有才算数」，一块未接入的表会把整组否决。
    """
    if first_ok is None:
        first_ok = first_data_map(meters)
    return [m for m in meters if m.id in first_ok]


def unconnected(meters, first_ok: dict[int, datetime] | None = None) -> list:
    """与 participating 互补：登记了但从未成功采到过读数的表。"""
    if first_ok is None:
        first_ok = first_data_map(meters)
    return [m for m in meters if m.id not in first_ok]


def expected_ids(first_ok: dict[int, datetime], meters, bucket: tuple[date_cls, int]) -> set[int]:
    """某一格里应当有数据的计量点 id 集合。

    只包含「首次可信时刻 ≤ 本格」的表 —— 中途接入的表不该否决它接入之前的小时，
    否则新装一块表会让上周的图表整体倒退。
    """
    at = _hour_key(*bucket)
    return {m.id for m in meters if (t := first_ok.get(m.id)) is not None and t <= at}


def split_by_kind(meters) -> dict[str, list]:
    """按能源类型分组（ELECTRIC / WATER / STEAM）。

    跨类型混算是本模块要消灭的头号问题：水、汽、电的量纲和接入进度都不同，
    放进同一个「全员可信才算数」的组里，任何一类缺数都会连坐其它类。
    """
    groups: dict[str, list] = {}
    for m in meters:
        groups.setdefault(m.energy_type.type_code, []).append(m)
    return groups


def unit_of(meters, fallback: str = "kWh") -> str:
    """这批点的计量单位 —— 按能源类型定，不看「排序后的第一块表」。

    此前焦点序列取 `focus_meters[0].unit`，一旦组里混了类型或排序变了，
    一条电量序列就会挂着 m³ 的单位（数值没错，读的人却被单位骗）。
    """
    codes = {m.energy_type.type_code for m in meters}
    if len(codes) == 1:
        code = codes.pop()
        if code == EnergyType.ELECTRIC:
            return "kWh"
        if code == EnergyType.WATER:
            return "m³"
        if code == EnergyType.STEAM:
            return "t"
    units = {m.unit for m in meters if m.unit}
    return units.pop() if len(units) == 1 else fallback


def coverage_of(meters, start: date_cls, end: date_cls, first_ok: dict[int, datetime] | None = None) -> dict:
    """查询区间内的计量点覆盖度，区分「没出数」与「还没接入」。

    total      登记且启用的表数
    connected  其中已接入（历史上有过可信值）的表数
    covered    本区间内出过行的表数
    missing    已接入、但本区间没出数 —— 真的缺采，该报该告警
    unconnected 从未成功采到过读数 —— 不参与合计，另走「未接入」告警
    """
    if first_ok is None:
        first_ok = first_data_map(meters)
    total = len(meters)
    if not total:
        return {"total": 0, "connected": 0, "covered": 0, "missing": [], "unconnected": []}

    have = set(
        EnergyHourly.objects.filter(
            meter__in=meters, stat_date__gte=start, stat_date__lte=end
        ).values_list("meter_id", flat=True)
    )
    missing = [m.code for m in meters if m.id in first_ok and m.id not in have]
    unconn = [m.code for m in meters if m.id not in first_ok]
    covered = sum(1 for m in meters if m.id in have)
    return {
        "total": total,
        "connected": total - len(unconn),
        "covered": covered,
        "missing": missing,
        "unconnected": unconn,
    }
