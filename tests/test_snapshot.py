import dataclasses
from pathlib import Path

import pytest

from src.snapshot import (
    AnalysisSnapshot,
    DrugScope,
    ObservationWindow,
    iter_periods,
    load_snapshot,
    shift_period,
)

from factories import make_snapshot


def test_iter_periods_crosses_year():
    assert iter_periods("2025-11", "2026-02") == (
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
    )


def test_shift_period_backwards():
    assert shift_period("2026-01", -1) == "2025-12"
    assert shift_period("2026-06", -6) == "2025-12"


def test_window_rejects_bad_format_and_order():
    with pytest.raises(ValueError):
        ObservationWindow("2026-13", "2026-12")
    with pytest.raises(ValueError):
        ObservationWindow("2026-06", "2026-01")


def test_baseline_periods_match_window_length():
    window = ObservationWindow("2026-01", "2026-06")
    assert window.baseline_periods() == (
        "2025-07",
        "2025-08",
        "2025-09",
        "2025-10",
        "2025-11",
        "2025-12",
    )


def test_snapshot_is_frozen():
    snapshot = make_snapshot()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.snapshot_id = "other"
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.window = ObservationWindow("2026-01", "2026-03")


def test_catalog_change_creates_new_snapshot():
    snapshot = make_snapshot()
    new_scope = DrugScope("cat-2026-v2", frozenset({"D1001", "D1002", "D1004"}))
    updated = snapshot.with_drug_scope(
        new_scope, snapshot_id="snap-test-v2", created_at="2026-08-01T00:00:00Z"
    )
    assert updated.snapshot_id == "snap-test-v2"
    assert updated.drug_scope is new_scope
    # 原快照保持不变
    assert snapshot.drug_scope.drug_ids == frozenset({"D1001", "D1002", "D1003"})
    assert updated.window == snapshot.window


def test_admits_scope_rules():
    snapshot = make_snapshot()

    class Rec:
        region = "110000"
        drug_id = "D1001"
        period = "2025-09"  # 窗口前的基线数据允许接入

    assert snapshot.admits(Rec())
    Rec.period = "2026-07"  # 窗口之后不允许
    assert not snapshot.admits(Rec())
    Rec.period = "2026-03"
    Rec.region = "999999"  # 口径外地区不允许
    assert not snapshot.admits(Rec())
    Rec.region = "110000"
    Rec.drug_id = "D9999"  # 口径外药品不允许
    assert not snapshot.admits(Rec())


def test_load_snapshot_fixture():
    snapshot = load_snapshot(Path("fixtures/snapshot.json"))
    assert isinstance(snapshot, AnalysisSnapshot)
    assert snapshot.snapshot_id == "snap-2026h1-monitor-v1"
    assert snapshot.window.periods[0] == "2026-01"
    assert len(snapshot.drug_scope.drug_ids) == 6
    assert ("insurance", "basic_medical") in snapshot.cohort.definition
