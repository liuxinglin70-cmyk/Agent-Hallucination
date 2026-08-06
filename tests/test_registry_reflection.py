"""Tests for the L2 vocabulary registration planner (P3B).

Covers the constrained registration path: candidate enumeration, shadow
compile fail-closed rules, the model-selection contract, and the DSL
compiler's whitelist (legal sources compile to pure predicates; anything
outside the facts whitelist or value vocabulary is rejected at compile time
and never registered).
"""

from __future__ import annotations

import pytest

from src.sehd_v6.core.bccse.registry_reflection import (
    PURPOSE_REGISTRY_REFLECTION,
    build_registry_selection_prompt,
    compile_registration_candidate,
    plan_registry_candidates,
    select_registry_candidate,
    RegistryCandidate,
)
from src.sehd_v6.core.bccse.v6_contracts import (
    AdjudicationFacts,
    FailureAttribution,
)
from src.sehd_v6.core.bccse.vocabulary_registry import (
    L2_REGISTRY,
    RegistryDslError,
    apply_registry_edit,
    compile_predicate_dsl,
    predicate_facts_whitelist,
)


def _l2_attribution(reason_code="no_registered_runtime_action"):
    return FailureAttribution(
        layer="l2_adjudication_policy",
        artifact="coverage_validity_policy",
        reason_code=reason_code,
        evidence={},
    )


# ------------------------------------------------------------------
# DSL compiler
# ------------------------------------------------------------------

def test_legal_dsl_compiles_to_pure_predicate():
    predicate = compile_predicate_dsl(
        "quarantined_nonzero", "quarantined_error_count > 0",
    )
    positive = AdjudicationFacts(quarantined_error_count=2)
    negative = AdjudicationFacts(quarantined_error_count=0)
    assert predicate(positive) is True
    assert predicate(negative) is False
    assert predicate_facts_whitelist("quarantined_error_count > 0") == (
        "quarantined_error_count",
    )


def test_legal_dsl_supports_boolean_and_comparison_operators():
    predicate = compile_predicate_dsl(
        "combo",
        'surviving_error_count == 0 and terminal_state == "unresolved"',
    )
    assert predicate(AdjudicationFacts(
        surviving_error_count=0, terminal_state="unresolved",
    )) is True
    assert predicate(AdjudicationFacts(
        surviving_error_count=1, terminal_state="unresolved",
    )) is False
    in_predicate = compile_predicate_dsl(
        "cov", 'defense_coverage in ["complete", "incomplete"]',
    )
    assert in_predicate(AdjudicationFacts(defense_coverage="complete")) is True
    assert in_predicate(AdjudicationFacts(defense_coverage="unresolved")) is False


@pytest.mark.parametrize("bad_source", [
    "__import__('os')",                 # dynamic import
    "open('file')",                      # function call
    "facts.quarantined_error_count",     # attribute access
    "unknown_field > 1",                 # field outside facts whitelist
    "quarantined_error_count > 0.5 if True else 0",  # conditional expression
    "[x for x in [1]]",                  # comprehension
    "quarantined_error_count >> 1",      # non-whitelisted operator
    'terminal_state == "not_a_state"',   # string outside value vocabulary
    "True",                              # no fact field referenced
    "surviving_error_count == 1e9",      # float literal (counts are ints)
    "terminal_state > 0",                # ordering str field vs int (TypeError)
    "fully_quarantined > 0",             # ordering on a boolean field
])
def test_invalid_dsl_fails_closed_at_compile_time(bad_source):
    with pytest.raises((RegistryDslError, SyntaxError, ValueError)):
        compile_predicate_dsl("bad", bad_source)


def test_unsigned_and_negative_count_comparisons_are_legal():
    # Unsigned/negative integer literals compare against count fields, so
    # boundary predicates like ">= 0" or "== -1" compile and run.
    assert compile_predicate_dsl("a", "surviving_error_count >= 0")(
        AdjudicationFacts(surviving_error_count=0)
    ) is True
    assert compile_predicate_dsl("b", "surviving_error_count == -1")(
        AdjudicationFacts(surviving_error_count=0)
    ) is False


