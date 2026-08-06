"""P7: Meta Review aggregation, outcome validation, guidance and drift."""

from __future__ import annotations

import pytest

from src.sehd_v6.core.bccse.gate_feedback import GateEvent, SCHEMA_VERSION as GE_SCHEMA
from src.sehd_v6.core.bccse.meta_review import (
    FOLLOW_UP_PENDING,
    OUTCOME_EXACT_FALSE_REJECT,
    OUTCOME_GATE_CLASS_COUNTEREVIDENCE,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_SUPPORTS_ACCEPT,
    OUTCOME_SUPPORTS_REJECT,
    GateExperience,
    aggregate_gate_experience,
    build_drift_snapshot,
    classify_outcome,
    generate_meta_edit_candidates,
    project_meta_review_for_prompt,
    rank_candidates,
)
from src.sehd_v6.core.bccse.meta_skill import (
    GUIDANCE_TOKENS,
    RANKING_PREFERENCES,
    create_baseline_meta_skill,
)


def make_event(**overrides) -> GateEvent:
    base = dict(
        schema_version=GE_SCHEMA,
        event_id="evt_1",
        subject_id="subj",
        episode_id="ep",
        attempt_id="att",
        layer="l2",
        artifact="policy_genome",
        operation="add_item",
        stage="fitness_gate",
        legacy_decision="reject_policy_no_effect",
        evidence_grade="reject",
        activation_state="",
        quality_reason_codes=("policy_route_unavailable",),
        execution_trace_class="applied",
        observable_delta_class="none",
        downstream_delta_class="none",
    )
    base.update(overrides)
    return GateEvent(**base)


# ============================================================
# P7.1 GateExperience aggregation
# ============================================================

def test_aggregate_gate_experience_from_event():
    experience = aggregate_gate_experience(make_event(), mechanism_signature=("l2", "policy"))
    assert isinstance(experience, GateExperience)
    assert experience.follow_up_status == FOLLOW_UP_PENDING
    assert experience.mechanism_signature == ("l2", "policy")
    assert experience.evidence_grade == "reject"


def test_gate_experience_roundtrip():
    experience = aggregate_gate_experience(make_event())
    restored = GateExperience.from_dict(experience.to_dict())
    assert restored.gate_event_id == experience.gate_event_id


# ============================================================
# P7.2 OutcomeValidation: exact false reject vs class counterevidence
# ============================================================

def test_exact_false_reject_requires_all_conditions():
    outcome = classify_outcome(
        gate_event_id="e1",
        validation_event_id="v1",
        same_canonical_intervention=True,
        compatible_discriminator=True,
        independent_validation=True,
        positive_effect=True,
        no_regression=True,
        same_mechanism=True,
    )
    assert outcome.outcome == OUTCOME_EXACT_FALSE_REJECT


def test_missing_independent_validation_not_exact_false_reject():
    outcome = classify_outcome(
        gate_event_id="e1",
        validation_event_id="v1",
        same_canonical_intervention=True,
        compatible_discriminator=True,
        independent_validation=False,  # not independent
        positive_effect=True,
        no_regression=True,
        same_mechanism=True,
    )
    assert outcome.outcome != OUTCOME_EXACT_FALSE_REJECT


def test_class_counterevidence_separated_from_exact_false_reject():
    outcome = classify_outcome(
        gate_event_id="e1",
        validation_event_id="v1",
        same_canonical_intervention=False,  # different intervention
        compatible_discriminator=True,
        independent_validation=True,
        positive_effect=True,
        no_regression=True,
        same_mechanism=True,
    )
    assert outcome.outcome == OUTCOME_GATE_CLASS_COUNTEREVIDENCE


def test_forbidden_source_never_establishes_false_reject():
    for forbidden in (True,):
        outcome = classify_outcome(
            gate_event_id="e1",
            validation_event_id="v1",
            same_canonical_intervention=True,
            compatible_discriminator=True,
            independent_validation=True,
            positive_effect=True,
            no_regression=True,
            same_mechanism=True,
            forbidden_source=forbidden,
        )
        assert outcome.outcome == OUTCOME_INCONCLUSIVE


