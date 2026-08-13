"""Tests for the §10/§13/§14#4/#6/#7 architecture items.

Covers, independently and without a live provider:

- §14#7: the FN capability coverage matrix names covered vs uncovered families
  and maps a failure shape + owner onto its family.
- §14#4: the candidate-failure classifier separates DSL_NOT_REPRESENTABLE /
  ENTITY_NOT_FOUND / BINDING_AMBIGUOUS / SEARCH_FAILURE / STRUCTURAL_INVALID,
  and the CandidateArchive preserves rejected candidates by failure class.
- §13: RepairIntent carries the DSL-independent repair intent and its missing
  capability, so a CAPABILITY_GAP is recorded instead of a silent no-edit.
- §10: resolve_run_mode maps the three regimes onto flags and enforces the
  protected-label-free canary prerequisite.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sehd_rise.causal.episode import FailureEdge
from sehd_rise.learning.protocol import (
    CandidateArchive,
    ExecutableCandidate,
    RepairIntent,
    STAGE_SCHEMA_VALID,
    classify_candidate_failure,
    FAILURE_CLASS_BINDING_AMBIGUOUS,
    FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
    FAILURE_CLASS_ENTITY_NOT_FOUND,
    FAILURE_CLASS_SEARCH_FAILURE,
    FAILURE_CLASS_STRUCTURAL_INVALID,
)
from sehd_rise.loop.run_mode import (
    RunMode,
    resolve_run_mode,
)
from sehd_rise.skills.capabilities import (
    fn_capability_coverage_matrix,
    fn_family_for,
)


# --- §14#7: FN capability coverage matrix -----------------------------------

def test_fn_coverage_matrix_reports_covered_and_uncovered() -> None:
    matrix = fn_capability_coverage_matrix()
    assert matrix["missing_claim_candidate"]["covered"] is True
    assert "construct_candidate" in matrix["missing_claim_candidate"]["operations"]
    # Families with no registered clean_to_error primitive are explicit gaps.
    assert matrix["missing_evidence_acquisition"]["covered"] is False
    assert matrix["missing_certificate_path"]["operations"] == []


def test_fn_coverage_kind_distinguishes_primitive_and_specialization() -> None:
    matrix = fn_capability_coverage_matrix()
    # construct_candidate IS the missing-claim mechanism.
    assert matrix["missing_claim_candidate"]["coverage_kind"] == "primitive"
    assert matrix["missing_claim_candidate"]["covered_by"] == "construct_candidate"
    # These three collapse onto the same primitive; the family lives in the
    # learned condition, not a distinct opcode.
    for family in (
        "cross_claim_dependency",
        "tool_grounding_requirement",
        "temporal_numeric_entity_consistency",
    ):
        assert matrix[family]["coverage_kind"] == "condition_specialization"
        assert matrix[family]["covered_by"] == "construct_candidate"


def test_fn_coverage_uncovered_rows_carry_blocker_and_required_capability() -> None:
    matrix = fn_capability_coverage_matrix()
    uncovered = {
        family
        for family, row in matrix.items()
        if row["coverage_kind"] == "provider_side_capability"
    }
    assert uncovered == {
        "missing_evidence_obligation",
        "missing_evidence_acquisition",
        "wrong_qualification",
        "missing_verifier_route",
        "wrong_verifier_instruction",
        "missing_certificate_path",
        "coverage_blind_spot",
    }
    for family in uncovered:
        row = matrix[family]
        assert row["covered"] is False
        assert row["covered_by"] == ""
        assert row["required_capability"]
        assert row["blocker"]
    # A spot-check: the blocker mirrors the owner's registered does_not_support.
    assert "collect missing evidence" in matrix["missing_evidence_acquisition"]["blocker"]
    assert "invoke a verifier" in matrix["wrong_qualification"]["blocker"]
    assert "create missing certificate" in matrix["missing_certificate_path"]["blocker"]


def test_fn_family_for_maps_failure_shape_and_owner() -> None:
    assert (
        fn_family_for("gold_false_negative_clean", "candidate_trigger")
        == "missing_claim_candidate"
    )
    # Forward-recall only: an FP shape never gets an FN family.
    assert fn_family_for("gold_false_positive", "evidence_obligation") == ""


# --- §14#4: candidate-failure classification -------------------------------

def test_classify_candidate_failure_dsl_not_representable() -> None:
    assert classify_candidate_failure(
        "condition_feature_not_bindable_in_domain:claim_anchor:candidate_kind"
    ) == FAILURE_CLASS_DSL_NOT_REPRESENTABLE
    assert classify_candidate_failure(
        "condition_feature_unregistered:missing_field"
    ) == FAILURE_CLASS_DSL_NOT_REPRESENTABLE


def test_classify_candidate_failure_entity_not_found() -> None:
    assert classify_candidate_failure(
        "target_entity_not_found:claim_anchor:anchor_123"
    ) == FAILURE_CLASS_ENTITY_NOT_FOUND
    assert classify_candidate_failure("BINDING_NOT_FOUND") == FAILURE_CLASS_ENTITY_NOT_FOUND


def test_classify_candidate_failure_binding_ambiguous() -> None:
    assert classify_candidate_failure("BINDING_AMBIGUOUS") == FAILURE_CLASS_BINDING_AMBIGUOUS


def test_classify_candidate_failure_search_failure() -> None:
    assert classify_candidate_failure(
        "repair_edge_id_unknown:edge_123"
    ) == FAILURE_CLASS_SEARCH_FAILURE
    assert classify_candidate_failure(
        "action_not_bound_to_repair_edge:edge_1:anchor_id->certificate_id"
    ) == FAILURE_CLASS_SEARCH_FAILURE
    # A wrong-polarity or unowned opcode is a search miss, not a DSL gap.
    assert classify_candidate_failure(
        "action_polarity_mismatch:clean_to_error:candidate_trigger:construct_candidate"
    ) == FAILURE_CLASS_SEARCH_FAILURE
    assert classify_candidate_failure(
        "action_opcode_unowned:withhold_certificate"
    ) == FAILURE_CLASS_SEARCH_FAILURE


def test_classify_candidate_failure_structural_default() -> None:
    assert classify_candidate_failure(
        "condition_overload"
    ) == FAILURE_CLASS_STRUCTURAL_INVALID


def test_candidate_archive_preserves_by_failure_class() -> None:
    archive = CandidateArchive(capacity=64)
    archive.archive(
        stage=STAGE_SCHEMA_VALID,
        failure_class=FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
        reason="condition_feature_not_bindable_in_domain:claim_anchor:candidate_kind",
        candidate=ExecutableCandidate(
            operation="add_rule", target_module="candidate_trigger",
            action="construct_candidate: anchor_id", condition="candidate_kind",
        ),
    )
    counts = archive.failure_class_counts()
    assert counts[FAILURE_CLASS_DSL_NOT_REPRESENTABLE] == 1
    recent = archive.recent(failure_class=FAILURE_CLASS_DSL_NOT_REPRESENTABLE)
    assert len(recent) == 1
    assert archive.recent(failure_class=FAILURE_CLASS_SEARCH_FAILURE) == ()


# --- §13: RepairIntent carries missing capability --------------------------

def _episode():
    edge = FailureEdge(
        producer_route="local_constructive_harness",
        output_object="candidate",
        field="anchor_id",
        consumer="proof_court",
        observed="absent",
        required="present",
        owner_module="candidate_trigger",
    )
    return SimpleNamespace(
        failure_owner="candidate_trigger",
        skill_layer="l1_provider_rule",
        reason_code="gold_false_negative_clean",
        terminal_state="verified_clean",
        blocking_predicate="gold_polarity_mismatch",
        causal_failure_edge=edge,
    )


def test_repair_intent_records_missing_capability() -> None:
    intent = RepairIntent.from_episode(
        _episode(), missing_capability="construct_candidate",
    )
    payload = intent.to_dict()
    assert payload["owner"] == "candidate_trigger"
    assert payload["output_field"] == "anchor_id"
    assert payload["missing_capability"] == "construct_candidate"


# --- §10: run-mode tri-section ---------------------------------------------

def test_run_mode_supervised() -> None:
    resolved = resolve_run_mode(
        "supervised_evolution", use_gold=False, fitness_use_gold=False,
        gold_reflection=False,
    )
    assert resolved.use_gold is True
    assert resolved.fitness_use_gold is True
    assert resolved.claim_learning is True


def test_run_mode_label_free_diagnostic() -> None:
    resolved = resolve_run_mode(
        "label_free_diagnostic", use_gold=True, fitness_use_gold=True,
        gold_reflection=True,
    )
    assert resolved.use_gold is False
    assert resolved.fitness_use_gold is False
    assert resolved.claim_learning is False


def test_run_mode_protected_label_free_requires_canary() -> None:
    resolved = resolve_run_mode(
        "protected_label_free_adaptation", use_gold=False,
        fitness_use_gold=False, gold_reflection=False,
        canary_case_ids=("c1",),
    )
    # A canary proves process non-regression, never correctness, so the
    # protected label-free regime records adaptation, not learning success.
    assert resolved.claim_learning is False
    assert resolved.requires_canary is True
    with pytest.raises(ValueError, match="canary_case_ids"):
        resolve_run_mode(
            "protected_label_free_adaptation", use_gold=False,
            fitness_use_gold=False, gold_reflection=False,
        )


def test_run_mode_unknown_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown run_mode"):
        resolve_run_mode(
            "mystery_mode", use_gold=False, fitness_use_gold=False,
            gold_reflection=False,
        )


def test_run_mode_enum_values() -> None:
    assert set(m.value for m in RunMode) == {
        "supervised_evolution",
        "label_free_diagnostic",
        "protected_label_free_adaptation",
    }
