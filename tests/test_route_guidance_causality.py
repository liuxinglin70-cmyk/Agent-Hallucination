"""Causal contracts for route-guidance evolution (F1/F2/F4/F5/F6).

These pin the repairs that close the route-guidance loop: the frozen route
replay (F1) is the causal control surface, the guidance executed judgement (F2)
requires injection + observable movement + edge counterfactual, the entity
scoping (F4) stops a case-wide feature union from authorizing injection, the
semantic payload (F5) survives the attempt -> memory -> reference chain, and
the genome behavior identity (F6a) includes the guidance text.
"""

from __future__ import annotations

from types import SimpleNamespace

from sehd_rise.causal.episode import (
    CausalFailureEpisode,
    FailureAttempt,
    HistoricalEpisodeReference,
)
from sehd_rise.detection import route_replay as route_replay_module
from sehd_rise.detection.court import engine as court_engine
from sehd_rise.detection.injection import (
    RouteGuidanceEntry,
    compile_route_guidance,
    select_route_guidance,
)
from sehd_rise.detection.route_replay import (
    replay_route_guidance_focused,
    route_observable_fingerprints,
)
from sehd_rise.evaluation.effect import _attribute_intervention_fitness
from sehd_rise.evaluation.fitness import FitnessAssessment
from sehd_rise.experience.memory import (
    ExperienceMemory,
    Representative,
)
from sehd_rise.skills.genome import (
    MODULE_IDS,
    SkillEdit,
    SkillGenome,
    SkillModule,
    create_baseline_genome,
    validate_edit,
)


def _condition_spec() -> dict:
    return {
        "schema_version": "sehd.condition_spec.v3",
        "feature_source": "court_result",
        "feature": "evidence_verified",
        "operator": "not_equals",
        "value_type": "boolean",
        "value": True,
        "scope": "l1_court_transform",
        "evidence_binding": "post_court_projection",
        "combinator": "atom",
        "binding": "entity_scoped",
    }


def _certificate(cert_id: str) -> dict:
    return {
        "certificate_id": cert_id,
        "source_candidate_id": "c1",
        "origin_event_id": "event_001",
        "breach_type": "invalid_action",
        "claim_kind": "agent_assertion",
        "norm_source": "tool_contract",
        "impact_kind": "x",
    }


# --- F6a: route_guidance participates in behavior identity -----------------

def test_execution_hash_covers_route_guidance() -> None:
    base = create_baseline_genome()
    modules = {
        mid: SkillModule.from_dict(base.modules[mid].to_dict())
        for mid in MODULE_IDS
    }
    genome = SkillGenome(genome_id="g", modules=modules)
    module = genome.modules["atomic_verifier_instruction"]
    module.rules.append("When X, follow the guidance.")
    module.set_rule_meta(0, {
        "route_guidance": "Verify cited events independently.",
        "condition_spec": _condition_spec(),
    })
    original = genome.execution_hash
    changed = genome.clone()
    changed.modules["atomic_verifier_instruction"].set_rule_meta(0, {
        "route_guidance": "Verify cited events differently.",
        "condition_spec": _condition_spec(),
    })
    assert changed.execution_hash != original


# --- F6b: guidance modify cannot touch an immutable baseline rule ----------

def test_guidance_modify_respects_baseline_protection() -> None:
    base = create_baseline_genome()
    edit = SkillEdit(
        edit_id="e", target_module="atomic_verifier_instruction",
        operation="modify_rule", rule_index=0,
        new_content="When X, follow the guidance.",
        condition_spec=_condition_spec(),
        route_guidance="Verify cited events.",
    )
    ok, reason = validate_edit(edit, base)
    assert ok is False
    assert reason == "immutable_baseline_rule"


# --- F4: entity scoping, not case-wide feature union -----------------------

def test_select_route_guidance_requires_entity_grounding() -> None:
    entry = RouteGuidanceEntry(
        condition_spec=_condition_spec(), text="Verify cited events.",
    )
    # No certificate carries the predicate: the case-wide union could be made
    # to contain the feature but no entity actually matches, so nothing injects.
    empty = {
        "proof_adjudication": {"surviving_error_certificate_ids": []},
        "census": [],
        "prosecution": {"candidates": []},
        "defense": {"candidates": []},
    }
    assert select_route_guidance(
        {"atomic_verifier_instruction": (entry,)}, result=empty,
    ) == {}
    # A certificate that is not evidence-verified matches the predicate.
    matched = {
        **empty,
        "census": [{
            "candidate_id": "c1", "candidate_kind": "unsupported_claim",
            "module_tag": "tool_use", "origin_hypothesis_event_id": "event_001",
            "axes": ["F"],
        }],
        "prosecution": {"candidates": [_certificate("c1")]},
        "proof_adjudication": {
            "surviving_error_certificate_ids": [],
            "error_audits": [{"certificate_id": "c1", "decision": "fail"}],
        },
    }
    assert select_route_guidance(
        {"atomic_verifier_instruction": (entry,)}, result=matched,
    ) == {"atomic_verifier_instruction": ("Verify cited events.",)}


