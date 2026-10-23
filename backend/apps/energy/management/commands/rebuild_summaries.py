"""按**生产日 06:00—次日 06:00** 重建日 / 月 / 年汇总。

v1.6.0 把「日」的边界从自然日改成生产日，旧汇总行（自然日口径）不能继续用。
本命令清空 `energy_daily` / `energy_monthly` / `energy_yearly`，再从小时表重算。
**原始读数、小时差值表、产量表都不动**；日报 / 月报可以丢，源数据不能丢。

用法：
    python manage.py rebuild_summaries                 # 从最早小时数据重建到当前生产日
    python manage.py rebuild_summaries --from 2026-09-23
    python manage.py rebuild_summaries --dry-run       # 只显示将重建的范围
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Min

from ...models import EnergyDaily, EnergyHourly, EnergyMonthly, EnergyYearly
from ...services import alerts
from ...services import business_day as B
from ...services import summary as S


class Command(BaseCommand):
    help = "清空并按生产日（06:00—次日06:00）重建日/月/年汇总"

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="from_date", help="起始生产日 YYYY-MM-DD（默认取最早小时数据）")
        parser.add_argument("--to", dest="to_date", help="结束生产日 YYYY-MM-DD（默认当前生产日）")
        parser.add_argument("--dry-run", action="store_true", help="只打印范围，不删除、不写库")

    def handle(self, *args, **opts):
        now = datetime.now()
        to_day = self._parse(opts.get("to_date")) or B.production_day(now)
        from_day = self._parse(opts.get("from_date"))
        if from_day is None:
            earliest = EnergyHourly.objects.aggregate(d=Min("stat_date"))["d"]
            from_day = earliest or (to_day - timedelta(days=30))
        if from_day > to_day:
            from_day, to_day = to_day, from_day

        days = [from_day + timedelta(days=i) for i in range((to_day - from_day).days + 1)]
        months = self._months(from_day, to_day)
        years = [date_cls(y, 1, 1) for y in range(from_day.year, to_day.year + 1)]
        self.stdout.write(
            f"生产日范围：{from_day} → {to_day}（{len(days)} 天）；月 {len(months)} 个；年 {len(years)} 个"
        )
        if opts["dry_run"]:
            self.stdout.write("dry-run：未清空、未写库")
            return

        with transaction.atomic():
            # 汇总表是派生数据，清空后可从小时表完整重建；源表不在删除范围内。
            deleted = (
                EnergyDaily.objects.all().delete()[0]
                + EnergyMonthly.objects.all().delete()[0]
                + EnergyYearly.objects.all().delete()[0]
            )
            self.stdout.write(f"已清空汇总行：{deleted} 条")

            day_rows = 0
            for day in days:
                day_rows += len(S.summarize_days(day, now))
            self.stdout.write(f"日汇总：{day_rows} 条")

            month_rows = len(S.summarize_periods(S.DIM_MONTH, months, now))
            year_rows = len(S.summarize_periods(S.DIM_YEAR, years, now))
            self.stdout.write(f"月汇总：{month_rows} 条；年汇总：{year_rows} 条")

        res = alerts.evaluate(now=now)
        self.stdout.write(
            self.style.SUCCESS(
                f"重建完成；告警：新增 {res['created']}、恢复 {res['resolved']}、未处理 {res['active']}"
            )
        )

    @staticmethod
    def _parse(raw: str | None) -> date_cls | None:
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(f"日期必须是 YYYY-MM-DD，收到 {raw}") from exc

    @staticmethod
    def _months(start: date_cls, end: date_cls) -> list[date_cls]:
        out: list[date_cls] = []
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            out.append(date_cls(y, m, 1))
            m += 1
            if m > 12:
                y, m = y + 1, 1
        return out
