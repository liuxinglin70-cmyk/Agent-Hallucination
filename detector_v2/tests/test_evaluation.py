from __future__ import annotations

from detector_v2.evaluation import binary_metrics


def test_binary_metrics_balanced():
    rows = [
        {"gold": True, "prediction": True},
        {"gold": True, "prediction": False},
        {"gold": False, "prediction": True},
        {"gold": False, "prediction": False},
    ]
    m = binary_metrics(rows)
    assert m["decisive"] == 4
    assert m["completion"] == 1.0
    assert m["tp"] == m["fp"] == m["fn"] == m["tn"] == 1
    assert abs(m["macro_f1"] - 0.5) < 1e-9


def test_binary_metrics_unresolved_counted():
    rows = [
        {"gold": True, "prediction": True},
        {"gold": False, "prediction": None},
    ]
    m = binary_metrics(rows)
    assert m["decisive"] == 1
    assert m["unresolved"] == 1
    assert m["completion"] == 0.5
    assert m["all_case_accuracy"] == 0.5
    assert m["decisive_accuracy"] == 1.0
