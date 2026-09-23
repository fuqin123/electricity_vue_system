"""按配置同步网关与计量点登记表（幂等）。

    python manage.py seed_registry            # 同步（跳过后台维护过的记录）
    python manage.py seed_registry --force    # 以配置文件为准，覆盖后台的修改

**两种配置方式并存，优先级：后台管理 > 配置文件。**
  · 配置文件（`config/settings.local.yaml`）—— 适合批量初始化、随 Git 留痕；
  · 后台管理界面 —— 适合现场调试（改从站号、试采、启停），不需要登服务器改文件。
  后台改过的记录会被标记 `source = admin`，本命令默认跳过，
  避免「现场在后台调好了、下一次同步又被改回配置里的旧值」这种最难查的问题。

现场新增一块电表 / 一路水表的步骤：
  1.（推荐）直接在后台「设备与采集 → 计量点位」新增，填网关、从站号、寄存器、倍率，点「试采」验证；
  2. 或者改 `config/settings.local.yaml` 的 `meters:` 再跑本命令；
  3. 下次采集就会带上它。**两种方式都不需要改代码。**
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.energy.services.registry import sync_registry
from eep import conf


class Command(BaseCommand):
    help = "按 config/settings.yaml 同步产线 / 网关 / 计量点（幂等；后台维护过的默认不覆盖）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true", help="以配置文件为准，覆盖后台维护过的记录（慎用）"
        )

    def handle(self, *args, **options):
        self.stdout.write(f"配置文件：{conf.CONFIG_FILE}")
        stat = sync_registry(force=options["force"])
        self.stdout.write(
            self.style.SUCCESS(
                f"同步完成：产线 {stat['line']} 条、网关 {stat['gateway']} 个、"
                f"计量点 {stat['meter']} 个、跳过（后台维护）{stat['skipped']} 个"
            )
        )
        if stat["skipped"] and not options["force"]:
            self.stdout.write(
                self.style.WARNING(
                    "有记录被跳过：它们在后台被改过，配置文件不再对它们生效。"
                    "需要以配置为准请加 --force。"
                )
            )
