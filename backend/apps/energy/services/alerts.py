"""告警引擎 —— 把「能源数据没被正确计量」变成可确认、可自愈的记录。

调用方式：`evaluate()` 是幂等的全量评估，采集轮次结束、每日结算之后、
或 `manage.py check_alerts` 手工巡检都可以调；它自己决定新增/更新/恢复。

## 为什么这些规则在这里，而不是继续塞在 snapshot 里现算

旧实现每次请求现算 4 条提示（见 `snapshot.py` 的 `alerts`），有三个绕不过去的坑：

  · 点「确认」没用 —— 下一帧又算出来，等于没有确认功能；
  · 没有「恢复」—— 修好了还挂在列表里，几次之后没人再看告警页；
  · 没有「首次/最近/次数」—— 一块表今天抖 3 次和连续挂 6 小时是两回事。

## 与其它模块的口径关系（重要）

  · **未接入的表不参与合计**（见 `coverage` 模块）。正因为数字层面把它摘掉了，
    必须在这里把它报出来 —— 否则「为什么这块表的数没算进去」就变成了隐式行为。
    这两条规则是一枚硬币的两面，改一边必须改另一边。
  · **网关这条规则不等于列表页的「在线」徽标**。列表页走 `api.gateway_is_online()`
    的「心跳并集 + 最新证据优先」（B-542），语义是「这台网关现在通不通」；
    这里只陈述一个更具体的事实：「它最近一轮采集，点位全部失败」。
    两者可以并存且结论不同（例如刚重启过的网关心跳还旧但本轮成功），
    所以不要把这里改成复用那套判定 —— 那会让「离线」和「采集失败」再也分不清。

  · 级别沿用 `docs/api-contract.md` §4.4 的四级，前端 `AlarmLevelTag` 直接映射。
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime, time, timedelta

from django.db import transaction
from django.db.models import Count, Max

from eep import conf

from ..models import (
    EnergyAlarm,
    EnergyCollectLog,
    EnergyDaily,
    EnergyGateway,
    EnergyHourly,
    EnergyMeter,
    EnergyRaw,
)
from .coverage import first_data_map

logger = logging.getLogger(__name__)

#: 参与「小时质量」统计的回看小时数 —— 再早就该由日报而不是告警来承担
QUALITY_LOOKBACK_HOURS = 24

#: 单轮最多落多少条同规则告警。现场 30 多块表，正常量级在个位数；
#: 设上限是为了「采集整体停摆」时不要一次灌进几百条把告警页埋掉。
MAX_PER_RULE = 50

#: 日终结算时刻。覆盖率规则只认在此之后重算过的日汇总行：00:30 是 EEP-Rollup
#: 的固定结算点，也是跨日边界读数重试后最迟落库的时刻。不能只要求「过了零点」——
#: rollup 容器在 00:00–00:30 之间重启会立刻补跑一次 summarize，那一刻昨天的
#: 最后一小时（等今天 00:xx 的边界读数）可能还没算出来，日行同样是残缺快照。
COVERAGE_SETTLED_AT = time(0, 30)


def _fmt(dt: datetime | None) -> str:
    return f"{dt:%Y-%m-%d %H:%M}" if dt else ""


def _stale_hours() -> int:
    """采集停摆判定阈值（小时），与大屏用的是同一个配置项。"""
    return int(conf.get("collect.stale_alert_hours", 2) or 2)


class _Collector:
    """本轮评估的结果集合，负责落库与「本轮没再出现 → 自动恢复」。

    `codes` 按类别收集，恢复时**只处理本轮真正评估过的类别** ——
    某条规则因异常没跑成，不该顺手把它名下的告警全部关掉。
    """

    def __init__(self, now: datetime, *, bump: bool = True, categories: set[str] | None = None) -> None:
        self.codes: dict[str, set[str]] = {}
        # 本轮**评估过**的类别（哪怕一条都没产生）。自愈必须以它为准：
        # 某类告警全部消失恰恰是最需要关单的情况，而 codes 里那时根本没有这个键。
        self.categories: set[str] = set(categories) if categories is not None else set()
        self.created = 0
        self.updated = 0
        self.resolved = 0
        # 读路径的轻量评估每 30 秒就来一次，累加 repeat 会让「重复次数」
        # 变成「被查询次数」而失去意义 —— 那条路径传 bump=False
        self.bump = bump
        self._now = now

    def add(self, category: str, code: str, **fields) -> None:
        self.codes.setdefault(category, set()).add(code)
        obj = EnergyAlarm.objects.filter(code=code, status__in=(EnergyAlarm.STATUS_ACTIVE, EnergyAlarm.STATUS_ACKED)).first()
        if obj is None:
            EnergyAlarm.objects.create(
                code=code,
                category=category,
                occurred_at=self._now,
                last_at=self._now,
                repeat=1,
                **fields,
            )
            self.created += 1
            return
        # 已确认的告警不该被重新打开成 active —— 那会让「确认」变成静音一轮
        obj.last_at = self._now
        if self.bump:
            obj.repeat += 1
        for key, val in fields.items():
            setattr(obj, key, val)  # 内容/级别/阈值会随现场变化更新（同一件事，描述更准）
        self.updated += 1
        obj.save()

    def resolve_stale(self) -> None:
        """把本轮没再出现、却仍挂着的告警标为已恢复。"""
        live = {c for codes in self.codes.values() for c in codes}
        qs = EnergyAlarm.objects.filter(
            status__in=(EnergyAlarm.STATUS_ACTIVE, EnergyAlarm.STATUS_ACKED),
            category__in=self.categories,
        ).exclude(code__in=live)
        n = qs.update(status=EnergyAlarm.STATUS_RESOLVED, resolved_at=self._now)
        self.resolved += n


# ── 各条规则 ────────────────────────────────────────────────


def _rule_unconnected(col: _Collector, meters: list[EnergyMeter], first_ok: dict[int, datetime]) -> None:
    """登记了却从未出过可信值 —— 「未接入」。

    这些表已被排除出合计口径（不否决别的表），所以必须在这里显式报出来，
    否则「为什么这块表的数没算进来」就成了看不见的隐式行为。
    """
    for m in [x for x in meters if x.id not in first_ok][:MAX_PER_RULE]:
        col.add(
            EnergyAlarm.CAT_METER_UNCONNECTED,
            f"METER-UNCONNECTED:{m.code}",
            level=EnergyAlarm.LEVEL_MAJOR,
            source=m.name,
            content=f"计量点 {m.code} 已登记但从未采到可信数据，未参与合计",
            value="",
            threshold="",
            meter=m,
            gateway_id=m.gateway_id,
            line_id=m.line_id,
        )


def _rule_collect_fail(col: _Collector, meters: list[EnergyMeter]) -> None:
    """采集失败的表（最近一轮 last_ok=False）。

    ⚠ 旧的大屏提示用的是 `.exclude(last_at=None)`，把「从没成功过的表」整批
      排除掉了 —— 而那恰恰是最该报的一类。这里不再排除：从没成功过的表
      由「未接入」规则覆盖，两者互斥（last_at 为空 → 未接入；有值但 last_ok=False → 失败）。
    """
    bad = [m for m in meters if m.last_at is not None and m.last_ok is False]
    for m in bad[:MAX_PER_RULE]:
        col.add(
            EnergyAlarm.CAT_METER_FAIL,
            f"METER-FAIL:{m.code}",
            level=EnergyAlarm.LEVEL_CRITICAL,
            source=m.name,
            content=f"{m.name} 采集失败：{m.last_error or '未知原因'}",
            value=f"最近成功采集 {_fmt(m.last_at)}",
            threshold="",
            meter=m,
            gateway_id=m.gateway_id,
            line_id=m.line_id,
        )


def _rule_hour_quality(col: _Collector, now: datetime) -> None:
    """小时差值出现**数据完整性异常**的表：累计值回退（疑似换表/清零）、异常尖峰。

    ⚠ 为什么不含 `missing`（缺数）：缺数已经被另外三条覆盖了 ——
      日级有 coverage_low、读数级有 meter_collect_fail、界面上分时表那一格
      本来就标着「异常·缺数」。再单独开一条只会把同一件事报三遍。
      实测过：系统停机几天再拉起时，33 块正常在采的表会因为「还没有攒出
      一个完整小时」同时冒出 33 条缺数告警 —— 一个全亮的告警页等于没有告警页。

    `partial`（端点偏离整点）同理不报：它是采集节奏抖动，值本身可用。
    """
    since = (now - timedelta(hours=QUALITY_LOOKBACK_HOURS)).date()
    label = {EnergyHourly.Q_RESET: "累计值回退（疑似换表或清零）", EnergyHourly.Q_SPIKE: "异常尖峰"}
    rows = (
        EnergyHourly.objects.filter(stat_date__gte=since, quality__in=list(label))
        .values("meter_id", "quality")
        .annotate(n=Count("id"))
        .order_by("meter_id")
    )
    per_meter: dict[int, dict[str, int]] = {}
    for r in rows:
        per_meter.setdefault(r["meter_id"], {})[r["quality"]] = r["n"]

    for mid, stat in list(per_meter.items())[:MAX_PER_RULE]:
        m = EnergyMeter.objects.filter(id=mid).first()
        if m is None or not m.enabled:
            continue
        total = sum(stat.values())
        detail = "、".join(f"{label[q]} {n} 个" for q, n in sorted(stat.items()))
        col.add(
            EnergyAlarm.CAT_HOUR_QUALITY,
            f"HOUR-QUALITY:{m.code}",
            level=EnergyAlarm.LEVEL_MAJOR,
            source=m.name,
            content=f"近 {QUALITY_LOOKBACK_HOURS} 小时有 {total} 个小时差值不可信（{detail}）",
            value=str(total),
            threshold="0",
            meter=m,
            gateway_id=m.gateway_id,
            line_id=m.line_id,
        )


def _rule_gateway(col: _Collector) -> None:
    """最近一轮采集「全部失败」的网关。

    只陈述这一件具体的事，不复用列表页的「在线」判定（原因见模块 docstring）。
    """
    for g in EnergyGateway.objects.filter(enabled=True):
        latest = EnergyCollectLog.objects.filter(gateway=g).order_by("-started_at").first()
        if latest is None or not latest.total or latest.ok_count:
            continue
        col.add(
            EnergyAlarm.CAT_GATEWAY_OFFLINE,
            f"GATEWAY-FAIL:{g.code}",
            level=EnergyAlarm.LEVEL_CRITICAL,
            source=g.name,
            content=f"{g.name} 最近一轮采集 {latest.total} 个点位全部失败"
                    + (f"：{latest.message[:120]}" if latest.message else ""),
            value=f"0/{latest.total}",
            threshold="",
            gateway=g,
        )


def _rule_stale(col: _Collector, now: datetime) -> None:
    """采集整体停摆 —— 最新读数超过阈值没更新。

    这条是「兜底的兜底」：计划任务没跑、容器挂了、网络整段断，
    表现都一样。没有它的话，单表告警会一起冒出来，反而看不出根因是全局的。
    """
    limit = _stale_hours()
    newest = EnergyRaw.objects.filter(ok=True).aggregate(v=Max("collect_time"))["v"]
    if newest is None:
        col.add(
            EnergyAlarm.CAT_COLLECT_STALE,
            "COLLECT-STALE:ALL",
            level=EnergyAlarm.LEVEL_CRITICAL,
            source="采集服务",
            content="尚无任何采集数据 —— 请确认计划任务 EEP-Collect 是否在运行",
            value="",
            threshold=f"{limit} 小时",
        )
        return
    # USE_TZ=False：库里的 collect_time 就是本地朴素时间，直接比即可
    gap_hours = (now - newest).total_seconds() / 3600
    if gap_hours > limit:
        col.add(
            EnergyAlarm.CAT_COLLECT_STALE,
            "COLLECT-STALE:ALL",
            level=EnergyAlarm.LEVEL_CRITICAL,
            source="采集服务",
            content=f"采集疑似中断：最新读数停留在 {_fmt(newest)}，已 {gap_hours:.1f} 小时未更新",
            value=f"{gap_hours:.1f} 小时",
            threshold=f"{limit} 小时",
        )


def _coverage_days(now: datetime) -> set[date_cls]:
    """本轮要复核的日汇总日期：昨天 + 仍挂着的覆盖率告警所属日期。

    只复核昨天会让「换日」变成一次隐式恢复：某天真实缺数 6 小时，第二天凌晨
    规则改看新的一天，旧告警从 live 集合里消失、被标成已恢复 —— 但数据并没有
    被补回来。把仍挂着的历史日期一起复核，恢复才代表问题真的修好了。
    """
    days = {(now - timedelta(days=1)).date()}
    codes = EnergyAlarm.objects.filter(
        category=EnergyAlarm.CAT_COVERAGE,
        status__in=(EnergyAlarm.STATUS_ACTIVE, EnergyAlarm.STATUS_ACKED),
    ).values_list("code", flat=True)
    for code in codes:
        # code = COVERAGE:YYYY-MM-DD:line_id:energy_type_id
        parts = code.split(":")
        if len(parts) != 4:
            continue
        try:
            days.add(datetime.strptime(parts[1], "%Y-%m-%d").date())
        except ValueError:
            continue
    return days


def _rule_coverage(col: _Collector, now: datetime) -> None:
    """已结算周期（昨天）覆盖率不足 —— 日合计里少算了几个小时。

    为什么不靠小时级告警代替：小时告警会随下一轮采集自愈，而「昨天只结算到
    18/24 小时」是既成事实，那个偏小的日合计会一直留在报表里。

    只判已经「在日终结算后重算过」的日行：`refresh_rollups` 每天 00:30 会写一条
    当天行，那只是「到此刻」的快照（当天刚起步时就是 0/24）。次日 00:30 之前
    采集侧每轮都会评估告警，若直接读这一行，每天凌晨都会把所有计量数据误报成
    「昨日仅结算 0/24」，半小时后又自动恢复 —— 告警页会变成日历（B-553）。
    `computed_at` 是「这一行最后一次被重算」的时间，只有它晚于次日 00:30 的
    结算时刻，这一行才是对完整一天的结算结果（门槛为什么不取零点，见
    `COVERAGE_SETTLED_AT` 的说明）。
    """
    for day in sorted(_coverage_days(now)):
        settled_after = datetime.combine(day + timedelta(days=1), COVERAGE_SETTLED_AT)
        rows = EnergyDaily.objects.filter(stat_date=day).select_related("energy_type", "line")
        for r in rows:
            if r.computed_at is None or r.computed_at < settled_after:
                continue
            expected = int(r.expected_count or 0)
            covered = int(r.covered_count or 0)
            if expected <= 0 or covered >= expected:
                continue
            name = r.energy_type.type_name if r.energy_type_id else "能源"
            where = r.line.line_name if r.line_id else "全厂"
            col.add(
                EnergyAlarm.CAT_COVERAGE,
                f"COVERAGE:{r.stat_date:%Y-%m-%d}:{r.line_id or 'ALL'}:{r.energy_type_id}",
                level=EnergyAlarm.LEVEL_MINOR,
                source=f"{where} · {name}",
                content=(
                    f"{where}的{name}昨日仅结算 {covered}/{expected} 小时，"
                    f"日合计比真实值少 {expected - covered} 个小时"
                ),
                value=f"{covered}/{expected}",
                threshold=f"{expected}/{expected}",
                line_id=r.line_id,
            )


# ── 入口 ────────────────────────────────────────────────────


#: 类别 → 规则实现。evaluate(categories=...) 按这个表挑几条跑。
_RULES = {
    EnergyAlarm.CAT_METER_UNCONNECTED: lambda col, now, ctx: _rule_unconnected(col, ctx["meters"], ctx["first_ok"]),
    EnergyAlarm.CAT_METER_FAIL: lambda col, now, ctx: _rule_collect_fail(col, ctx["meters"]),
    EnergyAlarm.CAT_HOUR_QUALITY: lambda col, now, ctx: _rule_hour_quality(col, now),
    EnergyAlarm.CAT_GATEWAY_OFFLINE: lambda col, now, ctx: _rule_gateway(col),
    EnergyAlarm.CAT_COLLECT_STALE: lambda col, now, ctx: _rule_stale(col, now),
    EnergyAlarm.CAT_COVERAGE: lambda col, now, ctx: _rule_coverage(col, now),
}


@transaction.atomic
def evaluate(
    *,
    now: datetime | None = None,
    categories: set[str] | None = None,
    bump: bool = True,
) -> dict:
    """评估一轮告警：新增 / 更新 / 自动恢复。返回计数供日志与巡检用。

    幂等 —— 重复调用只会把 `repeat` 累加、把消失的关掉，不会堆重复行。

    `categories` 限定只跑其中几条。**大屏的读路径只跑「采集停摆」这一条**：
    采集进程一旦死掉，就再没有别的东西会去生成这条告警 —— 而它恰恰是
    最该在界面上看得见的一条（B-511 的原始动机就是「停摆时界面不会有任何提示」）。
    规则实现仍然只有这一份，读路径只是触发它，不另写一套判定。

    `bump=False` 给读路径用：那种调用每 30 秒来一次，不该污染 `repeat`。
    """
    now = now or datetime.now()
    wanted = set(categories) if categories else set(_RULES)
    need_meters = wanted & {EnergyAlarm.CAT_METER_UNCONNECTED, EnergyAlarm.CAT_METER_FAIL}
    ctx: dict = {}
    if need_meters:
        ctx["meters"] = list(
            EnergyMeter.objects.filter(enabled=True).select_related("gateway", "line", "energy_type")
        )
        ctx["first_ok"] = first_data_map(ctx["meters"])

    col = _Collector(now, bump=bump, categories=wanted)
    for cat in _RULES:
        if cat in wanted:
            _RULES[cat](col, now, ctx)
    col.resolve_stale()

    if col.created or col.resolved:
        logger.info(
            "告警评估：新增 %d、更新 %d、恢复 %d（未接入 %d、采集失败 %d、小时异常 %d）",
            col.created,
            col.updated,
            col.resolved,
            len(col.codes.get(EnergyAlarm.CAT_METER_UNCONNECTED, ())),
            len(col.codes.get(EnergyAlarm.CAT_METER_FAIL, ())),
            len(col.codes.get(EnergyAlarm.CAT_HOUR_QUALITY, ())),
        )
    return {
        "created": col.created,
        "updated": col.updated,
        "resolved": col.resolved,
        "active": EnergyAlarm.objects.filter(status=EnergyAlarm.STATUS_ACTIVE).count(),
    }
