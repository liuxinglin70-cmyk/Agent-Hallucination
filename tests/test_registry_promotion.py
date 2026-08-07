"""Regression tests for the pending-to-working registry promotion path (G3).

Pins the third deep-review gap: a registered vocabulary entry was pending
(shadow-only) forever — no code path ever transitioned it to working, and
even an enabled action alias could not execute because the evaluator
dispatched on the action name instead of the operator.

- P1: the motivating case is the first observation; a predicate that never
  fires on its own motivating facts is rejected at enqueue time;
- P2: promotion needs PROMOTION_OBSERVATION_THRESHOLD distinct cases and
  deduplicates the same case id;
- P3: a non-registered-action attribution never advances the queue;
- P4: promotion re-enables the entry (registered), marks the queue entry
  validated, and the enabled predicate is active;
- P5: the post-ledger evaluator executes a promoted action alias through
  its operator;
- P6: the pre-ledger evaluator resolves an alias to the restore operator;
- P7: queue round-trip preserves observed_case_ids (checkpoint/resume);
- P8: a registration may carry an intended edit (precondition + params),
  validated fail-closed, stored with the queue entry, and surfaced as a
  concrete policy candidate only after promotion;
- P9: detector-side provider metas (per-surface usage) are recorded into
  the same usage ledger the mutation path uses.
"""

from __future__ import annotations

from src.sehd_v6.core.bccse.policy_genome import (
    PENDING_STATUS_PENDING,
    PENDING_STATUS_REJECTED,
    PENDING_STATUS_VALIDATED,
    create_baseline_policy_genome,
)
from src.sehd_v6.core.bccse.registry_promotion import (
    PROMOTION_OBSERVATION_THRESHOLD,
    REGISTRY_REQUIRED_VALIDATION,
    enqueue_registry_validation,
    promote_pending_registry_entries,
)
from src.sehd_v6.core.bccse.vocabulary_registry import (
    AdjudicationFacts,
    L2_REGISTRY,
    apply_registry_edit,
)

REGISTRATION_REASON = "no_registered_runtime_action"

FACTS_QUARANTINED = AdjudicationFacts(
    prosecution_coverage="complete",
    quarantined_error_count=2,
    required_candidate_count=2,
    fully_quarantined=True,
)
FACTS_CLEAN = AdjudicationFacts()


def _register_predicate(genome, target="quarantined_error_count_nonzero"):
    registry = apply_registry_edit(
        genome.registry,
        operation="register_predicate",
        target=target,
        dsl="quarantined_error_count > 0",
        reason_codes=(REGISTRATION_REASON,),
    )
    genome.registry = registry
    return registry


def _register_action_alias(genome, target="downgrade_coverage_instance"):
    registry = apply_registry_edit(
        genome.registry,
        operation="register_action",
        target=target,
        operator="downgrade_coverage",
        reason_codes=(REGISTRATION_REASON,),
    )
    genome.registry = registry
    return registry


# ============================================================
# P1: enqueue-time applicability
# ============================================================

def test_enqueue_predicate_fires_on_motivating_facts() -> None:
    genome = create_baseline_policy_genome()
    _register_predicate(genome)
    entry = enqueue_registry_validation(
        genome=genome,
        operation="register_predicate",
        target="quarantined_error_count_nonzero",
        facts=FACTS_QUARANTINED,
        case_id="case_a",
    )
    assert entry.status == PENDING_STATUS_PENDING
    assert entry.observed_case_ids == ("case_a",)
    assert entry.required_validation == REGISTRY_REQUIRED_VALIDATION


def test_enqueue_predicate_that_never_fires_is_rejected() -> None:
    genome = create_baseline_policy_genome()
    _register_predicate(genome)
    entry = enqueue_registry_validation(
        genome=genome,
        operation="register_predicate",
        target="quarantined_error_count_nonzero",
        facts=FACTS_CLEAN,
        case_id="case_a",
    )
    assert entry.status == PENDING_STATUS_REJECTED
    assert entry.source_evidence["reject_reason"] == (
        "predicate_never_fires_on_motivating_facts"
    )
    # A rejected entry never accumulates observations.
    assert entry.observed_case_ids == ()


def test_enqueue_action_alias_resolves_operator() -> None:
    genome = create_baseline_policy_genome()
    _register_action_alias(genome)
    entry = enqueue_registry_validation(
        genome=genome,
        operation="register_action",
        target="downgrade_coverage_instance",
        facts=FACTS_CLEAN,
        case_id="case_a",
    )
    assert entry.status == PENDING_STATUS_PENDING


