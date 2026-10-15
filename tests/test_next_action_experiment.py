"""真实实验的指标与全尝试统计采用合成证据验证，不调用模型。"""

import json

import pytest

from scripts import run_next_action_experiment as experiment


def row(case, gold, prediction, step=None):
    return {"case": case, "gold": gold, "prediction": prediction, "responsible_step": step, "gold_step": 2,
            "localization_authority": "authoritative", "localization_target": "dataset_first_error_event.v1"}


def test_all_requested_metrics_keep_unresolved_in_denominator():
    rows = [row("a", True, True, 2), row("b", True, None), row("c", False, False)]
    result = experiment.metrics(rows)
    assert result["all_case_accuracy"] == 2 / 3
    assert result["authoritative"] == 2
    assert result["step_localization"] == {"n": 2, "hits": 1, "accuracy": 0.5}
    assert not experiment.localized({**rows[0], "localization_authority": "none"})


def test_paired_changes_include_authority_recovery_without_correctness_selection():
    before = [row("a", True, None), row("b", True, True, 2)]
    after = [row("a", True, True, 2), row("b", True, False)]
    result = experiment.paired(before, after)
    assert result == {"detection_fixed": ["a"], "detection_regressed": ["b"],
                      "localization_fixed": ["a"], "localization_regressed": ["b"]}


def test_journal_statistics_include_abandoned_attempts_and_deduplicate_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "OUT", tmp_path)
    logs = tmp_path / "runs" / "main" / "attempts"
    logs.mkdir(parents=True)
    events = [
        {"request_id": "a", "event": "request_started", "purpose": "action_forecast"},
        {"request_id": "a", "event": "request_finished", "purpose": "action_forecast", "status": "ok", "usage": {"total_tokens": 10}},
        {"request_id": "b", "event": "request_started", "purpose": "judge"},
    ]
    for sub in ("discarded", "adopted"):
        folder = logs / sub
        folder.mkdir()
        (folder / "requests.jsonl").write_text("\n".join(json.dumps(row) for row in events), encoding="utf-8")
    result = experiment.journal_cost()
    assert result["started_requests"] == 2
    assert result["started_without_finish"] == 1
    assert result["by_purpose"] == {"action_forecast": 1, "judge": 1}
    assert result["usage"] == {"total_tokens": 10}


def test_source_drift_blocks_live_execution(monkeypatch):
    monkeypatch.setattr(experiment, "source_manifest", lambda: {"current.py": "changed"})
    with pytest.raises(ValueError, match="身份变化"):
        experiment.verify_identity({"source_manifest": {"current.py": "frozen"}})
