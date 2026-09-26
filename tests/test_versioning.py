import unittest

from src.aggregation import aggregate_window
from src.ingestion import DataStore
from src.models import FrozenScope, SettlementRecord, Source
from src.versioning import RevisionCause, VersionStore


def make_scope(regions=("R1", "R2"), drugs=("D01",), catalog="cat-v1"):
    return FrozenScope(
        population="测试人群",
        catalog_version=catalog,
        drug_ids=drugs,
        regions=regions,
        baseline_start="2026-01",
        baseline_end="2026-02",
        observation_start="2026-03",
        observation_end="2026-04",
    )


def settle(store, records, note=""):
    return store.ingest(Source.SETTLEMENT, records, note=note)


def r1_rows(amount_obs=(60, 60)):
    return [
        SettlementRecord("2026-01", "R1", "D01", 100, 20, 30),
        SettlementRecord("2026-02", "R1", "D01", 100, 20, 30),
        SettlementRecord("2026-03", "R1", "D01", amount_obs[0], 15, 20),
        SettlementRecord("2026-04", "R1", "D01", amount_obs[1], 15, 20),
    ]


def r2_rows():
    return [
        SettlementRecord("2026-01", "R2", "D01", 50, 10, 10),
        SettlementRecord("2026-02", "R2", "D01", 50, 10, 10),
        SettlementRecord("2026-03", "R2", "D01", 30, 8, 8),
        SettlementRecord("2026-04", "R2", "D01", 30, 8, 8),
    ]


class VersionChainTest(unittest.TestCase):
    def test_versions_form_append_only_chain(self):
        store = DataStore()
        settle(store, r1_rows())
        versions = VersionStore()
        scope = make_scope(regions=("R1",))
        first = versions.publish(RevisionCause.INITIAL, scope, aggregate_window(scope, store))
        second = versions.publish(
            RevisionCause.DATA_REVISION, scope, aggregate_window(scope, store), note="修订"
        )
        self.assertEqual((first.version_id, second.version_id), ("v0001", "v0002"))
        self.assertIsNone(first.parent_id)
        self.assertEqual(second.parent_id, "v0001")
        self.assertEqual(versions.latest().version_id, "v0002")
        self.assertEqual(len(versions.all()), 2)

    def test_publish_rejects_mismatched_aggregate(self):
        versions = VersionStore()
        store = DataStore()
        scope = make_scope(regions=("R1",))
        other = make_scope(regions=("R1",), catalog="cat-v2")
        with self.assertRaises(ValueError):
            versions.publish(RevisionCause.INITIAL, scope, aggregate_window(other, store))

    def test_unknown_version_raises(self):
        versions = VersionStore()
        with self.assertRaises(KeyError):
            versions.get("v9999")


