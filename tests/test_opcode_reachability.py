"""P0-4 reachability acceptance: every registered L1 opcode and reachable L2
edit has one proven end-to-end chain — cut → episode → plan → binding →
execution → effect → fitness — driven through the real pipeline helpers.

Only the five reachable L1 ops and the three reachable L2 templates are
proven here; ops whose owners the causal cut can never produce are absent
from the registry by construction (see the unreachable-surface invariant
test).
"""

from __future__ import annotations

import copy
import re
from types import SimpleNamespace
from typing import Mapping

from sehd_rise.causal.cut import (
    allowed_l1_modules,
    build_causal_cut,
    infer_evolution_direction,
)
from sehd_rise.causal.episode import build_causal_episode
from sehd_rise.causal.plan import build_repair_plan
from sehd_rise.causal.repairability import (
    RepairabilityStatus,
    analyze as analyze_repairability,
)
from sehd_rise.causal.signals import project_detection
from sehd_rise.detection import continuation
from sehd_rise.detection.court import engine as court_engine
from sehd_rise.evaluation.fitness import (
    assess_source_fitness,
    observable_process_delta,
)
from sehd_rise.skills.action import compile_action_spec
from sehd_rise.skills.l1_execution import evaluate_l1_rule
from sehd_rise.skills.policy import PolicyGenome
from sehd_rise.skills.vocabulary.contracts import (
    LAYER_L1,
    LAYER_L2,
    validate_policy_params,
)
from sehd_rise.planning.l2 import plan_policy_candidates
from sehd_rise.skills.vocabulary.registry import L2_REGISTRY


def _chain(result: dict, *, use_gold: bool = False):
    signal = project_detection(result, use_gold=use_gold)
    cut = build_causal_cut(signal)
    direction = infer_evolution_direction(signal.state, signal.gold_label)
    frontier = (
        cut.evidence.get("artifact_frontier", ())
        if isinstance(cut.evidence, Mapping) else ()
    )
    modules = allowed_l1_modules(signal, cut.owner, frontier, cut.secondary_owner)
    episode = build_causal_episode(
        signal=signal,
        cut=cut,
        record_key="case_r",
        evolution_direction=direction,
        allowed_modules=modules,
    )
    verdict = analyze_repairability(
        episode,
        allowed_modules=(modules if cut.skill_layer == LAYER_L1 else ()),
        evolution_direction=direction,
    )
    plans = build_repair_plan(
        episode, allowed_modules=modules, evolution_direction=direction,
    )
    return signal, cut, episode, verdict, plans


def _atom(feature: str, value) -> dict:
    return {
        "schema_version": "sehd.condition_spec.v3",
        "feature_source": "court_result",
        "feature": feature,
        "operator": "contains",
        "value_type": "string",
        "value": value,
        "scope": "l1_court_transform",
        "evidence_binding": "post_court_projection",
        "combinator": "atom",
        "binding": "entity_scoped",
    }


def _fp_result() -> dict:
    """Gold says clean, detector says error with one surviving carrier."""
    cert = {
        "certificate_id": "e1",
        "source_candidate_id": "c1",
        "origin_event_id": "event_001",
    }
    return {
        "state": "verified_error",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "responsible_event_id": "event_001",
        "responsible_step": 1,
        "census": [{"candidate_id": "c1", "origin_hypothesis_event_id": "event_001"}],
        "prosecution": {"coverage_state": "complete", "candidates": [cert]},
        "combined_prosecution": {"coverage_state": "complete", "candidates": [cert]},
        "defense": {"coverage_state": "incomplete", "candidates": []},
        "proof_adjudication": {
            "state": "verified_error",
            "prosecution_coverage": "complete",
            "defense_coverage": "incomplete",
            "surviving_error_certificate_ids": ["e1"],
            "valid_error_certificate_count": 1,
            "valid_defense_certificate_count": 0,
            "quarantined_error_certificate_ids": [],
            "quarantined_defense_certificate_ids": [],
            "consensus_gate_blocked_ids": [],
            "consensus_gate_applied": False,
            "error_audits": [],
            "defense_audits": [],
            "responsible_event_id": "event_001",
            "terminal_basis": {"decisive_facts": {
                "coverage_complete": True,
                "candidate_terminal": True,
            }},
        },
    }


