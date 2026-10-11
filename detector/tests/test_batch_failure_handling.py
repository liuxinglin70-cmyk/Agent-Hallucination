"""What a batch does when the provider stops answering.

The 2026-09-23 real run is the specification for these tests. Its credential
expired at 06:22:18 and the batch kept going until 06:33:01, writing 128 more
rows of which 123 were ``invalid`` with ``every judge vote failed``. Those rows
are not measurements, they are the same measurement failure repeated, and they
cost ten minutes of the run's remaining budget to produce.

Each test below isolates one part of the answer: the classification that
recognises the condition, the stop that ends the batch, and the record that
keeps "not measured" distinct from "measured as false" afterwards.
"""

from __future__ import annotations

import json

import pytest

from detector.measurement import (
    fatal_failure,
    measurement_status,
    needs_remeasurement,
    result_usage,
    retry_in_run,
)
from detector.result import RESULT_SCHEMA
from detector.runner import run_batch, safe_case_id


def _envelope(case_id, *, state="verified_clean", authority="authoritative",
              reasons=(), execution=None):
    payload = {
        "schema_version": RESULT_SCHEMA,
        "case_id": case_id,
        "state": state,
        "occurrence_state": state,
        "decision_authority": authority,
        "runtime_valid": state != "unresolved",
        "responsible_event_id": None,
        "responsible_step": None,
        "incident_occurred": state == "verified_error",
        "proof": {},
        "stage_status": {},
        "providers": {},
        "authority_failure_reasons": list(reasons),
        "evidence_health": {},
        "provenance": {},
    }
    if execution is not None:
        payload["execution"] = execution
    return payload


def _auth_failure():
    return [{
        "component": "judge", "code": "provider_error",
        "error_type": "authentication_error",
        "message": "every judge vote failed: http 401",
    }]


def _outage_failure():
    return [{
        "component": "judge", "code": "provider_error",
        "error_type": "provider_error", "message": "every judge vote failed",
    }]


def _deadline_failure():
    return [{
        "component": "backend", "code": "batch_deadline_exceeded",
        "error_type": "Timeout",
        "message": "batch deadline exceeded before this case started",
    }]


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------


def test_a_rejected_credential_is_fatal_and_a_dead_provider_is_not():
    """Both are "no measurement"; only one justifies ending the run.

    A 401 will not heal by asking again, and every remaining case would produce
    the same row. A transient provider error might heal, so the run may keep
    its remaining budget for it.
    """
    fatal = _envelope("a.json", state="unresolved", authority="invalid",
                      reasons=_auth_failure())
    outage = _envelope("b.json", state="unresolved", authority="invalid",
                       reasons=_outage_failure())
    assert measurement_status(fatal) == "fatal_provider"
    assert measurement_status(outage) == "infrastructure_failure"
    assert fatal_failure(fatal) is not None
    assert fatal_failure(outage) is None


def test_an_exhausted_budget_is_not_retried_inside_the_run():
    """The budget a retry would spend is the one that just ran out.

    Observed: one gain-set case (``Magentic_One/049``) exceeded its own
    deadline on every attempt, and the whole-case retry re-opened the deadline
    each time -- three hours of worker time to reach the same answer, while the
    round's budget drained before patrol could run.
    """
    expired = _envelope("c.json", state="unresolved", authority="invalid",
                        reasons=_deadline_failure())
    assert measurement_status(expired) == "infrastructure_failure"
    assert retry_in_run(expired) is False
    assert needs_remeasurement(expired) is True


def test_explicit_terminal_case_error_is_not_retried_inside_the_run():
    result = _envelope("c.json", state="unresolved", authority="invalid", reasons=[{
        "component": "judge", "error_type": "provider_error", "retryable": False}])
    assert not retry_in_run(result)


def test_a_provider_error_is_retried_inside_the_run():
    outage = _envelope("d.json", state="unresolved", authority="invalid",
                       reasons=_outage_failure())
    assert retry_in_run(outage) is True


@pytest.mark.parametrize("reason", [
    {"error_type": "authentication_error", "code": "provider_error"},
    {"error_type": "ProviderError", "code": "authentication_error"},
])
def test_fatal_error_in_either_field_stops_same_run_retry(reason):
    row = _envelope("fatal.json", state="unresolved", authority="invalid",
                    reasons=[reason])
    assert fatal_failure(row) == reason
    assert measurement_status(row) == "fatal_provider"
    assert retry_in_run(row) is False
    assert needs_remeasurement(row) is True


