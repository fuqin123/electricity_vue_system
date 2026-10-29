"""数据模型 —— v1.3.0 重新设计后的库表结构。

设计全文见 `backend/docs/数据库设计-v1.3.0.md`（含「为什么这么改」的逐条理由）。
这里只强调三条最容易改错的地方：

## 一、表名前缀统一为业务语义

    eep_line              → production_line      产线
    eep_gateway           → energy_gateway       采集网关
    eep_meter             → energy_meter         计量点（仪表）
    eep_reading           → energy_raw           原始累计读数
    eep_hourly_usage      → energy_hourly        小时用量（差值）
    eep_hourly_production → production_hourly    小时产量
    eep_collect_log       → energy_collect_log   采集日志
    eep_period_summary    → 拆成 energy_daily / energy_monthly / energy_yearly

## 二、分层：原始值 → 小时用量 → 日/月/年

    production_line ─┬─< energy_meter ─< energy_raw ─(差值)─> energy_hourly
                     │        └── energy_gateway
                     ├─< production_hourly
                     └─< energy_daily ─> energy_monthly ─> energy_yearly

  · `energy_raw.reading` 是**累计值**（如 6,312,639.3 kWh），不是用量；
  · `energy_hourly.consumption` 才是「这个小时用了多少」；
  · **日/月/年一律由 `energy_hourly` 累加得出**，绝不用「日末读数 − 日初读数」——
    后者在采集中断时会静默吞掉缺的那几小时（见 docs/02 的 B-502）。

## 三、两条铁律（模型层面就用可空字段挡住）

  1. **缺数绝不补 0。** 所有用量字段（`consumption`）与读数端点都可空：
     NULL = 没采到 / 不可信，0 = 确实没用。两者语义相反。
  2. **`ok` 与可人工复核的 `partial` 进入合计；`missing` / `reset` / `spike` 不进。**
     见 `EnergyHourly.TRUSTED`（严格可信）与 `SUMMABLE`（可计入合计）。

⚠ 产量侧（灌装机计数器 / 装箱机 PLC / MES 班报工）**尚未改造**，
  `production_hourly` 目前只有 `ingest_production` 写入的 null 占位，
  接口对这类行一律返回 null（不是 0），绝不拿电量反推产量。
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models


class EnergyType(models.Model):
    """能源类型（水 / 电 / 汽）—— 替代原来的 `Meter.kind` 字符串。

    为什么单独建表：能源类型是被 `energy_meter` / `energy_raw` / `energy_hourly`
    与三级汇总共同引用的维度，用字符串会在各处散落魔法值；建表后单位、名称、
    排序都只有一处定义。新增一种能源（如压缩空气）只需加一行 + 加计量点，
    **不用改代码**。
    """

    ELECTRIC = "ELECTRIC"
    WATER = "WATER"
    STEAM = "STEAM"

    #: 接口层给前端的短码（前端 `MeterPoint.kind` 用的是 elec/water/steam）
    FRONTEND_CODE = {ELECTRIC: "elec", WATER: "water", STEAM: "steam"}
    #: 默认单位（现场确认后以 `unit` 字段为准）
    DEFAULT_UNIT = {ELECTRIC: "kWh", WATER: "m³", STEAM: "t"}

    type_code = models.CharField("能源编码", max_length=20, unique=True)
    type_name = models.CharField("能源名称", max_length=50)
    unit = models.CharField("单位", max_length=20)
    sort = models.PositiveSmallIntegerField("排序", default=0)
    enabled = models.BooleanField("启用", default=True)

    class Meta:
        db_table = "energy_type"
        ordering = ["sort", "id"]
        verbose_name = "能源类型"
        verbose_name_plural = "能源类型"

    def __str__(self) -> str:
        return f"{self.type_name}({self.type_code})"

    @property
    def frontend_code(self) -> str:
        return self.FRONTEND_CODE.get(self.type_code, self.type_code.lower())


class ProductionLine(models.Model):
    """组织节点表 —— 由「产线 + 一条车间哨兵行」升级为**多车间树**。

    为什么升级（v1.5.2，见 `docs/车间归属与能耗分摊设计-草案v0.1.md`）：
      原设计只有**一条**车间哨兵行（`line_code='WORKSHOP'`），够不上现场实际 ——
      现场是「碳水车间 / 制造车间」两个车间，各带自己的产线，且车间下还有
      「辅机 / 瓶胚 / 调配」等非产线计量单元。`seed_registry` 会把所有
      `scope=workshop` 的仪表强行挂到唯一那条哨兵行上，多车间必然互相覆盖。

    ⚠ **哨兵行机制本身是对的，只是数量从 1 变成 N。** 它解决的是同一个问题：
      日/月/年汇总表要按 `line_id` 建唯一键，而车间级总表不属于任何产线。
      若把 `line_id` 设为可空，MySQL 的唯一索引会把多个 NULL 视为互不相同 ——
      唯一约束**形同失效**，重复行能照样插进去。所以每个车间各有一条真实记录
      （`node_type='workshop'`）来承载它的总表，唯一键处处有效。

    ⚠ 分类口径（`node_type` 与 `line_type` 的分工，**不要混用**）：
      · `line_type`  —— **兼容字段**，只区分「是不是车间级」。汇总/取数等
        既有代码全按它分支，多车间上线时不必逐处改。
      · `node_type`  —— **新分类字段**，三值：
          `workshop` 车间（碳水车间 / 制造车间）—— 挂车间总表
          `line`     产线（W103 / W101 / W102 / W301 / W104 / W105）—— 挂生产线电表，参与产量与达成率
          `aux`      辅助计量单元（辅机 / 瓶胚 / 调配）—— 挂辅机类电表，
                     **不参与**产量与分摊分母，但属于车间合计
      两者由 `sync_line_type()` 保持一致（`node_type='workshop' ⟺ line_type='workshop'`），
      避免出现「line_type 说是车间、node_type 说是产线」这种自相矛盾的行。

    ⚠ 车间电力口径（实测，勿再假设为推导值）：
      现场车间级电力**有实测总表**（辅机 / 瓶胚 / 调配等），车间电力合计 =
      本车间产线电表 + 本车间辅机类电表。旧注释里的「车间电力 = 6 条产线之和、
      无独立总表」是辅机未接入时的临时口径，已作废。
    """

    TYPE_LINE = "line"
    TYPE_WORKSHOP = "workshop"
    TYPE_CHOICES = [(TYPE_LINE, "产线"), (TYPE_WORKSHOP, "车间（总表）")]

    NODE_WORKSHOP = "workshop"
    NODE_LINE = "line"
    NODE_AUX = "aux"
    NODE_CHOICES = [
        (NODE_WORKSHOP, "车间"),
        (NODE_LINE, "产线"),
        (NODE_AUX, "辅助计量单元"),
    ]

    STATUS_ON = 1
    STATUS_OFF = 0

    WORKSHOP_CODE = "WORKSHOP"

    #: 一期已建的两个车间节点编码（配置 `workshops` 段的 code 即此值）
    CODE_CARB = "WS-CARB"
    CODE_MANU = "WS-MANU"

    @classmethod
    def default_workshop(cls):
        """默认车间节点 —— 「当前唯一实际在跑的车间」。

        ⚠ v1.5.2：旧代码到处写 `ProductionLine.objects.get(line_code='WORKSHOP')`，
          多车间改造后那条哨兵行的编码已改成 `WS-CARB`，这些调用会直接抛
          DoesNotExist。凡是要「随便拿一个车间来用」的地方改调本方法：
          优先碳水车间（一期唯一），不存在再退到任意一条 workshop 行。

        新代码**不要**用它做业务判断 —— 多车间下线后必须显式指定车间编码。
        """
        ws = cls.objects.filter(node_type=cls.NODE_WORKSHOP, line_code=cls.CODE_CARB).first()
        if ws is not None:
            return ws
        return cls.objects.filter(node_type=cls.NODE_WORKSHOP).order_by("sort", "line_code").first()

    line_code = models.CharField("编码", max_length=50, unique=True)
    line_name = models.CharField("名称", max_length=100)
    status = models.PositiveSmallIntegerField("状态", default=STATUS_ON, help_text="1 启用 / 0 停用")
    line_type = models.CharField("类型（兼容字段）", max_length=10, choices=TYPE_CHOICES, default=TYPE_LINE)
    node_type = models.CharField(
        "节点类型", max_length=10, choices=NODE_CHOICES, default=NODE_LINE,
        help_text="workshop 车间 / line 产线 / aux 辅助计量单元（辅机、瓶胚、调配）",
    )
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children",
        verbose_name="上级节点",
        help_text="产线 / 辅助单元指向所属车间；车间节点为空",
    )
    #: 静态台账参数：没有它算不出计划产量与达成率（不是产量数据）。
    #: 对 workshop / aux 节点无意义，保持 0 即可。
    rated = models.PositiveIntegerField("额定产能(瓶/小时)", default=0)
    product = models.CharField("品项", max_length=64, blank=True, default="")
    sort = models.PositiveSmallIntegerField("排序", default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "production_line"
        ordering = ["sort", "line_code"]
        verbose_name = "组织节点（车间/产线/辅助单元）"
        verbose_name_plural = "组织节点（车间/产线/辅助单元）"

    def __str__(self) -> str:
        return f"{self.line_code} {self.line_name}"

    # ── 下面三个属性是**唯一的判定入口**，别在别处再写 node_type == '...' ──
    # 散落的字面量比较正是 B-519/B-532 那类「漏改一处」的温床。

    @property
    def is_workshop(self) -> bool:
        """车间节点（挂车间总表）。等价于旧的 `line_type == workshop`。"""
        return self.node_type == self.NODE_WORKSHOP

    @property
    def is_line(self) -> bool:
        """真实产线（参与产量 / 达成率 / 分摊分母）。"""
        return self.node_type == self.NODE_LINE

    @property
    def is_aux(self) -> bool:
        """辅助计量单元（辅机 / 瓶胚 / 调配）—— 计入车间合计，但不是产线。"""
        return self.node_type == self.NODE_AUX

    def sync_line_type(self) -> None:
        """把 `line_type` 与 `node_type` 对齐（保存前调）。"""
        self.line_type = (
            self.TYPE_WORKSHOP if self.node_type == self.NODE_WORKSHOP else self.TYPE_LINE
        )

    def save(self, *args, **kwargs):
        self.sync_line_type()
        super().save(*args, **kwargs)

    # ── 常用查询集（收敛散落的过滤条件，见本文档 §七 的影响面清单）──

    @classmethod
    def lines_qs(cls):
        """真实产线（不含车间节点与辅助单元）。"""
        return cls.objects.filter(node_type=cls.NODE_LINE)

    @classmethod
    def active_lines_qs(cls):
        """启用中的真实产线 —— 大屏 / 取数的默认范围。"""
        return cls.objects.filter(status=cls.STATUS_ON, node_type=cls.NODE_LINE)

    @classmethod
    def workshops_qs(cls):
        return cls.objects.filter(node_type=cls.NODE_WORKSHOP)


class EnergyGateway(models.Model):
    """采集网关：串口服务器（Modbus RTU over TCP）或第三方数据平台（HTTP API）。"""

    KIND_MODBUS = "modbus_rtu_tcp"
    KIND_HTTP = "http_api"
    KIND_CHOICES = [
        (KIND_MODBUS, "串口服务器（Modbus RTU over TCP）"),
        (KIND_HTTP, "HTTP API（第三方数据平台）"),
    ]

    SOURCE_CONFIG = "config"
    SOURCE_ADMIN = "admin"
    SOURCE_CHOICES = [(SOURCE_CONFIG, "配置文件"), (SOURCE_ADMIN, "后台维护")]

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
    source = models.CharField(
        "配置来源", max_length=10, choices=SOURCE_CHOICES, default=SOURCE_CONFIG,
        help_text="后台维护的记录不会被 seed_registry 覆盖，除非加 --force",
    )

    # ── 最近一次「网关连接测试」的证据（B-542）──────────────────────
    # 网关在线与否由**心跳**推导：采集日志 ∪ 点位读数成功 ∪ 本表 last_ok_at。
    # 但心跳只能证明「这一刻通」，无法证明「不通」——
    # 于是现场点「测试连接」失败后，状态会一直停在「在线」（直到旧心跳过期，最长约 91 分钟），
    # 与操作者眼前看到的结果正好相反。
    # 增加失败证据后，判定改为**最新证据优先**：一次更新的失败可以推翻旧的成功。
    last_ok_at = models.DateTimeField("最近测试成功", null=True, blank=True)
    last_fail_at = models.DateTimeField("最近测试失败", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "energy_gateway"
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


class EnergyMeter(models.Model):
    """计量点（仪表）—— 采集对象，一行一块表。

    为什么必须有这张表：采集主体是**表**而不是产线。一条产线有 2—3 块表
    （三线 3 块、五线 3 块），设备参数（网关 / 从站号 / 寄存器 / 倍率，
    或 HTTP 取值路径）只有这里能放。这也是 `energy_raw` 的唯一键落在
    `meter_id` 而不是 `line_id` 的原因（见设计文档 §一.2）。
    """

    SCOPE_LINE = "line"
    SCOPE_WORKSHOP = "workshop"
    SCOPE_CHOICES = [(SCOPE_LINE, "产线"), (SCOPE_WORKSHOP, "车间级")]

    ROLE_LINE = "line"
    ROLE_AUX = "aux"
    ROLE_TOTAL = "total"
    ROLE_CHOICES = [(ROLE_LINE, "生产线"), (ROLE_AUX, "辅机"), (ROLE_TOTAL, "车间总表")]

    #: 「用电类型 / 用水类型」—— 界面三级筛选的第二级（车间 → 类型 → 设备）。
    #: 取值直接来自现场接口表（生产线用电 / 辅机用电 / 瓶胚用电 / 调配用电 …），
    #: 因此用**自由字符串**而不是 choices：现场还会加类型，写死枚举就得改代码。
    #: 同类型跨能源不冲突（汽表该字段留空）。
    USAGE_LINE = "生产线用电"
    USAGE_AUX = "辅机用电"
    USAGE_PREFORM = "瓶胚用电"
    USAGE_BLEND = "调配用电"

    SOURCE_CONFIG = "config"
    SOURCE_ADMIN = "admin"
    SOURCE_CHOICES = [(SOURCE_CONFIG, "配置文件"), (SOURCE_ADMIN, "后台维护")]

    # ── 寄存器数据类型（v1.5.3）────────────────────────────────
    # 为什么必须有这一列：现场并非所有表都是「32 位无符号整数」。
    # 例：碳水市政水表（`W-CARB-CITY-1`）起始地址 68、**4 个寄存器、
    # IEEE754 双精度（double）**，按 32 位整数拼出来的是一个完全无关的大整数 ——
    # 采集不报错、小时差值照算，只有数字荒谬（现场核对时表现为「水表读数不对」）。
    #
    # 类型决定两件事，且**只有这一个真值来源**：
    #   ① 拼出来是什么数（整数 / 浮点）；
    #   ② 要读几个寄存器（16 位→1、32 位→2、64 位→4）。
    # `registers` 列保留为**兼容镜像**，由 `sync_registers()` 与本节保持同步。
    DT_UINT16 = "uint16"
    DT_INT16 = "int16"
    DT_UINT32 = "uint32"
    DT_INT32 = "int32"
    DT_FLOAT32 = "float32"
    DT_FLOAT64 = "float64"

    DATA_TYPE_CHOICES = [
        (DT_UINT16, "16 位无符号整数（1 个寄存器）"),
        (DT_INT16, "16 位有符号整数（1 个寄存器）"),
        (DT_UINT32, "32 位无符号整数（2 个寄存器）"),
        (DT_INT32, "32 位有符号整数（2 个寄存器）"),
        (DT_FLOAT32, "32 位浮点数 IEEE754（2 个寄存器）"),
        (DT_FLOAT64, "64 位双精度 IEEE754 / double（4 个寄存器）"),
    ]
    #: 数据类型 → 寄存器个数。**唯一来源**：读取长度由此推导，不看 `registers` 列。
    REGISTERS_BY_TYPE = {
        DT_UINT16: 1, DT_INT16: 1,
        DT_UINT32: 2, DT_INT32: 2, DT_FLOAT32: 2,
        DT_FLOAT64: 4,
    }
    #: 默认值 = 现场 13 块电表的既有口径（32 位无符号），保证老配置行为不变。
    DATA_TYPE_DEFAULT = DT_UINT32

    BYTE_BIG = "big"
    BYTE_LITTLE = "little"
    BYTE_ORDER_CHOICES = [(BYTE_BIG, "大端（寄存器内高字节在前）"), (BYTE_LITTLE, "小端（寄存器内低字节在前）")]

    code = models.CharField("计量点编号", max_length=32, unique=True)
    name = models.CharField("计量点名称", max_length=64)
    energy_type = models.ForeignKey(
        EnergyType, on_delete=models.PROTECT, related_name="meters", verbose_name="能源类型"
    )
    scope = models.CharField("归属层级", max_length=10, choices=SCOPE_CHOICES, default=SCOPE_LINE)
    role = models.CharField("计量角色", max_length=10, choices=ROLE_CHOICES, default=ROLE_LINE)
    usage_type = models.CharField(
        "用能类型", max_length=32, blank=True, default="",
        help_text="界面二级分类，如「生产线用电 / 辅机用电 / 瓶胚用电 / 调配用电」；汽/水表可为空",
    )
    device_name = models.CharField(
        "设备名", max_length=64, blank=True, default="",
        help_text="现场接口表里的「设备」列，如「吹瓶机 / 冰水机和水塔 / 溶糖CIP」——界面第三级",
    )
    line = models.ForeignKey(
        ProductionLine, on_delete=models.PROTECT, related_name="meters", verbose_name="所属节点",
        help_text="产线表挂产线节点、辅机表挂 aux 节点、车间水/汽总表挂 workshop 车间节点",
    )
    gateway = models.ForeignKey(
        EnergyGateway, on_delete=models.PROTECT, related_name="meters", verbose_name="所属网关"
    )

    # ── 串口服务器（Modbus）参数 ──
    slave = models.PositiveSmallIntegerField("从站号", null=True, blank=True)
    register = models.PositiveIntegerField("寄存器起始地址", null=True, blank=True)
    registers = models.PositiveSmallIntegerField(
        "寄存器数量", default=2,
        help_text="兼容镜像：由「数据类型」推导，保存时自动同步；改它不生效",
    )
    func_code = models.PositiveSmallIntegerField("功能码", default=3, help_text="读保持寄存器用 03")
    data_type = models.CharField(
        "数据类型", max_length=10, choices=DATA_TYPE_CHOICES, default=DATA_TYPE_DEFAULT,
        help_text="决定拼出整数还是浮点数，以及要读几个寄存器。水表（double）选 float64",
    )
    byte_order = models.CharField(
        "字节序", max_length=8, choices=BYTE_ORDER_CHOICES, default=BYTE_BIG,
        help_text="单个寄存器内部的字节顺序；与「字序」一起决定 4 种组合（现场电表为 大端+高字在前）",
    )
    word_order = models.CharField(
        "字序", max_length=8, default="big",
        choices=[("big", "高字在前（ABCD）"), ("little", "低字在前（CDAB）")],
    )
    asset_no = models.CharField("资产编号", max_length=64, blank=True, default="")
    install_date = models.DateField("安装日期", null=True, blank=True)

    # ── HTTP API 参数 ──
    url_path = models.CharField("接口路径", max_length=200, blank=True, default="")
    params = models.JSONField("接口参数", default=dict, blank=True)
    value_path = models.CharField("累计值取值路径", max_length=120, blank=True, default="")
    time_path = models.CharField("采集时间取值路径", max_length=120, blank=True, default="")

    scale = models.DecimalField("倍率", max_digits=12, decimal_places=4, default=1)
    unit = models.CharField("单位", max_length=16, default="kWh")
    max_hourly = models.DecimalField(
        "单小时上限", max_digits=18, decimal_places=3, null=True, blank=True,
        help_text="差值超过它判 spike、不计入合计（换表/断讯补传会产生尖峰）",
    )
    enabled = models.BooleanField("启用", default=True)
    remark = models.CharField("备注", max_length=200, blank=True, default="")
    source = models.CharField(
        "配置来源", max_length=10, choices=SOURCE_CHOICES, default=SOURCE_CONFIG,
        help_text="后台维护的记录不会被 seed_registry 覆盖，除非加 --force",
    )

    # ── 最近一次采集结果（后台列表直接读，不用回表）──
    last_value = models.DecimalField("最近累计值", max_digits=18, decimal_places=3, null=True, blank=True)
    last_at = models.DateTimeField("最近采集时间", null=True, blank=True)
    last_ok = models.BooleanField("最近采集成功", default=False)
    last_error = models.CharField("最近错误", max_length=200, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "energy_meter"
        ordering = ["code"]
        verbose_name = "计量点"
        verbose_name_plural = "计量点"

    def __str__(self) -> str:
        return f"{self.code} {self.name}"

    @property
    def type_code(self) -> str:
        """能源编码（ELECTRIC / WATER / STEAM），省去到处写 `meter.energy_type.type_code`。"""
        return self.energy_type.type_code

    @property
    def workshop(self) -> ProductionLine | None:
        """所属车间节点 —— 界面三级筛选的第一级。

        产线直接挂在车间下，所以取 `line.parent`；若计量点本身挂在车间节点上
        （车间总表），就是 `line` 自己。**不再假设全库只有一个车间**
        （旧代码 `ProductionLine.objects.filter(line_type=workshop).first()` 在多车间下会取错）。
        """
        node = self.line
        if node is None:
            return None
        if node.node_type == ProductionLine.NODE_WORKSHOP:
            return node
        return node.parent

    @property
    def category_path(self) -> tuple[str, str, str]:
        """(车间名, 用能类型, 设备名) —— 界面三级筛选的联合键。

        比逐个字段拼更可靠：三处都从这里取，界面与接口不会各拼一套。
        """
        ws = self.workshop
        return (
            ws.line_name if ws is not None else "",
            self.usage_type or "",
            self.device_name or self.name,
        )

    @property
    def expected_registers(self) -> int:
        """按数据类型推出的寄存器个数（16 位→1、32 位→2、64 位→4）。

        ⚠ 采集侧读几个寄存器**只看这里**，不看 `registers` 列 ——
          两个来源并存必然出现「配的是 float64、却只读了 2 个寄存器」这类错配，
          而它不会报错，只会给出一个数值离谱的读数（v1.5.2「同一指标只能有一条规则」）。
        """
        return self.REGISTERS_BY_TYPE.get(self.data_type, 2)

    def sync_registers(self) -> bool:
        """把兼容镜像列 `registers` 对齐到 `expected_registers`；有变化返回 True。

        只为「后台列表 / 接口 / 前端表单」显示用，采集不读它。
        """
        want = self.expected_registers
        if self.registers != want:
            self.registers = want
            return True
        return False

    def save(self, *args, **kwargs):
        """写库前强制对齐 `registers`（单点，覆盖所有写入路径）。

        ⚠ 为什么放在 `save()` 而不是只放在接口层：`objects.create()`、
          `seed_registry`、Django admin 都能**绕过接口层**直接写库，
          而那些路径同样必须保持「读几个寄存器」与「按什么类型解」一致。
          放在这里才叫「一个真值来源」；只放在 `_save_meter` 里，
          任何一条新写入路径都可能重新引入漂移（本次就是被一条直连 create 的
          测试用例照出来的：类型 float64、镜像列却还是 2）。
        """
        update_fields = kwargs.get("update_fields")
        if update_fields is None:
            self.sync_registers()
        else:
            fields = set(update_fields)
            # 改数据类型、或本身就在写 registers 时，把推导结果一并写下去
            if "registers" in fields or "data_type" in fields:
                self.sync_registers()
                fields.add("registers")
                kwargs["update_fields"] = fields
        super().save(*args, **kwargs)


class EnergyRaw(models.Model):
    """原始数据表：**累计读数**（如 6,312,639.3 kWh），不是用量。

    唯一键是 `(meter_id, collect_time)`，不是 `(line_id, energy_type_id, collect_time)`：
      · 采集主体是表 —— 一条产线有多块表，按产线唯一就必须先求和、丢掉单表读数；
      · 车间总表没有 line_id，而 MySQL 把多个 NULL 视为互不相同 → 唯一约束失效。
    你要的「按产线 + 能源类型 + 时间查」由普通索引 `idx_raw_line_time` 覆盖。
    """

    meter = models.ForeignKey(
        EnergyMeter, on_delete=models.CASCADE, related_name="readings", verbose_name="计量点"
    )
    line = models.ForeignKey(
        ProductionLine, on_delete=models.PROTECT, related_name="readings", verbose_name="所属产线",
        help_text="入库时的归属快照（历史归属不随产线调整而变）",
    )
    energy_type = models.ForeignKey(
        EnergyType, on_delete=models.PROTECT, related_name="readings", verbose_name="能源类型"
    )
    collect_time = models.DateTimeField("采集时间", db_index=True, help_text="精确到小时，如 2026-09-24 10:00:00")
    reading = models.DecimalField(
        "累计读数", max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="采集失败时为 NULL —— 绝不能写 0，0 会被差值当成真实读数",
    )
    ok = models.BooleanField("采集成功", default=True)
    raw = models.CharField("原始报文/响应", max_length=200, blank=True, default="")
    error = models.CharField("错误信息", max_length=200, blank=True, default="")
    created_at = models.DateTimeField("入库时间", auto_now_add=True)

    class Meta:
        db_table = "energy_raw"
        ordering = ["-collect_time", "meter_id"]
        verbose_name = "原始读数"
        verbose_name_plural = "原始读数"
        constraints = [
            models.UniqueConstraint(fields=["meter", "collect_time"], name="uk_raw_meter_time"),
        ]
        indexes = [
            models.Index(fields=["line", "energy_type", "collect_time"], name="idx_raw_line_time"),
            models.Index(fields=["collect_time"], name="idx_raw_collect_time"),
        ]

    def __str__(self) -> str:
        return f"{self.meter_id}@{self.collect_time:%Y-%m-%d %H:%M}={self.reading}"


class EnergyHourly(models.Model):
    """小时用量 —— **日 / 月 / 年汇总的唯一数据源**。

    为什么不直接用「日末读数 − 日初读数」：采集中断 5 小时时，后者照样给出一个数，
    把缺的那段静默吞掉，界面上完全看不出异常（B-502）。逐小时算差值 + 质量标记，
    缺的时段就是 `missing`，合计宁可空白也不给偏小值。
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
        (Q_RESET, "累计值回退"),
        (Q_SPIKE, "异常尖峰"),
    ]
    #: 严格可信：用于需要「绝对干净」的判定（如覆盖率/质量告警的原有口径）
    TRUSTED = (Q_OK,)
    #: 可计入合计：partial 是端点偏差/补采造成的**有值但时段不完美**的数据，
    #: 现场要求保留并在 11:00 生成人工复核告警；missing/reset/spike 仍不进合计。
    SUMMABLE = (Q_OK, Q_PARTIAL)

    meter = models.ForeignKey(
        EnergyMeter, on_delete=models.CASCADE, related_name="hourly", verbose_name="计量点"
    )
    line = models.ForeignKey(
        ProductionLine, on_delete=models.PROTECT, related_name="hourly", verbose_name="所属产线",
    )
    energy_type = models.ForeignKey(
        EnergyType, on_delete=models.PROTECT, related_name="hourly", verbose_name="能源类型"
    )
    stat_date = models.DateField("归属日期")
    stat_hour = models.PositiveSmallIntegerField("归属小时")
    consumption = models.DecimalField(
        "本小时用量", max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="缺数 / 换表 / 尖峰时为 NULL —— 绝不补 0",
    )
    unit = models.CharField("单位", max_length=20, default="kWh")
    quality = models.CharField("数据质量", max_length=10, choices=QUALITY_CHOICES, default=Q_OK)
    start_reading = models.DecimalField("起点累计值", max_digits=18, decimal_places=4, null=True, blank=True)
    end_reading = models.DecimalField("终点累计值", max_digits=18, decimal_places=4, null=True, blank=True)
    start_at = models.DateTimeField("区间起点(读数时间)", null=True, blank=True)
    end_at = models.DateTimeField("区间终点(读数时间)", null=True, blank=True)
    note = models.CharField("说明", max_length=200, blank=True, default="")
    computed_at = models.DateTimeField("计算时间", auto_now=True)

    class Meta:
        db_table = "energy_hourly"
        ordering = ["stat_date", "stat_hour", "meter_id"]
        verbose_name = "小时用量"
        verbose_name_plural = "小时用量"
        constraints = [
            models.UniqueConstraint(fields=["meter", "stat_date", "stat_hour"], name="uk_hourly"),
        ]
        indexes = [models.Index(fields=["stat_date", "stat_hour"], name="idx_hourly_period")]

    def __str__(self) -> str:
        return f"{self.meter_id} {self.stat_date} {self.stat_hour}时={self.consumption}"


