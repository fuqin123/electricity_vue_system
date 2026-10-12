# v1.5.2：网关连接测试的「成功 / 失败证据」（B-542）
#
# 背景：网关在线与否由**心跳**推导（采集日志 ∪ 点位读数 ∪ 网关自身成功时刻），
# 而心跳只能证明「这一刻通」，无法证明「不通」。于是现场点「测试连接」失败后，
# 状态仍停在「在线」（要等旧心跳过期，最长约 91 分钟），与眼前看到的结果相反。
#
# 本迁移加两列可空时间戳，让「失败」也成为一等证据：
#   · last_ok_at   最近一次**连接测试成功**（并进心跳）
#   · last_fail_at 最近一次**连接测试失败**（并进失败证据）
# 判定改为**最新证据优先**：失败的证据比心跳更新 → 判离线。
#
# ⚠ 两列都**可空**，语义是「还没测过」而不是「测过但失败」——
#   这也是本项目反复强调的一条：可空字段的「没填」只有 NULL 一种表达，
#   用 0 / 空串表示会与「真值就是 0」混淆。
#
# 纯加列、不改既有数据，无回填步骤。

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("energy", "0009_v152_workshop_hierarchy"),
    ]

    operations = [
        migrations.AddField(
            model_name="energygateway",
            name="last_ok_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="最近测试成功"),
        ),
        migrations.AddField(
            model_name="energygateway",
            name="last_fail_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="最近测试失败"),
        ),
    ]
