"""串口服务器采集器 —— Modbus RTU over TCP。

现场用的是「485 串口服务器」：一个网口后面挂一条 RS485 总线，总线上并着多块电表。
所以**不能用标准 Modbus TCP 帧**（那是 0x0000 事务号 + 长度 + 单元号的另一套封装），
必须让帧器按 RTU 组帧（从站号 + 功能码 + 数据 + CRC16），也就是这里的 `FramerType.RTU`。

参考实现见 `E:\\http_test\\serial_servier.py`（现场已验证可读），
本模块用 pymodbus 达到同样效果，并统一接入重试与日志。

已核对的现场读法（13 块电表全部验证通过，耗时 66—127ms）：
    功能码 03（读保持寄存器）、起始地址 1000、2 个寄存器、32 位无符号、倍率 ×0.1 → kWh
    响应：08 03 04 03 c3 3b 79 41 99   → 数据 0x03C33B79 = 63,126,393 → 6,312,639.3 kWh

⚠ v1.5.3：**并非所有表都是 32 位整数**。碳水市政水表（`W-CARB-CITY-1`）是
  **4 个寄存器、IEEE754 双精度（double）**，按 32 位整数拼出来是一个完全无关的大整数。
  解码方式改由计量点的 `data_type` / `byte_order` / `word_order` 决定，
  读取长度也由 `data_type` 推导（见 `EnergyMeter.REGISTERS_BY_TYPE`）。
"""

from __future__ import annotations

import inspect
import logging
import math
import struct
from decimal import Decimal

from pymodbus.client import ModbusTcpClient
from pymodbus.framer import FramerType

from apps.energy.models import EnergyMeter

from .base import BaseCollector, CollectResult

logger = logging.getLogger(__name__)

#: 「从站号」这个参数在 pymodbus 各版本里改过名，缓存一下探测结果
_UNIT_PARAM: str | None = None


def unit_kwarg(client) -> str:
    """探测当前 pymodbus 用哪个关键字传从站号。

    pymodbus 的历史包袱（踩过一次，现场表现为**全部电表读取失败**）：
      · 3.7 及以前  → `read_holding_registers(..., slave=N)`
      · 3.8 及以后  → 改名为 `device_id=N`（`slave` 保留为兼容别名）
    代码里写死任一名字，都会在另一种版本上直接抛 TypeError：

        read_holding_registers() got an unexpected keyword argument 'device_id'

    这个报错看起来像「协议不兼容」，实际只是关键字改名，但会让 13 块表全部读不到，
    现场第一反应会去查 485 接线和从站号 —— 白跑一趟。
    所以这里按**实际签名**决定用哪个名字，而不是绑定某个版本。
    """
    global _UNIT_PARAM
    if _UNIT_PARAM is None:
        try:
            params = inspect.signature(client.read_holding_registers).parameters
            _UNIT_PARAM = "device_id" if "device_id" in params else "slave"
        except (TypeError, ValueError):  # pragma: no cover - 极端情况下退回旧名字
            _UNIT_PARAM = "slave"
        logger.debug("pymodbus 从站号参数名：%s", _UNIT_PARAM)
    return _UNIT_PARAM


def _to_bytes(regs: list[int], byte_order: str, word_order: str) -> bytes:
    """按字序 + 字节序把寄存器铺成字节串。

    两层的含义不同，别混：
      · `word_order`（字序）—— **寄存器之间**的顺序：`big` 高字在前（ABCD）、
        `little` 低字在前（CDAB）；
      · `byte_order`（字节序）—— **单个寄存器内部**两个字节的顺序：
        `big` 高字节在前（AB）、`little` 低字节在前（BA）。

    两者组合即工业上通用的四种排布，本函数与 `decode_registers` 遵守**标准定义**
    （已用 round-trip 验证：四种排布各自发出的值都能解回原值）：

    | 排布 | 字节序 | 字序 | 64 位寄存器序列 |
    | :--- | :--- | :--- | :--- |
    | ABCD | big | big | R0 R1 R2 R3 |
    | CDAB | big | little | R3 R2 R1 R0 |
    | BADC | little | big | 每个寄存器内部字节对调，字序不变 |
    | DCBA | little | little | 每个寄存器内部字节对调，且字序颠倒 |

    ⚠ 与现场试读脚本的一处差异，别去「对齐」它：
      现场脚本（`parse_double`）的组合 3/4 用 `byteorder='little'` 时，既把每个寄存器
      内部的字节对调、又用 `'<d'` 整体按小端解释 —— 等于**把字节序应用了两次**，
      所以它打印出来的 BADC/DCBA 并不是标准的那两种排布（脚本里的标签只是探索用的）。
      该脚本里真正有意义的是组合 1（ABCD），而本实现与它在组合 1、2 上**逐位一致**。
    """
    ordered = list(reversed(regs)) if word_order == "little" else list(regs)
    inner = "little" if byte_order == "little" else "big"
    return b"".join(int(r).to_bytes(2, inner) for r in ordered)


