"""Phase 2: EvolutionExperienceMemory — migration, round-trip, retrieval
levels, slot replacement, idempotent upserts and the no-raw-trajectory
firewall."""

from __future__ import annotations

import json

import pytest

from src.sehd_v6.core.bccse.evolution_experience_memory import (
    SCHEMA_VERSION,
    EvolutionExperienceMemory,
    NO_EFFECT_REASONS,
)
from src.sehd_v6.core.bccse.failure_episode import (
    FailureAttempt,
    build_failure_episode,
)
from src.sehd_v6.core.bccse.v6_contracts import LAYER_L1, FailureAttribution
from src.sehd_v6.core.htc_tes.type_aware_fitness import TypeExperienceMemory

from tests.fixtures.causal_evolution import (
    missing_evidence_field,
    signal_for,
)


def _episode(
    *,
    record_key: str = "case_source",
    direction: str = "unresolved_to_decisive",
    reason: str = "provider_artifact_missing",
):
    return build_failure_episode(
        detection_result=missing_evidence_field(),
        signal=signal_for("unresolved"),
        attribution=FailureAttribution(
            layer=LAYER_L1,
            artifact="evidence_obligation",
            reason_code=reason,
            evidence={
                "kind": "error_certificate",
                "missing_fields": ["certificate"],
            },
        ),
        evolution_direction=direction,
        record_key=record_key,
        allowed_modules=("evidence_obligation", "atomic_verifier_instruction"),
    )


def _attempt(
    *,
    decision: str = "reject_fitness_no_effect",
    no_effect_reason: str = "condition_not_matched",
    compiled: bool = True,
    injected: bool = True,
    applied: bool = False,
    deltas: tuple[str, ...] = (),
) -> FailureAttempt:
    return FailureAttempt(
        attempt_id="attempt_1",
        proposal_signature="sig_1",
        operation="add_rule",
        target_module="evidence_obligation",
        action_spec={"module_id": "evidence_obligation"},
        structural_result="accepted",
        compiled=compiled,
        injected=injected,
        applied=applied,
        observable_delta=deltas,
        downstream_court_delta=(),
        no_effect_reason=no_effect_reason,
        decision=decision,
    )


def test_legacy_type_only_artifact_migrates_losslessly() -> None:
    legacy = {
        "max_per_type": 50,
        "types": {
            "factual": [
                {
                    "hallucination_type": "factual",
                    "evolution_direction": "unresolved_to_decisive",
                    "module_targeted": "evidence_obligation",
                    "strategy_used": "operation:add_rule",
                    "detection_improved": True,
                    "source_case_id": "case_x",
                    "timestamp": 1.0,
                    "metadata": {"outcome": "improved"},
                    "feature_description": "factual claim",
                    "type_confidence": 0.4,
                }
            ]
        },
    }
    memory = EvolutionExperienceMemory()
    memory.from_dict(legacy)
    assert memory.entries() == ()
    serialized = memory.to_dict()
    assert serialized["schema_version"] == SCHEMA_VERSION
    assert serialized["failure_mechanisms"] == []
    assert serialized["type_experience_memory"]["types"]["factual"][0][
        "metadata"
    ]["outcome"] == "improved"


def test_new_schema_round_trip_preserves_normalized_content() -> None:
    memory = EvolutionExperienceMemory()
    episode = _episode()
    memory.upsert_observation(episode)
    memory.upsert_attempt(
        episode,
        _attempt(decision="accept_batch", no_effect_reason="expected_delta_observed"),
    )
    payload = memory.to_dict()
    restored = EvolutionExperienceMemory()
    restored.from_dict(payload)
    assert restored.to_dict() == payload
    refs = restored.retrieve(
        episode=_episode(record_key="case_other"),
        exclude_episode_id="nope",
    )
    assert len(refs) == 1
    assert refs[0].local_outcome["decision"] == "accept_batch"


