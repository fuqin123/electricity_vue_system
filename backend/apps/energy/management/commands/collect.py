"""采集一轮（13 路电表 + 蒸汽总表）。

用法：
    python manage.py collect                   采全部启用网关
    python manage.py collect --gateway GW-PDB-1 只采某个网关
    python manage.py collect --dry-run          只读不写库（现场接线排障用）
    python manage.py collect --aggregate        采完顺手算最近 2 小时的小时差值

生产环境建议每 60 秒跑一次（Windows 计划任务 / Linux cron / APScheduler 均可），
比常驻进程更抗崩溃：
    * * * * *  cd /opt/eep/backend && /opt/eep/venv/bin/python manage.py collect --aggregate
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.energy.services.aggregate import aggregate_recent
from apps.energy.services.collect import collect_round


class Command(BaseCommand):
    help = "采集一轮能源数据（原始累计值），可选顺带算小时差值"

    def add_arguments(self, parser):
        parser.add_argument("--gateway", action="append", help="只采指定网关编号，可重复")
        parser.add_argument("--dry-run", action="store_true", help="只读不写库")
        parser.add_argument("--aggregate", action="store_true", help="采完算最近 2 小时差值")
        parser.add_argument("--json", action="store_true", help="以 JSON 输出汇总（给脚本用）")

    def handle(self, *args, **options):
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

        # 有失败时返回非 0，方便计划任务判定
        if summary["fail"]:
            raise SystemExit(2)
