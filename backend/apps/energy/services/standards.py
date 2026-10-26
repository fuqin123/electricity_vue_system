"""标准单耗的取数与瓶型容量解析 —— 配置端与显示端共用这一份。"""

from __future__ import annotations

import re
from decimal import Decimal

from ..models import EnergyType, ProductionLine, StandardUnitConsumption

_CAPACITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|mL|ML|l|L)\b")


def capacity_from_product(product: str | None) -> str:
    """从「冰红茶 500ml」提取「500ml」；没有容量信息时返回空串。"""
    match = _CAPACITY_RE.search(product or "")
    if not match:
        return ""
    number, unit = match.group(1), match.group(2).lower()
    return f"{number}{'ml' if unit == 'ml' else 'L'}"


def standard_for_line(
    line: ProductionLine, energy_code: str = EnergyType.ELECTRIC
) -> Decimal | None:
    """某条产线当前瓶型的标准单耗。

    优先匹配 `line.product` 中的瓶型容量；没有匹配时退回该线第一条已标定值。
    `value=0` 视为未配置（界面显示 `-`，且不能画成 0 目标线）。
    """
    rows = StandardUnitConsumption.objects.filter(
        line=line,
        energy_type__type_code=energy_code,
        value__isnull=False,
        value__gt=0,
    )
    capacity = capacity_from_product(line.product)
    if capacity:
        row = rows.filter(bottle_capacity__iexact=capacity).first()
        if row is not None:
            return row.value
    row = rows.order_by("bottle_capacity", "id").first()
    return row.value if row is not None else None


def workshop_standard() -> float | None:
    """车间目标电单耗：每条线取当前瓶型标准后的算术平均；未标定为 None。"""
    values: list[float] = []
    for line in ProductionLine.active_lines_qs():
        value = standard_for_line(line)
        if value is not None:
            values.append(float(value))
    return round(sum(values) / len(values), 2) if values else None
