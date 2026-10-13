"""One contract for "healthy undecided" across gates, metrics and retirement.

The framework distinguishes two ways a row can read ``prediction is None``:
a healthy semantic-unresolved reading (the judge genuinely could not decide)
and an infrastructure failure (the provider died, a stage broke, the envelope
is invalid). Three defects made that distinction inconsistent:

* ``arm_has_infrastructure_failure`` treated *every* ``None`` as
  infrastructure, so a resolution claim -- whose control arm necessarily
  contains healthy undecided rows -- could never be scored;
* the resolution gates counted raw ``U->T`` / ``U->F`` flips, so a control-arm
  outage the candidate papered over read as a resolution;
* opportunity, attribution and retirement read binary ``marginal_cases`` for
  every objective, so a localization or resolution member whose benefit lives
  off the binary scale recorded zeros and was retired.

These tests pin each half of the single reading.
"""

from __future__ import annotations

from evolution.gates import (
    arm_has_infrastructure_failure,
    meets_resolution,
    resolution_behavior_gate,
)
from evolution.metrics import (
    gain_opportunity,
    objective_marginal,
    paired_delta,
    resolution_gain,
    target_opportunity,
    target_outcome,
)

CONFIG = {"gates": {}}


def _row(case, *, gold, prediction, authority="authoritative",
         runtime_valid=True, state=None, fired=None, step=None):
    if state is None:
        state = (
            "verified_error" if prediction is True
            else "verified_clean" if prediction is False
            else "unresolved"
        )
    return {
        "case": case,
        "gold": gold,
        "prediction": prediction,
        "authority": authority,
        "state": state,
        "responsible_step": step,
        "result": {
            "schema_version": "detector.detection_result.v1",
            "case_id": case,
            "state": state,
            "occurrence_state": state,
            "decision_authority": authority,
            "runtime_valid": runtime_valid,
            "stage_status": {},
            "responsible_step": step,
            "proof": {
                "deployment": {
                    "fired": [
                        {"intervention_id": item, "event": "text_injected"}
                        for item in (fired or ())
                    ],
                },
            },
        },
    }


def _healthy_unresolved(case, gold):
    return _row(case, gold=gold, prediction=None)


def _outage_unresolved(case, gold):
    return _row(case, gold=gold, prediction=None, authority="invalid",
                runtime_valid=False)


def _decided(case, gold, *, fired=()):
    return _row(case, gold=gold, prediction=gold, fired=fired)


class TestHealthyUndecidedIsNotAnOutage:
    def test_authoritative_unresolved_row_is_healthy(self):
        rows = [_healthy_unresolved("A/1.json", True)]
        assert arm_has_infrastructure_failure(rows) == ""

    def test_non_authoritative_unresolved_row_is_still_refused(self):
        rows = [_outage_unresolved("A/1.json", True)]
        assert arm_has_infrastructure_failure(rows)

    def test_runtime_invalid_unresolved_row_is_still_refused(self):
        rows = [_row("A/1.json", gold=True, prediction=None, runtime_valid=False)]
        assert arm_has_infrastructure_failure(rows)

    def test_failed_stage_is_still_refused(self):
        row = _row("A/1.json", gold=True, prediction=True)
        row["result"]["stage_status"] = {"judge": {"state": "provider_error"}}
        assert arm_has_infrastructure_failure([row])


