"""v1.7.0：标准单耗按「产线 + 瓶型容量」组织。

v1.6.0 的表是 (line, energy_type) 唯一；一个产线只能有一套标准。
现场同一产线会切换瓶型（500ml / 550ml / 1L），标准单耗因此必须带上瓶型容量。

本迁移：
1. 加 `bottle_capacity`（默认空串）；
2. 用 `production_line.product` 里的容量回填已有行（如「冰红茶 500ml」→ 500ml）；
3. 对还没有任何标准行的产线，补三行空白记录（电/水/汽，value=NULL），
   让后台配置页能直接看到六条产线并逐行修改；
4. 唯一键从 (line, energy_type) 改为 (line, bottle_capacity, energy_type)。
"""

from __future__ import annotations

import re

from django.db import migrations, models

_CAPACITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|mL|ML|l|L)\b")


def _capacity(product: str | None) -> str:
    match = _CAPACITY_RE.search(product or "")
    if not match:
        return ""
    number, unit = match.group(1), match.group(2).lower()
    return f"{number}{'ml' if unit == 'ml' else 'L'}"


def forwards(apps, schema_editor):
    Std = apps.get_model("energy", "StandardUnitConsumption")
    Line = apps.get_model("energy", "ProductionLine")
    EnergyType = apps.get_model("energy", "EnergyType")

    for line in Line.objects.filter(node_type="line"):
        rows = Std.objects.filter(line=line)
        capacity = _capacity(line.product)
        if rows.exists():
            if capacity:
                rows.update(bottle_capacity=capacity)
            continue
        # 还没有配置的产线：补三行空白，页面直接显示「-」，可逐行修改
        for et in EnergyType.objects.all():
            Std.objects.create(
                line=line,
                energy_type=et,
                bottle_capacity=capacity,
                value=None,
                remark="",
                updated_by="",
            )


class Migration(migrations.Migration):
    dependencies = [
        ("energy", "0014_v160_standard_unit"),
    ]

    operations = [
        migrations.AddField(
            model_name="standardunitconsumption",
            name="bottle_capacity",
            field=models.CharField(
                blank=True,
                default="",
                help_text="如 500ml / 1L；同一产线可按瓶型分别配置",
                max_length=32,
                verbose_name="瓶型容量",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="standardunitconsumption",
            name="uniq_standard_line_energy",
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="standardunitconsumption",
            constraint=models.UniqueConstraint(
                fields=("line", "bottle_capacity", "energy_type"),
                name="uniq_standard_line_capacity_energy",
            ),
        ),
        migrations.AlterModelOptions(
            name="standardunitconsumption",
            options={
                "ordering": ["line__sort", "line__line_code", "bottle_capacity", "energy_type__type_code"],
                "verbose_name": "标准单耗",
                "verbose_name_plural": "标准单耗",
            },
        ),
        migrations.AlterField(
            model_name="standardunitconsumption",
            name="value",
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                help_text="每千瓶消耗量；NULL=未标定，0 在界面显示为 -",
                max_digits=14,
                null=True,
                verbose_name="标准单耗",
            ),
        ),
    ]
