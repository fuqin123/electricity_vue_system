"""常驻采集循环（适合手工跑或小规模部署；生产环境用计划任务更省事）。

    python manage.py collect_loop                   每 60 秒采一轮，整点后 30 秒算差值
    python manage.py collect_loop --interval 30     自定义周期

Ctrl+C 优雅退出。日志同时写控制台与 logs/eep-backend.log。
"""

from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand

from apps.energy.services.aggregate import aggregate_hour
from apps.energy.services.collect import collect_round
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
            self.style.SUCCESS(f"开始采集：周期 {interval}s，整点后 {offset}s 计算小时差值。Ctrl+C 退出。")
        )

        while self._running:
            started = time.monotonic()
            try:
                summary = collect_round()
                logger.info("采集完成：%s/%s 成功", summary["ok"], summary["total"])
                self.stdout.write(
                    f"[{datetime.now():%H:%M:%S}] 采集 {summary['ok']}/{summary['total']} 成功"
                    + (f"，失败 {summary['fail']}" if summary["fail"] else "")
                )
                self._maybe_aggregate(offset)
            except Exception as exc:  # noqa: BLE001 —— 采集循环不能因为一次异常退出
                logger.exception("采集轮次异常")
                self.stdout.write(self.style.ERROR(f"采集异常：{exc}"))

            elapsed = time.monotonic() - started
            sleep_for = max(1.0, interval - elapsed)
            waited = 0.0
            # 分片睡眠，保证收到退出信号能及时响应
            while waited < sleep_for and self._running:
                step = min(1.0, sleep_for - waited)
                time.sleep(step)
                waited += step

        self.stdout.write("已停止采集。")

    def _maybe_aggregate(self, offset: int) -> None:
        """每个整点过 offset 秒后，重算上一小时；同一小时只算一次。"""
        now = datetime.now()
        if now.minute != 0 or now.second < offset:
            return
        prev = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        key = f"{prev:%Y-%m-%d %H}"
        if key == self._last_aggregated:
            return
        written = aggregate_hour(prev.date(), prev.hour)
        self._last_aggregated = key
        self.stdout.write(f"   └─ 已计算 {key} 的小时差值（{written} 条）")
