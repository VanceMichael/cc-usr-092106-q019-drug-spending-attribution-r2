"""就医中断与数据质量信号：识别"下降"背后的风险形态。

三类信号：

- 取药受阻（ACCESS_PRESSURE）：结算服务量明显下降，而相关诊疗量
  基本稳定、取药难反馈上升——提示过度压降可能挡住了真实患者；
- 供应受限（SUPPLY_CONSTRAINT）：配送满足率低于下限，提示下降
  可能由供应短缺而非需求变化造成；
- 结算延迟（SETTLEMENT_LAG）：某月费用中跨月回补占比过高，
  提示近期"下降"可能只是结算时滞，不宜直接解读。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.ingestion import DataStore
from src.models import FrozenScope, Source


class SignalKind(str, Enum):
    """信号类型。"""

    ACCESS_PRESSURE = "access_pressure"  # 疑似取药受阻
    SUPPLY_CONSTRAINT = "supply_constraint"  # 供应受限
    SETTLEMENT_LAG = "settlement_lag"  # 结算延迟


@dataclass(frozen=True)
class SignalConfig:
    """信号判定阈值。"""

    volume_drop_ratio: float = 0.2  # 结算人次降幅阈值
    visits_drop_tolerance: float = 0.05  # 诊疗量"基本稳定"的容差
    complaint_rise_ratio: float = 0.3  # 取药难反馈升幅阈值
    fulfillment_floor: float = 0.8  # 配送满足率下限
    backfill_share_warn: float = 0.15  # 跨月回补金额占比预警线
    complaint_category: str = "取药难"  # 取药受阻类反馈类目


@dataclass(frozen=True)
class InterruptionSignal:
    """一条信号。"""

    kind: SignalKind
    region: str
    severity: str  # "high" | "medium" | "low"
    detail: str


_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _monthly_avg(values: dict[str, float], months: list[str]) -> float | None:
    """窗口内月均；窗口内无任何记录时返回 None。"""
    present = [values[m] for m in months if m in values]
    if not present:
        return None
    return sum(present) / len(months)


def _detect_access_pressure(
    scope: FrozenScope,
    store: DataStore,
    config: SignalConfig,
    regions: list[str],
) -> list[InterruptionSignal]:
    claims: dict[str, dict[str, float]] = {r: {} for r in regions}
    for record in store.latest(Source.SETTLEMENT):
        if record.region in claims and record.drug_id in scope.drug_ids:
            row = claims[record.region]
            row[record.month] = row.get(record.month, 0) + record.claim_count
    visits: dict[str, dict[str, float]] = {r: {} for r in regions}
    for record in store.latest(Source.VISITS):
        if record.region in visits:
            visits[record.region][record.month] = record.visit_count
    complaints: dict[str, dict[str, float]] = {r: {} for r in regions}
    for record in store.latest(Source.FEEDBACK):
        if record.region in complaints and record.category == config.complaint_category:
            row = complaints[record.region]
            row[record.month] = row.get(record.month, 0) + record.count

    signals = []
    for region in regions:
        base_claims = _monthly_avg(claims[region], scope.baseline_months)
        obs_claims = _monthly_avg(claims[region], scope.observation_months)
        base_visits = _monthly_avg(visits[region], scope.baseline_months)
        obs_visits = _monthly_avg(visits[region], scope.observation_months)
        if not base_claims or obs_claims is None or not base_visits or obs_visits is None:
            continue
        volume_drop = 1 - obs_claims / base_claims
        if volume_drop < config.volume_drop_ratio:
            continue
        visits_stable = obs_visits >= base_visits * (1 - config.visits_drop_tolerance)
        if not visits_stable:
            continue
        base_complaints = _monthly_avg(complaints[region], scope.baseline_months) or 0.0
        obs_complaints = _monthly_avg(complaints[region], scope.observation_months) or 0.0
        complaints_up = obs_complaints >= base_complaints * (1 + config.complaint_rise_ratio) and (
            obs_complaints > base_complaints
        )
        if complaints_up:
            signals.append(
                InterruptionSignal(
                    kind=SignalKind.ACCESS_PRESSURE,
                    region=region,
                    severity="high",
                    detail=(
                        f"结算人次下降 {volume_drop:.0%}，诊疗量基本稳定，"
                        f"取药难反馈由月均 {base_complaints:.0f} 件升至 {obs_complaints:.0f} 件"
                    ),
                )
            )
        else:
            signals.append(
                InterruptionSignal(
                    kind=SignalKind.ACCESS_PRESSURE,
                    region=region,
                    severity="medium",
                    detail=f"结算人次下降 {volume_drop:.0%}，诊疗量基本稳定，需核查取药渠道",
                )
            )
    return signals


def _detect_supply_constraint(
    scope: FrozenScope,
    store: DataStore,
    config: SignalConfig,
    regions: list[str],
) -> list[InterruptionSignal]:
    region_set = set(regions)
    observation = set(scope.observation_months)
    short: dict[str, list[str]] = {}
    for record in store.latest(Source.SUPPLY):
        if (
            record.region in region_set
            and record.drug_id in scope.drug_ids
            and record.month in observation
            and record.fulfillment_rate < config.fulfillment_floor
        ):
            drugs = short.setdefault(record.region, [])
            if record.drug_id not in drugs:
                drugs.append(record.drug_id)
    return [
        InterruptionSignal(
            kind=SignalKind.SUPPLY_CONSTRAINT,
            region=region,
            severity="medium",
            detail=f"观察期配送满足率低于 {config.fulfillment_floor:.0%}：{', '.join(sorted(drugs))}",
        )
        for region, drugs in sorted(short.items())
    ]


def _detect_settlement_lag(
    scope: FrozenScope,
    store: DataStore,
    config: SignalConfig,
    regions: list[str],
) -> list[InterruptionSignal]:
    region_set = set(regions)
    observation = scope.observation_months
    by_region: dict[str, list[tuple[int, object]]] = {}
    for seq, record in store.latest_with_seq(Source.SETTLEMENT):
        if record.region in region_set:
            by_region.setdefault(record.region, []).append((seq, record))

    signals = []
    for region in sorted(by_region):
        entries = by_region[region]
        first_seq_by_month: dict[str, int] = {}
        for seq, record in entries:
            if record.month not in first_seq_by_month or seq < first_seq_by_month[record.month]:
                first_seq_by_month[record.month] = seq
        # 只评估"已有更晚月份到达"的观察月：最新月尚无法判断是否被回补
        candidates = [
            m for m in observation if any(later > m for later in first_seq_by_month)
        ]
        if not candidates:
            continue
        month = max(candidates)
        later_min = min(seq for m, seq in first_seq_by_month.items() if m > month)
        total = 0
        backfilled = 0
        for seq, record in entries:
            if record.month != month:
                continue
            total += record.amount_yuan
            if seq >= later_min:  # 不早于更晚月份到达，属跨月回补
                backfilled += record.amount_yuan
        if total and backfilled / total >= config.backfill_share_warn:
            signals.append(
                InterruptionSignal(
                    kind=SignalKind.SETTLEMENT_LAG,
                    region=region,
                    severity="low",
                    detail=f"{month} 结算额中 {backfilled / total:.0%} 为跨月回补，近期下降解读需谨慎",
                )
            )
    return signals


def detect_signals(
    scope: FrozenScope,
    store: DataStore,
    config: SignalConfig | None = None,
    regions: list[str] | tuple[str, ...] | None = None,
) -> list[InterruptionSignal]:
    """对指定地区（缺省为口径内全部地区）检测全部信号。"""
    config = config or SignalConfig()
    region_list = list(regions) if regions is not None else list(scope.regions)
    signals = (
        _detect_access_pressure(scope, store, config, region_list)
        + _detect_supply_constraint(scope, store, config, region_list)
        + _detect_settlement_lag(scope, store, config, region_list)
    )
    return sorted(signals, key=lambda s: (_SEVERITY_ORDER[s.severity], s.region, s.kind.value))
