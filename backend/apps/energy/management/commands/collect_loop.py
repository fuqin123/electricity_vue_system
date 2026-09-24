"""常驻采集循环（适合手工跑或小规模部署；生产环境用计划任务更省事）。

    python manage.py collect_loop                    按配置节奏跑（默认每小时一次）
    python manage.py collect_loop --interval 30      强制覆盖为每 30 秒一轮

调度规则（v1.2.2 起跟随 `collect.cadence`）：
  · hourly（默认）—— 睡到「整点 + collect.hourly_offset_seconds」采一次，
    然后立刻做差算出**刚结束的那个小时**；
  · minute        —— 每 interval 秒采一轮，整点后补算上一小时。

⚠ 常驻进程会在机器重启 / 进程被杀时中断，那时整点的采样就丢了。
   生产环境**更推荐**用计划任务每 5 分钟跑一次 `collect --aggregate --if-due`：
   它是幂等的，错过一次下一个 5 分钟自动补上。见 backend/docs/采集说明.md。

Ctrl+C 优雅退出。日志同时写控制台与 logs/eep-backend.log。
"""

from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand

from apps.energy.models import PeriodSummary
from apps.energy.services.aggregate import aggregate_hour
from apps.energy.services.collect import collect_round
from apps.energy.services import slots
from apps.energy.services import summary as S
from eep import conf

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "常驻采集：按周期采一轮，整点后补算上一小时差值"

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=None, help="采样周期（秒），默认取配置")
        parser.add_argument("--aggregate-offset", type=int, default=30, help="整点后多少秒算差值")

    def handle(self, *args, **options):
        interval = int(options["interval"] or conf.get("collect.interval_seconds", 60))
        offset = int(options["aggregate_offset"])
        self._running = True
        self._last_aggregated: str | None = None

        def stop(signum, frame):  # noqa: ANN001, ARG001
            self.stdout.write("\n收到退出信号，正在停止…")
            self._running = False

        signal.signal(signal.SIGINT, stop)
        try:
            signal.signal(signal.SIGTERM, stop)
        except (AttributeError, ValueError):  # Windows 上 SIGTERM 支持有限
            pass

        self.stdout.write(
            self.style.SUCCESS(f"开始采集：节奏 {slots.describe()}。Ctrl+C 退出。")
        )

        hourly = slots.cadence() == slots.CADENCE_HOURLY and options["interval"] is None

        while self._running:
            # hourly 节奏：先睡到「整点 + 偏移」，这样每个小时的差值区间严格等于 1 小时。
            # 若按固定 interval 睡，采样时刻会随轮次耗时逐轮漂移，小时值会稳定偏一点。
            if hourly:
                self._wait_until(slots.next_run_at())

            started = time.monotonic()
            try:
                summary = collect_round()
                logger.info("采集完成：%s/%s 成功", summary["ok"], summary["total"])
                self.stdout.write(
                    f"[{datetime.now():%H:%M:%S}] 采集 {summary['ok']}/{summary['total']} 成功"
                    + (f"，失败 {summary['fail']}" if summary["fail"] else "")
                )
                self._aggregate_after_collect()
            except Exception as exc:  # noqa: BLE001 —— 采集循环不能因为一次异常退出
                logger.exception("采集轮次异常")
                self.stdout.write(self.style.ERROR(f"采集异常：{exc}"))

            if not hourly:
                elapsed = time.monotonic() - started
                self._wait_until(datetime.now() + timedelta(seconds=max(1.0, interval - elapsed)))

        self.stdout.write("已停止采集。")

    def _wait_until(self, moment: datetime) -> None:
        """睡到指定时刻（分片睡眠，保证收到退出信号能及时响应）。"""
        while self._running:
            remain = (moment - datetime.now()).total_seconds()
            if remain <= 0:
                return
            time.sleep(min(1.0, remain))

    def _aggregate_after_collect(self) -> None:
        """采完立刻做差，算出「刚结束的那个小时」。

        为什么是「刚结束的那个小时」：hourly 节奏下，本次在 HH:01 采到的读数是
        「HH:00 这个边界」的值，也就是 **HH−1 点这一小时**的终点。
        所以此刻能算出来的就是 HH−1 点。
        同时把再往前一小时也重算一遍 —— 上一轮若迟到或失败，顺手修掉，成本极低。

        做完差值顺手刷周期总计：大屏与后台的日/月/年 KPI 都读它，
        不刷的话界面上的日月年数字会一直停在上一次刷新。
        """
        now = datetime.now()
        prev = slots.hour_start(now) - timedelta(hours=1)
        key = f"{prev:%Y-%m-%d %H}"

        written = aggregate_hour(prev.date(), prev.hour)
        earlier = prev - timedelta(hours=1)
        written += aggregate_hour(earlier.date(), earlier.hour)
        self.stdout.write(f"   └─ 小时差值：{key} 共 {written} 条")

        rows = S.summarize_days(prev.date(), now)
        rows += S.summarize_periods(S.DIM_MONTH, S.recent_months(2, now), now)
        rows += S.summarize_periods(S.DIM_YEAR, S.recent_years(1, now), now)
        self.stdout.write(f"   └─ 已刷新周期总计（{len(rows)} 条）")
