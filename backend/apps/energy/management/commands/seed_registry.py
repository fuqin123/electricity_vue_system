"""按配置同步网关与计量点登记表（幂等）。

    python manage.py seed_registry

现场新增一块电表 / 一路水表的步骤：
  1. 在 `config/settings.local.yaml` 的 `meters:` 下加一行（网关、从站号、寄存器、倍率）；
  2. 跑一次本命令；
  3. 下次采集就会带上它。**不需要改任何代码**。
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.energy.services.registry import sync_registry
from eep import conf


class Command(BaseCommand):
    help = "按 config/settings.yaml 同步产线 / 网关 / 计量点（幂等）"

    def handle(self, *args, **options):
        self.stdout.write(f"配置文件：{conf.CONFIG_FILE}")
        stat = sync_registry()
        self.stdout.write(
            self.style.SUCCESS(
                f"同步完成：产线 {stat['line']} 条、网关 {stat['gateway']} 个、计量点 {stat['meter']} 个"
            )
        )
