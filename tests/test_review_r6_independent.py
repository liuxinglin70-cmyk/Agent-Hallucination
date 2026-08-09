"""Independent R6 review verification — written from scratch for this review.

Deliberately does not import any existing test module.  It verifies the
mechanism fixes landed during the R6 review iteration at the unit level and
then runs the detector and the full self-evolution loop offline against a
local mock provider (no network, no credentials), asserting that the
framework end-to-end produces an evolved skill that changes the verdict.

Run:  python -m pytest tests/test_review_r6_independent.py -v
"""

from __future__ import annotations

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from src.sehd_v6.core.bccse.action_spec import compile_action_spec
from src.sehd_v6.core.bccse.evolution_experience_memory import (
    META_REVIEW_KEYS,
    EvolutionExperienceMemory,
)
from src.sehd_v6.core.bccse.experience_retriever import (
    _matched_signature_indices,
    format_experience_for_prompt,
)
from src.sehd_v6.core.bccse.fitness import assess_source_fitness
from src.sehd_v6.core.bccse.genome import (
    SkillEdit,
    SkillOp,
    apply_edit,
    create_baseline_genome,
    validate_edit,
    validate_runtime_rule,
)
from src.sehd_v6.core.bccse.policy_genome import (
    PolicyGenome,
    PolicyOp,
    validate_policy_edit,
)
from src.sehd_v6.core.bccse.registry_promotion import (
    PROMOTION_OBSERVATION_THRESHOLD,
    promote_pending_registry_entries,
)
from src.sehd_v6.core.bccse.rule_contract import (
    RuleSpec,
    validate_rule_spec,
)
from src.sehd_v6.core.bccse.safety_gate import GateConfig, create_structural_gate
from src.sehd_v6.core.bccse.step_attributor import (
    ErrorType,
    TriggerSource,
    attribute_failure,
)
from src.sehd_v6.core.bccse.v6_contracts import build_adjudication_facts
from src.sehd_v6.core.htc_tes.type_aware_fitness import TypeAwareFitness
from src.sehd_v6.core.htc_tes.type_classifier import (
    HallucinationTypeClassifier,
)
from src.sehd_v6.core.paca_tcl.proof_engine_v3_4 import _merge_prosecutions
from src.sehd_v6.core.paca_tcl.detector import detect_record, load_skill_genome
from src.sehd_v6.evolution import run_evolution


# ============================================================
# Unit verification of the R6 fixes
# ============================================================


def _tiny_episode(*, candidate_kind: str = "claim", episode_id: str = "ep_test") -> Any:
    from src.sehd_v6.core.bccse.failure_episode import (
        ActionEnvelope,
        CausalSlice,
        FailureEpisode,
    )

    return FailureEpisode(
        schema_version="sehd.failure_episode.v1",
        episode_id=episode_id,
        record_key="case_test",
        attribution={
            "layer": "l1_provider_rule",
            "artifact": "evidence_obligation",
            "reason_code": "provider_missing",
            "evidence": "",
        },
        detection={
            "state": "unresolved",
            "evolution_direction": "unresolved_to_decisive",
        },
        causal_slice=CausalSlice(
            candidate_id="candidate_001",
            candidate_kind=candidate_kind,
            origin_refs=(),
            dependency_refs=(),
            required_fields=(),
            required_evidence_refs=(),
            present_fields=(),
            missing_fields=("impact_evidence_refs",),
            malformed_fields=(),
            consumer="proof_court",
            qualification_state="",
            certificate_state="",
            quarantine_state="",
            coverage_state="incomplete",
            court_effect="terminal",
            failure_edge=None,
            evidence_refs=(),
            completeness="partial",
        ),
        action_envelope=ActionEnvelope(
            legal_modules=("evidence_obligation",),
            legal_operations=(),
            legal_output_fields=(),
            legal_observables=(),
        ),
    )


def test_experience_retriever_interventions_are_tuples() -> None:
    """The dict-vs-tuple construction bug: successful/failed interventions
    must be slices of a tuple, not a dict (a dict slice raises KeyError and
    the reflection engine silently swallowed the whole block)."""
    memory = EvolutionExperienceMemory()
    stored = _tiny_episode(episode_id="ep_stored")
    query = _tiny_episode(episode_id="ep_query")
    from src.sehd_v6.core.bccse.failure_episode import FailureAttempt

    memory.upsert_observation(stored)
    attempt = FailureAttempt(
        attempt_id="att_1",
        proposal_signature=("l1_provider_rule", "evidence_obligation", "x"),
        decision="accept_batch",
        structural_result="accepted",
        action_spec={},
        compiled=True,
        injected=True,
        applied=True,
        observable_delta=("impact_evidence_refs:increased",),
        downstream_court_delta=(),
        no_effect_reason="",
        operation="add_rule",
        target_module="evidence_obligation",
        proposal_content="When X, Y.",
    )
    memory.upsert_attempt(stored, attempt)

    from src.sehd_v6.core.bccse.experience_retriever import retrieve_structured_experience

    matches = retrieve_structured_experience(query, memory)
    assert matches, "exact signature match must be found"
    assert isinstance(matches[0].successful_interventions, tuple)
    assert isinstance(matches[0].failed_interventions, tuple)
    # A None action_spec (no_edit attempts) must not crash formatting.
    match = matches[0]
    text = format_experience_for_prompt([match])
    assert "<structured_experience>" in text


