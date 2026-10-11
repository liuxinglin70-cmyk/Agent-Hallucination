from copy import deepcopy

import pytest

from detector.measurement import case_execution, cost_summary, result_usage
from detector.runner import detect_with_transport_retry
from detector.runtime import record_request
from detector.tests.test_runner import _FakeDetector, _envelope
from experiments.report import StageReport, summarize_stage


@pytest.mark.parametrize("mode", ["stage", "mixed", "request"])
@pytest.mark.parametrize("nested", [False, True])
def test_actual_retry_preserves_cost_source_through_aggregates(mode, nested):
    class Detector(_FakeDetector):
        def detect(self, record, *, case_id, interventions=None):
            self.calls += 1
            stage = mode == "stage" or (mode == "mixed" and self.calls == 1)
            retry = mode == "mixed" and self.calls == 1
            result = _envelope(case_id, state="unresolved" if retry else "verified_clean",
                               authority="invalid" if retry else "authoritative")
            if retry:
                result["authority_failure_reasons"] = [{"error_type": "provider_error"}]
            if stage:
                result["providers"] = {"judge": {"usage": {"total_tokens": 10}}}
            else:
                record_request({"purpose": "judge", "usage": {"total_tokens": 20}})
            return result

    result = detect_with_transport_retry(Detector({}), {}, case_id="synthetic")
    if nested:
        child = deepcopy(result["execution"])
        result["execution"] = case_execution(
            [{"execution": child}], started_at="same", finished_at="same", elapsed_seconds=0,
        )
    expected_source = "request_observations" if mode == "request" else "legacy_stage_metadata_lower_bound"
    expected_tokens = {"stage": 10, "mixed": 30, "request": 20}[mode]
    reading = result_usage(result)
    assert reading["source"] == expected_source
    assert reading["usage"]["total_tokens"] == expected_tokens
    assert reading["attempts"] == (1 if mode == "request" else None)
    assert reading["missing_attempts"] == (0 if mode == "request" else None)
    rows = [{"case": "synthetic", "result": result, "elapsed_seconds": 0}]
    assert cost_summary(rows)["usage_source"] == expected_source
    report = StageReport(stage="baseline", arm="empty", requested=["synthetic"], rows=rows)
    assert report.cost()["usage_source"] == expected_source


def test_reader_retains_source_from_nested_observations_without_rewriting_them():
    result = {"execution": {"usage": {"total_tokens": 10}, "case_attempts": [
        {"execution": {"usage_source": "legacy_stage_metadata_lower_bound"}},
    ]}}
    before = deepcopy(result)
    assert result_usage(result)["source"] == "legacy_stage_metadata_lower_bound"
    assert result == before


@pytest.mark.parametrize("source", [None, "unsupported", []])
def test_unidentified_explicit_cost_source_is_not_reported_as_request_observations(source):
    result = {"execution": {"usage": {"total_tokens": 10}, "usage_source": source}}
    assert result_usage(result)["source"] == "unknown"


@pytest.mark.parametrize("attempts", [None, {}, "unsupported"])
def test_malformed_attempt_sources_are_unknown(attempts):
    result = {"execution": {"usage": {"total_tokens": 10}, "case_attempts": attempts}}
    assert result_usage(result)["source"] == "unknown"


@pytest.mark.parametrize("source", ["legacy_stage_metadata_lower_bound", "missing", None])
def test_invocation_report_preserves_its_own_cost_source(source):
    invocation = {"elapsed_seconds": 1, "usage": {"total_tokens": 10},
                  "provider_attempts": None, "usage_missing_attempts": 0, "usage_unknown_cases": 1}
    if source != "missing":
        invocation["usage_source"] = source
    report = summarize_stage(stage="baseline", arm="empty", requested=[], rows=[],
                             batch_summary={"measurement": {"invocation_cost": invocation}})
    assert report["cost"]["usage_source"] == (source if isinstance(source, str) and source != "missing" else "unknown")
    assert report["cost"]["provider_attempts"] is None


@pytest.mark.parametrize("mode", ["stage", "legacy", "unknown", "request"])
def test_script_exports_preserve_the_cost_source_of_actual_rows(mode):
    from scripts.real_path_check import _case_metrics
    from scripts.run_baseline_693 import _usage_report

    result = _envelope("synthetic")
    result["providers"] = {"judge": {"usage": {"total_tokens": 10}}}
    source = {"stage": "legacy_stage_metadata_lower_bound", "legacy": "legacy_stage_metadata_lower_bound",
              "unknown": "unknown", "request": "request_observations"}[mode]
    if mode != "stage":
        result["execution"] = {"usage": {"total_tokens": 10}, "usage_source": source,
                               "provider_attempts": 1, "usage_missing_attempts": 0}
    before = deepcopy(result)
    row = _case_metrics("synthetic", result)
    report = _usage_report([{"case": "synthetic", "result": result}], ["synthetic"])
    assert row["usage_source"] == source
    assert report["usage_source"] == source
    assert report["per_case"][0]["usage_source"] == source
    assert row["tokens"] == report["total_tokens"] == 10
    assert report["usage_unknown_cases"] == (1 if mode == "stage" else 0)
    expected_bound = None if mode == "unknown" else mode != "request"
    assert report["total_is_lower_bound"] is expected_bound
    assert result == before
