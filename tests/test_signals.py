import unittest

from src.ingestion import DataStore
from src.models import (
    FeedbackRecord,
    FrozenScope,
    SettlementRecord,
    Source,
    SupplyRecord,
    VisitsRecord,
)
from src.signals import SignalKind, detect_signals

MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
BASELINE = MONTHS[:3]
OBSERVATION = MONTHS[3:]


def make_scope(regions=("R1",)):
    return FrozenScope(
        population="测试人群",
        catalog_version="cat-v1",
        drug_ids=("D01",),
        regions=regions,
        baseline_start=BASELINE[0],
        baseline_end=BASELINE[-1],
        observation_start=OBSERVATION[0],
        observation_end=OBSERVATION[-1],
    )


def fill_region(store, region, claims, visits=None, complaints=None, fulfillment=None):
    """按月份逐批灌入一个地区的资料（claims 为六个月的人次序列）。"""
    for index, month in enumerate(MONTHS):
        store.ingest(Source.SETTLEMENT, [
            SettlementRecord(month, region, "D01", 1000, 20, claims[index])
        ])
        if visits is not None:
            store.ingest(Source.VISITS, [VisitsRecord(month, region, visits[index])])
        if complaints is not None:
            store.ingest(Source.FEEDBACK, [
                FeedbackRecord(month, region, "取药难", complaints[index])
            ])
        if fulfillment is not None:
            store.ingest(Source.SUPPLY, [
                SupplyRecord(month, region, "D01", fulfillment[index])
            ])


class AccessPressureTest(unittest.TestCase):
    def test_high_when_volume_down_visits_stable_complaints_up(self):
        store = DataStore()
        fill_region(
            store, "R1",
            claims=[1000, 1000, 1000, 600, 550, 500],
            visits=[5000] * 6,
            complaints=[10, 10, 10, 14, 15, 16],
        )
        signals = detect_signals(make_scope(), store)
        pressure = [s for s in signals if s.kind is SignalKind.ACCESS_PRESSURE]
        self.assertEqual(len(pressure), 1)
        self.assertEqual(pressure[0].severity, "high")
        self.assertEqual(pressure[0].region, "R1")

    def test_medium_when_complaints_flat(self):
        store = DataStore()
        fill_region(
            store, "R1",
            claims=[1000, 1000, 1000, 600, 550, 500],
            visits=[5000] * 6,
            complaints=[10, 10, 10, 10, 11, 10],
        )
        signals = detect_signals(make_scope(), store)
        pressure = [s for s in signals if s.kind is SignalKind.ACCESS_PRESSURE]
        self.assertEqual(len(pressure), 1)
        self.assertEqual(pressure[0].severity, "medium")

    def test_no_signal_when_visits_also_dropped(self):
        store = DataStore()
        fill_region(
            store, "R1",
            claims=[1000, 1000, 1000, 600, 550, 500],
            visits=[5000, 5000, 5000, 3800, 3600, 3500],  # 诊疗量同步下降
            complaints=[10, 10, 10, 14, 15, 16],
        )
        signals = detect_signals(make_scope(), store)
        self.assertFalse([s for s in signals if s.kind is SignalKind.ACCESS_PRESSURE])

    def test_no_signal_when_volume_stable(self):
        store = DataStore()
        fill_region(
            store, "R1",
            claims=[1000, 1000, 1000, 980, 990, 970],
            visits=[5000] * 6,
            complaints=[10, 10, 10, 14, 15, 16],
        )
        signals = detect_signals(make_scope(), store)
        self.assertFalse([s for s in signals if s.kind is SignalKind.ACCESS_PRESSURE])


class SupplyConstraintTest(unittest.TestCase):
    def test_low_fulfillment_flagged(self):
        store = DataStore()
        fill_region(
            store, "R1",
            claims=[1000] * 6,
            fulfillment=[1.0, 1.0, 1.0, 1.0, 0.5, 0.45],
        )
        signals = detect_signals(make_scope(), store)
        supply = [s for s in signals if s.kind is SignalKind.SUPPLY_CONSTRAINT]
        self.assertEqual(len(supply), 1)
        self.assertIn("D01", supply[0].detail)

    def test_adequate_fulfillment_not_flagged(self):
        store = DataStore()
        fill_region(store, "R1", claims=[1000] * 6, fulfillment=[0.95] * 6)
        signals = detect_signals(make_scope(), store)
        self.assertFalse([s for s in signals if s.kind is SignalKind.SUPPLY_CONSTRAINT])


class SettlementLagTest(unittest.TestCase):
    def test_cross_month_backfill_flagged(self):
        store = DataStore()
        scope = make_scope()
        # 01~04 月正常报送；05 月记录随 06 月批次到达（跨月回补）
        for month in MONTHS[:4]:
            store.ingest(Source.SETTLEMENT, [
                SettlementRecord(month, "R1", "D01", 1000, 20, 100)
            ])
        store.ingest(Source.SETTLEMENT, [
            SettlementRecord("2026-05", "R1", "D01", 900, 20, 90),
            SettlementRecord("2026-06", "R1", "D01", 800, 20, 80),
        ])
        signals = detect_signals(scope, store)
        lag = [s for s in signals if s.kind is SignalKind.SETTLEMENT_LAG]
        self.assertEqual(len(lag), 1)
        self.assertIn("2026-05", lag[0].detail)

    def test_normal_flow_not_flagged(self):
        store = DataStore()
        scope = make_scope()
        for month in MONTHS:
            store.ingest(Source.SETTLEMENT, [
                SettlementRecord(month, "R1", "D01", 1000, 20, 100)
            ])
        signals = detect_signals(scope, store)
        self.assertFalse([s for s in signals if s.kind is SignalKind.SETTLEMENT_LAG])


if __name__ == "__main__":
    unittest.main()
