"""分析师提交的竞争性归因：每条归因都标明证据与不确定性，可并存竞争。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from src.sources import SourceKind


class AttributionCategory(Enum):
    ANTI_FRAUD = "anti_fraud"  # 反欺诈核查成效
    PRICE_ADJUSTMENT = "price_adjustment"  # 价格调整
    SUPPLY_SHORTAGE = "supply_shortage"  # 供应短缺
    PATIENT_MIX = "patient_mix"  # 患者结构变化
    SUBSTITUTION = "substitution"  # 治疗替代
    SETTLEMENT_DELAY = "settlement_delay"  # 结算延迟
    ACCESS_BARRIER = "access_barrier"  # 过度压降导致取药受阻


class Strength(Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


class Uncertainty(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Support(Enum):
    SUPPORTED = "supported"  # 得到有力证据支持
    PLAUSIBLE = "plausible"  # 有一定依据但仍存疑
    INSUFFICIENT = "insufficient"  # 证据不足


@dataclass(frozen=True)
class Evidence:
    """一条证据：来源、摘要、强度与可追溯的数据记录标识。"""

    source: SourceKind
    summary: str
    strength: Strength
    record_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Hypothesis:
    """一条竞争性归因。"""

    hypothesis_id: str
    category: AttributionCategory
    statement: str
    author: str
    submitted_at: str
    uncertainty: Uncertainty
    evidence: tuple[Evidence, ...] = ()


def assess(hypothesis: Hypothesis) -> Support:
    """评估单条归因的支持度；不同归因可并存竞争，不强制互斥。"""
    strengths = {e.strength for e in hypothesis.evidence}
    if Strength.STRONG in strengths and hypothesis.uncertainty is not Uncertainty.HIGH:
        return Support.SUPPORTED
    if strengths & {Strength.STRONG, Strength.MODERATE}:
        return Support.PLAUSIBLE
    return Support.INSUFFICIENT


def assess_all(hypotheses) -> tuple[tuple[Hypothesis, Support], ...]:
    return tuple((h, assess(h)) for h in hypotheses)


_CATEGORIES = {c.value: c for c in AttributionCategory}
_STRENGTHS = {s.value: s for s in Strength}
_UNCERTAINTIES = {u.value: u for u in Uncertainty}
_SOURCES = {s.value: s for s in SourceKind}


def load_hypotheses(path: Path) -> tuple[Hypothesis, ...]:
    """读取并校验归因文件。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("归因文件应为数组")
    hypotheses = []
    for item in raw:
        required = {
            "hypothesis_id",
            "category",
            "statement",
            "author",
            "submitted_at",
            "uncertainty",
        }
        missing = required - item.keys()
        if missing:
            raise ValueError(f"归因缺少字段：{sorted(missing)}")
        try:
            category = _CATEGORIES[item["category"]]
            uncertainty = _UNCERTAINTIES[item["uncertainty"]]
            evidence = tuple(
                Evidence(
                    source=_SOURCES[e["source"]],
                    summary=str(e["summary"]),
                    strength=_STRENGTHS[e["strength"]],
                    record_refs=tuple(map(str, e.get("record_refs", ()))),
                )
                for e in item.get("evidence", ())
            )
        except KeyError as exc:
            raise ValueError(f"归因 {item.get('hypothesis_id')} 含非法枚举值：{exc}") from exc
        hypotheses.append(
            Hypothesis(
                hypothesis_id=str(item["hypothesis_id"]),
                category=category,
                statement=str(item["statement"]),
                author=str(item["author"]),
                submitted_at=str(item["submitted_at"]),
                uncertainty=uncertainty,
                evidence=evidence,
            )
        )
    return tuple(hypotheses)
