import pytest

from src.results import (
    RevisionReason,
    ResultStore,
    VersionError,
    compute_result,
)
from src.sources import ScopeError

from factories import CREATED_AT, make_record, make_snapshot, settlement_grid


def _group(result, **keys):
    want = tuple(sorted(keys.items()))
    for agg in result.aggregates:
        if agg.group == want:
            return agg
    raise AssertionError(f"缺少分组：{keys}")


def _change(result, **keys):
    want = tuple(sorted(keys.items()))
    for change in result.changes:
        if change.group == want:
            return change
    raise AssertionError(f"缺少分组：{keys}")


def test_missing_region_is_not_counted_as_zero():
    snapshot = make_snapshot()
    # 某地区整个窗口期漏报
    skip = {("440000", p) for p in snapshot.window.periods}
    records = settlement_grid(
        snapshot, monthly_amount=60, baseline_amount=100, skip=skip
    )
    result = compute_result(
        snapshot,
        records,
        result_id="r",
        version=1,
        reason=RevisionReason.INITIAL,
        created_at=CREATED_AT,
    )

    total = _group(result)
    # 汇总只覆盖已上报地区，缺失地区不按零值计入
    assert total.value == 60 * 6 * 2
    assert total.partial
    assert total.missing_cells == 6
    assert not result.coverage.complete
    assert result.coverage.ratio == pytest.approx(24 / 36)

    total_change = _change(result)
    assert total_change.delta == (60 - 100) * 6 * 2
    assert total_change.partial

    # 漏报地区本身没有任何窗口数据，按未知处理而非零
    region_change = _change(result, region="440000")
    assert region_change.delta is None
    assert region_change.partial


def test_missing_baseline_yields_unknown_change():
    snapshot = make_snapshot()
    skip = {("310000", p) for p in snapshot.window.baseline_periods()}
    records = settlement_grid(
        snapshot, monthly_amount=60, baseline_amount=100, skip=skip
    )
    result = compute_result(
        snapshot,
        records,
        result_id="r",
        version=1,
        reason=RevisionReason.INITIAL,
        created_at=CREATED_AT,
    )
    change = _change(result, region="310000")
    assert change.baseline is None
    assert change.delta is None
    assert change.partial


def test_out_of_scope_records_rejected():
    snapshot = make_snapshot()
    records = settlement_grid(snapshot, monthly_amount=60, baseline_amount=100)
    records.append(make_record(region="999999", period="2026-03", value=1))
    with pytest.raises(ScopeError):
        compute_result(
            snapshot,
            records,
            result_id="r",
            version=1,
            reason=RevisionReason.INITIAL,
            created_at=CREATED_AT,
        )


def test_revisions_create_new_versions_and_preserve_history():
    snapshot = make_snapshot()
    skip = {("440000", "2026-06")}
    records = settlement_grid(
        snapshot, monthly_amount=60, baseline_amount=100, skip=skip
    )
    store = ResultStore()
    v1 = compute_result(
        snapshot,
        records,
        result_id="r",
        version=1,
        reason=RevisionReason.INITIAL,
        created_at=CREATED_AT,
    )
    store.publish(v1)
    assert _group(v1).missing_cells == 1

    # 跨月回补：补上缺失月份后只能生成新版本
    backfilled = records + [
        make_record(region="440000", period="2026-06", value=60, patient_count=100)
    ]
    v2 = compute_result(
        snapshot,
        backfilled,
        result_id="r",
        version=2,
        reason=RevisionReason.BACKFILL,
        supersedes=1,
        created_at="2026-08-01T00:00:00Z",
    )
    store.publish(v2)

    assert store.latest("r") is v2
    assert store.get("r", 1) is v1  # 旧版本保持不变
    assert _group(store.get("r", 1)).missing_cells == 1
    assert _group(store.get("r", 2)).missing_cells == 0
    assert [r.version for r in store.history("r")] == [1, 2]


def test_version_rules_enforced():
    snapshot = make_snapshot()
    records = settlement_grid(snapshot, monthly_amount=60, baseline_amount=100)
    store = ResultStore()
    v1 = compute_result(
        snapshot,
        records,
        result_id="r",
        version=1,
        reason=RevisionReason.INITIAL,
        created_at=CREATED_AT,
    )
    store.publish(v1)

    # 重复发布同一版本
    with pytest.raises(VersionError):
        store.publish(v1)

    # 跳号发布
    v3 = compute_result(
        snapshot,
        records,
        result_id="r",
        version=3,
        reason=RevisionReason.DATA_REVISION,
        supersedes=2,
        created_at=CREATED_AT,
    )
    with pytest.raises(VersionError):
        store.publish(v3)

    # 修订版本缺少 supersedes
    v2_bad = compute_result(
        snapshot,
        records,
        result_id="r",
        version=2,
        reason=RevisionReason.UNDERREPORT,
        created_at=CREATED_AT,
    )
    with pytest.raises(VersionError):
        store.publish(v2_bad)

    # 首版本不能使用修订原因
    v1_bad = compute_result(
        snapshot,
        records,
        result_id="other",
        version=1,
        reason=RevisionReason.CATALOG_CHANGE,
        supersedes=None,
        created_at=CREATED_AT,
    )
    with pytest.raises(VersionError):
        store.publish(v1_bad)