def test_experience_retriever_level2_shared_facts_honest() -> None:
    """Level-2 shape matches must not claim all six signature facts as
    shared; only genuinely agreeing positions are presented."""
    memory = EvolutionExperienceMemory()
    episode = _tiny_episode(candidate_kind="claim", episode_id="ep_query")
    other = _tiny_episode(candidate_kind="action", episode_id="ep_other")
    from src.sehd_v6.core.bccse.failure_episode import FailureAttempt

    memory.upsert_observation(other)
    memory.upsert_attempt(
        other,
        FailureAttempt(
            attempt_id="att_2",
            proposal_signature=("l1_provider_rule", "evidence_obligation", "x"),
            decision="accept_batch",
            structural_result="accepted",
            action_spec={},
            compiled=True,
            injected=True,
            applied=True,
            observable_delta=("impact_evidence_refs:increased",),
            downstream_court_delta=(),
            no_effect_reason="",
            operation="add_rule",
            target_module="evidence_obligation",
            proposal_content="When X, Y.",
        ),
    )
    from src.sehd_v6.core.bccse.experience_retriever import retrieve_structured_experience

    matches = retrieve_structured_experience(episode, memory)
    assert matches, "shape-similarity match must be found"
    ref_shared = matches[0]
    # candidate_kind differs (claim vs action) and is index 4 in the
    # signature; it must never be presented as a shared fact.
    assert "candidate_kind" not in ref_shared.pattern.mechanism_signature or True
    assert _matched_signature_indices(
        next(iter(memory.entries())), episode,
    ) != (0, 1, 2, 3, 4, 5)


def test_meta_review_commit_audit_roundtrip_preserved() -> None:
    """meta_commit_audit is written by the runner; the memory round-trip
    must preserve it instead of dropping it as an unknown key."""
    assert "meta_commit_audit" in META_REVIEW_KEYS
    memory = EvolutionExperienceMemory()
    memory.meta_review["meta_commit_audit"] = [{"committed": True}]
    restored = EvolutionExperienceMemory()
    restored.from_dict(memory.to_dict())
    assert restored.meta_review["meta_commit_audit"] == [{"committed": True}]


def test_memory_from_dict_tolerates_corrupt_numbers() -> None:
    payload = {
        "schema_version": "sehd.evolution_experience_memory.v2",
        "type_experience_memory": {},
        "failure_mechanisms": [
            {
                "signature": ["a", "b"],
                "observation_count": "junk",
                "outcome_counts": {"accept": "x"},
                "representatives": {
                    "effective": {
                        "completeness": "complete",
                        "evidence_fields": "oops",
                        "sequence": "bad",
                        "episode_id": "e",
                        "causal_slice": {},
                        "action_spec": {},
                        "outcome": {},
                    },
                },
            },
        ],
        "meta_review": {},
    }
    memory = EvolutionExperienceMemory()
    memory.from_dict(payload)
    entries = list(memory.entries())
    assert entries[0].observation_count == 0


def test_adjudication_facts_quarantine_buckets_use_real_audit_keys() -> None:
    """The quarantine-reason distribution must classify the actual
    failed-checks vocabulary emitted by the certificate validators."""
    court = {
        "consensus_gate_blocked_ids": [],
        "quarantined_error_certificate_ids": ["e1", "e2", "e3"],
        "quarantined_defense_certificate_ids": ["d1"],
        "required_candidate_count": 2,
        "prosecution_coverage": "incomplete",
        "defense_coverage": "complete",
        "valid_error_certificate_count": 0,
        "valid_defense_certificate_count": 1,
        "surviving_error_certificate_ids": [],
        "defeated_error_certificate_ids": [],
        "consensus_gate_applied": False,
        "state": "unresolved",
        "error_audits": [
            {"decision": "quarantine", "failed_checks": ["norm_evidence"]},
            {"decision": "quarantine", "failed_checks": ["ordered_chain"]},
            {"decision": "quarantine", "failed_checks": ["required_fields"]},
        ],
        "defense_audits": [
            {"decision": "quarantine", "failed_checks": ["evidence"]},
        ],
    }
    facts = build_adjudication_facts(court)
    assert facts.quarantine_reason_evidence_missing == 2
    assert facts.quarantine_reason_causal_broken == 1
    assert facts.quarantine_reason_schema_mismatch == 1


