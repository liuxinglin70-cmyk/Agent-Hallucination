"""Contract tests for the versioned L2 vocabulary baseline registry.

The registry (``vocabulary_registry.L2_REGISTRY``) is the single source of
truth for the L2 precondition predicates, court-local action operators and
instances, the action parameter tables, and the reserved engine-level
actions. These tests freeze its P3A guarantees: immutability, a stable
version and content hash, fail-closed handling of unknown/disabled entries,
and a drift guard asserting the deterministic evaluator's semantics match
each registered entry exactly (so the prompt vocabulary and the runtime can
never drift apart).
"""

from __future__ import annotations

import pytest

from src.sehd_v6.core.bccse import v6_contracts as contracts
from src.sehd_v6.core.bccse.vocabulary_registry import (
    COMPILER_VERSION,
    FACTS_SCHEMA_VERSION,
    L2_REGISTRY,
    REGISTRY_SCHEMA_VERSION,
    REGISTRY_VERSION,
    STATUS_BASELINE,
    STATUS_DISABLED,
    STATUS_PENDING,
    STATUS_REGISTERED,
    PredicateEntry,
    VocabularyRegistry,
    _build_baseline_registry,
    apply_registry_edit,
)
from src.sehd_v6.core.paca_tcl.policy_evaluator import evaluate_policies


def _facts(**overrides) -> contracts.AdjudicationFacts:
    base = dict(
        prosecution_coverage="incomplete",
        defense_coverage="incomplete",
        valid_error_certificate_count=0,
        valid_defense_certificate_count=0,
        surviving_error_count=0,
        defeated_error_count=0,
        quarantined_error_count=0,
        quarantined_defense_count=0,
        consensus_gate_applied=False,
        consensus_gate_blocked_count=0,
        required_candidate_count=0,
        fully_quarantined=False,
        boundary_path_state="",
        boundary_authority_valid=False,
        terminal_state="unresolved",
        policy_stage="post_ledger",
    )
    base.update(overrides)
    return contracts.AdjudicationFacts(**base)


# ------------------------------------------------------------------
# Immutability
# ------------------------------------------------------------------

def test_registry_views_are_immutable() -> None:
    with pytest.raises(TypeError):
        L2_REGISTRY.action_params()["new_action"] = ()
    with pytest.raises(TypeError):
        L2_REGISTRY.action_params()["downgrade_coverage"] = ()
    # Rebuilding the registry yields an independent object with equal state;
    # mutating neither view affects the shared authority.
    rebuilt = _build_baseline_registry()
    assert rebuilt is not L2_REGISTRY
    assert rebuilt == L2_REGISTRY


# ------------------------------------------------------------------
# Version and hash stability
# ------------------------------------------------------------------

def test_registry_version_anchors_are_stable() -> None:
    assert L2_REGISTRY.registry_version == REGISTRY_VERSION
    assert L2_REGISTRY.schema_version == REGISTRY_SCHEMA_VERSION
    assert L2_REGISTRY.facts_schema_version == FACTS_SCHEMA_VERSION
    assert L2_REGISTRY.compiler_version == COMPILER_VERSION


def test_registry_hash_is_stable_and_rebuildable() -> None:
    first = L2_REGISTRY.content_hash
    second = L2_REGISTRY.content_hash
    assert first == second
    assert len(first) == 64
    # A from-scratch rebuild of the same baseline state hashes identically.
    assert _build_baseline_registry().content_hash == first


def test_registry_hash_ignores_entry_order() -> None:
    # content_hash is canonical (sorted keys), so the serialized state is
    # independent of construction order.
    ordered = L2_REGISTRY.to_dict()
    assert [row["name"] for row in ordered["predicates"]] == sorted(
        row["name"] for row in ordered["predicates"]
    )
    assert [row["name"] for row in ordered["action_instances"]] == sorted(
        row["name"] for row in ordered["action_instances"]
    )


# ------------------------------------------------------------------
# Fail-closed lookups
# ------------------------------------------------------------------

