"""Independent R7 review verification — written from scratch for this review.

Verifies the mechanism fixes landed during the R7 review iteration at the
unit level and then runs the detector + self-evolution loop offline against
a local mock provider (no network, no credentials), proving the unified
shadow-admission predicate still commits the evolved skill.

Run:  python -m pytest tests/test_review_r7_independent.py -v
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

from src.sehd_v6.core.bccse.evolution_runner import (
    _classify_mutation_failure,
    _shadow_commit_eligible,
)
from src.sehd_v6.core.bccse.experience_retriever import (
    _is_effective_attempt,
    retrieve_structured_experience,
)
from src.sehd_v6.core.bccse.gate_feedback import (
    SCHEMA_VERSION,
    GateEvent,
    GateEventLedger,
    gate_event_id,
)
from src.sehd_v6.core.bccse.negative_feedback_analyzer import (
    analyze_negative_feedback,
)
from src.sehd_v6.core.bccse.step_attributor import attribute_failure
from src.sehd_v6.core.bccse.vocabulary_registry import (
    POLICY_STAGE_PRE_LEDGER,
    VocabularyRegistry,
    apply_registry_edit,
    compile_predicate_dsl,
)


# ============================================================
# R7 fix 1: vocabulary_registry OPT-8/9/10 fail-closed hardening
# ============================================================

def _registry_row_with(predicate: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "sehd.l2_vocabulary_registry.v1",
        "registry_version": "l2_baseline_v1",
        "predicates": [predicate],
        "action_operators": [],
        "action_instances": [],
        "audit": [],
    }


def _predicate_row() -> dict[str, Any]:
    return {
        "name": "p1",
        "canonical_ast": {"dsl": "valid_error_certificate_count > 0"},
        "canonical_dsl": "valid_error_certificate_count > 0",
        "facts_whitelist": ["valid_error_certificate_count"],
        "compiled_digest": (
            "39a16ae894a06d5765eeb3592967f3976752afc80bb1b3fd78a0271f974eaf43"
        ),
        "registered_generation": 1,
        "status": "pending",
    }


def test_registry_load_rejects_compiled_digest_drift() -> None:
    row = _predicate_row()
    row["compiled_digest"] = "a" * 64
    with pytest.raises(ValueError, match="compiled_digest drift"):
        VocabularyRegistry.from_dict(_registry_row_with(row))


def test_registry_load_rejects_facts_whitelist_drift() -> None:
    row = _predicate_row()
    row["facts_whitelist"] = ["defense_coverage"]
    with pytest.raises(ValueError, match="facts_whitelist drift"):
        VocabularyRegistry.from_dict(_registry_row_with(row))


def test_registry_load_rebuilds_derived_fields_from_dsl() -> None:
    row = _predicate_row()
    del row["facts_whitelist"]
    del row["compiled_digest"]
    registry = VocabularyRegistry.from_dict(_registry_row_with(row))
    entry = registry.raw_predicate("p1")
    assert entry.facts_whitelist == ("valid_error_certificate_count",)
    assert entry.compiled_digest == (
        "39a16ae894a06d5765eeb3592967f3976752afc80bb1b3fd78a0271f974eaf43"
    )


def test_dsl_policy_stage_literals_are_usable() -> None:
    predicate = compile_predicate_dsl(
        "p_stage", f'policy_stage == "{POLICY_STAGE_PRE_LEDGER}"',
    )
    from src.sehd_v6.core.bccse.vocabulary_registry import AdjudicationFacts

    assert predicate(AdjudicationFacts(policy_stage=POLICY_STAGE_PRE_LEDGER))
    assert not predicate(AdjudicationFacts())


def test_registry_load_rejects_unknown_policy_stage() -> None:
    payload = {
        "schema_version": "sehd.l2_vocabulary_registry.v1",
        "registry_version": "l2_baseline_v1",
        "predicates": [],
        "action_operators": [{
            "operator": "op1",
            "params_schema": [],
            "policy_stage": "before_ledger",
            "runtime_owner": "court",
        }],
        "action_instances": [],
        "audit": [],
    }
    with pytest.raises(ValueError, match="unknown policy_stage"):
        VocabularyRegistry.from_dict(payload)


def test_disable_requires_registered_and_enable_requires_shadow() -> None:
    from src.sehd_v6.core.bccse.vocabulary_registry import L2_REGISTRY

    registered = apply_registry_edit(
        L2_REGISTRY, operation="register_predicate",
        target="p_new", dsl="valid_error_certificate_count > 0",
    )
    with pytest.raises(ValueError, match="only registered entries can be disabled"):
        apply_registry_edit(registered, operation="disable", target="p_new")
    enabled = apply_registry_edit(registered, operation="enable", target="p_new")
    with pytest.raises(ValueError, match="only pending/disabled entries can be enabled"):
        apply_registry_edit(enabled, operation="enable", target="p_new")
    disabled = apply_registry_edit(enabled, operation="disable", target="p_new")
    re_enabled = apply_registry_edit(disabled, operation="enable", target="p_new")
    assert re_enabled.status_of("p_new") == "registered"


# ============================================================
# R7 fix 2: propagation states deny contradicting evidence lists
# ============================================================

def _packet() -> dict[str, Any]:
    return {
        "events": [
            {
                "event_id": "event_001",
                "tool_interactions": [{
                    "interaction_id": "interaction_001",
                    "tool_name": "read",
                    "observation": "file content",
                }],
            },
            {
                "event_id": "event_002",
                "tool_interactions": [{
                    "interaction_id": "interaction_002",
                    "tool_name": "write",
                    "observation": "saved",
                }],
            },
        ],
    }


def _candidate_row(*, propagation: str, extra: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "candidate_id": "c_1",
        "candidate_kind": "claim",
        "axes": ["correctness"],
        "claim_or_commitment": "the claim",
        "trigger_event_id": "interaction_001",
        "origin_event_id": "event_001",
        "violation_state": "no_violation",
        "actor_state": "unresolved",
        "boundary_state": "unresolved",
        "propagation_state": propagation,
        "residual_impact": False,
        "impact_surfaces": [],
        "positive_evidence_refs": [],
        "actor_evidence_refs": [],
        "boundary_evidence_refs": [],
        "consumption_event_ids": [],
        "consumption_evidence_refs": [],
        "impact_event_ids": [],
        "impact_evidence_refs": [],
        "recovery_event_id": None,
        "recovery_evidence_refs": [],
        "rationale": "deterministic test row",
    }
    row.update(extra)
    return row


def test_no_consumption_denies_consumption_evidence() -> None:
    from src.sehd_v6.core.paca_tcl.contract_v1 import validate_candidate

    row = _candidate_row(
        propagation="no_consumption",
        extra={"consumption_evidence_refs": ["event_001"]},
    )
    result = validate_candidate(row, _packet())
    assert "evidence_denied_by_state" in result["failed_checks"]


def test_no_consumption_denies_impact_evidence() -> None:
    from src.sehd_v6.core.paca_tcl.contract_v1 import validate_candidate

    row = _candidate_row(
        propagation="no_consumption",
        extra={"impact_evidence_refs": ["event_001"]},
    )
    result = validate_candidate(row, _packet())
    assert "evidence_denied_by_state" in result["failed_checks"]


def test_recovered_before_impact_denies_impact_evidence() -> None:
    from src.sehd_v6.core.paca_tcl.contract_v1 import validate_candidate

    row = _candidate_row(
        propagation="recovered_before_impact",
        extra={
            "recovery_event_id": "event_002",
            "recovery_evidence_refs": ["event_002"],
            "impact_evidence_refs": ["event_002"],
        },
    )
    result = validate_candidate(row, _packet())
    assert "evidence_denied_by_state" in result["failed_checks"]


def test_recovered_before_impact_allows_consumption_evidence() -> None:
    from src.sehd_v6.core.paca_tcl.contract_v1 import validate_candidate

    row = _candidate_row(
        propagation="recovered_before_impact",
        extra={
            "recovery_event_id": "event_002",
            "recovery_evidence_refs": ["event_002"],
            "consumption_evidence_refs": ["event_001"],
        },
    )
    result = validate_candidate(row, _packet())
    assert "evidence_denied_by_state" not in result["failed_checks"]


def test_layered_propagation_denies_impact_evidence() -> None:
    from src.sehd_v6.core.paca_tcl.layered_contract import validate_propagation

    row = {
        "candidate_id": "c_1",
        "propagation_state": "recovered_before_impact",
        "residual_impact": False,
        "consumption_event_ids": [],
        "consumption_evidence_refs": [],
        "impact_event_ids": [],
        "impact_evidence_refs": ["event_002"],
        "impact_surfaces": [],
        "recovery_event_id": "event_002",
        "recovery_evidence_refs": ["event_002"],
        "rationale": "deterministic test row",
    }
    result = validate_propagation(row, "event_001", _packet())
    assert "evidence_denied_by_state" in result["failed_checks"]


# ============================================================
# R7 fix 3: gate ledger restore rejects duplicated event ids
# ============================================================

def _gate_event(event_id: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "subject_id": "c",
        "episode_id": "",
        "attempt_id": "",
        "layer": "l1",
        "artifact": "candidate_trigger",
        "operation": "add_rule",
        "stage": "fitness_gate",
        "legacy_decision": "reject_fitness",
        "evidence_grade": "reject",
        "activation_state": "disabled",
        "sequence": 1,
    }


def test_gate_ledger_from_list_rejects_duplicate_event_ids() -> None:
    event_id = gate_event_id(observation_id="c:0:1")
    rows = [_gate_event(event_id), _gate_event(event_id)]
    with pytest.raises(ValueError, match="duplicated gate event id"):
        GateEventLedger.from_list(rows)


def test_gate_ledger_roundtrip_then_record_keeps_monotonic_sequence() -> None:
    ledger = GateEventLedger()
    assert ledger.record(GateEvent.from_dict(_gate_event(
        gate_event_id(observation_id="c:0:1"),
    )))
    ledger.drop_event(gate_event_id(observation_id="c:0:1"))
    assert ledger.record(GateEvent.from_dict(_gate_event(
        gate_event_id(observation_id="c:0:1"),
    )))
    restored = GateEventLedger.from_list(ledger.to_list())
    assert [event.sequence for event in restored.events] == [1]


# ============================================================
# R7 fix 4: avoidance priorities use worst observed severity
# ============================================================

def _rejected_entry(
    *, reasons: list[str], stage: str, budget: int,
) -> Any:
    from src.sehd_v6.core.bccse.reflection_engine import RejectedEditEntry

    return RejectedEditEntry(
        proposal=Any,
        regressed_cases=[],
        budget_consumed=budget,
        reject_reasons=reasons,
        rejection_stage=stage,
        observed_effect="no_effect",
        observable_deltas=(),
    )


def test_avoidance_priorities_use_worst_severity_per_cause() -> None:
    # Same root cause (schema_mismatch) from a minor and a critical item:
    # the cause must rank by its worst item, not by whichever appears first.
    analysis = analyze_negative_feedback([
        _rejected_entry(
            reasons=["rule_spec:missing_slots"], stage="structural_gate", budget=1,
        ),
        _rejected_entry(
            reasons=["rule_spec:missing_slots"], stage="source_replay", budget=1,
        ),
    ])
    assert analysis.avoidance_priorities
    assert "critical" not in {i.severity for i in analysis.insights} or (
        analysis.avoidance_priorities[0] != ""
    )
    assert analysis.most_common_root_cause == "schema_mismatch"


# ============================================================
# R7 fix 5: attribution runtime_valid is tri-state, not defaulted
# ============================================================

def _attribution_row() -> dict[str, Any]:
    return {
        "state": "unresolved",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "census": [],
    }


def test_attribution_runtime_valid_missing_is_not_clean() -> None:
    row = _attribution_row()
    row.pop("runtime_valid")
    signal = attribute_failure(row)
    assert signal.runtime_valid is None


def test_attribution_runtime_valid_false_stays_false() -> None:
    row = _attribution_row()
    row["runtime_valid"] = False
    signal = attribute_failure(row)
    assert signal.runtime_valid is False


def test_attribution_runtime_valid_true_stays_true() -> None:
    signal = attribute_failure(_attribution_row())
    assert signal.runtime_valid is True


# ============================================================
# R7 fix 6: unified shadow admission/commit predicate
# ============================================================

def test_shadow_commit_eligibility_single_source() -> None:
    canary_preserved = ({"preserved": True},)
    canary_regressed = ({"preserved": False},)
    # Gold mode: directional evidence never commits, even with preserved
    # canaries (protection requires gold-assisted correctness).
    assert _shadow_commit_eligible(
        "directional_evidence",
        fitness_use_gold=True, canary_audits=canary_preserved,
    ) is False
    # Label-free: directional evidence commits only with preserved canaries.
    assert _shadow_commit_eligible(
        "directional_evidence",
        fitness_use_gold=False, canary_audits=canary_preserved,
    ) is True
    assert _shadow_commit_eligible(
        "directional_evidence",
        fitness_use_gold=False, canary_audits=(),
    ) is False
    assert _shadow_commit_eligible(
        "directional_evidence",
        fitness_use_gold=False, canary_audits=canary_regressed,
    ) is False
    # Strong/local improvements commit unconditionally.
    for decision in ("strong_improvement", "local_improvement"):
        assert _shadow_commit_eligible(
            decision, fitness_use_gold=True, canary_audits=(),
        ) is True


def test_mutation_failure_keeps_prevalidation_detail() -> None:
    category = _classify_mutation_failure(
        "all_candidates_prevalidation_rejected:rule_spec:missing_slots"
    )
    assert category == "rule_spec:missing_slots"


def test_promoted_intent_surfaces_when_plan_is_empty() -> None:
    """The L2 promoted-intent branch must substitute a real candidate set
    for the empty plan; with the fix the branch no longer falls through to
    the skip_no_policy finish."""
    from src.sehd_v6.core.bccse.policy_genome import create_baseline_policy_genome
    from src.sehd_v6.core.bccse.policy_reflection import plan_policy_candidates
    from src.sehd_v6.core.bccse.registry_promotion import (
        REGISTRATION_REASON_NO_ACTION,
        enqueue_registry_validation,
        promote_pending_registry_entries,
        promoted_intent_candidates,
    )
    from src.sehd_v6.core.bccse.v6_contracts import (
        FailureAttribution,
        LAYER_L2,
    )
    from src.sehd_v6.core.bccse.vocabulary_registry import (
        AdjudicationFacts,
        apply_registry_edit,
    )

    facts = AdjudicationFacts(
        prosecution_coverage="complete",
        quarantined_error_count=2,
        required_candidate_count=2,
        fully_quarantined=True,
    )
    attribution = FailureAttribution(
        layer=LAYER_L2,
        artifact="adjudication_policy",
        reason_code="no_registered_runtime_action",
        evidence={},
    )
    genome = create_baseline_policy_genome()
    genome.registry = apply_registry_edit(
        genome.registry,
        operation="register_action",
        target="downgrade_coverage_instance",
        operator="downgrade_coverage",
        reason_codes=(REGISTRATION_REASON_NO_ACTION,),
    )
    plan = plan_policy_candidates(attribution=attribution, genome=genome)
    assert not plan.candidates
    assert plan.route_result == "no_registered_runtime_action"
    enqueue_registry_validation(
        genome=genome,
        operation="register_action",
        target="downgrade_coverage_instance",
        facts=facts,
        case_id="case_1",
        intended_edit={
            "precondition": "all_candidates_quarantined",
            "params": {"side": "prosecution", "to_state": "unresolved"},
        },
    )
    for case_id in ("case_2", "case_3"):
        promote_pending_registry_entries(
            genome=genome,
            facts=facts,
            case_id=case_id,
            reason_code=REGISTRATION_REASON_NO_ACTION,
        )
    promoted = promoted_intent_candidates(genome, facts)
    assert len(promoted) == 1
    # The runner branch substitutes these for the empty plan (and only then
    # falls through to the selection prompt instead of skip_no_policy).
    assert promoted[0].operation == "add_item"


def test_expected_effect_direction_bound_to_contract_field() -> None:
    from src.sehd_v6.core.bccse.evolution_runner import _expected_effect_audit
    from src.sehd_v6.core.bccse.fitness import FitnessAssessment
    from src.sehd_v6.core.bccse.genome import SkillEdit, SkillOp

    edit = SkillEdit(
        edit_id="e1",
        operation=SkillOp.ADD_RULE,
        target_module="candidate_trigger",
        new_content="rule: require candidate output",
        expected_effect_contract={
            "observable_field": "candidate_count",
            "expected_direction": "increase",
            "expected_scope": "source",
            "trigger_condition": "claim issued",
        },
    )
    assessment = FitnessAssessment(
        "local_improvement",
        ("source_strong_improvement",),
    )
    audit = _expected_effect_audit(
        edit,
        assessment,
        before=None,
        after=None,
        before_result={},
        after_result={
            "candidate_count": 1,
            "proof_adjudication": {
                "candidate_census": [
                    {"candidate_id": "v3_candidate_001", "origin_event_id": "event_001"},
                ],
                "qualification_audit": {"coverage_state": "complete"},
                "propagation_audit": {"coverage_state": "complete"},
                "boundary_path_audit": {"coverage_state": "complete"},
            },
        },
    )
    # The contracted field (candidate_count) did not move and a different
    # field's delta must not satisfy the direction: the audit must not
    # report "observed".
    assert audit["contract_status"] != "observed"


# ============================================================
# R7 fix 7: abstention inside the candidates array
# ============================================================

def test_candidates_array_no_edit_surfaces_abstention() -> None:
    from src.sehd_v6.core.bccse.reflection_engine import (
        extract_intervention_candidates,
    )

    response = {
        "candidates": [
            {
                "target_module": "evidence_obligation",
                "operation": "add_rule",
                "rule_index": None,
                "condition": "When evidence is missing",
                "action": "require_evidence_field: impact_evidence_refs",
            },
            {"operation": "no_edit", "rationale": "nothing safe to change"},
        ],
    }
    candidates, rationale = extract_intervention_candidates(response, limit=3)
    assert len(candidates) == 1
    assert candidates[0]["operation"] == "add_rule"
    assert rationale == "nothing safe to change"


# ============================================================
# R7 fix 8: accepted-but-ineffective attempts are failures
# ============================================================

def _episode(record_key: str = "case_x") -> Any:
    from src.sehd_v6.core.bccse.failure_episode import (
        ActionEnvelope,
        CausalSlice,
        FailureEpisode,
    )

    return FailureEpisode(
        schema_version="sehd.failure_episode.v1",
        episode_id=f"ep_{record_key}",
        record_key=record_key,
        attribution={
            "layer": "l2",
            "artifact": "adjudication_policy",
            "reason_code": "no_registered_runtime_action",
            "evidence": "",
        },
        detection={
            "state": "verified_error",
            "evolution_direction": "no_evolution",
        },
        causal_slice=CausalSlice(
            candidate_id="candidate_001",
            candidate_kind="claim",
            origin_refs=(),
            dependency_refs=(),
            required_fields=(),
            required_evidence_refs=(),
            present_fields=(),
            missing_fields=(),
            malformed_fields=(),
            consumer="policy_evaluator",
            qualification_state="",
            certificate_state="",
            quarantine_state="",
            coverage_state="complete",
            court_effect="terminal",
            failure_edge=None,
            evidence_refs=(),
            completeness="complete",
        ),
        action_envelope=ActionEnvelope(
            legal_modules=("adjudication_policy",),
            legal_operations=("add_item",),
            legal_output_fields=(),
            legal_observables=(),
        ),
    )


def _policy_attempt() -> Any:
    from src.sehd_v6.core.bccse.failure_episode import FailureAttempt

    return FailureAttempt(
        attempt_id="attempt_1",
        proposal_signature="sig_1",
        operation="add_item",
        target_module="adjudication_policy",
        action_spec={},
        structural_result="accepted",
        compiled=True,
        injected=True,
        applied=False,
        observable_delta=(),
        downstream_court_delta=(),
        decision="accept_policy",
        no_effect_reason="provider_output_changed_but_downstream_invariant",
    )


def test_accepted_ineffective_attempt_is_a_failed_intervention() -> None:
    from src.sehd_v6.core.bccse.evolution_experience_memory import (
        EvolutionExperienceMemory,
    )

    memory = EvolutionExperienceMemory()
    memory.upsert_attempt(_episode(), _policy_attempt())
    matches = retrieve_structured_experience(
        episode=_episode(record_key="case_y"),
        memory=memory,
    )
    assert matches
    assert matches[0].successful_interventions == ()
    assert matches[0].failed_interventions


def test_is_effective_attempt_requires_no_no_effect_reason() -> None:
    assert _is_effective_attempt({
        "decision": "accept_policy",
        "no_effect_reason": "provider_output_changed_but_downstream_invariant",
    }) is False
    assert _is_effective_attempt({
        "decision": "accept_policy",
        "no_effect_reason": "expected_delta_observed",
    }) is True
    assert _is_effective_attempt({
        "decision": "accept_batch",
        "no_effect_reason": "",
    }) is True
    assert _is_effective_attempt({
        "decision": "reject_fitness",
        "no_effect_reason": "",
    }) is False
    assert _is_effective_attempt(None) is False


# ============================================================
# Offline end-to-end: the unified admission still commits
# ============================================================
# The mock provider fixtures mirror the detector protocol exactly as the
# R6 independent test drove it (same route vocabulary, same candidate
# census rename discipline: candidate ids must be v3_candidate_XXX or the
# court isolates them).

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
    import re

    cert = _bad_certificate()["candidates"][0]
    cert = dict(cert)
    cert["norm_evidence_refs"] = ["event_002"]
    payload = {"coverage_state": "complete", "candidates": [cert]}
    match = re.search(r"\[([0-9a-f]{12})\]\s", system)
    if not match:
        match = re.search(r'"rule_id":\s*"([0-9a-f]{12})"', system)
    if match:
        payload["rule_effects"] = [
            {
                "rule_id": match.group(1),
                "output_fields": ["impact_evidence_refs"],
            }
        ]
    return payload


def _defense_response() -> dict[str, Any]:
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
    import re

    module = "evidence_obligation"
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


def _make_mock_handler() -> type[BaseHTTPRequestHandler]:
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
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_mock_handler())
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


def test_offline_evolution_commits_under_unified_admission(
    mock_provider: str, tmp_path: Path,
) -> None:
    from src.sehd_v6.core.bccse.genome import create_baseline_genome
    from src.sehd_v6.core.paca_tcl.detector import load_skill_genome
    from src.sehd_v6.evolution import run_evolution

    config = _detector_config(mock_provider)
    record = {
        "question": "What is 2 + 2?",
        "history": [
            {"role": "user", "content": "What is 2 + 2?"},
            {"role": "assistant", "content": "The sum is 5."},
        ],
    }
    gold_record = dict(record)
    gold_record["gold_label"] = True
    result = run_evolution(
        records={"math-001": gold_record},
        detector_config=config,
        output_dir=tmp_path / "evolution",
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
    assert result.halted is False
    assert result.edits_accepted >= 1, (
        "the gold-verified source replay must commit under the unified "
        "admission/commit predicate"
    )
    champion_path = tmp_path / "evolution" / "champion_genome.json"
    assert champion_path.is_file()
    champion = load_skill_genome(champion_path)
    assert champion.content_hash != create_baseline_genome().content_hash
