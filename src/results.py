"""只增不改的版本化结果。

数据修订、跨月回补、药品目录变化与地区漏报补报都只能生成新版本，
历史版本永远可查；未覆盖的地区-期间单元只标记为缺失，绝不按零值
计入汇总。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.privacy import DEFAULT_MIN_PATIENTS, suppress_aggregate, suppress_change
from src.snapshot import AnalysisSnapshot
from src.sources import (
    METRIC_AMOUNT,
    Coverage,
    SourceKind,
    check_scope,
    coverage,
)


class RevisionReason(Enum):
    INITIAL = "initial"  # 首次发布
    DATA_REVISION = "data_revision"  # 数据修订
    BACKFILL = "backfill"  # 跨月回补
    CATALOG_CHANGE = "catalog_change"  # 药品目录变化
    UNDERREPORT = "underreport"  # 地区漏报补报


class VersionError(ValueError):
    """违反结果版本规则。"""


@dataclass(frozen=True)
class Aggregate:
    """某一分组下的聚合数值；missing_cells 记录未覆盖单元数。"""

    group: tuple[tuple[str, str], ...]
    metric: str
    value: float | None
    patient_count: int | None
    suppressed: bool = False
    missing_cells: int = 0

    @property
    def partial(self) -> bool:
        return self.missing_cells > 0


@dataclass(frozen=True)
class Change:
    """相对基线的可比变化。

    只在基线与窗口均已覆盖的地区上计算；覆盖不全时 partial 为 True，
    无任何可比地区时 delta 为 None，绝不把未覆盖区域当作零值。
    """

    group: tuple[tuple[str, str], ...]
    baseline: float | None
    current: float | None
    delta: float | None
    patient_count: int | None
    suppressed: bool = False
    partial: bool = False


@dataclass(frozen=True)
class ResultVersion:
    """一版计算结果，发布后不可变。"""

    result_id: str
    version: int
    snapshot_id: str
    reason: RevisionReason
    supersedes: int | None
    aggregates: tuple[Aggregate, ...]
    changes: tuple[Change, ...]
    coverage: Coverage
    baseline_coverage: Coverage
    created_at: str


def compute_result(
    snapshot: AnalysisSnapshot,
    records,
    *,
    result_id: str,
    version: int,
    reason: RevisionReason,
    supersedes: int | None = None,
    created_at: str,
    min_patients: int = DEFAULT_MIN_PATIENTS,
) -> ResultVersion:
    """在冻结口径下计算聚合与变化；缺失单元只标记、不补零。"""
    records = check_scope(snapshot, records)
    settlement = tuple(
        r for r in records if r.source is SourceKind.SETTLEMENT and r.metric == METRIC_AMOUNT
    )
    window_periods = snapshot.window.periods
    baseline_periods = snapshot.window.baseline_periods()
    cov = coverage(snapshot, settlement, window_periods)
    baseline_cov = coverage(snapshot, settlement, baseline_periods)

    current = [r for r in settlement if r.period in window_periods]
    baseline = [r for r in settlement if r.period in baseline_periods]

    regions = sorted(snapshot.region_scope.region_codes)
    group_defs = [()]
    group_defs += [(("region", region),) for region in regions]
    group_defs += [(("drug_id", drug),) for drug in sorted(snapshot.drug_scope.drug_ids)]

    # 基线与窗口均已完整覆盖的地区，变化只在可比口径内计算
    missing_cells = cov.missing | baseline_cov.missing
    complete_regions = {
        region
        for region in regions
        if not any(cell_region == region for cell_region, _ in missing_cells)
    }

    def in_group(record, group) -> bool:
        return all(getattr(record, key) == value for key, value in group)

    def group_regions(group) -> set[str]:
        keys = dict(group)
        return {keys["region"]} if "region" in keys else set(regions)

    aggregates = []
    changes = []
    for group in group_defs:
        cur = [r for r in current if in_group(r, group)]
        base = [r for r in baseline if in_group(r, group)]
        value = sum(r.value for r in cur)
        counts = [r.patient_count for r in cur if r.patient_count is not None]
        patient_count = sum(counts) if counts else None

        # 药品维度不预设必须逐期逐地区上报，缺失单元只在地区与总计层面统计
        if "drug_id" in dict(group):
            missing = 0
            base_missing = 0
            comparable_cur = cur
            comparable_base = base
        else:
            group_region_set = group_regions(group)
            missing = sum(
                1 for region, period in cov.missing if region in group_region_set
            )
            base_missing = sum(
                1 for region, period in baseline_cov.missing if region in group_region_set
            )
            usable = group_region_set & complete_regions
            comparable_cur = [r for r in cur if r.region in usable]
            comparable_base = [r for r in base if r.region in usable]

        aggregates.append(
            suppress_aggregate(
                Aggregate(
                    group=group,
                    metric=METRIC_AMOUNT,
                    value=value,
                    patient_count=patient_count,
                    missing_cells=missing,
                ),
                min_patients,
            )
        )

        # 可比地区无任何基线或窗口数据时，变化记为未知而非零
        base_value = sum(r.value for r in comparable_base) if comparable_base else None
        current_value = sum(r.value for r in comparable_cur) if comparable_cur else None
        delta = (
            (current_value - base_value)
            if base_value is not None and current_value is not None
            else None
        )
        changes.append(
            suppress_change(
                Change(
                    group=group,
                    baseline=base_value,
                    current=current_value,
                    delta=delta,
                    patient_count=patient_count,
                    partial=(missing > 0 or base_missing > 0),
                ),
                min_patients,
            )
        )

    return ResultVersion(
        result_id=result_id,
        version=version,
        snapshot_id=snapshot.snapshot_id,
        reason=reason,
        supersedes=supersedes,
        aggregates=tuple(aggregates),
        changes=tuple(changes),
        coverage=cov,
        baseline_coverage=baseline_cov,
        created_at=created_at,
    )


class ResultStore:
    """结果库：只增不改，历史版本永远可查。"""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, int], ResultVersion] = {}

    def publish(self, result: ResultVersion) -> ResultVersion:
        key = (result.result_id, result.version)
        if key in self._versions:
            raise VersionError(f"结果版本已存在，只能生成新版本：{key}")
        if result.version == 1:
            if result.reason is not RevisionReason.INITIAL or result.supersedes is not None:
                raise VersionError("首个版本必须为 INITIAL 且无前置版本")
        else:
            previous = self._versions.get((result.result_id, result.version - 1))
            if previous is None:
                raise VersionError("缺少前一版本，不能跳号发布")
            if result.reason is RevisionReason.INITIAL:
                raise VersionError("修订版本不能使用 INITIAL 原因")
            if result.supersedes != result.version - 1:
                raise VersionError("supersedes 必须指向前一版本")
        self._versions[key] = result
        return result

    def get(self, result_id: str, version: int) -> ResultVersion:
        try:
            return self._versions[(result_id, version)]
        except KeyError:
            raise VersionError(f"结果版本不存在：{result_id} v{version}") from None

    def latest(self, result_id: str) -> ResultVersion:
        versions = [v for rid, v in self._versions if rid == result_id]
        if not versions:
            raise VersionError(f"结果不存在：{result_id}")
        return self._versions[(result_id, max(versions))]

    def history(self, result_id: str) -> tuple[ResultVersion, ...]:
        return tuple(
            self._versions[(result_id, v)]
            for v in sorted(v for rid, v in self._versions if rid == result_id)
        )