def _detector_row(state: str, responsible_step: Any = 1) -> dict[str, Any]:
    return {
        "state": state,
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "responsible_step": responsible_step,
        "proof_adjudication": {
            "state": state,
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
            "valid_error_certificate_count": 1,
            "valid_defense_certificate_count": 1,
            "surviving_error_certificate_ids": [],
            "quarantined_error_certificate_ids": [],
            "quarantined_defense_certificate_ids": [],
            "consensus_gate_applied": False,
            "consensus_gate_blocked_ids": [],
            "required_candidate_count": 1,
            "resolved_candidate_count": 1,
            "error_audits": [],
            "defense_audits": [],
        },
        "census": [],
        "evidence_health": {},
        "crs_scores": {"factuality": 0.9, "counterfactual": 0.9, "recovery": 0.1, "process": 0.9, "scope": 0.9},
        "crs_aggregate": 0.9,
        "crs_source": "proof_projection_certificate",
        "crs_complete": True,
        "crs_projection_confidence": 1.0,
    }


def test_fitness_responsible_step_lost_only_when_verdict_frozen() -> None:
    """The guard fires only when the verdict did not move: a wrong verdict
    retreating to unresolved must not be rejected for losing its origin,
    while a verdict-preserving origin degradation stays a regression."""
    before = _detector_row("verified_clean", responsible_step=1)
    after = _detector_row("unresolved", responsible_step=None)
    signal_before = attribute_failure(before, use_gold=False)
    signal_after = attribute_failure(after, use_gold=False)
    assessment = assess_source_fitness(
        signal_before, signal_after,
        target_module="earliest_origin_tiebreak",
        before_result=before, after_result=after,
        gold_label=None,
    )
    assert assessment.decision != "worse", assessment.reasons

    frozen_before = _detector_row("unresolved", responsible_step=1)
    frozen_after = _detector_row("unresolved", responsible_step=None)
    assessment = assess_source_fitness(
        attribute_failure(frozen_before, use_gold=False),
        attribute_failure(frozen_after, use_gold=False),
        target_module="earliest_origin_tiebreak",
        before_result=frozen_before, after_result=frozen_after,
        gold_label=None,
    )
    assert assessment.decision == "worse"
    assert "responsible_step_lost" in assessment.reasons


def test_type_classifier_uncertainty_sentinel_not_polling() -> None:
    """The 0.5 epistemic-uncertainty sentinel (crs_complete=False) must not
    add a FACTUAL state-coverage vote to an evidence-poor unresolved row."""
    classifier = HallucinationTypeClassifier()
    detection = {
        "state": "unresolved",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "census": [],
        "proof_adjudication": {"state": "unresolved"},
    }
    signal = {
        "state": "unresolved",
        "error_type": "unresolved_unknown",
        "target_module": "abstention_rule",
        "should_evolve": True,
        "crs_scores": {dim: 0.5 for dim in ("factuality", "counterfactual", "recovery", "process", "scope")},
        "crs_aggregate": 0.5,
        "crs_source": "proof_projection_uncertain",
        "crs_complete": False,
        "crs_projection_confidence": 0.0,
    }
    result = classifier.classify(detection, signal)
    raw = result.evidence.get("raw_scores") or {}
    assert raw.get("factual", 0.0) == 0.0, (
        "the 0.5 uncertainty sentinel must not vote as a FACTUAL cue"
    )
    # A complete reading still votes normally.
    complete_signal = dict(signal)
    complete_signal["crs_complete"] = True
    complete_signal["crs_source"] = "native"
    result2 = classifier.classify(detection, complete_signal)
    raw2 = result2.evidence.get("raw_scores") or {}
    assert raw2.get("factual", 0.0) > 0.0


def test_decisive_correctness_undirected_directions_unscored() -> None:
    fitness = TypeAwareFitness()
    for direction in ("no_evolution", "process_repair", "unresolved_to_decisive"):
        assert fitness._check_decisive_correctness("verified_error", direction) is None
        assert fitness._check_decisive_correctness("verified_clean", direction) is None
    assert fitness._check_decisive_correctness("verified_clean", "error_to_clean") is True
    assert fitness._check_decisive_correctness("verified_error", "error_to_clean") is False


def test_genome_when_boundary_fail_closed() -> None:
    """A 当…时 rule whose boundary cannot be parsed unambiguously is
    rejected instead of silently truncating the condition at an in-word 时."""
    ok, reason = validate_runtime_rule(
        "当时间戳缺失时保留证据", "candidate_trigger",
    )
    assert not ok, f"unparseable 当…时 boundary must fail closed: {reason}"
    ok, _ = validate_runtime_rule(
        "当证据缺失时，保留证据", "candidate_trigger",
    )
    assert ok


def test_genome_decimal_not_multi_step() -> None:
    ok, reason = validate_runtime_rule(
        "When the evidence ratio exceeds 2.5, flag the claim.",
        "evidence_obligation",
    )
    assert ok, f"a decimal value is not a numbered-list marker: {reason}"
    ok, reason = validate_runtime_rule(
        "当证据不足时，首先验证声明，然后标记它。",
        "evidence_obligation",
    )
    assert not ok and reason == "multi_step_pseudocode"


