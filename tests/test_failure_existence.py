"""P0 acceptance: failure existence and repairability are two distinct
truths, derived from one canonical context everywhere.

- ``triggered_episode`` means "an active failure exists": REPAIRABLE,
  CAPABILITY_GAP, UNADDRESSABLE and FRAMEWORK_DEFECT all stay open; only a
  no-failure-shape NON_EVOLVABLE (or runtime-invalid) projection closes.
- The predicate derives evolution direction and the legal L1 module surface
  the same way the main loop does, so one result holds one repairability
  truth in every consumer.
- A constructive FN that raises candidate_count but leaves the verdict
  wrong never earns ``source_failure_closed`` / strong improvement.
"""

from __future__ import annotations

from typing import Mapping

from sehd_rise.causal.cut import build_causal_cut
from sehd_rise.causal.episode import build_causal_episode
from sehd_rise.causal.repairability import (
    RepairabilityStatus,
    analyze as analyze_repairability,
    triggered_episode,
)
from sehd_rise.causal.signals import project_detection
from sehd_rise.evaluation.fitness import assess_source_fitness


def _fn_clean_result() -> dict:
    """Gold says error, detector says clean, no candidate: the classic FN."""
    return {
        "state": "verified_clean",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "responsible_event_id": None,
        "responsible_step": None,
        "census": [],
        "prosecution": {"coverage_state": "complete", "candidates": []},
        "defense": {"coverage_state": "incomplete", "candidates": []},
        "proof_adjudication": {
            "state": "verified_clean",
            "prosecution_coverage": "complete",
            "defense_coverage": "incomplete",
            "surviving_error_certificate_ids": [],
            "valid_error_certificate_count": 0,
            "valid_defense_certificate_count": 0,
            "quarantined_error_certificate_ids": [],
            "quarantined_defense_certificate_ids": [],
            "consensus_gate_blocked_ids": [],
            "consensus_gate_applied": False,
            "error_audits": [],
            "defense_audits": [],
        },
    }


def _gap_result() -> dict:
    """A candidate was required but none was accepted: the missing-candidate
    capability gap (provider_artifact_missing has no registered L1 op)."""
    return {
        "state": "verified_clean",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "responsible_event_id": None,
        "responsible_step": None,
        "census": [
            {"candidate_id": "c1", "origin_hypothesis_event_id": "event_001"},
        ],
        "prosecution": {"coverage_state": "incomplete", "candidates": []},
        "defense": {"coverage_state": "incomplete", "candidates": []},
        "evidence_health": {
            "required_candidate_count": 1,
            "construct_accepted_candidate_count": 0,
            "construct_raw_candidate_count": 0,
        },
        "proof_adjudication": {
            "state": "verified_clean",
            "prosecution_coverage": "incomplete",
            "defense_coverage": "incomplete",
            "surviving_error_certificate_ids": [],
            "valid_error_certificate_count": 0,
            "valid_defense_certificate_count": 0,
            "quarantined_error_certificate_ids": [],
            "quarantined_defense_certificate_ids": [],
            "consensus_gate_blocked_ids": [],
            "consensus_gate_applied": False,
            "error_audits": [],
            "defense_audits": [],
        },
    }


def _verdict(result: dict) -> RepairabilityStatus:
    """Reproduce the main loop's exact context: direction + the cut's legal
    module surface, then the Repairability Authority on that episode."""
    from sehd_rise.causal.cut import allowed_l1_modules, infer_evolution_direction

    signal = project_detection(
        result, use_gold=bool(result.get("gold_label") is not None),
    )
    cut = build_causal_cut(signal)
    direction = infer_evolution_direction(signal.state, signal.gold_label)
    modules = allowed_l1_modules(
        signal, cut.owner,
        (
            cut.evidence.get("artifact_frontier", ())
            if isinstance(cut.evidence, Mapping) else ()
        ),
        cut.secondary_owner,
    )
    episode = build_causal_episode(
        signal=signal,
        cut=cut,
        record_key="c",
        evolution_direction=direction,
        allowed_modules=modules,
    )
    return analyze_repairability(
        episode,
        allowed_modules=(
            modules if cut.skill_layer == "l1_provider_rule" else ()
        ),
        evolution_direction=direction,
    )


def test_gold_fn_clean_is_repairable_and_triggered() -> None:
    result = {**_fn_clean_result(), "gold_label": True}
    verdict = _verdict(result)
    assert verdict.status is RepairabilityStatus.REPAIRABLE
    assert triggered_episode(result, use_gold=True, gold_label=True) is True