def test_unknown_predicate_is_absent_and_fail_closed() -> None:
    assert L2_REGISTRY.predicate_fn("no_such_predicate") is None
    assert "no_such_predicate" not in L2_REGISTRY.predicate_names()
    result = evaluate_policies(
        _facts(),
        [contracts.PolicyItem(
            item_id="p_unknown",
            precondition="no_such_predicate",
            action="emit_telemetry_only",
        )],
    )
    assert result.fired == ()
    assert result.skipped[0]["reason"] == "unknown_predicate"


def test_unknown_action_is_absent_and_fail_closed() -> None:
    assert "no_such_action" not in L2_REGISTRY.action_names()
    ok, reason = contracts.validate_policy_params("no_such_action", {})
    assert not ok
    assert "unknown_action" in reason


def test_disabled_entry_is_not_resolvable() -> None:
    # A disabled entry stays in the registry payload (audit trail) but is
    # excluded from every active view, so policy items cannot reference it.
    disabled = PredicateEntry(
        name="ghost_predicate",
        canonical_ast={"dsl": "fully_quarantined"},
        canonical_dsl="fully_quarantined",
        facts_whitelist=("fully_quarantined",),
        compiled_digest="0" * 64,
        registered_generation=1,
        status="disabled",
        evaluate=lambda facts: True,
    )
    registry = VocabularyRegistry(
        registry_version=REGISTRY_VERSION,
        predicates={disabled.name: disabled},
        action_operators={},
        action_instances={},
    )
    assert registry.predicate_fn("ghost_predicate") is None
    assert "ghost_predicate" not in registry.predicate_names()


def test_engine_level_actions_are_reserved_not_proposable() -> None:
    # Reserved engine-level operators exist in the registry but are excluded
    # from the proposable/executable vocabulary.
    assert L2_REGISTRY.engine_level_action_names() == {
        "reclassify_candidate_state",
        "require_route_replay",
    }
    assert L2_REGISTRY.engine_level_action_names().isdisjoint(
        L2_REGISTRY.action_names()
    )
    for reserved in L2_REGISTRY.engine_level_action_names():
        ok, reason = contracts.validate_policy_params(reserved, {})
        assert not ok
        assert "unknown_action" in reason


# ------------------------------------------------------------------
# Single-authority consistency (no dual source)
# ------------------------------------------------------------------

def test_derived_views_match_registry_exactly() -> None:
    # v6_contracts exposes registry-derived compatibility views; they must
    # be pure projections of the registry, never a second authority.
    assert set(contracts.L2_PREDICATES) == set(L2_REGISTRY.predicate_names())
    assert contracts.L2_PREDICATE_NAMES == L2_REGISTRY.predicate_names()
    assert contracts.L2_ACTIONS == L2_REGISTRY.action_names()
    assert contracts.COURT_LOCAL_ACTIONS == L2_REGISTRY.action_names()
    assert contracts.ENGINE_LEVEL_ACTIONS == (
        L2_REGISTRY.engine_level_action_names()
    )
    assert contracts.ACTION_PARAMS == dict(L2_REGISTRY.action_params())
    for name, fn in contracts.L2_PREDICATES.items():
        assert fn is L2_REGISTRY.predicate_fn(name)


# ------------------------------------------------------------------
# Registry <-> evaluator semantics drift guard
# ------------------------------------------------------------------
# For every registered predicate, assert the evaluator applies exactly the
# semantics the registry declares: an item whose precondition holds fires,
# an item whose precondition does not hold neither fires nor skips. The
# witness facts below pin each predicate's truth condition.

