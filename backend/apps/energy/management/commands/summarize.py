"""生成 / 刷新周期总计（日、月、年），落到 `energy_daily` / `energy_monthly` / `energy_yearly`。

★ 常态用法 = **每天结算一次**（v1.4.2 起，配 00:30 的计划任务）：

    python manage.py summarize --rollup

  `--rollup` 走 `summary.refresh_rollups()` 这一个入口：昨天 + 今天 + 近 2 月 + 今年。
  必须包含昨天，因为昨天 23 点的差值要等今天 00:04 采到跨日边界读数才算得出来
  （只刷今天 = 每一天永远少最后一个小时，B-523）。
  今天这一行照写，但它只是「结算到此刻」的快照：界面上的当期数字
  一律从小时表现算（见 `summary.is_open_period`），不依赖这一行。

  采集侧（`collect --aggregate` / `collect_loop`）**不再每小时刷汇总**。

其它用法（手工回填 / 换算法后重跑）：
    python manage.py summarize                     # 近 2 天 + 近 13 个月 + 近 6 年
    python manage.py summarize --dim day --days 30 # 只重刷近 30 天的日总计
    python manage.py summarize --show              # 顺带打印结果
"""

from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand

from ...models import PeriodSummary
from ...services import alerts
from ...services import summary as S


class Command(BaseCommand):
    help = "生成或刷新日 / 月 / 年周期总计（eep_period_summary）"

    def add_arguments(self, parser):
        parser.add_argument("--dim", default="all", choices=["day", "month", "year", "all"])
        parser.add_argument("--days", type=int, default=2, help="刷最近多少天的日总计")
        parser.add_argument("--months", type=int, default=13, help="覆盖最近多少个月")
        parser.add_argument("--years", type=int, default=6, help="覆盖最近多少年")
        parser.add_argument(
            "--rollup",
            action="store_true",
            help="跑运维滚动窗口（昨天 + 今天 + 近 2 月 + 今年）—— 每天 00:30 的计划任务用这个",
        )
        parser.add_argument("--show", action="store_true", help="打印生成结果")

    def handle(self, *args, **opts):
        now = datetime.now()

        if opts["rollup"]:
            # 每天一次的结算入口。窗口为什么含昨天，见 refresh_rollups 的说明（B-523）
            written = S.refresh_rollups(now)
            self.stdout.write(
                self.style.SUCCESS(f"滚动窗口结算完成（昨天 + 今天 + 近 2 月 + 今年）：{len(written)} 条")
            )
            # 结算完立刻评估：覆盖率规则读的就是刚写下的这批昨日日汇总行。
            # 等下一轮采集才报会白挂几个小时，而 00:30 正是值班交接前
            # 最该看到「昨天哪些数不完整」的时刻。
            res = alerts.evaluate(now=now)
            self.stdout.write(
                f"告警：新增 {res['created']}、恢复 {res['resolved']}、未处理 {res['active']}"
            )
            return

        dim = opts["dim"]
        touched: list[PeriodSummary] = []

        if dim in ("day", "all"):
            days = S.recent_days(opts["days"], now)
            for d in days:
                touched.extend(S.summarize_days(d, now))
            self.stdout.write(f"日总计：{len(days)} 天 × (6 线 + 车间) → {len(touched)} 条")

        if dim in ("month", "all"):
            days = S.recent_months(opts["months"], now)
            rows = S.summarize_periods(S.DIM_MONTH, days, now)
            touched.extend(rows)
            self.stdout.write(f"月总计：{len(rows)} 条")

        if dim in ("year", "all"):
            days = S.recent_years(opts["years"], now)
            rows = S.summarize_periods(S.DIM_YEAR, days, now)
            touched.extend(rows)
            self.stdout.write(f"年总计：{len(rows)} 条")

        if opts["show"]:
            self.stdout.write("")
            for r in touched:
                if r.dim != S.DIM_DAY or r.scope != PeriodSummary.SCOPE_WORKSHOP:
                    continue
                self.stdout.write(
                    f"  {r.period_key} 车间  产量 {r.output} 瓶  电量 {r.kwh} kWh  "
                    f"单耗 {r.unit_kwh if r.unit_kwh is not None else '--'}  "
                    f"覆盖 {r.covered_hours}h [{r.quality}]"
                )

        self.stdout.write(self.style.SUCCESS(f"完成，共写入 {len(touched)} 条周期总计"))
