"""数据模型 —— 采集侧的对象模型。

分层说明（这是整套采集的口径基础，改之前先想清楚）：

    Gateway 网关 ──< Meter 计量点 ──< Reading 原始读数（累计值）
                                 ├─< HourlyUsage 小时用量（差值，交付给大屏的就是它）
                                 └─< HourlyProduction 小时产量（产量侧接入后写入）

    Line 产线 ──< PeriodSummary 日 / 月 / 年总计（产线级与车间级）

  · **Reading 存的是电表/流量计的累计值**（如 6,312,640.6 kWh），不是用量。
  · **HourlyUsage 才是「这个小时用了多少」**，由「本小时末读数 − 上小时末读数」得出。
  · 产线级、车间级的**小时**用量不落表，查询时按 Meter 聚合出来，避免两处口径不一致。
  · 但**日 / 月 / 年总计落表**（PeriodSummary）：这类聚合窗口长、算一次开销大，
    且大屏与后台都要读，落表才能保证两边数字完全一致、且历史数据可回溯。

⚠ 产量侧（灌装机计数器 / 装箱机 PLC / MES 班报工）**尚未改造**，
  因此 HourlyProduction 目前只有 `ingest_production` 写入的 **null 占位**
  （source=demo / quality=nodata），接口对这类行一律返回 **null**（不是 0），
  绝不拿电量反推产量。
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

    SOURCE_CONFIG = "config"
    SOURCE_ADMIN = "admin"
    SOURCE_CHOICES = [(SOURCE_CONFIG, "配置文件"), (SOURCE_ADMIN, "后台维护")]

    source = models.CharField(
        "配置来源", max_length=10, choices=SOURCE_CHOICES, default=SOURCE_CONFIG,
        help_text="后台维护的记录不会被 seed_registry 覆盖，除非加 --force",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "eep_gateway"
        ordering = ["code"]
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
    func_code = models.PositiveSmallIntegerField("功能码", default=3, help_text="读保持寄存器用 03")
    word_order = models.CharField(
        "32 位字序", max_length=8, default="big", choices=[("big", "高字在前"), ("little", "低字在前")]
    )

    # ── 台账信息（现场贴标签用，不影响采集）──
    asset_no = models.CharField("资产编号", max_length=64, blank=True, default="")
    install_date = models.DateField("安装日期", null=True, blank=True)

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

    SOURCE_CONFIG = "config"
    SOURCE_ADMIN = "admin"
    SOURCE_CHOICES = [(SOURCE_CONFIG, "配置文件"), (SOURCE_ADMIN, "后台维护")]

    source = models.CharField(
        "配置来源", max_length=10, choices=SOURCE_CHOICES, default=SOURCE_CONFIG,
        help_text="后台维护的记录不会被 seed_registry 覆盖，除非加 --force",
    )

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


class HourlyProduction(models.Model):
    """线别每时产量 —— 与 HourlyUsage 并列的第二条腿。

    为什么单独一张表、而不是塞进 HourlyUsage：
      · 两者的数据源、采集频率、质量判定完全不同（电量来自电表差分，产量来自计数器/PLC/MES）；
      · 产量往往需要人工修正（补报、作废），混在一起会把电量也改脏；
      · `quality` 语义也不同（产量有「班报工按班摊到小时」这种估算态）。

    口径：`output` 是**该小时实际产出**（瓶）。未上报时输出 null —— **绝不补 0**。

    ⚠ 当前产量数据源未接入，本表只有 `ingest_production` 写入的 null 占位
      （source=demo / quality=nodata），接口层据此识别「这不是真实产量」。
    """

    Q_OK = "ok"
    Q_MISSING = "missing"
    Q_NODATA = "nodata"
    Q_EST = "estimated"
    QUALITY_CHOICES = [
        (Q_OK, "正常上报"),
        (Q_MISSING, "应报未报"),
        (Q_NODATA, "数据源未接入（产量侧待改造）"),
        (Q_EST, "估算（如班报工摊到小时）"),
    ]
    #: 只有正常与估算的产量才允许进入合计
    TRUSTED = (Q_OK, Q_EST)

    SOURCE_COUNTER = "counter"
    SOURCE_PLC = "plc"
    SOURCE_MES = "mes"
    SOURCE_MANUAL = "manual"
    SOURCE_DEMO = "demo"
    SOURCE_NONE = "none"
    SOURCE_CHOICES = [
        (SOURCE_COUNTER, "灌装机计数器"),
        (SOURCE_PLC, "装箱机 PLC"),
        (SOURCE_MES, "MES 班次报工"),
        (SOURCE_MANUAL, "人工录入"),
        (SOURCE_DEMO, "演示占位（非真实产量）"),
        (SOURCE_NONE, "无数据源"),
    ]

    line = models.ForeignKey(Line, on_delete=models.CASCADE, related_name="production", verbose_name="产线")
    date = models.DateField("日期")
    hour = models.PositiveSmallIntegerField("小时")
    output = models.DecimalField("本小时产量(瓶)", max_digits=18, decimal_places=1, null=True, blank=True)
    quality = models.CharField("数据质量", max_length=12, choices=QUALITY_CHOICES, default=Q_MISSING)
    source = models.CharField("数据来源", max_length=12, choices=SOURCE_CHOICES, default=SOURCE_NONE)
    note = models.CharField("说明", max_length=200, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "eep_hourly_production"
        verbose_name = "小时产量"
        verbose_name_plural = "小时产量"
        constraints = [models.UniqueConstraint(fields=["line", "date", "hour"], name="uniq_line_hour_prod")]
        indexes = [models.Index(fields=["date", "hour"])]

    def __str__(self) -> str:
        return f"{self.line_id} {self.date} {self.hour:02d} {self.output}瓶 [{self.quality}]"


class PeriodSummary(models.Model):
    """日 / 月 / 年总计（产线级与车间级）—— 大屏与后台共用的一张汇总表。

    为什么要落表而不是每次现算：
      · 年 / 月聚合要扫数十万行小时数据，每次刷新都现算会把接口拖慢；
      · 大屏与后台必须拿到**完全相同**的数字，落表是唯一能保证两边一致的做法；
      · 历史月份封账后不再变化，落表后可直接作为报表与审计依据。

    `period_key` 取值：
      · dim = day    → '2026-09-22'
      · dim = month  → '2026-09'
      · dim = year   → '2026'
    `line` 为空表示**车间级**（全部产线合计）。
    """

    SCOPE_LINE = "line"
    SCOPE_WORKSHOP = "workshop"
    SCOPE_CHOICES = [(SCOPE_LINE, "产线级"), (SCOPE_WORKSHOP, "车间级")]

    DIM_DAY = "day"
    DIM_MONTH = "month"
    DIM_YEAR = "year"
    DIM_CHOICES = [(DIM_DAY, "日"), (DIM_MONTH, "月"), (DIM_YEAR, "年")]

    Q_OK = "ok"
    Q_PARTIAL = "partial"
    Q_MISSING = "missing"
    QUALITY_CHOICES = [
        (Q_OK, "完整"),
        (Q_PARTIAL, "部分时段缺数"),
        (Q_MISSING, "无数据"),
    ]

    scope = models.CharField("统计层级", max_length=10, choices=SCOPE_CHOICES)
    line = models.ForeignKey(
        Line, null=True, blank=True, on_delete=models.CASCADE, related_name="summaries", verbose_name="产线"
    )
    dim = models.CharField("统计维度", max_length=8, choices=DIM_CHOICES)
    period_key = models.CharField("周期标识", max_length=16)

    start_date = models.DateField("起始日期")
    end_date = models.DateField("结束日期")

    output = models.DecimalField("产量(瓶)", max_digits=20, decimal_places=1, default=0)
    kwh = models.DecimalField("用电量(kWh)", max_digits=20, decimal_places=3, default=0)
    water_t = models.DecimalField("用水量(t)", max_digits=16, decimal_places=3, default=0)
    steam_t = models.DecimalField("用汽量(t)", max_digits=16, decimal_places=3, default=0)

    plan_output = models.DecimalField("计划产量(瓶)", max_digits=20, decimal_places=1, default=0)
    unit_kwh = models.DecimalField(
        "单位电耗(kWh/千瓶)", max_digits=12, decimal_places=3, null=True, blank=True,
        help_text="产量为 0 或未接入时为 null，**不要用 0 兜底**",
    )

    covered_hours = models.PositiveSmallIntegerField("已覆盖小时数", default=0)
    quality = models.CharField("数据质量", max_length=10, choices=QUALITY_CHOICES, default=Q_OK)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "eep_period_summary"
        verbose_name = "周期总计"
        verbose_name_plural = "周期总计"
        constraints = [
            models.UniqueConstraint(fields=["scope", "line", "dim", "period_key"], name="uniq_period_summary")
        ]
        indexes = [models.Index(fields=["dim", "period_key"]), models.Index(fields=["scope", "dim"])]

    def __str__(self) -> str:
        who = self.line_id or "车间"
        return f"{self.dim}:{self.period_key} {who} {self.output}瓶/{self.kwh}kWh [{self.quality}]"


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