def test_enqueue_is_idempotent_on_resume() -> None:
    genome = create_baseline_policy_genome()
    _register_predicate(genome)
    enqueue_registry_validation(
        genome=genome,
        operation="register_predicate",
        target="quarantined_error_count_nonzero",
        facts=FACTS_QUARANTINED,
        case_id="case_a",
    )
    before = len(genome.pending_queue)
    enqueue_registry_validation(
        genome=genome,
        operation="register_predicate",
        target="quarantined_error_count_nonzero",
        facts=FACTS_QUARANTINED,
        case_id="case_a",
    )
    assert len(genome.pending_queue) == before


# ============================================================
# P2/P3: observation accumulation
# ============================================================

def test_promotion_requires_threshold_distinct_cases() -> None:
    genome = create_baseline_policy_genome()
    _register_predicate(genome)
    enqueue_registry_validation(
        genome=genome,
        operation="register_predicate",
        target="quarantined_error_count_nonzero",
        facts=FACTS_QUARANTINED,
        case_id="case_1",
    )
    rows = promote_pending_registry_entries(
        genome=genome,
        facts=FACTS_QUARANTINED,
        case_id="case_2",
        reason_code=REGISTRATION_REASON,
    )
    assert rows == [{
        "subject_id": "quarantined_error_count_nonzero",
        "outcome": "still_pending",
        "observed_cases": 2,
        "required": PROMOTION_OBSERVATION_THRESHOLD,
    }]
    # Registry still pending; queue entry still pending.
    assert genome.registry.status_of(
        "quarantined_error_count_nonzero"
    ) == "pending"
    assert genome.pending_queue[0].status == PENDING_STATUS_PENDING


def test_same_case_observation_counts_once() -> None:
    genome = create_baseline_policy_genome()
    _register_predicate(genome)
    enqueue_registry_validation(
        genome=genome,
        operation="register_predicate",
        target="quarantined_error_count_nonzero",
        facts=FACTS_QUARANTINED,
        case_id="case_x",
    )
    for _ in range(5):
        promote_pending_registry_entries(
            genome=genome,
            facts=FACTS_QUARANTINED,
            case_id="case_x",
            reason_code=REGISTRATION_REASON,
        )
    assert genome.pending_queue[0].observed_case_ids == ("case_x",)


def test_non_registration_reason_never_advances_queue() -> None:
    genome = create_baseline_policy_genome()
    _register_predicate(genome)
    enqueue_registry_validation(
        genome=genome,
        operation="register_predicate",
        target="quarantined_error_count_nonzero",
        facts=FACTS_QUARANTINED,
        case_id="case_1",
    )
    rows = promote_pending_registry_entries(
        genome=genome,
        facts=FACTS_QUARANTINED,
        case_id="case_2",
        reason_code="hollow_complete_defense",
    )
    assert rows == []
    assert genome.pending_queue[0].observed_case_ids == ("case_1",)


# ============================================================
# P4: promotion at threshold
# ============================================================

def test_promotion_enables_entry_at_threshold() -> None:
    genome = create_baseline_policy_genome()
    _register_predicate(genome)
    enqueue_registry_validation(
        genome=genome,
        operation="register_predicate",
        target="quarantined_error_count_nonzero",
        facts=FACTS_QUARANTINED,
        case_id="case_1",
    )
    promote_pending_registry_entries(
        genome=genome,
        facts=FACTS_QUARANTINED,
        case_id="case_2",
        reason_code=REGISTRATION_REASON,
    )
    rows = promote_pending_registry_entries(
        genome=genome,
        facts=FACTS_QUARANTINED,
        case_id="case_3",
        reason_code=REGISTRATION_REASON,
    )
    assert rows == [{
        "subject_id": "quarantined_error_count_nonzero",
        "outcome": "promoted",
        "observed_cases": PROMOTION_OBSERVATION_THRESHOLD,
        "edit_id": rows[0]["edit_id"],
        "registry_hash": rows[0]["registry_hash"],
    }]
    # The entry is now working: active view, evaluator-resolvable, and the
    # queue entry is terminal so resume cannot re-promote it.
    assert genome.registry.status_of(
        "quarantined_error_count_nonzero"
    ) == "registered"
    assert "quarantined_error_count_nonzero" in genome.registry.predicate_names()
    assert genome.pending_queue[0].status == PENDING_STATUS_VALIDATED
    # The promoted predicate is a live pure function.
    assert genome.registry.predicate_fn(
        "quarantined_error_count_nonzero"
    )(FACTS_QUARANTINED) is True
    # A second promotion attempt on the validated entry is inert.
    rows2 = promote_pending_registry_entries(
        genome=genome,
        facts=FACTS_QUARANTINED,
        case_id="case_4",
        reason_code=REGISTRATION_REASON,
    )
    assert rows2 == []