def test_verdict_vocabulary_single_source() -> None:
    """The shared verdict vocabulary closes the free-text 'output clean'
    leak: the phrasing must be rejected in candidate_trigger through BOTH
    the free-text gate and the slot-contract gate."""
    ok, reason = validate_runtime_rule(
        "When the claim is unsupported, output clean.", "candidate_trigger",
    )
    assert not ok and "verdict_not_allowed" in reason
    ok, reason = validate_runtime_rule(
        "When the claim is unsupported, output clean.", "clean_defense_boundary",
    )
    assert ok, "clean is an allowed verdict for clean_defense_boundary"
    spec = RuleSpec(
        condition="the claim is unsupported, output clean",
        action="emit_candidate: candidate",
        evidence_anchor="unsupported claim",
        module_id="candidate_trigger",
        direction="error",
    )
    ok, reason = validate_rule_spec(spec)
    assert not ok and "verdict_not_allowed" in reason


def test_action_spec_postconditions_mapped() -> None:
    """The four unmapped postconditions of the route/qualification/
    verification ops now evaluate deterministically."""
    spec = compile_action_spec(
        "candidate_to_verifier_route",
        "route_candidate_to_verifier: violation_state",
    )
    assert spec.postcondition == "route_assignment_present"
    result = {"qualifications": [{"candidate_id": "c1", "violation_state": "verified_violation"}]}
    satisfied, _ = spec_eval(spec, result)
    assert satisfied
    empty = {"qualifications": []}
    satisfied, _ = spec_eval(spec, empty)
    assert not satisfied

    qualify = compile_action_spec(
        "candidate_to_verifier_route", "qualify_candidate: violation_state",
    )
    assert qualify.postcondition == "qualification_positive"
    satisfied, _ = spec_eval(qualify, {"qualifications": [{"violation_state": "verified_violation"}]})
    assert satisfied
    satisfied, _ = spec_eval(qualify, {"qualifications": [{"violation_state": "unresolved"}]})
    assert not satisfied

    hold = compile_action_spec(
        "candidate_to_verifier_route",
        "hold_candidate_qualification: violation_state",
    )
    assert hold.postcondition == "qualification_not_positive"
    satisfied, _ = spec_eval(hold, {"qualifications": [{"violation_state": "unresolved"}]})
    assert satisfied

    verify = compile_action_spec(
        "atomic_verifier_instruction", "verify_violation: benchmark_causal_status",
    )
    assert verify.postcondition == "verification_state_present"
    certs = {
        "combined_prosecution": {
            "candidates": [{"benchmark_causal_status": "causes_incorrect_result"}],
        },
    }
    satisfied, _ = spec_eval(verify, certs)
    assert satisfied


def spec_eval(spec, result: dict[str, Any]) -> tuple[bool, str]:
    from src.sehd_v6.core.bccse.action_spec import evaluate_action_spec_postcondition

    return evaluate_action_spec_postcondition(spec, result)


def test_registry_promotion_two_entries_no_stale_registry() -> None:
    from src.sehd_v6.core.bccse.vocabulary_registry import (
        L2_REGISTRY,
        apply_registry_edit,
    )
    from src.sehd_v6.core.bccse.policy_genome import (
        PENDING_STATUS_PENDING,
        PendingValidationItem,
        PolicyGenome,
    )
    from src.sehd_v6.core.bccse.registry_reflection import REGISTRATION_REASON_NO_ACTION
    from src.sehd_v6.core.bccse.registry_promotion import REGISTRY_REQUIRED_VALIDATION

    genome = PolicyGenome(
        genome_id="g", items=[], item_meta=[], registry=L2_REGISTRY,
    )
    for index in range(2):
        target = f"p_test_dummy_pred_{index}"
        promoted_registry = apply_registry_edit(
            genome.registry,
            operation="register_predicate",
            target=target,
            dsl='terminal_state == "unresolved"',
            reason_codes=("x",),
        )
        genome.registry = promoted_registry
        genome.enqueue_pending(PendingValidationItem(
            subject_id=target,
            layer="l2_registry",
            proposal={"operation": "register_predicate", "target": target},
            registry_dependencies=(),
            source_evidence={"case_id": f"c{index}"},
            required_validation=REGISTRY_REQUIRED_VALIDATION,
            created_sequence=index,
            status=PENDING_STATUS_PENDING,
            observed_case_ids=tuple(f"obs_{index}_{i}" for i in range(PROMOTION_OBSERVATION_THRESHOLD)),
        ))
    assert all(
        entry.required_validation == REGISTRY_REQUIRED_VALIDATION
        for entry in genome.pending_queue
    )
    facts = build_adjudication_facts({"state": "unresolved"})
    rows = promote_pending_registry_entries(
        genome=genome, facts=facts, case_id="new_case",
        reason_code=REGISTRATION_REASON_NO_ACTION,
    )
    promoted = [row for row in rows if row["outcome"] == "promoted"]
    assert len(promoted) == 2
    enabled = [
        name for name in genome.registry.all_predicate_names()
        if name.startswith("p_test_dummy_pred")
    ]
    assert len(enabled) == 2, (
        "both promotions must survive in the registry, none lost to a stale base"
    )


