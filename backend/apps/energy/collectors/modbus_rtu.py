"""串口服务器采集器 —— Modbus RTU over TCP。

现场用的是「485 串口服务器」：一个网口后面挂一条 RS485 总线，总线上并着多块电表。
所以**不能用标准 Modbus TCP 帧**（那是 0x0000 事务号 + 长度 + 单元号的另一套封装），
必须让帧器按 RTU 组帧（从站号 + 功能码 + 数据 + CRC16），也就是这里的 `FramerType.RTU`。

参考实现见 `E:\\http_test\\serial_servier.py`（现场已验证可读），
本模块用 pymodbus 达到同样效果，并统一接入重试与日志。

已核对的现场读法（13 块电表全部验证通过，耗时 66—127ms）：
    功能码 03（读保持寄存器）、起始地址 1000、2 个寄存器、32 位无符号、倍率 ×0.1 → kWh
    响应：08 03 04 03 c3 3b 79 41 99   → 数据 0x03C33B79 = 63,126,393 → 6,312,639.3 kWh
"""

from __future__ import annotations

import logging
from decimal import Decimal

from pymodbus.client import ModbusTcpClient
from pymodbus.framer import FramerType

from .base import BaseCollector, CollectResult

logger = logging.getLogger(__name__)


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

        count = int(meter.registers or 2)
        try:
            rr = self.client.read_holding_registers(
                address=int(meter.register), count=count, slave=int(meter.slave)
            )
        except Exception as exc:  # noqa: BLE001
            return CollectResult(ok=False, error=f"读取异常：{type(exc).__name__} {exc}")

        if rr.isError():
            return CollectResult(ok=False, error=f"Modbus 错误：{rr}")

        regs = list(rr.registers)
        if len(regs) < count:
            return CollectResult(ok=False, error=f"寄存器数量不足：{len(regs)}/{count}")

        raw_int = self._combine(regs, meter.word_order)
        value = Decimal(raw_int) * Decimal(meter.scale)

        # 报文留档：现场排障时「哪个从站、哪个地址、读到什么」一目了然
        raw = f"slave={meter.slave} addr={meter.register} regs={' '.join(f'{r:04x}' for r in regs)}"
        return CollectResult(
            ok=True,
            value=value,
            raw=raw,
            extra={"raw_int": raw_int, "slave": meter.slave, "register": meter.register},
        )

    @staticmethod
    def _combine(regs: list[int], word_order: str) -> int:
        """把寄存器拼成整数。

        一般的电表是 32 位无符号、**高字在前**（现场 13 块都验证如此）；
        少数表是低字在前，所以字序做成配置项而不是写死。
        """
        if len(regs) == 1:
            return int(regs[0])
        first, second = int(regs[0]), int(regs[1])
        if word_order == "little":
            return (second << 16) | first
        return (first << 16) | second
