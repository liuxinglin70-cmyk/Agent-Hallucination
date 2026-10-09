from detector.runner import _summarize, run_batch
from detector.tests.test_runner import _FakeDetector, _envelope, _write_dataset
import json


def test_cache_hit_has_zero_invocation_cost_and_retains_case_cost():
    result = _envelope("a.json")
    result["execution"] = {"provider_attempts": 2, "usage": {"total_tokens": 20},
                           "usage_missing_attempts": 0, "elapsed_seconds": 4}
    summary = _summarize(run_id="r", cases=["a.json"],
        rows=[{"case": "a.json", "result": result, "source": "cached", "elapsed_seconds": 0}],
        manifest={}, stop_reason={})
    measurement = summary["measurement"]
    assert measurement["usage"] is None
    assert measurement["invocation_cost"]["provider_attempts"] == 0
    assert measurement["invocation_cost"]["elapsed_seconds"] == 0
    assert measurement["case_attempt_cost"]["usage"] == {"total_tokens": 20}


def test_resume_cost_counts_each_journal_attempt_once(tmp_path, monkeypatch):
    class Metered(_FakeDetector):
        def detect(self, record, *, case_id, interventions=None):
            result = super().detect(record, case_id=case_id, interventions=interventions)
            result["execution"] = {"provider_attempts": 1, "usage": {"total_tokens": 10},
                                   "usage_missing_attempts": 0, "requests": []}
            return result
    monkeypatch.setattr("detector.runner.Detector", Metered)
    options = dict(dataset_root=_write_dataset(tmp_path / "data"), cases=["a.json"],
                   runs_root=tmp_path / "runs", run_id="r", config={}, workers=1)
    first = run_batch(**options)
    resumed = run_batch(**options)
    assert first["measurement"]["invocation_cost"]["usage"] == {"total_tokens": 10}
    assert resumed["measurement"]["invocation_cost"]["provider_attempts"] == 0
    assert resumed["measurement"]["run_lifetime_cost"]["provider_attempts"] == 1
    assert resumed["measurement"]["run_lifetime_cost"]["usage"] == {"total_tokens": 10}
    assert resumed["measurement"]["peak_provider_inflight"] == 0


def test_partial_execution_is_invocation_cost_without_a_completed_case():
    summary = _summarize(run_id="r", cases=["a.json"], rows=[],
        manifest={}, stop_reason={"error_type": "KeyboardInterrupt"}, started_cases={"a.json"},
        partial_executions={"a.json": {"elapsed_seconds": 3, "provider_attempts": 1,
            "usage": {"total_tokens": 7}, "usage_missing_attempts": 0}})
    assert summary["measurement"]["invocation_cost"]["usage"] == {"total_tokens": 7}
    assert summary["measurement"]["case_attempt_cost"]["provider_attempts"] == 0


def test_unknown_request_count_is_not_reported_as_zero():
    summary = _summarize(run_id="r", cases=["a.json"],
        rows=[{"case": "a.json", "result": _envelope("a.json"), "elapsed_seconds": 2}],
        manifest={}, stop_reason={})
    cost = summary["measurement"]["invocation_cost"]
    assert cost["provider_attempts"] is None
    assert cost["usage_unknown_cases"] == 1


def test_remeasurement_includes_failed_history_without_charging_resume_twice(tmp_path, monkeypatch):
    from detector.runtime import record_request
    class Retried(_FakeDetector):
        healthy = False
        def detect(self, record, *, case_id, interventions=None):
            record_request({"purpose": "judge", "usage": {"total_tokens": 5}})
            return _envelope(case_id, state="verified_clean" if self.healthy else "unresolved",
                             authority="authoritative" if self.healthy else "invalid")
    monkeypatch.setattr("detector.runner.Detector", Retried)
    options = dict(dataset_root=_write_dataset(tmp_path / "data"), cases=["a.json"],
                   runs_root=tmp_path / "runs", run_id="r", config={}, workers=1)
    first = run_batch(**options)
    assert first["measurement"]["run_lifetime_cost"]["provider_attempts"] == 3
    Retried.healthy = True
    measured = run_batch(**options, retry_unresolved=True)
    assert measured["measurement"]["invocation_cost"]["provider_attempts"] == 1
    assert measured["measurement"]["case_attempt_cost"]["provider_attempts"] == 1
    assert measured["measurement"]["run_lifetime_cost"]["usage"] == {"total_tokens": 20}
    cached = run_batch(**options)
    assert cached["measurement"]["invocation_cost"]["usage"] is None
    assert cached["measurement"]["run_lifetime_cost"]["provider_attempts"] == 4


def test_stage_report_uses_invocation_cost_and_keeps_historical_cost_separate():
    from experiments.report import summarize_stage
    invocation = {"elapsed_seconds": 0, "usage": None, "provider_attempts": 0,
                  "usage_missing_attempts": 0, "usage_unknown_cases": 0}
    historical = {**invocation, "usage": {"total_tokens": 20}, "provider_attempts": 2}
    result = _envelope("a.json")
    result["execution"] = {"provider_attempts": 2, "usage": {"total_tokens": 20}}
    report = summarize_stage(stage="baseline", arm="g0", requested=["a.json"],
        rows=[{"case": "a.json", "result": result, "elapsed_seconds": 4}],
        batch_summary={"measurement": {"invocation_cost": invocation,
                                      "case_attempt_cost": historical, "run_lifetime_cost": historical}})
    assert report["cost"]["usage"] is None
    assert report["cost"]["case_seconds"] == 0
    assert report["cost"]["run_lifetime_cost"]["usage"] == {"total_tokens": 20}


def test_measurement_scripts_keep_early_failed_attempt_usage(tmp_path):
    from scripts import run_baseline_693, real_path_check
    directory = tmp_path / "r/cases/a.json"
    directory.mkdir(parents=True)
    result = _envelope("a.json")
    result.update(execution={"provider_attempts": 3, "usage_missing_attempts": 0,
                            "usage": {"total_tokens": 15}},
                  providers={"judge": {"usage": {"total_tokens": 5}}})
    (directory / "result.json").write_text(json.dumps(result), encoding="utf-8")
    assert run_baseline_693._case_usage(tmp_path, "r", "a.json") == {"total_tokens": 15}
    assert real_path_check._case_metrics(tmp_path, "r", "a.json")["tokens"] == 15


def test_incomplete_execution_counters_remain_unknown():
    from detector.measurement import cost_summary
    cost = cost_summary([{"result": {"execution": {"usage": {"total_tokens": 8}}}}])
    assert cost["provider_attempts"] is None
    assert cost["usage_unknown_cases"] == 1


def test_resume_refuses_corrupt_attempt_journal(tmp_path, monkeypatch):
    monkeypatch.setattr("detector.runner.Detector", _FakeDetector)
    options = dict(dataset_root=_write_dataset(tmp_path / "data"), cases=["a.json"],
                   runs_root=tmp_path / "runs", run_id="r", config={}, workers=1)
    run_batch(**options)
    path = next((tmp_path / "runs/r/cases/a.json/attempts").glob("*/case_attempts/*/attempt.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["execution"]["usage"] = {"total_tokens": 999}
    path.write_text(json.dumps(payload), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="journal"):
        run_batch(**options)
