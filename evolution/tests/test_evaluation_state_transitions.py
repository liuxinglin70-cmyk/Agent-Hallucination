"""Tests for evaluation-side state transitions and resolution mechanisms (PR-04 / C07, C08)."""

from __future__ import annotations

import pytest

from detector.result import build_detection_output
from evolution.metrics import (
    EvaluationState,
    classify_transition,
    evaluate_row_state,
    mechanism_opportunity,
    paired_delta,
    paired_state_transitions_v1,
)


def _result_envelope(
    *,
    case_id: str = "case-1",
    state: str = "verified_error",
    authority: str = "authoritative",
    runtime_valid: bool = True,
    stage_error: str | None = None,
) -> dict:
    judge_state = "authoritative" if not stage_error else stage_error
    return build_detection_output(
        case_id=case_id,
        packet={"events": [{"event_id": "e1", "kind": "content", "tool_calls": []}]},
        proof={
            "state": state,
            "decision_authority": authority,
            "runtime_valid": runtime_valid,
            "authority_failure_reasons": (
                [{"error_type": stage_error, "code": stage_error}] if stage_error else []
            ),
        },
        stage_status={
            "judge": {"view": authority, "state": judge_state},
            "defense": {"view": authority, "state": "authoritative"},
        },
        providers={},
        provenance={"test": True},
    )


def test_evaluation_state_constants():
    expected = {
        "authoritative_correct",
        "authoritative_wrong",
        "semantic_unresolved_healthy",
        "degraded",
        "infrastructure_failure",
        "fatal_provider",
        "not_started",
        "invalid_envelope",
    }
    assert EvaluationState.ALL == expected
    for state in expected:
        assert getattr(EvaluationState, state.upper()) == state


def test_evaluate_row_state_lightweight():
    # Authoritative correct & wrong
    assert evaluate_row_state({"case": "c1", "gold": True, "prediction": True}) == EvaluationState.AUTHORITATIVE_CORRECT
    assert evaluate_row_state({"case": "c2", "gold": False, "prediction": False}) == EvaluationState.AUTHORITATIVE_CORRECT
    assert evaluate_row_state({"case": "c3", "gold": True, "prediction": False}) == EvaluationState.AUTHORITATIVE_WRONG
    assert evaluate_row_state({"case": "c4", "gold": False, "prediction": True}) == EvaluationState.AUTHORITATIVE_WRONG

    # Semantic unresolved healthy
    assert evaluate_row_state({"case": "c5", "gold": True, "prediction": None}) == EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY
    assert evaluate_row_state({"case": "c6", "gold": False, "prediction": None, "state": "unresolved"}) == EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY

    # Degraded
    assert evaluate_row_state({"case": "c7", "gold": True, "prediction": True, "authority": "degraded_pre"}) == EvaluationState.DEGRADED

    # Infrastructure failure & fatal provider
    assert evaluate_row_state({"case": "c8", "gold": True, "prediction": None, "measurement_status": "infrastructure_failure"}) == EvaluationState.INFRASTRUCTURE_FAILURE
    assert evaluate_row_state({"case": "c9", "gold": True, "prediction": None, "measurement_status": "fatal_provider"}) == EvaluationState.FATAL_PROVIDER
    assert evaluate_row_state({"case": "c10", "gold": True, "prediction": None, "error_type": "batch_deadline_exceeded"}) == EvaluationState.INFRASTRUCTURE_FAILURE

    # Not started & invalid
    assert evaluate_row_state({"case": "c11", "state": "not_started"}) == EvaluationState.NOT_STARTED
    assert evaluate_row_state("not_a_mapping") == EvaluationState.INVALID_ENVELOPE
    assert evaluate_row_state({"case": "c12", "prediction": "invalid_bool"}) == EvaluationState.INVALID_ENVELOPE

    # Explicit override
    assert evaluate_row_state({"case": "c13", "evaluation_state": "degraded"}) == EvaluationState.DEGRADED


