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
from apps.energy.models import EnergyMeter, PeriodSummary, EnergyRaw
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
            written += S.summarize_periods(S.DIM_MONTH, S.recent_months(2, now), now)
            written += S.summarize_periods(S.DIM_YEAR, S.recent_years(1, now), now)
            self.stdout.write(f"周期总计：刷新今天 + 近 2 月 + 今年，共 {len(written)} 条")

        # 有失败时返回非 0，方便计划任务判定
        if summary["fail"]:
            raise SystemExit(2)

    # ── 幂等守卫 ──────────────────────────────────────────

    def _is_due(self, gateway_codes) -> bool:
        """本小时的采样是否还没完成（两个条件都要满足才认为「该采」）。

        **条件一：本小时的采样时刻已经到了。**
        由 `slots.due_slot()` 判定，即「整点 + `collect.hourly_offset_seconds`」。
        这一步不能省 —— 现场把计划任务起在整点触发时（如每 5 分钟一次的
        10:55 / 11:00 / 11:05），没有它就会在 **11:00:00 整**立刻开采，
        把刻意留出的避让窗口（避开整点瞬间的采集竞争与表内累计刷新）绕过去，
        配置里写的 `hourly_offset_seconds` 也就成了摆设。

        **条件二：每一个启用中的计量点在本小时都已经有一条有效读数。**
        用「全部」而不是「任意」，正是**重试机制本身**：某点位这一轮失败时，
        下一个 5 分钟会自动把整轮重跑一遍，直到全部拿到读数为止。
        采集中途被打断（只采到一部分）同理 —— 也会重跑。
        多出来的读数无害：`boundary_reading` 取的是「距整点最近的一条」，槽内第一条永远胜出。

        ⚠ 重试的有效窗口只到本小时结束：过了整点，本小时的采样槽就过去了，
        之后再补采到的读数离整点太远，会被判成 partial / 缺数（见 aggregate.py 的容差）。
        """
        now = datetime.now()
        if slots.due_slot(now) is None:
            return False
        b = slots.hour_start(now)
        ms = EnergyMeter.objects.filter(enabled=True)
        if gateway_codes:
            ms = ms.filter(gateway__code__in=gateway_codes)
        ms = list(ms)
        if not ms:
            return True
        done = set(
            EnergyRaw.objects.filter(meter__in=ms, collect_time__gte=b, ok=True).values_list("meter_id", flat=True)
        )
        return len(done) < len(ms)
