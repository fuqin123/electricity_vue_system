"""v1.7.0：把电价 / 水价 / 汽价归到「能源价格」，供独立的能源配置管理菜单维护。"""

from django.db import migrations

PRICE_KEYS = ("price.electric", "price.water", "price.steam")


def forwards(apps, schema_editor):
    Param = apps.get_model("energy", "SystemParam")
    Param.objects.filter(key__in=PRICE_KEYS).update(group="能源价格")


def backwards(apps, schema_editor):
    Param = apps.get_model("energy", "SystemParam")
    Param.objects.filter(key__in=PRICE_KEYS).update(group="成本参数")


class Migration(migrations.Migration):
    dependencies = [
        ("energy", "0015_v170_standard_capacity"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