def test_promotion_rejects_entry_no_longer_applicable() -> None:
    genome = create_baseline_policy_genome()
    _register_predicate(genome)
    enqueue_registry_validation(
        genome=genome,
        operation="register_predicate",
        target="quarantined_error_count_nonzero",
        facts=FACTS_QUARANTINED,
        case_id="case_1",
    )
    promote_pending_registry_entries(
        genome=genome,
        facts=FACTS_QUARANTINED,
        case_id="case_2",
        reason_code=REGISTRATION_REASON,
    )
    rows = promote_pending_registry_entries(
        genome=genome,
        facts=FACTS_CLEAN,
        case_id="case_3",
        reason_code=REGISTRATION_REASON,
    )
    assert rows == [{
        "subject_id": "quarantined_error_count_nonzero",
        "outcome": "rejected",
        "reason": "entry_not_applicable_to_observation",
    }]
    assert genome.pending_queue[0].status == PENDING_STATUS_REJECTED


# ============================================================
# P5/P6: promoted aliases execute
# ============================================================

def test_evaluator_executes_promoted_action_alias() -> None:
    from src.sehd_v6.core.bccse.v6_contracts import PolicyItem
    from src.sehd_v6.core.paca_tcl.policy_evaluator import evaluate_policies

    genome = create_baseline_policy_genome()
    _register_action_alias(genome)
    registry = apply_registry_edit(
        genome.registry,
        operation="enable",
        target="downgrade_coverage_instance",
        reason_codes=(REGISTRATION_REASON,),
    )
    genome.registry = registry
    assert registry.operator_of("downgrade_coverage_instance") == (
        "downgrade_coverage"
    )
    item = PolicyItem(
        item_id="alias_item",
        precondition="all_candidates_quarantined",
        action="downgrade_coverage_instance",
        params={"side": "prosecution", "to_state": "unresolved"},
        is_baseline=False,
    )
    result = evaluate_policies(
        FACTS_QUARANTINED, [item], registry=genome.registry,
    )
    fired = [row for row in result.fired if row["action"] == "downgrade_coverage_instance"]
    assert fired, "the promoted alias must dispatch to its operator"
    assert result.effective_prosecution_coverage == "unresolved"


def test_pre_ledger_evaluator_resolves_alias_operator() -> None:
    from src.sehd_v6.core.bccse.v6_contracts import PolicyItem
    from src.sehd_v6.core.paca_tcl.policy_evaluator import (
        PreLedgerPolicyFacts,
        evaluate_pre_ledger_policies,
    )

    genome = create_baseline_policy_genome()
    registry = apply_registry_edit(
        genome.registry,
        operation="register_action",
        target="restore_audited_consensus_instance",
        operator="restore_audited_consensus",
        reason_codes=(REGISTRATION_REASON,),
    )
    registry = apply_registry_edit(
        registry,
        operation="enable",
        target="restore_audited_consensus_instance",
        reason_codes=(REGISTRATION_REASON,),
    )
    item = PolicyItem(
        item_id="restore_alias",
        precondition="consensus_gate_blocked_nonempty",
        action="restore_audited_consensus_instance",
        params={"certificate_id": "cert_a"},
        is_baseline=False,
    )
    facts = PreLedgerPolicyFacts(
        consensus_gate_applied=True,
        blocked_certificate_ids=("cert_a",),
        blocked_certificate_audit_status={"cert_a": "pass"},
        blocked_certificate_causal_status={
            "cert_a": "causes_incorrect_result",
        },
        blocked_certificate_impact_kind={"cert_a": "impact"},
        blocked_certificate_defense_status={"cert_a": "standing"},
        blocked_certificate_quarantine_status={"cert_a": "clean"},
        terminal_without_restore="",
    )
    result = evaluate_pre_ledger_policies(
        facts, [item], registry=registry,
    )
    assert result.restored_certificate_ids == ("cert_a",)


# ============================================================
# P7: checkpoint/resume round-trip
# ============================================================

