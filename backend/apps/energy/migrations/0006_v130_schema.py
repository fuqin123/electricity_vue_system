"""v1.3.0 第二步：结构变更 + 数据搬迁。

⚠ 本文件是**手工整理过顺序**的：Django 自动探测生成的操作顺序直接用会出问题
（旧列先被删、表还没改名就回填、加唯一约束撞重复行），具体见文件末尾的顺序说明。
"""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


# ══════════════════════════════════════════════════════════════
# v1.3.0 数据搬迁（顺序见文件末尾说明，改之前先读）
# ══════════════════════════════════════════════════════════════

#: 三种能源类型：编码 / 名称 / 单位 / 排序
V130_ENERGY_TYPES = [
    ("ELECTRIC", "电", "kWh", 1),
    ("WATER", "水", "m³", 2),
    ("STEAM", "汽", "t", 3),
]
#: 旧 `meter.kind` 字符串 → 新能源编码
V130_KIND_MAP = {"elec": "ELECTRIC", "water": "WATER", "steam": "STEAM"}
#: 车间哨兵行的编码（车间水/汽总表挂它，见 models.ProductionLine）
V130_WORKSHOP_CODE = "WORKSHOP"


def v130_prepare_legacy(apps, schema_editor):
    """搬迁前的数据准备 —— **本迁移的第一件事**，必须在所有结构变更之前。

    三件事，顺序不能换：

    ① **清空旧汇总表 `eep_period_summary`。** 本迁移会删掉它的 `line` 列，
       而 MySQL 删列会重建唯一索引 —— 旧唯一键是 (scope, line, dim, period_key)，
       6 条产线的行只在 line 上不同，删掉 line 后剩下的三元组立刻互相重复，
       直接报 `Duplicate entry 'line-day-2026-09-22' for key 'uniq_period_summary'`（实测）。
       该表在本迁移末尾会被整个删除，内容可由 `manage.py summarize` 重算，清空是安全的。

    ② **按保留窗口裁剪。** 默认只留 2026-09-23 起（`EEP_V130_KEEP_FROM` 可覆盖）：
       窗口外的 09-22 是 60 秒采样节奏时代的残留，采样时刻不在整点，几乎全是缺数。

    ③ **按唯一键去重。** 旧 `eep_reading` / `eep_hourly_usage` **没有唯一约束**，
       库里确实存在同一秒的重复读数（实测 meter_id=14 在 2026-09-22 17:18:00 有两条）——
       不去重就会在加 uk_raw_meter_time / uk_hourly 时失败。保留 id 最大的一条。
    """
    import os

    cursor = schema_editor.connection.cursor()

    def table_exists(name: str) -> bool:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = %s",
            [name],
        )
        return bool(cursor.fetchone()[0])

    if table_exists("eep_period_summary"):
        cursor.execute("DELETE FROM eep_period_summary")
        print("  ①  清空 eep_period_summary（该表随后被删除，内容可由 summarize 重算）")

    keep_from = os.environ.get("EEP_V130_KEEP_FROM", "2026-09-23")
    # 此刻表名还是旧的：改名在第 ① 步之后才执行
    for table, col in (("eep_reading", "collect_time"), ("eep_hourly_usage", "stat_date")):
        if not table_exists(table):
            continue
        cursor.execute(f"DELETE FROM {table} WHERE DATE({col}) < %s", [keep_from])
        print(f"  ②  {table}：清理 {col} < {keep_from} 共 {cursor.rowcount} 行")

    for table, cols in (
        ("eep_reading", "meter_id, collect_time"),
        ("eep_hourly_usage", "meter_id, stat_date, stat_hour"),
    ):
        if not table_exists(table):
            continue
        join = " AND ".join(f"t.{c.strip()} = d.{c.strip()}" for c in cols.split(","))
        cursor.execute(
            f"DELETE t FROM {table} t JOIN ("
            f"  SELECT MAX(id) AS keep_id, {cols} FROM {table}"
            f"  GROUP BY {cols} HAVING COUNT(*) > 1"
            f") d ON {join} WHERE t.id < d.keep_id"
        )
        print(f"  ③  {table}：删除重复行 {cursor.rowcount} 条（按 {cols} 去重）")


def v130_seed_energy_types(apps, schema_editor):
    """灌入三种能源类型（幂等）。"""
    EnergyType = apps.get_model("energy", "EnergyType")
    for code, name, unit, sort in V130_ENERGY_TYPES:
        EnergyType.objects.update_or_create(
            type_code=code,
            defaults={"type_name": name, "unit": unit, "sort": sort, "enabled": True},
        )