class _PeriodSummaryBase(models.Model):
    """日 / 月 / 年汇总的公共部分（抽象基类，不建表）。

    ⚠ `consumption` 与 `unit_kwh` **必须可空**：该周期一条可信数据都没有时，
      它是「未知」而不是 0。写成 NOT NULL + default 0，等于把「没采到」硬说成
      「用了 0」，而 0 在这个语境里是「确实没用」—— 与现场理解正好相反。

    ⚠ `covered_*` / `expected_*` 是界面可信度的关键：没有它，
      「今日 300 kWh」和「今日只统计到 2 小时共 300 kWh」在页面上长得一模一样，
      后者会被读成「今天几乎没用电」（B-403 现场踩过）。
    """

    Q_OK = "ok"
    Q_PARTIAL = "partial"
    Q_MISSING = "missing"
    QUALITY_CHOICES = [(Q_OK, "完整"), (Q_PARTIAL, "部分时段缺数"), (Q_MISSING, "无数据")]

    line = models.ForeignKey(
        ProductionLine, on_delete=models.CASCADE, related_name="%(class)s_set", verbose_name="产线"
    )
    energy_type = models.ForeignKey(
        EnergyType, on_delete=models.PROTECT, related_name="%(class)s_set", verbose_name="能源类型"
    )
    consumption = models.DecimalField(
        "消耗量", max_digits=18, decimal_places=4, null=True, blank=True
    )
    unit = models.CharField("单位", max_length=20, blank=True, default="")
    covered_count = models.PositiveSmallIntegerField("已覆盖时段数", default=0)
    expected_count = models.PositiveSmallIntegerField("应有时段数", default=0)
    quality = models.CharField("数据质量", max_length=10, choices=QUALITY_CHOICES, default=Q_OK)
    computed_at = models.DateTimeField("计算时间", auto_now=True)

    class Meta:
        abstract = True


