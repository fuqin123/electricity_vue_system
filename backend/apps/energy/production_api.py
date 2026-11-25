"""产量采集与「产量计量绑定」后台接口（v1.8.0）。

与能源计量绑定完全独立：
  · 能源计量绑定：车间 → 类型 → 设备 → EnergyMeter；
  · 产量计量绑定：车间 → 类型 → 设备 → PlcPoint。

PLC 设备 / 点位读写在本模块；真正的采集动作在 `services/plc.py` 与
`manage.py collect_production`，Web 请求只做配置与**一次试读**。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .api import _binding_name, _binding_sort, _body, json_err, json_ok
from .models import (
    PlcDevice,
    PlcPoint,
    PlcReading,
    ProductionBindingAssignment,
    ProductionBindingDevice,
    ProductionBindingType,
    ProductionBindingWorkshop,
    ProductionLine,
    StandardCaseRule,
)
from .services import plc as PLC


# ══════════════════════════════════════════════════════════════
#  PLC 设备与点位
# ══════════════════════════════════════════════════════════════


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _plc_device_json(device: PlcDevice, point_count: int = 0) -> dict:
    return {
        "id": device.id,
        "code": device.code,
        "name": device.name,
        "model": device.model,
        "host": device.host,
        "port": device.port,
        "rack": device.rack,
        "slot": device.slot,
        "mode": device.mode,
        "timeoutMs": device.timeout_ms,
        "intervalSeconds": device.interval_seconds,
        "enabled": device.enabled,
        "remark": device.remark,
        "endpoint": device.endpoint,
        "status": "online" if device.is_online else "offline",
        "lastOkAt": _fmt(device.last_ok_at),
        "lastFailAt": _fmt(device.last_fail_at),
        "lastError": device.last_error,
        "pointCount": point_count,
        "updatedAt": _fmt(device.updated_at),
    }


def _plc_point_json(point: PlcPoint) -> dict:
    return {
        "id": point.id,
        "code": point.code,
        "name": point.name,
        "deviceId": point.device_id,
        "deviceCode": point.device.code if point.device_id else "",
        "area": point.area,
        "areaLabel": point.get_area_display(),
        "dbNumber": point.db_number,
        "startOffset": point.start_offset,
        "bitOffset": point.bit_offset,
        "dataType": point.data_type,
        "dataTypeLabel": point.get_data_type_display(),
        "role": point.role,
        "roleLabel": point.get_role_display(),
        "address": point.address,
        "counterMode": point.counter_mode,
        "scale": float(point.scale),
        "unit": point.unit,
        "maxHourly": float(point.max_hourly) if point.max_hourly is not None else None,
        "enabled": point.enabled,
        "sort": point.sort,
        "description": point.description,
        "simBase": float(point.sim_base),
        "simRate": float(point.sim_rate),
        "lastValue": float(point.last_value) if point.last_value is not None else None,
        "lastAt": _fmt(point.last_at),
        "lastOk": point.last_ok,
        "lastError": point.last_error,
    }


def _plc_int(raw, default: int, *, label: str, low: int = 0, high: int = 65535) -> int:
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是整数") from exc
    if not low <= value <= high:
        raise ValueError(f"{label}必须在 {low}—{high} 之间")
    return value


def _plc_decimal(raw, default: str = "0") -> object:
    from decimal import Decimal, InvalidOperation

    if raw in (None, ""):
        return Decimal(default)
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("数值格式不正确") from exc


@require_GET
def admin_plc_devices(request: HttpRequest) -> JsonResponse:
    counts = {d.id: d.points.count() for d in PlcDevice.objects.prefetch_related("points")}
    rows = [_plc_device_json(d, counts.get(d.id, 0)) for d in PlcDevice.objects.order_by("code")]
    return json_ok(rows)


@csrf_exempt
@require_http_methods(["POST"])
def admin_plc_device_create(request: HttpRequest) -> JsonResponse:
    try:
        data = _body(request)
        code = str(data.get("code") or "").strip().upper()
        name = str(data.get("name") or "").strip()
        if not code:
            raise ValueError("设备编号不能为空")
        if not name:
            raise ValueError("设备名称不能为空")
        if PlcDevice.objects.filter(code=code).exists():
            return json_err(f"设备编号已存在：{code}", status=409)
        mode = str(data.get("mode") or PlcDevice.MODE_SIMULATED)
        if mode not in (PlcDevice.MODE_SNAP7, PlcDevice.MODE_SIMULATED):
            raise ValueError("采集模式必须是 snap7 或 simulated")
        host = str(data.get("host") or "").strip()
        if mode == PlcDevice.MODE_SNAP7 and not host:
            raise ValueError("真实 PLC 模式必须填写 IP")
        device = PlcDevice.objects.create(
            code=code,
            name=name,
            model=str(data.get("model") or PlcDevice.MODEL_S7_200_SMART),
            host=host,
            port=_plc_int(data.get("port"), 102, label="端口", low=1),
            rack=_plc_int(data.get("rack"), 0, label="机架号", high=7),
            slot=_plc_int(data.get("slot"), 1, label="槽位", high=31),
            mode=mode,
            timeout_ms=_plc_int(data.get("timeoutMs") or data.get("timeout_ms"), 3000, label="超时", low=100, high=60000),
            interval_seconds=_plc_int(
                data.get("intervalSeconds") or data.get("interval_seconds"), 60, label="采样周期", low=5, high=86400
            ),
            enabled=bool(data.get("enabled", True)),
            remark=str(data.get("remark") or "")[:200],
        )
    except ValueError as exc:
        return json_err(str(exc))
    return json_ok({"device": _plc_device_json(device)})


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def admin_plc_device_detail(request: HttpRequest, pk: int) -> JsonResponse:
    device = PlcDevice.objects.filter(pk=pk).first()
    if device is None:
        return json_err(f"PLC 设备不存在：{pk}", status=404, code="PLC_DEVICE_NOT_FOUND")
    if request.method == "DELETE":
        device.delete()
        return json_ok({"deleted": pk})

    try:
        data = _body(request)
        if "code" in data:
            code = str(data.get("code") or "").strip().upper()
            if not code:
                raise ValueError("设备编号不能为空")
            if PlcDevice.objects.filter(code=code).exclude(pk=device.pk).exists():
                return json_err(f"设备编号已存在：{code}", status=409)
            device.code = code
        if "name" in data:
            name = str(data.get("name") or "").strip()
            if not name:
                raise ValueError("设备名称不能为空")
            device.name = name
        if "mode" in data:
            mode = str(data.get("mode") or PlcDevice.MODE_SIMULATED)
            if mode not in (PlcDevice.MODE_SNAP7, PlcDevice.MODE_SIMULATED):
                raise ValueError("采集模式必须是 snap7 或 simulated")
            device.mode = mode
        if "host" in data:
            device.host = str(data.get("host") or "").strip()
        if device.mode == PlcDevice.MODE_SNAP7 and not device.host:
            raise ValueError("真实 PLC 模式必须填写 IP")
        if "port" in data:
            device.port = _plc_int(data.get("port"), device.port, label="端口", low=1)
        if "rack" in data:
            device.rack = _plc_int(data.get("rack"), device.rack, label="机架号", high=7)
        if "slot" in data:
            device.slot = _plc_int(data.get("slot"), device.slot, label="槽位", high=31)
        if "timeoutMs" in data or "timeout_ms" in data:
            device.timeout_ms = _plc_int(
                data.get("timeoutMs") if "timeoutMs" in data else data.get("timeout_ms"),
                device.timeout_ms, label="超时", low=100, high=60000,
            )
        if "intervalSeconds" in data or "interval_seconds" in data:
            device.interval_seconds = _plc_int(
                data.get("intervalSeconds") if "intervalSeconds" in data else data.get("interval_seconds"),
                device.interval_seconds, label="采样周期", low=5, high=86400,
            )
        if "enabled" in data:
            device.enabled = bool(data.get("enabled"))
        if "remark" in data:
            device.remark = str(data.get("remark") or "")[:200]
        if device.mode == PlcDevice.MODE_SNAP7 and not device.host:
            raise ValueError("真实 PLC 模式必须填写 IP")
        device.save()
    except ValueError as exc:
        return json_err(str(exc))
    return json_ok({"device": _plc_device_json(device, device.points.count())})


@csrf_exempt
@require_http_methods(["POST"])
def admin_plc_device_test(request: HttpRequest, pk: int) -> JsonResponse:
    """试读一次（不写产量、不写原始读数），返回每个点位的值。

    会更新点位的最近读数基准 —— 与能源「试采」一致：试读成功后不应在下次正式
    采集里把试读前的累计量再算一遍。
    """
    device = PlcDevice.objects.filter(pk=pk).first()
    if device is None:
        return json_err(f"PLC 设备不存在：{pk}", status=404, code="PLC_DEVICE_NOT_FOUND")
    try:
        data = _body(request)
    except ValueError:
        data = {}
    force_simulated = bool(data.get("simulate")) or str(request.GET.get("simulate", "")) in ("1", "true")
    now = datetime.now()
    started = datetime.now()
    results = PLC.read_points(device, now=now, force_simulated=force_simulated)
    items = []
    failed = 0
    for item in results:
        point: PlcPoint = item["point"]
        value = item["value"]
        error = item["error"]
        if value is None:
            failed += 1
            point.last_ok = False
            point.last_error = error[:200]
            point.save(update_fields=["last_ok", "last_error", "updated_at"])
        else:
            point.last_value = value
            point.last_at = now
            point.last_ok = True
            point.last_error = ""
            point.save(
                update_fields=["last_value", "last_at", "last_ok", "last_error", "updated_at"]
            )
        items.append(
            {
                "point": point.code,
                "name": point.name,
                "address": point.address,
                "ok": value is not None,
                "value": float(value) if value is not None else None,
                "error": error,
            }
        )
    ok = failed == 0 and bool(results)
    if results:
        if ok:
            device.last_ok_at = now
            device.last_error = ""
        else:
            device.last_fail_at = now
            device.last_error = f"{failed}/{len(results)} 个点位读取失败"[:200]
        device.save(update_fields=["last_ok_at", "last_fail_at", "last_error", "updated_at"])
    return json_ok(
        {
            "ok": ok,
            "simulated": force_simulated or device.mode == PlcDevice.MODE_SIMULATED,
            "durationMs": int((datetime.now() - started).total_seconds() * 1000),
            "items": items,
        }
    )


@require_GET
def admin_plc_points(request: HttpRequest) -> JsonResponse:
    qs = PlcPoint.objects.select_related("device").order_by("device__code", "sort", "code")
    device_id = request.GET.get("device")
    if device_id:
        qs = qs.filter(device_id=device_id)
    return json_ok([_plc_point_json(p) for p in qs])


def _apply_point_fields(point: PlcPoint, data: dict) -> None:
    if "code" in data:
        code = str(data.get("code") or "").strip().upper()
        if not code:
            raise ValueError("点位编号不能为空")
        if PlcPoint.objects.filter(code=code).exclude(pk=point.pk).exists():
            raise ValueError(f"点位编号已存在：{code}")
        point.code = code
    if "name" in data:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("点位名称不能为空")
        point.name = name
    if "area" in data:
        area = str(data.get("area") or "").strip().upper()
        if area not in dict(PlcPoint.AREA_CHOICES):
            raise ValueError("存储区必须是 DB / M / I / Q")
        point.area = area
    if "dbNumber" in data or "db_number" in data:
        raw = data.get("dbNumber") if "dbNumber" in data else data.get("db_number")
        point.db_number = _plc_int(raw, 1, label="DB 号", low=1)
    if point.area != PlcPoint.AREA_DB:
        point.db_number = None
    elif point.db_number is None:
        point.db_number = 1
    if "startOffset" in data or "start_offset" in data:
        raw = data.get("startOffset") if "startOffset" in data else data.get("start_offset")
        point.start_offset = _plc_int(raw, 0, label="起始字节")
    if "bitOffset" in data or "bit_offset" in data:
        raw = data.get("bitOffset") if "bitOffset" in data else data.get("bit_offset")
        point.bit_offset = None if raw in (None, "") else _plc_int(raw, 0, label="位偏移", high=7)
    if "dataType" in data or "data_type" in data:
        raw = data.get("dataType") if "dataType" in data else data.get("data_type")
        data_type = str(raw or "").strip()
        if data_type not in dict(PlcPoint.DATA_TYPE_CHOICES):
            raise ValueError("数据类型不在支持范围内")
        point.data_type = data_type
    if point.data_type == PlcPoint.DT_BOOL and point.bit_offset is None:
        point.bit_offset = 0
    if "role" in data:
        role = str(data.get("role") or "").strip()
        if role not in dict(PlcPoint.ROLE_CHOICES):
            raise ValueError("点位用途不在支持范围内")
        point.role = role
    if "counterMode" in data or "counter_mode" in data:
        raw = data.get("counterMode") if "counterMode" in data else data.get("counter_mode")
        mode = str(raw or "").strip()
        if mode not in dict(PlcPoint.COUNTER_MODE_CHOICES):
            raise ValueError("计数方式必须是 cumulative 或 instant")
        point.counter_mode = mode
    if "scale" in data:
        point.scale = _plc_decimal(data.get("scale"), "1")
    if "unit" in data:
        point.unit = str(data.get("unit") or "瓶")[:16]
    if "maxHourly" in data or "max_hourly" in data:
        raw = data.get("maxHourly") if "maxHourly" in data else data.get("max_hourly")
        point.max_hourly = None if raw in (None, "") else _plc_decimal(raw, "0")
    if "enabled" in data:
        point.enabled = bool(data.get("enabled"))
    if "sort" in data:
        point.sort = _plc_int(data.get("sort"), point.sort, label="排序", high=9999)
    if "description" in data:
        point.description = str(data.get("description") or "")[:200]
    if "simBase" in data or "sim_base" in data:
        raw = data.get("simBase") if "simBase" in data else data.get("sim_base")
        point.sim_base = _plc_decimal(raw, "0")
    if "simRate" in data or "sim_rate" in data:
        raw = data.get("simRate") if "simRate" in data else data.get("sim_rate")
        point.sim_rate = _plc_decimal(raw, "0")


@csrf_exempt
@require_http_methods(["POST"])
def admin_plc_point_create(request: HttpRequest) -> JsonResponse:
    try:
        data = _body(request)
        device_id = data.get("deviceId") or data.get("device_id")
        device = PlcDevice.objects.filter(pk=device_id).first()
        if device is None:
            raise ValueError("PLC 设备不存在，无法新增点位")
        point = PlcPoint(device=device, code="", name="")
        _apply_point_fields(point, data)
        if not point.code:
            raise ValueError("点位编号不能为空")
        if not point.name:
            raise ValueError("点位名称不能为空")
        point.save()
    except ValueError as exc:
        return json_err(str(exc))
    return json_ok({"point": _plc_point_json(point)})


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def admin_plc_point_detail(request: HttpRequest, pk: int) -> JsonResponse:
    point = PlcPoint.objects.select_related("device").filter(pk=pk).first()
    if point is None:
        return json_err(f"PLC 点位不存在：{pk}", status=404, code="PLC_POINT_NOT_FOUND")
    if request.method == "DELETE":
        point.delete()
        return json_ok({"deleted": pk})
    try:
        data = _body(request)
        _apply_point_fields(point, data)
        if not point.code or not point.name:
            raise ValueError("点位编号和名称不能为空")
        point.save()
    except ValueError as exc:
        return json_err(str(exc))
    return json_ok({"point": _plc_point_json(point)})


# ══════════════════════════════════════════════════════════════
#  产量计量绑定（车间 → 类型 → 设备 → PLC 点位）
# ══════════════════════════════════════════════════════════════


def _prod_point_json(point: PlcPoint) -> dict:
    return {
        "id": point.id,
        "code": point.code,
        "name": point.name,
        "deviceId": point.device_id,
        "deviceCode": point.device.code if point.device_id else "",
        "deviceName": point.device.name if point.device_id else "",
        "address": point.address,
        "unit": point.unit,
        "role": point.role,
        "roleLabel": point.get_role_display(),
        "counterMode": point.counter_mode,
        "enabled": point.enabled,
    }


def _prod_device_json(device: ProductionBindingDevice, btype: ProductionBindingType, workshop: ProductionBindingWorkshop) -> dict:
    points = [a.point for a in device.assignments.all()]
    return {
        "id": device.id,
        "name": device.name,
        "sort": device.sort,
        "typeId": btype.id,
        "typeName": btype.name,
        "workshopId": workshop.id,
        "workshopName": workshop.name,
        "lineCode": device.line.line_code if device.line_id else None,
        "lineName": device.line.line_name if device.line_id else "",
        "countsOutput": device.counts_output,
        "pointCodes": [p.code for p in points],
        "pointCount": len(points),
        "points": [_prod_point_json(p) for p in points],
    }


def _prod_type_json(btype: ProductionBindingType, workshop: ProductionBindingWorkshop) -> dict:
    devices = [_prod_device_json(d, btype, workshop) for d in btype.devices.all()]
    codes = {code for d in devices for code in d["pointCodes"]}
    return {
        "id": btype.id,
        "name": btype.name,
        "sort": btype.sort,
        "devices": devices,
        "pointCount": len(codes),
    }


def _prod_workshop_json(workshop: ProductionBindingWorkshop) -> dict:
    types = [_prod_type_json(t, workshop) for t in workshop.types.all()]
    codes = {code for t in types for d in t["devices"] for code in d["pointCodes"]}
    return {
        "id": workshop.id,
        "name": workshop.name,
        "sort": workshop.sort,
        "types": types,
        "typeCount": len(types),
        "pointCount": len(codes),
    }


def _prod_tree_queryset():
    return ProductionBindingWorkshop.objects.prefetch_related(
        Prefetch("types", queryset=ProductionBindingType.objects.order_by("sort", "id")),
        Prefetch(
            "types__devices",
            queryset=ProductionBindingDevice.objects.select_related("line")
            .prefetch_related(
                Prefetch(
                    "assignments",
                    queryset=ProductionBindingAssignment.objects.select_related(
                        "point__device"
                    ).order_by("sort", "id"),
                )
            )
            .order_by("sort", "id"),
        ),
    ).order_by("sort", "id")


def _prod_options() -> dict:
    return {
        "workshops": [
            {"code": ws.line_code, "name": ws.line_name}
            for ws in ProductionLine.workshops_qs().order_by("sort", "line_code")
        ],
        "lines": [
            {"code": ln.line_code, "name": ln.line_name, "product": ln.product, "rated": ln.rated}
            for ln in ProductionLine.lines_qs().order_by("sort", "line_code")
        ],
        "points": [
            _prod_point_json(p)
            for p in PlcPoint.objects.select_related("device").order_by("device__code", "sort", "code")
        ],
    }


@require_GET
def admin_production_binding_tree(request: HttpRequest) -> JsonResponse:
    return json_ok(
        {
            "workshops": [_prod_workshop_json(ws) for ws in _prod_tree_queryset()],
            "options": _prod_options(),
        }
    )


def _prod_body(request: HttpRequest) -> dict | JsonResponse:
    try:
        return _body(request)
    except ValueError as exc:
        return json_err(str(exc))


def _prod_line(data: dict, current: ProductionLine | None = None) -> ProductionLine | None:
    raw = data.get("lineCode") if "lineCode" in data else data.get("line_code")
    if raw in (None, ""):
        return None
    line = ProductionLine.lines_qs().filter(line_code=str(raw).strip().upper()).first()
    if line is None:
        raise ValueError(f"产线不存在：{raw}")
    return line


@csrf_exempt
@require_http_methods(["POST"])
def admin_prod_binding_workshops(request: HttpRequest) -> JsonResponse:
    body = _prod_body(request)
    if isinstance(body, JsonResponse):
        return body
    name, error = _binding_name(body, "车间名称")
    if error:
        return json_err(error)
    if ProductionBindingWorkshop.objects.filter(name=name).exists():
        return json_err(f"车间已存在：{name}", status=409)
    ws = ProductionBindingWorkshop.objects.create(name=name, sort=_binding_sort(body))
    return json_ok({"workshop": _prod_workshop_json(ws)})


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def admin_prod_binding_workshop_detail(request: HttpRequest, pk: int) -> JsonResponse:
    ws = ProductionBindingWorkshop.objects.filter(pk=pk).first()
    if ws is None:
        return json_err(f"产量绑定车间不存在：{pk}", status=404)
    if request.method == "DELETE":
        ws.delete()
        return json_ok({"deleted": pk})
    body = _prod_body(request)
    if isinstance(body, JsonResponse):
        return body
    name, error = _binding_name(body, "车间名称", fallback=ws.name)
    if error:
        return json_err(error)
    if ProductionBindingWorkshop.objects.filter(name=name).exclude(pk=ws.pk).exists():
        return json_err(f"车间已存在：{name}", status=409)
    ws.name = name
    ws.sort = _binding_sort(body, ws.sort)
    ws.save(update_fields=["name", "sort", "updated_at"])
    return json_ok({"workshop": _prod_workshop_json(ws)})


@csrf_exempt
@require_http_methods(["POST"])
def admin_prod_binding_types(request: HttpRequest) -> JsonResponse:
    body = _prod_body(request)
    if isinstance(body, JsonResponse):
        return body
    workshop_id = body.get("workshopId") or body.get("workshop_id")
    ws = ProductionBindingWorkshop.objects.filter(pk=workshop_id).first()
    if ws is None:
        return json_err("车间不存在，无法新增类型")
    name, error = _binding_name(body, "类型名称")
    if error:
        return json_err(error)
    if ProductionBindingType.objects.filter(workshop=ws, name=name).exists():
        return json_err(f"该车间下已存在类型：{name}", status=409)
    btype = ProductionBindingType.objects.create(workshop=ws, name=name, sort=_binding_sort(body))
    return json_ok({"type": _prod_type_json(btype, ws)})


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def admin_prod_binding_type_detail(request: HttpRequest, pk: int) -> JsonResponse:
    btype = ProductionBindingType.objects.select_related("workshop").filter(pk=pk).first()
    if btype is None:
        return json_err(f"产量绑定类型不存在：{pk}", status=404)
    if request.method == "DELETE":
        btype.delete()
        return json_ok({"deleted": pk})
    body = _prod_body(request)
    if isinstance(body, JsonResponse):
        return body
    name, error = _binding_name(body, "类型名称", fallback=btype.name)
    if error:
        return json_err(error)
    if ProductionBindingType.objects.filter(workshop=btype.workshop, name=name).exclude(pk=btype.pk).exists():
        return json_err(f"该车间下已存在类型：{name}", status=409)
    btype.name = name
    btype.sort = _binding_sort(body, btype.sort)
    btype.save(update_fields=["name", "sort", "updated_at"])
    return json_ok({"type": _prod_type_json(btype, btype.workshop)})


@csrf_exempt
@require_http_methods(["POST"])
def admin_prod_binding_devices(request: HttpRequest) -> JsonResponse:
    body = _prod_body(request)
    if isinstance(body, JsonResponse):
        return body
    type_id = body.get("typeId") or body.get("type_id")
    btype = ProductionBindingType.objects.select_related("workshop").filter(pk=type_id).first()
    if btype is None:
        return json_err("类型不存在，无法新增设备")
    name, error = _binding_name(body, "设备名称")
    if error:
        return json_err(error)
    if ProductionBindingDevice.objects.filter(type=btype, name=name).exists():
        return json_err(f"该类型下已存在设备：{name}", status=409)
    try:
        line = _prod_line(body)
    except ValueError as exc:
        return json_err(str(exc))
    device = ProductionBindingDevice.objects.create(
        type=btype,
        name=name,
        line=line,
        counts_output=bool(body.get("countsOutput", True)),
        sort=_binding_sort(body),
    )
    return json_ok({"device": _prod_device_json(device, btype, btype.workshop)})


def _prod_device_queryset():
    return ProductionBindingDevice.objects.select_related(
        "type__workshop", "line"
    ).prefetch_related(
        Prefetch(
            "assignments",
            queryset=ProductionBindingAssignment.objects.select_related("point__device").order_by("sort", "id"),
        )
    )


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def admin_prod_binding_device_detail(request: HttpRequest, pk: int) -> JsonResponse:
    device = _prod_device_queryset().filter(pk=pk).first()
    if device is None:
        return json_err(f"产量绑定设备不存在：{pk}", status=404)
    if request.method == "DELETE":
        device.delete()
        return json_ok({"deleted": pk})
    body = _prod_body(request)
    if isinstance(body, JsonResponse):
        return body
    name, error = _binding_name(body, "设备名称", fallback=device.name)
    if error:
        return json_err(error)
    if ProductionBindingDevice.objects.filter(type_id=device.type_id, name=name).exclude(pk=device.pk).exists():
        return json_err(f"该类型下已存在设备：{name}", status=409)
    try:
        if "lineCode" in body or "line_code" in body:
            device.line = _prod_line(body)
    except ValueError as exc:
        return json_err(str(exc))
    device.name = name
    device.sort = _binding_sort(body, device.sort)
    if "countsOutput" in body or "counts_output" in body:
        device.counts_output = bool(
            body.get("countsOutput") if "countsOutput" in body else body.get("counts_output")
        )
    device.save()
    return json_ok({"device": _prod_device_json(device, device.type, device.type.workshop)})


@csrf_exempt
@require_http_methods(["PUT"])
def admin_prod_binding_device_points(request: HttpRequest, pk: int) -> JsonResponse:
    device = _prod_device_queryset().filter(pk=pk).first()
    if device is None:
        return json_err(f"产量绑定设备不存在：{pk}", status=404)
    body = _prod_body(request)
    if isinstance(body, JsonResponse):
        return body
    raw = body.get("pointCodes")
    if raw is None:
        raw = body.get("point_codes")
    if not isinstance(raw, list):
        return json_err("pointCodes 必须是点位编码数组")
    codes: list[str] = []
    for value in raw:
        code = str(value or "").strip().upper()
        if code and code not in codes:
            codes.append(code)
    points = list(PlcPoint.objects.select_related("device").filter(code__in=codes).order_by("code"))
    found = {p.code for p in points}
    missing = [c for c in codes if c not in found]
    if missing:
        return json_err("PLC 点位不存在：" + "、".join(missing))
    with transaction.atomic():
        device.assignments.all().delete()
        ProductionBindingAssignment.objects.bulk_create(
            [ProductionBindingAssignment(device=device, point=p, sort=idx) for idx, p in enumerate(points)]
        )
    device = _prod_device_queryset().get(pk=pk)
    return json_ok(
        {
            "device": _prod_device_json(device, device.type, device.type.workshop),
            "saved": len(points),
        }
    )


# ══════════════════════════════════════════════════════════════
#  标箱换算规则（v1.8.1）：(入数, ml) → 设定值
# ══════════════════════════════════════════════════════════════


def _case_rule_json(rule: StandardCaseRule) -> dict:
    return {
        "id": rule.id,
        "inCount": rule.in_count,
        "ml": rule.ml,
        "factor": float(rule.factor),
        "remark": rule.remark,
        "updatedAt": _fmt(rule.updated_at),
    }


def _case_rule_values(data: dict) -> dict:
    try:
        in_count = int(data.get("inCount") or data.get("in_count") or 0)
        ml = int(data.get("ml") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("入数和 ml 必须是整数") from exc
    if not 1 <= in_count <= 999:
        raise ValueError("入数必须在 1—999 之间")
    if not 1 <= ml <= 100000:
        raise ValueError("ml 数必须在 1—100000 之间")
    raw_factor = data.get("factor", 1)
    from decimal import Decimal, InvalidOperation

    try:
        factor = Decimal(str(raw_factor if raw_factor not in (None, "") else 1))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("设定值必须是数字") from exc
    if factor <= 0:
        raise ValueError("设定值必须大于 0")
    return {
        "in_count": in_count,
        "ml": ml,
        "factor": factor,
        "remark": str(data.get("remark") or "")[:200],
    }


@require_GET
def admin_standard_case_rules(request: HttpRequest) -> JsonResponse:
    rows = [_case_rule_json(r) for r in StandardCaseRule.objects.order_by("ml", "in_count")]
    return json_ok(rows)


@csrf_exempt
@require_http_methods(["POST"])
def admin_standard_case_rule_create(request: HttpRequest) -> JsonResponse:
    try:
        values = _case_rule_values(_body(request))
        if StandardCaseRule.objects.filter(in_count=values["in_count"], ml=values["ml"]).exists():
            return json_err("该入数 + ml 的规则已存在", status=409)
        rule = StandardCaseRule.objects.create(**values)
    except ValueError as exc:
        return json_err(str(exc))
    return json_ok({"rule": _case_rule_json(rule)})


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def admin_standard_case_rule_detail(request: HttpRequest, pk: int) -> JsonResponse:
    rule = StandardCaseRule.objects.filter(pk=pk).first()
    if rule is None:
        return json_err(f"标箱换算规则不存在：{pk}", status=404)
    if request.method == "DELETE":
        rule.delete()
        return json_ok({"deleted": pk})
    try:
        values = _case_rule_values(_body(request))
        if (
            StandardCaseRule.objects.filter(in_count=values["in_count"], ml=values["ml"])
            .exclude(pk=rule.pk)
            .exists()
        ):
            return json_err("该入数 + ml 的规则已存在", status=409)
        for key, value in values.items():
            setattr(rule, key, value)
        rule.save()
    except ValueError as exc:
        return json_err(str(exc))
    return json_ok({"rule": _case_rule_json(rule)})


# ══════════════════════════════════════════════════════════════
#  PLC 设备历史读数（v1.8.3）
# ══════════════════════════════════════════════════════════════


@require_GET
def admin_plc_readings(request: HttpRequest) -> JsonResponse:
    """PLC 设备历史读数 —— 原始值 + 本小时首末差值。

    与仪表读数查询同思路：现场排查要看 PLC 原始读数，而不是加工后的产量。
    小时差值按「同一小时第一条与最后一条成功读数相减」计算；只有一条读数时
    如实标记为「数据不足」，不拿 0 冒充。
    """
    from .services import ranges

    try:
        start_at, end_at = ranges.parse_hours(request)
    except ValueError as exc:
        return json_err(str(exc))

    qs = PlcReading.objects.filter(
        collect_time__gte=start_at, collect_time__lte=end_at
    ).select_related("point", "point__device")
    device = request.GET.get("device")
    if device:
        qs = qs.filter(point__device__id=int(device)) if device.isdigit() else qs.filter(
            point__device__code__iexact=device
        )
    point = request.GET.get("point")
    if point:
        qs = qs.filter(point__id=int(point)) if point.isdigit() else qs.filter(
            point__code__iexact=point
        )
    ok = request.GET.get("ok")
    if ok in ("0", "1"):
        qs = qs.filter(ok=(ok == "1"))

    qs = qs.order_by("-collect_time", "point__code")
    total = qs.count()
    page = max(1, int(request.GET.get("page", 1) or 1))
    size = max(1, min(int(request.GET.get("pageSize", 50) or 50), 500))
    rows = list(qs[(page - 1) * size : page * size])

    # 计算本页涉及到的 (点位, 小时) 的首末差值：只多查这一小段，不扫整库
    delta_map: dict[tuple[int, object, int], dict] = {}
    if rows:
        point_ids = {r.point_id for r in rows}
        hour_starts = [
            r.collect_time.replace(minute=0, second=0, microsecond=0)
            for r in rows
            if r.collect_time
        ]
        range_start = min(hour_starts)
        range_end = max(hour_starts) + timedelta(hours=1)
        readings = (
            PlcReading.objects.filter(
                point_id__in=point_ids,
                collect_time__gte=range_start,
                collect_time__lt=range_end,
                ok=True,
                value__isnull=False,
            )
            .select_related("point")
            .order_by("point_id", "collect_time", "id")
        )
        buckets: dict[tuple[int, object, int], list] = {}
        for r in readings:
            key = (r.point_id, r.collect_time.date(), r.collect_time.hour)
            buckets.setdefault(key, []).append(r)
        for key, items in buckets.items():
            p = items[0].point
            if p.role == PlcPoint.ROLE_SPEC:
                delta_map[key] = {"hourDelta": None, "hourStatus": "spec", "hourNote": "规格点不计算差值"}
                continue
            if p.counter_mode == PlcPoint.MODE_INSTANT:
                delta_map[key] = {
                    "hourDelta": sum((r.value for r in items), Decimal(0)),
                    "hourStatus": "ok",
                    "hourNote": "瞬时值按本条小时累加",
                }
                continue
            if len(items) < 2:
                delta_map[key] = {
                    "hourDelta": None,
                    "hourStatus": "insufficient",
                    "hourNote": "本小时仅 1 条成功读数，无法计算差值",
                }
                continue
            first, last = items[0], items[-1]
            delta = last.value - first.value
            if delta < 0:
                delta_map[key] = {
                    "hourDelta": None,
                    "hourStatus": "reset",
                    "hourNote": "本小时累计值回退（换表 / 归零），差值不成立",
                }
            else:
                delta_map[key] = {
                    "hourDelta": delta,
                    "hourStatus": "ok",
                    "hourNote": "本小时最后一条 − 第一条成功读数",
                }

    items = []
    for r in rows:
        key = (r.point_id, r.collect_time.date(), r.collect_time.hour)
        extra = delta_map.get(
            key,
            {"hourDelta": None, "hourStatus": "insufficient", "hourNote": "本小时没有该点位的成功读数"},
        )
        p = r.point
        items.append(
            {
                "id": r.id,
                "collectTime": _fmt(r.collect_time),
                "device": p.device.code,
                "deviceName": p.device.name,
                "deviceId": p.device_id,
                "point": p.code,
                "pointName": p.name,
                "pointId": p.id,
                "role": p.role,
                "roleLabel": p.get_role_display(),
                "address": p.address,
                "unit": p.unit,
                "value": float(r.value) if r.value is not None else None,
                "ok": r.ok,
                "error": r.error,
                "hourDelta": float(extra["hourDelta"]) if extra["hourDelta"] is not None else None,
                "hourStatus": extra["hourStatus"],
                "hourNote": extra["hourNote"],
            }
        )
    return json_ok({"list": items, "total": total, "page": page, "pageSize": size})