def _localization_result() -> dict:
    certificates = [
        {
            "certificate_id": "e2",
            "source_candidate_id": "c2",
            "origin_event_id": "event_002",
        },
        {
            "certificate_id": "e1",
            "source_candidate_id": "c1",
            "origin_event_id": "event_001",
        },
    ]
    return {
        "state": "verified_error",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "responsible_event_id": "event_002",
        "responsible_step": 2,
        "census": [
            {"candidate_id": "c1", "origin_hypothesis_event_id": "event_001"},
            {"candidate_id": "c2", "origin_hypothesis_event_id": "event_002"},
        ],
        "prosecution": {"coverage_state": "complete", "candidates": certificates},
        "combined_prosecution": {
            "coverage_state": "complete", "candidates": certificates,
        },
        "defense": {"coverage_state": "incomplete", "candidates": []},
        "proof_adjudication": {
            "state": "verified_error",
            "decision_authority": "authoritative",
            "responsible_event_id": "event_002",
            "localization_status": "failed_temporal_fallback",
            "prosecution_coverage": "complete",
            "defense_coverage": "incomplete",
            "surviving_error_certificate_ids": ["e2", "e1"],
            "valid_error_certificate_count": 2,
            "valid_defense_certificate_count": 0,
            "quarantined_error_certificate_ids": [],
            "quarantined_defense_certificate_ids": [],
            "consensus_gate_blocked_ids": [],
            "consensus_gate_applied": False,
            "error_audits": [],
            "terminal_basis": {"decisive_facts": {
                "coverage_complete": True,
                "candidate_terminal": True,
            }},
        },
    }


def test_keep_coverage_incomplete_full_chain() -> None:
    result = {**_fp_result(), "gold_label": False}
    signal, cut, _episode, verdict, plans = _chain(result, use_gold=True)
    assert cut.reason_code == "gold_false_positive"
    assert verdict.status is RepairabilityStatus.REPAIRABLE
    assert plans, "no plan compiled for the gold FP"
    assert "keep_coverage_incomplete" in plans[0].legal_operations
    mutated, report = evaluate_l1_rule(
        condition_spec=_atom("coverage_state", "complete"),
        action_spec=compile_action_spec(
            "evidence_obligation", "keep_coverage_incomplete: coverage_state",
        ).to_dict(),
        result=result,
    )
    assert report["fired"] is True
    assert mutated["proof_adjudication"]["prosecution_coverage"] == "incomplete"
    after = project_detection(mutated, use_gold=True)
    deltas = observable_process_delta(
        signal, after, target_module="evidence_obligation",
        before_result=result, after_result=mutated,
        before_trigger=True, after_trigger=True,
    )
    assert "prosecution_coverage:decreased" in deltas
    assessment = assess_source_fitness(
        signal, after, target_module="evidence_obligation",
        before_result=result, after_result=mutated,
        before_trigger=True, after_trigger=True, gold_label=False,
    )
    assert assessment.decision != "worse"


def test_withhold_certificate_full_chain() -> None:
    result = _fp_result()
    result["proof_adjudication"]["error_audits"] = [{
        "certificate_id": "e1",
        "decision": "quarantine",
        "failed_checks": ["required_fields"],
        "missing_fields": ["certificate_id"],
    }]
    result["proof_adjudication"]["quarantined_error_certificate_ids"] = ["e1"]
    signal, cut, _episode, verdict, plans = _chain(result)
    assert cut.owner == "atomic_verifier_instruction"
    assert cut.reason_code == "quarantine_pattern_dominant"
    assert verdict.status is RepairabilityStatus.REPAIRABLE
    assert "withhold_certificate" in plans[0].legal_operations
    mutated, report = evaluate_l1_rule(
        condition_spec=_atom("missing_field", "certificate_id"),
        action_spec=compile_action_spec(
            "atomic_verifier_instruction", "withhold_certificate: certificate_id",
        ).to_dict(),
        result=result,
    )
    assert report["fired"] is True
    assert mutated["proof_adjudication"]["surviving_error_certificate_ids"] == []
    after = project_detection(mutated)
    deltas = observable_process_delta(
        signal, after, target_module="atomic_verifier_instruction",
        before_result=result, after_result=mutated,
        before_trigger=True, after_trigger=True,
    )
    assert "surviving_certificates:decreased" in deltas
    assessment = assess_source_fitness(
        signal, after, target_module="atomic_verifier_instruction",
        before_result=result, after_result=mutated,
        before_trigger=True, after_trigger=True, gold_label=None,
    )
    assert assessment.decision != "worse"


