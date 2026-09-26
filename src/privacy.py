"""隐私保护：公开材料的聚合阈值与抑制规则。

公开导出遵守以下规则，防止由明细反推个人：

- 单元格（地区×药品）在观察窗口内**每一个月**的患者数都达到
  ``min_cell_patients`` 才可放行；任一月低于阈值即抑制
  （取最严口径，避免小样本月暴露）。
- 互补抑制：某地区若恰好只有一个被抑制的单元格，则追加抑制
  该地区患者数最少的一个可放行格，防止"地区合计 − 其余格"
  反推出被抑制格。
- 含抑制格的地区不发布精确合计；全国总额按 ``round_to_yuan``
  取整后发布，避免精确总额参与差推。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.models import SettlementRecord


@dataclass(frozen=True)
class PrivacyPolicy:
    """公开导出的隐私策略。"""

    min_cell_patients: int = 10  # 单元格最小患者数
    round_to_yuan: int = 1_000_000  # 总额发布取整粒度

    def __post_init__(self) -> None:
        if self.min_cell_patients < 1:
            raise ValueError("min_cell_patients 至少为 1")
        if self.round_to_yuan < 1:
            raise ValueError("round_to_yuan 至少为 1")


@dataclass(frozen=True)
class PublicCell:
    """一个可公开的单元格。"""

    region: str
    drug_id: str
    amount_yuan: int
    min_monthly_patients: int  # 窗口内最低月患者数（放行依据）


@dataclass(frozen=True)
class PublicExport:
    """一份公开导出。"""

    cells: tuple[PublicCell, ...]  # 已放行的单元格
    suppressed: tuple[tuple[str, str], ...]  # 被抑制的 (地区, 药品)
    region_totals: dict[str, int]  # 仅无抑制地区的精确合计
    grand_total_yuan: int  # 取整后的全国总额（含被抑制格）
    policy: PrivacyPolicy


def build_public_export(
    records: list[SettlementRecord],
    regions: tuple[str, ...] | list[str],
    drug_ids: tuple[str, ...] | list[str],
    policy: PrivacyPolicy,
) -> PublicExport:
    """由观察期结算记录生成公开导出。

    ``records`` 应只包含观察窗口内的结算记录；函数按 (地区, 药品)
    汇总金额，并以窗口内最低月患者数判定是否放行。
    """
    regions = tuple(regions)
    drug_set = set(drug_ids)
    amounts: dict[tuple[str, str], int] = {}
    min_patients: dict[tuple[str, str], int] = {}
    for record in records:
        if record.region not in regions or record.drug_id not in drug_set:
            continue
        key = (record.region, record.drug_id)
        amounts[key] = amounts.get(key, 0) + record.amount_yuan
        previous = min_patients.get(key)
        if previous is None or record.patient_count < previous:
            min_patients[key] = record.patient_count

    suppressed: set[tuple[str, str]] = set()
    releasable: dict[tuple[str, str], PublicCell] = {}
    for key in sorted(amounts):
        region, drug_id = key
        patients = min_patients[key]
        if patients < policy.min_cell_patients:
            suppressed.add(key)
        else:
            releasable[key] = PublicCell(
                region=region,
                drug_id=drug_id,
                amount_yuan=amounts[key],
                min_monthly_patients=patients,
            )

    # 互补抑制：地区内恰好一个被抑制格时，追加抑制患者数最少的可放行格
    for region in regions:
        region_suppressed = [key for key in suppressed if key[0] == region]
        if len(region_suppressed) == 1:
            candidates = [cell for key, cell in releasable.items() if key[0] == region]
            if candidates:
                extra = min(candidates, key=lambda c: (c.min_monthly_patients, c.drug_id))
                suppressed.add((extra.region, extra.drug_id))
                del releasable[(extra.region, extra.drug_id)]

    region_totals: dict[str, int] = {}
    for region in regions:
        if any(key[0] == region for key in suppressed):
            continue  # 含抑制格的地区不发布精确合计
        total = sum(
            cell.amount_yuan for key, cell in releasable.items() if key[0] == region
        )
        if total or any(key[0] == region for key in releasable):
            region_totals[region] = total

    grand_total = sum(amounts.values())
    granularity = policy.round_to_yuan
    grand_total = round(grand_total / granularity) * granularity

    return PublicExport(
        cells=tuple(releasable[key] for key in sorted(releasable)),
        suppressed=tuple(sorted(suppressed)),
        region_totals=region_totals,
        grand_total_yuan=grand_total,
        policy=policy,
    )
