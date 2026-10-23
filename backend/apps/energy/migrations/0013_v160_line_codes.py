"""v1.6.0：六条产线编号由 A1—A6 改为现场 W 码。

必须**原地 UPDATE**，不能靠 `seed_registry` 按新编码新建：
`energy_meter.line_id` / `energy_hourly.line_id` / `energy_daily.line_id` 都指向
`production_line.id`，新建行会让历史数据留在旧 id 上，界面上看起来像“历史没了”。

映射（按线号，不是按编码顺序）：
    一线 A1 → W103      二线 A2 → W101      三线 A3 → W102
    四线 A4 → W301      五线 A5 → W104      六线 A6 → W105
"""

from django.db import migrations, transaction

FORWARD = {
    "A1": "W103",
    "A2": "W101",
    "A3": "W102",
    "A4": "W301",
    "A5": "W104",
    "A6": "W105",
}
BACKWARD = {new: old for old, new in FORWARD.items()}


def _rename(apps, mapping):
    line = apps.get_model("energy", "ProductionLine")
    with transaction.atomic():
        for old, new in mapping.items():
            rows = line.objects.filter(line_code=old)
            if not rows.exists():
                continue
            # 目标编码已存在说明这条线已经被迁移过（或现场手工改过），
            # 不能把两批历史合并到一行，直接报错让部署停下来人工确认。
            if line.objects.filter(line_code=new).exclude(pk__in=rows.values("pk")).exists():
                raise RuntimeError(
                    f"产线编码迁移冲突：{old} 与 {new} 同时存在，请先核对 production_line 后再部署"
                )
            rows.update(line_code=new)


def forwards(apps, schema_editor):
    _rename(apps, FORWARD)


def backwards(apps, schema_editor):
    _rename(apps, BACKWARD)


class Migration(migrations.Migration):
    dependencies = [
        ("energy", "0012_energyalarm"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