def test_localization_ops_full_chain() -> None:
    result = _localization_result()
    signal, cut, _episode, verdict, plans = _chain(result)
    assert cut.owner == "earliest_origin_tiebreak"
    assert verdict.status is RepairabilityStatus.REPAIRABLE
    assert set(plans[0].legal_operations) == {
        "select_earliest_origin", "suppress_later_origin",
    }
    for operation in ("select_earliest_origin", "suppress_later_origin"):
        from sehd_rise.skills.binding import Binding
        from sehd_rise.skills.l1_execution import execute_l1_intervention

        spec = compile_action_spec(
            "earliest_origin_tiebreak", f"{operation}: responsible_event_id",
        )
        mutated, report = execute_l1_intervention(
            result, spec=spec,
            bindings=(Binding(
                domain="origin", entity_id="event_001",
                identifiers={"origin_event_id": "event_001"},
            ),),
        )
        assert report["fired"] is True
        assert mutated["proof_adjudication"]["responsible_event_id"] == "event_001"
        assert mutated["proof_adjudication"]["localization_status"] == "complete"
        after = project_detection(mutated)
        deltas = observable_process_delta(
            signal, after, target_module="earliest_origin_tiebreak",
            before_result=result, after_result=mutated,
            before_trigger=True, after_trigger=False,
        )
        assert "responsible_step:shifted" in deltas
        assessment = assess_source_fitness(
            signal, after, target_module="earliest_origin_tiebreak",
            before_result=result, after_result=mutated,
            before_trigger=True, after_trigger=False, gold_label=None,
        )
        assert assessment.decision != "worse"


def test_construct_candidate_full_chain(monkeypatch, tmp_path) -> None:
    record = {
        "history": [
            {"role": "user", "content": "Check the release date."},
            {"role": "assistant", "content": "The package was released on March 3."},
            {"role": "assistant", "content": "I have completed the check."},
        ],
    }
    result = {
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
            "terminal_basis": {"decisive_facts": {
                "coverage_complete": True,
                "candidate_terminal": True,
            }},
        },
        "gold_label": True,
    }
    signal, cut, _episode, verdict, plans = _chain(result, use_gold=True)
    assert cut.owner == "candidate_trigger"
    assert cut.reason_code == "gold_false_negative_clean"
    assert verdict.status is RepairabilityStatus.REPAIRABLE
    assert plans[0].legal_operations == ("construct_candidate",)
    from sehd_rise.skills.anchors import project_claim_anchors

    anchor = project_claim_anchors(record)[0]
    candidate = continuation._candidate_from_anchor(anchor)
    provider = _constructive_provider(candidate["candidate_id"])
    monkeypatch.setattr(continuation, "detection_call", provider)
    monkeypatch.setattr(court_engine, "detection_call", provider)
    mutated, proof = continuation.resume_court_from_candidate(
        result, candidate=candidate, record=record, config={
            "qualification_route": {}, "propagation_route": {},
            "focused_prosecution_route": {}, "defense_route": {},
            "focused_prosecution_retries": 0,
        },
    )
    assert proof.status == "CONSTRUCTION_COMPLETED"
    assert mutated["state"] == "verified_error"
    after = project_detection(mutated, use_gold=True)
    deltas = observable_process_delta(
        signal, after, target_module="candidate_trigger",
        before_result=result, after_result=mutated,
        before_trigger=True, after_trigger=False,
    )
    assert "candidate_count:increased" in deltas
    assessment = assess_source_fitness(
        signal, after, target_module="candidate_trigger",
        before_result=result, after_result=mutated,
        before_trigger=True, after_trigger=False, gold_label=True,
    )
    assert assessment.decision == "strong_improvement"