class VersionDiffTest(unittest.TestCase):
    def setUp(self):
        self.store = DataStore()
        self.versions = VersionStore()
        self.scope = make_scope()

    def publish(self, cause, scope=None, note=""):
        scope = scope or self.scope
        return self.versions.publish(cause, scope, aggregate_window(scope, self.store), note=note)

    def test_region_backfill_is_coverage_effect(self):
        settle(self.store, r1_rows())  # R2 漏报
        first = self.publish(RevisionCause.INITIAL)
        self.assertEqual(first.aggregate.missing_regions, ("R2",))
        self.assertEqual(first.aggregate.total_delta_yuan, -80)

        settle(self.store, r2_rows(), note="R2 补报")
        second = self.publish(RevisionCause.REGION_BACKFILL, note="R2 补报")
        self.assertEqual(second.aggregate.total_delta_yuan, -120)

        diff = self.versions.diff(first.version_id, second.version_id)
        self.assertEqual(diff.change_yuan, -40)
        self.assertEqual(diff.coverage_effect_yuan, -40)
        self.assertEqual(diff.catalog_effect_yuan, 0)
        self.assertEqual(diff.revision_effect_yuan, 0)
        self.assertEqual(diff.newly_covered, ("R2",))
        self.assertEqual(
            diff.coverage_effect_yuan + diff.catalog_effect_yuan + diff.revision_effect_yuan,
            diff.change_yuan,
        )

    def test_data_revision_is_revision_effect(self):
        settle(self.store, r1_rows())
        settle(self.store, r2_rows())
        first = self.publish(RevisionCause.INITIAL)

        # 数据修订：R1 观察期费用更正为更低值
        settle(self.store, [
            SettlementRecord("2026-03", "R1", "D01", 50, 15, 20),
            SettlementRecord("2026-04", "R1", "D01", 50, 15, 20),
        ], note="数据修订")
        second = self.publish(RevisionCause.DATA_REVISION)

        diff = self.versions.diff(first.version_id, second.version_id)
        self.assertEqual(diff.change_yuan, -20)
        self.assertEqual(diff.revision_effect_yuan, -20)
        self.assertEqual(diff.coverage_effect_yuan, 0)
        self.assertEqual(diff.catalog_effect_yuan, 0)

    def test_catalog_change_is_catalog_effect(self):
        settle(self.store, r1_rows())
        settle(self.store, r2_rows())
        first = self.publish(RevisionCause.INITIAL)

        # 目录扩围：新增 D02，其观察期相对基线下降 40（R1、R2 各 20）
        wider = make_scope(drugs=("D01", "D02"), catalog="cat-v2")
        settle(self.store, [
            SettlementRecord("2026-01", "R1", "D02", 40, 10, 10),
            SettlementRecord("2026-02", "R1", "D02", 40, 10, 10),
            SettlementRecord("2026-03", "R1", "D02", 30, 8, 8),
            SettlementRecord("2026-04", "R1", "D02", 30, 8, 8),
            SettlementRecord("2026-01", "R2", "D02", 20, 5, 5),
            SettlementRecord("2026-02", "R2", "D02", 20, 5, 5),
            SettlementRecord("2026-03", "R2", "D02", 10, 4, 4),
            SettlementRecord("2026-04", "R2", "D02", 10, 4, 4),
        ])
        second = self.publish(RevisionCause.CATALOG_CHANGE, scope=wider)

        diff = self.versions.diff(first.version_id, second.version_id)
        self.assertEqual(diff.added_drugs, ("D02",))
        self.assertEqual(diff.catalog_effect_yuan, -40)  # R1 -20 + R2 -20
        self.assertEqual(diff.coverage_effect_yuan, 0)
        self.assertEqual(diff.revision_effect_yuan, 0)
        self.assertEqual(diff.change_yuan, -40)

    def test_combined_changes_decompose_exactly(self):
        settle(self.store, r1_rows())
        first = self.publish(RevisionCause.INITIAL)  # R2 缺失

        # 同时发生：R2 补报（覆盖）、目录加 D02（目录）、R1 修订（修订）
        settle(self.store, r2_rows())
        settle(self.store, [
            SettlementRecord("2026-03", "R1", "D01", 50, 15, 20),
            SettlementRecord("2026-04", "R1", "D01", 50, 15, 20),
        ])
        settle(self.store, [
            SettlementRecord("2026-01", "R1", "D02", 40, 10, 10),
            SettlementRecord("2026-02", "R1", "D02", 40, 10, 10),
            SettlementRecord("2026-03", "R1", "D02", 30, 8, 8),
            SettlementRecord("2026-04", "R1", "D02", 30, 8, 8),
        ])
        wider = make_scope(drugs=("D01", "D02"), catalog="cat-v2")
        second = self.publish(RevisionCause.SCOPE_CHANGE, scope=wider)

        diff = self.versions.diff(first.version_id, second.version_id)
        self.assertEqual(diff.coverage_effect_yuan, -40)  # R2 D01
        self.assertEqual(diff.catalog_effect_yuan, -20)  # R1 D02
        self.assertEqual(diff.revision_effect_yuan, -20)  # R1 D01 修订
        self.assertEqual(
            diff.change_yuan,
            diff.coverage_effect_yuan + diff.catalog_effect_yuan + diff.revision_effect_yuan,
        )


if __name__ == "__main__":
    unittest.main()
