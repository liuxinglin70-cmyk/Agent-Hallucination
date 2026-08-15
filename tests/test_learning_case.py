"""Learning Case validation: fact -> hypothesis -> capability ordering."""

from __future__ import annotations

from sehd_rise2.evolution import gap_taxonomy
from sehd_rise2.evolution.learning_case import (
    reclassify,
    validate_annotations,
    validate_learning_case,
)


def _case(
    *, gold=True, state="verified_clean", gold_step=3, responsible_step=None,
    hypothesis=None, capability=None, override=None, evidence_gap="missing",
    root_cause="agent skipped a required call",
):
    return {
        "schema_version": "sehd2.learning_case.v1",
        "case_id": "BFCL/067.json",
        "source": {"run_id": "r", "dataset": "d", "family": "BFCL"},
        "input": {
            "trajectory_summary": "3 events",
            "task_type": "BFCL",
            "agent_behavior": "answered directly",
        },
        "ground_truth": {
            "hallucination_label": gold,
            "hallucination_type": "Tool-Use / Missing Required Call",
            "gold_step": gold_step,
            "root_cause": root_cause,
        },
        "detector_execution": {
            "final_decision": state,
            "responsible_step": responsible_step,
            "census": [],
            "verification": {"certificates": []},
            "defense": {"decisions": {}},
            "decisive_facts": {
                "candidate_count": 0,
                "supported_certificate_count": 0,
                "error_certificate_count": 0,
            },
        },
        "failure_analysis": {
            "failure_type": None,
            "failed_stage": "census",
            "failed_stage_source": (
                "human_override" if override else "mechanical"
            ),
            "override_reason": override,
            "failure_observation": (
                "census proposed no candidate; the gold error never "
                "entered the evidence pipeline"
            ),
            "missed_candidates": [],
            "evidence_gap": evidence_gap,
        },
        "failure_hypothesis": hypothesis,
        "capability_analysis": capability,
    }


_HYPOTHESIS = {
    "initial_hypothesis": "census lacked required-action awareness",
    "hypothesis_basis": "gold step 3 skipped lockDoors; no candidate named it",
}
_CAPABILITY = {
    "capability_gap": {
        "category": "observation",
        "name": "missing_required_action",
        "status": "known",
    },
    "generalization": "cross_case",
    "proposed_intervention": "required-action awareness",
}


def test_fn_case_valid():
    case = _case(
        hypothesis=_HYPOTHESIS, capability=_CAPABILITY,
    )
    case["failure_analysis"]["failure_type"] = reclassify(case)
    checks = validate_learning_case(case)
    assert all(checks.values()), checks


def test_correct_case_must_not_carry_analysis_layers():
    case = _case(
        gold=True, state="verified_error", responsible_step=3,
        hypothesis=_HYPOTHESIS, capability=_CAPABILITY,
    )
    case["failure_analysis"]["failure_type"] = reclassify(case)
    checks = validate_learning_case(case)
    assert checks["hypothesis_layer_ordered"] is False
    assert checks["capability_layer_ordered"] is False
    stripped = dict(
        case, failure_hypothesis=None, capability_analysis=None,
    )
    assert all(validate_learning_case(stripped).values())


def test_override_requires_reason():
    case = _case(
        hypothesis=_HYPOTHESIS, capability=_CAPABILITY, override=None,
    )
    case["failure_analysis"]["failure_type"] = reclassify(case)
    case["failure_analysis"]["failed_stage_source"] = "human_override"
    checks = validate_learning_case(case)
    assert checks["override_reason_present_when_overridden"] is False


def test_proposed_gap_name_rejected_when_frozen(monkeypatch):
    proposed = dict(
        _CAPABILITY,
        capability_gap={
            "category": "observation",
            "name": "novel_shape",
            "status": "proposed",
        },
    )
    case = _case(hypothesis=_HYPOTHESIS, capability=proposed)
    case["failure_analysis"]["failure_type"] = reclassify(case)
    assert not validate_learning_case(case)["capability_name_vocabulary"]
    monkeypatch.setattr(gap_taxonomy, "TAXONOMY_FROZEN", False)
    assert validate_learning_case(case)["capability_name_vocabulary"]


def test_failure_type_must_match_gold_polarity():
    case = _case(hypothesis=_HYPOTHESIS, capability=_CAPABILITY)
    case["failure_analysis"]["failure_type"] = "FP"
    checks = validate_learning_case(case)
    assert checks["failure_type_consistent"] is False


def test_annotations_envelope():
    valid = {
        "schema_version": "sehd2.learning_annotations.v1",
        "annotator": "agent-first-pass",
        "review_status": "first_pass",
        "cases": {"BFCL/067.json": {}},
    }
    assert all(validate_annotations(valid).values())
    invalid = dict(valid, review_status="vibes")
    assert not all(validate_annotations(invalid).values())
