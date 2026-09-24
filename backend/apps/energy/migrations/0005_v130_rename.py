"""v1.3.0 第一步：只改名字（表名 / 字段名），全部是保数据操作。

本迁移**不改任何结构内容**：只有 RenameModel 与 RenameField。
目的是把「改名」和「增删字段」分开 —— 后者由 0006 负责。
两件事混在一起时，Django 会把「字段类型也变了」的改名判成删列 + 加列，
那会直接清掉该列数据（例如 energy_raw.reading 的所有读数）。

对应表名变化：
  eep_line → productionline
  eep_gateway → energygateway
  eep_meter → energymeter
  eep_reading → energyraw
  eep_hourly_usage → energyhourly
  eep_hourly_production → productionhourly
  eep_collect_log → energycollectlog
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("energy", "0004_alter_periodsummary_kwh_alter_periodsummary_output_and_more")]

    operations = [
        # ── 表改名（表内数据原样保留）──
        migrations.RenameModel(old_name="line", new_name="productionline"),  # eep_line → production_line
        migrations.RenameModel(old_name="gateway", new_name="energygateway"),  # eep_gateway → energy_gateway
        migrations.RenameModel(old_name="meter", new_name="energymeter"),  # eep_meter → energy_meter
        migrations.RenameModel(old_name="reading", new_name="energyraw"),  # eep_reading → energy_raw
        migrations.RenameModel(old_name="hourlyusage", new_name="energyhourly"),  # eep_hourly_usage → energy_hourly
        migrations.RenameModel(old_name="hourlyproduction", new_name="productionhourly"),  # eep_hourly_production → production_hourly
        migrations.RenameModel(old_name="collectlog", new_name="energycollectlog"),  # eep_collect_log → energy_collect_log

        # ── 字段改名（列内数据原样保留）──
        # ── 先删掉引用旧字段名的索引与唯一约束 ──
        # ⚠ 必须在下面的 RenameField 之前：MySQL 删索引时要按字段名定位，
        #   字段改名后就找不到 'date' / 'kind' / 'at' 了（会抛 KeyError）。
        migrations.RemoveConstraint(
            model_name="energyhourly",
            name="uniq_meter_hour",
        ),
        migrations.RemoveConstraint(
            model_name="productionhourly",
            name="uniq_line_hour_prod",
        ),
        migrations.RemoveIndex(
            model_name="energycollectlog",
            name="eep_collect_started_04917c_idx",
        ),
        migrations.RemoveIndex(
            model_name="energyhourly",
            name="eep_hourly__date_2d369b_idx",
        ),
        migrations.RemoveIndex(
            model_name="energymeter",
            name="eep_meter_kind_67c289_idx",
        ),
        migrations.RemoveIndex(
            model_name="energymeter",
            name="eep_meter_enabled_b6c6b8_idx",
        ),
        migrations.RemoveIndex(
            model_name="energyraw",
            name="eep_reading_meter_i_5c6157_idx",
        ),
        migrations.RemoveIndex(
            model_name="energyraw",
            name="eep_reading_at_c7d190_idx",
        ),
        migrations.RemoveIndex(
            model_name="productionhourly",
            name="eep_hourly__date_4138ac_idx",
        ),

        migrations.RenameField(model_name="productionline", old_name="code", new_name="line_code"),
        migrations.RenameField(model_name="productionline", old_name="name", new_name="line_name"),
        migrations.RenameField(model_name="productionline", old_name="enabled", new_name="status"),
        migrations.RenameField(model_name="energyraw", old_name="at", new_name="collect_time"),
        migrations.RenameField(model_name="energyraw", old_name="value", new_name="reading"),
        migrations.RenameField(model_name="energyhourly", old_name="date", new_name="stat_date"),
        migrations.RenameField(model_name="energyhourly", old_name="hour", new_name="stat_hour"),
        migrations.RenameField(model_name="energyhourly", old_name="usage", new_name="consumption"),
        migrations.RenameField(model_name="energyhourly", old_name="start_value", new_name="start_reading"),
        migrations.RenameField(model_name="energyhourly", old_name="end_value", new_name="end_reading"),
        migrations.RenameField(model_name="productionhourly", old_name="date", new_name="stat_date"),
        migrations.RenameField(model_name="productionhourly", old_name="hour", new_name="stat_hour"),
]
