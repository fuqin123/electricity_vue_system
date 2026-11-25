"""产量 PLC 采集（v1.8.0）。

现场设备是西门子 **S7-200 SMART**，通过以太网（默认 102 端口）读取 DB / M / I / Q 区。
传输层用 `python-snap7>=3`（纯 Python，不再需要 `snap7.dll`）；没有 PLC 时用
`PlcDevice.mode = simulated` 生成可重复的递增数据，页面、绑定、采集链路都能先跑通。

产量口径（与现有 `production_hourly` 对齐）：
  · 累计计数器：本次读数 − 上次读数 = 本区间增量，累加到读数**所属小时**；
  · 瞬时值：本次读数就是本区间产量，直接累加（只在 PLC 已经按区间计数时使用）；
  · 负数 / 超过单次上限的增量视为换表 / 归零 / 尖峰，**不写产量**，只重置基准；
  · 一个 PLC 点可以被多个绑定设备引用，增量按设备分别落到其 `line`；没有
    `line` 或 `counts_output=False` 的设备只采集、不计产。

⚠ 采集只在后台/命令行触发，接口层不直接连 PLC —— Web 请求不能被设备超时拖住。
"""

from __future__ import annotations

import logging
import re
import struct
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from ..models import (
    PlcDevice,
    PlcPoint,
    PlcReading,
    ProductionCaseHourly,
    ProductionCounterHourly,
    ProductionHourly,
    ProductionLine,
    StandardCaseRule,
)

logger = logging.getLogger(__name__)

Q3 = Decimal("0.001")

#: 库内数据类型 → struct 解码格式（S7 为大端）
_STRUCT_FMT = {
    PlcPoint.DT_UINT16: ">H",
    PlcPoint.DT_INT16: ">h",
    PlcPoint.DT_UINT32: ">I",
    PlcPoint.DT_INT32: ">i",
    PlcPoint.DT_FLOAT32: ">f",
}

#: 库内区域 → python-snap7 的 Area 名称
_S7_AREA = {"DB": "DB", "M": "MK", "I": "PE", "Q": "PA"}


class PlcReadError(Exception):
    """PLC 连接 / 读取失败（带现场可读的原因）。"""


def simulated_value(point: PlcPoint, now: datetime) -> Decimal:
    """模拟读数：`sim_base + sim_rate × 从 epoch 起的小时数`。

    用 epoch 而不是「当天 0 点」是为了跨零点不回退：计数器一旦回退会被当成
    换表，模拟测试就再也跑不出连续曲线。默认 rate=0 时读数恒为 base。
    """
    hours = Decimal(str(now.timestamp())) / Decimal(3600)
    return (point.sim_base + point.sim_rate * hours).quantize(Q3)


def decode_value(raw: bytes, point: PlcPoint) -> Decimal:
    """按点位的数据类型解码原始字节（bool 取指定位）。"""
    if point.data_type == PlcPoint.DT_BOOL:
        if not raw:
            raise PlcReadError("bool 点位读到的字节为空")
        bit = int(point.bit_offset or 0)
        if not 0 <= bit <= 7:
            raise PlcReadError("bool 位偏移必须在 0—7")
        return Decimal(1 if (raw[0] >> bit) & 1 else 0)
    fmt = _STRUCT_FMT.get(point.data_type)
    if fmt is None:
        raise PlcReadError(f"不支持的数据类型：{point.data_type}")
    size = struct.calcsize(fmt)
    if len(raw) < size:
        raise PlcReadError(f"读取长度不足：需要 {size} 字节，实际 {len(raw)}")
    value = struct.unpack(fmt, raw[:size])[0]
    return Decimal(str(value)).quantize(Q3)