def test_equality_with_mismatched_type_is_legal_but_ordering_is_not():
    # Equality against a mismatched literal is simply always False (legal);
    # an ordering comparison would raise TypeError at runtime (rejected).
    assert compile_predicate_dsl("c", 'surviving_error_count == "complete"')(
        AdjudicationFacts(surviving_error_count=0)
    ) is False
    with pytest.raises(RegistryDslError):
        compile_predicate_dsl("d", 'surviving_error_count > "complete"')


# ------------------------------------------------------------------
# Planner candidate enumeration
# ------------------------------------------------------------------

def test_planner_only_opens_on_no_registered_action():
    assert plan_registry_candidates(
        attribution=_l2_attribution("no_registered_runtime_action"),
        registry=L2_REGISTRY,
    )
    # A reason with an existing runtime repair never opens the vocabulary.
    assert plan_registry_candidates(
        attribution=_l2_attribution("hollow_complete_defense"),
        registry=L2_REGISTRY,
    ) == ()


def test_planner_candidates_are_deterministic():
    first = plan_registry_candidates(
        attribution=_l2_attribution(), registry=L2_REGISTRY,
    )
    second = plan_registry_candidates(
        attribution=_l2_attribution(), registry=L2_REGISTRY,
    )
    assert [c.candidate_id for c in first] == [c.candidate_id for c in second]
    # Candidate ids are content-addressed, so the same plan is reproducible.
    assert all(c.candidate_id.startswith("registry_candidate_") for c in first)


def test_planner_skips_already_registered_targets():
    registry = apply_registry_edit(
        L2_REGISTRY, operation="register_predicate",
        target="quarantined_error_count_nonzero",
        dsl="quarantined_error_count > 0",
    )
    candidates = plan_registry_candidates(
        attribution=_l2_attribution(), registry=registry,
    )
    assert "quarantined_error_count_nonzero" not in {
        c.target for c in candidates
    }


# ------------------------------------------------------------------
# Shadow compile (fail-closed)
# ------------------------------------------------------------------

def test_shadow_compile_rejects_unknown_operator():
    candidate = RegistryCandidate(
        candidate_id="c1", operation="register_action",
        target="x_instance", operator="no_such_operator",
    )
    with pytest.raises(ValueError, match="unknown action operator"):
        compile_registration_candidate(candidate, L2_REGISTRY)


def test_shadow_compile_rejects_engine_level_operator():
    candidate = RegistryCandidate(
        candidate_id="c2", operation="register_action",
        target="x_instance", operator="reclassify_candidate_state",
    )
    with pytest.raises(ValueError, match="not court-local"):
        compile_registration_candidate(candidate, L2_REGISTRY)


def test_shadow_compile_rejects_duplicate_predicate():
    candidate = RegistryCandidate(
        candidate_id="c3", operation="register_predicate",
        target="all_candidates_quarantined", dsl="fully_quarantined",
    )
    with pytest.raises(ValueError, match="already registered"):
        compile_registration_candidate(candidate, L2_REGISTRY)


def test_shadow_compile_rejects_invalid_dsl():
    candidate = RegistryCandidate(
        candidate_id="c4", operation="register_predicate",
        target="evil", dsl="__import__('os')",
    )
    with pytest.raises(RegistryDslError):
        compile_registration_candidate(candidate, L2_REGISTRY)


# ------------------------------------------------------------------
# Selection contract
# ------------------------------------------------------------------

def test_selection_prompt_and_parse_contract():
    candidates = plan_registry_candidates(
        attribution=_l2_attribution(), registry=L2_REGISTRY,
    )
    system, user = build_registry_selection_prompt(
        attribution=_l2_attribution(),
        facts_summary={"quarantined_error_count": 2},
        candidates=candidates,
    )
    assert "candidate_index" in system
    assert "no_proposal" in system
    # The model never sees gold or case identity; the prompt is a ranking task.
    selected, reason = select_registry_candidate(
        {"candidate_index": 0, "reason": "expose the fact"}, candidates,
    )
    assert reason == ""
    assert selected is candidates[0]
    none_selected, none_reason = select_registry_candidate(
        {"no_proposal": True, "reason": "no extension justified"}, candidates,
    )
    assert none_selected is None
    assert none_reason == "no extension justified"
    out_of_range, oor_reason = select_registry_candidate(
        {"candidate_index": 999}, candidates,
    )
    assert out_of_range is None
    assert oor_reason == "candidate_index_out_of_range"


def test_purpose_token_is_stable():
    assert PURPOSE_REGISTRY_REFLECTION == "registry_reflection"