def test_retrieval_levels_exact_then_fallback_then_none() -> None:
    memory = EvolutionExperienceMemory()
    base = _episode()
    memory.upsert_observation(base)
    memory.upsert_attempt(
        base, _attempt(decision="accept_batch", no_effect_reason="expected_delta_observed")
    )

    same = _episode(record_key="other_case_id")
    refs = memory.retrieve(episode=same, exclude_episode_id="x")
    assert len(refs) == 1
    assert refs[0].mechanism_signature == memory.entries()[0].signature

    # Level-2 fallback: same layer+artifact+direction+consumer, different
    # reason code (reason is not part of the level-2 key).
    deflated = _episode(
        record_key="other_case_id",
        reason="provider_artifact_malformed",
    )
    refs = memory.retrieve(episode=deflated, exclude_episode_id="x")
    assert len(refs) == 1

    # Level-3 fallback: a persisted entry whose consumer differs while
    # layer+artifact+direction stay identical; the level-2 key must miss
    # and the level-3 key must hit.
    from src.sehd_v6.core.bccse.evolution_experience_memory import (
        MechanismEntry,
    )

    base_signature = memory.entries()[0].signature
    altered = MechanismEntry(
        signature=(
            *base_signature[:5],
            "adjudication_policy",
            *base_signature[6:],
        )
    )
    altered.observation_count = 7
    altered.representatives["effective"] = memory.entries()[
        0
    ].representatives["effective"]
    memory._entries.clear()
    memory._entries[altered.signature] = altered
    refs = memory.retrieve(episode=deflated, exclude_episode_id="x")
    assert len(refs) == 1
    assert refs[0].mechanism_signature == altered.signature

    unrelated = build_failure_episode(
        detection_result=missing_evidence_field(),
        signal=signal_for("unresolved"),
        attribution=FailureAttribution(
            layer=LAYER_L1,
            artifact="candidate_trigger",
            reason_code="provider_artifact_missing",
            evidence={"kind": "candidate", "missing_fields": ["candidate"]},
        ),
        evolution_direction="unresolved_to_decisive",
        record_key="case_other",
        allowed_modules=("candidate_trigger",),
    )
    assert memory.retrieve(episode=unrelated, exclude_episode_id="x") == []


def test_current_episode_is_excluded_from_retrieval() -> None:
    memory = EvolutionExperienceMemory()
    episode = _episode()
    memory.upsert_observation(episode)
    memory.upsert_attempt(
        episode, _attempt(decision="accept_batch", no_effect_reason="expected_delta_observed")
    )
    refs = memory.retrieve(episode=episode, exclude_episode_id=episode.episode_id)
    assert refs == []
    refs = memory.retrieve(episode=episode, exclude_episode_id="other")
    assert len(refs) == 1


def test_outcome_slot_replacement_order() -> None:
    memory = EvolutionExperienceMemory()
    episode = _episode()

    memory.upsert_attempt(
        episode,
        _attempt(
            decision="reject_fitness_no_effect",
            no_effect_reason="condition_not_matched",
            applied=False,
        ),
    )
    first_slot = memory.entries()[0].representatives["no_effect"]
    assert first_slot is not None
    assert first_slot.outcome["no_effect_reason"] == "condition_not_matched"

    memory.upsert_attempt(
        episode,
        _attempt(
            decision="reject_fitness_no_effect",
            no_effect_reason="provider_did_not_apply_action",
            applied=True,
        ),
    )
    second_slot = memory.entries()[0].representatives["no_effect"]
    assert second_slot.outcome["no_effect_reason"] == "provider_did_not_apply_action"

    memory.upsert_attempt(
        episode,
        _attempt(
            decision="reject_fitness_no_effect",
            no_effect_reason="unresolved_no_effect",
            applied=False,
        ),
    )
    third_slot = memory.entries()[0].representatives["no_effect"]
    assert third_slot.outcome["no_effect_reason"] == "provider_did_not_apply_action"

    # A complete effective attempt occupies its own slot, never the
    # no-effect slot.
    memory.upsert_attempt(
        episode,
        _attempt(decision="accept_batch", no_effect_reason="expected_delta_observed"),
    )
    entry = memory.entries()[0]
    assert entry.representatives["effective"] is not None
    assert entry.representatives["no_effect"] is second_slot