def test_supports_accept_and_reject():
    assert classify_outcome(
        gate_event_id="e", validation_event_id="v",
        same_canonical_intervention=False, compatible_discriminator=False,
        independent_validation=True, positive_effect=True, no_regression=True,
        same_mechanism=False,
    ).outcome == OUTCOME_SUPPORTS_ACCEPT
    assert classify_outcome(
        gate_event_id="e", validation_event_id="v",
        same_canonical_intervention=False, compatible_discriminator=False,
        independent_validation=True, positive_effect=False, no_regression=True,
        same_mechanism=False,
    ).outcome == OUTCOME_SUPPORTS_REJECT


# ============================================================
# P7.3 deterministic MetaEdit candidates
# ============================================================

def test_candidates_are_deterministic_and_closed_vocabulary():
    experiences = [aggregate_gate_experience(make_event())]
    state = create_baseline_meta_skill()
    candidates = generate_meta_edit_candidates(experiences, [], state)
    assert candidates  # policy_route_unavailable -> prefer_registered_runtime_route
    for edit in candidates:
        assert edit.target in GUIDANCE_TOKENS or edit.target in RANKING_PREFERENCES


def test_candidates_not_reproposed_when_satisfied():
    experiences = [aggregate_gate_experience(make_event())]
    state = create_baseline_meta_skill()
    first = generate_meta_edit_candidates(experiences, [], state)
    from src.sehd_v6.core.bccse.meta_skill import apply_meta_edit
    for edit in first:
        state = apply_meta_edit(state, edit)
    assert generate_meta_edit_candidates(experiences, [], state) == []


# ============================================================
# P7.4 prompt projection + ranking (never enlarges candidate set)
# ============================================================

def test_prompt_projection_is_bounded_safe_and_guidance_only():
    from src.sehd_v6.core.bccse.meta_skill import MetaEdit, OP_ADD_GUIDANCE, apply_meta_edit
    state = apply_meta_edit(
        create_baseline_meta_skill(),
        MetaEdit(edit_id="m", operation=OP_ADD_GUIDANCE, target="require_independent_anchor"),
    )
    projection = project_meta_review_for_prompt(state)
    assert projection["authority"] == "evolution_guidance_only"
    assert set(projection["guidance_tokens"]) <= GUIDANCE_TOKENS
    text = str(projection).lower()
    for forbidden in ("gold", "case/", "prompt", "provider"):
        assert forbidden not in text


def test_ranking_never_enlarges_candidate_set():
    features = {
        "c1": {"owned_output_match": True},
        "c2": {"owned_output_match": False},
        "c3": {},
    }
    from src.sehd_v6.core.bccse.meta_skill import MetaEdit, OP_ENABLE_RANKING, apply_meta_edit
    state = apply_meta_edit(
        create_baseline_meta_skill(),
        MetaEdit(edit_id="m", operation=OP_ENABLE_RANKING, target="owned_output_match"),
    )
    ranked = rank_candidates(["c1", "c2", "c3"], features, state)
    assert set(ranked) == {"c1", "c2", "c3"}  # same set
    assert ranked[0] == "c1"  # matching candidate ranks first


# ============================================================
# P7.5 drift snapshot (reporting only)
# ============================================================

def test_drift_snapshot_aggregates_distributions():
    experiences = [
        aggregate_gate_experience(make_event(event_id="a")),
        aggregate_gate_experience(make_event(event_id="b", evidence_grade="weak", activation_state="working")),
    ]
    snapshot = build_drift_snapshot(experiences, [], registry_size=12)
    assert snapshot.evidence_grade_distribution.get("reject") == 1
    assert snapshot.evidence_grade_distribution.get("weak") == 1
    assert snapshot.registry_size == 12
    assert snapshot.false_reject_count == 0
