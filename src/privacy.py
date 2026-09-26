"""聚合阈值与公开材料检查：敏感明细保持聚合，公开材料不得反推个人。"""

from __future__ import annotations

from dataclasses import replace

DEFAULT_MIN_PATIENTS = 10


class PrivacyError(ValueError):
    """公开材料未达到聚合阈值要求。"""


def needs_suppression(
    patient_count: int | None, min_patients: int = DEFAULT_MIN_PATIENTS
) -> bool:
    """人数未知或低于阈值时一律按未达阈值处理。"""
    return patient_count is None or patient_count < min_patients


def suppress_aggregate(aggregate, min_patients: int = DEFAULT_MIN_PATIENTS):
    """未达阈值的聚合单元隐藏数值并标记。"""
    if aggregate.suppressed or needs_suppression(aggregate.patient_count, min_patients):
        return replace(aggregate, value=None, suppressed=True)
    return aggregate


def suppress_change(change, min_patients: int = DEFAULT_MIN_PATIENTS):
    """未达阈值的变化单元隐藏基线、现值与差值并标记。"""
    if change.suppressed or needs_suppression(change.patient_count, min_patients):
        return replace(change, baseline=None, current=None, delta=None, suppressed=True)
    return change


def ensure_public_safe(
    aggregates, changes=(), min_patients: int = DEFAULT_MIN_PATIENTS
) -> None:
    """公开前检查：任何未隐藏的单元都必须达到聚合阈值。"""
    for item in tuple(aggregates) + tuple(changes):
        if not item.suppressed and needs_suppression(item.patient_count, min_patients):
            raise PrivacyError(
                f"聚合单元未达最小人数阈值 {min_patients}：{dict(item.group)}"
            )


def public_view(result) -> dict:
    """生成公开材料视图：仅保留聚合数值，不含人数与明细标识。"""
    ensure_public_safe(result.aggregates, result.changes)
    return {
        "result_id": result.result_id,
        "version": result.version,
        "snapshot_id": result.snapshot_id,
        "coverage_ratio": round(result.coverage.ratio, 4),
        "missing_cells": sorted(result.coverage.missing),
        "aggregates": [
            {
                "group": dict(a.group),
                "metric": a.metric,
                "value": a.value,
                "suppressed": a.suppressed,
                "partial": a.partial,
            }
            for a in result.aggregates
        ],
        "changes": [
            {
                "group": dict(c.group),
                "delta": c.delta,
                "suppressed": c.suppressed,
                "partial": c.partial,
            }
            for c in result.changes
        ],
    }
