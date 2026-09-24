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


@transaction.atomic
def sync_registry(*, force: bool = False) -> dict:
    """按配置 upsert 产线 / 网关 / 计量点。返回变更计数。

    ⚠ **配置来源优先级：后台维护 > 配置文件。**
      后台（管理界面）改过的记录会打上 `source = admin`，本函数默认**跳过**它们 ——
      否则现场在后台调好的从站号/倍率，会被下一次 `seed_registry` 悄悄改回配置里的旧值，
      而且现场根本想不到是这个原因（这类「改了又自己变回去」最难查）。
      确实要以配置文件为准时，显式加 `--force`。
    """
    stat = {"line": 0, "gateway": 0, "meter": 0, "skipped": 0}

    # ── 产线 ────────────────────────────────────────────────
    for item in conf.get("lines", []) or []:
        ProductionLine.objects.update_or_create(
            line_code=item["code"],
            defaults={
                "line_name": item.get("name", item["code"]),
                "rated": int(item.get("rated", 0) or 0),
                "product": item.get("product", ""),
                "sort": int(item.get("sort", 0) or 0),
                # 配置里的 enabled 只映射到 status；line_type 一律是产线
                # （车间哨兵行由 0006 迁移建好，配置里不要出现，否则会把 it 变回产线）
                "status": (
                    ProductionLine.STATUS_ON if item.get("enabled", True) else ProductionLine.STATUS_OFF
                ),
                "line_type": ProductionLine.TYPE_LINE,
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

        line = None
        if item.get("line"):
            line = line_map.get(item["line"]) or ProductionLine.objects.filter(line_code=item["line"]).first()
            if line is None:
                raise ValueError(f"计量点 {item.get('code')} 引用了不存在的产线：{item['line']}")

        scope = item.get("scope", EnergyMeter.SCOPE_LINE)
        if scope == EnergyMeter.SCOPE_WORKSHOP:
            # 车间水/汽总表挂「车间哨兵行」—— 汇总表的唯一键需要 line，
            # 留空会让 MySQL 的唯一约束失效（多个 NULL 互不相同）
            line = ProductionLine.objects.get_or_create(
                line_code=ProductionLine.WORKSHOP_CODE,
                defaults={
                    "line_name": "车间（总表）",
                    "line_type": ProductionLine.TYPE_WORKSHOP,
                    "sort": 0,
                },
            )[0]

        existed = EnergyMeter.objects.filter(code=item["code"]).first()
        if existed is not None and existed.source == EnergyMeter.SOURCE_ADMIN and not force:
            stat["skipped"] += 1
            logger.warning("计量点 %s 由后台维护，跳过配置覆盖（需要覆盖请加 --force）", item["code"])
            continue

        EnergyMeter.objects.update_or_create(
            code=item["code"],
            defaults={
                "name": item.get("name", item["code"]),
                # 配置里写的是能源短码（elec/water/steam），库里是 ELECTRIC/WATER/STEAM
                "energy_type": _energy_type(item["kind"]),
                "scope": scope,
                "role": item.get("role", EnergyMeter.ROLE_LINE),
                "line": line,
                "gateway": gw,
                "slave": item.get("slave"),
                "register": item.get("register"),
                "registers": int(item.get("registers", 2) or 2),
                "func_code": int(item.get("func_code", 3) or 3),
                "word_order": item.get("word_order", "big"),
                "url_path": item.get("url_path", "") or "",
                "params": item.get("params", {}) or {},
                "value_path": item.get("value_path", "") or "",
                "time_path": item.get("time_path", "") or "",
                "scale": _dec(item.get("scale", 1)),
                "unit": item.get("unit", "kWh"),
                "max_hourly": _dec(item["max_hourly"]) if item.get("max_hourly") is not None else None,
                "enabled": bool(item.get("enabled", True)),
                "remark": item.get("remark", ""),
                "asset_no": item.get("asset_no", "") or "",
                "source": EnergyMeter.SOURCE_CONFIG,
            },
        )
        stat["meter"] += 1

    logger.info("登记表同步完成：%s", stat)
    return stat
