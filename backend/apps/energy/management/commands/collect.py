"""采集一轮（13 路电表 + 蒸汽总表）。

用法：
    python manage.py collect                   采全部启用网关
    python manage.py collect --gateway GW-PDB-1 只采某个网关
    python manage.py collect --dry-run          只读不写库（现场接线排障用）
    python manage.py collect --aggregate        采完顺手算最近 2 小时的小时差值
    python manage.py collect --aggregate --if-due       仅当本小时还没采过时才采（推荐配 cron）
    python manage.py collect --aggregate --no-summary   采完不刷周期总计

★ 现场推荐用法（hourly 节奏）：计划任务每 5 分钟跑一次
      python manage.py collect --aggregate --if-due
  `--if-due` 会先检查本小时的采样是否已经完成：
  已完成就直接退出，所以「每 5 分钟跑一次」等价于「每小时采一次」，
  而万一某次因为机器重启 / 网络抖动没跑成，下一个 5 分钟会自动补上 ——
  比「每小时准点跑一次、错过就丢一小时」可靠得多。

⚠ 建议保持 `--summarize` 默认开启：
  大屏与后台的「年 / 月 / 日」KPI 都读 `eep_period_summary`，
  采完不刷新汇总表，界面上的日月年数字就会落后于小时数据。

生产环境建议每 60 秒跑一次（Windows 计划任务 / Linux cron / APScheduler 均可），
比常驻进程更抗崩溃：
    * * * * *  cd /opt/eep/backend && /opt/eep/venv/bin/python manage.py collect --aggregate
"""

from __future__ import annotations

import json
from datetime import datetime

from django.core.management.base import BaseCommand

from apps.energy.services.aggregate import aggregate_recent
from apps.energy.services.collect import collect_round
from apps.energy.models import Meter, PeriodSummary, Reading
from apps.energy.services import slots
from apps.energy.services import summary as S


class Command(BaseCommand):
    help = "采集一轮能源数据（原始累计值），可选顺带算小时差值"

    def add_arguments(self, parser):
        parser.add_argument("--gateway", action="append", help="只采指定网关编号，可重复")
        parser.add_argument("--dry-run", action="store_true", help="只读不写库")
        parser.add_argument("--aggregate", action="store_true", help="采完算最近 2 小时差值")
        parser.add_argument("--json", action="store_true", help="以 JSON 输出汇总（给脚本用）")
        parser.add_argument(
            "--no-summary", action="store_true", help="不刷新日/月/年总计（默认会刷）"
        )
        parser.add_argument(
            "--if-due",
            action="store_true",
            help="仅当本小时的采样尚未完成时才采（幂等；配 cron 每 5 分钟跑一次即可）",
        )

    def handle(self, *args, **options):
        if options["if_due"] and not options["dry_run"] and not self._is_due(options.get("gateway")):
            self.stdout.write(
                f"本小时的采样已完成，跳过（节奏：{slots.describe()}）。"
                "需要强制重采请去掉 --if-due。"
            )
            return

        summary = collect_round(gateway_codes=options.get("gateway"), dry_run=options["dry_run"])

        if options["json"]:
            self.stdout.write(json.dumps(summary, ensure_ascii=False))
        else:
            for gw in summary["gateways"]:
                flag = "OK " if gw["fail"] == 0 else "!! "
                self.stdout.write(
                    f"{flag}{gw['gateway']:<10} {gw['endpoint']:<22} "
                    f"{gw['ok']}/{gw['total']} 成功  {gw['duration_ms']}ms"
                )
                for err in gw["errors"]:
                    self.stdout.write(self.style.WARNING(f"     └─ {err}"))
            self.stdout.write(
                f"合计：{summary['ok']}/{summary['total']} 个点位成功"
                + ("（dry-run，未写库）" if summary["dry_run"] else f"，写入 {summary['readings']} 条原始读数")
            )

        if options["aggregate"] and not options["dry_run"]:
            written = aggregate_recent(hours=2)
            self.stdout.write(f"小时差值：重算最近 2 小时，写入 {written} 条")

        # 刷新周期总计（大屏与后台的日月年 KPI 都读它）
        if not options["dry_run"] and not options["no_summary"]:
            now = datetime.now()
            written = S.summarize_days(now.date(), now)
            written += S.summarize_periods(PeriodSummary.DIM_MONTH, S.recent_months(2, now), now)
            written += S.summarize_periods(PeriodSummary.DIM_YEAR, S.recent_years(1, now), now)
            self.stdout.write(f"周期总计：刷新今天 + 近 2 月 + 今年，共 {len(written)} 条")

        # 有失败时返回非 0，方便计划任务判定
        if summary["fail"]:
            raise SystemExit(2)

    # ── 幂等守卫 ──────────────────────────────────────────

    def _is_due(self, gateway_codes) -> bool:
        """本小时的采样是否还没完成。

        判定标准：**每一个启用中的计量点**在本小时都已经有一条读数。
        用「全部」而不是「任意」，是为了容忍采集中途被打断 ——
        那时只有部分点位有读数，下次运行会把整轮重跑一遍
        （多出来的读数无害：差值取的是边界前最后一条）。
        """
        now = datetime.now()
        b = slots.hour_start(now)
        ms = Meter.objects.filter(enabled=True)
        if gateway_codes:
            ms = ms.filter(gateway__code__in=gateway_codes)
        ms = list(ms)
        if not ms:
            return True
        done = set(
            Reading.objects.filter(meter__in=ms, at__gte=b, ok=True).values_list("meter_id", flat=True)
        )
        return len(done) < len(ms)
