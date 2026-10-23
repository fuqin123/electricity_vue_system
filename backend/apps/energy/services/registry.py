"""把「网关 / 计量点登记表」（config/settings.yaml）同步进数据库。

为什么用配置文件当登记表的真值来源，而不是让人在页面上一条条录：
  · 现场设备表本来就是 Excel/文档形式，一行一台设备，配置文件的形态最接近它；
  · 走 Git 能留痕、能评审，新增一路摄像头/电表就是一次可 review 的提交；
  · 同步是幂等的（按 code upsert），重复执行不会产生重复记录。
后台页面后续要做的「编辑计量点」，本质上是改这份配置或改库，两者用 code 对齐。
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction

from eep import conf

from ..models import EnergyGateway, EnergyMeter, EnergyType, ProductionLine

logger = logging.getLogger(__name__)

#: v1.6.0 产线编号迁移的**过渡别名**：现场挂载的 settings.local.yaml 可能还是
#: 旧的 A1—A6，而库里已经原地改成 W 码。seed 时先归一化，避免按旧码新建
#: 6 条线、把历史留在旧 id 上。新配置应直接写 W 码；别名不改变 API / 前端口径。
LINE_CODE_ALIASES = {
    "A1": "W103",
    "A2": "W101",
    "A3": "W102",
    "A4": "W301",
    "A5": "W104",
    "A6": "W105",
}


def normalize_line_code(code: str | None) -> str | None:
    if code is None:
        return None
    text = str(code)
    return LINE_CODE_ALIASES.get(text.upper(), text)


def _dec(value, default="1") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return Decimal(default)


#: 配置里的能源短码 → energy_type.type_code
KIND_TO_TYPE_CODE = {"elec": "ELECTRIC", "water": "WATER", "steam": "STEAM"}


def _energy_type(kind: str) -> EnergyType:
    """把配置里的能源标识（短码 elec 或编码 ELECTRIC）解析成 EnergyType 实例。

    ⚠ 解析不到就直接报错，不要静默兜底成「电」—— 那会让水表按电来统计。
    """
    raw = str(kind or "").strip()
    code = KIND_TO_TYPE_CODE.get(raw.lower(), raw.upper())
    obj = EnergyType.objects.filter(type_code=code).first()
    if obj is None:
        raise ValueError(
            f"未知能源类型 {kind!r}：配置文件里请写 {' / '.join(KIND_TO_TYPE_CODE)} "
            f"之一（或 energy_type 表里的编码）"
        )
    return obj


def _unit_for(etype: EnergyType, raw: str | None) -> str:
    """计量点单位 —— 配置没写时**按能源类型推**，不要一律兜成 kWh。

    ⚠ 这是一个静默错量纲的源头（B-535 同族）：旧代码写死
      `item.get("unit", "kWh")`，于是漏写 `unit` 的**水表 / 汽表**会被登记成
      「单位 = kWh」。单位本身不参与计算（小时差值只看累计值），所以采集、
      聚合、汇总全都正常跑通、不报任何错，只有界面把 380 吨水标成 380 kWh ——
      现场核对时表现为「数字对不上」，而不是「配置错了」。

    推论：`unit` 只影响展示，**越是不报错的地方越要写死规则**。
    """
    text = str(raw or "").strip()
    if text:
        return text
    return EnergyType.DEFAULT_UNIT.get(etype.type_code, "kWh")


def _data_type_for(raw: str | None) -> str:
    """计量点寄存器数据类型 —— 配置没写时用**现场既有的默认口径**（32 位无符号整数）。

    ⚠ 为什么不能一律兜一个「聪明」的值：数据类型与 `unit` 一样**不参与校验、只影响解码**，
      猜错不会报错，只会读出一个数值离谱的数（例：double 水表按 32 位整数拼）。
      所以这里只做「写错就报错、不写就用既有口径」两件事，绝不猜测含义。

    现场已知的例外：碳水市政水表 `W-CARB-CITY-1` 是 **4 寄存器 IEEE754 double**，
    配置里必须显式写 `data_type: float64`。
    """
    text = str(raw or "").strip()
    if not text:
        return EnergyMeter.DATA_TYPE_DEFAULT
    if text not in dict(EnergyMeter.DATA_TYPE_CHOICES):
        raise ValueError(
            f"未知数据类型 {text!r}：配置里请写 "
            + " / ".join(t for t, _ in EnergyMeter.DATA_TYPE_CHOICES)
            + " 之一"
        )
    return text


@transaction.atomic
def sync_registry(*, force: bool = False) -> dict:
    """按配置 upsert 车间 / 辅助单元 / 产线 / 网关 / 计量点。返回变更计数。

    ⚠ **配置来源优先级：后台维护 > 配置文件。**
      后台（管理界面）改过的记录会打上 `source = admin`，本函数默认**跳过**它们 ——
      否则现场在后台调好的从站号/倍率，会被下一次 `seed_registry` 悄悄改回配置里的旧值，
      而且现场根本想不到是这个原因（这类「改了又自己变回去」最难查）。
      确实要以配置文件为准时，显式加 `--force`。

    ⚠ v1.5.2 起组织节点分三层（见 models.ProductionLine 的 node_type）：
      `workshops`（车间）→ `lines`（产线）/ `aux_nodes`（辅助单元）。
      产线与辅助单元通过 `workshop` 字段指定上级车间。
    """
    stat = {"workshop": 0, "aux": 0, "line": 0, "gateway": 0, "meter": 0, "skipped": 0}

    # ── 车间 ────────────────────────────────────────────────
    # 先建车间，后面产线/辅助单元才能挂上去（parent 是非空挂靠，顺序不能反）。
    for item in conf.get("workshops", []) or []:
        ProductionLine.objects.update_or_create(
            line_code=item["code"],
            defaults={
                "line_name": item.get("name", item["code"]),
                "node_type": ProductionLine.NODE_WORKSHOP,
                "line_type": ProductionLine.TYPE_WORKSHOP,
                "parent": None,  # 车间节点没有父级
                "sort": int(item.get("sort", 0) or 0),
                "status": (
                    ProductionLine.STATUS_ON if item.get("enabled", True) else ProductionLine.STATUS_OFF
                ),
            },
        )
        stat["workshop"] += 1

    # ── 辅助计量单元（辅机 / 瓶胚 / 调配）────────────────────
    for item in conf.get("aux_nodes", []) or []:
        parent = _find_workshop(item.get("workshop"))
        ProductionLine.objects.update_or_create(
            line_code=item["code"],
            defaults={
                "line_name": item.get("name", item["code"]),
                "node_type": ProductionLine.NODE_AUX,
                "line_type": ProductionLine.TYPE_LINE,
                "parent": parent,
                "sort": int(item.get("sort", 0) or 0),
                "status": ProductionLine.STATUS_ON,
            },
        )
        stat["aux"] += 1

    # ── 产线 ────────────────────────────────────────────────
    for item in conf.get("lines", []) or []:
        parent = _find_workshop(item.get("workshop"))
        ProductionLine.objects.update_or_create(
            line_code=normalize_line_code(item["code"]),
            defaults={
                "line_name": item.get("name", item["code"]),
                "node_type": ProductionLine.NODE_LINE,
                # 配置里的 enabled 只映射到 status
                "line_type": ProductionLine.TYPE_LINE,
                "parent": parent,
                "rated": int(item.get("rated", 0) or 0),
                "product": item.get("product", ""),
                "sort": int(item.get("sort", 0) or 0),
                "status": (
                    ProductionLine.STATUS_ON if item.get("enabled", True) else ProductionLine.STATUS_OFF
                ),
            },
        )
        stat["line"] += 1

    # ── 网关 ────────────────────────────────────────────────
    gw_map: dict[str, EnergyGateway] = {}
    for item in conf.get("gateways", []) or []:
        existed = EnergyGateway.objects.filter(code=item["code"]).first()
        gw_map[item["code"]] = existed  # 先记下，供计量点引用（含被跳过的）
        if existed is not None and existed.source == EnergyGateway.SOURCE_ADMIN and not force:
            stat["skipped"] += 1
            logger.warning("网关 %s 由后台维护，跳过配置覆盖（需要覆盖请加 --force）", item["code"])
            continue
        gw, _ = EnergyGateway.objects.update_or_create(
            code=item["code"],
            defaults={
                "name": item.get("name", item["code"]),
                # 配置里写的是短码（elec/water/steam），库里是 ENERGY 编码
                # 网关的 kind 是「网关类型」（modbus_rtu_tcp / http_api），与能源类型无关
                "kind": item["kind"],
                "host": item.get("host", "") or "",
                "port": item.get("port"),
                "base_url": item.get("base_url", "") or "",
                "timeout_ms": int(item.get("timeout_ms", 3000) or 3000),
                "interval_seconds": int(item.get("interval_seconds", 60) or 60),
                "enabled": bool(item.get("enabled", True)),
                "remark": item.get("remark", ""),
                "source": EnergyGateway.SOURCE_CONFIG,
            },
        )
        gw_map[gw.code] = gw
        stat["gateway"] += 1

    line_map = {ln.line_code: ln for ln in ProductionLine.objects.all()}

    # ── 计量点 ──────────────────────────────────────────────
    for item in conf.get("meters", []) or []:
        gw_code = item.get("gateway")
        gw = gw_map.get(gw_code) or EnergyGateway.objects.filter(code=gw_code).first()
        if gw is None:
            raise ValueError(f"计量点 {item.get('code')} 引用了不存在的网关：{gw_code}")

        line = _resolve_node(item, line_map)
        # 能源类型先解析出来：单位兜底要用它，且解析失败要在这里就报错（别等写库）
        etype = _energy_type(item["kind"])

        existed = EnergyMeter.objects.filter(code=item["code"]).first()
        if existed is not None and existed.source == EnergyMeter.SOURCE_ADMIN and not force:
            stat["skipped"] += 1
            logger.warning("计量点 %s 由后台维护，跳过配置覆盖（需要覆盖请加 --force）", item["code"])
            continue

        obj, _created = EnergyMeter.objects.update_or_create(
            code=item["code"],
            defaults={
                "name": item.get("name", item["code"]),
                # 配置里写的是能源短码（elec/water/steam），库里是 ELECTRIC/WATER/STEAM
                "energy_type": etype,
                "scope": item.get("scope", EnergyMeter.SCOPE_LINE),
                "role": item.get("role", EnergyMeter.ROLE_LINE),
                # v1.5.2：界面「车间 → 类型 → 设备」三级筛选的数据来源
                "usage_type": item.get("usage_type", "") or "",
                "device_name": item.get("device_name", "") or "",
                "line": line,
                "gateway": gw,
                "slave": item.get("slave"),
                "register": item.get("register"),
                # ⚠ registers 是**推导列**（见 EnergyMeter.sync_registers）：它由 data_type 决定，
                #   配置里仍允许写，但随后会被 data_type 覆盖 —— 避免「配的 float64、读的 2 个寄存器」。
                "registers": int(item.get("registers", 2) or 2),
                "func_code": int(item.get("func_code", 3) or 3),
                "data_type": _data_type_for(item.get("data_type")),
                "byte_order": item.get("byte_order", "big") or "big",
                "word_order": item.get("word_order", "big"),
                "url_path": item.get("url_path", "") or "",
                "params": item.get("params", {}) or {},
                "value_path": item.get("value_path", "") or "",
                "time_path": item.get("time_path", "") or "",
                "scale": _dec(item.get("scale", 1)),
                # 漏写 unit 时按能源类型推（水=m³ / 汽=t），**不要兜成 kWh**
                "unit": _unit_for(etype, item.get("unit")),
                "max_hourly": _dec(item["max_hourly"]) if item.get("max_hourly") is not None else None,
                "enabled": bool(item.get("enabled", True)),
                "remark": item.get("remark", ""),
                "asset_no": item.get("asset_no", "") or "",
                "source": EnergyMeter.SOURCE_CONFIG,
            },
        )
        # registers 由 data_type 推导：配置里写错（或没写）也以数据类型为准，
        # 保证「读几个寄存器」永远与「按什么类型解」一致（v1.5.3）。
        if obj.sync_registers():
            obj.save(update_fields=["registers", "updated_at"])
        stat["meter"] += 1

    logger.info("登记表同步完成：%s", stat)
    return stat


def _find_workshop(code: str | None) -> ProductionLine | None:
    """按车间编码取车间节点；未指定时返回 None（不瞎猜一个车间回去）。

    ⚠ 旧实现在 scope=workshop 时无条件 `get_or_create(WORKSHOP)` —— 多车间下
      这会把所有车间级仪表都挂到同一个节点上，是必须修掉的写法。
    """
    if not code:
        return None
    obj = ProductionLine.objects.filter(
        line_code=code, node_type=ProductionLine.NODE_WORKSHOP
    ).first()
    if obj is None:
        raise ValueError(
            f"配置引用了不存在的车间：{code}（请先在 workshops 段登记，注意大小写）"
        )
    return obj


def _resolve_node(item: dict, line_map: dict[str, ProductionLine]) -> ProductionLine:
    """决定这条计量点挂到哪个组织节点。

    优先级：`node`（车间 / 辅助单元）> `line`（产线）> `scope=workshop` 时的车间。

    ⚠ `node` 是新写法，现场接口表里辅机类的「线别」列写的是设备组合名
      （「1号高压机+3台低压机…」），不是一个产线编码，必须能挂到 aux 节点上。
    """
    node_code = normalize_line_code(item.get("node"))
    if node_code:
        node = line_map.get(node_code) or ProductionLine.objects.filter(line_code=node_code).first()
        if node is None:
            raise ValueError(f"计量点 {item.get('code')} 引用了不存在的节点：{node_code}")
        return node

    line_code = normalize_line_code(item.get("line"))
    if line_code:
        line = line_map.get(line_code) or ProductionLine.objects.filter(
            line_code=line_code
        ).first()
        if line is None:
            raise ValueError(f"计量点 {item.get('code')} 引用了不存在的产线：{line_code}")
        return line

    if item.get("scope") == EnergyMeter.SCOPE_WORKSHOP:
        # 车间总表必须显式说明是哪个车间；只写 scope=workshop 而不给 node，
        # 在多车间下无法判断该挂哪里（旧代码是瞎猜一个）。
        raise ValueError(
            f"车间级计量点 {item.get('code')} 必须用 `node:` 指定所属车间编码"
            f"（如 node: WS-CARB）"
        )

    raise ValueError(f"计量点 {item.get('code')} 既没有 line 也没有 node，无法确定归属")
