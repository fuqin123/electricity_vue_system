"""清理过期的原始读数。

    python manage.py purge_readings --days 180          删除 180 天前的原始读数
    python manage.py purge_readings --days 180 --dry-run 只统计不删

为什么可以删原始读数：**小时差值（EnergyHourly）是永久保留的**，
大屏和报表都只读差值表；原始读数的作用是「出问题时能复查、算法改了能重算」，
保留半年足够覆盖排查与回归需求。

⚠ 本命令只删 eep_reading 表，不碰小时用量，也不做任何库级操作。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand

from apps.energy.models import EnergyHourly, EnergyRaw
from eep import conf


class Command(BaseCommand):
    help = "清理超期原始读数（小时用量永久保留）"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None, help="保留天数，默认取配置")
        parser.add_argument("--dry-run", action="store_true", help="只统计不删除")

    def handle(self, *args, **options):
        days = int(options["days"] or conf.get("collect.readings_retention_days", 180))
        cutoff = datetime.now() - timedelta(days=days)
        qs = EnergyRaw.objects.filter(collect_time__lt=cutoff)
        count = qs.count()

        if options["dry_run"]:
            self.stdout.write(f"（dry-run）{cutoff:%Y-%m-%d %H:%M} 之前的原始读数共 {count} 条，未删除")
            return

        if count == 0:
            self.stdout.write("没有需要清理的原始读数。")
            return

        # 分批删除，避免一次删太多把库锁住
        deleted = 0
        batch = 5000
        while True:
            ids = list(qs.values_list("id", flat=True)[:batch])
            if not ids:
                break
            EnergyRaw.objects.filter(id__in=ids).delete()
            deleted += len(ids)
            self.stdout.write(f"  已删除 {deleted}/{count}")

        self.stdout.write(self.style.SUCCESS(f"清理完成：删除 {deleted} 条 {cutoff:%Y-%m-%d} 之前的原始读数"))
