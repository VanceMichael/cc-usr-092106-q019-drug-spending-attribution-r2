import unittest

from src.aggregation import aggregate_window
from src.ingestion import DataStore
from src.models import FrozenScope, SettlementRecord, Source


def make_scope(regions=("R1", "R2"), drugs=("D01",), baseline=("2026-01", "2026-02"),
               observation=("2026-03", "2026-04")):
    return FrozenScope(
        population="测试人群",
        catalog_version="cat-v1",
        drug_ids=drugs,
        regions=regions,
        baseline_start=baseline[0],
        baseline_end=baseline[1],
        observation_start=observation[0],
        observation_end=observation[1],
    )


def settle(store, records, note=""):
    return store.ingest(Source.SETTLEMENT, records, note=note)


class AggregationTest(unittest.TestCase):
    def test_delta_is_observation_minus_baseline(self):
        store = DataStore()
        settle(store, [
            SettlementRecord("2026-01", "R1", "D01", 100, 20, 30),
            SettlementRecord("2026-02", "R1", "D01", 100, 20, 30),
            SettlementRecord("2026-03", "R1", "D01", 60, 15, 20),
            SettlementRecord("2026-04", "R1", "D01", 40, 10, 10),
        ])
        settle(store, [
            SettlementRecord("2026-01", "R2", "D01", 50, 10, 10),
            SettlementRecord("2026-02", "R2", "D01", 50, 10, 10),
            SettlementRecord("2026-03", "R2", "D01", 50, 10, 10),
            SettlementRecord("2026-04", "R2", "D01", 50, 10, 10),
        ])
        result = aggregate_window(make_scope(), store)
        self.assertEqual(result.cells["R1"], {"D01": -100})
        self.assertEqual(result.cells["R2"], {})
        self.assertEqual(result.total_delta_yuan, -100)
        self.assertFalse(result.is_partial)

    def test_missing_region_excluded_not_zeroed(self):
        store = DataStore()
        settle(store, [
            SettlementRecord("2026-01", "R1", "D01", 100, 20, 30),
            SettlementRecord("2026-02", "R1", "D01", 100, 20, 30),
            SettlementRecord("2026-03", "R1", "D01", 60, 15, 20),
            SettlementRecord("2026-04", "R1", "D01", 60, 15, 20),
        ])
        # R2 仅报送部分月份：整体视为未覆盖
        settle(store, [SettlementRecord("2026-01", "R2", "D01", 999, 10, 10)])
        result = aggregate_window(make_scope(), store)
        self.assertEqual(result.covered_regions, ("R1",))
        self.assertEqual(result.missing_regions, ("R2",))
        self.assertTrue(result.is_partial)
        self.assertEqual(result.total_delta_yuan, -80)  # 不含 R2 的任何数值

    def test_reported_region_without_records_is_genuine_zero(self):
        store = DataStore()
        settle(store, [
            SettlementRecord("2026-01", "R1", "D01", 100, 20, 30),
            SettlementRecord("2026-02", "R1", "D01", 100, 20, 30),
            SettlementRecord("2026-03", "R1", "D01", 60, 15, 20),
            SettlementRecord("2026-04", "R1", "D01", 60, 15, 20),
        ])
        for month in ["2026-01", "2026-02", "2026-03", "2026-04"]:
            store.mark_reported(Source.SETTLEMENT, month, "R2")
        result = aggregate_window(make_scope(), store)
        self.assertEqual(result.covered_regions, ("R1", "R2"))
        self.assertEqual(result.missing_regions, ())
        self.assertEqual(result.cells["R2"], {})

    def test_unequal_windows_use_monthly_average(self):
        store = DataStore()
        settle(store, [SettlementRecord("2026-01", "R1", "D01", 100, 20, 30)])
        settle(store, [
            SettlementRecord("2026-03", "R1", "D01", 90, 15, 20),
            SettlementRecord("2026-04", "R1", "D01", 70, 10, 10),
        ])
        scope = make_scope(regions=("R1",), baseline=("2026-01", "2026-01"),
                           observation=("2026-03", "2026-04"))
        result = aggregate_window(scope, store)
        # 观察期合计 160 − 基线月均 100 × 2 个月 = -40
        self.assertEqual(result.cells["R1"], {"D01": -40})

    def test_totals_derive_from_cells(self):
        store = DataStore()
        settle(store, [
            SettlementRecord("2026-01", "R1", "D01", 100, 20, 30),
            SettlementRecord("2026-02", "R1", "D01", 100, 20, 30),
            SettlementRecord("2026-03", "R1", "D01", 50, 15, 20),
            SettlementRecord("2026-04", "R1", "D01", 50, 15, 20),
            SettlementRecord("2026-01", "R1", "D02", 80, 12, 12),
            SettlementRecord("2026-02", "R1", "D02", 80, 12, 12),
            SettlementRecord("2026-03", "R1", "D02", 40, 10, 10),
            SettlementRecord("2026-04", "R1", "D02", 40, 10, 10),
        ])
        scope = make_scope(regions=("R1",), drugs=("D01", "D02"))
        result = aggregate_window(scope, store)
        self.assertEqual(result.by_region, {"R1": -180})
        self.assertEqual(result.by_drug, {"D01": -100, "D02": -80})
        self.assertEqual(result.total_delta_yuan, -180)

    def test_records_outside_scope_ignored(self):
        store = DataStore()
        settle(store, [
            SettlementRecord("2026-01", "R1", "D01", 100, 20, 30),
            SettlementRecord("2026-02", "R1", "D01", 100, 20, 30),
            SettlementRecord("2026-03", "R1", "D01", 60, 15, 20),
            SettlementRecord("2026-04", "R1", "D01", 60, 15, 20),
            SettlementRecord("2026-03", "R1", "D99", 500, 15, 20),  # 目录外药品
            SettlementRecord("2026-03", "R9", "D01", 500, 15, 20),  # 范围外地区
        ])
        result = aggregate_window(make_scope(regions=("R1",)), store)
        self.assertEqual(result.total_delta_yuan, -80)


if __name__ == "__main__":
    unittest.main()
