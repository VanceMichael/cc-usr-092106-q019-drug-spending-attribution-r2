"""领域模型：冻结比较口径与多源资料记录。

本模块定义支出异动解释后台的基础对象：

- :class:`FrozenScope` 冻结一次比较的全部口径（比较人群、药品目录、
  地区范围、基线与观察窗口）。口径任何变化都必须构造新对象，
  对象内容哈希即 ``scope_id``，保证"同一口径同一标识"。
- 六类资料记录：结算、价格、供应、诊疗量、核查行动、患者服务反馈。
  记录为不可变值对象，修订通过登记处（见 ``ingestion``）以新记录体现。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def check_month(value: str) -> str:
    """校验并返回 ``YYYY-MM`` 月份字符串。"""
    if not isinstance(value, str) or not _MONTH_RE.match(value):
        raise ValueError(f"月份格式应为 YYYY-MM：{value!r}")
    return value


def month_iter(start: str, end: str) -> list[str]:
    """返回从 ``start`` 到 ``end``（含两端）的月份序列。"""
    check_month(start)
    check_month(end)
    year, month = int(start[:4]), int(start[5:])
    end_year, end_month = int(end[:4]), int(end[5:])
    if (year, month) > (end_year, end_month):
        raise ValueError(f"窗口起点晚于终点：{start} > {end}")
    months = []
    while (year, month) <= (end_year, end_month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


class Source(str, Enum):
    """六类接入资料来源。"""

    SETTLEMENT = "settlement"  # 结算
    PRICE = "price"  # 价格
    SUPPLY = "supply"  # 供应
    VISITS = "visits"  # 诊疗量
    INSPECTION = "inspection"  # 核查行动
    FEEDBACK = "feedback"  # 患者服务反馈


def _check_non_negative(name: str, value: int | float) -> None:
    if value < 0:
        raise ValueError(f"{name} 不得为负：{value}")


@dataclass(frozen=True)
class SettlementRecord:
    """结算记录：某地区某月某药品的基金支出与服务量。"""

    month: str
    region: str
    drug_id: str
    amount_yuan: int
    patient_count: int
    claim_count: int

    def __post_init__(self) -> None:
        check_month(self.month)
        _check_non_negative("amount_yuan", self.amount_yuan)
        _check_non_negative("patient_count", self.patient_count)
        _check_non_negative("claim_count", self.claim_count)


@dataclass(frozen=True)
class PriceRecord:
    """价格记录：某月某药品的支付标准价；``region=None`` 表示全国统一价。"""

    month: str
    drug_id: str
    region: str | None
    unit_price_yuan: float

    def __post_init__(self) -> None:
        check_month(self.month)
        _check_non_negative("unit_price_yuan", self.unit_price_yuan)


@dataclass(frozen=True)
class SupplyRecord:
    """供应记录：某地区某月某药品的配送满足率（0~1）。"""

    month: str
    region: str
    drug_id: str
    fulfillment_rate: float

    def __post_init__(self) -> None:
        check_month(self.month)
        if not 0.0 <= self.fulfillment_rate <= 1.0:
            raise ValueError(f"fulfillment_rate 应位于 [0, 1]：{self.fulfillment_rate}")


@dataclass(frozen=True)
class VisitsRecord:
    """诊疗量记录：某地区某月相关诊疗人次（用于判断就医是否中断）。"""

    month: str
    region: str
    visit_count: int

    def __post_init__(self) -> None:
        check_month(self.month)
        _check_non_negative("visit_count", self.visit_count)


@dataclass(frozen=True)
class InspectionRecord:
    """核查行动记录：某地区某月针对重点药品的核查次数与追回金额。"""

    month: str
    region: str
    action_count: int
    recovered_yuan: int

    def __post_init__(self) -> None:
        check_month(self.month)
        _check_non_negative("action_count", self.action_count)
        _check_non_negative("recovered_yuan", self.recovered_yuan)


@dataclass(frozen=True)
class FeedbackRecord:
    """患者服务反馈：某地区某月某类诉求的件数（如 ``取药难``）。"""

    month: str
    region: str
    category: str
    count: int

    def __post_init__(self) -> None:
        check_month(self.month)
        if not self.category:
            raise ValueError("category 不得为空")
        _check_non_negative("count", self.count)


_RECORD_TYPES = {
    Source.SETTLEMENT: SettlementRecord,
    Source.PRICE: PriceRecord,
    Source.SUPPLY: SupplyRecord,
    Source.VISITS: VisitsRecord,
    Source.INSPECTION: InspectionRecord,
    Source.FEEDBACK: FeedbackRecord,
}


def record_type(source: Source) -> type:
    """返回某来源对应的记录类型。"""
    return _RECORD_TYPES[Source(source)]


def record_key(source: Source, record: object) -> tuple:
    """返回记录的业务主键；同键新记录视为对旧值的修订。"""
    source = Source(source)
    if source is Source.SETTLEMENT:
        return (record.month, record.region, record.drug_id)
    if source is Source.PRICE:
        return (record.month, record.drug_id, record.region)
    if source is Source.SUPPLY:
        return (record.month, record.region, record.drug_id)
    if source is Source.VISITS:
        return (record.month, record.region)
    if source is Source.INSPECTION:
        return (record.month, record.region)
    if source is Source.FEEDBACK:
        return (record.month, record.region, record.category)
    raise ValueError(f"未知来源：{source}")


@dataclass(frozen=True)
class FrozenScope:
    """一次支出比较的冻结口径。

    冻结内容：比较人群标识、药品目录版本及具体药品清单、地区范围、
    基线窗口与观察窗口。任何一项变化都必须构造新的 ``FrozenScope``，
    得到新的 ``scope_id``；不允许就地修改。
    """

    population: str  # 比较人群标识（指向外部治理的人群定义）
    catalog_version: str  # 药品目录口径版本
    drug_ids: tuple[str, ...]  # 冻结时的药品清单
    regions: tuple[str, ...]  # 地区范围
    baseline_start: str
    baseline_end: str
    observation_start: str
    observation_end: str
    scope_id: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "drug_ids", tuple(sorted(set(self.drug_ids))))
        object.__setattr__(self, "regions", tuple(sorted(set(self.regions))))
        if not self.population:
            raise ValueError("population 不得为空")
        if not self.catalog_version:
            raise ValueError("catalog_version 不得为空")
        if not self.drug_ids:
            raise ValueError("drug_ids 不得为空")
        if not self.regions:
            raise ValueError("regions 不得为空")
        # 校验两个窗口均为合法且有序的月份区间
        month_iter(self.baseline_start, self.baseline_end)
        month_iter(self.observation_start, self.observation_end)
        canonical = json.dumps(
            {
                "population": self.population,
                "catalog_version": self.catalog_version,
                "drug_ids": list(self.drug_ids),
                "regions": list(self.regions),
                "baseline": [self.baseline_start, self.baseline_end],
                "observation": [self.observation_start, self.observation_end],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
        object.__setattr__(self, "scope_id", f"scope-{digest}")

    @property
    def baseline_months(self) -> list[str]:
        """基线窗口月份序列。"""
        return month_iter(self.baseline_start, self.baseline_end)

    @property
    def observation_months(self) -> list[str]:
        """观察窗口月份序列。"""
        return month_iter(self.observation_start, self.observation_end)
