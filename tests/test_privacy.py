import unittest

from src.models import SettlementRecord
from src.privacy import PrivacyPolicy, build_public_export

POLICY = PrivacyPolicy(min_cell_patients=10, round_to_yuan=1_000_000)


def cell(region, drug, month, amount, patients):
    return SettlementRecord(month, region, drug, amount, patients, claim_count=patients)


class PrivacyTest(unittest.TestCase):
    def test_small_cell_suppressed(self):
        records = [
            cell("R1", "D01", "2026-04", 1000, 20),
            cell("R1", "D02", "2026-04", 500, 5),  # 患者数不足
            cell("R2", "D01", "2026-04", 800, 30),
        ]
        export = build_public_export(records, ("R1", "R2"), ("D01", "D02"), POLICY)
        self.assertIn(("R1", "D02"), export.suppressed)
        self.assertNotIn(("R1", "D02"), [(c.region, c.drug_id) for c in export.cells])

    def test_every_month_must_meet_threshold(self):
        records = [
            cell("R1", "D01", "2026-04", 1000, 50),
            cell("R1", "D01", "2026-05", 1000, 8),  # 有一个月低于阈值
            cell("R1", "D02", "2026-04", 900, 30),
            cell("R1", "D02", "2026-05", 900, 30),
        ]
        export = build_public_export(records, ("R1",), ("D01", "D02"), POLICY)
        self.assertIn(("R1", "D01"), export.suppressed)

    def test_complementary_suppression_when_single_cell_suppressed(self):
        records = [
            cell("R1", "D01", "2026-04", 1000, 12),  # 可放行中患者数最少 → 被追加抑制
            cell("R1", "D02", "2026-04", 2000, 40),
            cell("R1", "D03", "2026-04", 300, 5),  # 低于阈值
        ]
        export = build_public_export(records, ("R1",), ("D01", "D02", "D03"), POLICY)
        self.assertEqual(set(export.suppressed), {("R1", "D03"), ("R1", "D01")})
        self.assertEqual([(c.region, c.drug_id) for c in export.cells], [("R1", "D02")])

    def test_no_complementary_suppression_when_two_cells_suppressed(self):
        records = [
            cell("R1", "D01", "2026-04", 1000, 12),
            cell("R1", "D02", "2026-04", 300, 5),
            cell("R1", "D03", "2026-04", 300, 4),
        ]
        export = build_public_export(records, ("R1",), ("D01", "D02", "D03"), POLICY)
        self.assertEqual(set(export.suppressed), {("R1", "D02"), ("R1", "D03")})
        self.assertEqual(len(export.cells), 1)

    def test_region_total_omitted_when_region_has_suppression(self):
        records = [
            cell("R1", "D01", "2026-04", 1000, 20),
            cell("R1", "D02", "2026-04", 300, 5),
            cell("R1", "D03", "2026-04", 700, 30),  # 互补抑制对象
            cell("R2", "D01", "2026-04", 800, 30),
        ]
        export = build_public_export(records, ("R1", "R2"), ("D01", "D02", "D03"), POLICY)
        self.assertNotIn("R1", export.region_totals)
        self.assertEqual(export.region_totals["R2"], 800)

    def test_grand_total_rounded_and_includes_suppressed(self):
        records = [
            cell("R1", "D01", "2026-04", 1_234_567, 20),
            cell("R1", "D02", "2026-04", 345_678, 5),
            cell("R1", "D03", "2026-04", 700_000, 30),
        ]
        export = build_public_export(records, ("R1",), ("D01", "D02", "D03"), POLICY)
        # 总额含被抑制格，按百万元取整：2_280_245 → 2_000_000
        self.assertEqual(export.grand_total_yuan, 2_000_000)

    def test_policy_validation(self):
        with self.assertRaises(ValueError):
            PrivacyPolicy(min_cell_patients=0)
        with self.assertRaises(ValueError):
            PrivacyPolicy(round_to_yuan=0)


if __name__ == "__main__":
    unittest.main()