def _predicate_witnesses():
    return {
        "consensus_gate_blocked_nonempty": (
            _facts(consensus_gate_applied=True, consensus_gate_blocked_count=1),
            _facts(consensus_gate_applied=False, consensus_gate_blocked_count=0),
        ),
        "defense_coverage_complete_and_zero_certificates": (
            _facts(defense_coverage="complete", valid_defense_certificate_count=0),
            _facts(defense_coverage="complete", valid_defense_certificate_count=1),
        ),
        "prosecution_coverage_complete_and_zero_certificates": (
            _facts(
                prosecution_coverage="complete", valid_error_certificate_count=0,
            ),
            _facts(
                prosecution_coverage="complete", valid_error_certificate_count=1,
            ),
        ),
        "boundary_state_conflicts_with_terminal": (
            _facts(boundary_path_state="verified_clean", terminal_state="unresolved"),
            _facts(boundary_path_state="verified_clean", terminal_state="verified_clean"),
        ),
        "surviving_errors_nonempty_and_verdict_clean": (
            _facts(surviving_error_count=1, terminal_state="verified_clean"),
            _facts(surviving_error_count=0, terminal_state="verified_clean"),
        ),
        "all_candidates_quarantined": (
            _facts(fully_quarantined=True, required_candidate_count=1),
            _facts(fully_quarantined=False, required_candidate_count=1),
        ),
    }


def test_registry_declares_exactly_the_baseline_vocabulary() -> None:
    # Guard against silent vocabulary drift: the registry holds exactly the
    # six baseline predicates and four court-local action instances.
    assert set(L2_REGISTRY.predicate_names()) == {
        "consensus_gate_blocked_nonempty",
        "defense_coverage_complete_and_zero_certificates",
        "prosecution_coverage_complete_and_zero_certificates",
        "boundary_state_conflicts_with_terminal",
        "surviving_errors_nonempty_and_verdict_clean",
        "all_candidates_quarantined",
    }
    assert L2_REGISTRY.action_names() == {
        "downgrade_coverage",
        "invalidate_coverage_claim",
        "emit_telemetry_only",
        "consume_verified_clean_boundary",
    }
    for name in L2_REGISTRY.predicate_names():
        assert L2_REGISTRY.predicate_entry(name).status == STATUS_BASELINE


def test_evaluator_semantics_match_every_registered_predicate() -> None:
    witnesses = _predicate_witnesses()
    assert set(witnesses) == set(L2_REGISTRY.predicate_names())
    for name, (positive, negative) in witnesses.items():
        predicate = L2_REGISTRY.predicate_fn(name)
        assert predicate is not None
        # The registry's declared semantics agree with the witnesses...
        assert predicate(positive) is True, name
        assert predicate(negative) is False, name
        # ...and the evaluator resolves the same predicate by name to the
        # same truth: fires when it holds, silent when it does not.
        item = contracts.PolicyItem(
            item_id=f"probe_{name}",
            precondition=name,
            action="emit_telemetry_only",
        )
        fired_result = evaluate_policies(positive, [item])
        assert fired_result.fired[0]["item_id"] == f"probe_{name}", name
        assert fired_result.skipped == (), name
        silent_result = evaluate_policies(negative, [item])
        assert silent_result.fired == (), name
        assert silent_result.skipped == (), name


def test_evaluator_uses_only_registered_actions() -> None:
    # Every action the evaluator can fire is a registered court-local action;
    # the parameter table it validates against is the registry's.
    for action, params in contracts.ACTION_PARAMS.items():
        assert action in L2_REGISTRY.action_names()
        assert L2_REGISTRY.action_params()[action] == params


# ------------------------------------------------------------------
# Online evolution (P3B): registration lifecycle and lineage
# ------------------------------------------------------------------

def _register_pending(registry, target="quarantined_error_count_nonzero"):
    return apply_registry_edit(
        registry, operation="register_predicate", target=target,
        dsl="quarantined_error_count > 0",
        reason_codes=("no_registered_runtime_action",),
    )


def test_first_registration_is_pending_not_active() -> None:
    registry = _register_pending(L2_REGISTRY)
    assert registry.status_of("quarantined_error_count_nonzero") == STATUS_PENDING
    # Pending entries are shadow-only: absent from every active view.
    assert "quarantined_error_count_nonzero" not in registry.predicate_names()
    assert registry.predicate_fn("quarantined_error_count_nonzero") is None