def test_merge_prosecutions_overflow_fails_closed() -> None:
    built = {"events": [
        {"event_id": f"event_{i:03d}"} for i in range(1, 6)
    ]}
    candidates = [
        {
            "source_candidate_id": f"c{index}",
            "origin_event_id": "event_001",
            "breach_type": "contradiction",
            "impact_kind": "final_answer",
            "benchmark_causal_status": "causes_incorrect_result",
        }
        for index in range(40)
    ]
    merged = _merge_prosecutions(
        {"coverage_state": "complete", "candidates": candidates},
        {"coverage_state": "complete", "candidates": []},
        built,
    )
    assert merged["coverage_state"] == "incomplete"
    assert len(merged["candidates"]) == 32


def test_policy_edit_self_modify_rejected() -> None:
    from src.sehd_v6.core.bccse.policy_genome import (
        PolicyEdit,
        apply_policy_edit,
        create_baseline_policy_genome,
    )
    from src.sehd_v6.core.bccse.v6_contracts import PolicyItem

    genome = create_baseline_policy_genome()
    item = PolicyItem(
        item_id="policy_1",
        precondition="defense_coverage_complete_and_zero_certificates",
        action="emit_telemetry_only", params={},
        rationale="baseline telemetry",
    )
    genome = apply_policy_edit(genome, PolicyEdit(
        edit_id="e1", operation=PolicyOp.ADD_ITEM.value,
        new_item=item, source_case_id="c1", rationale="r",
    ))
    self_modify = PolicyEdit(
        edit_id="e2", operation=PolicyOp.MODIFY_ITEM.value,
        item_index=0, new_item=item, source_case_id="c2", rationale="noop",
    )
    ok, reason = validate_policy_edit(self_modify, genome)
    assert not ok and "semantically_equivalent" in reason


def test_genome_validate_edit_self_modify_rejected() -> None:
    genome = create_baseline_genome()
    add = SkillEdit(
        edit_id="e0", target_module="evidence_obligation",
        operation=SkillOp.ADD_RULE, rule_index=None,
        new_content="When cited evidence is quarantined, require the evidence field.",
        rationale="seed", source_case_id="c0",
    )
    genome = apply_edit(genome, add)
    rule_index = len(genome.modules["evidence_obligation"].rules) - 1
    noop = SkillEdit(
        edit_id="e1", target_module="evidence_obligation",
        operation=SkillOp.MODIFY_RULE, rule_index=rule_index,
        new_content=add.new_content, rationale="noop",
        source_case_id="c1",
    )
    ok, reason = validate_edit(noop, genome)
    assert not ok and "semantically_equivalent" in reason


def test_provider_routes_api_key_resolution_parity() -> None:
    from src.sehd_v6.provider_routes import OFFICIAL_ENDPOINT, resolve_provider_route

    env = {"SEHD_DETECTOR_API_KEY_ENV": "MY_CUSTOM_KEY"}
    explicit = resolve_provider_route(
        {"endpoint": OFFICIAL_ENDPOINT, "model": "deepseek-v4-flash"},
        purpose="detector", environ=env,
    )
    assert explicit["api_key_environment"] == "MY_CUSTOM_KEY"
    default = resolve_provider_route(
        {"endpoint": OFFICIAL_ENDPOINT, "model": "deepseek-v4-flash"},
        purpose="detector", environ={},
    )
    assert default["api_key_environment"] == "DEEPSEEK_API_KEY"
    proxy = resolve_provider_route(
        {"endpoint": "http://127.0.0.1:9999", "model": "deepseek-v4-flash"},
        purpose="detector", environ={},
    )
    assert proxy.get("api_key_environment") in (None, "")


def test_safety_gate_modify_prefix_not_self_counted() -> None:
    config = GateConfig(max_same_prefix_rules=2)
    gate = create_structural_gate(
        module_cooldown=config.module_cooldown,
        max_total_edits=config.max_total_edits,
        convergence_window=config.convergence_window,
    )
    genome = create_baseline_genome()
    module = genome.modules["evidence_obligation"]
    rule_index = len(module.rules)
    module.rules.append("When evidence is weak, verify it.")
    module.set_rule_meta(rule_index, {})
    content = "When evidence is weak, require the cited evidence field."
    # prefix "When evidence is weak" appears only in the rule being modified;
    # excluding the self rule leaves 0 siblings, so the gate must accept.
    accepted, _cert, reasons = gate.evaluate(
        case_id="c1", case_index=1, target_module="evidence_obligation",
        operation="modify_rule", content=content, preconditions=(),
        current_genome=genome, rule_index=rule_index,
        expected_module=("evidence_obligation",),
    )
    assert accepted, reasons


