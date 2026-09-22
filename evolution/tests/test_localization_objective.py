"""Localization as a first-class objective (optimization plan §5).

These tests lock the properties that make a localization claim different from a
binary one:

- the joint delta is counted over the frozen batch, so a case that stops being
  convicted cannot leave the denominator;
- breaking a localization the control arm already had right is charged, not
  netted out by new gains;
- the localization BEHAVIOR check requires an actual responsible-step change,
  so firing (or merely flipping a label) cannot satisfy a localization claim;
- ``target_kind`` is absent on older entries and means ``detection`` there.
"""

from __future__ import annotations

from evolution.gates import (
    candidate_target_kind,
    localization_behavior_gate,
    meets_localization,
    meets_m,
)
from evolution.metrics import (
    localization_metrics,
    localization_opportunity,
    paired_localization_delta,
)

GOLD_STEPS = {"A/001.json": 6, "B/001.json": 14, "C/001.json": 4}

CONFIG = {
    "gates": {
        "localization_min_joint_cases": 1,
        "localization_joint_broken_allowed": 0,
        "m_new_errors_allowed": 0,
        "behavior_require_fired": True,
    },
}


def _row(case: str, gold: bool, prediction, step, *, fired: bool = True,
         authority: str = "authoritative") -> dict:
    return {
        "case": case,
        "gold": gold,
        "prediction": prediction,
        "authority": authority,
        "responsible_step": step,
        "state": (
            "verified_error" if prediction is True
            else ("verified_clean" if prediction is False else "unresolved")
        ),
        "result": {
            "case_id": case,
            "state": "verified_error" if prediction is True else "verified_clean",
            "decision_authority": authority,
            "runtime_valid": True,
            "responsible_step": step,
            "proof": {"deployment": {"fired": (
                [{"intervention_id": "IV-L-001", "event": "text_injected"}]
                if fired else []
            )}},
            "stage_status": {},
        },
    }


# ---------------------------------------------------------------------------
def test_joint_delta_counts_over_the_frozen_batch():
    """A case that stops being convicted must not leave the denominator."""
    baseline = [_row("A/001.json", True, True, 6)]
    candidate = [_row("A/001.json", True, False, None)]
    delta = paired_localization_delta(baseline, candidate, gold_steps=GOLD_STEPS)
    assert delta["n"] == 1
    assert delta["localizable_n"] == 1
    assert delta["joint_delta"] == -1
    assert delta["joint_broken"] == 1
    assert delta["joint_improved"] == 0


def test_joint_delta_counts_a_correct_localization():
    baseline = [_row("B/001.json", True, True, 9)]
    candidate = [_row("B/001.json", True, True, 14)]
    delta = paired_localization_delta(baseline, candidate, gold_steps=GOLD_STEPS)
    assert delta["joint_delta"] == 1
    assert delta["joint_improved"] == 1
    assert delta["joint_broken"] == 0


def test_cases_without_gold_step_are_out_of_the_localization_batch():
    """An unannotated case is not a silent zero."""
    baseline = [_row("C/001.json", True, True, 1)]
    candidate = [_row("C/001.json", True, True, 2)]
    delta = paired_localization_delta(
        baseline, candidate, gold_steps={},
    )
    assert delta["n"] == 1
    assert delta["localizable_n"] == 0
    assert delta["joint_delta"] == 0


def test_clean_cases_never_enter_the_localization_batch():
    baseline = [_row("D/001.json", False, False, None)]
    candidate = [_row("D/001.json", False, False, None)]
    delta = paired_localization_delta(
        baseline, candidate, gold_steps={"D/001.json": 3},
    )
    assert delta["localizable_n"] == 0


def test_localization_opportunity_counts_only_improvable_cases():
    rows = [
        _row("A/001.json", True, True, 3),    # convicts, wrong step -> improvable
        _row("B/001.json", True, True, 14),   # convicts, right step -> preserved
        _row("E/001.json", True, False, None),  # misses -> neither
    ]
    opportunity = localization_opportunity(rows, gold_steps=GOLD_STEPS)
    assert opportunity["improvable"] == 1
    assert opportunity["preserved"] == 1


def test_behavior_gate_requires_a_real_step_change():
    candidate = {"intervention_id": "IV-L-001", "claim": {"target_kind": "localization"}}
    # Fires, but no responsible step moves: not a localization claim met.
    flat_base = [_row("A/001.json", True, True, 3)]
    flat_cand = [_row("A/001.json", True, True, 3)]
    verdict = localization_behavior_gate(
        candidate, baseline_rows=flat_base, candidate_rows=flat_cand,
        config=CONFIG, gold_steps=GOLD_STEPS,
    )
    assert verdict["passed"] is False
    assert "no responsible step was corrected" in verdict["reason"]


def test_behavior_gate_rejects_an_intervention_that_never_fires():
    candidate = {"intervention_id": "IV-L-001", "claim": {"target_kind": "localization"}}
    base = [_row("A/001.json", True, True, 3)]
    cand = [_row("A/001.json", True, True, 6, fired=False)]
    verdict = localization_behavior_gate(
        candidate, baseline_rows=base, candidate_rows=cand,
        config=CONFIG, gold_steps=GOLD_STEPS,
    )
    assert verdict["passed"] is False
    assert "never fired" in verdict["reason"]