def _constructive_provider(candidate_id: str):
    def provider(*, purpose: str, **_kwargs):
        if purpose == "detector:qualification":
            value = {"coverage_state": "complete", "candidates": [{
                "candidate_id": candidate_id,
                "violation_state": "verified_violation",
                "actor_state": "agent_caused",
                "boundary_state": "cleared",
                "positive_evidence_refs": ["event_002"],
                "actor_evidence_refs": ["event_002"],
                "boundary_evidence_refs": [],
                "rationale": "The assistant authored the unsupported release claim.",
            }]}
        elif purpose == "detector:propagation":
            value = {"coverage_state": "complete", "candidates": [{
                "candidate_id": candidate_id,
                "propagation_state": "unrecovered_impact",
                "residual_impact": True,
                "consumption_event_ids": ["event_002"],
                "consumption_evidence_refs": ["event_002"],
                "impact_event_ids": ["event_003"],
                "impact_evidence_refs": ["event_003"],
                "recovery_event_id": None,
                "recovery_evidence_refs": [],
                "impact_surfaces": ["A"],
                "rationale": "The unsupported claim remained in the produced result.",
            }]}
        elif purpose == "detector:focused_prosecution":
            value = {"coverage_state": "complete", "candidates": [{
                "certificate_id": "focused_error_001",
                "source_candidate_id": candidate_id,
                "origin_event_id": "event_002",
                "claim_kind": "agent_assertion",
                "norm_source": "prior_trajectory_evidence",
                "norm_evidence_refs": ["event_001"],
                "breach_type": "fabrication",
                "violation_evidence_refs": ["event_002"],
                "agent_control_event_id": "event_002",
                "agent_control_evidence_refs": ["event_002"],
                "impact_kind": "final_answer",
                "impact_event_ids": ["event_003"],
                "impact_evidence_refs": ["event_003"],
                "recovery_state": "none",
                "recovery_evidence_refs": [],
                "benchmark_causal_status": "causes_incorrect_result",
                "benchmark_causal_evidence_refs": ["event_003"],
                "rationale": (
                    "NORM: release dates need support. ORIGIN: March 3 was "
                    "introduced at event two. CONTROL: the assistant authored "
                    "it. PROPAGATION: it remained in the answer. IMPACT: the "
                    "answer is wrong. COUNTERFACTUAL: removing it fixes the "
                    "result."
                ),
            }]}
        elif purpose == "detector:defense":
            value = {"coverage_state": "complete", "candidates": [{
                "certificate_id": "focused_defense_001",
                "target_candidate_id": candidate_id,
                "target_origin_event_id": "event_002",
                "defense_type": "insufficient_evidence",
                "evidence_refs": ["event_002"],
                "rationale": "No addressable evidence proves a clean boundary.",
            }]}
        else:
            raise AssertionError(purpose)
        return value, {"purpose": purpose}

    return provider


def test_l2_reachable_edits_compile_and_validate() -> None:
    for reason in (
        "hollow_complete_prosecution",
        "hollow_complete_defense",
        "boundary_terminal_conflict",
    ):
        attribution = SimpleNamespace(
            reason_code=reason,
            layer=LAYER_L2,
            artifact="coverage_validity_policy",
            evidence={"terminal_state": "unresolved"},
            to_dict=lambda: {
                "layer": LAYER_L2,
                "artifact": "coverage_validity_policy",
                "reason_code": reason,
                "evidence": {"terminal_state": "unresolved"},
            },
        )
        plan = plan_policy_candidates(
            attribution=attribution,
            genome=PolicyGenome(genome_id="g", registry=L2_REGISTRY),
        )
        assert plan.candidates, reason
        for edit in plan.candidates:
            item = edit.new_item
            assert item is not None
            assert item.precondition in L2_REGISTRY.predicate_names(), reason
            assert item.action in L2_REGISTRY.action_names(), reason
            params_ok, why = validate_policy_params(
                item.action, item.params, registry=L2_REGISTRY,
            )
            assert params_ok, f"{reason}: {why}"


def test_registry_has_no_unreachable_l1_op() -> None:
    """Registry truthfulness: every registered L1 capability's owner must be
    producible by the causal cut. The three removed ops (suppress_candidate /
    hold_candidate_qualification / reject_non_causal_origin) stay out because
    their owners never become cut owners, so a prompt can never advertise an
    opcode no upstream authority can route to."""
    from sehd_rise.skills.capabilities import SKILL_CAPABILITY_REGISTRY

    owners = {
        capability.module
        for capability in SKILL_CAPABILITY_REGISTRY.query(skill_layer=LAYER_L1)
    }
    assert owners <= {"candidate_trigger", "evidence_obligation",
                      "atomic_verifier_instruction", "earliest_origin_tiebreak"}
    operations = {
        capability.executable_operation
        for capability in SKILL_CAPABILITY_REGISTRY.query(skill_layer=LAYER_L1)
    }
    assert operations == {
        "construct_candidate", "keep_coverage_incomplete",
        "withhold_certificate", "select_earliest_origin",
        "suppress_later_origin",
    }