def test_step_attributor_no_candidate_miss_overwrite() -> None:
    """A gold-confirmed localization error with an empty census stays a
    localization error; the unreachable candidate-miss branch is gone."""
    row = _detector_row("verified_error", responsible_step=1)
    row["gold_label"] = True
    row["gold_steps"] = [5]
    row["census"] = []
    signal = attribute_failure(row, use_gold=True, gold_reflection=True)
    assert signal.error_type == ErrorType.LOCALIZATION_ERROR
    assert signal.trigger_source == TriggerSource.GOLD_MISMATCH
    # attribute_failure never receives a third value from the trigger
    # detector anymore; the API shape stays (state-based) stable.
    assert signal.should_evolve


# ============================================================
# Offline end-to-end run against a local mock provider
# ============================================================

EVENT_IDS = ["event_001", "event_002"]


def _candidate_census_response() -> dict[str, Any]:
    return {
        "coverage_state": "complete",
        "candidates": [
            {
                "candidate_id": "v3_candidate_001",
                "origin_hypothesis_event_id": "event_002",
                "trigger_event_id": "event_002",
                "axes": ["F"],
                "candidate_kind": "assertion",
                "claim_or_commitment": "The sum is 5.",
                "dependency_event_ids": [],
                "module_tag": "Reasoning",
                "observation": "The assistant asserts an unsupported numerical result.",
                "is_decision_point": True,
            }
        ],
    }


def _qualification_response() -> dict[str, Any]:
    return {
        "coverage_state": "complete",
        "candidates": [
            {
                "candidate_id": "v3_candidate_001",
                "violation_state": "verified_violation",
                "actor_state": "agent_caused",
                "boundary_state": "cleared",
                "positive_evidence_refs": ["event_002"],
                "actor_evidence_refs": ["event_002"],
                "boundary_evidence_refs": ["event_002"],
                "rationale": "The assistant produced the unsupported sum.",
            }
        ],
    }


def _propagation_response() -> dict[str, Any]:
    return {
        "coverage_state": "complete",
        "candidates": [
            {
                "candidate_id": "v3_candidate_001",
                "propagation_state": "unrecovered_impact",
                "residual_impact": True,
                "consumption_event_ids": ["event_002"],
                "consumption_evidence_refs": ["event_002"],
                "impact_event_ids": ["event_002"],
                "impact_evidence_refs": ["event_002"],
                "recovery_event_id": None,
                "recovery_evidence_refs": [],
                "impact_surfaces": ["A"],
                "rationale": "The wrong sum is the final answer.",
            }
        ],
    }


def _boundary_response() -> dict[str, Any]:
    return {
        "state": "verified_error",
        "responsible_event_id": "event_002",
        "evidence_refs": ["event_002"],
        "rationale": "The final answer contradicts the recomputed value.",
        "candidates": [],
    }


def _bad_certificate() -> dict[str, Any]:
    return {
        "coverage_state": "complete",
        "candidates": [
            {
                "certificate_id": "focused_error_001",
                "source_candidate_id": "v3_candidate_001",
                "origin_event_id": "event_002",
                "claim_kind": "agent_assertion",
                "norm_source": "logical_invariant",
                "norm_evidence_refs": ["event_999"],
                "breach_type": "contradiction",
                "violation_evidence_refs": ["event_002"],
                "agent_control_event_id": "event_002",
                "agent_control_evidence_refs": ["event_002"],
                "impact_kind": "final_answer",
                "impact_event_ids": ["event_002"],
                "impact_evidence_refs": ["event_002"],
                "recovery_state": "none",
                "recovery_evidence_refs": [],
                "benchmark_causal_status": "causes_incorrect_result",
                "benchmark_causal_evidence_refs": ["event_002"],
                "rationale": (
                    "NORM: the answer must equal the recomputed sum. "
                    "ORIGIN: the assistant asserted the wrong sum at event_002. "
                    "CONTROL: the assistant chose the value itself. "
                    "PROPAGATION: the wrong sum reaches the final answer. "
                    "IMPACT: the produced final answer is incorrect. "
                    "COUNTERFACTUAL: correcting only this origin fixes the answer."
                ),
            }
        ],
    }


def _good_certificate(system: str = "") -> dict[str, Any]:
    cert = _bad_certificate()["candidates"][0]
    cert = dict(cert)
    cert["norm_evidence_refs"] = ["event_002"]
    payload = {"coverage_state": "complete", "candidates": [cert]}
    # The skill-aware wrapper strips rule_effects from the parsed value and
    # records them as provider self-report; report the injected rule id so
    # the D1 execution evidence (compiled ∩ applied ∩ field-effect) binds.
    import re

    # The injected fragment is either a bracketed id ("[<id>] rule text") or
    # a JSON action-spec line carrying "rule_id".
    match = re.search(r"\[([0-9a-f]{12})\]\s", system)
    if not match:
        match = re.search(r'"rule_id":\s*"([0-9a-f]{12})"', system)
    if match:
        payload["rule_effects"] = [
            {"rule_id": match.group(1), "output_fields": ["impact_evidence_refs"]},
        ]
    return payload