def test_observed_case_ids_survive_round_trip() -> None:
    from src.sehd_v6.core.bccse.policy_genome import PolicyGenome

    genome = create_baseline_policy_genome()
    _register_predicate(genome)
    enqueue_registry_validation(
        genome=genome,
        operation="register_predicate",
        target="quarantined_error_count_nonzero",
        facts=FACTS_QUARANTINED,
        case_id="case_1",
    )
    promote_pending_registry_entries(
        genome=genome,
        facts=FACTS_QUARANTINED,
        case_id="case_2",
        reason_code=REGISTRATION_REASON,
    )
    restored = PolicyGenome.from_dict(genome.to_dict())
    entry = restored.pending_queue[0]
    assert entry.observed_case_ids == ("case_1", "case_2")
    assert entry.status == PENDING_STATUS_PENDING
    assert restored.registry.content_hash == genome.registry.content_hash

# ============================================================
# P8: registration intent -> promoted policy candidate
# ============================================================

def test_enqueue_action_with_intended_edit() -> None:
    from src.sehd_v6.core.bccse.registry_reflection import (
        RegistryCandidate,
        select_registry_candidate,
    )

    genome = create_baseline_policy_genome()
    _register_action_alias(genome)
    candidate, reason = select_registry_candidate(
        {
            "candidate_index": 0,
            "reason": "expose the repair",
            "precondition": "all_candidates_quarantined",
            "params": {"side": "prosecution", "to_state": "unresolved"},
        },
        (RegistryCandidate(
            candidate_id="c1", operation="register_action",
            target="downgrade_coverage_instance",
            operator="downgrade_coverage",
        ),),
        registry=genome.registry,
    )
    assert reason == ""
    assert candidate is not None
    assert candidate.precondition == "all_candidates_quarantined"
    assert candidate.params == {"side": "prosecution", "to_state": "unresolved"}
    entry = enqueue_registry_validation(
        genome=genome,
        operation="register_action",
        target="downgrade_coverage_instance",
        facts=FACTS_QUARANTINED,
        case_id="case_1",
        intended_edit={
            "precondition": candidate.precondition,
            "params": dict(candidate.params),
        },
    )
    assert entry.source_evidence["intended_edit"] == {
        "precondition": "all_candidates_quarantined",
        "params": {"side": "prosecution", "to_state": "unresolved"},
    }


def test_intent_validation_is_fail_closed() -> None:
    from src.sehd_v6.core.bccse.registry_reflection import (
        RegistryCandidate,
        select_registry_candidate,
    )

    genome = create_baseline_policy_genome()
    _register_action_alias(genome)
    candidate = RegistryCandidate(
        candidate_id="c1", operation="register_action",
        target="downgrade_coverage_instance",
        operator="downgrade_coverage",
    )
    # Unknown precondition drops the intent (registration itself survives).
    selected, reason = select_registry_candidate(
        {
            "candidate_index": 0,
            "precondition": "no_such_predicate",
            "params": {"side": "prosecution", "to_state": "unresolved"},
        },
        (candidate,),
        registry=genome.registry,
    )
    assert selected is candidate
    assert reason.startswith("intent_precondition_unknown")
    # Invalid params are rejected too.
    selected2, reason2 = select_registry_candidate(
        {
            "candidate_index": 0,
            "precondition": "all_candidates_quarantined",
            "params": {"side": "bogus"},
        },
        (candidate,),
        registry=genome.registry,
    )
    assert selected2 is candidate
    assert reason2.startswith("intent_params_invalid")


def test_promoted_intent_becomes_policy_candidate() -> None:
    from src.sehd_v6.core.bccse.registry_promotion import (
        promoted_intent_candidates,
    )

    genome = create_baseline_policy_genome()
    _register_action_alias(genome)
    enqueue_registry_validation(
        genome=genome,
        operation="register_action",
        target="downgrade_coverage_instance",
        facts=FACTS_QUARANTINED,
        case_id="case_1",
        intended_edit={
            "precondition": "all_candidates_quarantined",
            "params": {"side": "prosecution", "to_state": "unresolved"},
        },
    )
    promote_pending_registry_entries(
        genome=genome,
        facts=FACTS_QUARANTINED,
        case_id="case_2",
        reason_code=REGISTRATION_REASON,
    )
    promote_pending_registry_entries(
        genome=genome,
        facts=FACTS_QUARANTINED,
        case_id="case_3",
        reason_code=REGISTRATION_REASON,
    )
    candidates = promoted_intent_candidates(genome, FACTS_QUARANTINED)
    assert len(candidates) == 1
    item = candidates[0].new_item
    assert item is not None
    assert item.precondition == "all_candidates_quarantined"
    assert item.action == "downgrade_coverage_instance"
    assert item.params == {"side": "prosecution", "to_state": "unresolved"}
    # The candidate survives the genome's own item contract once the
    # evolution loop assigns identity (item_id is loop-owned, never
    # model-owned).
    from src.sehd_v6.core.bccse.policy_genome import _validate_item_contract
    from src.sehd_v6.core.bccse.v6_contracts import PolicyItem as _PolicyItem
    ok, reason = _validate_item_contract(
        _PolicyItem(
            item_id="loop_assigned",
            precondition=item.precondition,
            action=item.action,
            params=item.params,
        ),
        genome.registry,
    )
    assert ok, reason


