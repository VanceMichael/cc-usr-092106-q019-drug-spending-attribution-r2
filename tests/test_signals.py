from src.signals import detect_interruptions

from factories import complaints, make_snapshot, stock, visits


def test_interruption_signal_detected():
    snapshot = make_snapshot()
    records = (
        visits(snapshot, region="110000", drug_id="D1001", baseline_value=1000, window_value=600)
        + complaints(snapshot, region="110000", drug_id="D1001", baseline_value=10, window_value=15)
    )
    signals = detect_interruptions(records, snapshot)
    assert len(signals) == 1
    signal = signals[0]
    assert signal.region == "110000"
    assert signal.drug_id == "D1001"
    assert signal.visit_drop == 0.4
    assert signal.feedback_rise == 0.5
    assert signal.severity == "medium"
    assert "取药受阻" in signal.note


def test_supply_confound_is_flagged():
    snapshot = make_snapshot()
    records = (
        visits(snapshot, region="110000", drug_id="D1001", baseline_value=1000, window_value=600)
        + complaints(snapshot, region="110000", drug_id="D1001", baseline_value=10, window_value=15)
        + stock(snapshot, region="110000", drug_id="D1001", baseline_value=0.9, window_value=0.5)
    )
    (signal,) = detect_interruptions(records, snapshot)
    assert "供应短缺" in signal.note


def test_no_signal_when_feedback_stable():
    snapshot = make_snapshot()
    records = (
        visits(snapshot, region="110000", drug_id="D1001", baseline_value=1000, window_value=600)
        + complaints(snapshot, region="110000", drug_id="D1001", baseline_value=10, window_value=10)
    )
    assert detect_interruptions(records, snapshot) == ()


def test_no_signal_when_visits_stable():
    snapshot = make_snapshot()
    records = (
        visits(snapshot, region="110000", drug_id="D1001", baseline_value=1000, window_value=980)
        + complaints(snapshot, region="110000", drug_id="D1001", baseline_value=10, window_value=15)
    )
    assert detect_interruptions(records, snapshot) == ()


def test_high_severity_when_drop_large():
    snapshot = make_snapshot()
    records = (
        visits(snapshot, region="110000", drug_id=None, baseline_value=1000, window_value=400)
        + complaints(snapshot, region="110000", drug_id=None, baseline_value=10, window_value=13)
    )
    (signal,) = detect_interruptions(records, snapshot)
    assert signal.severity == "high"
