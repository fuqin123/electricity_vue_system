"""算小时差值（可重复执行，幂等）。

    python manage.py aggregate --recent 3           重算最近 3 个已结束的小时（推荐）
    python manage.py aggregate --date 2026-09-22    重算某一天全部 24 小时
    python manage.py aggregate --date 2026-09-22 --hours 8-14   只重算某几小时

用「上小时末读数 − 本小时末读数」得出每小时用量，异常处理口径见 services/aggregate.py。
算法改了以后直接重跑即可，原始读数都在，不用等现场重新采。
"""

from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from apps.energy.models import HourlyUsage, Meter
from apps.energy.services.aggregate import aggregate_day, aggregate_hour, aggregate_recent


class Command(BaseCommand):
    help = "按原始读数计算小时用量（差值）"

    def add_arguments(self, parser):
        parser.add_argument("--recent", type=int, help="重算最近 N 个已结束的小时")
        parser.add_argument("--date", help="重算指定日期 YYYY-MM-DD 的全部小时")
        parser.add_argument("--hours", help="配合 --date 使用，如 8-14 或 8,9,10")
        parser.add_argument("--meter", help="只算某个计量点")
        parser.add_argument("--show", action="store_true", help="算完打印结果明细")

    def handle(self, *args, **options):
        meters = None
        if options.get("meter"):
            meter = Meter.objects.filter(code=options["meter"]).first()
            if meter is None:
                raise CommandError(f"计量点不存在：{options['meter']}")
            meters = [meter]

        written = 0
        if options.get("recent"):
            written = aggregate_recent(hours=int(options["recent"]), meters=meters)
            scope = f"最近 {options['recent']} 个已结束的小时"
        elif options.get("date"):
            day = parse_date(options["date"])
            if day is None:
                raise CommandError("--date 格式应为 YYYY-MM-DD")
            hours = self._parse_hours(options.get("hours"))
            written = aggregate_day(day, hours=hours, meters=meters)
            scope = f"{day} 的 {len(list(hours))} 个小时"
        else:
            # 默认：算上一个已结束的小时（整点后定时任务用这个最稳）
            now = datetime.now()
            back = now.replace(minute=0, second=0, microsecond=0)
            from datetime import timedelta

            prev = back - timedelta(hours=1)
            written = aggregate_hour(prev.date(), prev.hour, meters=meters)
            scope = f"上一个整点小时 {prev:%Y-%m-%d %H}"

        self.stdout.write(self.style.SUCCESS(f"已重算 {scope}，覆盖 {written} 条小时记录"))

        if options.get("show"):
            from apps.energy.models import HourlyUsage as HU

            qs = HU.objects.select_related("meter").order_by("-date", "-hour", "meter__code")[:40]
            flag = {"ok": "  ", "partial": "~ ", "missing": "× ", "reset": "R ", "spike": "! "}
            self.stdout.write(f"{'计量点':<12}{'日期':<12}{'时':<4}{'用量':>12}  {'质量':<9}说明")
            for r in qs:
                usage = f"{r.usage}" if r.usage is not None else "—"
                self.stdout.write(
                    f"{r.meter.code:<12}{r.date}  {r.hour:>2}  {usage:>12}  "
                    f"{flag.get(r.quality, '')}{r.quality:<9}{r.note[:44]}"
                )

    @staticmethod
    def _parse_hours(raw: str | None):
        if not raw:
            return range(24)
        if "-" in raw:
            a, b = raw.split("-", 1)
            return range(int(a), int(b) + 1)
        return [int(x) for x in raw.split(",")]
