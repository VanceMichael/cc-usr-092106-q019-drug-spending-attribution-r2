"""竞争性归因登记：分析师提交彼此竞争的解释，附证据与不确定性。

设计要点：

- 归因以"解释的压降金额区间"表达不确定性（``effect_low_yuan`` ~
  ``effect_high_yuan``，非负表示解释了多少下降；允许负值表示
  主张存在反向增加）。
- 每条归因必须附至少一条证据；证据可以是聚合结果、资料批次或外部材料。
- 同一现象的竞争性解释通过 ``competition_group`` 声明互斥：
  汇总"已解释区间"时，同组只取并集跨度（min 下界 ~ max 上界），
  不同组之间才可加总，避免把竞争观点重复计入成效。
- 状态流转（提出 → 支持 / 质疑 → 撤回）全程留痕。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Hypothesis(str, Enum):
    """可归因的假设类型。"""

    ANTI_FRAUD = "anti_fraud"  # 反欺诈核查成效
    PRICE_CUT = "price_cut"  # 价格调整
    SUPPLY_SHORTAGE = "supply_shortage"  # 供应短缺
    PATIENT_MIX = "patient_mix"  # 患者结构变化
    SUBSTITUTION = "substitution"  # 治疗替代
    SETTLEMENT_DELAY = "settlement_delay"  # 结算延迟
    ACCESS_BARRIER = "access_barrier"  # 真实患者取药受阻（风险信号，非成效）
    OTHER = "other"


class AttributionStatus(str, Enum):
    """归因生命周期状态。"""

    PROPOSED = "proposed"  # 已提出
    SUPPORTED = "supported"  # 证据支持
    CONTESTED = "contested"  # 受到质疑
    WITHDRAWN = "withdrawn"  # 已撤回


@dataclass(frozen=True)
class Evidence:
    """一条归因证据。"""

    kind: str  # "aggregate"（聚合结果）| "dataset"（资料批次）| "note"（外部材料）
    ref: str  # 指向版本号、批次号或材料标识
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"aggregate", "dataset", "note"}:
            raise ValueError(f"未知证据类型：{self.kind!r}")
        if not self.ref:
            raise ValueError("证据必须给出引用")


@dataclass(frozen=True)
class StatusChange:
    """一次状态流转记录。"""

    status: AttributionStatus
    note: str


@dataclass(frozen=True)
class Attribution:
    """一条归因主张。"""

    attribution_id: str
    scope_id: str
    author: str
    hypothesis: Hypothesis
    effect_low_yuan: int  # 解释的压降金额下界（负值表示主张反向增加）
    effect_high_yuan: int  # 解释的压降金额上界
    rationale: str
    evidence: tuple[Evidence, ...]
    competition_group: str | None  # 同组归因彼此竞争，不可加总
    status: AttributionStatus
    history: tuple[StatusChange, ...]


@dataclass(frozen=True)
class ExplainedInterval:
    """已解释与未解释区间的汇总。"""

    total_decline_yuan: int  # 观察到的总压降（非负）
    explained_low_yuan: int
    explained_high_yuan: int
    unexplained_low_yuan: int
    unexplained_high_yuan: int
    over_claim: bool  # 已解释下界超过总压降，存在过度认领
    groups: tuple[tuple[str, int, int], ...]  # (组名, 下界, 上界)


def combined_span(attributions: list[Attribution]) -> tuple[int, int]:
    """合并一组归因的解释区间。

    同一竞争组内取并集跨度（min 下界 ~ max 上界），
    不同竞争组之间加总，避免把竞争观点重复计入。
    """
    groups: dict[str, list[Attribution]] = {}
    for attribution in attributions:
        group_key = attribution.competition_group or attribution.attribution_id
        groups.setdefault(group_key, []).append(attribution)
    low = sum(min(a.effect_low_yuan for a in members) for members in groups.values())
    high = sum(max(a.effect_high_yuan for a in members) for members in groups.values())
    return low, high


class AttributionRegistry:
    """归因登记处：提交、流转、汇总。"""

    def __init__(self) -> None:
        self._items: dict[str, Attribution] = {}
        self._counter = 0

    def submit(
        self,
        scope_id: str,
        author: str,
        hypothesis: Hypothesis,
        effect_low_yuan: int,
        effect_high_yuan: int,
        rationale: str,
        evidence: list[Evidence],
        competition_group: str | None = None,
    ) -> Attribution:
        """提交一条归因主张（初始状态为"已提出"）。"""
        hypothesis = Hypothesis(hypothesis)
        if effect_low_yuan > effect_high_yuan:
            raise ValueError("effect_low_yuan 不得大于 effect_high_yuan")
        if not author:
            raise ValueError("author 不得为空")
        if not rationale:
            raise ValueError("必须给出归因理由")
        evidence = tuple(evidence)
        if not evidence:
            raise ValueError("归因必须附至少一条证据")
        self._counter += 1
        attribution = Attribution(
            attribution_id=f"attr-{self._counter:04d}",
            scope_id=scope_id,
            author=author,
            hypothesis=hypothesis,
            effect_low_yuan=effect_low_yuan,
            effect_high_yuan=effect_high_yuan,
            rationale=rationale,
            evidence=evidence,
            competition_group=competition_group,
            status=AttributionStatus.PROPOSED,
            history=(StatusChange(AttributionStatus.PROPOSED, "提交"),),
        )
        self._items[attribution.attribution_id] = attribution
        return attribution

    def set_status(self, attribution_id: str, status: AttributionStatus, note: str = "") -> Attribution:
        """流转归因状态并留痕，返回更新后的归因。"""
        status = AttributionStatus(status)
        current = self._items[attribution_id]
        updated = Attribution(
            **{
                **current.__dict__,
                "status": status,
                "history": current.history + (StatusChange(status, note),),
            }
        )
        self._items[attribution_id] = updated
        return updated

    def get(self, attribution_id: str) -> Attribution:
        """按标识读取归因。"""
        return self._items[attribution_id]

    def for_scope(self, scope_id: str, statuses: set[AttributionStatus] | None = None) -> list[Attribution]:
        """列出某冻结口径下的归因，可按状态过滤。"""
        items = [a for a in self._items.values() if a.scope_id == scope_id]
        if statuses is not None:
            items = [a for a in items if a.status in statuses]
        return sorted(items, key=lambda a: a.attribution_id)

    def explained_interval(self, scope_id: str, total_decline_yuan: int) -> ExplainedInterval:
        """汇总"已支持"归因的解释区间。

        同一竞争组内取并集跨度，不同组之间加总；
        未解释区间 = 总压降 − 已解释区间（区间运算）。
        """
        supported = self.for_scope(scope_id, {AttributionStatus.SUPPORTED})
        groups: dict[str, list[Attribution]] = {}
        for attribution in supported:
            group_key = attribution.competition_group or attribution.attribution_id
            groups.setdefault(group_key, []).append(attribution)
        spans = []
        for group_key, members in sorted(groups.items()):
            low = min(a.effect_low_yuan for a in members)
            high = max(a.effect_high_yuan for a in members)
            spans.append((group_key, low, high))
        explained_low, explained_high = combined_span(supported)
        return ExplainedInterval(
            total_decline_yuan=total_decline_yuan,
            explained_low_yuan=explained_low,
            explained_high_yuan=explained_high,
            unexplained_low_yuan=total_decline_yuan - explained_high,
            unexplained_high_yuan=total_decline_yuan - explained_low,
            over_claim=explained_low > total_decline_yuan,
            groups=tuple(spans),
        )
