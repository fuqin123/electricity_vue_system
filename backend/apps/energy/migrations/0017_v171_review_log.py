from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("energy", "0016_v170_energy_price_group"),
    ]

    operations = [
        migrations.CreateModel(
            name="EnergyReviewLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("review_date", models.DateField(unique=True, verbose_name="复核日期")),
                ("target_day", models.DateField(verbose_name="目标生产日")),
                ("reviewed_at", models.DateTimeField(auto_now_add=True, verbose_name="执行时间")),
                ("abnormal_hours", models.PositiveIntegerField(default=0, verbose_name="异常小时数")),
                ("alarms_created", models.PositiveIntegerField(default=0, verbose_name="生成告警数")),
            ],
            options={
                "verbose_name": "异常数据复核记录",
                "verbose_name_plural": "异常数据复核记录",
                "db_table": "energy_review_log",
                "ordering": ["-review_date"],
            },
        ),
    ]