def decode_registers(
    regs: list[int],
    *,
    data_type: str,
    byte_order: str = EnergyMeter.BYTE_BIG,
    word_order: str = "big",
) -> int | float:
    """按数据类型把寄存器解成一个数。整数返回 `int`，浮点返回 `float`。

    ⚠ 这是**唯一的解码入口**：`read_one` 与 `_combine` 都走这里，
      避免「一处按整数拼、一处按浮点解」这类同一数据两条规则的问题
      （本项目在 v1.5.2 刚因「合计与序列各用一套口径」踩过一次）。

    `data_type` 同时决定要几个寄存器；给少了直接报错，**不猜、不补** ——
      猜出来的数不会报错，只会是一个数值离谱的读数，比报错难查得多。
    """
    need = EnergyMeter.REGISTERS_BY_TYPE.get(data_type)
    if need is None:
        raise ValueError(f"未知数据类型 {data_type!r}")
    if len(regs) < need:
        raise ValueError(f"寄存器数量不足：读到 {len(regs)} 个，{data_type} 需要 {need} 个")

    data = _to_bytes(regs[:need], byte_order, word_order)
    if data_type == EnergyMeter.DT_FLOAT32:
        return struct.unpack(">f", data)[0]
    if data_type == EnergyMeter.DT_FLOAT64:
        return struct.unpack(">d", data)[0]
    signed = data_type in (EnergyMeter.DT_INT16, EnergyMeter.DT_INT32)
    return int.from_bytes(data, "big", signed=signed)


class ModbusRtuOverTcpCollector(BaseCollector):
    def __init__(self, gateway, **kwargs):
        super().__init__(gateway, **kwargs)
        self.client: ModbusTcpClient | None = None

    # ── 连接 ────────────────────────────────────────────────
    def prepare(self) -> str:
        self.close()
        try:
            self.client = ModbusTcpClient(
                self.gateway.host,
                port=int(self.gateway.port),
                framer=FramerType.RTU,  # ★ 关键：RTU 组帧，不是 TCP 组帧
                timeout=self.timeout_ms / 1000,
                # ⚠ 千万不要传 retries=0（踩过）：pymodbus 3.7 在这个值下**根本不发请求**，
                #   13 块表全部返回 "No Response received from the remote slave"，
                #   看起来像现场断线，其实是我们自己没发出去。传 1 及以上才正常。
                #   实测：正常点 ~70—110ms；从站不存在时正好耗时一个 timeout（不会翻倍）。
                #   重试由基类统一控制，避免库内重试把单点耗时放大到不可控。
                retries=1,
            )
            if not self.client.connect():
                return f"无法连接串口服务器 {self.gateway.host}:{self.gateway.port}"
        except Exception as exc:  # noqa: BLE001
            return f"连接异常：{type(exc).__name__} {exc}"
        return ""

    def on_read_failed(self, meter, error: str) -> None:
        """RS485 是共享总线，一次超时后流里常残留半包；重连比在原连接上硬重试可靠。"""
        self.prepare()

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:  # noqa: BLE001
                pass
            self.client = None

    # ── 读一个计量点 ─────────────────────────────────────────
    def read_one(self, meter) -> CollectResult:
        if self.client is None:
            return CollectResult(ok=False, error="连接未建立")
        if meter.slave is None or meter.register is None:
            return CollectResult(ok=False, error="计量点缺少从站号或寄存器地址")

        count = int(meter.expected_registers)
        try:
            rr = self.client.read_holding_registers(
                address=int(meter.register),
                count=count,
                **{unit_kwarg(self.client): int(meter.slave)},
            )
        except Exception as exc:  # noqa: BLE001
            return CollectResult(ok=False, error=f"读取异常：{type(exc).__name__} {exc}")

        if rr.isError():
            return CollectResult(ok=False, error=f"Modbus 错误：{rr}")

        regs = list(rr.registers)
        if len(regs) < count:
            return CollectResult(ok=False, error=f"寄存器数量不足：{len(regs)}/{count}")

        # 报文留档：现场排障时「哪个从站、哪个地址、按什么类型读、读到什么」一目了然
        raw = (
            f"slave={meter.slave} addr={meter.register} "
            f"type={meter.data_type} byte={meter.byte_order} word={meter.word_order} "
            f"regs={' '.join(f'{r:04x}' for r in regs)}"
        )

        try:
            decoded = decode_registers(
                regs,
                data_type=meter.data_type,
                byte_order=meter.byte_order,
                word_order=meter.word_order,
            )
        except ValueError as exc:
            return CollectResult(ok=False, error=f"解码失败：{exc}", raw=raw)

        if isinstance(decoded, float):
            # 浮点读数可能是 NaN / ±Inf（表故障或字节序配错时常见）。
            # Decimal('nan') 能构造出来但会在后续比较与入库时到处炸，且差值毫无意义 ——
            # 一律判为采集失败并把原因写清楚，不把一个「数字形状的错误」放进库里。
            if not math.isfinite(decoded):
                return CollectResult(
                    ok=False,
                    error=f"浮点读数非法（{decoded}）——请核对数据类型与字节/字序",
                    raw=raw,
                )
            # str() 而非 Decimal(float)：repr 给出最短可往返表示，
            # 避免把 float 的二进制尾巴（12345.678000000001）带进库。
            value = Decimal(str(decoded)) * Decimal(meter.scale)
        else:
            value = Decimal(decoded) * Decimal(meter.scale)

        return CollectResult(
            ok=True,
            value=value,
            raw=raw,
            extra={
                "raw_int": decoded,
                "slave": meter.slave,
                "register": meter.register,
                "data_type": meter.data_type,
            },
        )

    @staticmethod
    def _combine(regs: list[int], word_order: str) -> int:
        """把寄存器拼成整数（兼容旧调用）。

        ⚠ 保留这个名字是为了不改既有测试与调用点；内部已统一走
          `decode_registers`，所以它和实际采集结果**不可能**出现两套口径。
          语义等同于「32 位无符号、字节序大端」。
        """
        return int(
            decode_registers(
                regs,
                data_type=EnergyMeter.DT_UINT32 if len(regs) >= 2 else EnergyMeter.DT_UINT16,
                byte_order=EnergyMeter.BYTE_BIG,
                word_order=word_order,
            )
        )