def _read_raw(client, point: PlcPoint) -> bytes:
    """从 snap7 Client 读取点位原始字节。"""
    if point.area == PlcPoint.AREA_DB:
        return bytes(client.db_read(int(point.db_number or 1), int(point.start_offset), point.size))

    from snap7.type import Area

    area = getattr(Area, _S7_AREA.get(point.area, "DB"))
    return bytes(client.read_area(area, 0, int(point.start_offset), point.size))


def _read_snap7(device: PlcDevice, points: list[PlcPoint]) -> list[dict]:
    if not device.host:
        raise PlcReadError("PLC IP 未配置")
    try:
        from s7 import Client
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出可执行提示
        raise PlcReadError(
            "未安装 python-snap7，请执行 pip install python-snap7==3.6.0"
        ) from exc

    client = Client()
    timeout = max(0.2, (device.timeout_ms or 3000) / 1000)
    try:
        client.connect(device.host, int(device.rack), int(device.slot), int(device.port or 102))
        if hasattr(client, "set_timeout"):
            try:
                client.set_timeout(timeout)
            except Exception:  # noqa: BLE001 - 超时设置失败不阻断读取
                pass
        out: list[dict] = []
        for point in points:
            try:
                raw = _read_raw(client, point)
                out.append({"point": point, "value": decode_value(raw, point), "error": ""})
            except PlcReadError as exc:
                out.append({"point": point, "value": None, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - 单点失败不拖垮整台 PLC
                out.append({"point": point, "value": None, "error": str(exc)})
        return out
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass


def read_points(device: PlcDevice, *, now: datetime | None = None, force_simulated: bool = False) -> list[dict]:
    """读取一台 PLC 的全部启用点位，返回 `[{point, value, error}]`。

    `force_simulated=True` 用于没有设备时联调：即使 mode=snap7 也走模拟值。
    """
    now = now or datetime.now()
    points = list(device.points.filter(enabled=True).order_by("sort", "code"))
    if not points:
        return []
    if force_simulated or device.mode == PlcDevice.MODE_SIMULATED:
        return [
            {"point": p, "value": simulated_value(p, now), "error": ""}
            for p in points
        ]
    try:
        return _read_snap7(device, points)
    except PlcReadError as exc:
        return [{"point": p, "value": None, "error": str(exc)} for p in points]
    except Exception as exc:  # noqa: BLE001
        return [{"point": p, "value": None, "error": str(exc)} for p in points]


def _scaled(value: Decimal, point: PlcPoint) -> Decimal:
    return (value * Decimal(point.scale or 1)).quantize(Q3)


def _hour_bucket(now: datetime) -> tuple[object, int]:
    """读数归到哪个自然小时：06:00 整点采集的增量属于 05:00 那一小时。"""
    anchor = now - timedelta(seconds=1)
    return anchor.date(), anchor.hour


def _round_cases(value: Decimal) -> Decimal:
    """标箱数取整：现场口径是四舍五入，不用银行家舍入。"""
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _line_ml(line_id: int) -> int | None:
    """PLC 没有规格点时，退而解析产线当前产品名里的 ml（如「冰红茶 500ml」）。"""
    product = (
        ProductionLine.objects.filter(pk=line_id).values_list("product", flat=True).first() or ""
    )
    matched = re.search(r"(\d+)\s*ml", product, re.I)
    return int(matched.group(1)) if matched else None


def _case_factor(in_count: int | None, ml: int | None) -> tuple[Decimal, bool]:
    """按 (入数, ml) 查标箱换算规则；未配置时按 1.0 兜底并明确标记未命中。"""
    if in_count and ml:
        rule = StandardCaseRule.objects.filter(in_count=in_count, ml=ml).first()
        if rule is not None:
            return Decimal(rule.factor), True
    return Decimal("1"), False


def collect_device(
    device: PlcDevice,
    *,
    now: datetime | None = None,
    force_simulated: bool = False,
) -> dict:
    """读取一台 PLC 并把增量写入 `production_hourly`。

    返回统计：`{ok, points, failed, written, lines, error}`，供命令/接口展示。
    """
    now = now or datetime.now()
    stat_date, stat_hour = _hour_bucket(now)
    results = read_points(device, now=now, force_simulated=force_simulated)
    if not results:
        return {"ok": False, "points": 0, "failed": 0, "written": 0, "lines": [], "error": "没有启用的点位"}

    # 每线累计：瓶数 / 箱数 / 普通计数 / PLC 当前 ml
    line_roles: dict[int, dict[str, object]] = {}
    failed = 0
    notes: list[str] = []

    def bucket(line_id: int) -> dict[str, object]:
        return line_roles.setdefault(
            line_id, {"bottle": Decimal(0), "case": Decimal(0), "counter": Decimal(0), "ml": None}
        )

    with transaction.atomic():
        for item in results:
            point: PlcPoint = item["point"]
            value: Decimal | None = item["value"]
            error = item["error"]
            if value is None:
                failed += 1
                point.last_ok = False
                point.last_error = error[:200]
                point.save(update_fields=["last_ok", "last_error", "updated_at"])
                PlcReading.objects.create(
                    point=point, collect_time=now, value=None, ok=False, error=error[:200]
                )
                continue

            delta = Decimal(0)
            if point.counter_mode == PlcPoint.MODE_INSTANT:
                delta = _scaled(value, point)
            elif point.last_value is not None:
                diff = value - point.last_value
                if diff < 0:
                    notes.append(f"{point.code} 累计值回退（{point.last_value}→{value}），只重置基准")
                else:
                    delta = _scaled(diff, point)
            if point.max_hourly is not None and delta > Decimal(point.max_hourly):
                notes.append(f"{point.code} 增量 {delta} 超过上限 {point.max_hourly}，未计入产量")
                delta = Decimal(0)

            point.last_value = value
            point.last_at = now
            point.last_ok = True
            point.last_error = ""
            point.save(update_fields=["last_value", "last_at", "last_ok", "last_error", "updated_at"])
            PlcReading.objects.create(point=point, collect_time=now, value=value, ok=True)

            seen_lines: set[int] = set()
            for assignment in point.production_assignments.select_related("device").all():
                bound = assignment.device
                if not bound.counts_output or bound.line_id is None:
                    continue
                # 同一个点位可能被同一产线的多个设备勾选（共享计数器）——
                # 按「产线 × 点位」只计一次，避免重复放大产量。
                if bound.line_id in seen_lines:
                    continue
                seen_lines.add(bound.line_id)
                slot = bucket(bound.line_id)
                if point.role == PlcPoint.ROLE_SPEC:
                    # 规格点是「当前选择」，不是增量：取最新值作为本小时的 ml
                    slot["ml"] = int(value)
                    continue
                if delta <= 0:
                    continue
                if point.role == PlcPoint.ROLE_BOTTLE:
                    slot["bottle"] = Decimal(slot["bottle"]) + delta
                elif point.role == PlcPoint.ROLE_CASE:
                    slot["case"] = Decimal(slot["case"]) + delta
                else:
                    slot["counter"] = Decimal(slot["counter"]) + delta

        written = 0
        for line_id, slot in line_roles.items():
            bottle = Decimal(slot["bottle"])
            cases = Decimal(slot["case"])
            counter = Decimal(slot["counter"])
            spec_ml = slot["ml"]
            if bottle > 0 and cases <= 0:
                # 只有 01 电眼（瓶数）时无法换算标箱，沿用旧的瓶数口径
                counter += bottle
                bottle = Decimal(0)
            if bottle <= 0 and cases <= 0:
                if counter <= 0:
                    continue
                row, created = ProductionHourly.objects.get_or_create(
                    line_id=line_id,
                    stat_date=stat_date,
                    stat_hour=stat_hour,
                    defaults={
                        "output": counter,
                        "quality": ProductionHourly.Q_OK,
                        "source": ProductionHourly.SOURCE_PLC,
                        "note": f"PLC {device.code} {now:%H:%M}",
                    },
                )
                if not created:
                    row.output = (row.output or Decimal(0)) + counter
                    row.quality = ProductionHourly.Q_OK
                    row.source = ProductionHourly.SOURCE_PLC
                    row.note = f"PLC {device.code} {now:%H:%M}"
                    row.save(update_fields=["output", "quality", "source", "note", "updated_at"])
                written += 1
                continue

            # 01/02 电眼都有：先累计计数参量，再按 (入数, ml) 换算标箱
            counter_row, created = ProductionCounterHourly.objects.get_or_create(
                line_id=line_id, stat_date=stat_date, stat_hour=stat_hour,
                defaults={"bottles": Decimal(0), "cases": Decimal(0)},
            )
            counter_row.bottles = (counter_row.bottles or Decimal(0)) + bottle
            counter_row.cases = (counter_row.cases or Decimal(0)) + cases
            if spec_ml is not None:
                counter_row.ml = int(spec_ml)
            if counter_row.cases > 0:
                counter_row.in_count = int(
                    (counter_row.bottles / counter_row.cases).quantize(
                        Decimal("1"), rounding=ROUND_HALF_UP
                    )
                )
            ml = counter_row.ml or _line_ml(line_id)
            factor, matched = _case_factor(counter_row.in_count, ml)
            counter_row.ml = ml
            counter_row.factor = factor
            counter_row.save()

            standard_cases = _round_cases(counter_row.cases * factor)
            ProductionCaseHourly.objects.update_or_create(
                line_id=line_id, stat_date=stat_date, stat_hour=stat_hour,
                defaults={
                    "standard_cases": standard_cases,
                    "in_count": counter_row.in_count,
                    "ml": ml,
                    "factor": factor,
                    "rule_matched": matched,
                    "note": "" if matched else "未配置该入数/ml 的标箱换算，按 1.0 计",
                },
            )
            # 页面显示的产量统一取标箱数（覆盖写，避免重复累加）
            row, created = ProductionHourly.objects.get_or_create(
                line_id=line_id,
                stat_date=stat_date,
                stat_hour=stat_hour,
                defaults={
                    "output": standard_cases,
                    "quality": ProductionHourly.Q_OK,
                    "source": ProductionHourly.SOURCE_PLC,
                    "note": f"PLC {device.code} 标箱",
                },
            )
            if not created:
                row.output = standard_cases
                row.quality = ProductionHourly.Q_OK
                row.source = ProductionHourly.SOURCE_PLC
                row.note = f"PLC {device.code} 标箱"
                row.save(update_fields=["output", "quality", "source", "note", "updated_at"])
            if not matched:
                notes.append(f"{line_id} 未配置 {counter_row.in_count}入/{ml}ml 的标箱换算")
            written += 1

    ok = failed == 0
    error = "" if ok else f"{failed}/{len(results)} 个点位读取失败"
    if ok:
        device.last_ok_at = now
        device.last_error = ""
    else:
        device.last_fail_at = now
        device.last_error = error[:200]
    device.save(update_fields=["last_ok_at", "last_fail_at", "last_error", "updated_at"])

    if notes:
        logger.info("PLC %s 采集提示：%s", device.code, "；".join(notes))
    return {
        "ok": ok,
        "points": len(results),
        "failed": failed,
        "written": written,
        "lines": list(line_roles.keys()),
        "error": error,
    }


def collect_all(*, now: datetime | None = None, force_simulated: bool = False) -> dict:
    """采集所有启用的 PLC 设备。"""
    now = now or datetime.now()
    devices = list(PlcDevice.objects.filter(enabled=True).order_by("code"))
    rows = []
    for device in devices:
        rows.append({"device": device.code, **collect_device(device, now=now, force_simulated=force_simulated)})
    return {"devices": len(devices), "rows": rows}
