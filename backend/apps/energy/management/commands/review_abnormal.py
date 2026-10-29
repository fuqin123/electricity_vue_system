"""执行异常时段人工复核（每天一次，时间由系统参数决定）。

review 容器每分钟调用一次；`alerts._rule_hour_deviation` 自己判断是否到达
`data_quality.review_hour`、以及今天是否已经执行过（EnergyReviewLog），
因此本命令可安全重复调用，不需要额外的 --if-due 参数。

未执行复核时，partial 小时仍已按正常口径参与汇总与展示；复核只负责生成
“差值 / 时段值”告警供人工确认，不会把数据藏起来。
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from ...models import EnergyAlarm
from ...services import alerts


class Command(BaseCommand):
    help = "执行异常时段人工复核（可重复调用；每天只执行一次）"

    def handle(self, *args, **opts):
        res = alerts.evaluate(categories={EnergyAlarm.CAT_HOUR_DEVIATION})
        self.stdout.write(
            f"异常时段复核：新增 {res['created']}、恢复 {res['resolved']}、未处理 {res['active']}"
        )
