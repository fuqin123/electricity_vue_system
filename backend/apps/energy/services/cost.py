"""成本折算 —— 「单箱变动费用」的唯一算口。

算式（口径必须和财务对得上，别在别处再算一遍）：

    变动费用(元) = 电(kWh) × 电价 + 水(t) × 水价 + 汽(t) × 汽价
    箱数(箱)     = 产量(瓶) ÷ 箱规(瓶/箱)
    单箱变动费用 = 变动费用 ÷ 箱数

⚠ 三条不能含糊的地方：

1. **任一单价未标定 → 整个费用为 None**，不是「按 0 计入」。
   少算一项单价得到的是一笔看起来正常的偏小费用，正是本项目反复禁止的那类假数字
   （B-302 / B-502 / B-505 同源）。
2. **产量未接入 → 箱数与单箱费用一律 None**。绝不拿电量反推产量再除。
3. 参数取自 `system_param` 表（后台「系统参数」页维护），配置文件里不留第二份，
   否则改了参数界面不变（ADR-007 同一条思路）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..models import SystemParam

#: 参数键集中定义，避免到处写字符串字面量
KEY_ELECTRIC = "price.electric"
KEY_WATER = "price.water"
KEY_STEAM = "price.steam"
KEY_BOTTLES_PER_CASE = "package.bottles_per_case"
KEY_STANDARD_COST = "cost.standard_per_case"


@dataclass(frozen=True)
class CostParams:
    """一组成本参数。None = 未标定（≠ 0）。"""

    electric: Decimal | None
    water: Decimal | None
    steam: Decimal | None
    bottles_per_case: Decimal | None
    standard_per_case: Decimal | None

    @property
    def complete(self) -> bool:
        """算单箱费用需要的四项是否齐全（标准线那项不参与，缺了只是不画线）。"""
        return all(
            v is not None and v > 0
            for v in (self.electric, self.water, self.steam, self.bottles_per_case)
        )


def load_params() -> CostParams:
    """读参数表。缺行按 None（未标定）处理，不猜默认值。"""
    rows = {p.key: p.value for p in SystemParam.objects.all()}
    return CostParams(
        electric=rows.get(KEY_ELECTRIC),
        water=rows.get(KEY_WATER),
        steam=rows.get(KEY_STEAM),
        bottles_per_case=rows.get(KEY_BOTTLES_PER_CASE),
        standard_per_case=rows.get(KEY_STANDARD_COST),
    )


def variable_cost_yuan(
    kwh: Decimal | None, water_t: Decimal | None, steam_t: Decimal | None, cp: CostParams
) -> Decimal | None:
    """区间变动费用（元）。三项用量里**有值的项都要能算**：
    某项用量为 None（该能源这段没数据）→ 合计就是不完整的 → 返回 None。
    用量为 0 是「确实没用」，与 None 不同，照常计入。
    """
    if not cp.complete:
        return None
    if kwh is None or water_t is None or steam_t is None:
        return None
    return (
        kwh * cp.electric  # type: ignore[operator]
        + water_t * cp.water  # type: ignore[operator]
        + steam_t * cp.steam  # type: ignore[operator]
    )


def per_case_cost(
    kwh: Decimal | None, water_t: Decimal | None, steam_t: Decimal | None, bottles: Decimal | None,
    cp: CostParams,
) -> Decimal | None:
    """单箱变动费用（元/箱）。产量未接入（bottle=None）或算料不齐时返回 None。"""
    cost = variable_cost_yuan(kwh, water_t, steam_t, cp)
    if cost is None or bottles is None or bottles <= 0 or not cp.bottles_per_case:
        return None
    cases = bottles / cp.bottles_per_case  # type: ignore[operator]
    return cost / cases
