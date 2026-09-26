"""示例面板加载：把 ``fixtures/sample_panel.json`` 灌入后台。

面板为虚构数据，覆盖六类典型情形，便于演示与测试：

- R1/R2 的重点药品在核查行动加强后明显下降；
- D03 在观察期开始时价格下调（量稳价降）；
- D05 在 R1/R2 出现供应短缺；
- R2 诊疗量稳定但取药难反馈上升（疑似取药受阻）；
- R3 的 2026-05 结算随 2026-06 批次到达（跨月回补）；
- R4 的 2026-06 结算 initially 漏报，``apply_backfill`` 补报。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.backend import ExplanationBackend
from src.ingestion import IngestBatch
from src.models import (
    FeedbackRecord,
    FrozenScope,
    InspectionRecord,
    PriceRecord,
    SettlementRecord,
    Source,
    SupplyRecord,
    VisitsRecord,
)


@dataclass(frozen=True)
class SamplePanel:
    """解析后的示例面板。"""

    raw: dict

    @property
    def meta(self) -> dict:
        return self.raw["meta"]


def load_sample_panel(path: Path) -> SamplePanel:
    """读取示例面板文件。"""
    return SamplePanel(json.loads(path.read_text(encoding="utf-8")))


def make_scope(
    panel: SamplePanel,
    drug_ids: tuple[str, ...] | None = None,
    catalog_version: str | None = None,
) -> FrozenScope:
    """由面板元信息构造冻结口径；可替换药品清单模拟目录变化。"""
    meta = panel.meta
    return FrozenScope(
        population=meta["population"],
        catalog_version=catalog_version or meta["catalog_version"],
        drug_ids=tuple(drug_ids) if drug_ids is not None else tuple(meta["drug_ids"]),
        regions=tuple(meta["regions"]),
        baseline_start=meta["baseline_months"][0],
        baseline_end=meta["baseline_months"][-1],
        observation_start=meta["observation_months"][0],
        observation_end=meta["observation_months"][-1],
    )


def _settlement_records(panel: SamplePanel) -> list[SettlementRecord]:
    months = panel.meta["months"]
    records = []
    for region, drugs in panel.raw["settlement"].items():
        for drug_id, series in drugs.items():
            for index, amount in enumerate(series["amount"]):
                records.append(
                    SettlementRecord(
                        month=months[index],
                        region=region,
                        drug_id=drug_id,
                        amount_yuan=amount,
                        patient_count=series["patients"][index],
                        claim_count=series["claims"][index],
                    )
                )
    return records


def ingest_initial(backend: ExplanationBackend, panel: SamplePanel) -> None:
    """按月份逐批灌入初始资料（应用延迟与漏报设定）。"""
    months = panel.meta["months"]
    delays = {(d["region"], d["month"]) for d in panel.raw.get("settlement_delays", [])}

    settlement_by_month: dict[str, list[SettlementRecord]] = {m: [] for m in months}
    delayed: list[SettlementRecord] = []
    for record in _settlement_records(panel):
        if (record.region, record.month) in delays:
            delayed.append(record)
        else:
            settlement_by_month[record.month].append(record)

    price_by_month: dict[str, list[PriceRecord]] = {m: [] for m in months}
    for drug_id, series in panel.raw.get("price", {}).items():
        for index, price in enumerate(series):
            price_by_month[months[index]].append(
                PriceRecord(month=months[index], drug_id=drug_id, region=None, unit_price_yuan=price)
            )

    supply_by_month: dict[str, list[SupplyRecord]] = {m: [] for m in months}
    for region, drugs in panel.raw.get("supply", {}).items():
        for drug_id, series in drugs.items():
            for index, rate in enumerate(series):
                supply_by_month[months[index]].append(
                    SupplyRecord(month=months[index], region=region, drug_id=drug_id, fulfillment_rate=rate)
                )

    visits_by_month: dict[str, list[VisitsRecord]] = {m: [] for m in months}
    for region, series in panel.raw.get("visits", {}).items():
        for index, count in enumerate(series):
            visits_by_month[months[index]].append(
                VisitsRecord(month=months[index], region=region, visit_count=count)
            )

    inspection_by_month: dict[str, list[InspectionRecord]] = {m: [] for m in months}
    for region, series in panel.raw.get("inspection", {}).items():
        for index, actions in enumerate(series["actions"]):
            inspection_by_month[months[index]].append(
                InspectionRecord(
                    month=months[index],
                    region=region,
                    action_count=actions,
                    recovered_yuan=series["recovered"][index],
                )
            )

    feedback_by_month: dict[str, list[FeedbackRecord]] = {m: [] for m in months}
    for region, categories in panel.raw.get("feedback", {}).items():
        for category, series in categories.items():
            for index, count in enumerate(series):
                feedback_by_month[months[index]].append(
                    FeedbackRecord(month=months[index], region=region, category=category, count=count)
                )

    for month in months:
        # 跨月回补：被延迟的结算记录随其后续月份批次到达
        if month == months[-1] and delayed:
            settlement_by_month[month] = settlement_by_month[month] + delayed
        for source, table in [
            (Source.SETTLEMENT, settlement_by_month),
            (Source.PRICE, price_by_month),
            (Source.SUPPLY, supply_by_month),
            (Source.VISITS, visits_by_month),
            (Source.INSPECTION, inspection_by_month),
            (Source.FEEDBACK, feedback_by_month),
        ]:
            records = table.get(month, [])
            if records:
                backend.ingest(source, records, note=f"{month} 月报")


def apply_backfill(backend: ExplanationBackend, panel: SamplePanel) -> IngestBatch:
    """灌入漏报补报记录（R4 的 2026-06 结算）。"""
    backfill = panel.raw["backfill_later"]
    records = [
        SettlementRecord(
            month=backfill["month"],
            region=backfill["region"],
            drug_id=drug_id,
            amount_yuan=values["amount"],
            patient_count=values["patients"],
            claim_count=values["claims"],
        )
        for drug_id, values in backfill["records"].items()
    ]
    return backend.ingest(
        Source.SETTLEMENT,
        records,
        note=f"{backfill['region']} {backfill['month']} 漏报补报",
    )
