from pathlib import Path

from src.attribution import (
    AttributionCategory,
    Evidence,
    Hypothesis,
    Strength,
    Support,
    Uncertainty,
    assess,
    assess_all,
    load_hypotheses,
)
from src.sources import SourceKind


def _hypothesis(*, evidence=(), uncertainty=Uncertainty.LOW, hid="h1"):
    return Hypothesis(
        hypothesis_id=hid,
        category=AttributionCategory.ANTI_FRAUD,
        statement="测试归因",
        author="analyst",
        submitted_at="2026-07-01T00:00:00Z",
        uncertainty=uncertainty,
        evidence=tuple(evidence),
    )


def _evidence(strength):
    return Evidence(
        source=SourceKind.INSPECTION,
        summary="证据",
        strength=strength,
    )


def test_assess_support_levels():
    assert assess(_hypothesis(evidence=[_evidence(Strength.STRONG)])) is Support.SUPPORTED
    # 强证据但不确定性高 → 降级为有一定依据
    assert (
        assess(
            _hypothesis(
                evidence=[_evidence(Strength.STRONG)], uncertainty=Uncertainty.HIGH
            )
        )
        is Support.PLAUSIBLE
    )
    assert (
        assess(_hypothesis(evidence=[_evidence(Strength.MODERATE)]))
        is Support.PLAUSIBLE
    )
    assert (
        assess(_hypothesis(evidence=[_evidence(Strength.WEAK)]))
        is Support.INSUFFICIENT
    )
    assert assess(_hypothesis()) is Support.INSUFFICIENT


def test_competing_hypotheses_coexist():
    hypotheses = (
        _hypothesis(hid="h-anti-fraud", evidence=[_evidence(Strength.STRONG)]),
        _hypothesis(hid="h-price", evidence=[_evidence(Strength.MODERATE)]),
        _hypothesis(hid="h-supply", evidence=[_evidence(Strength.WEAK)]),
    )
    assessed = dict((h.hypothesis_id, s) for h, s in assess_all(hypotheses))
    assert assessed == {
        "h-anti-fraud": Support.SUPPORTED,
        "h-price": Support.PLAUSIBLE,
        "h-supply": Support.INSUFFICIENT,
    }


def test_load_hypotheses_fixture():
    hypotheses = load_hypotheses(Path("fixtures/hypotheses.json"))
    assert len(hypotheses) == 3
    by_id = {h.hypothesis_id: h for h in hypotheses}
    assert by_id["hyp-anti-fraud"].category is AttributionCategory.ANTI_FRAUD
    assert by_id["hyp-price-cut"].uncertainty is Uncertainty.MEDIUM
    assert by_id["hyp-supply"].evidence[0].source is SourceKind.SUPPLY
    # 与评估规则一致：反欺诈得到支持，供应短缺证据不足
    assert assess(by_id["hyp-anti-fraud"]) is Support.SUPPORTED
    assert assess(by_id["hyp-supply"]) is Support.INSUFFICIENT
