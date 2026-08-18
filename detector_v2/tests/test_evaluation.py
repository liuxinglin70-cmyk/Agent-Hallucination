from __future__ import annotations

import json

from detector_v2.evaluation import binary_metrics, evaluate_run


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


def test_degraded_result_is_not_authoritative():
    rows = [
        {
            "gold": True,
            "prediction": True,
            "decision_authority": "degraded_judge_only",
        },
    ]
    from detector_v2.result import prediction_state

    assert prediction_state(rows[0]) is None


def test_evaluate_run_counts_missing_results(tmp_path):
    dataset = tmp_path / "data"
    dataset.mkdir()
    (dataset / "a.json").write_text(json.dumps({
        "question": "q",
        "history": [{"content": "a"}],
        "is_hallucination": False,
    }), encoding="utf-8")
    report = evaluate_run(
        runs_root=tmp_path / "runs",
        run_id="r",
        dataset_root=dataset,
        cases=["a.json"],
    )
    assert report["metrics"]["completion"] == 0.0
    assert report["missing_cases"] == ["a.json"]
