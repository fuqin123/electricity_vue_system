"""采集一轮（13 路电表 + 蒸汽总表）。

用法：
    python manage.py collect                   采全部启用网关
    python manage.py collect --gateway GW-PDB-1 只采某个网关
    python manage.py collect --dry-run          只读不写库（现场接线排障用）
    python manage.py collect --aggregate        采完顺手算最近 2 小时的小时差值
    python manage.py collect --aggregate --if-due       仅当本小时还没采过时才采（推荐配 cron）
    python manage.py collect --aggregate --summarize    顺手把日/月/年汇总也刷一遍（调试 / 回填用）

★ 现场推荐用法（hourly 节奏）：计划任务每 5 分钟跑一次
      python manage.py collect --aggregate --if-due
  `--if-due` 先确定当前采样槽（如 1:00 槽，补采窗口到 2:00），再找出本槽
  **还没有成功读数**的计量点，只对这些点位补采。已成功的点位不会因为
  同网关的某块表失败而被反复重读（v1.7.3）。
  某一轮因为机器重启 / 网络抖动没跑成，下一个 5 分钟会自动补上 ——
  比「每小时准点跑一次、错过就丢一小时」可靠得多。

⚠ 周期汇总**不在本命令的默认路径里**（v1.4.2 起）：日 / 月 / 年汇总改成每天结算一次
  （`manage.py summarize --rollup`，生产日 06:00 结束后于 06:30 结算）。
  当期（今天 / 本月 / 本年）的数字在界面上是**直接从小时表现算**的
  （见 `summary.is_open_period`），所以按小时重刷汇总表只是白耗一次扫描。
  06:00—08:00 的轮次会额外补算刚结束的生产日一次（`refresh_just_closed_day`），
  防止跨日边界读数晚到导致覆盖率告警挂一整天。

生产环境建议每 60 秒跑一次（Windows 计划任务 / Linux cron / APScheduler 均可），
比常驻进程更抗崩溃：
    * * * * *  cd /opt/eep/backend && /opt/eep/venv/bin/python manage.py collect --aggregate
"""

from __future__ import annotations

import json
from datetime import datetime

from django.core.management.base import BaseCommand

from apps.energy.services.aggregate import aggregate_recent
from apps.energy.services import alerts
from apps.energy.services.collect import collect_round, missing_meters
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
            "--summarize",
            action="store_true",
            help="采完顺手刷日/月/年总计。默认不刷 —— 汇总已由每天 06:30 的 "
                 "`summarize --rollup` 结算，当期数字界面直接从小时表现算",
        )
        parser.add_argument(
            "--if-due",
            action="store_true",
            help="仅当本小时的采样尚未完成时才采（幂等；配 cron 每 5 分钟跑一次即可）",
        )

    def handle(self, *args, **options):
        now = datetime.now()
        meter_ids = None

        if options["if_due"] and not options["dry_run"]:
            slot = slots.due_slot(now)
            if slot is None:
                self.stdout.write(f"当前没有可补采的采样槽，跳过（节奏：{slots.describe()}）。")
                return

            missing = missing_meters(slot, options.get("gateway"))
            if not missing:
                self.stdout.write(
                    f"{slot:%Y-%m-%d %H:%M} 这一槽已采全，跳过（节奏：{slots.describe()}）。"
                    "需要强制重采请去掉 --if-due。"
                )
                return

            meter_ids = [m.id for m in missing]
            self.stdout.write(
                f"补采 {slot:%Y-%m-%d %H:%M} 槽缺失的 {len(missing)} 个点位"
                f"（其余点位本槽已成功，不再重读）。"
            )
        else:
            # 手工采集不属于补采，归到离当前时刻最近的整点，避免落到上一槽。
            slot = slots.nearest_slot(now)

        summary = collect_round(
            gateway_codes=options.get("gateway"),
            meter_ids=meter_ids,
            slot=slot,
            dry_run=options["dry_run"],
        )

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

        # 周期总计默认不刷（v1.4.2）：由每天 00:30 的 `summarize --rollup` 结算
        if options["summarize"] and not options["dry_run"]:
            written = S.refresh_rollups(datetime.now())
            self.stdout.write(f"周期总计：刷新昨天 + 今天 + 近 2 月 + 今年，共 {len(written)} 条")

        # 告警评估 —— ⚠ 必须放在「有失败就返回非 0」之前。
        # 部分失败的那一轮恰恰是最该生成告警的一轮；放在后面就等于
        # 「只在一切正常时才工作的告警系统」，那是没有意义的。
        if not options["dry_run"]:
            # 06:00 边界读数可能晚到：先在 06—08 点的轮次里补算刚结束的生产日，
            # 再评估覆盖率告警，避免 06:30 的 23/24 挂到第二天。
            S.refresh_just_closed_day()
            res = alerts.evaluate()
            if not options["json"]:
                self.stdout.write(
                    f"告警：新增 {res['created']}、恢复 {res['resolved']}、未处理 {res['active']}"
                )

        # 有失败时返回非 0，方便计划任务判定
        if summary["fail"]:
            raise SystemExit(2)

    # ── 幂等守卫 ──────────────────────────────────────────

    def _is_due(self, gateway_codes) -> bool:
        """当前采样槽是否还有缺失点位（有才需要跑一轮）。

        ① **槽由 `slots.due_slot()` 决定**：整点 + `hourly_offset_seconds` 后
           开始采样；还没到偏移时，上一槽仍然开放（补采窗口到下一槽采样时刻）。
           所以计划任务在 11:00:00 触发时补的是 10:00 槽，不会提前读 11:00 槽。
        ② **只检查缺失点位，不再要求「整轮全部重跑」**（v1.7.3）：
           同网关里一块水表失败，不会把已经成功的电表再读一遍；
           补采列表由 `services.collect.missing_meters()` 统一给出。
        ③ **启用网关下没有点位时返回 False**：没有可采的东西，不应让
           每 5 分钟的计划任务空跑一轮。
        """
        slot = slots.due_slot(datetime.now())
        if slot is None:
            return False
        return bool(missing_meters(slot, gateway_codes))