def test_a_decision_is_never_retried():
    decided = _envelope("e.json")
    assert retry_in_run(decided) is False
    assert needs_remeasurement(decided) is False


def test_a_degraded_verdict_is_a_reading_not_an_outage():
    degraded = _envelope("f.json", authority="degraded_judge_only")
    assert measurement_status(degraded) == "degraded"
    assert needs_remeasurement(degraded) is False


# --------------------------------------------------------------------------
# usage accounting
# --------------------------------------------------------------------------


def test_usage_comes_from_the_per_attempt_record_not_one_ballot():
    """An aggregate numeric reading does not establish its provenance."""
    result = _envelope("g.json", execution={
        "usage": {"total_tokens": 17359},
        "provider_attempts": 3,
        "usage_missing_attempts": 0,
    })
    reading = result_usage(result)
    assert reading["usage"]["total_tokens"] == 17359
    assert reading["source"] == "unknown"


def test_legacy_rows_report_a_lower_bound_and_say_so():
    """An archived envelope has no per-attempt record, so it cannot be exact.

    The old shape stores one ballot per stage under ``providers``; summing what
    is there is a lower bound, and calling it anything else is how a run's cost
    model ends up built on prose.
    """
    result = _envelope("h.json")
    result["providers"] = {"judge": {"usage": {"total_tokens": 10}}}
    reading = result_usage(result)
    assert reading["usage"]["total_tokens"] == 10
    assert reading["source"] == "legacy_stage_metadata_lower_bound"


def test_stage_usage_prefers_the_vote_total_over_one_ballot():
    result = _envelope("i.json")
    result["providers"] = {"judge": {
        "usage": {"total_tokens": 10}, "usage_total": {"total_tokens": 33},
    }}
    assert result_usage(result)["usage"]["total_tokens"] == 33


# --------------------------------------------------------------------------
# the batch stops instead of repeating the failure
# --------------------------------------------------------------------------


def _write_dataset(root, count=40):
    root.mkdir(parents=True, exist_ok=True)
    cases = []
    for index in range(count):
        case = f"src/{index:03d}.json"
        (root / "src").mkdir(exist_ok=True)
        (root / f"src/{index:03d}.json").write_text(
            json.dumps({"question": "q", "history": [{"content": "a"}]}),
            encoding="utf-8",
        )
        cases.append(case)
    return cases


def test_a_rejected_credential_stops_the_batch_and_says_why(tmp_path, monkeypatch):
    """The whole case list must not be spent re-proving a 401.

    This is the regression test for the observed run: 123 identical invalid
    rows were written after the credential died, each one costing a full
    detect call to produce.
    """
    class _Unauthorized:
        def __init__(self, config):
            self.config = config
            self.calls = 0

        def detect(self, record, *, case_id, interventions=None):
            self.calls += 1
            return _envelope(case_id, state="unresolved", authority="invalid",
                             reasons=_auth_failure())

    cases = _write_dataset(tmp_path / "data", 40)
    monkeypatch.setattr("detector.runner.Detector", _Unauthorized)
    summary = run_batch(
        dataset_root=tmp_path / "data", cases=cases, run_id="r",
        runs_root=tmp_path / "runs", config={}, workers=4,
    )
    assert summary["stop"]["error_type"] == "authentication_error"
    started = summary["measurement"]["measured"] + summary["measurement"]["not_started"]
    assert summary["measurement"]["not_started"] > 0, (
        "the batch must record the cases it never started, not omit them"
    )
    assert summary["measurement"]["measured"] < len(cases), (
        "the batch kept measuring after the provider rejected the credential"
    )


def test_a_dead_provider_does_not_stop_the_batch(tmp_path, monkeypatch):
    """A transient outage is per-case; the run keeps its remaining budget."""

    class _Outage:
        def __init__(self, config):
            self.config = config

        def detect(self, record, *, case_id, interventions=None):
            return _envelope(case_id, state="unresolved", authority="invalid",
                             reasons=_outage_failure())

    cases = _write_dataset(tmp_path / "data", 12)
    monkeypatch.setattr("detector.runner.Detector", _Outage)
    summary = run_batch(
        dataset_root=tmp_path / "data", cases=cases, run_id="r",
        runs_root=tmp_path / "runs", config={}, workers=4,
    )
    assert summary["stop"] == {}, "an outage must not end the batch"
    assert summary["measurement"]["not_started"] == 0
    assert summary["measurement"]["status_counts"]["infrastructure_failure"] == 12