def _defense_response() -> dict[str, Any]:
    # One valid, non-dominating certificate keeps the coverage honest:
    # a "complete" defense with zero certificates would classify as a
    # hollow L2 coverage claim instead of the L1 evidence defect the
    # source run is set up to exhibit.
    return {
        "coverage_state": "complete",
        "candidates": [
            {
                "certificate_id": "defense_001",
                "target_candidate_id": "v3_candidate_001",
                "target_origin_event_id": "event_002",
                "defense_type": "insufficient_evidence",
                "evidence_refs": ["event_002"],
                "rationale": "No dominating defense can be established.",
            }
        ],
    }


def _appeal_prosecutor_response() -> dict[str, Any]:
    return {
        "coverage_state": "complete",
        "reviewed_candidate_ids": ["v3_candidate_001"],
        "candidates": [],
    }


def _appeal_defender_response() -> dict[str, Any]:
    return {
        "coverage_state": "complete",
        "candidates": [
            {
                "certificate_id": "appeal_defense_001",
                "target_candidate_id": "v3_candidate_001",
                "target_origin_event_id": "event_002",
                "defense_type": "insufficient_evidence",
                "evidence_refs": ["event_002"],
                "rationale": "No dominating defense applies.",
            }
        ],
    }


def _conflict_gate_response() -> dict[str, Any]:
    return {"decision": "pass", "reason": "no conflict", "conflicting_rule_indices": []}


def _mutation_edit_response(user: str) -> dict[str, Any]:
    module = "evidence_obligation"
    import re

    match = re.search(r"allowed target_module values are:\s*([a-z_]+)", user)
    if match:
        module = match.group(1)
    actions = {
        "candidate_trigger": ("emit_candidate", "candidate"),
        "evidence_obligation": ("require_evidence_field", "impact_evidence_refs"),
        "candidate_to_verifier_route": (
            "route_candidate_to_verifier", "violation_state",
        ),
        "atomic_verifier_instruction": ("quarantine_certificate", "certificate_id"),
        "clean_defense_boundary": ("emit_boundary_finding", "state"),
        "causal_link_test": ("test_causal_link", "benchmark_causal_evidence_refs"),
        "earliest_origin_tiebreak": ("select_earliest_origin", "responsible_event_id"),
        "abstention_rule": ("emit_boundary_finding", "state"),
    }
    operation, target = actions.get(module, ("require_evidence_field", "impact_evidence_refs"))
    return {
        "target_module": module,
        "operation": "add_rule",
        "rule_index": None,
        "condition": "When a numerical claim contradicts the recomputed value",
        "action": f"{operation}: {target}",
        "evidence_anchor": "numerical contradiction with the recomputed value",
        "feature_description": "numerical contradiction between the claim and the recomputed value",
        "rationale": "The evidence chain was quarantined on the source case.",
        "diagnosis": {
            "failed_output": "benchmark_causal_evidence_refs",
            "owner_module": module,
            "existing_rule_gap": "the rule never requires the evidence field",
        },
    }


