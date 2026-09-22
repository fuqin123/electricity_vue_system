"""HTTP API 采集器 —— 读第三方数据平台的 JSON 接口。

现场样例（一期蒸汽表 1#，平台：顶津能源管理）：

    GET http://47.111.74.100:8980/js/a/at/yewushishi/atYewushuju/Data_dingjin?cid=135&token=***
    {
      "code": 1, "msg": "操作成功!",
      "data": [{
        "device_id": "5082",
        "device_name": "长沙顶津食品有限公司",
        "type_id": 2,
        "device_data": {
          "collect_time": "2026-09-22 14:25:00",
          "var1": 60915.18,   ← 累计蒸汽量，做差值得每小时用量
          "var2": 0.28,       ← 瞬时参数
          "var3": 0.92,
          "var4": 214.97
        }
      }]
    }

取值路径全部走配置（`value_path` / `time_path`），换表只改配置不改代码。
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .base import BaseCollector, CollectResult, get_path

logger = logging.getLogger(__name__)

#: 平台约定的成功业务码
OK_CODE = 1


class HttpApiCollector(BaseCollector):
    def read_one(self, meter) -> CollectResult:
        url = self._build_url(meter)
        try:
            body = self._fetch(url)
        except urllib.error.HTTPError as exc:
            return CollectResult(ok=False, error=f"HTTP {exc.code}")
        except urllib.error.URLError as exc:
            return CollectResult(ok=False, error=f"网络错误：{exc.reason}")
        except Exception as exc:  # noqa: BLE001
            return CollectResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            return CollectResult(ok=False, error=f"响应不是合法 JSON：{exc}", raw=body[:150])

        if isinstance(payload, dict) and "code" in payload and payload.get("code") != OK_CODE:
            return CollectResult(
                ok=False,
                error=f"业务码异常 code={payload.get('code')} msg={payload.get('msg')}",
                raw=body[:150],
            )

        raw_value = get_path(payload, meter.value_path)
        if raw_value is None:
            return CollectResult(ok=False, error=f"按路径 {meter.value_path} 取不到值", raw=body[:150])

        try:
            value = Decimal(str(raw_value)) * Decimal(meter.scale)
        except (InvalidOperation, ValueError, TypeError) as exc:
            return CollectResult(ok=False, error=f"取值无法转成数值：{raw_value}（{exc}）")

        at = self._read_time(payload, meter)
        # 顺带把同一响应里的其它字段留档（瞬时流量/温度/压力等），排障时有据可查
        extra = {}
        data0 = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data0, list) and data0 and isinstance(data0[0], dict):
            extra = dict(data0[0].get("device_data") or {})

        return CollectResult(ok=True, value=value, raw=body[:200], at=at, extra=extra)

    # ── 内部工具 ────────────────────────────────────────────
    def _build_url(self, meter) -> str:
        base = (self.gateway.base_url or "").rstrip("/")
        path = meter.url_path or ""
        if not path.startswith("/"):
            path = "/" + path
        url = base + path
        params = meter.params or {}
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        return url

    def _fetch(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "eep-collector/1.2"})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=self.timeout_ms / 1000, context=ctx) as resp:
            return resp.read().decode("utf-8", "replace")

    @staticmethod
    def _read_time(payload, meter) -> datetime | None:
        """取设备上送时间。拿不到就用采集时刻（由调用方填）。"""
        if not meter.time_path:
            return None
        raw = get_path(payload, meter.time_path)
        if not raw:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(str(raw), fmt)
            except ValueError:
                continue
        logger.warning("计量点 %s 的采集时间格式无法解析：%s", meter.code, raw)
        return None