def test_evaluate_row_state_with_result_envelope():
    # Authoritative correct from envelope
    corr_env = _result_envelope(state="verified_error", authority="authoritative")
    assert evaluate_row_state({"case": "c1", "gold": True, "result": corr_env}) == EvaluationState.AUTHORITATIVE_CORRECT
    assert evaluate_row_state({"case": "c1", "gold": False, "result": corr_env}) == EvaluationState.AUTHORITATIVE_WRONG

    # Semantic unresolved healthy from envelope
    unres_env = _result_envelope(state="unresolved", authority="unresolved")
    assert evaluate_row_state({"case": "c2", "gold": True, "result": unres_env}) == EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY

    # Infrastructure failure in stage
    fail_env = _result_envelope(state="unresolved", authority="unresolved", stage_error="provider_error")
    assert evaluate_row_state({"case": "c3", "gold": True, "result": fail_env}) == EvaluationState.INFRASTRUCTURE_FAILURE

    # Degraded authority
    deg_env = _result_envelope(state="verified_clean", authority="degraded_pre")
    assert evaluate_row_state({"case": "c4", "gold": False, "result": deg_env}) == EvaluationState.DEGRADED


def test_classify_transition():
    # Core Plan §7.2 transitions
    assert classify_transition(EvaluationState.AUTHORITATIVE_WRONG, EvaluationState.AUTHORITATIVE_CORRECT) == "decisive_fixed"
    assert classify_transition(EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY, EvaluationState.AUTHORITATIVE_CORRECT) == "resolution_correct"
    assert classify_transition(EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY, EvaluationState.AUTHORITATIVE_WRONG) == "resolution_wrong"
    assert classify_transition(EvaluationState.AUTHORITATIVE_CORRECT, EvaluationState.AUTHORITATIVE_WRONG) == "correctness_worsened"
    assert classify_transition(EvaluationState.AUTHORITATIVE_CORRECT, EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY) == "unresolved_added"
    assert classify_transition(EvaluationState.AUTHORITATIVE_WRONG, EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY) == "wrong_to_abstain"
    assert classify_transition(EvaluationState.AUTHORITATIVE_WRONG, EvaluationState.AUTHORITATIVE_WRONG) == "still_wrong"
    assert classify_transition(EvaluationState.AUTHORITATIVE_CORRECT, EvaluationState.AUTHORITATIVE_CORRECT) == "still_correct"
    assert classify_transition(EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY, EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY) == "still_unresolved"

    # Infrastructure transitions
    assert classify_transition(EvaluationState.AUTHORITATIVE_CORRECT, EvaluationState.INFRASTRUCTURE_FAILURE) == "health_regressed"
    assert classify_transition(EvaluationState.INFRASTRUCTURE_FAILURE, EvaluationState.AUTHORITATIVE_CORRECT) == "service_recovery"
    assert classify_transition(EvaluationState.INFRASTRUCTURE_FAILURE, EvaluationState.INFRASTRUCTURE_FAILURE) == "still_infrastructure_failure"

    # Degraded transitions
    assert classify_transition(EvaluationState.AUTHORITATIVE_CORRECT, EvaluationState.DEGRADED) == "degraded_added"
    assert classify_transition(EvaluationState.DEGRADED, EvaluationState.AUTHORITATIVE_CORRECT) == "degraded_recovered"


def test_paired_state_transitions_v1_accounting():
    baseline = [
        {"case": "c1", "gold": True, "prediction": False},               # authoritative_wrong -> fixed
        {"case": "c2", "gold": True, "prediction": None},                # unresolved -> resolution_correct (positive)
        {"case": "c3", "gold": False, "prediction": None},               # unresolved -> resolution_wrong (FP)
        {"case": "c4", "gold": True, "prediction": True},                # authoritative_correct -> worsened (FN)
        {"case": "c5", "gold": False, "prediction": False},              # authoritative_correct -> unresolved_added
        {"case": "c6", "gold": True, "prediction": False},               # authoritative_wrong -> abstain
        {"case": "c7", "gold": True, "prediction": True},                # still_correct
        {"case": "c8", "gold": False, "prediction": False},              # correct -> health_regressed
    ]
    active = [
        {"case": "c1", "gold": True, "prediction": True},                # decisive_fixed
        {"case": "c2", "gold": True, "prediction": True},                # resolution_correct
        {"case": "c3", "gold": False, "prediction": True},               # resolution_wrong
        {"case": "c4", "gold": True, "prediction": False},               # correctness_worsened (new_fn)
        {"case": "c5", "gold": False, "prediction": None},               # unresolved_added
        {"case": "c6", "gold": True, "prediction": None},                # wrong_to_abstain
        {"case": "c7", "gold": True, "prediction": True},                # still_correct
        {"case": "c8", "gold": False, "prediction": None, "measurement_status": "infrastructure_failure"},  # health_regressed
    ]

    report = paired_state_transitions_v1(baseline, active)
    assert report["n"] == 8
    summary = report["summary"]
    assert summary["decisive_fixed"] == 1
    assert summary["resolution_correct"] == 1
    assert summary["resolution_correct_positive"] == 1
    assert summary["resolution_correct_negative"] == 0
    assert summary["resolution_wrong"] == 1
    assert summary["resolution_wrong_fp"] == 1
    assert summary["resolution_wrong_fn"] == 0
    assert summary["correctness_worsened"] == 1
    assert summary["new_fn"] == 1
    assert summary["new_fp"] == 0
    assert summary["unresolved_added"] == 1
    assert summary["wrong_to_abstain"] == 1
    assert summary["still_correct"] == 1
    assert summary["health_regressed"] == 1

    # Matrix integrity
    assert report["matrix"][EvaluationState.AUTHORITATIVE_WRONG][EvaluationState.AUTHORITATIVE_CORRECT] == 1
    assert report["matrix"][EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY][EvaluationState.AUTHORITATIVE_CORRECT] == 1


