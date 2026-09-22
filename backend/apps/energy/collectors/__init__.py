"""采集器注册表：网关类型 → 采集器实现。

新增一种协议（比如 OPC UA、MQTT）只需：
  1. 写一个继承 BaseCollector 的类；
  2. 在下面注册表里加一行；
  3. 在 settings.yaml 里把网关的 kind 改成新类型。
业务代码完全不用动。
"""

from __future__ import annotations

from apps.energy.models import Gateway

from .base import BaseCollector, CollectResult, get_path
from .http_api import HttpApiCollector
from .modbus_rtu import ModbusRtuOverTcpCollector

COLLECTORS: dict[str, type[BaseCollector]] = {
    Gateway.KIND_MODBUS: ModbusRtuOverTcpCollector,
    Gateway.KIND_HTTP: HttpApiCollector,
}


def build_collector(gateway, *, timeout_ms: int = 3000, retry_times: int = 2, retry_backoff: float = 0.5) -> BaseCollector:
    cls = COLLECTORS.get(gateway.kind)
    if cls is None:  # pragma: no cover
        raise ValueError(f"未知网关类型：{gateway.kind}（网关 {gateway.code}）")
    # 网关自身配置的超时优先于全局默认
    timeout = int(gateway.timeout_ms or timeout_ms)
    return cls(gateway, timeout_ms=timeout, retry_times=retry_times, retry_backoff=retry_backoff)


__all__ = ["BaseCollector", "CollectResult", "get_path", "build_collector", "COLLECTORS"]