class TestResolutionOpportunityAndGain:
    def test_opportunity_counts_only_healthy_undecided_rows(self):
        read = gain_opportunity([
            _healthy_unresolved("A/1.json", True),
            _outage_unresolved("A/2.json", False),
            _decided("A/3.json", True),
        ])
        assert read["resolution"] == 1

    def test_target_opportunity_reads_the_resolution_count(self):
        detection = gain_opportunity([_healthy_unresolved("A/1.json", True)])
        assert target_opportunity(
            target_kind="resolution", polarity="recall", detection=detection,
        ) == 1
        assert target_opportunity(
            target_kind="resolution", polarity="boundary", detection=detection,
        ) == 1

    def test_resolution_gain_requires_a_healthy_control_row(self):
        healthy = paired_delta(
            [_healthy_unresolved("A/1.json", True)],
            [_decided("A/1.json", True)],
        )
        assert resolution_gain(healthy) == 1
        sick = paired_delta(
            [_outage_unresolved("A/1.json", True)],
            [_decided("A/1.json", True)],
        )
        assert sick["flips"]["U->T"] == 1
        assert resolution_gain(sick) == 0

    def test_target_outcome_separates_healthy_resolution_from_outage(self):
        healthy = paired_delta(
            [_healthy_unresolved("A/1.json", True)],
            [_decided("A/1.json", True)],
        )
        outcome = target_outcome(
            target_kind="resolution", polarity="recall",
            delta_basis="benefit_set", detection_delta=healthy,
        )
        assert outcome["measurable"] is True
        assert outcome["target_gain"] == 1
        assert outcome["status"] == "improved"

        sick = paired_delta(
            [_outage_unresolved("A/1.json", True)],
            [_decided("A/1.json", True)],
        )
        outcome = target_outcome(
            target_kind="resolution", polarity="recall",
            delta_basis="benefit_set", detection_delta=sick,
        )
        assert outcome["target_gain"] == 0
        assert outcome["status"] != "improved"


class TestResolutionGatesUseTheHealthyReading:
    def test_meets_resolution_refuses_a_papered_over_outage(self):
        delta = paired_delta(
            [_outage_unresolved("A/1.json", True)],
            [_decided("A/1.json", True)],
        )
        passed, failures = meets_resolution(delta, config=CONFIG)
        assert passed is False
        assert any("resolved_correct=0" in reason for reason in failures)

    def test_meets_resolution_accepts_a_healthy_resolution(self):
        delta = paired_delta(
            [_healthy_unresolved("A/1.json", True)],
            [_decided("A/1.json", True)],
        )
        passed, failures = meets_resolution(delta, config=CONFIG)
        assert passed is True, failures

    def test_behavior_gate_counts_only_healthy_resolutions(self):
        candidate = {
            "intervention_id": "IV-RES-001",
            "type": "text_boundary",
            "claim": {"target_kind": "resolution", "target_polarity": "recall"},
        }
        delta_rows = (
            [_outage_unresolved("A/1.json", True)],
            [_decided("A/1.json", True, fired=["IV-RES-001"])],
        )
        verdict = resolution_behavior_gate(
            candidate, baseline_rows=delta_rows[0],
            candidate_rows=delta_rows[1], config=CONFIG,
        )
        assert verdict["passed"] is False

        healthy_rows = (
            [_healthy_unresolved("A/1.json", True)],
            [_decided("A/1.json", True, fired=["IV-RES-001"])],
        )
        verdict = resolution_behavior_gate(
            candidate, baseline_rows=healthy_rows[0],
            candidate_rows=healthy_rows[1], config=CONFIG,
        )
        assert verdict["passed"] is True, verdict


class TestObjectiveMarginal:
    def test_localization_reads_joint_movement_not_binary_marginal(self):
        delta = paired_delta(
            [_row("A/1.json", gold=True, prediction=True, step=1)],
            [_row("A/1.json", gold=True, prediction=True, step=3)],
        )
        assert delta["marginal_cases"] == 0
        localization = {"joint_delta": 1}
        assert objective_marginal(
            target_kind="localization", delta=delta,
            localization_delta=localization,
        ) == 1

    def test_resolution_reads_healthy_resolutions(self):
        delta = paired_delta(
            [_healthy_unresolved("A/1.json", True)],
            [_decided("A/1.json", True)],
        )
        assert objective_marginal(target_kind="resolution", delta=delta) == 1

    def test_detection_keeps_the_binary_reading(self):
        delta = paired_delta(
            [_row("A/1.json", gold=True, prediction=False)],
            [_decided("A/1.json", True)],
        )
        assert objective_marginal(target_kind="detection", delta=delta) == 1