# --- F1: observable fingerprints are drift-tolerant ------------------------

def test_observable_fingerprints_ignore_positional_rename() -> None:
    result = {
        "prosecution": {"candidates": [_certificate("c1")]},
    }
    renamed = {
        "prosecution": {"candidates": [{**_certificate("c9"), "certificate_id": "c9"}]},
    }
    # The claim fingerprint is content-anchored, so a positional re-mint reads
    # as the same observable.
    before = route_observable_fingerprints(result)["certificate_audit_fingerprints"]
    after = route_observable_fingerprints(renamed)["certificate_audit_fingerprints"]
    assert before == after


# --- F1: frozen route replay injects guidance only into the owner's route --

def _record() -> dict:
    return {
        "question": "q",
        "history": [
            {"role": "assistant", "content": "The package was released on March 3."},
        ],
    }


def _focused_baseline() -> dict:
    return {
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "state": "verified_clean",
        "census": [{
            "candidate_id": "c1",
            "candidate_kind": "unsupported_claim",
            "module_tag": "tool_use",
            "origin_hypothesis_event_id": "event_001",
            "axes": ["F"],
        }],
        "qualifications": [],
        "propagations": [],
        "prosecution": {"coverage_state": "complete", "candidates": []},
        "combined_prosecution": {"coverage_state": "complete", "candidates": []},
        "defense": {"coverage_state": "complete", "candidates": []},
        "providers": {},
        "proof_adjudication": {
            "state": "verified_clean",
            "decision_authority": "authoritative",
            "responsible_event_id": None,
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
            "surviving_error_certificate_ids": [],
            "error_audits": [],
        },
    }


def test_focused_replay_injects_guidance_and_freezes_upstream(monkeypatch) -> None:
    captured: dict[str, list[str]] = {"systems": [], "purposes": []}

    def provider(*, route, system, user, purpose, **kwargs):
        captured["systems"].append(system)
        captured["purposes"].append(purpose)
        if purpose == "detector:focused_prosecution":
            return ({
                "coverage_state": "complete",
                "candidates": [{
                    "certificate_id": "focused_error_001",
                    "source_candidate_id": "c1",
                    "origin_event_id": "event_001",
                    "claim_kind": "agent_assertion",
                    "norm_source": "prior_trajectory_evidence",
                    "norm_evidence_refs": ["event_001"],
                    "breach_type": "fabrication",
                    "violation_evidence_refs": ["event_001"],
                    "agent_control_event_id": "event_001",
                    "agent_control_evidence_refs": ["event_001"],
                    "impact_kind": "final_answer",
                    "impact_event_ids": ["event_001"],
                    "impact_evidence_refs": ["event_001"],
                    "recovery_state": "none",
                    "recovery_evidence_refs": [],
                    "benchmark_causal_status": "causes_incorrect_result",
                    "benchmark_causal_evidence_refs": ["event_001"],
                    "rationale": "unsupported claim",
                }],
            }, {"purpose": purpose})
        if purpose == "detector:defense":
            return ({
                "coverage_state": "complete",
                "candidates": [{
                    "certificate_id": "defense_001",
                    "target_candidate_id": "c1",
                    "target_origin_event_id": "event_001",
                    "defense_type": "insufficient_evidence",
                    "evidence_refs": ["event_001"],
                    "rationale": "no clean boundary",
                }],
            }, {"purpose": purpose})
        raise AssertionError(purpose)

    monkeypatch.setattr(route_replay_module, "detection_call", provider)
    monkeypatch.setattr(court_engine, "detection_call", provider)
    guided = replay_route_guidance_focused(
        _focused_baseline(), owner="evidence_obligation",
        guidance_blocks=("Verify cited events.",),
        record=_record(), config={
            "qualification_route": {}, "propagation_route": {},
            "focused_prosecution_route": {}, "defense_route": {},
            "construct_census_route": {}, "boundary_route": {},
            "focused_prosecution_retries": 0,
        },
    )
    # Only focused prosecution + defense re-ran (upstream construct census is
    # frozen); guidance reached the focused-prosecution system prompt.
    assert set(captured["purposes"]) == {
        "detector:focused_prosecution", "detector:defense",
    }
    assert any("Verify cited events." in system for system in captured["systems"])
    assert guided["route_guidance_injection"]["injected"] is True
    # The error certificate moved the observable and the terminal.
    assert guided["state"] == "verified_error"
    assert guided["route_observable_fingerprints"][
        "certificate_audit_fingerprints"
    ] != ()


