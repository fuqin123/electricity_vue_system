# v1.5.3：计量点寄存器「数据类型 + 字节序」
#
# 背景：现场并非所有表都是 32 位整数。碳水市政水表 `W-CARB-CITY-1`
#   （`GW-WATER-CTY` 10.114.1.71:10123、从站 5、起始地址 68）是
#   **4 个寄存器、IEEE754 双精度（double）**。按 32 位整数拼出来是一个
#   完全无关的大整数 —— 采集不报错、小时差值照算，只有数字荒谬，
#   现场表现为「水表读数不对」，而不是「配置错了」。
#
# 本迁移加两列：
#   · data_type   数据类型：uint16/int16/uint32/int32/float32/float64
#                 —— 同时决定拼出整数还是浮点、以及**要读几个寄存器**
#   · byte_order  单个寄存器内部的字节序（big=AB / little=BA），
#                 与既有的 word_order（字序：big=ABCD / little=CDAB）组合
#                 覆盖现场四种常见排布
#
# ⚠ 回填口径 = **保持现行为不变**：
#   已核对该库全部 34 个点位的 `registers` 均为 2、`word_order` 均为 big，
#   即既有的「32 位无符号 + 高字在前 + 寄存器内大端」。因此回填
#   `data_type='uint32'` / `byte_order='big'` 后，解码结果逐位一致。
#
# ⚠ `registers` 从此是**推导列**（由 data_type 决定），本迁移顺手把它对齐一次；
#   此后由 `EnergyMeter.sync_registers()` / `seed_registry` 维护。
#   水表改为 float64 后会自动变成 4，不需要手改。

from django.db import migrations, models

#: 数据类型 → 寄存器个数（与 EnergyMeter.REGISTERS_BY_TYPE 保持一致）
REGISTERS_BY_TYPE = {
    "uint16": 1, "int16": 1,
    "uint32": 2, "int32": 2, "float32": 2,
    "float64": 4,
}
DEFAULT_TYPE = "uint32"


def forwards(apps, schema_editor):
    """把兼容镜像列 `registers` 对齐到数据类型推导出来的值。"""
    EnergyMeter = apps.get_model("energy", "EnergyMeter")
    for meter in EnergyMeter.objects.all():
        want = REGISTERS_BY_TYPE.get(meter.data_type or DEFAULT_TYPE, 2)
        if meter.registers != want:
            meter.registers = want
            meter.save(update_fields=["registers"])


def backwards(apps, schema_editor):
    """回滚只需删列（`registers` 的值保持不变，仍能被旧代码直接使用）。"""


class Migration(migrations.Migration):

    dependencies = [
        ("energy", "0010_v152_gateway_test_evidence"),
    ]

    operations = [
        migrations.AddField(
            model_name="energymeter",
            name="data_type",
            field=models.CharField(
                choices=[
                    ("uint16", "16 位无符号整数（1 个寄存器）"),
                    ("int16", "16 位有符号整数（1 个寄存器）"),
                    ("uint32", "32 位无符号整数（2 个寄存器）"),
                    ("int32", "32 位有符号整数（2 个寄存器）"),
                    ("float32", "32 位浮点数 IEEE754（2 个寄存器）"),
                    ("float64", "64 位双精度 IEEE754 / double（4 个寄存器）"),
                ],
                default=DEFAULT_TYPE,
                help_text="决定拼出整数还是浮点数，以及要读几个寄存器。水表（double）选 float64",
                max_length=10,
                verbose_name="数据类型",
            ),
        ),
        migrations.AddField(
            model_name="energymeter",
            name="byte_order",
            field=models.CharField(
                choices=[
                    ("big", "大端（寄存器内高字节在前）"),
                    ("little", "小端（寄存器内低字节在前）"),
                ],
                default="big",
                help_text="单个寄存器内部的字节顺序；与「字序」一起决定 4 种组合（现场电表为 大端+高字在前）",
                max_length=8,
                verbose_name="字节序",
            ),
        ),
        migrations.AlterField(
            model_name="energymeter",
            name="registers",
            field=models.PositiveSmallIntegerField(
                default=2,
                help_text="兼容镜像：由「数据类型」推导，保存时自动同步；改它不生效",
                verbose_name="寄存器数量",
            ),
        ),
        migrations.AlterField(
            model_name="energymeter",
            name="word_order",
            field=models.CharField(
                choices=[("big", "高字在前（ABCD）"), ("little", "低字在前（CDAB）")],
                default="big",
                max_length=8,
                verbose_name="字序",
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
