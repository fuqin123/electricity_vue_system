"""数据质量相关的可运营参数（后台「系统参数」页维护）。"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, timedelta

from ..models import EnergyReviewLog, SystemParam

DEFAULT_REVIEW_HOUR = 11.0
DEFAULT_DEVIATION_MINUTES = 10

PARAM_REVIEW_HOUR = "data_quality.review_hour"
PARAM_DEVIATION_MINUTES = "data_quality.deviation_minutes"


def _number(key: str, default: float) -> float:
    row = SystemParam.objects.filter(key=key).first()
    if row is None or row.value is None:
        return default
    return float(row.value)


def deviation_minutes() -> int:
    """端点/区间时长允许偏差（分钟）；0 也可配置为严格模式。"""
    return max(0, int(_number(PARAM_DEVIATION_MINUTES, DEFAULT_DEVIATION_MINUTES)))


def review_hour() -> float:
    """异常数据人工复核触发时刻（小时，可含小数，如 11.5 = 11:30）。"""
    return min(23.99, max(0.0, _number(PARAM_REVIEW_HOUR, DEFAULT_REVIEW_HOUR)))


def review_due(now: datetime) -> bool:
    current = now.hour + now.minute / 60.0 + now.second / 3600.0
    return current >= review_hour()


def review_target_day(now: datetime) -> date_cls:
    """复核目标：上一个生产日（生产日到次日 06:00 结束）。"""
    from .business_day import production_day

    return production_day(now) - timedelta(days=1)


def reviewed_today(now: datetime) -> bool:
    return EnergyReviewLog.objects.filter(review_date=now.date()).exists()
