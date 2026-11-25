"""清空能源与产量数据（测试环境用，v1.8.5）。

保留主数据：产线、计量点/网关、PLC 设备/点位、绑定关系、单价与标箱规则、
用户与系统参数都不动；只删**采集与统计结果**，让最新版本从零开始重新采集。

    python manage.py clear_measurements --yes

清理范围：
  · 能源：原始读数、小时差值、日/月/年汇总、采集日志、告警、复核记录
  · 产量：PLC 原始读数、小时产量、计数参量、标箱产量
并重置计量点 / PLC 点位的「最近读数」基准，避免清空后第一次差值是旧数据算出来的。

⚠ 这是**不可恢复**的操作。执行前请自行备份（本命令不负责备份）。
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ...models import (
    EnergyAlarm,
    EnergyCollectLog,
    EnergyDaily,
    EnergyGateway,
    EnergyHourly,
    EnergyMeter,
    EnergyMonthly,
    EnergyRaw,
    EnergyReviewLog,
    EnergyYearly,
    PlcPoint,
    PlcReading,
    ProductionCaseHourly,
    ProductionCounterHourly,
    ProductionHourly,
)


class Command(BaseCommand):
    help = "清空能源与产量采集/统计数据（保留主数据），用于测试环境重新统计"

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="确认执行（不加此参数只显示将要清理的数据量）")

    def handle(self, *args, **opts):
        targets = [
            ("能源原始读数", EnergyRaw),
            ("能源小时差值", EnergyHourly),
            ("能源日汇总", EnergyDaily),
            ("能源月汇总", EnergyMonthly),
            ("能源年汇总", EnergyYearly),
            ("采集日志", EnergyCollectLog),
            ("能源告警", EnergyAlarm),
            ("复核记录", EnergyReviewLog),
            ("PLC 原始读数", PlcReading),
            ("小时产量", ProductionHourly),
            ("产量计数参量", ProductionCounterHourly),
            ("标箱产量", ProductionCaseHourly),
        ]
        counts = [(label, model, model.objects.count()) for label, model in targets]
        total = sum(c for _l, _m, c in counts)
        for label, _model, count in counts:
            self.stdout.write(f"  {label}：{count} 条")
        self.stdout.write(f"合计 {total} 条")

        if not opts["yes"]:
            self.stdout.write(self.style.WARNING("未加 --yes：以上数据未删除"))
            return

        with transaction.atomic():
            for _label, model, _count in counts:
                model.objects.all().delete()

            # 清空后不能沿用旧基准：否则第一次采集会把「清空前 → 清空后」的
            # 累计差当成这一小时的用量/产量，得到一条巨大的假数据。
            EnergyMeter.objects.update(
                last_value=None, last_at=None, last_ok=False, last_error=""
            )
            PlcPoint.objects.update(
                last_value=None, last_at=None, last_ok=None, last_error=""
            )
            EnergyGateway.objects.update(last_ok_at=None, last_fail_at=None)

        self.stdout.write(self.style.SUCCESS(f"已清空 {total} 条能源/产量数据，并重置采集基准"))
