"""The experiment package's contract: every case accounted for, once.

The failure these tests guard against is not a crash. It is a report that reads
as a result: the 2026-09-23 baseline summary said ``completed: 693`` while 134
of those rows were provider failures, and the accuracy a reader would take from
it was computed over the 204 rows that were measurements. Nothing in that file
said which was which.
"""

from __future__ import annotations

from dataclasses import replace
import json

import pytest

from detector.runner import record_case
from detector.result import RESULT_SCHEMA
from experiments.plan import (
    load_measurement,
    load_plan,
)
from experiments.report import StageReport, summarize_stage
from experiments.runner import paired_sample, plan_status


def _result(case_id, *, state="verified_clean", authority="authoritative",
            reasons=()):
    return {
        "schema_version": RESULT_SCHEMA,
        "case_id": case_id,
        "state": state,
        "occurrence_state": state,
        "decision_authority": authority,
        "runtime_valid": state != "unresolved",
        "responsible_step": None,
        "incident_occurred": state == "verified_error",
        "proof": {}, "stage_status": {}, "providers": {},
        "authority_failure_reasons": list(reasons), "provenance": {},
    }


def _outage():
    return [{"component": "judge", "code": "provider_error",
             "error_type": "provider_error", "message": "every judge vote failed"}]


def _fatal():
    return [{"component": "judge", "code": "provider_error",
             "error_type": "authentication_error", "message": "http 401"}]


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------


def test_the_shipped_measurement_set_excludes_the_loops_own_cases():
    """The measurement set must not contain the cases the loop develops on."""
    measurement = load_measurement()
    plan = load_plan()
    assert measurement.size == 653
    assert len(measurement.excluded) == 40
    assert not (set(measurement.cases) & set(measurement.excluded))
    assert plan.measurement.size == measurement.size


def test_a_prefix_of_the_frozen_order_stays_stratified():
    """Every prefix must span the sources, or a stopped run measures one block.

    This is the property that makes the sample size a budget decision: if the
    order clustered by source, stopping early would mean measuring
    whichever source sorts first.
    """
    measurement = load_measurement()
    for size in (40, 120, 300):
        spread = measurement.stratification(size)
        assert spread["sources"] == 7, (
            f"a {size}-case prefix covers only {spread['by_source']}"
        )


def test_the_plan_refuses_a_stage_with_no_criterion(tmp_path):
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({
        "stages": ["baseline", "unheard_of"],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="no criterion"):
        load_plan(plan_path=plan_file)


def test_the_plan_states_its_effect_criterion_and_the_detectable_minimum():
    plan = load_plan()
    effect = plan.stage("effect")
    assert "decisive_fixed" in effect["rule"]
    assert effect["minimum_detectable"]["fixed"] == 5
    assert effect["unstable_cases_excluded"] is True


def test_the_plan_states_that_zero_admissions_is_not_a_mechanism_verdict():
    plan = load_plan()
    rule = plan.stage("no_admission")["rule"]
    assert "not measured" in rule
    assert len(plan.stage("no_admission")["questions"]) == 5


def test_a_missing_measurement_file_is_an_error_not_an_empty_sample(tmp_path):
    with pytest.raises(Exception):
        load_measurement(tmp_path / "nope.json")


# --------------------------------------------------------------------------
# the report contract
# --------------------------------------------------------------------------


def _report(requested, outcomes):
    rows = [
        {"case": case, "result": result, "elapsed_seconds": 1.0}
        for case, result in outcomes.items()
    ]
    return StageReport(stage="baseline", arm="empty",
                       requested=list(requested), rows=rows)


def test_every_requested_case_is_accounted_for_exactly_once():
    requested = ["a", "b", "c", "d"]
    report = _report(requested, {
        "a": _result("a"),
        "b": _result("b", state="unresolved", authority="invalid", reasons=_outage()),
    })
    buckets = report.buckets()
    flat = [case for cases in buckets.values() for case in cases]
    assert sorted(flat) == requested, "a case was dropped or double-counted"
    assert buckets["not_started"] == ["c", "d"]
    assert buckets["infrastructure_failure"] == ["b"]
    assert buckets["authoritative"] == ["a"]


def test_an_outage_row_is_not_counted_as_a_measurement():
    requested = ["a", "b", "c"]
    report = _report(requested, {
        "a": _result("a"),
        "b": _result("b", state="unresolved", authority="invalid", reasons=_outage()),
        "c": _result("c", state="unresolved", authority="invalid", reasons=_fatal()),
    })
    payload = report.to_dict()
    assert payload["coverage"]["measured"] == 1
    assert payload["coverage"]["not_measured"] == 2
    assert payload["coverage"]["by_status"]["fatal_provider"] == 1
    assert set(payload["coverage"]["not_measured_cases"]) == {"b", "c"}


def test_a_degraded_verdict_is_reported_but_not_scored():
    """A degraded decision is a reading under a reduced view, not an outage.

    It is counted in its own bucket: excluding it from scoring is right, and
    calling it "not measured" is also right, but they are different statements
    from "the provider died", so the report separates all three.
    """
    report = _report(["a"], {"a": _result("a", authority="degraded_judge_only")})
    payload = report.to_dict()
    assert payload["coverage"]["by_status"]["degraded"] == 1
    assert payload["coverage"]["measured"] == 0
    assert "a" in payload["coverage"]["not_measured_cases"]


def test_cost_comes_from_the_rows_not_from_a_side_total():
    rows = [
        {"case": "a", "elapsed_seconds": 10.0,
         "result": {**_result("a"), "execution": {
             "provider_attempts": 3, "usage_missing_attempts": 0,
             "usage": {"total_tokens": 100}}}},
        {"case": "b", "elapsed_seconds": 90.0,
         "result": {**_result("b"), "execution": {
             "provider_attempts": 3, "usage_missing_attempts": 0,
             "usage": {"total_tokens": 200}}}},
    ]
    report = StageReport(stage="baseline", arm="empty",
                         requested=["a", "b"], rows=rows, wall_seconds=120.0)
    cost = report.cost()
    assert cost["case_seconds"] == 100.0
    assert cost["provider_attempts"] == 6
    assert cost["usage"]["total_tokens"] == 300
    assert cost["slowest_cases"][0]["case"] == "b", (
        "the long tail must be visible by name, not only as a total"
    )


def test_summarize_stage_records_the_unstarted_cases_and_says_the_sample_is_partial():
    requested = ["a", "b", "c"]
    batch = {
        "stop": {"error_type": "authentication_error", "message": "http 401"},
        "measurement": {
            "attempt_id": "001", "attempts": 1,
            "not_started_cases": ["c"], "peak_provider_inflight": 2,
            "deadline_seconds": None,
        },
    }
    payload = summarize_stage(
        stage="baseline", arm="empty", requested=requested,
        batch_summary=batch,
        rows=[{"case": "a", "result": _result("a"), "elapsed_seconds": 1.0},
              {"case": "b", "result": _result("b"), "elapsed_seconds": 1.0}],
        wall_seconds=2.0,
    )
    assert payload["batch"]["not_started_cases"] == ["c"]
    assert any("partial" in note or "prefix" in note for note in payload["notes"])
    assert payload["stop"]["error_type"] == "authentication_error"


# --------------------------------------------------------------------------
# writing the report must not change the reading
# --------------------------------------------------------------------------


def test_report_writing_is_json_round_trippable(tmp_path):
    report = _report(["a", "b"], {
        "a": _result("a"),
        "b": _result("b", state="unresolved", authority="invalid", reasons=_outage()),
    })
    payload = report.to_dict()
    path = tmp_path / "report.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    again = json.loads(path.read_text(encoding="utf-8"))
    assert again["coverage"] == payload["coverage"]


@pytest.fixture
def measured_runs(tmp_path):
    plan = load_plan()
    cases = ["Synthetic/a.json", "Synthetic/b.json", "Synthetic/c.json"]
    plan = replace(plan, measurement=replace(plan.measurement, cases=cases))
    runs = tmp_path / "runs"
    data = tmp_path / "data"
    for case in cases:
        record = data / case
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"question": "2+2", "history": [], "is_hallucination": False}), encoding="utf-8")
    for arm, measured in (("g0", cases[:2]), ("g0_roll2", cases[1:])):
        for case in measured:
            record_case(runs_root=runs, run_id=arm, case_id=case,
                        input_record={"question": "2+2", "history": []},
                        result=_result(case), elapsed_seconds=0)
    return plan, runs, data


