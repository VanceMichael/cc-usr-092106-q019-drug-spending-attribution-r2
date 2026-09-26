"""就医中断信号：支出下降若伴随诊疗量下滑与患者反馈上升，需警惕真实患者取药受阻。"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from src.snapshot import AnalysisSnapshot
from src.sources import (
    METRIC_COMPLAINTS,
    METRIC_STOCK_RATIO,
    METRIC_VISITS,
    SourceKind,
)

DEFAULT_DROP_THRESHOLD = 0.3  # 诊疗量降幅阈值
DEFAULT_RISE_THRESHOLD = 0.2  # 患者反馈增幅阈值
SUPPLY_CONFOUND_THRESHOLD = 0.2  # 供应库存降幅达到该值时提示混杂因素


@dataclass(frozen=True)
class InterruptionSignal:
    """一次就医中断信号。"""

    region: str
    drug_id: str | None
    visit_drop: float
    feedback_rise: float
    severity: str
    note: str


def _mean(values) -> float | None:
    values = list(values)
    return mean(values) if values else None


def detect_interruptions(
    records,
    snapshot: AnalysisSnapshot,
    *,
    drop_threshold: float = DEFAULT_DROP_THRESHOLD,
    rise_threshold: float = DEFAULT_RISE_THRESHOLD,
) -> tuple[InterruptionSignal, ...]:
    """按（地区, 药品）比较窗口与基线：诊疗量下降且患者反馈上升时生成信号。"""
    baseline_periods = set(snapshot.window.baseline_periods())
    window_periods = set(snapshot.window.periods)

    def pick(source: SourceKind, metric: str) -> dict:
        out: dict[tuple[str, str | None], dict[str, list]] = {}
        for r in records:
            if r.source is not source or r.metric != metric:
                continue
            if r.period in baseline_periods:
                bucket = "baseline"
            elif r.period in window_periods:
                bucket = "window"
            else:
                continue
            out.setdefault((r.region, r.drug_id), {"baseline": [], "window": []})[
                bucket
            ].append(r.value)
        return out

    visits = pick(SourceKind.TREATMENT, METRIC_VISITS)
    feedback = pick(SourceKind.PATIENT_FEEDBACK, METRIC_COMPLAINTS)
    supply = pick(SourceKind.SUPPLY, METRIC_STOCK_RATIO)

    signals = []
    for key, buckets in visits.items():
        base = _mean(buckets["baseline"])
        cur = _mean(buckets["window"])
        if not base or cur is None:
            continue
        drop = 1 - cur / base
        fb = feedback.get(key)
        if not fb:
            continue
        fb_base = _mean(fb["baseline"])
        fb_cur = _mean(fb["window"])
        if not fb_base or fb_cur is None:
            continue
        rise = fb_cur / fb_base - 1
        if drop < drop_threshold or rise < rise_threshold:
            continue

        supply_drop = None
        sp = supply.get(key)
        if sp:
            sp_base = _mean(sp["baseline"])
            sp_cur = _mean(sp["window"])
            if sp_base and sp_cur is not None:
                supply_drop = 1 - sp_cur / sp_base
        if supply_drop is not None and supply_drop >= SUPPLY_CONFOUND_THRESHOLD:
            note = "同期供应库存明显下降，需先区分供应短缺与取药受阻"
        else:
            note = "诊疗量下降且患者反馈上升，提示真实患者可能取药受阻"

        signals.append(
            InterruptionSignal(
                region=key[0],
                drug_id=key[1],
                visit_drop=round(drop, 4),
                feedback_rise=round(rise, 4),
                severity="high" if drop >= 0.5 else "medium",
                note=note,
            )
        )
    return tuple(sorted(signals, key=lambda s: (s.region, s.drug_id or "")))
