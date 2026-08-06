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
    PredicateEntry,
    VocabularyRegistry,
    _build_baseline_registry,
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