# --------------------------------------------------------------------------
# pairing is decided by measurement, and the bias is stated
# --------------------------------------------------------------------------


def test_paired_sample_uses_only_cases_both_arms_measured(measured_runs):
    """Pairing requires the same case on both sides, by construction."""
    plan, runs, _ = measured_runs
    sample = paired_sample(plan, runs,
                           ["g0", "g0_roll2"])
    assert sample["size"] > 0
    assert sample["size"] <= min(sample["arms"].values())
    assert all(case in plan.measurement.cases for case in sample["common"])


def test_paired_sample_says_what_conditioning_on_measurement_costs(measured_runs):
    """The common set is difficulty-biased; the report must not hide that."""
    plan, runs, _ = measured_runs
    sample = paired_sample(plan, runs,
                           ["g0", "g0_roll2"])
    assert "biased" in sample["note"]
    assert "not by their verdicts" in sample["note"]


def test_plan_status_lists_the_arms_that_do_not_exist_yet(measured_runs):
    plan, runs, _ = measured_runs
    status = plan_status(plan, runs)
    assert status["arms"]["g0"]["exists"] is True
    assert status["arms"]["g1"]["exists"] is False
    assert status["shared_prefix"] is not None


# --------------------------------------------------------------------------
# two rolls of one arm are not an effect
# --------------------------------------------------------------------------


def test_two_rolls_of_one_registry_are_recognised_as_noise():
    """A paired delta between identical configurations is the instrument.

    Measured on the real trees: ``g0`` and ``g0_roll2`` are both the empty
    registry, and pairing them gives 2 fixed against 1 broken. Quoted as an
    effect that reads like a finding; it is jitter, and the terminal criterion
    needs five net corrections before it means anything at all.
    """
    from experiments.runner import _shared_registry

    plan = load_plan()
    assert _shared_registry(plan, ["g0", "g0_roll2"]) is True
    assert _shared_registry(plan, ["g0", "g1"]) is False


def test_the_paired_command_labels_a_same_registry_pair_as_noise(measured_runs, monkeypatch):
    """The CLI must not print an effect verdict it is not entitled to."""
    from experiments.__main__ import main
    import io
    import contextlib

    plan, runs, data = measured_runs
    monkeypatch.setattr("experiments.__main__.load_plan", lambda **_: plan)
    from experiments.runner import _shared_registry
    assert _shared_registry(plan, ["g0", "g0_roll2"]) is True
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(["paired", "--arm", "g0", "--arm", "g0_roll2",
                     "--runs-root", str(runs), "--dataset-root", str(data)])
    assert code == 0
    payload = json.loads(buffer.getvalue())
    assert payload["reading"] == "noise_floor"
    assert payload["effect_established"] is None
    assert payload["flips_on_stable_cases"]["fixed"] == payload["decisive_fixed"]