def v130_backfill(apps, schema_editor):
    """回填：建车间哨兵行、kind → energy_type、补 line_id 与能源类型快照。

    ⚠ 必须排在 `RemoveField(energymeter.kind)` **之前** —— 旧数据没了就无从回填。
    """
    cursor = schema_editor.connection.cursor()

    # ① 车间哨兵行：车间水/汽总表不属于任何产线，但汇总表要按 line_id 建唯一键，
    #    而 MySQL 把多个 NULL 视为互不相同（唯一约束会失效），所以用一条真实记录承载。
    cursor.execute(
        """
        INSERT INTO production_line
            (line_code, line_name, status, line_type, rated, product, sort, created_at, updated_at)
        VALUES (%s, %s, 1, 'workshop', 0, '', 0, NOW(), NOW())
        ON DUPLICATE KEY UPDATE line_name = VALUES(line_name), line_type = 'workshop'
        """,
        [V130_WORKSHOP_CODE, "车间（总表）"],
    )

    # ② 旧 kind → energy_type
    for kind, code in V130_KIND_MAP.items():
        cursor.execute(
            "UPDATE energy_meter m JOIN energy_type t ON t.type_code = %s "
            "SET m.energy_type_id = t.id WHERE m.kind = %s",
            [code, kind],
        )

    # ②·补 自检：kind 不在已知三种之内会被漏掉而留 NULL，那会让 0007 的
    #       「收紧为非空」在 MySQL 层直接失败（报错还不好懂）。这里先发现、先给提示。
    cursor.execute("SELECT m.code FROM energy_meter m WHERE m.energy_type_id IS NULL")
    unmapped = [row[0] for row in cursor.fetchall()]
    if unmapped:
        cursor.execute(
            "UPDATE energy_meter m JOIN energy_type t ON t.type_code = 'ELECTRIC' "
            "SET m.energy_type_id = t.id WHERE m.energy_type_id IS NULL"
        )
        print("  ⚠ 以下计量点的旧 kind 无法识别，已按「电」处理，请到后台核对：" + "、".join(unmapped))

    # ③ 车间级总表的 line 指向哨兵行（这一步也让后面「line 改非空」变得可行）
    cursor.execute(
        "UPDATE energy_meter m JOIN production_line l ON l.line_code = %s "
        "SET m.line_id = l.id WHERE m.scope = 'workshop' OR m.line_id IS NULL",
        [V130_WORKSHOP_CODE],
    )

    # ④ raw / hourly 的 line 与能源类型都从计量点带过来（入库时快照，历史归属不随后续调整而变）
    for table in ("energy_raw", "energy_hourly"):
        cursor.execute(
            f"UPDATE {table} x JOIN energy_meter m ON m.id = x.meter_id "
            f"SET x.line_id = m.line_id, x.energy_type_id = m.energy_type_id "
            f"WHERE x.line_id IS NULL OR x.energy_type_id IS NULL"
        )