def test_intent_without_promotion_produces_no_candidate() -> None:
    from src.sehd_v6.core.bccse.registry_promotion import (
        promoted_intent_candidates,
    )

    genome = create_baseline_policy_genome()
    _register_action_alias(genome)
    enqueue_registry_validation(
        genome=genome,
        operation="register_action",
        target="downgrade_coverage_instance",
        facts=FACTS_QUARANTINED,
        case_id="case_1",
        intended_edit={
            "precondition": "all_candidates_quarantined",
            "params": {"side": "prosecution", "to_state": "unresolved"},
        },
    )
    assert promoted_intent_candidates(genome, FACTS_QUARANTINED) == ()


def test_stale_intent_after_vocabulary_drift_contributes_nothing() -> None:
    from src.sehd_v6.core.bccse.registry_promotion import (
        promoted_intent_candidates,
    )

    genome = create_baseline_policy_genome()
    _register_action_alias(genome)
    enqueue_registry_validation(
        genome=genome,
        operation="register_action",
        target="downgrade_coverage_instance",
        facts=FACTS_QUARANTINED,
        case_id="case_1",
        intended_edit={
            "precondition": "all_candidates_quarantined",
            "params": {"side": "prosecution", "to_state": "unresolved"},
        },
    )
    promote_pending_registry_entries(
        genome=genome,
        facts=FACTS_QUARANTINED,
        case_id="case_2",
        reason_code=REGISTRATION_REASON,
    )
    promote_pending_registry_entries(
        genome=genome,
        facts=FACTS_QUARANTINED,
        case_id="case_3",
        reason_code=REGISTRATION_REASON,
    )
    # Retire the promoted alias the intent references: the candidate must
    # vanish fail-closed instead of referencing a dead action.
    genome.registry = apply_registry_edit(
        genome.registry, operation="disable",
        target="downgrade_coverage_instance",
        reason_codes=(REGISTRATION_REASON,),
    )
    assert promoted_intent_candidates(genome, FACTS_QUARANTINED) == ()


# ============================================================
# P9: detector-side provider metas feed the usage ledger
# ============================================================

def test_detector_provider_usage_walker_records_surfaces() -> None:
    from src.sehd_v6.core.bccse import evolution_runner as runner
    from src.sehd_v6.core.provider_usage import ProviderUsageLedger

    ledger = ProviderUsageLedger()
    result = {
        "providers": {
            "construct_census": {
                "initial": {
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    "cache_hit": False,
                },
                "repair": {
                    "usage": {"prompt_tokens": 20},
                    "cache_hit": False,
                    "transport_attempts": 1,
                },
            },
            "defense": {
                "usage": {"prompt_tokens": 30},
                "cache_hit": False,
            },
            "focused_prosecution": {
                "candidate_1": {
                    "usage": {"prompt_tokens": 40},
                    "cache_hit": True,
                },
            },
            "errored_surface": {"error_type": "TimeoutError"},
        },
    }
    runner._record_detector_provider_usage(ledger, result, "source")
    totals = ledger.totals()
    assert totals["logical_calls"] == 4
    assert totals["cache_hits"] == 1
    # Tokens are billed for fresh calls only (cached ones are reported,
    # never re-billed): 10 + 20 + 30 = 60.
    assert totals["prompt_tokens"] == 60
    assert totals["completion_tokens"] == 5
    assert totals["transport_attempts"] == 1
    by_purpose = totals["by_purpose"]
    assert by_purpose["detector:source:defense"] == 1
    assert by_purpose["detector:source:construct_census"] == 2
    assert "errored_surface" not in " ".join(by_purpose)


def test_detector_usage_walker_skips_non_mapping_results() -> None:
    from src.sehd_v6.core.bccse import evolution_runner as runner
    from src.sehd_v6.core.provider_usage import ProviderUsageLedger

    ledger = ProviderUsageLedger()
    runner._record_detector_provider_usage(ledger, {"state": "unresolved"}, "source")
    assert ledger.totals()["logical_calls"] == 0
