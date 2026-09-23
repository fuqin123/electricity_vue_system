"""采样时刻（slot）—— 把「什么时候采、采到的数代表哪个边界」这件事固化在这里。

这是 v1.2.2 的核心约定，改之前务必先读完本节。

## 为什么要单独一层

采集节奏与差值口径是强耦合的：**采样点落在哪里，差值区间就是哪里**。
如果采样节奏在代码各处零散判断，很容易出现「晚了一个小时」「区间比 1 小时长 1 分钟」
这类问题 —— 而且它们在界面上只表现为「数字略有偏差」，极难发现。

## 约定（hourly 节奏，现场默认）

设整点 B（如 15:00）、整点后偏移 O 秒（配置 `collect.hourly_offset_seconds`，默认 60）：

    · **B + O 采到的读数，就是「B 这个边界」的读数**，记作 read(B)。
    · `usage(H) = read(H+1:00) − read(H:00)`

    每个小时的值实际覆盖 `[H:00+O, (H+1):00+O]`，即整体后移 O 秒。
    这是离散采样的固有性质（不可能在整点瞬间取到值），O 取小一点即可忽略；
    关键是**两个端点偏移相同**，所以区间严格等于 1 小时，不会累积偏差。

## 为什么不用「每 60 秒采一次」再取最近点

密集采样时端点是「各自边界前最后一条读数」，而两条读数之间的间隔
会随采集轮次耗时的漂移而偏离 1 小时（实测一轮 1.4 秒，跑上一小时就能偏出十几秒），
于是每个小时都被系统性地少算或多数一点点 —— 单看一小时看不出来，
但月累计、年累计会稳定偏掉。

## hourly 节奏的代价（必须让现场知道）

1. **一次采样失败会连带毁掉两个小时的值**（前一个小时的 end 与后一个小时的 start 是同一条读数）；
2. **当前小时在下一个整点采样之前算不出值**，界面会显示「待整点采集」而不是一个数。

如果现场更需要「随时能看到本小时的参考值」，把 `collect.cadence` 改成 `minute`
（配合 `interval_seconds`）即可，代价是上面那点相位偏差。
两种节奏的差值口径完全一致，切换不需要改代码。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from eep import conf

CADENCE_HOURLY = "hourly"
CADENCE_MINUTE = "minute"


def cadence() -> str:
    """采集节奏：hourly（每小时一次，现场默认）/ minute（每 N 秒一次）。"""
    value = str(conf.get("collect.cadence", CADENCE_HOURLY) or CADENCE_HOURLY).lower()
    return value if value in (CADENCE_HOURLY, CADENCE_MINUTE) else CADENCE_HOURLY


def hourly_offset_seconds() -> int:
    """整点后多少秒采样。留出几十秒是为了避开整点瞬间的采集竞争与表内累计刷新。"""
    return max(0, int(conf.get("collect.hourly_offset_seconds", 60) or 0))


def minute_interval_seconds() -> int:
    return max(5, int(conf.get("collect.interval_seconds", 60) or 60))


def hour_start(moment: datetime) -> datetime:
    """所在小时的整点。"""
    return moment.replace(minute=0, second=0, microsecond=0)


def slot_ready_at(boundary: datetime) -> datetime:
    """某个边界（整点）的采样时刻 = 整点 + 偏移。"""
    return boundary + timedelta(seconds=hourly_offset_seconds())


def due_slot(now: datetime | None = None) -> datetime | None:
    """当前应当采样的「边界整点」；None 表示本小时的采样窗口还没到。

    hourly 节奏下：整点后过了 offset 秒就返回该整点，否则返回 None。
    minute 节奏下：永远返回本小时整点（表示「随时可以采」）。
    """
    now = now or datetime.now()
    b = hour_start(now)
    if cadence() == CADENCE_MINUTE:
        return b
    return b if now >= slot_ready_at(b) else None


def next_run_at(now: datetime | None = None) -> datetime:
    """下一次应当唤起采集的时刻（供常驻循环计算 sleep 时长）。"""
    now = now or datetime.now()
    b = hour_start(now)
    if cadence() == CADENCE_MINUTE:
        return now + timedelta(seconds=minute_interval_seconds())

    target = slot_ready_at(b)
    if now < target:
        return target
    return slot_ready_at(b + timedelta(hours=1))


def slot_window(boundary: datetime, grace_seconds: int = 300) -> tuple[datetime, datetime]:
    """某个边界采样时刻的容忍窗口 —— 用来判断「这个小时是不是已经采过了」。

    窗口卡在 [offset + 1s, offset + grace]：
      · 下界用 +1 秒，避开「上一次采样的读数恰好落在 offset 内」的边界歧义；
      · 上界给 5 分钟，容忍调度延迟（cron 每 5 分钟跑一次的场景）。
    """
    start = slot_ready_at(boundary) + timedelta(seconds=1)
    return start, start + timedelta(seconds=max(0, grace_seconds))


def describe() -> str:
    """一行人类可读的节奏说明，给启动日志与接口用。"""
    if cadence() == CADENCE_HOURLY:
        return f"每小时一次（整点后 {hourly_offset_seconds()} 秒采样，随后做差得出上一小时用量）"
    return f"每 {minute_interval_seconds()} 秒一次（密采样，端点为各边界前最后一条读数）"
