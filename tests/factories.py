"""测试共用的虚构数据构造；所有数据均为虚构，不含真实信息。"""

from src.snapshot import (
    AnalysisSnapshot,
    Cohort,
    DrugScope,
    ObservationWindow,
    RegionScope,
)
from src.sources import (
    METRIC_AMOUNT,
    METRIC_COMPLAINTS,
    METRIC_STOCK_RATIO,
    METRIC_VISITS,
    DataRecord,
    SourceKind,
)

REGIONS = ("110000", "310000", "440000")
DRUGS = ("D1001", "D1002", "D1003")

CREATED_AT = "2026-07-01T00:00:00Z"

_seq = 0


def make_snapshot(
    *,
    snapshot_id: str = "snap-test",
    drug_ids=DRUGS,
    regions=REGIONS,
    start: str = "2026-01",
    end: str = "2026-06",
    catalog: str = "cat-2026-v1",
) -> AnalysisSnapshot:
    return AnalysisSnapshot(
        snapshot_id=snapshot_id,
        cohort=Cohort.from_mapping("cohort-all", {"insurance": "basic_medical"}),
        drug_scope=DrugScope(catalog, frozenset(drug_ids)),
        region_scope=RegionScope(frozenset(regions)),
        window=ObservationWindow(start, end),
        created_at=CREATED_AT,
    )


def make_record(
    *,
    source: SourceKind = SourceKind.SETTLEMENT,
    metric: str = METRIC_AMOUNT,
    region: str = "110000",
    period: str = "2026-01",
    value: float = 0.0,
    drug_id: str | None = None,
    patient_count: int | None = 100,
    revision: int = 0,
) -> DataRecord:
    global _seq
    _seq += 1
    return DataRecord(
        record_id=f"rec-{_seq}",
        source=source,
        region=region,
        period=period,
        metric=metric,
        value=value,
        drug_id=drug_id,
        patient_count=patient_count,
        revision=revision,
    )


def settlement_grid(
    snapshot: AnalysisSnapshot,
    *,
    monthly_amount: float,
    baseline_amount: float,
    patient_count: int = 100,
    drug_shares: dict[str, float] | None = None,
    skip=frozenset(),
) -> list[DataRecord]:
    """为口径内全部地区与窗口/基线月份生成结算记录。

    skip 中的 (region, period) 不上报，用于模拟地区漏报。
    drug_shares 给出时按药品拆分金额。
    """
    records = []
    periods = list(snapshot.window.periods) + list(snapshot.window.baseline_periods())
    for region in sorted(snapshot.region_scope.region_codes):
        for period in periods:
            if (region, period) in skip:
                continue
            amount = baseline_amount if period < snapshot.window.start else monthly_amount
            if drug_shares:
                for drug_id, share in drug_shares.items():
                    records.append(
                        make_record(
                            region=region,
                            period=period,
                            value=amount * share,
                            drug_id=drug_id,
                            patient_count=patient_count,
                        )
                    )
            else:
                records.append(
                    make_record(
                        region=region,
                        period=period,
                        value=amount,
                        patient_count=patient_count,
                    )
                )
    return records


def metric_series(
    snapshot: AnalysisSnapshot,
    *,
    source: SourceKind,
    metric: str,
    region: str,
    drug_id: str | None,
    baseline_value: float,
    window_value: float,
) -> list[DataRecord]:
    """生成某（地区, 药品）在基线与窗口期的指标序列。"""
    records = []
    for period in snapshot.window.baseline_periods():
        records.append(
            make_record(
                source=source,
                metric=metric,
                region=region,
                period=period,
                value=baseline_value,
                drug_id=drug_id,
                patient_count=None,
            )
        )
    for period in snapshot.window.periods:
        records.append(
            make_record(
                source=source,
                metric=metric,
                region=region,
                period=period,
                value=window_value,
                drug_id=drug_id,
                patient_count=None,
            )
        )
    return records


def visits(snapshot, **kwargs):
    return metric_series(snapshot, source=SourceKind.TREATMENT, metric=METRIC_VISITS, **kwargs)


def complaints(snapshot, **kwargs):
    return metric_series(
        snapshot, source=SourceKind.PATIENT_FEEDBACK, metric=METRIC_COMPLAINTS, **kwargs
    )


def stock(snapshot, **kwargs):
    return metric_series(
        snapshot, source=SourceKind.SUPPLY, metric=METRIC_STOCK_RATIO, **kwargs
    )
