"""结果版本化：任何资料或口径变化只生成新版结果，历史版本可回溯。

触发新版本的原因（``RevisionCause``）：数据修订、跨月回补、
药品目录变化、地区漏报补报、其他口径变化。版本构成单向链，
``diff`` 把两版总异动额之差精确分解为覆盖效应、目录效应与
修订效应，用于回答"某次口径变化如何影响汇总判断"。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.aggregation import WindowAggregate
from src.models import FrozenScope


class RevisionCause(str, Enum):
    """生成新版本的原因。"""

    INITIAL = "initial"  # 首次发布
    DATA_REVISION = "data_revision"  # 数据修订
    CROSS_MONTH_BACKFILL = "cross_month_backfill"  # 跨月回补
    CATALOG_CHANGE = "catalog_change"  # 药品目录变化
    REGION_BACKFILL = "region_backfill"  # 地区漏报补报
    SCOPE_CHANGE = "scope_change"  # 其他口径变化（人群/窗口/地区范围）


@dataclass(frozen=True)
class ResultVersion:
    """一版冻结的汇总结果。"""

    version_id: str
    seq: int
    cause: RevisionCause
    scope: FrozenScope
    aggregate: WindowAggregate
    parent_id: str | None
    note: str = ""


@dataclass(frozen=True)
class VersionDiff:
    """两版结果的差异及其成因分解。

    三类效应之和恒等于 ``change_yuan``：

    - 覆盖效应：地区覆盖范围变化（漏报补报等）带来的差异；
    - 目录效应：药品目录增删带来的差异；
    - 修订效应：共同覆盖范围内数值变化（修订、跨月回补等）带来的差异。
    """

    old_id: str
    new_id: str
    old_delta_yuan: int
    new_delta_yuan: int
    change_yuan: int  # 新 − 旧
    coverage_effect_yuan: int
    catalog_effect_yuan: int
    revision_effect_yuan: int
    newly_covered: tuple[str, ...]
    newly_missing: tuple[str, ...]
    added_drugs: tuple[str, ...]
    removed_drugs: tuple[str, ...]


class VersionStore:
    """只增不改的版本链。"""

    def __init__(self) -> None:
        self._versions: list[ResultVersion] = []

    def publish(
        self,
        cause: RevisionCause,
        scope: FrozenScope,
        aggregate: WindowAggregate,
        note: str = "",
    ) -> ResultVersion:
        """发布新版本；父版本自动指向当前最新版。"""
        cause = RevisionCause(cause)
        if aggregate.scope_id != scope.scope_id:
            raise ValueError("聚合结果与冻结口径不一致")
        seq = len(self._versions) + 1
        version = ResultVersion(
            version_id=f"v{seq:04d}",
            seq=seq,
            cause=cause,
            scope=scope,
            aggregate=aggregate,
            parent_id=self._versions[-1].version_id if self._versions else None,
            note=note,
        )
        self._versions.append(version)
        return version

    def latest(self) -> ResultVersion | None:
        """返回最新版本；无版本时返回 None。"""
        return self._versions[-1] if self._versions else None

    def get(self, version_id: str) -> ResultVersion:
        """按标识读取历史版本。"""
        for version in self._versions:
            if version.version_id == version_id:
                return version
        raise KeyError(f"未知版本：{version_id}")

    def all(self) -> list[ResultVersion]:
        """返回全部版本（按发布顺序）。"""
        return list(self._versions)

    def diff(self, old_id: str, new_id: str) -> VersionDiff:
        """比较两版结果，精确分解差异成因。"""
        old = self.get(old_id)
        new = self.get(new_id)
        old_cells = old.aggregate.cells
        new_cells = new.aggregate.cells

        old_regions = set(old_cells)
        new_regions = set(new_cells)
        newly_covered = tuple(sorted(new_regions - old_regions))
        newly_missing = tuple(sorted(old_regions - new_regions))

        old_drugs = set(old.scope.drug_ids)
        new_drugs = set(new.scope.drug_ids)
        added_drugs = tuple(sorted(new_drugs - old_drugs))
        removed_drugs = tuple(sorted(old_drugs - new_drugs))
        common_drugs = old_drugs & new_drugs

        coverage_effect = 0
        for region in newly_covered:
            coverage_effect += sum(
                delta for drug, delta in new_cells[region].items() if drug in common_drugs
            )
        for region in newly_missing:
            coverage_effect -= sum(
                delta for drug, delta in old_cells[region].items() if drug in common_drugs
            )

        catalog_effect = 0
        for region in new_regions:
            catalog_effect += sum(
                delta for drug, delta in new_cells[region].items() if drug in added_drugs
            )
        for region in old_regions:
            catalog_effect -= sum(
                delta for drug, delta in old_cells[region].items() if drug in removed_drugs
            )

        revision_effect = 0
        for region in old_regions & new_regions:
            old_row = old_cells[region]
            new_row = new_cells[region]
            for drug in common_drugs:
                revision_effect += new_row.get(drug, 0) - old_row.get(drug, 0)

        change = new.aggregate.total_delta_yuan - old.aggregate.total_delta_yuan
        return VersionDiff(
            old_id=old_id,
            new_id=new_id,
            old_delta_yuan=old.aggregate.total_delta_yuan,
            new_delta_yuan=new.aggregate.total_delta_yuan,
            change_yuan=change,
            coverage_effect_yuan=coverage_effect,
            catalog_effect_yuan=catalog_effect,
            revision_effect_yuan=revision_effect,
            newly_covered=newly_covered,
            newly_missing=newly_missing,
            added_drugs=added_drugs,
            removed_drugs=removed_drugs,
        )