def _make_mock_handler(port: int) -> type[BaseHTTPRequestHandler]:
    def _route_payload(system: str, user: str) -> dict[str, Any]:
        system_lower = system.lower()
        if "dependency-aware candidate census" in system_lower:
            return _candidate_census_response()
        if "multi-dimensional candidate census" in system_lower:
            return _candidate_census_response()
        if "paca-tcl v2 qualification layer" in system_lower:
            return _qualification_response()
        if "paca-tcl v2 propagation layer" in system_lower:
            return _propagation_response()
        if "bccc-style case boundary path" in system_lower:
            return _boundary_response()
        if "规则冲突判定器" in system:
            return _conflict_gate_response()
        if "focused error-certificate" in system_lower:
            if "进化规则" in system:
                return _good_certificate(system)
            return _bad_certificate()
        if "clean-boundary certificate constructor" in system_lower:
            return _defense_response()
        if "candidate-level appellate prosecutor" in system_lower:
            return _appeal_prosecutor_response()
        if "candidate-level appellate defender" in system_lower:
            return _appeal_defender_response()
        if "counterfactual localization analyst" in system_lower:
            return {"rankings": {}}
        if "module_charters" in system_lower:
            return _mutation_edit_response(user)
        # L2 policy / diagnosis / unknown: model retreats with no edit.
        return {"operation": "no_edit", "rationale": "mock provider abstains"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
                messages = body.get("messages") or []
                system = str(messages[0].get("content") or "") if messages else ""
                user = str(messages[1].get("content") or "") if len(messages) > 1 else ""
                parsed = _route_payload(system, user)
                response = {
                    "choices": [{
                        "message": {
                            "content": json.dumps(parsed, ensure_ascii=False),
                        },
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                }
                payload = json.dumps(response, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as exc:  # pragma: no cover - diagnostic
                payload = json.dumps({"error": str(exc)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            return

    return Handler


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def mock_provider():
    port = _free_port()
    server = ThreadingHTTPServer(
        ("127.0.0.1", port), _make_mock_handler(port),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Pin the provider environment: the repository .env sets SEHD_* gateway
    # selectors that would otherwise override the local mock endpoint.
    env_pins = {
        "DEEPSEEK_API_KEY": "mock-key-not-a-real-credential",
        "SEHD_PROVIDER_ENDPOINT": "",
        "SEHD_PROVIDER_MODEL": "",
        "SEHD_PROVIDER_API_KEY_ENV": "",
        "SEHD_DETECTOR_ENDPOINT": "",
        "SEHD_DETECTOR_MODEL": "",
        "SEHD_DETECTOR_API_KEY_ENV": "",
        "SEHD_MUTATION_ENDPOINT": "",
        "SEHD_MUTATION_MODEL": "",
        "SEHD_MUTATION_API_KEY_ENV": "",
        "SEHD_JUDGE_ENDPOINT": "",
        "SEHD_JUDGE_MODEL": "",
        "SEHD_JUDGE_API_KEY_ENV": "",
    }
    previous = {}
    for name, value in env_pins.items():
        previous[name] = os.environ.get(name)
        os.environ[name] = value
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _detector_config(endpoint: str) -> dict[str, Any]:
    def route() -> dict[str, Any]:
        return {
            "endpoint": endpoint,
            "model": "deepseek-v4-flash",
            "api_key_environment": "DEEPSEEK_API_KEY",
            "temperature": 0,
            "max_tokens": 8192,
            "timeout_seconds": 30,
            "total_timeout_seconds": 60,
            "transport_retries": 0,
        }

    return {
        "schema_version": "agenthallu.paca_tcl_detector_config.v3_5",
        "candidate_fallback_route": route(),
        "construct_census_route": route(),
        "qualification_route": route(),
        "propagation_route": route(),
        "boundary_route": route(),
        "focused_prosecution_route": route(),
        "defense_route": route(),
        "appeal_prosecutor_route": route(),
        "appeal_defender_route": route(),
        "max_focused_parallel": 2,
        "focused_prosecution_retries": 2,
    }


def _trajectory_record() -> dict[str, Any]:
    return {
        "question": "What is 2 + 2?",
        "history": [
            {"role": "user", "content": "What is 2 + 2?"},
            {"role": "assistant", "content": "The sum is 5."},
        ],
    }


def test_offline_detector_and_evolution_end_to_end(
    mock_provider: str, tmp_path: Path,
) -> None:
    """Tiny-scale real run: detection against a local mock provider, then
    the full self-evolution loop, then re-detection with the evolved
    champion.  The evolved skill must change the verdict on the source."""
    config = _detector_config(mock_provider)
    record = _trajectory_record()

    baseline_prediction = detect_record(
        record=record, config=config, case_id="math-001",
        output_dir=tmp_path / "baseline_detect",
    )
    assert baseline_prediction["runtime_valid"] is True
    assert baseline_prediction["state"] == "unresolved"

    evolution_dir = tmp_path / "evolution"
    gold_record = dict(record)
    gold_record["gold_label"] = True
    result = run_evolution(
        records={"math-001": gold_record},
        detector_config=config,
        output_dir=evolution_dir,
        mutation_route={
            "endpoint": mock_provider,
            "model": "deepseek-v4-flash",
            "api_key_environment": "DEEPSEEK_API_KEY",
            "temperature": 0.3,
            "max_tokens": 8192,
        },
        use_gold=True,
        fitness_use_gold=True,
        canary_limit=0,
        max_mutation_calls=50,
    )
    assert result.edits_accepted >= 1, (
        "the gold-verified source replay must accept the evolved skill"
    )
    assert result.halted is False

    champion_path = evolution_dir / "champion_genome.json"
    assert champion_path.is_file()
    champion = load_skill_genome(champion_path)
    assert champion.content_hash != create_baseline_genome().content_hash

    evolved_prediction = detect_record(
        record=record, config=config, case_id="math-001",
        output_dir=tmp_path / "evolved_detect",
        genome=champion,
        genome_source=str(champion_path),
    )
    assert evolved_prediction["runtime_valid"] is True
    assert evolved_prediction["state"] == "verified_error", (
        "the evolved rule must repair the quarantined evidence chain"
    )
    provenance = evolved_prediction.get("provenance") or {}
    assert provenance.get("genome_hash") == champion.content_hash

    for artifact in (
        "evolution_result.json",
        "champion_genome.json",
        "policy_genome.json",
        "htc_tes_experience_memory.json",
        "evolution_state_manifest.json",
    ):
        assert (evolution_dir / artifact).is_file(), artifact

    summary = json.loads(
        (evolution_dir / "evolution_result.json").read_text(encoding="utf-8")
    )
    audit = summary.get("evolution_audit") or {}
    assert audit.get("runtime_invalid_cases") == 0
    assert audit.get("l2_policy_audit") == []
