import dataclasses
import unittest

from src.models import (
    FeedbackRecord,
    FrozenScope,
    SettlementRecord,
    Source,
    SupplyRecord,
    check_month,
    month_iter,
    record_key,
)


def make_scope(**overrides):
    params = {
        "population": "门慢特病患者队列-2026H1",
        "catalog_version": "catalog-320-2026Q1",
        "drug_ids": ("D02", "D01"),
        "regions": ("R2", "R1"),
        "baseline_start": "2026-01",
        "baseline_end": "2026-03",
        "observation_start": "2026-04",
        "observation_end": "2026-06",
    }
    params.update(overrides)
    return FrozenScope(**params)


class MonthTest(unittest.TestCase):
    def test_valid_month(self):
        self.assertEqual(check_month("2026-09"), "2026-09")

    def test_invalid_month_rejected(self):
        for bad in ["2026-13", "2026-1", "26-01", "2026/01", ""]:
            with self.assertRaises(ValueError):
                check_month(bad)

    def test_month_iter_inclusive(self):
        self.assertEqual(
            month_iter("2025-11", "2026-02"),
            ["2025-11", "2025-12", "2026-01", "2026-02"],
        )

    def test_month_iter_reversed_rejected(self):
        with self.assertRaises(ValueError):
            month_iter("2026-03", "2026-01")


class FrozenScopeTest(unittest.TestCase):
    def test_scope_id_stable_for_same_content(self):
        self.assertEqual(make_scope().scope_id, make_scope().scope_id)

    def test_scope_id_changes_with_content(self):
        base = make_scope()
        self.assertNotEqual(base.scope_id, make_scope(drug_ids=("D01", "D02", "D03")).scope_id)
        self.assertNotEqual(base.scope_id, make_scope(regions=("R1",)).scope_id)
        self.assertNotEqual(base.scope_id, make_scope(observation_end="2026-07").scope_id)
        self.assertNotEqual(base.scope_id, make_scope(catalog_version="catalog-320-2026Q2").scope_id)

    def test_scope_is_immutable(self):
        scope = make_scope()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            scope.regions = ("R9",)

    def test_lists_sorted_and_deduplicated(self):
        scope = make_scope(drug_ids=("D02", "D01", "D02"), regions=("R2", "R1", "R1"))
        self.assertEqual(scope.drug_ids, ("D01", "D02"))
        self.assertEqual(scope.regions, ("R1", "R2"))

    def test_empty_fields_rejected(self):
        with self.assertRaises(ValueError):
            make_scope(drug_ids=())
        with self.assertRaises(ValueError):
            make_scope(regions=())
        with self.assertRaises(ValueError):
            make_scope(population="")

    def test_windows_exposed(self):
        scope = make_scope()
        self.assertEqual(scope.baseline_months, ["2026-01", "2026-02", "2026-03"])
        self.assertEqual(scope.observation_months, ["2026-04", "2026-05", "2026-06"])


class RecordTest(unittest.TestCase):
    def test_negative_values_rejected(self):
        with self.assertRaises(ValueError):
            SettlementRecord("2026-01", "R1", "D01", -1, 0, 0)

    def test_fulfillment_rate_bounds(self):
        with self.assertRaises(ValueError):
            SupplyRecord("2026-01", "R1", "D01", 1.2)

    def test_feedback_category_required(self):
        with self.assertRaises(ValueError):
            FeedbackRecord("2026-01", "R1", "", 3)

    def test_record_key_identifies_correction_target(self):
        first = SettlementRecord("2026-01", "R1", "D01", 100, 5, 6)
        corrected = SettlementRecord("2026-01", "R1", "D01", 120, 5, 6)
        self.assertEqual(
            record_key(Source.SETTLEMENT, first),
            record_key(Source.SETTLEMENT, corrected),
        )


if __name__ == "__main__":
    unittest.main()
