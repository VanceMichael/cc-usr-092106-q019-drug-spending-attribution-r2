import pytest

from src.privacy import (
    PrivacyError,
    ensure_public_safe,
    public_view,
)
from src.results import RevisionReason, compute_result

from factories import CREATED_AT, make_snapshot, settlement_grid


def _result(patient_count):
    snapshot = make_snapshot()
    records = settlement_grid(
        snapshot,
        monthly_amount=60,
        baseline_amount=100,
        patient_count=patient_count,
    )
    return compute_result(
        snapshot,
        records,
        result_id="r",
        version=1,
        reason=RevisionReason.INITIAL,
        created_at=CREATED_AT,
    )


def test_small_cells_are_suppressed():
    # 每个（地区, 期间）单元仅 1 名患者，地区分组远低于默认阈值
    result = _result(patient_count=1)
    by_group = {dict(a.group).get("region", "total"): a for a in result.aggregates if "region" in dict(a.group) or not a.group}
    # 地区分组人数不足 → 隐藏；总计人数达到阈值 → 保留
    assert by_group["110000"].suppressed
    assert by_group["110000"].value is None
    assert not by_group["total"].suppressed
    assert by_group["total"].value == 60 * 6 * 3
    # 无数据的药品分组人数未知，一律按未达阈值隐藏
    drug_aggs = [a for a in result.aggregates if "drug_id" in dict(a.group)]
    assert all(a.suppressed for a in drug_aggs)


def test_adequate_cells_are_not_suppressed():
    result = _result(patient_count=100)
    region_and_total = [
        a for a in result.aggregates if "drug_id" not in dict(a.group)
    ]
    assert region_and_total
    assert not any(a.suppressed for a in region_and_total)
    total = next(a for a in result.aggregates if not a.group)
    assert total.value == 60 * 6 * 3


def test_unknown_patient_count_is_treated_as_below_threshold():
    result = _result(patient_count=None)
    assert all(a.suppressed for a in result.aggregates)


def test_public_view_hides_counts_and_is_safe():
    result = _result(patient_count=100)
    view = public_view(result)
    assert view["coverage_ratio"] == 1.0
    assert view["missing_cells"] == []
    for item in view["aggregates"] + view["changes"]:
        assert "patient_count" not in item


def test_public_view_rejects_unsafe_result():
    result = _result(patient_count=1)
    # 构造一个未隐藏却低于阈值的单元，公开检查必须拦截
    unsafe = next(a for a in result.aggregates if a.suppressed)
    object.__setattr__(unsafe, "suppressed", False)
    object.__setattr__(unsafe, "value", 1.0)
    with pytest.raises(PrivacyError):
        ensure_public_safe([unsafe])
    with pytest.raises(PrivacyError):
        public_view(result)