def test_not_started_cases_are_named_rather_than_dropped(tmp_path, monkeypatch):
    """A deadline must leave "did not run" readable as such.

    A summary that only said ``completed=7`` would be indistinguishable from a
    corpus that had seven cases.
    """
    class _Slow:
        def __init__(self, config):
            self.config = config

        def detect(self, record, *, case_id, interventions=None):
            import time
            time.sleep(0.05)
            return _envelope(case_id)

    cases = _write_dataset(tmp_path / "data", 20)
    monkeypatch.setattr("detector.runner.Detector", _Slow)
    summary = run_batch(
        dataset_root=tmp_path / "data", cases=cases, run_id="r",
        runs_root=tmp_path / "runs", config={}, workers=2,
        deadline_seconds=0.12,
    )
    measurement = summary["measurement"]
    assert measurement["not_started"] > 0
    assert measurement["not_started_cases"], "the unstarted cases must be listed"
    assert set(measurement["not_started_cases"]) <= set(cases)


def test_the_run_manifest_records_attempt_identity_and_case_order(tmp_path, monkeypatch):
    class _Ok:
        def __init__(self, config):
            self.config = config
            self.calls = 0

        def detect(self, record, *, case_id, interventions=None):
            self.calls += 1
            return _envelope(case_id)

    cases = _write_dataset(tmp_path / "data", 3)
    monkeypatch.setattr("detector.runner.Detector", _Ok)
    runs_root = tmp_path / "runs"
    run_batch(dataset_root=tmp_path / "data", cases=cases, run_id="r",
              runs_root=runs_root, config={}, workers=1)
    manifest = json.loads((runs_root / "r" / "run.json").read_text(encoding="utf-8"))
    assert manifest["attempts"] == 1
    assert manifest["attempt_id"]
    assert manifest["cases"] == cases
    assert manifest["case_set_digest"]
    assert manifest["source_identity"]
    # A second invocation is a new attempt over the same identity.
    run_batch(dataset_root=tmp_path / "data", cases=list(reversed(cases)),
              run_id="r2", runs_root=runs_root, config={}, workers=1)
    other = json.loads((runs_root / "r2" / "run.json").read_text(encoding="utf-8"))
    assert other["case_set_digest"] != manifest["case_set_digest"], (
        "case order is part of the sample identity"
    )


def test_a_rerun_keeps_the_previous_attempt_beside_the_new_one(tmp_path, monkeypatch):
    """The earlier measurement is evidence of what this case once produced."""
    class _OutageThenOk:
        #: Class-level so the flag survives ``run_batch`` building a new
        #: detector for each invocation -- which is exactly the situation a
        #: resume is in.
        healthy = False

        def __init__(self, config):
            self.config = config

        def detect(self, record, *, case_id, interventions=None):
            if _OutageThenOk.healthy:
                return _envelope(case_id)
            return _envelope(case_id, state="unresolved", authority="invalid",
                             reasons=_outage_failure())

    cases = _write_dataset(tmp_path / "data", 1)
    monkeypatch.setattr("detector.runner.Detector", _OutageThenOk)
    runs_root = tmp_path / "runs"
    run_batch(dataset_root=tmp_path / "data", cases=cases, run_id="r",
              runs_root=runs_root, config={}, workers=1)
    _OutageThenOk.healthy = True
    run_batch(dataset_root=tmp_path / "data", cases=cases, run_id="r",
              runs_root=runs_root, config={}, workers=1, retry_unresolved=True)
    case_dir = runs_root / "r" / "cases" / safe_case_id(cases[0])
    assert (case_dir / "result.json").is_file()
    attempts = sorted((case_dir / "attempts").iterdir())
    assert len(attempts) == 2, "the first attempt must survive the retry"
    assert json.loads(
        (attempts[0] / "result.json").read_text(encoding="utf-8")
    )["state"] == "unresolved"
    assert json.loads(
        (attempts[1] / "result.json").read_text(encoding="utf-8")
    )["state"] == "verified_clean"