def test_unguided_control_freezes_and_injects_nothing(monkeypatch) -> None:
    captured: dict[str, list[str]] = {"systems": [], "purposes": []}

    def provider(*, route, system, user, purpose, **kwargs):
        captured["systems"].append(system)
        captured["purposes"].append(purpose)
        if purpose == "detector:focused_prosecution":
            return ({"coverage_state": "complete", "candidates": []}, {"purpose": purpose})
        if purpose == "detector:defense":
            return ({"coverage_state": "complete", "candidates": []}, {"purpose": purpose})
        raise AssertionError(purpose)

    monkeypatch.setattr(route_replay_module, "detection_call", provider)
    monkeypatch.setattr(court_engine, "detection_call", provider)
    control = replay_route_guidance_focused(
        _focused_baseline(), owner="evidence_obligation",
        guidance_blocks=(), record=_record(), config={
            "qualification_route": {}, "propagation_route": {},
            "focused_prosecution_route": {}, "defense_route": {},
            "construct_census_route": {}, "boundary_route": {},
            "focused_prosecution_retries": 0,
        },
    )
    assert control["route_guidance_injection"]["injected"] is False
    assert all(
        "route guidance" not in system for system in captured["systems"]
    )


# --- F2: guidance executed requires injection + movement + edge ------------

def _assessment() -> FitnessAssessment:
    return FitnessAssessment(
        "strong_improvement", ("gold_assisted_task_score_improved",), 0, 2, 0.0, 2.0,
    )


def _guidance_edit() -> SkillEdit:
    return SkillEdit(
        edit_id="e", target_module="evidence_obligation", operation="add_rule",
        new_content="When X, follow.", condition_spec=_condition_spec(),
        route_guidance="Verify cited events.",
        repair_edge={"edge_id": "e1", "owner_module": "evidence_obligation",
                     "blocking_predicate": "missing_certificate"},
    )


def test_guidance_not_injected_demotes_improvement() -> None:
    result = {
        "state": "verified_error",
        "route_guidance_injection": {"injected": False},
        "combined_prosecution": {"candidates": [_certificate("c1")]},
    }
    assessment, audit = _attribute_intervention_fitness(
        _assessment(), edit=_guidance_edit(), result=result,
        baseline_result={"state": "verified_clean"},
    )
    assert assessment.decision == "no_effect"
    assert audit["status"] == "not_executed"


def test_guidance_executed_keeps_improvement() -> None:
    result = {
        "state": "verified_error",
        "route_guidance_injection": {"injected": True, "blocks": []},
        "combined_prosecution": {"candidates": [_certificate("c1")]},
        "proof_adjudication": {},
    }
    assessment, audit = _attribute_intervention_fitness(
        _assessment(), edit=_guidance_edit(), result=result,
        baseline_result={"state": "verified_clean", "prosecution": {"candidates": []}},
    )
    assert assessment.decision == "strong_improvement"
    assert audit["status"] == "executed"
    assert audit["target_edge_changed"] is True


# --- F5: semantic payload survives the attempt -> memory -> reference chain

def test_representative_round_trips_route_guidance_payload() -> None:
    rep = Representative(
        completeness="complete", evidence_fields=3, sequence=1,
        episode_id="ep1", causal_slice={}, action_spec={}, outcome={},
        rule_text="", route_guidance="Verify cited events.",
        failure_mechanism="missing certificate",
        expected_local_effect="certificate fingerprint appears",
    )
    restored = Representative.from_dict(rep.to_dict())
    assert restored.route_guidance == "Verify cited events."
    assert restored.failure_mechanism == "missing certificate"
    assert restored.expected_local_effect == "certificate fingerprint appears"


def test_failure_attempt_round_trips_route_guidance_payload() -> None:
    attempt = FailureAttempt(
        attempt_id="a1", proposal_signature="p", operation="add_rule",
        target_module="evidence_obligation", action_spec={},
        structural_result="accepted", compiled=False, injected=True,
        applied=False, observable_delta=(), downstream_court_delta=(),
        no_effect_reason="", decision="accept",
        route_guidance="Verify cited events.",
        failure_mechanism="missing certificate",
        expected_local_effect="fingerprint appears",
    )
    restored = FailureAttempt.from_dict(attempt.to_dict())
    assert restored.route_guidance == "Verify cited events."


def test_reference_carries_route_guidance_payload() -> None:
    reference = HistoricalEpisodeReference(
        episode_id="ep1", mechanism_signature=(), shared_facts={},
        current_only_facts={}, historical_only_facts={}, rule_condition="",
        action_spec=None, local_outcome=None,
        route_guidance="Verify cited events.",
        failure_mechanism="missing certificate",
        expected_local_effect="fingerprint appears",
    )
    payload = reference.to_dict()
    assert payload["route_guidance"] == "Verify cited events."
    assert payload["failure_mechanism"] == "missing certificate"
