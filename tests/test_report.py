import pytest

from src.attribution import Support, load_hypotheses
from src.report import build_report, scope_change_impact
from src.results import RevisionReason, ResultStore, compute_result
from src.snapshot import DrugScope

from factories import CREATED_AT, make_record, make_snapshot

MONTHLY = {"110000": 7e9, "310000": 6.5e9, "440000": 6e9}
BASELINE = {"110000": 10e9, "310000": 9e9, "440000": 8e9}
SHARES_V1 = {"D1001": 0.5, "D1002": 0.3, "D1003": 0.2}
TOTAL_DECLINE = -45e9  # 六个月合计下降约四百五十亿元


def _scenario_records(snapshot, shares, extra=()):
    records = []
    periods = list(snapshot.window.periods) + list(snapshot.window.baseline_periods())
    for region in sorted(snapshot.region_scope.region_codes):
        for period in periods:
            amount = (
                BASELINE[region]
                if period < snapshot.window.start
                else MONTHLY[region]
            )
            for drug_id, share in shares.items():
                records.append(
                    make_record(
                        region=region,
                        period=period,
                        value=amount * share,
                        drug_id=drug_id,
                        patient_count=100,
                    )
                )
    return records + list(extra)


def _compute(snapshot, records, **kwargs):
    defaults = dict(
        result_id="main",
        version=1,
        reason=RevisionReason.INITIAL,
        created_at=CREATED_AT,
    )
    defaults.update(kwargs)
    return compute_result(snapshot, records, **defaults)


@pytest.fixture
def scenario():
    snapshot = make_snapshot()
    records = _scenario_records(snapshot, SHARES_V1)
    store = ResultStore()
    store.publish(_compute(snapshot, records))
    return snapshot, store


def test_total_decline_is_45_billion(scenario):
    _, store = scenario
    report = build_report(store, "main")
    assert report.total_change.delta == pytest.approx(TOTAL_DECLINE)
    assert report.coverage_ratio == 1.0
    assert report.missing_cells == ()


def test_decline_located_by_region_and_drug(scenario):
    _, store = scenario
    report = build_report(store, "main")
    region_deltas = {
        dict(c.group)["region"]: c.delta for c in report.decline_by_region
    }
    assert region_deltas["110000"] == pytest.approx(-18e9)
    assert region_deltas["310000"] == pytest.approx(-15e9)
    assert region_deltas["440000"] == pytest.approx(-12e9)
    # 下降最多者排在最前
    assert dict(report.decline_by_region[0].group)["region"] == "110000"

    drug_deltas = {dict(c.group)["drug_id"]: c.delta for c in report.decline_by_drug}
    assert drug_deltas["D1001"] == pytest.approx(TOTAL_DECLINE * 0.5)
    assert drug_deltas["D1003"] == pytest.approx(TOTAL_DECLINE * 0.2)


def test_report_collects_explanations_signals_and_caveats(scenario):
    from pathlib import Path

    from factories import complaints, visits

    snapshot, store = scenario
    hypotheses = load_hypotheses(Path("fixtures/hypotheses.json"))
    signals_snapshot = snapshot
    records = visits(
        signals_snapshot, region="110000", drug_id="D1001",
        baseline_value=1000, window_value=500,
    ) + complaints(
        signals_snapshot, region="110000", drug_id="D1001",
        baseline_value=10, window_value=14,
    )
    from src.signals import detect_interruptions

    signals = detect_interruptions(records, signals_snapshot)
    report = build_report(store, "main", hypotheses=hypotheses, signals=signals)

    supports = [s for _, s in report.explanations]
    assert supports[0] is Support.SUPPORTED  # 得到支持的解释排在最前
    assert report.interruption_signals[0].severity == "high"
    assert any("用药可及性" in c for c in report.caveats)


def test_catalog_change_impact_on_45_billion_headline(scenario):
    snapshot, store = scenario
    old_result = store.latest("main")

    # 目录变化：D1003 移出口径，D1004 新纳入（窗口期支出 10 亿元）
    new_scope = DrugScope("cat-2026-v2", frozenset({"D1001", "D1002", "D1004"}))
    snapshot_v2 = snapshot.with_drug_scope(
        new_scope, snapshot_id="snap-test-v2", created_at="2026-08-01T00:00:00Z"
    )
    extra = [
        make_record(
            region=region,
            period=period,
            value=1e9 / 18,
            drug_id="D1004",
            patient_count=100,
        )
        for region in sorted(snapshot.region_scope.region_codes)
        for period in snapshot.window.periods
    ]
    records_v2 = _scenario_records(snapshot_v2, {"D1001": 0.5, "D1002": 0.3}, extra)
    # 目录变化只能在同一结果链上生成新版本，历史版本保留
    new_result = _compute(
        snapshot_v2,
        records_v2,
        version=2,
        reason=RevisionReason.CATALOG_CHANGE,
        supersedes=1,
        created_at="2026-08-01T00:00:00Z",
    )
    store.publish(new_result)
    assert store.get("main", 1) is old_result

    impact = scope_change_impact(old_result, new_result, snapshot.drug_scope, new_scope)
    window_total_v1 = (7e9 + 6.5e9 + 6e9) * 6  # 1170 亿元
    assert impact.removed_effect == pytest.approx(window_total_v1 * 0.2)
    assert impact.added_effect == pytest.approx(1e9)
    assert impact.common_change == pytest.approx(0.0)
    # 汇总数字的变化完全是口径效应，而非实际支出变化
    assert impact.total_delta == pytest.approx(impact.scope_effect)

    report = build_report(store, "main", scope_impact=impact)
    assert report.version == 2
    assert report.scope_impact is impact
    assert any("口径效应" in c for c in report.caveats)
