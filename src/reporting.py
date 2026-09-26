"""决策报告：把一版结果整理为决策者可直接阅读的视图。

回答四个问题：

1. 下降发生在哪里（按地区排序，未覆盖地区单独列出）；
2. 哪些解释得到支持（按假设类型汇总已支持归因及其区间，
   还有多少下降未被解释）；
3. 是否伴随就医中断信号（取药受阻、供应受限、结算延迟）；
4. 某次口径或数据变化如何影响汇总判断（版本差异分解）。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.attribution import (
    Attribution,
    AttributionRegistry,
    AttributionStatus,
    ExplainedInterval,
    Hypothesis,
    combined_span,
)
from src.ingestion import DataStore
from src.signals import InterruptionSignal, SignalConfig, SignalKind, detect_signals
from src.versioning import ResultVersion, VersionDiff


def format_amount(yuan: int) -> str:
    """把金额格式化为"约 X 亿元/万元/元"。"""
    scale = abs(yuan)
    if scale >= 1e8:
        return f"约 {yuan / 1e8:.1f} 亿元"
    if scale >= 1e4:
        return f"约 {yuan / 1e4:.1f} 万元"
    return f"约 {yuan} 元"


@dataclass(frozen=True)
class ExplanationView:
    """某一假设类型下的归因汇总。"""

    hypothesis: Hypothesis
    supported: tuple[Attribution, ...]
    contested: tuple[Attribution, ...]
    proposed: tuple[Attribution, ...]
    combined_low_yuan: int  # 已支持归因的合并区间下界
    combined_high_yuan: int  # 已支持归因的合并区间上界


@dataclass(frozen=True)
class DecisionReport:
    """一版结果的决策者视图。"""

    version_id: str
    scope_id: str
    headline_text: str
    total_delta_yuan: int
    is_partial: bool
    missing_regions: tuple[str, ...]
    decline_by_region: tuple[tuple[str, int], ...]  # (地区, 异动额)，降幅大者在前
    explanations: tuple[ExplanationView, ...]
    explained: ExplainedInterval
    signals: tuple[InterruptionSignal, ...]
    cautions: tuple[str, ...]


def build_report(
    version: ResultVersion,
    registry: AttributionRegistry,
    store: DataStore,
    signal_config: SignalConfig | None = None,
) -> DecisionReport:
    """由版本、归因登记处与资料库构建决策报告。"""
    aggregate = version.aggregate
    scope = version.scope
    scope_id = scope.scope_id

    delta = aggregate.total_delta_yuan
    decline = max(0, -delta)
    if delta < 0:
        headline = f"重点监测药品支出下降{format_amount(-delta)}"
    else:
        headline = f"重点监测药品支出未下降（变动{format_amount(delta)}）"
    if aggregate.is_partial:
        headline += f"，未含 {len(aggregate.missing_regions)} 个漏报地区"

    decline_by_region = tuple(sorted(aggregate.by_region.items(), key=lambda kv: kv[1]))

    attributions = registry.for_scope(scope_id)
    explanations = []
    for hypothesis in Hypothesis:
        members = [a for a in attributions if a.hypothesis is hypothesis]
        if not members:
            continue
        supported = tuple(a for a in members if a.status is AttributionStatus.SUPPORTED)
        low, high = combined_span(list(supported)) if supported else (0, 0)
        explanations.append(
            ExplanationView(
                hypothesis=hypothesis,
                supported=supported,
                contested=tuple(a for a in members if a.status is AttributionStatus.CONTESTED),
                proposed=tuple(a for a in members if a.status is AttributionStatus.PROPOSED),
                combined_low_yuan=low,
                combined_high_yuan=high,
            )
        )

    explained = registry.explained_interval(scope_id, decline)
    signals = detect_signals(scope, store, signal_config, regions=aggregate.covered_regions)

    cautions = []
    if aggregate.is_partial:
        cautions.append(
            "结果未覆盖以下地区：" + "、".join(aggregate.missing_regions) + "；缺失地区未按零值计入"
        )
    if explained.over_claim:
        cautions.append("已支持归因的解释区间下界超过总压降，存在重复认领风险")
    if decline and explained.unexplained_high_yuan > 0:
        cautions.append(
            f"仍有 {format_amount(explained.unexplained_low_yuan)}"
            f" ~ {format_amount(explained.unexplained_high_yuan)} 下降未被已支持归因解释"
        )
    if any(s.kind is SignalKind.ACCESS_PRESSURE and s.severity == "high" for s in signals):
        cautions.append("存在疑似取药受阻信号：过度压降可能意味着真实患者取药受阻")
    if any(s.kind is SignalKind.SETTLEMENT_LAG for s in signals):
        cautions.append("存在跨月回补占比较高的月份，近期下降可能含结算时滞")

    return DecisionReport(
        version_id=version.version_id,
        scope_id=scope_id,
        headline_text=headline,
        total_delta_yuan=delta,
        is_partial=aggregate.is_partial,
        missing_regions=aggregate.missing_regions,
        decline_by_region=decline_by_region,
        explanations=tuple(explanations),
        explained=explained,
        signals=tuple(signals),
        cautions=tuple(cautions),
    )


def format_impact(diff: VersionDiff) -> str:
    """把版本差异格式化为"口径变化如何影响汇总判断"的说明。"""
    parts = [
        f"汇总判断由 {format_amount(abs(diff.old_delta_yuan))} 调整为"
        f" {format_amount(abs(diff.new_delta_yuan))}（变动 {format_amount(abs(diff.change_yuan))}）"
    ]
    effects = []
    if diff.coverage_effect_yuan:
        effects.append(f"覆盖效应 {format_amount(abs(diff.coverage_effect_yuan))}")
    if diff.catalog_effect_yuan:
        effects.append(f"目录效应 {format_amount(abs(diff.catalog_effect_yuan))}")
    if diff.revision_effect_yuan:
        effects.append(f"修订效应 {format_amount(abs(diff.revision_effect_yuan))}")
    if effects:
        parts.append("，其中" + "、".join(effects))
    details = []
    if diff.newly_covered:
        details.append("新覆盖地区：" + "、".join(diff.newly_covered))
    if diff.newly_missing:
        details.append("转为缺失地区：" + "、".join(diff.newly_missing))
    if diff.added_drugs:
        details.append(f"新增药品 {len(diff.added_drugs)} 种")
    if diff.removed_drugs:
        details.append(f"移出药品 {len(diff.removed_drugs)} 种")
    if details:
        parts.append("；" + "；".join(details))
    return "".join(parts)
