"""采集编排 —— 跑一轮采集：连网关 → 逐点读数 → 落原始读数 → 更新计量点状态 → 记日志。

设计要点：
  · **一个网关一个连接**：13 块电表分布在 4 个串口服务器上，只开 4 条 TCP 连接。
  · **单点失败不影响整轮**：某个从站超时/接线掉了，其余点位照常入库，
    失败原因写进 EnergyRaw.error 与 EnergyMeter.last_error，后台能直接看到是哪一块表。
  · 采集**只写原始累计值**，不做差值 —— 差值交给 aggregate.py，
    这样万一算法要改，重跑聚合即可，不用等现场重新采数据。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from django.db import transaction

from eep import conf

from ..collectors import CollectResult, build_collector
from ..models import EnergyCollectLog, EnergyGateway, EnergyMeter, EnergyRaw
from . import slots

logger = logging.getLogger(__name__)


def _truncate(text: str, limit: int = 200) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _slot_done_ids(meters, slot: datetime) -> set[int]:
    """某采样槽已经成功的计量点 id 集合。"""
    ids = [m.id for m in meters]
    if not ids:
        return set()

    done = set(
        EnergyRaw.objects.filter(
            meter_id__in=ids, slot_boundary=slot, ok=True, reading__isnull=False
        ).values_list("meter_id", flat=True)
    )
    # v1.7.3 之前的读数没有 slot_boundary。按旧口径（本小时内有成功读数）
    # 兼容一次，避免升级后的第一轮把所有点位重新采一遍。
    done |= set(
        EnergyRaw.objects.filter(
            meter_id__in=ids,
            slot_boundary__isnull=True,
            ok=True,
            reading__isnull=False,
            collect_time__gte=slot,
            collect_time__lt=slot + timedelta(hours=1),
        ).values_list("meter_id", flat=True)
    )
    return done


def missing_meters(slot: datetime, gateway_codes: list[str] | None = None) -> list[EnergyMeter]:
    """本槽还缺哪些点位的成功读数（只在这些点位之间补采）。

    只统计启用网关下的启用点位 —— 网关被停用时，它的点位永远不会被
    `collect_round` 采到，不能因此让 `--if-due` 每 5 分钟空跑一轮。
    """
    qs = EnergyMeter.objects.filter(enabled=True, gateway__enabled=True)
    if gateway_codes:
        qs = qs.filter(gateway__code__in=gateway_codes)
    meters = list(qs.select_related("gateway", "line", "energy_type").order_by("code"))
    if not meters:
        return []
    done = _slot_done_ids(meters, slot)
    return [m for m in meters if m.id not in done]


def collect_round(
    *,
    gateway_codes: list[str] | None = None,
    meter_ids: list[int] | None = None,
    slot: datetime | None = None,
    dry_run: bool = False,
) -> dict:
    """跑一轮采集。返回汇总字典（供命令行打印与日志使用）。

    `meter_ids` 非 None 时只采这些点位（按缺失点位补采），None 表示全部启用点位。
    `slot` 是本轮读数归属的整点边界；未指定时取离当前时刻最近的整点。

    ⚠ 采集日志的 `total / ok_count / fail_count` 仍按**网关下全部启用点位**统计，
      不是本轮实际尝试的点位数。补采轮只试 1 个失败点位时，如果日志写成 1/0，
      告警引擎会把它当成「整网关全失败」。实际尝试数看 `summary["gateways"]`
      与日志 message 里的「补采缺失点位 N/M」。
    """
    retry_times = int(conf.get("collect.retry_times", 2))
    retry_backoff = float(conf.get("collect.retry_backoff_seconds", 1.0))
    timeout_ms = int(conf.get("collect.timeout_ms", 3000))

    run_at = datetime.now()
    slot = slot or slots.nearest_slot(run_at)
    target_ids = set(meter_ids) if meter_ids is not None else None

    gateways = EnergyGateway.objects.filter(enabled=True)
    if gateway_codes:
        gateways = gateways.filter(code__in=gateway_codes)

    summary: dict = {
        "gateways": [],
        "total": 0,
        "ok": 0,
        "fail": 0,
        "readings": 0,
        "dry_run": dry_run,
        "slot": slot.strftime("%Y-%m-%d %H:%M"),
    }

    for gw in gateways:
        all_meters = list(EnergyMeter.objects.filter(gateway=gw, enabled=True).order_by("code"))
        meters = all_meters if target_ids is None else [m for m in all_meters if m.id in target_ids]
        if not meters:
            continue

        # 补采轮的日志要把本槽此前已成功的点位算进 ok，否则 1 个失败点位
        # 会让整台网关看起来「全失败」，误触发网关离线告警。
        done_before = _slot_done_ids(all_meters, slot) if not dry_run else set()

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
        ok_ids: set[int] = set()
        readings: list[EnergyRaw] = []
        touched: list[EnergyMeter] = []

        for meter in meters:
            res: CollectResult = results.get(meter.code) or CollectResult(ok=False, error="未返回结果")
            # 设备自带时间的（如蒸汽平台）用它，更贴近真实计量时刻；否则用采集时刻
            at = res.at or now
            if res.ok and res.value is not None:
                ok_count += 1
                ok_ids.add(meter.id)
            else:
                errors.append(f"{meter.code}：{res.error}")

            if not dry_run:
                readings.append(
                    EnergyRaw(
                        meter=meter,
                        # 归属在入库时快照一份，汇总时不用连表
                        line=meter.line,
                        energy_type=meter.energy_type,
                        collect_time=at,
                        slot_boundary=slot,
                        reading=res.value if res.ok else None,
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
            slot_ok = len(done_before | ok_ids)
            message_parts: list[str] = []
            if target_ids is not None:
                message_parts.append(f"补采缺失点位 {len(meters)}/{len(all_meters)}")
            message_parts.extend(errors)
            with transaction.atomic():
                EnergyRaw.objects.bulk_create(readings, batch_size=200)
                EnergyMeter.objects.bulk_update(
                    touched, ["last_value", "last_at", "last_ok", "last_error"], batch_size=200
                )
                EnergyCollectLog.objects.create(
                    gateway=gw,
                    started_at=started,
                    finished_at=datetime.now(),
                    duration_ms=duration_ms,
                    total=len(all_meters),
                    ok_count=slot_ok,
                    fail_count=len(all_meters) - slot_ok,
                    message=_truncate("；".join(message_parts), 500),
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

        retry_note = f"（补采 {len(meters)}/{len(all_meters)}）" if target_ids is not None else ""
        logger.info(
            "网关 %s 采集完成：%s/%s 成功%s，耗时 %sms%s",
            gw.code,
            ok_count,
            len(meters),
            retry_note,
            duration_ms,
            f"，失败：{'；'.join(errors)}" if errors else "",
        )

    return summary