def test_missing_candidate_gap_is_an_active_failure() -> None:
    """A missing-candidate failure is route-addressable (design D2): it now
    compiles a route-guidance plan instead of terminating as CAPABILITY_GAP,
    and it stays an active failure — the trigger must NOT close."""
    result = _gap_result()
    verdict = _verdict(result)
    assert verdict.status is RepairabilityStatus.REPAIRABLE
    assert verdict.evidence["primary_executor"] == "l1_route_guidance"
    assert triggered_episode(result, use_gold=False, gold_label=None) is True


def test_construct_certificate_family_is_route_guidance_not_gap() -> None:
    """The 10d dominant gap: gold=error, detector=clean, candidate present,
    evidence_obligation owns a missing error_certificate. It must compile a
    route-guidance plan (the provider route is the intervention point), never
    terminate as CAPABILITY_GAP — the field can no longer be dropped at
    frontier compile time merely for lacking a deterministic opcode."""
    result = {**_fn_clean_result(), "gold_label": True}
    result["census"] = [
        {"candidate_id": "c1", "origin_hypothesis_event_id": "event_001"},
    ]
    result["prosecution"] = {"coverage_state": "complete", "candidates": []}
    result["proof_adjudication"]["prosecution_coverage"] = "complete"
    verdict = _verdict(result)
    assert verdict.status is RepairabilityStatus.REPAIRABLE
    assert verdict.evidence["primary_executor"] == "l1_route_guidance"
    assert verdict.evidence["primary_legal_operations"] == []


def test_gold_false_positive_is_an_active_failure_with_canonical_context() -> None:
    """The context fix: with the main-loop module surface an FP compiles its
    repair plan, so the replay truth matches the main-route truth."""
    result = {
        **_fn_clean_result(),
        "state": "verified_error",
        "responsible_event_id": "event_001",
        "responsible_step": 1,
        "gold_label": False,
    }
    result["proof_adjudication"]["state"] = "verified_error"
    result["proof_adjudication"]["surviving_error_certificate_ids"] = ["e1"]
    result["proof_adjudication"]["valid_error_certificate_count"] = 1
    verdict = _verdict(result)
    assert verdict.status is RepairabilityStatus.REPAIRABLE
    assert triggered_episode(result, use_gold=True, gold_label=False) is True


def test_fitness_never_credits_gap_migration_as_failure_closed() -> None:
    """The constructive FN credit bug: candidate_count 0->1 while the
    verdict stays wrong must stay out of strong/source_failure_closed."""
    before_result = {**_fn_clean_result(), "gold_label": True}
    after_result = {**_gap_result(), "gold_label": True}
    before = project_detection(before_result, use_gold=True)
    after = project_detection(after_result, use_gold=True)
    assessment = assess_source_fitness(
        before,
        after,
        target_module="candidate_trigger",
        before_result=before_result,
        after_result=after_result,
        before_trigger=True,
        after_trigger=True,  # gap: the failure still exists
        gold_label=True,
    )
    assert assessment.decision != "strong_improvement"
    assert "source_failure_closed" not in assessment.reasons


def test_fitness_still_credits_a_genuine_failure_closure() -> None:
    """The legitimate path stays: verdict fixed to the gold pole AND the
    cut finds no remaining failure shape earns a strong improvement."""
    before_result = {**_fn_clean_result(), "gold_label": True}
    after_result = {
        "state": "verified_error",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "responsible_event_id": "event_001",
        "responsible_step": 1,
        "census": [{"candidate_id": "c1", "origin_hypothesis_event_id": "event_001"}],
        "prosecution": {"coverage_state": "complete", "candidates": []},
        "defense": {"coverage_state": "complete", "candidates": []},
        "gold_label": True,
        "proof_adjudication": {
            "state": "verified_error",
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
            "surviving_error_certificate_ids": ["e1"],
            "valid_error_certificate_count": 1,
            "valid_defense_certificate_count": 1,
            "quarantined_error_certificate_ids": [],
            "quarantined_defense_certificate_ids": [],
            "consensus_gate_blocked_ids": [],
            "consensus_gate_applied": False,
            "error_audits": [],
            "defense_audits": [],
        },
    }
    before = project_detection(before_result, use_gold=True)
    after = project_detection(after_result, use_gold=True)
    assert triggered_episode(before_result, use_gold=True, gold_label=True) is True
    assert triggered_episode(after_result, use_gold=True, gold_label=True) is False
    assessment = assess_source_fitness(
        before,
        after,
        target_module="candidate_trigger",
        before_result=before_result,
        after_result=after_result,
        before_trigger=True,
        after_trigger=False,
        gold_label=True,
    )
    assert assessment.decision == "strong_improvement"
