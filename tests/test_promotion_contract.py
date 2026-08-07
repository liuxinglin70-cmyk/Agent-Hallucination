"""Tests for the unified promotion contract (P1)."""

import pytest

from src.sehd_v6.core.bccse.promotion_contract import (
    ACTIVATION_DEPLOYABLE,
    ACTIVATION_NONE,
    ACTIVATION_PENDING,
    ACTIVATION_WORKING,
    AUTHORITY_L1_FITNESS,
    AUTHORITY_L1_STRUCTURAL,
    AUTHORITY_L2_POLICY,
    EVIDENCE_DIMENSIONS,
    EVIDENCE_NEGATIVE,
    EVIDENCE_POSITIVE,
    EVIDENCE_UNAVAILABLE,
    GATE_PROFILES,
    GRADE_CONDITIONAL,
    GRADE_REJECT,
    GRADE_SKIP,
    GRADE_STRONG,
    GRADE_WEAK,
    HARD_CANARY_REGRESSION,
    HARD_GOLD_POLARITY_REGRESSION,
    HARD_PROTECTED_BASELINE_VIOLATION,
    HARD_REPLAY_RUNTIME_INVALID,
    HARD_UNKNOWN_VOCABULARY,
    PROFILE_CONSERVATIVE,
    PROFILE_STEADY,
    PromotionDecision,
    PromotionEvidence,
    classify_reason_codes,
    promote,
)
from src.sehd_v6.core.bccse.v6_contracts import LAYER_L1, LAYER_L2


def _promote(legacy_decision, **overrides):
    kwargs = {
        "layer": LAYER_L1,
        "artifact": "evidence_obligation",
        "legacy_decision": legacy_decision,
        "decision_authority": AUTHORITY_L1_FITNESS,
    }
    kwargs.update(overrides)
    return promote(**kwargs)


# ------------------------------------------------------------------
# Total, deterministic mapping over the legacy decision catalogue
# ------------------------------------------------------------------

LEGACY_CATALOGUE = {
    # L1 accept
    "accept_batch": (GRADE_WEAK, ACTIVATION_WORKING),
    "accept_fitness_disabled": (GRADE_WEAK, ACTIVATION_WORKING),
    # L2 accept
    "accept_policy": (GRADE_WEAK, ACTIVATION_WORKING),
    # provisional
    "provisional": (GRADE_CONDITIONAL, ACTIVATION_PENDING),
    "provisional_accept": (GRADE_CONDITIONAL, ACTIVATION_PENDING),
    "provisional_label_free": (GRADE_CONDITIONAL, ACTIVATION_PENDING),
    "provisional_no_gold_telemetry": (GRADE_CONDITIONAL, ACTIVATION_PENDING),
    # reject / rollback / retry
    "reject": (GRADE_REJECT, ACTIVATION_NONE),
    "reject_apply": (GRADE_REJECT, ACTIVATION_NONE),
    "reject_fitness": (GRADE_REJECT, ACTIVATION_NONE),
    "reject_fitness_no_effect": (GRADE_REJECT, ACTIVATION_NONE),
    "reject_fitness_runtime": (GRADE_REJECT, ACTIVATION_NONE),
    "reject_policy_fitness": (GRADE_REJECT, ACTIVATION_NONE),
    "reject_policy_no_effect": (GRADE_REJECT, ACTIVATION_NONE),
    "structural_reject": (GRADE_REJECT, ACTIVATION_NONE),
    "rollback_batch": (GRADE_REJECT, ACTIVATION_NONE),
    "rollback_probe": (GRADE_REJECT, ACTIVATION_NONE),
    "rollback_resume": (GRADE_REJECT, ACTIVATION_NONE),
    "retry_structural": (GRADE_REJECT, ACTIVATION_NONE),
    "retry_fitness_no_effect": (GRADE_REJECT, ACTIVATION_NONE),
    "retry_invalid_mutation": (GRADE_REJECT, ACTIVATION_NONE),
    # skip
    "skip_invalid_mutation": (GRADE_SKIP, ACTIVATION_NONE),
    "skip_mutation_budget_exhausted": (GRADE_SKIP, ACTIVATION_NONE),
    "skip_no_edit": (GRADE_SKIP, ACTIVATION_NONE),
    "skip_no_policy": (GRADE_SKIP, ACTIVATION_NONE),
    "skip_route_conflict": (GRADE_SKIP, ACTIVATION_NONE),
    "skip_runtime_invalid": (GRADE_SKIP, ACTIVATION_NONE),
    "skip_stable": (GRADE_SKIP, ACTIVATION_NONE),
    "skip_unaddressable_failure": (GRADE_SKIP, ACTIVATION_NONE),
    "no_edit": (GRADE_SKIP, ACTIVATION_NONE),
    "halted": (GRADE_SKIP, ACTIVATION_NONE),
}


