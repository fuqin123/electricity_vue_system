"""手工/计划任务评估告警：`python manage.py check_alerts`

采集命令每轮结束已经会顺带评估一次，所以本命令主要用于三种场合：

  1. **只查不采**：想确认「现在到底有哪些告警」，但不想去碰设备
     （`collect` 会真的发起 Modbus / HTTP 请求）。
  2. **独立计划任务**：现场若希望告警与采集解耦（比如采集 5 分钟一轮、
     告警 10 分钟一轮），单独排本任务即可。
  3. **升级后补算**：新增规则后想让历史数据立刻参与判定，跑一次就行 ——
     所有规则都是幂等的，重复跑只会累加 `repeat`。

用法：
    python manage.py check_alerts            评估并打印未处理告警
    python manage.py check_alerts --json     给巡检脚本用
    python manage.py check_alerts --quiet    只评估不打印（配 cron 静默跑）
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.energy.models import EnergyAlarm
from apps.energy.services import alerts


class Command(BaseCommand):
    help = "评估一轮能源计量告警（幂等；可只查不采）"

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="以 JSON 输出（给巡检脚本用）")
        parser.add_argument("--quiet", action="store_true", help="只评估，不打印告警明细")

    def handle(self, *args, **options):
        res = alerts.evaluate()

        if options["json"]:
            self.stdout.write(json.dumps(res, ensure_ascii=False))
            return

        self.stdout.write(
            f"告警评估：新增 {res['created']}、更新 {res['updated']}、"
            f"自动恢复 {res['resolved']}、当前未处理 {res['active']}"
        )
        if options["quiet"]:
            return

        rows = EnergyAlarm.objects.filter(
            status__in=(EnergyAlarm.STATUS_ACTIVE, EnergyAlarm.STATUS_ACKED)
        ).order_by("-last_at")
        if not rows:
            self.stdout.write("  （无未处理告警）")
            return
        for a in rows:
            mark = {"critical": "!!", "major": "! ", "minor": "* ", "info": "  "}.get(a.level, "  ")
            acked = "（已确认）" if a.status == EnergyAlarm.STATUS_ACKED else ""
            self.stdout.write(
                f"  {mark} [{a.get_level_display()}] {a.source} · {a.content}{acked}"
                f" ×{a.repeat}"
            )
