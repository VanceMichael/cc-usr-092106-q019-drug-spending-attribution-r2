import unittest
from pathlib import Path

from src.attribution import AttributionStatus, Evidence, Hypothesis
from src.backend import ExplanationBackend
from src.sample_data import apply_backfill, ingest_initial, load_sample_panel, make_scope
from src.signals import SignalKind
from src.versioning import RevisionCause

PANEL_PATH = Path("fixtures/sample_panel.json")


class BackendEndToEndTest(unittest.TestCase):
    """用示例面板走完整流程：首版 → 漏报补报 → 目录变化 → 报告与公开导出。

    版本链与归因在 setUpClass 中按序构建，各测试方法只读取产物，
    互不依赖执行顺序。
    """

    @classmethod
    def setUpClass(cls):
        cls.panel = load_sample_panel(PANEL_PATH)
        cls.backend = ExplanationBackend()
        cls.scope = make_scope(cls.panel)
        cls.scope_id = cls.backend.register_scope(cls.scope)
        ingest_initial(cls.backend, cls.panel)

        # 首版：R4 的 2026-06 结算漏报
        cls.v1 = cls.backend.publish(cls.scope_id, RevisionCause.INITIAL, note="首版")

        # 竞争性归因：反欺诈、价格各自独立，供应短缺给出两种竞争估计
        submissions = [
            ("分析师甲", Hypothesis.ANTI_FRAUD, 4_000_000, 5_500_000,
             "核查行动与下降同步", [Evidence("aggregate", cls.v1.version_id)], None),
            ("分析师乙", Hypothesis.PRICE_CUT, 1_500_000, 2_000_000,
             "D03 观察期价格下调 40%，量稳价降", [Evidence("dataset", "batch-price")], None),
            ("分析师丙", Hypothesis.SUPPLY_SHORTAGE, 300_000, 500_000,
             "D05 满足率走低（保守估计）", [Evidence("dataset", "batch-supply")], "supply-effect"),
            ("分析师丁", Hypothesis.SUPPLY_SHORTAGE, 600_000, 900_000,
             "D05 满足率走低（充分估计）", [Evidence("dataset", "batch-supply")], "supply-effect"),
        ]
        for author, hypothesis, low, high, rationale, evidence, group in submissions:
            attribution = cls.backend.submit_attribution(
                cls.scope_id, author, hypothesis, low, high, rationale, evidence,
                competition_group=group,
            )
            cls.backend.set_attribution_status(
                attribution.attribution_id, AttributionStatus.SUPPORTED
            )

        # 第二版：R4 漏报补报（同口径，新数据）
        apply_backfill(cls.backend, cls.panel)
        cls.v2 = cls.backend.publish(cls.scope_id, RevisionCause.REGION_BACKFILL, note="R4 补报")

        # 第三版：目录调出 D06（新口径）
        cls.narrower = make_scope(
            cls.panel,
            drug_ids=tuple(d for d in cls.panel.meta["drug_ids"] if d != "D06"),
            catalog_version="catalog-320-2026Q2",
        )
        cls.narrower_id = cls.backend.register_scope(cls.narrower)
        cls.v3 = cls.backend.publish(cls.narrower_id, RevisionCause.CATALOG_CHANGE, note="目录调出 D06")

        cls.report_v1 = cls.backend.report(cls.v1.version_id)
        cls.report_v2 = cls.backend.report(cls.v2.version_id)

    def test_v1_excludes_missing_region_without_zeroing(self):
        aggregate = self.v1.aggregate
        self.assertTrue(aggregate.is_partial)
        self.assertEqual(aggregate.missing_regions, ("R4",))
        self.assertEqual(aggregate.covered_regions, ("R1", "R2", "R3"))
        self.assertEqual(aggregate.total_delta_yuan, -9_480_000)
        self.assertEqual(aggregate.by_region["R1"], -4_420_000)
        self.assertEqual(aggregate.by_region["R2"], -3_850_000)
        self.assertEqual(aggregate.by_region["R3"], -1_210_000)

    def test_report_shows_where_decline_happened(self):
        report = self.report_v1
        self.assertIn("下降", report.headline_text)
        self.assertIn("漏报", report.headline_text)
        self.assertEqual(report.decline_by_region[0], ("R1", -4_420_000))
        self.assertEqual(report.missing_regions, ("R4",))
        self.assertTrue(any("R4" in caution for caution in report.cautions))

    def test_report_surfaces_interruption_signals(self):
        by_kind = {}
        for signal in self.report_v1.signals:
            by_kind.setdefault(signal.kind, []).append(signal)
        pressure = by_kind[SignalKind.ACCESS_PRESSURE]
        high = [s for s in pressure if s.severity == "high"]
        self.assertEqual([s.region for s in high], ["R2"])  # 量降价稳、取药难反馈升
        self.assertIn("R1", [s.region for s in pressure])  # 量降价稳但反馈未升
        supply = by_kind[SignalKind.SUPPLY_CONSTRAINT]
        self.assertEqual({s.region for s in supply}, {"R1", "R2"})  # D05 供应短缺
        lag = by_kind[SignalKind.SETTLEMENT_LAG]
        self.assertEqual([s.region for s in lag], ["R3"])  # 2026-05 随 06 批次到达
        self.assertTrue(any("取药受阻" in caution for caution in self.report_v1.cautions))

    def test_competing_attributions_and_explained_interval(self):
        report = self.report_v1
        # 竞争组取并集跨度 [30万, 90万]，再与反欺诈、价格加总
        self.assertEqual(report.explained.explained_low_yuan, 5_800_000)
        self.assertEqual(report.explained.explained_high_yuan, 8_400_000)
        self.assertFalse(report.explained.over_claim)
        supply_view = next(
            v for v in report.explanations if v.hypothesis is Hypothesis.SUPPLY_SHORTAGE
        )
        self.assertEqual(len(supply_view.supported), 2)  # 两种竞争估计并存
        self.assertTrue(any("未被已支持归因解释" in caution for caution in report.cautions))

    def test_region_backfill_creates_new_version_with_coverage_effect(self):
        self.assertEqual(self.v2.parent_id, self.v1.version_id)
        self.assertEqual(self.v2.aggregate.missing_regions, ())
        self.assertEqual(self.v2.aggregate.total_delta_yuan, -9_739_000)

        diff, text = self.backend.impact(self.v1.version_id, self.v2.version_id)
        self.assertEqual(diff.change_yuan, -259_000)
        self.assertEqual(diff.coverage_effect_yuan, -259_000)
        self.assertEqual(diff.revision_effect_yuan, 0)
        self.assertIn("覆盖效应", text)

    def test_catalog_change_creates_new_version_with_catalog_effect(self):
        self.assertNotEqual(self.narrower_id, self.scope_id)  # 口径变化必有新标识
        diff, text = self.backend.impact(self.v2.version_id, self.v3.version_id)
        self.assertEqual(diff.removed_drugs, ("D06",))
        self.assertEqual(diff.catalog_effect_yuan, 408_000)  # 移出药品原为负异动
        self.assertEqual(diff.change_yuan, 408_000)
        self.assertEqual(self.v3.aggregate.total_delta_yuan, -9_331_000)
        self.assertIn("目录效应", text)
        self.assertIn("汇总判断由", text)

    def test_public_export_enforces_privacy_thresholds(self):
        export = self.backend.public_export(self.v2.version_id)
        # R4 的 D06 患者数不足被抑制；R4 仅剩一个抑制格，D01 被互补抑制
        self.assertIn(("R4", "D06"), export.suppressed)
        self.assertIn(("R4", "D01"), export.suppressed)
        self.assertNotIn("R4", export.region_totals)
        self.assertEqual(set(export.region_totals), {"R1", "R2", "R3"})
        self.assertEqual(export.grand_total_yuan, 17_000_000)  # 取整，含被抑制格
        for cell in export.cells:
            self.assertGreaterEqual(cell.min_monthly_patients, export.policy.min_cell_patients)

    def test_submit_attribution_requires_registered_scope(self):
        with self.assertRaises(KeyError):
            self.backend.submit_attribution(
                "scope-unknown", "分析师戊", Hypothesis.OTHER, 0, 1,
                "无口径", [Evidence("note", "x")],
            )


if __name__ == "__main__":
    unittest.main()