def test_every_legacy_decision_has_exactly_one_deterministic_mapping():
    for decision, (grade, activation) in LEGACY_CATALOGUE.items():
        first = _promote(decision)
        second = _promote(decision)
        assert first == second, decision
        assert first.evidence_grade == grade, decision
        assert first.activation_state == activation, decision
        assert first.legacy_decision == decision


def test_catalogue_matches_runner_vocabulary():
    """The test catalogue must stay exhaustive against the contract's own
    legacy decision set (drift fails here instead of at runtime)."""
    from src.sehd_v6.core.bccse.promotion_contract import LEGACY_DECISIONS

    assert set(LEGACY_CATALOGUE) == set(LEGACY_DECISIONS)


def test_unknown_decision_fails_closed():
    with pytest.raises(ValueError, match="unknown legacy_decision"):
        _promote("accept_maybe")


def test_unknown_authority_fails_closed():
    with pytest.raises(ValueError, match="unknown decision_authority"):
        _promote("accept_batch", decision_authority="meta_review")


def test_unknown_profile_fails_closed():
    with pytest.raises(ValueError, match="unknown gate profile"):
        _promote("accept_batch", profile="permissive")


# ------------------------------------------------------------------
# Hard gate
# ------------------------------------------------------------------

def test_hard_reasons_force_reject_without_activation():
    decision = _promote(
        "accept_policy",
        layer=LAYER_L2,
        artifact="coverage_validity_policy",
        decision_authority=AUTHORITY_L2_POLICY,
        reasons=["state_change_against_gold_polarity"],
    )
    assert decision.evidence_grade == GRADE_REJECT
    assert decision.activation_state == ACTIVATION_NONE
    assert decision.hard_reject_codes == (HARD_GOLD_POLARITY_REGRESSION,)


def test_hard_reject_is_invariant_across_profiles():
    for profile in GATE_PROFILES:
        decision = _promote(
            "accept_batch",
            reasons=["stable_canary_regression:case_x"],
            profile=profile,
        )
        assert decision.evidence_grade == GRADE_REJECT, profile
        assert decision.hard_reject_codes == (HARD_CANARY_REGRESSION,)


def test_hard_reject_cannot_be_overridden_by_positive_evidence():
    decision = _promote(
        "accept_policy",
        layer=LAYER_L2,
        artifact="boundary_consumption_policy",
        decision_authority=AUTHORITY_L2_POLICY,
        reasons=["replay_runtime_invalid:RuntimeError"],
        evidence=PromotionEvidence(
            execution_established=True,
            terminal_effect=True,
            gold_polarity=True,
            non_regression=True,
        ),
    )
    assert decision.evidence_grade == GRADE_REJECT
    assert decision.hard_reject_codes == (HARD_REPLAY_RUNTIME_INVALID,)


def test_reason_classification_prefix_table():
    hard, quality = classify_reason_codes([
        "unknown_action:rewrite_verdict",
        "protected_item: fitness_status=strong",
        "source_no_effect:no_measurable_target_local_change",
    ])
    assert hard == (HARD_UNKNOWN_VOCABULARY, HARD_PROTECTED_BASELINE_VIOLATION)
    assert quality == ("source_no_effect:no_measurable_target_local_change",)


