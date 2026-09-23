"""生成 / 刷新周期总计（日、月、年），落到 `eep_period_summary`。

大屏与后台的「年 / 月 / 日」KPI 与趋势图都读这张表，所以**每次采集完成后应跑一次日汇总**，
月 / 年可以在整点之后按较低频率跑（或由系统计划任务每小时跑一次 all）。

用法：
    python manage.py summarize                     # 近 2 天 + 近 13 个月 + 近 6 年
    python manage.py summarize --dim day --days 7  # 只刷近 7 天的日总计
    python manage.py summarize --dim all --days 30 --months 13 --years 6
    python manage.py summarize --show              # 顺带打印结果
"""

from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand

from ...models import PeriodSummary
from ...services import summary as S


class Command(BaseCommand):
    help = "生成或刷新日 / 月 / 年周期总计（eep_period_summary）"

    def add_arguments(self, parser):
        parser.add_argument("--dim", default="all", choices=["day", "month", "year", "all"])
        parser.add_argument("--days", type=int, default=2, help="刷最近多少天的日总计")
        parser.add_argument("--months", type=int, default=13, help="覆盖最近多少个月")
        parser.add_argument("--years", type=int, default=6, help="覆盖最近多少年")
        parser.add_argument("--show", action="store_true", help="打印生成结果")

    def handle(self, *args, **opts):
        now = datetime.now()
        dim = opts["dim"]
        touched: list[PeriodSummary] = []

        if dim in ("day", "all"):
            days = S.recent_days(opts["days"], now)
            for d in days:
                touched.extend(S.summarize_days(d, now))
            self.stdout.write(f"日总计：{len(days)} 天 × (6 线 + 车间) → {len(touched)} 条")

        if dim in ("month", "all"):
            days = S.recent_months(opts["months"], now)
            rows = S.summarize_periods(PeriodSummary.DIM_MONTH, days, now)
            touched.extend(rows)
            self.stdout.write(f"月总计：{len(rows)} 条")

        if dim in ("year", "all"):
            days = S.recent_years(opts["years"], now)
            rows = S.summarize_periods(PeriodSummary.DIM_YEAR, days, now)
            touched.extend(rows)
            self.stdout.write(f"年总计：{len(rows)} 条")

        if opts["show"]:
            self.stdout.write("")
            for r in touched:
                if r.dim != PeriodSummary.DIM_DAY or r.scope != PeriodSummary.SCOPE_WORKSHOP:
                    continue
                self.stdout.write(
                    f"  {r.period_key} 车间  产量 {r.output} 瓶  电量 {r.kwh} kWh  "
                    f"单耗 {r.unit_kwh if r.unit_kwh is not None else '--'}  "
                    f"覆盖 {r.covered_hours}h [{r.quality}]"
                )

        self.stdout.write(self.style.SUCCESS(f"完成，共写入 {len(touched)} 条周期总计"))