class EnergyDaily(_PeriodSummaryBase):
    """日汇总：日 × 产线 × 能源类型。

    为车间哨兵行（水 / 汽总表）也会落行；车间**电力**合计没有总表，
    由 6 条产线之和得出（查询时算，不落行）。
    """

    stat_date = models.DateField("统计日期")
    stat_year = models.SmallIntegerField("年", default=0, db_index=True)
    stat_month = models.CharField("年月(YYYY-MM)", max_length=7, blank=True, default="")
    start_reading = models.DecimalField("日初读数", max_digits=18, decimal_places=4, null=True, blank=True)
    end_reading = models.DecimalField("日末读数", max_digits=18, decimal_places=4, null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "energy_daily"
        ordering = ["-stat_date", "line_id"]
        verbose_name = "日汇总"
        verbose_name_plural = "日汇总"
        constraints = [
            models.UniqueConstraint(fields=["line", "energy_type", "stat_date"], name="uk_daily"),
        ]

    def __str__(self) -> str:
        return f"{self.stat_date} {self.line_id}/{self.energy_type_id}={self.consumption}"


class EnergyMonthly(_PeriodSummaryBase):
    """月汇总：月 × 产线 × 能源类型。由**日行累加**，保证「月 = 各日之和」。"""

    stat_month = models.DateField("统计月份", help_text="存当月第一天，如 2026-09-01")
    stat_year = models.SmallIntegerField("年", default=0, db_index=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "energy_monthly"
        ordering = ["-stat_month", "line_id"]
        verbose_name = "月汇总"
        verbose_name_plural = "月汇总"
        constraints = [
            models.UniqueConstraint(fields=["line", "energy_type", "stat_month"], name="uk_monthly"),
        ]

    def __str__(self) -> str:
        return f"{self.stat_month:%Y-%m} {self.line_id}/{self.energy_type_id}={self.consumption}"


class EnergyYearly(_PeriodSummaryBase):
    """年汇总：年 × 产线 × 能源类型。同样由日行累加。"""

    stat_year = models.SmallIntegerField("统计年份")

    class Meta:
        db_table = "energy_yearly"
        ordering = ["-stat_year", "line_id"]
        verbose_name = "年汇总"
        verbose_name_plural = "年汇总"
        constraints = [
            models.UniqueConstraint(fields=["line", "energy_type", "stat_year"], name="uk_yearly"),
        ]

    def __str__(self) -> str:
        return f"{self.stat_year} {self.line_id}/{self.energy_type_id}={self.consumption}"


class ProductionHourly(models.Model):
    """小时产量（产量侧未接入，仅占位）。

    产量**不是一种能源**，因此不放进 `energy_*` 汇总表；
    单位电耗（kWh/千瓶）在查询时用 `energy_daily(ELECTRIC) ÷ 产量` 算，两边各自可空。
    """

    Q_OK = "ok"
    Q_MISSING = "missing"
    Q_NODATA = "nodata"
    Q_EST = "estimated"
    QUALITY_CHOICES = [
        (Q_OK, "正常"),
        (Q_MISSING, "缺数"),
        (Q_NODATA, "数据源未接入"),
        (Q_EST, "估算"),
    ]
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
        (SOURCE_MANUAL, "人工填报"),
        (SOURCE_DEMO, "演示占位"),
        (SOURCE_NONE, "未接入"),
    ]

    line = models.ForeignKey(
        ProductionLine, on_delete=models.CASCADE, related_name="production", verbose_name="产线"
    )
    stat_date = models.DateField("日期")
    stat_hour = models.PositiveSmallIntegerField("小时")
    output = models.DecimalField("本小时产量(瓶)", max_digits=18, decimal_places=1, null=True, blank=True)
    quality = models.CharField("数据质量", max_length=12, choices=QUALITY_CHOICES, default=Q_MISSING)
    source = models.CharField("数据来源", max_length=12, choices=SOURCE_CHOICES, default=SOURCE_NONE)
    note = models.CharField("说明", max_length=200, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "production_hourly"
        ordering = ["stat_date", "stat_hour"]
        verbose_name = "小时产量"
        verbose_name_plural = "小时产量"
        constraints = [
            models.UniqueConstraint(fields=["line", "stat_date", "stat_hour"], name="uk_production_hourly"),
        ]

    def __str__(self) -> str:
        return f"{self.line_id} {self.stat_date} {self.stat_hour}时={self.output}"


class PeriodSummary:
    """周期维度的常量容器 —— **注意：它不再是一张表**。

    v1.3.0 把原来的单表 `eep_period_summary`（一行同时含电/水/汽/产量）
    拆成了按能源类型分行的三张表：`energy_daily` / `energy_monthly` / `energy_yearly`。

    之所以保留这个类名：维度常量（day/month/year、line/workshop、质量取值）
    在整个工程里有六十多处引用，把它们集中放在这里，语义不变、引用不用改。
    **查询与写入**请走 `services/summary.py`（`period_rows` 读、`summarize_*` 写）。
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


class EnergyCollectLog(models.Model):
    """每轮采集的日志：后台「采集日志」页与巡检脚本都读它。"""

    gateway = models.ForeignKey(
        EnergyGateway, on_delete=models.CASCADE, related_name="logs", verbose_name="网关"
    )
    started_at = models.DateTimeField("开始时间")
    finished_at = models.DateTimeField("结束时间", null=True, blank=True)
    duration_ms = models.PositiveIntegerField("耗时(ms)", default=0)
    total = models.PositiveSmallIntegerField("点位总数", default=0)
    ok_count = models.PositiveSmallIntegerField("成功", default=0)
    fail_count = models.PositiveSmallIntegerField("失败", default=0)
    message = models.CharField("说明", max_length=500, blank=True, default="")

    class Meta:
        db_table = "energy_collect_log"
        ordering = ["-started_at"]
        verbose_name = "采集日志"
        verbose_name_plural = "采集日志"

    def __str__(self) -> str:
        return f"{self.gateway_id} {self.started_at:%Y-%m-%d %H:%M} {self.ok_count}/{self.total}"


class SystemParam(models.Model):
    """系统参数 —— 成本单价、箱规、标准费用等的**唯一真值来源**。

    为什么落库而不是写在配置文件里：这些数由能源管理员/财务按周期维护（电价有峰谷、
    汽价随结算调整），改一次不该发一次版；后台「系统参数」页就是它的维护入口。
    与 ADR-007（阈值以系统参数为唯一真值）同一条思路。

    ⚠ `value` 可空表示「**尚未标定**」，与「标定成 0」是两回事：
      · 标准变动费用为 NULL → 图上不画那条对比虚线；
      · 若当成 0，界面会出现「实际费用远高于标准 0.00 元/箱」的假结论 ——
        与本项目反复强调的「缺数绝不补 0」是同一条铁律。
    """

    key = models.CharField("参数键", max_length=64, unique=True, help_text="代码里按这个键取值")
    name = models.CharField("参数名称", max_length=64)
    value = models.DecimalField("参数值", max_digits=14, decimal_places=4, null=True, blank=True)
    unit = models.CharField("单位", max_length=24, blank=True, default="")
    group = models.CharField("分组", max_length=24, default="成本参数")
    remark = models.CharField("说明", max_length=200, blank=True, default="")
    updated_at = models.DateTimeField("更新时间", auto_now=True)
    updated_by = models.CharField("更新人", max_length=32, blank=True, default="")

    class Meta:
        db_table = "system_param"
        ordering = ["group", "key"]
        verbose_name = "系统参数"
        verbose_name_plural = "系统参数"

    def __str__(self) -> str:
        return f"{self.key}={self.value}{self.unit}"

    @property
    def number(self) -> Decimal | None:
        """取值：未标定为 None（调用方必须区分 None 与 0）。"""
        return self.value


class StandardUnitConsumption(models.Model):
    """标准单耗：按**产线 × 瓶型容量 × 能源类型**维护，单位统一为「能源单位 / 千瓶」。

    同一产线会切换瓶型（500ml / 550ml / 1L），标准单耗必须带上瓶型容量；
    后台配置页把同一 (line, bottle_capacity) 的电/水/汽三行拼成一行显示。

    为什么独立一张表，而不是塞进 SystemParam：
      · 参数键会随产线 / 瓶型增删而漂移，既没有外键也没有组合唯一约束；
      · 外键 + unique(line, bottle_capacity, energy_type) 才能表达稳定配置。
    能源单价 / 箱规仍留在 SystemParam（全厂参数，不按产线拆分）。

    `value = NULL` 表示**未标定**；0 在配置页显示 `-`，且不参与目标线计算。
    水 / 汽目前只有车间总表，没有分线实测；这两列仍允许配置，界面标注「无分线实测」。
    """

    line = models.ForeignKey(
        ProductionLine, on_delete=models.CASCADE, related_name="standard_units", verbose_name="产线"
    )
    energy_type = models.ForeignKey(
        EnergyType, on_delete=models.CASCADE, related_name="standard_units", verbose_name="能源类型"
    )
    bottle_capacity = models.CharField(
        "瓶型容量", max_length=32, blank=True, default="",
        help_text="如 500ml / 1L；同一产线可按瓶型分别配置",
    )
    value = models.DecimalField(
        "标准单耗", max_digits=14, decimal_places=4, null=True, blank=True,
        help_text="每千瓶消耗量；NULL=未标定，0 在界面显示为 -",
    )
    remark = models.CharField("说明", max_length=200, blank=True, default="")
    updated_at = models.DateTimeField("更新时间", auto_now=True)
    updated_by = models.CharField("更新人", max_length=32, blank=True, default="")

    class Meta:
        db_table = "energy_standard_unit"
        ordering = ["line__sort", "line__line_code", "bottle_capacity", "energy_type__type_code"]
        verbose_name = "标准单耗"
        verbose_name_plural = "标准单耗"
        constraints = [
            models.UniqueConstraint(
                fields=("line", "bottle_capacity", "energy_type"),
                name="uniq_standard_line_capacity_energy",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.line.line_code} {self.bottle_capacity or '-'} {self.energy_type.type_code}={self.value}"


class EnergyReviewLog(models.Model):
    """异常时段人工复核的每日执行记录（幂等标记 + 审计）。

    11:00（可配置）的复核任务由 collect 的告警评估顺带触发：当天第一次跑完后写一行，
    之后每 5 分钟评估时跳过，避免同一批异常时段反复生成/累加告警。
    未执行复核时，异常时段数据仍按正常口径参与汇总与展示。
    """

    review_date = models.DateField("复核日期", unique=True)
    target_day = models.DateField("目标生产日")
    reviewed_at = models.DateTimeField("执行时间", auto_now_add=True)
    abnormal_hours = models.PositiveIntegerField("异常小时数", default=0)
    alarms_created = models.PositiveIntegerField("生成告警数", default=0)

    class Meta:
        db_table = "energy_review_log"
        ordering = ["-review_date"]
        verbose_name = "异常数据复核记录"
        verbose_name_plural = "异常数据复核记录"

    def __str__(self) -> str:
        return f"{self.review_date} → {self.target_day} {self.abnormal_hours}h"


class EnergyAlarm(models.Model):
    """能源计量告警 —— 「数据没被正确计量」这件事的落库记录。

    为什么要落库，而不是像以前那样在快照里现算一条报一条：

      · **确认（ack）必须有状态**。现算的话值班人员点了「确认」，下一轮采集
        又把同一条重新算出来挂回列表 —— 那等于没有确认功能，只能靠屏蔽解决，
        而屏蔽会让人开始忽略整个告警页。
      · **恢复要能自动关闭**。问题消失后还挂着 active，就是「狼来了」，
        几次之后告警就没人看了。
      · **首次/最近/次数**是现场判断严重程度的依据：一块表「今天抖了 3 次」
        和「连续挂了 6 小时」处理方式完全不同，现算只留得下最后一帧。

    `code` 是**稳定标识**（如 `METER-FAIL:E-A1-01`），同一件事反复检测到
    只更新 `last_at` / `repeat`，不新增行 —— 采集每 5 分钟跑一轮，
    不去重的话一块坏表能把告警页刷成垃圾堆。

    ⚠ `level` / `status` 的取值必须与 `docs/api-contract.md` §4.4 一致：
      前端 `AlarmLevelTag.vue` 的颜色映射和告警页的筛选下拉都是按那套枚举写的。
    """

    # 级别：与契约、前端标签组件同源（不要另发明一套）
    LEVEL_CRITICAL = "critical"
    LEVEL_MAJOR = "major"
    LEVEL_MINOR = "minor"
    LEVEL_INFO = "info"
    LEVEL_CHOICES = (
        (LEVEL_CRITICAL, "严重"),
        (LEVEL_MAJOR, "重要"),
        (LEVEL_MINOR, "次要"),
        (LEVEL_INFO, "提示"),
    )

    STATUS_ACTIVE = "active"
    STATUS_ACKED = "acked"
    STATUS_RESOLVED = "resolved"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, "未处理"),
        (STATUS_ACKED, "已确认"),
        (STATUS_RESOLVED, "已恢复"),
    )

    # 告警类别 —— 决定「该找谁处理」，也是去重 code 的前缀
    CAT_METER_UNCONNECTED = "meter_unconnected"
    CAT_METER_FAIL = "meter_collect_fail"
    CAT_HOUR_QUALITY = "hour_quality"
    CAT_GATEWAY_OFFLINE = "gateway_offline"
    CAT_COLLECT_STALE = "collect_stale"
    CAT_COVERAGE = "coverage_low"
    CAT_HOUR_DEVIATION = "hour_deviation"
    CATEGORY_CHOICES = (
        (CAT_METER_UNCONNECTED, "计量点未接入"),
        (CAT_METER_FAIL, "计量点采集失败"),
        (CAT_HOUR_QUALITY, "小时数据异常"),
        (CAT_GATEWAY_OFFLINE, "网关离线"),
        (CAT_COLLECT_STALE, "采集停摆"),
        (CAT_COVERAGE, "周期覆盖率不足"),
        (CAT_HOUR_DEVIATION, "异常时段待复核"),
    )

    code = models.CharField("告警标识", max_length=120, db_index=True, help_text="稳定标识，用于去重与自动恢复")
    category = models.CharField("类别", max_length=32, choices=CATEGORY_CHOICES, db_index=True)
    level = models.CharField("级别", max_length=16, choices=LEVEL_CHOICES, default=LEVEL_MAJOR)
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)

    source = models.CharField("来源", max_length=120, help_text="归属对象的显示名，如「A4 生产线」「一期配电房串口服务器1口」")
    content = models.CharField("告警内容", max_length=200)
    value = models.CharField("触发值", max_length=60, blank=True, default="")
    threshold = models.CharField("阈值", max_length=60, blank=True, default="")

    # 归属对象：能定位到表/网关/产线就定位，方便点进去直接处理；用 SET_NULL
    # 是为了「台账里删掉一块表」不该把历史告警一起抹掉（那是审计证据）。
    meter = models.ForeignKey(
        EnergyMeter, on_delete=models.SET_NULL, null=True, blank=True, related_name="alarms", verbose_name="计量点"
    )
    gateway = models.ForeignKey(
        EnergyGateway, on_delete=models.SET_NULL, null=True, blank=True, related_name="alarms", verbose_name="网关"
    )
    line = models.ForeignKey(
        ProductionLine, on_delete=models.SET_NULL, null=True, blank=True, related_name="alarms", verbose_name="产线"
    )

    occurred_at = models.DateTimeField("首次发生")
    last_at = models.DateTimeField("最近发生", db_index=True)
    repeat = models.PositiveIntegerField("重复次数", default=1)
    resolved_at = models.DateTimeField("恢复时间", null=True, blank=True)

    acked_by = models.CharField("确认人", max_length=32, blank=True, default="")
    acked_at = models.DateTimeField("确认时间", null=True, blank=True)

    class Meta:
        db_table = "energy_alarm"
        ordering = ["-last_at"]
        verbose_name = "计量告警"
        verbose_name_plural = "计量告警"
        indexes = [
            # 列表页固定是「先看未处理、按时间倒序」，没有这个索引会随历史行增长变慢
            models.Index(fields=("status", "-last_at"), name="alarm_status_time_idx"),
        ]

    def __str__(self) -> str:
        return f"[{self.level}] {self.source} {self.content}"
