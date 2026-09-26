import unittest

from src.attribution import (
    AttributionRegistry,
    AttributionStatus,
    Evidence,
    Hypothesis,
    combined_span,
)


def evidence(ref="v0001"):
    return [Evidence(kind="aggregate", ref=ref, note="汇总结果")]


class AttributionTest(unittest.TestCase):
    def setUp(self):
        self.registry = AttributionRegistry()

    def submit(self, **overrides):
        params = {
            "scope_id": "scope-a",
            "author": "分析师甲",
            "hypothesis": Hypothesis.ANTI_FRAUD,
            "effect_low_yuan": 100,
            "effect_high_yuan": 200,
            "rationale": "核查行动与下降同步",
            "evidence": evidence(),
        }
        params.update(overrides)
        return self.registry.submit(**params)

    def test_submit_requires_ordered_interval(self):
        with self.assertRaises(ValueError):
            self.submit(effect_low_yuan=300, effect_high_yuan=200)

    def test_submit_requires_evidence_and_rationale(self):
        with self.assertRaises(ValueError):
            self.submit(evidence=[])
        with self.assertRaises(ValueError):
            self.submit(rationale="")

    def test_evidence_requires_known_kind_and_ref(self):
        with self.assertRaises(ValueError):
            Evidence(kind="rumor", ref="x")
        with self.assertRaises(ValueError):
            Evidence(kind="note", ref="")

    def test_status_transitions_are_traced(self):
        attribution = self.submit()
        self.assertEqual(attribution.status, AttributionStatus.PROPOSED)
        updated = self.registry.set_status(
            attribution.attribution_id, AttributionStatus.SUPPORTED, "证据核验通过"
        )
        self.assertEqual(updated.status, AttributionStatus.SUPPORTED)
        self.assertEqual(len(updated.history), 2)
        self.assertEqual(updated.history[-1].note, "证据核验通过")

    def test_independent_supported_attributions_add_up(self):
        first = self.submit()
        second = self.submit(hypothesis=Hypothesis.PRICE_CUT, effect_low_yuan=50,
                             effect_high_yuan=80)
        for attribution in (first, second):
            self.registry.set_status(attribution.attribution_id, AttributionStatus.SUPPORTED)
        explained = self.registry.explained_interval("scope-a", total_decline_yuan=500)
        self.assertEqual((explained.explained_low_yuan, explained.explained_high_yuan), (150, 280))
        self.assertEqual((explained.unexplained_low_yuan, explained.unexplained_high_yuan), (220, 350))
        self.assertFalse(explained.over_claim)

    def test_competing_attributions_span_not_sum(self):
        first = self.submit(competition_group="fraud-effect")
        second = self.submit(competition_group="fraud-effect",
                             effect_low_yuan=150, effect_high_yuan=260)
        for attribution in (first, second):
            self.registry.set_status(attribution.attribution_id, AttributionStatus.SUPPORTED)
        explained = self.registry.explained_interval("scope-a", total_decline_yuan=500)
        # 同组竞争：取并集跨度 [100, 260]，而非加总 [250, 460]
        self.assertEqual((explained.explained_low_yuan, explained.explained_high_yuan), (100, 260))

    def test_over_claim_flagged(self):
        attribution = self.submit(effect_low_yuan=600, effect_high_yuan=700)
        self.registry.set_status(attribution.attribution_id, AttributionStatus.SUPPORTED)
        explained = self.registry.explained_interval("scope-a", total_decline_yuan=500)
        self.assertTrue(explained.over_claim)

    def test_only_supported_count_toward_explained(self):
        proposed = self.submit()
        contested = self.submit(hypothesis=Hypothesis.SUPPLY_SHORTAGE)
        self.registry.set_status(contested.attribution_id, AttributionStatus.CONTESTED)
        explained = self.registry.explained_interval("scope-a", total_decline_yuan=500)
        self.assertEqual((explained.explained_low_yuan, explained.explained_high_yuan), (0, 0))
        self.assertEqual(len(self.registry.for_scope("scope-a")), 2)
        self.assertEqual(
            len(self.registry.for_scope("scope-a", {AttributionStatus.PROPOSED})), 1
        )

    def test_combined_span_empty(self):
        self.assertEqual(combined_span([]), (0, 0))


if __name__ == "__main__":
    unittest.main()