def test_behavior_gate_passes_on_a_corrected_step():
    candidate = {"intervention_id": "IV-L-001", "claim": {"target_kind": "localization"}}
    base = [_row("A/001.json", True, True, 3)]
    cand = [_row("A/001.json", True, True, 6)]
    verdict = localization_behavior_gate(
        candidate, baseline_rows=base, candidate_rows=cand,
        config=CONFIG, gold_steps=GOLD_STEPS,
    )
    assert verdict["passed"] is True


def test_behavior_gate_reports_not_evaluated_without_annotations():
    candidate = {"intervention_id": "IV-L-001", "claim": {"target_kind": "localization"}}
    base = [_row("A/001.json", True, True, 3)]
    cand = [_row("A/001.json", True, True, 6)]
    verdict = localization_behavior_gate(
        candidate, baseline_rows=base, candidate_rows=cand,
        config=CONFIG, gold_steps={},
    )
    assert verdict["passed"] is False
    assert verdict["evaluable"] is False
    assert verdict["status"] == "not_evaluated"


def test_meets_localization_requires_positive_joint_delta():
    delta = {"joint_delta": 0, "joint_broken": 0, "n": 2, "localizable_n": 2}
    passed, failures = meets_localization(delta, config=CONFIG)
    assert passed is False
    assert any("joint_delta" in item for item in failures)


def test_meets_localization_protects_detection_health():
    delta = {"joint_delta": 2, "joint_broken": 0, "n": 4, "localizable_n": 2}
    detection = {
        "marginal_cases": 2, "new_errors": 1, "unresolved_added": 0,
        "health_regressed": 0,
    }
    passed, failures = meets_localization(
        delta, config=CONFIG, detection_delta=detection,
    )
    assert passed is False
    assert any("new_errors" in item for item in failures)


def test_meets_localization_joint_kind_accepts_a_detection_side_gain():
    """A joint claim needs one objective to improve and neither to regress."""
    delta = {"joint_delta": 0, "joint_broken": 0, "n": 4, "localizable_n": 2}
    detection = {
        "marginal_cases": 1, "new_errors": 0, "unresolved_added": 0,
        "health_regressed": 0,
    }
    passed, failures = meets_localization(
        delta, config=CONFIG, detection_delta=detection, target_kind="joint",
    )
    assert passed is True, failures


def test_meets_localization_joint_kind_rejects_two_flat_objectives():
    delta = {"joint_delta": 0, "joint_broken": 0, "n": 4, "localizable_n": 2}
    detection = {
        "marginal_cases": 0, "new_errors": 0, "unresolved_added": 0,
        "health_regressed": 0,
    }
    passed, failures = meets_localization(
        delta, config=CONFIG, detection_delta=detection, target_kind="joint",
    )
    assert passed is False
    assert any("neither objective" in item for item in failures)


def test_detection_objective_is_unchanged_by_the_new_gate():
    """The original M rule must keep its exact meaning."""
    delta = {
        "marginal_cases": 1, "new_errors": 0, "unresolved_added": 0,
        "health_regressed": 0,
    }
    passed, failures = meets_m(delta, config=CONFIG)
    assert passed is True and failures == []


def test_target_kind_absent_means_detection():
    assert candidate_target_kind({"claim": {}}) == "detection"
    assert candidate_target_kind({"claim": {"target_kind": "localization"}}) == (
        "localization"
    )


def test_localization_metrics_reports_both_readings():
    rows = [
        _row("A/001.json", True, True, 6),   # detected, right step
        _row("B/001.json", True, True, 9),   # detected, wrong step
        _row("C/001.json", True, False, None),  # missed
    ]
    metrics = localization_metrics(rows, gold_steps=GOLD_STEPS)
    assert metrics["annotated_positive"] == 3
    assert metrics["authoritative_detected"] == 2
    assert metrics["joint_hits"] == 1
    assert metrics["joint_accuracy"] == 1 / 3
    assert metrics["step_accuracy_on_detected"] == 0.5


def test_validate_intervention_accepts_and_rejects_target_kind():
    import pytest

    from evolution.contracts import make_intervention

    entry = make_intervention(
        intervention_id="IV-K-001",
        intervention_type="text_boundary",
        hook="judge.system",
        packet_features=["has_tool_calls"],
        body={"directive": "d", "not_when": "n"},
        expected_behavior="e",
        target_polarity="recall",
        cycle=1,
        failure_family="FAM-fn-x",
        failure_cases=["A/001.json", "B/001.json"],
    )
    assert "target_kind" not in entry["claim"]
    entry["claim"]["target_kind"] = "localization"
    from detector.interventions import (
        intervention_content_hash,
        validate_intervention,
    )

    entry["content_hash"] = intervention_content_hash(entry)
    validate_intervention(entry)
    entry["claim"]["target_kind"] = "vibes"
    entry["content_hash"] = intervention_content_hash(entry)
    with pytest.raises(ValueError) as exc:
        validate_intervention(entry)
    assert "target_kind" in str(exc.value)