def test_enable_promotes_pending_to_active() -> None:
    registry = _register_pending(L2_REGISTRY)
    promoted = apply_registry_edit(
        registry, operation="enable", target="quarantined_error_count_nonzero",
    )
    assert promoted.status_of(
        "quarantined_error_count_nonzero"
    ) == STATUS_REGISTERED
    assert "quarantined_error_count_nonzero" in promoted.predicate_names()
    assert promoted.predicate_fn("quarantined_error_count_nonzero") is not None


def test_disable_excludes_entry_from_active_views() -> None:
    registry = _register_pending(L2_REGISTRY)
    promoted = apply_registry_edit(
        registry, operation="enable", target="quarantined_error_count_nonzero",
    )
    disabled = apply_registry_edit(
        promoted, operation="disable", target="quarantined_error_count_nonzero",
    )
    assert disabled.status_of(
        "quarantined_error_count_nonzero"
    ) == STATUS_DISABLED
    assert "quarantined_error_count_nonzero" not in disabled.predicate_names()
    assert disabled.predicate_fn("quarantined_error_count_nonzero") is None


def test_baseline_entries_cannot_be_disabled() -> None:
    with pytest.raises(ValueError, match="baseline entry cannot be disabled"):
        apply_registry_edit(
            L2_REGISTRY, operation="disable",
            target="all_candidates_quarantined",
        )


def test_register_duplicate_target_fails_closed() -> None:
    registry = _register_pending(L2_REGISTRY)
    with pytest.raises(ValueError, match="already registered"):
        apply_registry_edit(
            registry, operation="register_predicate",
            target="quarantined_error_count_nonzero",
            dsl="quarantined_error_count > 0",
        )


def test_register_action_instance_binds_court_operator_only() -> None:
    registry = apply_registry_edit(
        L2_REGISTRY, operation="register_action",
        target="downgrade_coverage_instance", operator="downgrade_coverage",
    )
    assert registry.status_of("downgrade_coverage_instance") == STATUS_PENDING
    # Engine-level operators cannot be instantiated.
    with pytest.raises(ValueError, match="not court-local"):
        apply_registry_edit(
            L2_REGISTRY, operation="register_action",
            target="reclassify_instance", operator="reclassify_candidate_state",
        )


def test_registry_edit_audit_chain_is_verifiable() -> None:
    r1 = _register_pending(L2_REGISTRY)
    r2 = apply_registry_edit(
        r1, operation="enable", target="quarantined_error_count_nonzero",
    )
    assert [e.sequence for e in r2.audit] == [1, 2]
    first, second = r2.audit
    # before_hash chains to the prior registry; after_hash seals the post-edit
    # content (audit truncated to before the edit), so it is self-contained.
    assert first.before_hash == L2_REGISTRY.content_hash
    assert second.before_hash == r1.content_hash
    assert first.after_hash != second.after_hash
    assert first.operation == "register_predicate"
    assert second.operation == "enable"


def test_evolved_registry_roundtrip_recompiles_and_preserves_hash() -> None:
    registry = _register_pending(L2_REGISTRY)
    promoted = apply_registry_edit(
        registry, operation="enable", target="quarantined_error_count_nonzero",
    )
    restored = VocabularyRegistry.from_dict(promoted.to_dict())
    assert restored.content_hash == promoted.content_hash
    # The recompiled predicate is a pure function of the facts again.
    fn = restored.predicate_fn("quarantined_error_count_nonzero")
    assert fn is not None
    assert fn(contracts.AdjudicationFacts(quarantined_error_count=3)) is True
    assert fn(contracts.AdjudicationFacts(quarantined_error_count=0)) is False


def test_genome_content_hash_binds_registry_state() -> None:
    from src.sehd_v6.core.bccse.policy_genome import (
        PolicyGenome,
        create_baseline_policy_genome,
    )
    base = create_baseline_policy_genome()
    evolved_registry = _register_pending(L2_REGISTRY)
    same_items_evolved = PolicyGenome(
        genome_id="x", items=list(base.items), registry=evolved_registry,
    )
    # Same items but a different registry -> different genome identity (§8.6).
    assert same_items_evolved.content_hash != base.content_hash
    restored = PolicyGenome.from_dict(same_items_evolved.to_dict())
    assert restored.content_hash == same_items_evolved.content_hash