class Migration(migrations.Migration):

    dependencies = [("energy", "0005_v130_rename")]

    operations = [
        # ── ⓪ 数据准备（清旧汇总表 + 按窗口裁剪 + 去重）──
        #     必须最先做：后面加唯一约束时会撞上旧数据的重复行与窗口外数据
        migrations.RunPython(v130_prepare_legacy, migrations.RunPython.noop),

        # ── ① 表改名（保数据）──
        migrations.AlterModelTable(
            name="energycollectlog",
            table="energy_collect_log",
        ),
        migrations.AlterModelTable(
            name="energygateway",
            table="energy_gateway",
        ),
        migrations.AlterModelTable(
            name="energyhourly",
            table="energy_hourly",
        ),
        migrations.AlterModelTable(
            name="energymeter",
            table="energy_meter",
        ),
        migrations.AlterModelTable(
            name="energyraw",
            table="energy_raw",
        ),
        migrations.AlterModelTable(
            name="productionhourly",
            table="production_hourly",
        ),
        migrations.AlterModelTable(
            name="productionline",
            table="production_line",
        ),

        # ── ② 能源类型表 + 灌数据 ──
        migrations.CreateModel(
            name="EnergyType",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "type_code",
                    models.CharField(
                        max_length=20, unique=True, verbose_name="能源编码"
                    ),
                ),
                ("type_name", models.CharField(max_length=50, verbose_name="能源名称")),
                ("unit", models.CharField(max_length=20, verbose_name="单位")),
                (
                    "sort",
                    models.PositiveSmallIntegerField(default=0, verbose_name="排序"),
                ),
                ("enabled", models.BooleanField(default=True, verbose_name="启用")),
            ],
            options={
                "verbose_name": "能源类型",
                "verbose_name_plural": "能源类型",
                "db_table": "energy_type",
                "ordering": ["sort", "id"],
            },
        ),
        migrations.RunPython(v130_seed_energy_types, migrations.RunPython.noop),

        # ── ③ 加可空列（已有行才能写进去）──
        #     · energy_type ×3：meter / raw / hourly
        #     · 产线三列：created_at / line_type / updated_at（回填要往表里插哨兵行）
        #     · raw / hourly 的 line ×2（回填要写 line_id）
        migrations.AddField(
            model_name="energyhourly",
            name="energy_type",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="hourly",
                to="energy.energytype",
                verbose_name="能源类型",
            ),
        ),
        migrations.AddField(
            model_name="energymeter",
            name="energy_type",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="meters",
                to="energy.energytype",
                verbose_name="能源类型",
            ),
        ),
        migrations.AddField(
            model_name="energyraw",
            name="energy_type",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="readings",
                to="energy.energytype",
                verbose_name="能源类型",
            ),
        ),
        migrations.AddField(
            model_name="productionline",
            name="created_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now, verbose_name="创建时间"
            ),
        ),
        migrations.AddField(
            model_name="productionline",
            name="line_type",
            field=models.CharField(
                choices=[("line", "产线"), ("workshop", "车间（总表）")],
                default="line",
                max_length=10,
                verbose_name="类型",
            ),
        ),
        migrations.AddField(
            model_name="productionline",
            name="updated_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now, verbose_name="更新时间"
            ),
        ),
        migrations.AddField(
            model_name="energyhourly",
            name="line",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="hourly",
                to="energy.productionline",
                verbose_name="所属产线",
            ),
        ),
        migrations.AddField(
            model_name="energyraw",
            name="line",
            field=models.ForeignKey(
                blank=True,
                help_text="入库时的归属快照（历史归属不随产线调整而变）",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="readings",
                to="energy.productionline",
                verbose_name="所属产线",
            ),
        ),

        # ── ④ 数据回填（必须早于 ⑤ 删除旧列）──
        migrations.RunPython(v130_backfill, migrations.RunPython.noop),

        # ── ⑤ 其余结构变更（三张汇总表、索引、约束、删旧汇总表）──
        migrations.CreateModel(
            name="EnergyDaily",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "consumption",
                    models.DecimalField(
                        blank=True,
                        decimal_places=4,
                        max_digits=18,
                        null=True,
                        verbose_name="消耗量",
                    ),
                ),
                (
                    "unit",
                    models.CharField(
                        blank=True, default="", max_length=20, verbose_name="单位"
                    ),
                ),
                (
                    "covered_count",
                    models.PositiveSmallIntegerField(
                        default=0, verbose_name="已覆盖时段数"
                    ),
                ),
                (
                    "expected_count",
                    models.PositiveSmallIntegerField(
                        default=0, verbose_name="应有时段数"
                    ),
                ),
                (
                    "quality",
                    models.CharField(
                        choices=[
                            ("ok", "完整"),
                            ("partial", "部分时段缺数"),
                            ("missing", "无数据"),
                        ],
                        default="ok",
                        max_length=10,
                        verbose_name="数据质量",
                    ),
                ),
                (
                    "computed_at",
                    models.DateTimeField(auto_now=True, verbose_name="计算时间"),
                ),
                ("stat_date", models.DateField(verbose_name="统计日期")),
                (
                    "stat_year",
                    models.SmallIntegerField(
                        db_index=True, default=0, verbose_name="年"
                    ),
                ),
                (
                    "stat_month",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=7,
                        verbose_name="年月(YYYY-MM)",
                    ),
                ),
                (
                    "start_reading",
                    models.DecimalField(
                        blank=True,
                        decimal_places=4,
                        max_digits=18,
                        null=True,
                        verbose_name="日初读数",
                    ),
                ),
                (
                    "end_reading",
                    models.DecimalField(
                        blank=True,
                        decimal_places=4,
                        max_digits=18,
                        null=True,
                        verbose_name="日末读数",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
            ],
            options={
                "verbose_name": "日汇总",
                "verbose_name_plural": "日汇总",
                "db_table": "energy_daily",
                "ordering": ["-stat_date", "line_id"],
            },
        ),
        migrations.CreateModel(
            name="EnergyMonthly",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "consumption",
                    models.DecimalField(
                        blank=True,
                        decimal_places=4,
                        max_digits=18,
                        null=True,
                        verbose_name="消耗量",
                    ),
                ),
                (
                    "unit",
                    models.CharField(
                        blank=True, default="", max_length=20, verbose_name="单位"
                    ),
                ),
                (
                    "covered_count",
                    models.PositiveSmallIntegerField(
                        default=0, verbose_name="已覆盖时段数"
                    ),
                ),
                (
                    "expected_count",
                    models.PositiveSmallIntegerField(
                        default=0, verbose_name="应有时段数"
                    ),
                ),
                (
                    "quality",
                    models.CharField(
                        choices=[
                            ("ok", "完整"),
                            ("partial", "部分时段缺数"),
                            ("missing", "无数据"),
                        ],
                        default="ok",
                        max_length=10,
                        verbose_name="数据质量",
                    ),
                ),
                (
                    "computed_at",
                    models.DateTimeField(auto_now=True, verbose_name="计算时间"),
                ),
                (
                    "stat_month",
                    models.DateField(
                        help_text="存当月第一天，如 2026-09-01", verbose_name="统计月份"
                    ),
                ),
                (
                    "stat_year",
                    models.SmallIntegerField(
                        db_index=True, default=0, verbose_name="年"
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
            ],
            options={
                "verbose_name": "月汇总",
                "verbose_name_plural": "月汇总",
                "db_table": "energy_monthly",
                "ordering": ["-stat_month", "line_id"],
            },
        ),
        migrations.CreateModel(
            name="EnergyYearly",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "consumption",
                    models.DecimalField(
                        blank=True,
                        decimal_places=4,
                        max_digits=18,
                        null=True,
                        verbose_name="消耗量",
                    ),
                ),
                (
                    "unit",
                    models.CharField(
                        blank=True, default="", max_length=20, verbose_name="单位"
                    ),
                ),
                (
                    "covered_count",
                    models.PositiveSmallIntegerField(
                        default=0, verbose_name="已覆盖时段数"
                    ),
                ),
                (
                    "expected_count",
                    models.PositiveSmallIntegerField(
                        default=0, verbose_name="应有时段数"
                    ),
                ),
                (
                    "quality",
                    models.CharField(
                        choices=[
                            ("ok", "完整"),
                            ("partial", "部分时段缺数"),
                            ("missing", "无数据"),
                        ],
                        default="ok",
                        max_length=10,
                        verbose_name="数据质量",
                    ),
                ),
                (
                    "computed_at",
                    models.DateTimeField(auto_now=True, verbose_name="计算时间"),
                ),
                ("stat_year", models.SmallIntegerField(verbose_name="统计年份")),
            ],
            options={
                "verbose_name": "年汇总",
                "verbose_name_plural": "年汇总",
                "db_table": "energy_yearly",
                "ordering": ["-stat_year", "line_id"],
            },
        ),
        migrations.RemoveField(
            model_name="periodsummary",
            name="line",
        ),
        migrations.AlterModelOptions(
            name="energycollectlog",
            options={
                "ordering": ["-started_at"],
                "verbose_name": "采集日志",
                "verbose_name_plural": "采集日志",
            },
        ),
        migrations.AlterModelOptions(
            name="energyhourly",
            options={
                "ordering": ["stat_date", "stat_hour", "meter_id"],
                "verbose_name": "小时用量",
                "verbose_name_plural": "小时用量",
            },
        ),
        migrations.AlterModelOptions(
            name="energymeter",
            options={
                "ordering": ["code"],
                "verbose_name": "计量点",
                "verbose_name_plural": "计量点",
            },
        ),
        migrations.AlterModelOptions(
            name="energyraw",
            options={
                "ordering": ["-collect_time", "meter_id"],
                "verbose_name": "原始读数",
                "verbose_name_plural": "原始读数",
            },
        ),
        migrations.AlterModelOptions(
            name="productionhourly",
            options={
                "ordering": ["stat_date", "stat_hour"],
                "verbose_name": "小时产量",
                "verbose_name_plural": "小时产量",
            },
        ),
        migrations.AlterModelOptions(
            name="productionline",
            options={
                "ordering": ["sort", "line_code"],
                "verbose_name": "产线",
                "verbose_name_plural": "产线",
            },
        ),
        migrations.RemoveField(
            model_name="energymeter",
            name="kind",
        ),
        migrations.AlterField(
            model_name="energycollectlog",
            name="gateway",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="logs",
                to="energy.energygateway",
                verbose_name="网关",
            ),
        ),
        migrations.AlterField(
            model_name="energyhourly",
            name="computed_at",
            field=models.DateTimeField(auto_now=True, verbose_name="计算时间"),
        ),
        migrations.AlterField(
            model_name="energyhourly",
            name="consumption",
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                help_text="缺数 / 换表 / 尖峰时为 NULL —— 绝不补 0",
                max_digits=18,
                null=True,
                verbose_name="本小时用量",
            ),
        ),
        migrations.AlterField(
            model_name="energyhourly",
            name="end_reading",
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                max_digits=18,
                null=True,
                verbose_name="终点累计值",
            ),
        ),
        migrations.AlterField(
            model_name="energyhourly",
            name="quality",
            field=models.CharField(
                choices=[
                    ("ok", "正常"),
                    ("partial", "端点偏离整点"),
                    ("missing", "缺读数"),
                    ("reset", "累计值回退"),
                    ("spike", "异常尖峰"),
                ],
                default="ok",
                max_length=10,
                verbose_name="数据质量",
            ),
        ),
        migrations.AlterField(
            model_name="energyhourly",
            name="start_reading",
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                max_digits=18,
                null=True,
                verbose_name="起点累计值",
            ),
        ),
        migrations.AlterField(
            model_name="energyhourly",
            name="stat_date",
            field=models.DateField(verbose_name="归属日期"),
        ),
        migrations.AlterField(
            model_name="energyhourly",
            name="stat_hour",
            field=models.PositiveSmallIntegerField(verbose_name="归属小时"),
        ),
        migrations.AlterField(
            model_name="energyhourly",
            name="unit",
            field=models.CharField(default="kWh", max_length=20, verbose_name="单位"),
        ),
        migrations.AlterField(
            model_name="energymeter",
            name="line",
            field=models.ForeignKey(
                help_text="车间水/汽总表挂「车间哨兵行」（line_type=workshop），不分摊到线别",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="meters",
                to="energy.productionline",
                verbose_name="所属产线",
            ),
        ),
        migrations.AlterField(
            model_name="energymeter",
            name="max_hourly",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text="差值超过它判 spike、不计入合计（换表/断讯补传会产生尖峰）",
                max_digits=18,
                null=True,
                verbose_name="单小时上限",
            ),
        ),
        migrations.AlterField(
            model_name="energymeter",
            name="role",
            field=models.CharField(
                choices=[("line", "生产线"), ("aux", "辅机"), ("total", "车间总表")],
                default="line",
                max_length=10,
                verbose_name="计量角色",
            ),
        ),
        migrations.AlterField(
            model_name="energymeter",
            name="scope",
            field=models.CharField(
                choices=[("line", "产线"), ("workshop", "车间级")],
                default="line",
                max_length=10,
                verbose_name="归属层级",
            ),
        ),
        migrations.AlterField(
            model_name="energyraw",
            name="collect_time",
            field=models.DateTimeField(
                db_index=True,
                help_text="精确到小时，如 2026-09-24 10:00:00",
                verbose_name="采集时间",
            ),
        ),
        migrations.AlterField(
            model_name="energyraw",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, verbose_name="入库时间"),
        ),
        migrations.AlterField(
            model_name="energyraw",
            name="ok",
            field=models.BooleanField(default=True, verbose_name="采集成功"),
        ),
        migrations.AlterField(
            model_name="energyraw",
            name="reading",
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                help_text="采集失败时为 NULL —— 绝不能写 0，0 会被差值当成真实读数",
                max_digits=18,
                null=True,
                verbose_name="累计读数",
            ),
        ),
        migrations.AlterField(
            model_name="productionhourly",
            name="quality",
            field=models.CharField(
                choices=[
                    ("ok", "正常"),
                    ("missing", "缺数"),
                    ("nodata", "数据源未接入"),
                    ("estimated", "估算"),
                ],
                default="missing",
                max_length=12,
                verbose_name="数据质量",
            ),
        ),
        migrations.AlterField(
            model_name="productionhourly",
            name="source",
            field=models.CharField(
                choices=[
                    ("counter", "灌装机计数器"),
                    ("plc", "装箱机 PLC"),
                    ("mes", "MES 班次报工"),
                    ("manual", "人工填报"),
                    ("demo", "演示占位"),
                    ("none", "未接入"),
                ],
                default="none",
                max_length=12,
                verbose_name="数据来源",
            ),
        ),
        migrations.AlterField(
            model_name="productionline",
            name="line_code",
            field=models.CharField(max_length=50, unique=True, verbose_name="产线编码"),
        ),
        migrations.AlterField(
            model_name="productionline",
            name="line_name",
            field=models.CharField(max_length=100, verbose_name="产线名称"),
        ),
        migrations.AlterField(
            model_name="productionline",
            name="status",
            field=models.PositiveSmallIntegerField(
                default=1, help_text="1 启用 / 0 停用", verbose_name="状态"
            ),
        ),
        migrations.AddConstraint(
            model_name="productionhourly",
            constraint=models.UniqueConstraint(
                fields=("line", "stat_date", "stat_hour"), name="uk_production_hourly"
            ),
        ),
        migrations.AddField(
            model_name="energydaily",
            name="line",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="%(class)s_set",
                to="energy.productionline",
                verbose_name="产线",
            ),
        ),
        migrations.AddField(
            model_name="energymonthly",
            name="line",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="%(class)s_set",
                to="energy.productionline",
                verbose_name="产线",
            ),
        ),
        migrations.AddField(
            model_name="energymonthly",
            name="energy_type",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(class)s_set",
                to="energy.energytype",
                verbose_name="能源类型",
            ),
        ),
        migrations.AddField(
            model_name="energydaily",
            name="energy_type",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(class)s_set",
                to="energy.energytype",
                verbose_name="能源类型",
            ),
        ),
        migrations.AddIndex(
            model_name="energyhourly",
            index=models.Index(
                fields=["stat_date", "stat_hour"], name="idx_hourly_period"
            ),
        ),
        migrations.AddIndex(
            model_name="energyraw",
            index=models.Index(
                fields=["line", "energy_type", "collect_time"], name="idx_raw_line_time"
            ),
        ),
        migrations.AddIndex(
            model_name="energyraw",
            index=models.Index(fields=["collect_time"], name="idx_raw_collect_time"),
        ),
        migrations.AddConstraint(
            model_name="energyhourly",
            constraint=models.UniqueConstraint(
                fields=("meter", "stat_date", "stat_hour"), name="uk_hourly"
            ),
        ),
        migrations.AddConstraint(
            model_name="energyraw",
            constraint=models.UniqueConstraint(
                fields=("meter", "collect_time"), name="uk_raw_meter_time"
            ),
        ),
        migrations.AddField(
            model_name="energyyearly",
            name="energy_type",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(class)s_set",
                to="energy.energytype",
                verbose_name="能源类型",
            ),
        ),
        migrations.AddField(
            model_name="energyyearly",
            name="line",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="%(class)s_set",
                to="energy.productionline",
                verbose_name="产线",
            ),
        ),
        migrations.DeleteModel(
            name="PeriodSummary",
        ),
        migrations.AddConstraint(
            model_name="energymonthly",
            constraint=models.UniqueConstraint(
                fields=("line", "energy_type", "stat_month"), name="uk_monthly"
            ),
        ),
        migrations.AddConstraint(
            model_name="energydaily",
            constraint=models.UniqueConstraint(
                fields=("line", "energy_type", "stat_date"), name="uk_daily"
            ),
        ),
        migrations.AddConstraint(
            model_name="energyyearly",
            constraint=models.UniqueConstraint(
                fields=("line", "energy_type", "stat_year"), name="uk_yearly"
            ),
        ),
    ]


# ── 本迁移的操作顺序（改之前先读）─────────────────────────────
#   ⓪ RunPython 数据准备        清旧汇总表 / 按窗口裁剪 / 去重
#   ① AlterModelTable ×7        eep_* → 新表名（保数据）
#   ② CreateModel(EnergyType) + 灌三种能源
#   ③ AddField（全部先可空）      否则已有行写不进去
#   ④ RunPython 回填            哨兵行 / kind→energy_type / line_id 快照
#   ⑤ 其余结构变更               三张汇总表、索引、约束、删 period_summary
#   ⑥ 收紧为非空                见 0007_v130_notnull（由 Django 生成，保证与模型一致）
#
# ⚠ 旧索引与旧唯一约束的删除在 0005，不在这里：
#   它们引用的旧字段名（date / kind / at）在 0005 里就被改掉了，
#   放在 0006 会抛 KeyError（MySQL 删索引要按字段名定位）。
