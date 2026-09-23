from __future__ import annotations

import json

from detector.evaluation import binary_metrics, evaluate_run


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
    from detector.result import prediction_state

    assert prediction_state(rows[0]) is None


def test_degraded_terminal_result_is_predictable_for_stage_a():
    from detector.result import prediction_state

    row = {
        "schema_version": "detector.detection_result.v1",
        "state": "verified_error",
        "occurrence_state": "verified_error",
        "runtime_valid": True,
        "decision_authority": "degraded_judge_only",
    }
    assert prediction_state(row, require_authoritative=False) is True


def _write_case(dataset, case_id, gold):
    path = dataset / case_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "question": "q",
        "history": [{"content": "a"}],
        "is_hallucination": gold,
    }), encoding="utf-8")


def _write_result(runs_root, run_id, case_id, **overrides):
    from detector.runner import safe_case_id

    payload = {
        "schema_version": "detector.detection_result.v1",
        "case_id": case_id,
        "state": "verified_clean",
        "occurrence_state": "verified_clean",
        "runtime_valid": True,
        "decision_authority": "authoritative",
    }
    payload.update(overrides)
    directory = runs_root / run_id / "cases" / safe_case_id(case_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "result.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )


def test_degraded_readings_do_not_enter_the_accuracy_line(tmp_path):
    """A degraded terminal verdict is not a prediction about the case.

    Regression: ``evaluate_run`` read the envelopes with the loose reader, so a
    clean case whose recall net never ran scored as a true negative
    (``tn=1``, ``decisive_accuracy=1.0``) and a judge-only false positive
    scored as ``fp=1``. Both are statements about the run -- an outage, a
    reduced view -- and counting them as answers turned provider failures into
    detector accuracy (or into detector error).
    """
    dataset = tmp_path / "data"
    runs = tmp_path / "runs"
    _write_case(dataset, "clean.json", False)
    _write_case(dataset, "hallu.json", True)
    # The recall net never ran: judge-only clears for both cases.
    _write_result(runs, "r", "clean.json",
                  decision_authority="degraded_judge_only")
    _write_result(runs, "r", "hallu.json",
                  decision_authority="degraded_judge_only",
                  state="verified_error", occurrence_state="verified_error")

    report = evaluate_run(
        runs_root=runs, run_id="r", dataset_root=dataset,
        cases=["clean.json", "hallu.json"],
    )
    metrics = report["metrics"]
    # Neither degraded row is a prediction.
    assert metrics["decisive"] == 0
    assert metrics["tn"] == 0
    assert metrics["fp"] == 0
    assert metrics["completion"] == 0.0
    assert metrics["decisive_accuracy"] is None
    # They are reported as their own quantity instead.
    assert metrics["authoritative"] == 0
    assert metrics["degraded"] == 2
    assert report["errors"] == []


def test_degraded_disagreement_is_listed_but_not_scored(tmp_path):
    """A degraded verdict that disagrees with gold is visible, not counted."""
    dataset = tmp_path / "data"
    runs = tmp_path / "runs"
    _write_case(dataset, "clean.json", False)
    # A judge-only conviction on a clean case: read loosely this is an fp.
    _write_result(runs, "r", "clean.json",
                  decision_authority="degraded_judge_only",
                  state="verified_error", occurrence_state="verified_error")

    report = evaluate_run(
        runs_root=runs, run_id="r", dataset_root=dataset, cases=["clean.json"],
    )
    assert report["metrics"]["fp"] == 0
    assert report["metrics"]["degraded_disagreeing"] == 1
    listed = report["degraded_disagreements"]
    assert [row["case"] for row in listed] == ["clean.json"]
    assert listed[0]["kind"] == "fp"
    assert listed[0]["authority"] == "degraded_judge_only"


def test_loose_reading_is_available_under_terminal_metrics(tmp_path):
    """The provider-health reading survives, under its own name.

    Stage A's accounting needs the loose reader -- an outage has to be visible
    -- so the change keeps it as ``metrics["terminal_metrics"]`` rather than
    deleting it.
    """
    dataset = tmp_path / "data"
    runs = tmp_path / "runs"
    _write_case(dataset, "clean.json", False)
    _write_result(runs, "r", "clean.json",
                  decision_authority="degraded_judge_only")

    report = evaluate_run(
        runs_root=runs, run_id="r", dataset_root=dataset, cases=["clean.json"],
    )
    terminal = report["metrics"]["terminal_metrics"]
    assert terminal["tn"] == 1
    assert terminal["decisive"] == 1
    # And the authoritative row is still counted by both readers.
    _write_result(runs, "r", "clean.json")
    report = evaluate_run(
        runs_root=runs, run_id="r", dataset_root=dataset, cases=["clean.json"],
    )
    assert report["metrics"]["tn"] == 1
    assert report["metrics"]["decisive_accuracy"] == 1.0
    assert report["metrics"]["degraded"] == 0
    assert report["metrics"]["terminal_metrics"]["tn"] == 1


def test_unresolved_rows_are_not_degraded_answers(tmp_path):
    """An unresolved envelope made no terminal decision at all."""
    dataset = tmp_path / "data"
    runs = tmp_path / "runs"
    _write_case(dataset, "clean.json", False)
    _write_result(runs, "r", "clean.json", state="unresolved",
                  occurrence_state="unresolved", decision_authority="invalid",
                  runtime_valid=False)

    report = evaluate_run(
        runs_root=runs, run_id="r", dataset_root=dataset, cases=["clean.json"],
    )
    metrics = report["metrics"]
    assert metrics["degraded"] == 0
    assert metrics["no_terminal_decision"] == 1
    assert metrics["provider_unresolved"] == 1
    assert report["degraded_disagreements"] == []


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
