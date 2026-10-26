"""登记系统参数（能源单价 / 箱规 / 标准变动费用），幂等。

只补「库里没有的键」，**不覆盖后台已经改过的值** —— 这些参数由能源管理员在
后台「能源配置管理 → 能源单价」/「系统参数」页维护，重跑本命令不能把他们的修改冲掉（与 seed_registry 同一套约定）。

用法：
    python manage.py seed_params            # 缺什么补什么
    python manage.py seed_params --show     # 打印当前全部参数
"""

from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand

from ...models import SystemParam

#: 默认参数表。value=None 表示「尚未标定」，界面与图表必须按「无数据」处理，
#: 不能当 0 —— 0 元/箱的标准线会被画出来，读成「实际费用远高于标准」。
DEFAULTS: list[dict] = [
    {
        "key": "price.electric",
        "name": "电价",
        "value": Decimal("0.65"),
        "unit": "元/kWh",
        "group": "能源价格",
        "remark": "综合电价（含峰谷平加权）。上线前请用财务结算单实际值回填。",
    },
    {
        "key": "price.water",
        "name": "工业水价",
        "value": Decimal("4.50"),
        "unit": "元/t",
        "group": "能源价格",
        "remark": "车间进水单价。水价含污水处理费时在此一并登记。",
    },
    {
        "key": "price.steam",
        "name": "蒸汽单价",
        "value": Decimal("260.00"),
        "unit": "元/t",
        "group": "能源价格",
        "remark": "外购蒸汽结算单价，按锅炉房结算口径回填。",
    },
    {
        "key": "package.bottles_per_case",
        "name": "箱规",
        "value": Decimal("24"),
        "unit": "瓶/箱",
        "group": "成本参数",
        "remark": "折箱系数。多规格产线需按品项拆分时，此项升级为按线/按品项维护。",
    },
    {
        "key": "cost.standard_per_case",
        "name": "每月标准变动费用",
        "value": None,
        "unit": "元/箱",
        "group": "成本参数",
        "remark": "考核基准线。留空即未标定，趋势图不画这条线；标定后作为水平虚线与实际对比。",
    },
]


class Command(BaseCommand):
    help = "登记 / 查看系统参数（能源单价、箱规、标准变动费用）"

    def add_arguments(self, parser):
        parser.add_argument("--show", action="store_true", help="打印当前全部参数")

    def handle(self, *args, **opts):
        created = 0
        for spec in DEFAULTS:
            if SystemParam.objects.filter(key=spec["key"]).exists():
                continue
            SystemParam.objects.create(**spec)
            created += 1
            self.stdout.write(f"新增参数：{spec['key']} = {spec['value']} {spec['unit']}")

        skipped = len(DEFAULTS) - created
        self.stdout.write(self.style.SUCCESS(f"完成：新增 {created} 条，已存在跳过 {skipped} 条"))

        if opts["show"]:
            self.stdout.write("")
            self.stdout.write(f"{'键':<28}{'值':>12}  {'单位':<10} 名称")
            for p in SystemParam.objects.all():
                val = "未标定" if p.value is None else f"{p.value}"
                self.stdout.write(f"{p.key:<28}{val:>12}  {p.unit:<10} {p.name}")
