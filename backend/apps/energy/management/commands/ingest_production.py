"""产量入库入口。

产量侧（灌装机计数器 / 装箱机 PLC / MES 班次报工）尚未改造，所以这里现在承担两件事：

  1. **提供正式写入接口** —— 真实数据源接好后，由采集脚本调用本命令（或直接调
     `write_production`）把每小时的产量写进 `eep_hourly_production`，
     无需改任何模型或接口代码。

  2. **写入 null 占位（--demo-null）** —— 让「每个线别每时的产量」这张表有完整结构，
     便于联调与验收。**占位行会明确标 source=demo / quality=nodata 且 output=null**
     （v1.2.1 起统一为 null：0 会被读成「确实没产出」，与「还没接数据」是两回事），
     接口层据此识别「这不是真实产量」，绝不会被误当成真数据。

用法：
    # 正式写入一个点
    python manage.py ingest_production --line A1 --date 2026-09-22 --hour 10 --output 26800

    # 演示占位：把近 2 天所有线别所有已过小时写成 null（marked as demo）
    python manage.py ingest_production --demo-null --days 2

    # 查看当前产量数据情况
    python manage.py ingest_production --status
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from ...models import ProductionHourly, ProductionLine

logger = logging.getLogger(__name__)


def write_production(
    line_code: str,
    day,
    hour: int,
    output: Decimal | None,
    *,
    quality: str = ProductionHourly.Q_OK,
    source: str = ProductionHourly.SOURCE_COUNTER,
    note: str = "",
) -> ProductionHourly:
    """写入（或更新）某线某小时产量。

    ⚠ `output=None` 表示**未上报**，写入的是 null 而不是 0 —— 0 会被当成「确实没产出」。
    """
    if not (0 <= int(hour) <= 23):
        raise ValueError(f"小时取值必须在 0—23，收到 {hour}")
    line = ProductionLine.objects.filter(line_code=line_code.upper()).first()
    if line is None:
        raise ValueError(f"产线不存在：{line_code}")

    obj, _created = ProductionHourly.objects.update_or_create(
        line=line,
        stat_date=day,
        hour=int(hour),
        defaults={
            "output": output,
            "quality": quality,
            "source": source,
            "note": note,
        },
    )
    return obj


class Command(BaseCommand):
    help = "写入小时产量（真实数据源接入后由采集脚本调用；也支持写入演示 null 占位）"

    def add_arguments(self, parser):
        parser.add_argument("--line", help="产线代码，如 A1")
        parser.add_argument("--date", help="日期 YYYY-MM-DD")
        parser.add_argument("--hour", type=int, help="小时 0—23")
        parser.add_argument("--output", help="产量（瓶）；不传写到 null（未上报）")
        parser.add_argument(
            "--demo-null", action="store_true", help="为近 N 天写入 null 占位（标 source=demo / quality=nodata）"
        )
        parser.add_argument("--days", type=int, default=2)
        parser.add_argument("--status", action="store_true", help="只打印当前产量数据情况")

    def handle(self, *args, **opts):
        if opts["status"]:
            self._print_status()
            return

        if opts["demo_null"]:
            self._write_demo_null(opts["days"])
            self._print_status()
            return

        if not (opts["line"] and opts["date"] and opts["hour"] is not None):
            raise CommandError("请给出 --line / --date / --hour，或使用 --demo-null / --status")

        day = datetime.strptime(opts["date"], "%Y-%m-%d").date()
        value = Decimal(opts["output"]) if opts["output"] is not None else None
        row = write_production(opts["line"], day, opts["hour"], value)
        self.stdout.write(
            self.style.SUCCESS(
                f"已写入 {row.line_id} {row.date} {row.hour:02d}时 "
                f"产量 {row.output if row.output is not None else 'null'} 瓶 "
                f"[{row.quality}/{row.source}]"
            )
        )

    # ── 内部 ──────────────────────────────────────────────

    def _write_demo_null(self, days: int) -> None:
        """写入 null 占位。

        ⚠ 为什么写 null 而不是 0（v1.2.1 口径）：
          0 会被当成「确实没产出」，null 才是「还没接数据」。写 null 并标
          `quality=nodata` / `source=demo`，接口层就能把两类行区分开，
          绝不会把占位误当成真实产量。
        """
        now = datetime.now()
        lines = list(ProductionLine.objects.filter(status=ProductionLine.STATUS_ON, line_type=ProductionLine.TYPE_LINE))
        total = 0
        for back in range(days):
            day = (now - timedelta(days=back)).date()
            for line in lines:
                for hour in range(24):
                    if back == 0 and hour > now.hour:
                        continue  # 未到的小时不写字，别制造「未来已有产量」
                    write_production(
                        line.line_code,
                        day,
                        hour,
                        None,
                        quality=ProductionHourly.Q_NODATA,
                        source=ProductionHourly.SOURCE_DEMO,
                        note="演示占位：产量数据源未接入",
                    )
                    total += 1
        self.stdout.write(f"已写入演示占位 {total} 条（{days} 天 × {len(lines)} 线 × 已过小时）")

    def _print_status(self) -> None:
        qs = ProductionHourly.objects.all()
        self.stdout.write("")
        self.stdout.write(f"小时产量记录：{qs.count()} 条")
        for q in ProductionHourly.QUALITY_CHOICES:
            n = qs.filter(quality=q[0]).count()
            if n:
                self.stdout.write(f"  quality={q[0]:<9} {q[1]:<24} {n} 条")
        for s in ProductionHourly.SOURCE_CHOICES:
            n = qs.filter(source=s[0]).count()
            if n:
                self.stdout.write(f"  source ={s[0]:<9} {s[1]:<24} {n} 条")
