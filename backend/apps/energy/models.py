"""数据模型 —— 采集侧的对象模型。

分层说明（这是整套采集的口径基础，改之前先想清楚）：

    Gateway 网关 ──< Meter 计量点 ──< Reading 原始读数（累计值）
                                 └─< HourlyUsage 小时用量（差值，交付给大屏的就是它）

  · **Reading 存的是电表/流量计的累计值**（如 6,312,640.6 kWh），不是用量。
  · **HourlyUsage 才是「这个小时用了多少」**，由「本小时末读数 − 上小时末读数」得出。
  · 产线级、车间级的用量**不落表**，查询时按 Meter 聚合出来，避免两处口径不一致。
"""

from __future__ import annotations

from django.db import models


class Gateway(models.Model):
    """采集网关：串口服务器（Modbus RTU over TCP）或第三方数据平台（HTTP API）。"""

    KIND_MODBUS = "modbus_rtu_tcp"
    KIND_HTTP = "http_api"
    KIND_CHOICES = [
        (KIND_MODBUS, "串口服务器（Modbus RTU over TCP）"),
        (KIND_HTTP, "HTTP API（第三方数据平台）"),
    ]

    code = models.CharField("网关编号", max_length=32, unique=True)
    name = models.CharField("网关名称", max_length=64)
    kind = models.CharField("网关类型", max_length=20, choices=KIND_CHOICES)

    host = models.CharField("IP", max_length=64, blank=True, default="")
    port = models.PositiveIntegerField("端口", null=True, blank=True)
    base_url = models.CharField("接口根地址", max_length=200, blank=True, default="")

    timeout_ms = models.PositiveIntegerField("超时(ms)", default=3000)
    interval_seconds = models.PositiveIntegerField("采样周期(秒)", default=60)
    enabled = models.BooleanField("启用", default=True)
    remark = models.CharField("备注", max_length=200, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "eep_gateway"
        verbose_name = "采集网关"
        verbose_name_plural = "采集网关"

    def __str__(self) -> str:
        return f"{self.code} {self.name}"

    @property
    def endpoint(self) -> str:
        """给日志和排障用的一行地址。"""
        if self.kind == self.KIND_MODBUS:
            return f"{self.host}:{self.port}"
        return self.base_url


class Line(models.Model):
    """产线主数据。id 用 A1—A6，与前端大屏的路由 /#/line/A1 对应。"""

    code = models.CharField("产线代码", max_length=8, unique=True)
    name = models.CharField("产线名称", max_length=32)
    rated = models.PositiveIntegerField("额定产能(瓶/小时)", default=0)
    product = models.CharField("品项", max_length=64, blank=True, default="")
    sort = models.PositiveSmallIntegerField("排序", default=0)
    enabled = models.BooleanField("启用", default=True)

    class Meta:
        db_table = "eep_line"
        ordering = ["sort", "code"]
        verbose_name = "产线"
        verbose_name_plural = "产线"

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class Meter(models.Model):
    """计量点：一块电表 / 一台流量计。

    现场现状（2026-09-22 确认）：
      · 电表：**每条线都有**，且一线到六线共 13 块（三线两块生产线表、五线含冰水机）。
      · 水表 / 蒸汽表：**只有车间总表**，不拆分到线别（也就没有均摊问题）。
    """

    KIND_ELEC = "elec"
    KIND_WATER = "water"
    KIND_STEAM = "steam"
    KIND_CHOICES = [
        (KIND_ELEC, "电"),
        (KIND_WATER, "水"),
        (KIND_STEAM, "汽"),
    ]

    SCOPE_LINE = "line"
    SCOPE_WORKSHOP = "workshop"
    SCOPE_CHOICES = [
        (SCOPE_LINE, "产线级"),
        (SCOPE_WORKSHOP, "车间级（总表）"),
    ]

    ROLE_LINE = "line"
    ROLE_AUX = "aux"
    ROLE_TOTAL = "total"
    ROLE_CHOICES = [
        (ROLE_LINE, "生产线主表"),
        (ROLE_AUX, "辅机（吹瓶机/冰水机等）"),
        (ROLE_TOTAL, "车间总表"),
    ]

    code = models.CharField("计量点编号", max_length=32, unique=True)
    name = models.CharField("计量点名称", max_length=64)
    kind = models.CharField("能源类型", max_length=10, choices=KIND_CHOICES)
    scope = models.CharField("归属层级", max_length=10, choices=SCOPE_CHOICES, default=SCOPE_LINE)
    role = models.CharField("计量角色", max_length=10, choices=ROLE_CHOICES, default=ROLE_LINE)

    line = models.ForeignKey(
        Line, null=True, blank=True, on_delete=models.SET_NULL, related_name="meters", verbose_name="所属产线"
    )
    gateway = models.ForeignKey(Gateway, on_delete=models.PROTECT, related_name="meters", verbose_name="所属网关")

    # ── 串口服务器（Modbus）参数 ──
    slave = models.PositiveSmallIntegerField("从站号", null=True, blank=True)
    register = models.PositiveIntegerField("寄存器起始地址", null=True, blank=True)
    registers = models.PositiveSmallIntegerField("寄存器数量", default=2)
    word_order = models.CharField(
        "32 位字序", max_length=8, default="big", choices=[("big", "高字在前"), ("little", "低字在前")]
    )

    # ── HTTP API 参数 ──
    url_path = models.CharField("接口路径", max_length=200, blank=True, default="")
    params = models.JSONField("接口参数", default=dict, blank=True)
    value_path = models.CharField("累计值取值路径", max_length=120, blank=True, default="")
    time_path = models.CharField("采集时间取值路径", max_length=120, blank=True, default="")

    # ── 换算 ──
    scale = models.DecimalField("倍率", max_digits=12, decimal_places=4, default=1)
    unit = models.CharField("单位", max_length=16, default="kWh")
    max_hourly = models.DecimalField(
        "小时用量上限", max_digits=18, decimal_places=3, null=True, blank=True,
        help_text="超过该值判为异常尖峰（换表/断讯），不计入聚合。留空表示不判",
    )

    enabled = models.BooleanField("启用", default=True)
    remark = models.CharField("备注", max_length=200, blank=True, default="")

    # ── 最近一次采集结果（冗余字段，供页面快速展示，真值仍在 Reading）──
    last_value = models.DecimalField("最近累计值", max_digits=18, decimal_places=3, null=True, blank=True)
    last_at = models.DateTimeField("最近采集时间", null=True, blank=True)
    last_ok = models.BooleanField("最近采集成功", default=False)
    last_error = models.CharField("最近错误", max_length=200, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "eep_meter"
        ordering = ["kind", "line__sort", "code"]
        verbose_name = "计量点"
        verbose_name_plural = "计量点"
        indexes = [
            models.Index(fields=["kind", "scope"]),
            models.Index(fields=["enabled"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class Reading(models.Model):
    """一次采样的**累计值**快照。

    保留原始读数（而不是只留差值）是为了：出问题时能复查、换表能看出断点、
    小时差值可以随时用新口径重算 —— 差值算错了只需重跑聚合，不用等现场重新采。
    """

    meter = models.ForeignKey(Meter, on_delete=models.CASCADE, related_name="readings", verbose_name="计量点")
    at = models.DateTimeField("采样时间", db_index=True)
    value = models.DecimalField("累计值", max_digits=18, decimal_places=3, null=True, blank=True)
    raw = models.CharField("原始报文/响应", max_length=200, blank=True, default="")
    ok = models.BooleanField("成功", default=True)
    error = models.CharField("错误信息", max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "eep_reading"
        verbose_name = "原始读数"
        verbose_name_plural = "原始读数"
        indexes = [
            models.Index(fields=["meter", "-at"]),
            models.Index(fields=["at"]),
        ]

    def __str__(self) -> str:
        return f"{self.meter_id}@{self.at:%Y-%m-%d %H:%M} {self.value}"


class HourlyUsage(models.Model):
    """小时用量 —— 交付给大屏的就是这张表。

    口径：`usage = 本小时末读数 − 上小时末读数`，两个端点读数都取「距整点最近的一次采样」。

    quality 取值与含义：
      ok       正常
      partial  端点在整点附近没有采样（采集中断），差值区间跨度过大，仅供参考
      missing  缺读数 → usage 为 null。**绝不补 0**（补 0 会在图上显示成「产量暴跌」）
      reset    累计值回退 → 换表 / 表归零，差值无效，直接丢弃
      spike    差值超过 max_hourly → 判为异常尖峰，保留但不计入聚合
    """

    Q_OK = "ok"
    Q_PARTIAL = "partial"
    Q_MISSING = "missing"
    Q_RESET = "reset"
    Q_SPIKE = "spike"
    QUALITY_CHOICES = [
        (Q_OK, "正常"),
        (Q_PARTIAL, "端点偏离整点"),
        (Q_MISSING, "缺读数"),
        (Q_RESET, "累计值回退（换表/归零）"),
        (Q_SPIKE, "异常尖峰"),
    ]
    #: 只有这两种质量的数据才允许进入产线 / 车间聚合
    TRUSTED = (Q_OK,)

    meter = models.ForeignKey(Meter, on_delete=models.CASCADE, related_name="hourly", verbose_name="计量点")
    date = models.DateField("日期")
    hour = models.PositiveSmallIntegerField("小时")
    usage = models.DecimalField("本小时用量", max_digits=18, decimal_places=3, null=True, blank=True)
    unit = models.CharField("单位", max_length=16, default="kWh")
    quality = models.CharField("数据质量", max_length=10, choices=QUALITY_CHOICES, default=Q_OK)

    start_at = models.DateTimeField("区间起点(读数时间)", null=True, blank=True)
    end_at = models.DateTimeField("区间终点(读数时间)", null=True, blank=True)
    start_value = models.DecimalField("起点累计值", max_digits=18, decimal_places=3, null=True, blank=True)
    end_value = models.DecimalField("终点累计值", max_digits=18, decimal_places=3, null=True, blank=True)
    note = models.CharField("说明", max_length=200, blank=True, default="")
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "eep_hourly_usage"
        verbose_name = "小时用量"
        verbose_name_plural = "小时用量"
        constraints = [models.UniqueConstraint(fields=["meter", "date", "hour"], name="uniq_meter_hour")]
        indexes = [models.Index(fields=["date", "hour"])]

    def __str__(self) -> str:
        return f"{self.meter_id} {self.date} {self.hour:02d} {self.usage}{self.unit} [{self.quality}]"


class CollectLog(models.Model):
    """每轮采集的日志：后台「采集日志」页与巡检脚本都读它。"""

    gateway = models.ForeignKey(
        Gateway, null=True, blank=True, on_delete=models.SET_NULL, related_name="logs", verbose_name="网关"
    )
    started_at = models.DateTimeField("开始时间")
    finished_at = models.DateTimeField("结束时间", null=True, blank=True)
    duration_ms = models.PositiveIntegerField("耗时(ms)", default=0)
    total = models.PositiveSmallIntegerField("点位总数", default=0)
    ok_count = models.PositiveSmallIntegerField("成功", default=0)
    fail_count = models.PositiveSmallIntegerField("失败", default=0)
    message = models.CharField("说明", max_length=500, blank=True, default="")

    class Meta:
        db_table = "eep_collect_log"
        verbose_name = "采集日志"
        verbose_name_plural = "采集日志"
        indexes = [models.Index(fields=["-started_at"])]

    def __str__(self) -> str:
        return f"{self.started_at:%Y-%m-%d %H:%M:%S} {self.ok_count}/{self.total}"
