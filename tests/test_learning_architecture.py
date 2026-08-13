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
    ARCHIVE_SIGNAL_ANCHOR_PROJECTION,
    ARCHIVE_SIGNAL_DSL_CURRICULUM,
    ARCHIVE_SIGNAL_SEARCH_POLICY,
    CandidateArchive,
    ExecutableCandidate,
    FailureObservation,
    RepairIntent,
    STAGE_SCHEMA_VALID,
    archive_policy_signals,
    classify_candidate_failure,
    FAILURE_CLASS_BINDING_AMBIGUOUS,
    FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
    FAILURE_CLASS_ENTITY_NOT_FOUND,
    FAILURE_CLASS_SEARCH_FAILURE,
    FAILURE_CLASS_STRUCTURAL_INVALID,
)
from sehd_rise.loop.mechanism import classify_failure_mechanism
from sehd_rise.loop.run_mode import (
    RunMode,
    resolve_run_mode,
)
from sehd_rise.skills.capabilities import (
    fn_capability_coverage_matrix,
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


def _observation(**overrides) -> FailureObservation:
    """A gold FN-clean observation on candidate_trigger with plain evidence."""
    base = dict(
        terminal_state="verified_clean",
        blocking_predicate="gold_polarity_mismatch",
        failure_owner="candidate_trigger",
        skill_layer="l1_provider_rule",
        reason_code="gold_false_negative_clean",
        missing_fields=("anchor_id",),
        malformed_fields=(),
        candidate_kind="unknown",
        output_object="candidate",
        output_field="anchor_id",
        consumer="proof_court",
    )
    base.update(overrides)
    return FailureObservation(**base)


def test_mechanism_classifier_distinguishes_candidate_trigger_families() -> None:
    """One owner, four families: the observation decides, not the owner table.

    ``candidate_trigger`` hosts all four of these families, so a first-match
    owner walk would collapse them onto ``missing_claim_candidate``.
    """
    assert (
        classify_failure_mechanism(_observation())
        == "missing_claim_candidate"
    )
    assert (
        classify_failure_mechanism(_observation(dependency_refs=("event_002",)))
        == "cross_claim_dependency"
    )
    assert (
        classify_failure_mechanism(
            _observation(trajectory_hints=("tool_result_cited",)),
        )
        == "tool_grounding_requirement"
    )
    assert (
        classify_failure_mechanism(
            _observation(trajectory_hints=("numeric_locus",)),
        )
        == "temporal_numeric_entity_consistency"
    )


def test_mechanism_classifier_tool_grounding_beats_numeric_locus() -> None:
    """The matcher order is deterministic: tool grounding is the stronger
    mechanism even when the locus also carries numbers."""
    assert (
        classify_failure_mechanism(
            _observation(
                trajectory_hints=("tool_result_cited", "numeric_locus"),
            ),
        )
        == "tool_grounding_requirement"
    )


def test_mechanism_classifier_non_fn_shapes_get_no_family() -> None:
    # Forward-recall only: an FP shape never gets an FN family.
    assert (
        classify_failure_mechanism(
            _observation(
                reason_code="gold_false_positive",
                failure_owner="evidence_obligation",
            ),
        )
        == ""
    )
    # Owners outside the declared FN surface never get a guessed family.
    assert (
        classify_failure_mechanism(_observation(failure_owner="unknown_owner"))
        == ""
    )


def test_mechanism_classifier_uses_owner_default_when_evidence_is_plain() -> None:
    """A multi-family owner without discriminating evidence lands on its
    plain family, e.g. evidence_obligation -> missing_evidence_obligation."""
    observation = _observation(
        failure_owner="evidence_obligation",
        output_object="error_certificate",
        output_field="coverage_state",
        missing_fields=(),
    )
    assert (
        classify_failure_mechanism(observation)
        == "missing_evidence_obligation"
    )


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


def _archive_with_runs(classes: tuple[str, ...]) -> CandidateArchive:
    """An archive whose tail matches ``classes`` (newest last)."""
    archive = CandidateArchive(capacity=64)
    for index, failure_class in enumerate(classes):
        archive.archive(
            stage=STAGE_SCHEMA_VALID,
            failure_class=failure_class,
            reason=f"reason_{index}",
            candidate=ExecutableCandidate(
                operation="add_rule", target_module="candidate_trigger",
            ),
        )
    return archive


def test_archive_policy_signals_fire_on_consecutive_runs() -> None:
    """A same-class tail reaching the threshold emits exactly its own signal."""
    assert archive_policy_signals(
        _archive_with_runs((
            FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
            FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
            FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
            FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
        )),
    ) == (ARCHIVE_SIGNAL_DSL_CURRICULUM,)
    assert archive_policy_signals(
        _archive_with_runs((
            FAILURE_CLASS_SEARCH_FAILURE,
            FAILURE_CLASS_SEARCH_FAILURE,
            FAILURE_CLASS_SEARCH_FAILURE,
            FAILURE_CLASS_SEARCH_FAILURE,
        )),
    ) == (ARCHIVE_SIGNAL_SEARCH_POLICY,)
    assert archive_policy_signals(
        _archive_with_runs((
            FAILURE_CLASS_ENTITY_NOT_FOUND,
            FAILURE_CLASS_ENTITY_NOT_FOUND,
            FAILURE_CLASS_ENTITY_NOT_FOUND,
            FAILURE_CLASS_ENTITY_NOT_FOUND,
        )),
    ) == (ARCHIVE_SIGNAL_ANCHOR_PROJECTION,)


def test_archive_policy_signals_require_the_threshold() -> None:
    assert archive_policy_signals(
        _archive_with_runs((
            FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
            FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
            FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
        )),
    ) == ()


def test_archive_policy_signals_do_not_cross_pollute() -> None:
    """An interleaved tail emits nothing: one class never steers another's
    learner, and an older dsl run cannot fire behind a search tail."""
    assert archive_policy_signals(
        _archive_with_runs((
            FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
            FAILURE_CLASS_SEARCH_FAILURE,
            FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
            FAILURE_CLASS_SEARCH_FAILURE,
            FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
            FAILURE_CLASS_SEARCH_FAILURE,
        )),
    ) == ()
    # dsl x4 followed by one search rejection: the tail is search x1.
    assert archive_policy_signals(
        _archive_with_runs((
            FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
            FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
            FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
            FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
            FAILURE_CLASS_SEARCH_FAILURE,
        )),
    ) == ()
    # Non-routing classes never signal, however long their run.
    assert archive_policy_signals(
        _archive_with_runs((
            FAILURE_CLASS_STRUCTURAL_INVALID,
            FAILURE_CLASS_STRUCTURAL_INVALID,
            FAILURE_CLASS_STRUCTURAL_INVALID,
            FAILURE_CLASS_STRUCTURAL_INVALID,
        )),
    ) == ()


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


def test_failure_observation_from_episode_carries_edge_identity() -> None:
    """The typed observation carries the causal-edge identity and the
    cross-claim dependency refs the mechanism classifier discriminates on."""
    edge = FailureEdge(
        producer_route="local_constructive_harness",
        output_object="candidate",
        field="anchor_id",
        consumer="proof_court",
        observed="absent",
        required="present",
        owner_module="candidate_trigger",
    )
    slice_payload = SimpleNamespace(
        missing_fields=("anchor_id",),
        malformed_fields=(),
        candidate_kind="unknown",
        dependency_refs=("event_002",),
    )
    episode = SimpleNamespace(
        terminal_state="verified_clean",
        blocking_predicate="gold_polarity_mismatch",
        failure_owner="candidate_trigger",
        skill_layer="l1_provider_rule",
        reason_code="gold_false_negative_clean",
        causal_slice=slice_payload,
        causal_failure_edge=edge,
    )
    observation = FailureObservation.from_episode(episode)
    assert observation.output_object == "candidate"
    assert observation.output_field == "anchor_id"
    assert observation.consumer == "proof_court"
    assert observation.dependency_refs == ("event_002",)
    assert observation.trajectory_hints == ()
    payload = observation.to_dict()
    assert payload["output_object"] == "candidate"
    assert payload["output_field"] == "anchor_id"
    assert payload["consumer"] == "proof_court"
    assert payload["dependency_refs"] == ["event_002"]


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
