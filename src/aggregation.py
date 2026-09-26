"""支出聚合：按冻结口径计算观察期相对基线的异动。

核心规则：

- 未覆盖（漏报）地区不参与合计，也不得按零值计入；
  聚合结果显式列出缺失地区并标记 ``is_partial``。
- 已报送但无记录的地区×药品是真零值，正常计入。
- 异动额以"地区×药品"单元格为基础：单元格异动 =
  观察期合计 − 基线合计 × 观察月数 / 基线月数（窗口等长时即两期之差），
  全部汇总口径（按地区、按药品、总计）都由单元格加总而来，
  保证版本差异可以精确分解。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.ingestion import DataStore
from src.models import FrozenScope, Source


@dataclass
class WindowAggregate:
    """一次冻结口径下的支出异动聚合结果。"""

    scope_id: str
    cells: dict[str, dict[str, int]]  # region -> drug_id -> 异动额（元，负值为下降）
    baseline_yuan: int  # 覆盖地区的基线支出合计
    observation_yuan: int  # 覆盖地区的观察期支出合计
    covered_regions: tuple[str, ...]
    missing_regions: tuple[str, ...]  # 未覆盖地区（漏报），未按零计入
    baseline_months: tuple[str, ...]
    observation_months: tuple[str, ...]

    @property
    def total_delta_yuan(self) -> int:
        """覆盖范围内的总异动额（负值为下降）。"""
        return sum(sum(drugs.values()) for drugs in self.cells.values())

    @property
    def is_partial(self) -> bool:
        """是否存在未覆盖地区。"""
        return bool(self.missing_regions)

    @property
    def by_region(self) -> dict[str, int]:
        """按地区汇总的异动额。"""
        return {region: sum(drugs.values()) for region, drugs in self.cells.items()}

    @property
    def by_drug(self) -> dict[str, int]:
        """按药品汇总的异动额。"""
        totals: dict[str, int] = {}
        for drugs in self.cells.values():
            for drug_id, delta in drugs.items():
                totals[drug_id] = totals.get(drug_id, 0) + delta
        return totals


def aggregate_window(scope: FrozenScope, store: DataStore) -> WindowAggregate:
    """按冻结口径聚合支出异动。

    地区在基线与观察窗口内每一个月都已报送结算资料，才视为覆盖；
    否则整体排除并列入 ``missing_regions``。
    """
    baseline_months = scope.baseline_months
    observation_months = scope.observation_months
    all_months = baseline_months + observation_months

    covered, missing = [], []
    for region in scope.regions:
        if all(store.is_reported(Source.SETTLEMENT, month, region) for month in all_months):
            covered.append(region)
        else:
            missing.append(region)

    base_sum: dict[tuple[str, str], int] = {}
    obs_sum: dict[tuple[str, str], int] = {}
    for record in store.latest(Source.SETTLEMENT):
        if record.region not in covered or record.drug_id not in scope.drug_ids:
            continue
        key = (record.region, record.drug_id)
        if record.month in baseline_months:
            base_sum[key] = base_sum.get(key, 0) + record.amount_yuan
        elif record.month in observation_months:
            obs_sum[key] = obs_sum.get(key, 0) + record.amount_yuan

    n_base = len(baseline_months)
    n_obs = len(observation_months)
    cells: dict[str, dict[str, int]] = {region: {} for region in covered}
    keys = set(base_sum) | set(obs_sum)
    for region, drug_id in sorted(keys):
        # 观察期异动 = 观察期合计 − 基线月均 × 观察月数
        delta = round(obs_sum.get((region, drug_id), 0) - base_sum.get((region, drug_id), 0) * n_obs / n_base)
        if delta:
            cells[region][drug_id] = delta

    return WindowAggregate(
        scope_id=scope.scope_id,
        cells=cells,
        baseline_yuan=sum(base_sum.values()),
        observation_yuan=sum(obs_sum.values()),
        covered_regions=tuple(covered),
        missing_regions=tuple(missing),
        baseline_months=tuple(baseline_months),
        observation_months=tuple(observation_months),
    )
