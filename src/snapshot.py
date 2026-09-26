"""冻结的分析口径：比较人群、药品口径、地区范围与观察窗口。

快照一旦创建即不可变；任何口径调整（如药品目录变化）都必须生成
新的快照，从而保证不同版本结果之间的可比性可以追查。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def check_period(value: str) -> str:
    """校验并返回 YYYY-MM 格式的期间字符串。"""
    if not isinstance(value, str) or not _PERIOD_RE.match(value):
        raise ValueError(f"期间须为 YYYY-MM 格式：{value!r}")
    return value


def iter_periods(start: str, end: str) -> tuple[str, ...]:
    """枚举起止之间（含两端）的全部月份。"""
    check_period(start)
    check_period(end)
    if start > end:
        raise ValueError("起点不能晚于终点")
    year, month = int(start[:4]), int(start[5:])
    periods = []
    while (year, month) <= (int(end[:4]), int(end[5:])):
        periods.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return tuple(periods)


def shift_period(period: str, months: int) -> str:
    """将期间平移指定月数（负数向前）。"""
    check_period(period)
    year, month = int(period[:4]), int(period[5:])
    index = year * 12 + (month - 1) + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


@dataclass(frozen=True)
class ObservationWindow:
    """观察窗口，起止均为闭区间月份。"""

    start: str
    end: str

    def __post_init__(self) -> None:
        check_period(self.start)
        check_period(self.end)
        if self.start > self.end:
            raise ValueError("观察窗口起点不能晚于终点")

    @property
    def periods(self) -> tuple[str, ...]:
        return iter_periods(self.start, self.end)

    def contains(self, period: str) -> bool:
        return self.start <= check_period(period) <= self.end

    def baseline_periods(self) -> tuple[str, ...]:
        """窗口之前等长的基线月份，用于同比变化判断。"""
        length = len(self.periods)
        end = shift_period(self.start, -1)
        start = shift_period(end, -(length - 1))
        return iter_periods(start, end)


@dataclass(frozen=True)
class Cohort:
    """冻结的比较人群定义。"""

    cohort_id: str
    definition: tuple[tuple[str, str], ...] = ()

    @staticmethod
    def from_mapping(cohort_id: str, mapping: dict) -> "Cohort":
        return Cohort(
            cohort_id=cohort_id,
            definition=tuple(sorted((str(k), str(v)) for k, v in mapping.items())),
        )


@dataclass(frozen=True)
class DrugScope:
    """药品口径：目录版本与药品标识集合。"""

    catalog_version: str
    drug_ids: frozenset[str]

    def __post_init__(self) -> None:
        if not self.catalog_version:
            raise ValueError("药品口径须标明目录版本")
        if not self.drug_ids:
            raise ValueError("药品口径不能为空")


@dataclass(frozen=True)
class RegionScope:
    """地区范围。"""

    region_codes: frozenset[str]

    def __post_init__(self) -> None:
        if not self.region_codes:
            raise ValueError("地区范围不能为空")


@dataclass(frozen=True)
class AnalysisSnapshot:
    """一次分析冻结的全部口径。"""

    snapshot_id: str
    cohort: Cohort
    drug_scope: DrugScope
    region_scope: RegionScope
    window: ObservationWindow
    created_at: str

    def admits(self, record) -> bool:
        """判断数据记录是否落在冻结口径内（允许窗口之前的基线数据）。"""
        if record.region not in self.region_scope.region_codes:
            return False
        if record.drug_id is not None and record.drug_id not in self.drug_scope.drug_ids:
            return False
        return check_period(record.period) <= self.window.end

    def with_drug_scope(
        self, drug_scope: DrugScope, *, snapshot_id: str, created_at: str
    ) -> "AnalysisSnapshot":
        """药品目录变化等口径调整必须生成新快照，而不是修改原快照。"""
        return AnalysisSnapshot(
            snapshot_id=snapshot_id,
            cohort=self.cohort,
            drug_scope=drug_scope,
            region_scope=self.region_scope,
            window=self.window,
            created_at=created_at,
        )


def load_snapshot(path: Path) -> AnalysisSnapshot:
    """读取并校验快照定义文件。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"snapshot_id", "cohort", "drug_scope", "region_scope", "window", "created_at"}
    missing = required - raw.keys()
    if missing:
        raise ValueError(f"快照定义缺少字段：{sorted(missing)}")
    cohort_raw = raw["cohort"]
    return AnalysisSnapshot(
        snapshot_id=str(raw["snapshot_id"]),
        cohort=Cohort.from_mapping(
            str(cohort_raw["cohort_id"]), cohort_raw.get("definition", {})
        ),
        drug_scope=DrugScope(
            str(raw["drug_scope"]["catalog_version"]),
            frozenset(map(str, raw["drug_scope"]["drug_ids"])),
        ),
        region_scope=RegionScope(frozenset(map(str, raw["region_scope"]["region_codes"]))),
        window=ObservationWindow(str(raw["window"]["start"]), str(raw["window"]["end"])),
        created_at=str(raw["created_at"]),
    )