def test_paired_state_transitions_v1_validation_errors():
    baseline = [{"case": "c1", "gold": True, "prediction": True}]
    # Duplicate case
    with pytest.raises(ValueError, match="duplicate active case"):
        paired_state_transitions_v1(
            baseline,
            [
                {"case": "c1", "gold": True, "prediction": True},
                {"case": "c1", "gold": True, "prediction": True},
            ],
        )

    # Unexpected active case
    with pytest.raises(ValueError, match="unexpected active case"):
        paired_state_transitions_v1(baseline, [{"case": "c2", "gold": True, "prediction": True}])

    # Missing active rows
    with pytest.raises(ValueError, match="missing active rows"):
        paired_state_transitions_v1(
            [
                {"case": "c1", "gold": True, "prediction": True},
                {"case": "c2", "gold": True, "prediction": True},
            ],
            [{"case": "c1", "gold": True, "prediction": True}],
        )

    # Gold mismatch
    with pytest.raises(ValueError, match="gold mismatch"):
        paired_state_transitions_v1(
            baseline,
            [{"case": "c1", "gold": False, "prediction": True}],
        )


def test_paired_delta_invariance():
    baseline = [
        {"case": "c1", "gold": True, "prediction": False},
        {"case": "c2", "gold": False, "prediction": True},
        {"case": "c3", "gold": True, "prediction": True},
    ]
    active = [
        {"case": "c1", "gold": True, "prediction": True},
        {"case": "c2", "gold": False, "prediction": False},
        {"case": "c3", "gold": True, "prediction": True},
    ]
    # Existing paired_delta works exactly as before
    delta = paired_delta(baseline, active)
    assert delta["marginal_cases"] == 2
    assert delta["new_errors"] == 0

    transitions = paired_state_transitions_v1(baseline, active)
    assert transitions["summary"]["decisive_fixed"] == 2
    assert transitions["summary"]["correctness_worsened"] == 0


def test_mechanism_opportunity_with_resolution_objective():
    members = {
        "c1": {"mechanism_id": "M-RES-1", "status": "confirmed", "evidence": ["e1"], "repairable": True},
        "c2": {"mechanism_id": "M-RES-1", "status": "confirmed", "evidence": ["e2"], "repairable": True},
        "c3": {"mechanism_id": "M-RES-1", "status": "confirmed", "evidence": ["e3"], "repairable": False},
    }
    mechanism = {
        "mechanism_id": "M-RES-1",
        "objective": "resolution",
        "target_polarity": "boundary",
        "status": "confirmed",
    }
    # c1 and c2 are healthy unresolved rows; c3 is not repairable
    rows = [
        {"case": "c1", "gold": True, "prediction": None},
        {"case": "c2", "gold": False, "prediction": None},
        {"case": "c3", "gold": True, "prediction": None},
    ]
    res = mechanism_opportunity(rows, mechanism, members)
    assert res["evaluable"] is True
    assert res["reason_code"] == "measurable"
    assert res["repairable_errors"] == 2
    assert res["opportunity_cases"] == ["c1", "c2"]
    assert res["ineligible"] == 1
