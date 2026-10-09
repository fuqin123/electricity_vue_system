"""采集器基类与公共工具。

约定：**一个网关一个采集器实例**（连接复用），采集器负责把该网关下所有计量点读一遍。
这样 13 块电表只开 4 条 TCP 连接，而不是 13 条。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CollectResult:
    """单个计量点的一次采集结果。"""

    ok: bool
    value: Decimal | None = None
    raw: str = ""
    error: str = ""
    #: 设备上送的时间（API 类网关会给，如蒸汽平台的 collect_time）；串口类没有，用采集时刻
    at: datetime | None = None
    attempts: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


class BaseCollector:
    """采集器接口。

    子类实现 `prepare()` / `read_one()` / `close()`；
    重试、计时、异常兜底由基类统一处理，避免每个协议各写一套。
    """

    def __init__(self, gateway, *, timeout_ms: int = 3000, retry_times: int = 2, retry_backoff: float = 0.5):
        self.gateway = gateway
        self.timeout_ms = timeout_ms
        self.retry_times = max(0, retry_times)
        self.retry_backoff = retry_backoff

    # ── 子类实现 ────────────────────────────────────────────
    def prepare(self) -> str:
        """建立连接。返回空字符串表示成功，否则返回错误原因。"""
        return ""

    def read_one(self, meter) -> CollectResult:  # pragma: no cover - 抽象
        raise NotImplementedError

    def close(self) -> None:
        """释放连接。"""

    def on_read_failed(self, meter, error: str) -> None:
        """一次读失败后的钩子：串口服务器是共享总线，出错后重连比硬重试更稳。"""

    # ── 基类统一流程 ────────────────────────────────────────
    def read_one_with_retry(self, meter) -> CollectResult:
        """单点读取并按 `retry_times` 重试。

        ⚠ 后台的「测试连接 / 试采 / 读取当前值」必须走这里，**不要直接调 `read_one`**：
          直接调会把 `retry_times` 悄悄吞掉（参数传了、重试没发生），
          表现为「偶发一次读失败就报失败」，而现场以为是链路问题（v1.4.4 修）。
        """
        return self._read_with_retry(meter)

    def read_all(self, meters) -> dict[str, CollectResult]:
        out: dict[str, CollectResult] = {}
        meters = list(meters)
        if not meters:
            return out

        err = self.prepare()
        if err:
            for m in meters:
                out[m.code] = CollectResult(ok=False, error=err)
            logger.warning("网关 %s 连接失败：%s", self.gateway.code, err)
            return out

        try:
            for m in meters:
                out[m.code] = self._read_with_retry(m)
        finally:
            self.close()
        return out

    def _read_with_retry(self, meter) -> CollectResult:
        last = CollectResult(ok=False, error="未执行")
        for attempt in range(1, self.retry_times + 2):
            try:
                res = self.read_one(meter)
                res.attempts = attempt
                if res.ok:
                    return res
                last = res
            except Exception as exc:  # noqa: BLE001 —— 采集不能因为单点异常整轮崩掉
                last = CollectResult(ok=False, error=f"{type(exc).__name__}: {exc}", attempts=attempt)
                logger.warning("采集 %s 异常：%s", meter.code, last.error)

            if attempt <= self.retry_times:
                self.on_read_failed(meter, last.error)
                time.sleep(self.retry_backoff)

        return last


def get_path(data: Any, path: str) -> Any:
    """按 'data.0.device_data.var1' 取嵌套值；取不到返回 None。"""
    if not path:
        return None
    node = data
    for part in path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if node is None:
            return None
    return node