def test_statistics_accumulate() -> None:
    memory = EvolutionExperienceMemory()
    episode = _episode()
    memory.upsert_observation(episode)
    memory.upsert_observation(episode)
    entry = memory.entries()[0]
    assert entry.observation_count == 1  # dedup by episode id
    memory.upsert_attempt(episode, _attempt(decision="reject_fitness_no_effect"))
    memory.upsert_attempt(episode, _attempt(decision="accept_batch"))
    entry = memory.entries()[0]
    assert entry.outcome_counts == {
        "reject_fitness_no_effect": 1,
        "accept_batch": 1,
    }
    assert entry.observation_count == 1
    assert entry.representatives["no_effect"] is not None
    assert entry.representatives["effective"] is not None


def test_resume_upsert_is_idempotent() -> None:
    memory = EvolutionExperienceMemory()
    episode = _episode()
    memory.upsert_observation(episode)
    memory.upsert_observation(episode)
    assert memory.entries()[0].observation_count == 1

    payload = memory.to_dict()
    restored = EvolutionExperienceMemory()
    restored.from_dict(payload)
    restored.upsert_observation(episode)
    assert restored.entries()[0].observation_count == 1

    memory.upsert_observation(_episode(record_key="second_case"))
    assert memory.entries()[0].observation_count == 2


def test_memory_contains_no_raw_trajectory_or_evaluator_fields() -> None:
    memory = EvolutionExperienceMemory()
    episode = _episode(record_key="secret_case")
    memory.upsert_observation(episode)
    memory.upsert_attempt(
        episode, _attempt(decision="accept_batch", no_effect_reason="expected_delta_observed")
    )
    serialized = json.dumps(memory.to_dict(), ensure_ascii=False)
    assert "secret_case" not in serialized
    assert "gold_label" not in serialized
    assert "gold_explanation" not in serialized
    assert "trajectory" not in serialized


def test_no_proposal_slot_serves_reflection_when_no_effective() -> None:
    memory = EvolutionExperienceMemory()
    episode = _episode()
    memory.upsert_attempt(episode, _attempt(decision="no_edit", no_effect_reason=""))
    refs = memory.retrieve(
        episode=_episode(record_key="case_other"),
        exclude_episode_id="x",
    )
    assert len(refs) == 1
    assert refs[0].local_outcome["decision"] == "no_edit"


def test_no_effect_vocabulary_is_closed() -> None:
    assert NO_EFFECT_REASONS == {
        "condition_not_matched",
        "provider_did_not_apply_action",
        "output_changed_then_removed_or_quarantined",
        "provider_output_changed_but_downstream_invariant",
        "expected_delta_observed",
        "unresolved_no_effect",
    }


def test_reference_contains_shared_and_private_facts() -> None:
    memory = EvolutionExperienceMemory()
    episode = _episode()
    memory.upsert_observation(episode)
    memory.upsert_attempt(
        episode, _attempt(decision="accept_batch", no_effect_reason="expected_delta_observed")
    )
    current = _episode(record_key="case_other")
    refs = memory.retrieve(episode=current, exclude_episode_id="x")
    assert len(refs) == 1
    reference = refs[0]
    assert reference.shared_facts["artifact"] == "evidence_obligation"
    assert reference.shared_facts["normalized_fields"] == ["certificate"]
    assert reference.action_spec["module_id"] == "evidence_obligation"
    assert reference.local_outcome["decision"] == "accept_batch"
    assert reference.episode_id == episode.episode_id


def test_runner_persists_combined_memory_artifact(tmp_path, monkeypatch) -> None:
    from src.sehd_v6.core.bccse import evolution_runner as runner

    def detector(*, genome, case_id, **kwargs):
        if case_id == "anchor":
            return {"state": "verified_clean", "runtime_valid": True, "census": []}
        return missing_evidence_field()

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner,
        "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: signal_for(
            str(result["state"])
        ),
    )
    monkeypatch.setattr(
        runner, "json_object_provider_call",
        lambda **kwargs: ({}, {}),
    )
    runner.run_online_evolution(
        evolution_case_ids=("source",),
        canary_case_ids=("anchor",),
        records={"source": {}, "anchor": {}},
        config={},
        output_dir=tmp_path,
        census_root=tmp_path,
        mutation_route=None,
        run_id="combined_memory",
    )
    payload = json.loads(
        (tmp_path / "htc_tes_experience_memory.json").read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["failure_mechanisms"]
    entry = payload["failure_mechanisms"][0]
    assert entry["observation_count"] == 1
    assert entry["signature"][1] == "evidence_obligation"