def test_unmapped_reasons_stay_quality_never_hard():
    hard, quality = classify_reason_codes(["duplicate_content", "no_proposal"])
    assert hard == ()
    assert quality == ("duplicate_content", "no_proposal")


# ------------------------------------------------------------------
# Evidence-driven grading on the accept class
# ------------------------------------------------------------------

def test_accept_with_strong_evidence_earns_deployable():
    decision = _promote(
        "accept_batch",
        evidence=PromotionEvidence(
            structure_ok=True,
            execution_established=True,
            terminal_effect=True,
            gold_polarity=True,
            non_regression=True,
        ),
    )
    assert decision.evidence_grade == GRADE_STRONG
    assert decision.activation_state == ACTIVATION_DEPLOYABLE
    assert decision.evidence_table["gold_polarity"] == EVIDENCE_POSITIVE
    assert decision.evidence_table["non_regression"] == EVIDENCE_POSITIVE


def test_canary_alone_never_promotes_to_strong():
    """Canary non-regression is a veto, not a correctness authority."""
    decision = _promote(
        "accept_batch",
        evidence=PromotionEvidence(
            execution_established=True,
            terminal_effect=True,
            non_regression=True,
        ),
    )
    assert decision.evidence_grade == GRADE_WEAK
    assert decision.activation_state == ACTIVATION_WORKING


def test_independent_effect_supports_strong_without_gold():
    decision = _promote(
        "accept_policy",
        layer=LAYER_L2,
        artifact="coverage_validity_policy",
        decision_authority=AUTHORITY_L2_POLICY,
        evidence=PromotionEvidence(
            execution_established=True,
            terminal_effect=True,
            independent_effect=True,
            non_regression=True,
        ),
    )
    assert decision.evidence_grade == GRADE_STRONG


def test_regression_fact_blocks_strong():
    decision = _promote(
        "accept_batch",
        evidence=PromotionEvidence(
            execution_established=True,
            terminal_effect=True,
            gold_polarity=True,
            non_regression=False,
        ),
    )
    assert decision.evidence_grade == GRADE_WEAK


def test_default_evidence_table_is_unavailable_not_fabricated():
    decision = _promote("skip_stable", decision_authority=AUTHORITY_L1_STRUCTURAL)
    assert set(decision.evidence_table) == set(EVIDENCE_DIMENSIONS)
    assert set(decision.evidence_table.values()) == {EVIDENCE_UNAVAILABLE}


def test_negative_measurement_maps_to_negative_status():
    decision = _promote(
        "reject_fitness_no_effect",
        evidence=PromotionEvidence(execution_established=True, local_effect=False),
    )
    assert decision.evidence_table["execution"] == EVIDENCE_POSITIVE
    assert decision.evidence_table["local_effect"] == EVIDENCE_NEGATIVE


# ------------------------------------------------------------------
# Serialization
# ------------------------------------------------------------------

def test_round_trip_preserves_decision():
    decision = _promote(
        "reject_policy_fitness",
        layer=LAYER_L2,
        artifact="consensus_gate_policy",
        decision_authority=AUTHORITY_L2_POLICY,
        reasons=["state_change_against_gold_polarity", "extra_quality_reason"],
    )
    restored = PromotionDecision.from_dict(decision.to_dict())
    assert restored == decision
    assert restored.to_dict() == decision.to_dict()


def test_from_dict_rejects_unknown_schema():
    with pytest.raises(ValueError, match="unsupported promotion decision schema"):
        PromotionDecision.from_dict({"schema_version": "v0"})


def test_from_dict_rejects_unknown_grade_and_dimension():
    decision = _promote("accept_batch").to_dict()
    decision["evidence_grade"] = "amazing"
    with pytest.raises(ValueError, match="unknown evidence_grade"):
        PromotionDecision.from_dict(decision)
    decision = _promote("accept_batch").to_dict()
    decision["evidence_table"]["secret_dim"] = "positive"
    with pytest.raises(ValueError, match="unknown evidence dimensions"):
        PromotionDecision.from_dict(decision)
