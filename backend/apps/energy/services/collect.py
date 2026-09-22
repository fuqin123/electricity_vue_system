"""采集编排 —— 跑一轮采集：连网关 → 逐点读数 → 落原始读数 → 更新计量点状态 → 记日志。

设计要点：
  · **一个网关一个连接**：13 块电表分布在 4 个串口服务器上，只开 4 条 TCP 连接。
  · **单点失败不影响整轮**：某个从站超时/接线掉了，其余点位照常入库，
    失败原因写进 Reading.error 与 Meter.last_error，后台能直接看到是哪一块表。
  · 采集**只写原始累计值**，不做差值 —— 差值交给 aggregate.py，
    这样万一算法要改，重跑聚合即可，不用等现场重新采数据。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from django.db import transaction

from eep import conf

from ..collectors import CollectResult, build_collector
from ..models import CollectLog, Gateway, Meter, Reading

logger = logging.getLogger(__name__)


def _truncate(text: str, limit: int = 200) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def collect_round(*, gateway_codes: list[str] | None = None, dry_run: bool = False) -> dict:
    """跑一轮采集。返回汇总字典（供命令行打印与日志使用）。"""
    retry_times = int(conf.get("collect.retry_times", 2))
    retry_backoff = float(conf.get("collect.retry_backoff_seconds", 1.0))
    timeout_ms = int(conf.get("collect.timeout_ms", 3000))

    gateways = Gateway.objects.filter(enabled=True)
    if gateway_codes:
        gateways = gateways.filter(code__in=gateway_codes)

    summary: dict = {"gateways": [], "total": 0, "ok": 0, "fail": 0, "readings": 0, "dry_run": dry_run}

    for gw in gateways:
        meters = list(Meter.objects.filter(gateway=gw, enabled=True).order_by("code"))
        if not meters:
            continue

        started = datetime.now()
        t0 = time.monotonic()
        collector = build_collector(
            gw, timeout_ms=timeout_ms, retry_times=retry_times, retry_backoff=retry_backoff
        )
        results = collector.read_all(meters)
        duration_ms = int((time.monotonic() - t0) * 1000)

        now = datetime.now()
        errors: list[str] = []
        ok_count = 0
        readings: list[Reading] = []
        touched: list[Meter] = []

        for meter in meters:
            res: CollectResult = results.get(meter.code) or CollectResult(ok=False, error="未返回结果")
            # 设备自带时间的（如蒸汽平台）用它，更贴近真实计量时刻；否则用采集时刻
            at = res.at or now
            if res.ok and res.value is not None:
                ok_count += 1
            else:
                errors.append(f"{meter.code}：{res.error}")

            if not dry_run:
                readings.append(
                    Reading(
                        meter=meter,
                        at=at,
                        value=res.value if res.ok else None,
                        raw=_truncate(res.raw),
                        ok=res.ok,
                        error=_truncate(res.error),
                    )
                )
                if res.ok:
                    meter.last_value = res.value
                    meter.last_at = at
                meter.last_ok = res.ok
                meter.last_error = _truncate(res.error)
                touched.append(meter)

        if not dry_run:
            with transaction.atomic():
                Reading.objects.bulk_create(readings, batch_size=200)
                Meter.objects.bulk_update(
                    touched, ["last_value", "last_at", "last_ok", "last_error"], batch_size=200
                )
                CollectLog.objects.create(
                    gateway=gw,
                    started_at=started,
                    finished_at=datetime.now(),
                    duration_ms=duration_ms,
                    total=len(meters),
                    ok_count=ok_count,
                    fail_count=len(meters) - ok_count,
                    message=_truncate("；".join(errors), 500),
                )

        gw_summary = {
            "gateway": gw.code,
            "endpoint": gw.endpoint,
            "total": len(meters),
            "ok": ok_count,
            "fail": len(meters) - ok_count,
            "duration_ms": duration_ms,
            "errors": errors,
        }
        summary["gateways"].append(gw_summary)
        summary["total"] += len(meters)
        summary["ok"] += ok_count
        summary["fail"] += len(meters) - ok_count
        summary["readings"] += len(readings)

        logger.info(
            "网关 %s 采集完成：%s/%s 成功，耗时 %sms%s",
            gw.code,
            ok_count,
            len(meters),
            duration_ms,
            f"，失败：{'；'.join(errors)}" if errors else "",
        )

    return summary
