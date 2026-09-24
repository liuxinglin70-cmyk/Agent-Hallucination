"""Tests for evaluation-side row-state classification (EvaluationState)."""

from __future__ import annotations

from detector.result import build_detection_output
from evolution.metrics import (
    EvaluationState,
    evaluate_row_state,
    mechanism_opportunity,
    paired_delta,
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


def test_paired_delta_counts_decisive_fixes():
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
    delta = paired_delta(baseline, active)
    assert delta["marginal_cases"] == 2
    assert delta["new_errors"] == 0


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
