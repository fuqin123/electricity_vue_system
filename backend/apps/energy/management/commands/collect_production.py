"""产量 PLC 采集命令（v1.8.0）。

用法：
    python manage.py collect_production                # 按设备 mode 采集
    python manage.py collect_production --simulate     # 强制模拟，无 PLC 联调
    python manage.py collect_production --device PLC-01
    python manage.py collect_production --status

建议与能源采集同一节奏（每 5 分钟）执行：累计计数器每次轮询的增量会累加到
读数所属小时，重复执行不会重复计数（增量来自上次读数，不是固定值）。
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from ...models import PlcDevice, ProductionHourly
from ...services import plc


class Command(BaseCommand):
    help = "读取 PLC 产量点位并写入小时产量（累计计数器按两次读数差计算）"

    def add_arguments(self, parser):
        parser.add_argument("--device", help="只采集指定设备编号")
        parser.add_argument("--simulate", action="store_true", help="强制使用模拟值（无 PLC 时联调）")
        parser.add_argument("--status", action="store_true", help="只打印 PLC 与最近产量情况")

    def handle(self, *args, **opts):
        if opts["status"]:
            self._print_status()
            return

        qs = PlcDevice.objects.order_by("code")
        if opts.get("device"):
            code = str(opts["device"]).strip().upper()
            qs = qs.filter(code__iexact=code)
            if not qs.exists():
                raise CommandError(f"PLC 设备不存在：{opts['device']}")
        devices = list(qs.filter(enabled=True))
        if not devices:
            self.stdout.write("没有启用的 PLC 设备（可在「设备与采集 → PLC 设备」新增）")
            return

        total_points = total_failed = total_written = 0
        for device in devices:
            result = plc.collect_device(device, force_simulated=opts["simulate"])
            total_points += result["points"]
            total_failed += result["failed"]
            total_written += result["written"]
            flag = "OK" if result["ok"] else "FAIL"
            extra = f"，{result['error']}" if result["error"] else ""
            self.stdout.write(
                f"[{flag}] {device.code} {device.name}：读取 {result['points']} 点，"
                f"失败 {result['failed']}，写产量 {result['written']} 行{extra}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"完成：{len(devices)} 台 PLC，点位 {total_points}，失败 {total_failed}，写产量 {total_written} 行"
            )
        )

    def _print_status(self):
        devices = list(PlcDevice.objects.order_by("code"))
        if not devices:
            self.stdout.write("没有 PLC 设备")
        for device in devices:
            self.stdout.write(
                f"{device.code} {device.name} mode={device.mode} {device.endpoint} "
                f"点位={device.points.count()} 状态={'在线' if device.is_online else '离线'} "
                f"最近错误={device.last_error or '-'}"
            )
        latest = ProductionHourly.objects.filter(source=ProductionHourly.SOURCE_PLC).order_by(
            "-stat_date", "-stat_hour"
        )[:10]
        if latest:
            self.stdout.write("最近 PLC 产量：")
            for row in latest:
                self.stdout.write(
                    f"  {row.line.line_code} {row.stat_date} {row.stat_hour:02d}时 = {row.output} 瓶"
                )
