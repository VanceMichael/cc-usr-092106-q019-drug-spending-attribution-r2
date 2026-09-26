"""结算、价格、供应、诊疗量、核查行动与患者服务反馈六类数据接入。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.snapshot import AnalysisSnapshot, check_period


class SourceKind(Enum):
    SETTLEMENT = "settlement"  # 结算
    PRICE = "price"  # 价格
    SUPPLY = "supply"  # 供应
    TREATMENT = "treatment_volume"  # 诊疗量
    INSPECTION = "inspection"  # 核查行动
    PATIENT_FEEDBACK = "patient_feedback"  # 患者服务反馈


# 常用指标名约定
METRIC_AMOUNT = "amount_yuan"  # 结算金额（元）
METRIC_UNIT_PRICE = "unit_price_yuan"  # 单价（元）
METRIC_STOCK_RATIO = "stock_ratio"  # 供应库存比
METRIC_VISITS = "visits"  # 诊疗人次
METRIC_CASES = "cases"  # 核查立案数
METRIC_COMPLAINTS = "complaints"  # 患者服务反馈数


class ScopeError(ValueError):
    """数据落在冻结口径之外。"""


@dataclass(frozen=True)
class DataRecord:
    """一条接入数据；revision 标识数据供应方的修订次数。"""

    record_id: str
    source: SourceKind
    region: str
    period: str
    metric: str
    value: float
    drug_id: str | None = None
    patient_count: int | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        check_period(self.period)
        if self.patient_count is not None and self.patient_count < 0:
            raise ValueError("患者人数不能为负")


@dataclass(frozen=True)
class Coverage:
    """口径内（地区 × 期间）单元的覆盖情况；未覆盖不等于零。"""

    expected: frozenset[tuple[str, str]]
    reported: frozenset[tuple[str, str]]

    @property
    def missing(self) -> frozenset[tuple[str, str]]:
        return self.expected - self.reported

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def ratio(self) -> float:
        if not self.expected:
            return 1.0
        return len(self.expected & self.reported) / len(self.expected)


def expected_cells(snapshot: AnalysisSnapshot, periods) -> frozenset[tuple[str, str]]:
    return frozenset(
        (region, period)
        for region in snapshot.region_scope.region_codes
        for period in periods
    )


def check_scope(snapshot: AnalysisSnapshot, records) -> tuple[DataRecord, ...]:
    """校验全部记录落在冻结口径内，否则抛出 ScopeError。"""
    records = tuple(records)
    offenders = [r.record_id for r in records if not snapshot.admits(r)]
    if offenders:
        raise ScopeError(f"存在口径外数据记录：{offenders}")
    return records


def coverage(snapshot: AnalysisSnapshot, records, periods) -> Coverage:
    """统计指定期间范围内（地区 × 期间）单元的上报覆盖情况。"""
    cells = frozenset((r.region, r.period) for r in records)
    return Coverage(expected=expected_cells(snapshot, periods), reported=cells)
