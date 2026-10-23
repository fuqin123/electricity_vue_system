from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("energy", "0013_v160_line_codes"),
    ]

    operations = [
        migrations.CreateModel(
            name="StandardUnitConsumption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("value", models.DecimalField(blank=True, decimal_places=4, help_text="每千瓶消耗量；NULL=未标定", max_digits=14, null=True, verbose_name="标准单耗")),
                ("remark", models.CharField(blank=True, default="", max_length=200, verbose_name="说明")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                ("updated_by", models.CharField(blank=True, default="", max_length=32, verbose_name="更新人")),
                ("energy_type", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="standard_units", to="energy.energytype", verbose_name="能源类型")),
                ("line", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="standard_units", to="energy.productionline", verbose_name="产线")),
            ],
            options={
                "verbose_name": "标准单耗",
                "verbose_name_plural": "标准单耗",
                "db_table": "energy_standard_unit",
                "ordering": ["line__sort", "line__line_code", "energy_type__type_code"],
            },
        ),
        migrations.AddConstraint(
            model_name="standardunitconsumption",
            constraint=models.UniqueConstraint(fields=("line", "energy_type"), name="uniq_standard_line_energy"),
        ),
    ]
