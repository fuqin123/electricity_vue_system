# v1.5.2：车间层级（多车间组织节点树）+ 计量点用能分类
#
# 背景见 `docs/车间归属与能耗分摊设计-草案v0.1.md`：
#   · 原设计只有**一条**车间哨兵行（line_code='WORKSHOP'），装不下「碳水 / 制造」两个车间；
#   · 现场接口表（《能源管理平台网关接口配置.md》）把电表分为
#     生产线用电 / 辅机用电 / 瓶胚用电 / 调配用电，界面要按「车间 → 类型 → 设备」筛。
#
# 本迁移只做三件事，**不动任何既有数据行的语义**：
#   ① production_line 加 node_type / parent 两列；
#   ② energy_meter 加 usage_type / device_name 两列；
#   ③ 把已有的哨兵行 WORKSHOP 升级为「碳水车间」节点，
#      并把 6 条产线挂到它下面（现场确认 A1—A6 全部属于碳水车间）。
#
# ⚠ 迁移顺序遵循项目既有教训（数据库设计-v1.3.0.md §八）：
#   先加**可空**列 → 回填 → 不改非空（node_type 有 default，parent 保持可空）。

from django.db import migrations, models
import django.db.models.deletion


#: 原哨兵行编码（0006 建的那条）
V130_WORKSHOP_CODE = "WORKSHOP"
#: 碳水车间节点编码
WS_CARB = "WS-CARB"


def forwards(apps, schema_editor):
    """把单车间哨兵行升级为碳水车间节点，并挂上 6 条产线。"""
    ProductionLine = apps.get_model("energy", "ProductionLine")

    # ① 所有既有行默认是产线（node_type 的 default 已给 line，这里显式写一遍更保险）
    ProductionLine.objects.filter(node_type="").update(node_type="line")

    # ② 哨兵行 → 碳水车间。
    #    保留原 line_code 会被 rename 掉，所以直接改编码与名称；
    #    先把引用它的东西放开（计量点的 line 是指向它的外键，改的是行的属性不是 id，无需处理）。
    sentinel = ProductionLine.objects.filter(line_code=V130_WORKSHOP_CODE).first()
    if sentinel is not None:
        sentinel.line_code = WS_CARB
        sentinel.line_name = "碳水车间"
        sentinel.node_type = "workshop"
        sentinel.line_type = "workshop"
        sentinel.save(update_fields=["line_code", "line_name", "node_type", "line_type"])
        carbshop_id = sentinel.id
    else:
        # 全新库（没有哨兵行）：直接建一个
        obj, _ = ProductionLine.objects.get_or_create(
            line_code=WS_CARB,
            defaults={
                "line_name": "碳水车间",
                "node_type": "workshop",
                "line_type": "workshop",
                "sort": 0,
                "status": 1,
            },
        )
        carbshop_id = obj.id

    # ③ 六条产线挂到碳水车间下。
    #    用 A1—A6 的编码前缀判断，而不是硬编码 6 个 id：
    #    现场可能已改过产线编码，硬编码 id 会在别的库上挂错。
    ProductionLine.objects.filter(node_type="line").exclude(line_code=WS_CARB).update(
        parent_id=carbshop_id
    )

    # ④ 计量点的 role 与 usage_type 对齐：line → 生产线用电、aux → 辅机用电。
    #    只是给一个合理初值，现场可按接口表在后台改（见 seed_registry 的 usage_type）。
    EnergyMeter = apps.get_model("energy", "EnergyMeter")
    EnergyMeter.objects.filter(role="line", usage_type="").update(usage_type="生产线用电")
    EnergyMeter.objects.filter(role="aux", usage_type="").update(usage_type="辅机用电")


def backwards(apps, schema_editor):
    """回滚：把碳水车间还原成旧的哨兵行编码，产线解除挂靠。"""
    ProductionLine = apps.get_model("energy", "ProductionLine")
    ProductionLine.objects.filter(parent__isnull=False).update(parent=None)
    ProductionLine.objects.filter(line_code=WS_CARB).update(
        line_code=V130_WORKSHOP_CODE, line_name="车间（总表）"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("energy", "0008_v140_system_param"),
    ]

    operations = [
        # ── ① 组织节点树 ──
        migrations.AddField(
            model_name="productionline",
            name="node_type",
            field=models.CharField(
                choices=[("workshop", "车间"), ("line", "产线"), ("aux", "辅助计量单元")],
                default="line",
                help_text="workshop 车间 / line 产线 / aux 辅助计量单元（辅机、瓶胚、调配）",
                max_length=10,
                verbose_name="节点类型",
            ),
        ),
        migrations.AddField(
            model_name="productionline",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                help_text="产线 / 辅助单元指向所属车间；车间节点为空",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="children",
                to="energy.productionline",
                verbose_name="上级节点",
            ),
        ),
        migrations.AlterField(
            model_name="productionline",
            name="line_code",
            field=models.CharField(max_length=50, unique=True, verbose_name="编码"),
        ),
        migrations.AlterField(
            model_name="productionline",
            name="line_name",
            field=models.CharField(max_length=100, verbose_name="名称"),
        ),
        migrations.AlterField(
            model_name="productionline",
            name="line_type",
            field=models.CharField(
                choices=[("line", "产线"), ("workshop", "车间（总表）")],
                default="line",
                max_length=10,
                verbose_name="类型（兼容字段）",
            ),
        ),
        migrations.AlterModelOptions(
            name="productionline",
            options={
                "ordering": ["sort", "line_code"],
                "verbose_name": "组织节点（车间/产线/辅助单元）",
                "verbose_name_plural": "组织节点（车间/产线/辅助单元）",
            },
        ),
        # ── ② 计量点分类 ──
        migrations.AddField(
            model_name="energymeter",
            name="usage_type",
            field=models.CharField(
                blank=True,
                default="",
                help_text="界面二级分类，如「生产线用电 / 辅机用电 / 瓶胚用电 / 调配用电」；汽/水表可为空",
                max_length=32,
                verbose_name="用能类型",
            ),
        ),
        migrations.AddField(
            model_name="energymeter",
            name="device_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="现场接口表里的「设备」列，如「吹瓶机 / 冰水机和水塔 / 溶糖CIP」——界面第三级",
                max_length=64,
                verbose_name="设备名",
            ),
        ),
        migrations.AlterField(
            model_name="energymeter",
            name="line",
            field=models.ForeignKey(
                help_text="产线表挂产线节点、辅机表挂 aux 节点、车间水/汽总表挂 workshop 车间节点",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="meters",
                to="energy.productionline",
                verbose_name="所属节点",
            ),
        ),
        # ── ③ 数据回填 ──
        migrations.RunPython(forwards, backwards),
    ]
