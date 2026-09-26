"""决策者视图：下降在哪里、哪些解释得到支持、是否有就医中断、口径变化影响几何。"""

from __future__ import annotations

from dataclasses import dataclass

from src.attribution import Hypothesis, Support, assess
from src.results import Change, ResultStore, ResultVersion
from src.signals import InterruptionSignal
from src.snapshot import DrugScope


@dataclass(frozen=True)
class ScopeChangeImpact:
    """口径变化对汇总判断的影响分解（元）。"""

    removed_effect: float  # 被移出口径药品的原支出
    added_effect: float  # 新纳入口径药品的支出
    common_change: float  # 口径不变部分的真实变化
    total_delta: float  # 汇总数字的总变化

    @property
    def scope_effect(self) -> float:
        """纯口径效应：并非实际支出变化。"""
        return self.added_effect - self.removed_effect


def _by_drug(result: ResultVersion) -> dict[str, float]:
    out = {}
    for agg in result.aggregates:
        keys = dict(agg.group)
        if set(keys) == {"drug_id"} and agg.value is not None:
            out[keys["drug_id"]] = agg.value
    return out


def _total(result: ResultVersion) -> float | None:
    for agg in result.aggregates:
        if not agg.group:
            return agg.value
    return None


def scope_change_impact(
    old: ResultVersion,
    new: ResultVersion,
    old_scope: DrugScope,
    new_scope: DrugScope,
) -> ScopeChangeImpact:
    """比较同一窗口下两个口径的结果，区分口径效应与真实变化。"""
    old_drugs = _by_drug(old)
    new_drugs = _by_drug(new)
    removed = old_scope.drug_ids - new_scope.drug_ids
    added = new_scope.drug_ids - old_scope.drug_ids
    common = old_scope.drug_ids & new_scope.drug_ids
    removed_effect = sum(old_drugs.get(d, 0.0) for d in removed)
    added_effect = sum(new_drugs.get(d, 0.0) for d in added)
    common_change = sum(new_drugs.get(d, 0.0) - old_drugs.get(d, 0.0) for d in common)
    old_total = _total(old)
    new_total = _total(new)
    if old_total is not None and new_total is not None:
        total_delta = new_total - old_total
    else:
        total_delta = common_change + added_effect - removed_effect
    return ScopeChangeImpact(
        removed_effect=removed_effect,
        added_effect=added_effect,
        common_change=common_change,
        total_delta=total_delta,
    )


@dataclass(frozen=True)
class DecisionReport:
    """面向决策者的汇总视图。"""

    result_id: str
    version: int
    snapshot_id: str
    total_change: Change | None
    decline_by_region: tuple[Change, ...]
    decline_by_drug: tuple[Change, ...]
    coverage_ratio: float
    missing_cells: tuple[tuple[str, str], ...]
    explanations: tuple[tuple[Hypothesis, Support], ...]
    interruption_signals: tuple[InterruptionSignal, ...]
    scope_impact: ScopeChangeImpact | None
    caveats: tuple[str, ...]


def _group_keys(change: Change) -> set[str]:
    return {key for key, _ in change.group}


def _by_delta(changes) -> tuple[Change, ...]:
    """按变化额升序排列（下降最多者在前），未知变化排在最后。"""
    return tuple(
        sorted(changes, key=lambda c: (c.delta is None, c.delta if c.delta is not None else 0.0))
    )


def build_report(
    store: ResultStore,
    result_id: str,
    *,
    hypotheses=(),
    signals=(),
    scope_impact: ScopeChangeImpact | None = None,
) -> DecisionReport:
    """汇总最新一版结果、竞争归因、就医中断信号与口径影响。"""
    result = store.latest(result_id)
    total_change = next((c for c in result.changes if not c.group), None)
    by_region = _by_delta(c for c in result.changes if _group_keys(c) == {"region"})
    by_drug = _by_delta(c for c in result.changes if _group_keys(c) == {"drug_id"})
    explanations = tuple(
        sorted(
            ((h, assess(h)) for h in hypotheses),
            key=lambda p: list(Support).index(p[1]),
        )
    )

    caveats = []
    if not result.coverage.complete:
        caveats.append(
            f"观察窗口覆盖率 {result.coverage.ratio:.0%}，"
            f"{len(result.coverage.missing)} 个地区-期间单元未上报，缺失不按零值计"
        )
    suppressed = sum(1 for a in result.aggregates if a.suppressed) + sum(
        1 for c in result.changes if c.suppressed
    )
    if suppressed:
        caveats.append(f"{suppressed} 个聚合单元未达最小人数阈值，已按规则隐藏")
    if signals:
        caveats.append("存在就医中断信号：监管成效不能替代患者用药可及性判断")
    if scope_impact is not None:
        caveats.append(
            f"口径变化带来 {scope_impact.scope_effect:,.0f} 元口径效应，不计入实际支出变化"
        )

    return DecisionReport(
        result_id=result.result_id,
        version=result.version,
        snapshot_id=result.snapshot_id,
        total_change=total_change,
        decline_by_region=by_region,
        decline_by_drug=by_drug,
        coverage_ratio=result.coverage.ratio,
        missing_cells=tuple(sorted(result.coverage.missing)),
        explanations=explanations,
        interruption_signals=tuple(signals),
        scope_impact=scope_impact,
        caveats=tuple(caveats),
    )
