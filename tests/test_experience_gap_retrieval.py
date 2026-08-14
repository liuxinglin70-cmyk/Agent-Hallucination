"""A CAPABILITY_GAP episode leaves a retrievable ``semantic_unavailable``
representative: a later case with the same mechanism sees the gap as
negative experience instead of an empty history, and a checkpoint written
before the slot existed still loads."""

from __future__ import annotations

from types import SimpleNamespace

from sehd_rise.causal.episode import FailureAttempt
from sehd_rise.experience.memory import ExperienceMemory


def _episode(episode_id: str) -> SimpleNamespace:
    slice_payload = SimpleNamespace(
        missing_fields=("certificate",),
        malformed_fields=(),
        candidate_kind="structured",
        consumer="proof_court",
        completeness="complete",
    )
    slice_payload.to_dict = lambda: {
        "missing_fields": ["certificate"],
        "malformed_fields": [],
        "candidate_kind": "structured",
        "consumer": "proof_court",
        "completeness": "complete",
    }
    return SimpleNamespace(
        episode_id=episode_id,
        skill_layer="l1_provider_rule",
        failure_owner="evidence_obligation",
        reason_code="gold_false_negative_clean",
        detection_snapshot={"evolution_direction": "process_repair"},
        causal_slice=slice_payload,
    )


def _gap_attempt() -> FailureAttempt:
    return FailureAttempt(
        attempt_id="attempt_gap_1",
        proposal_signature="sig_gap",
        operation="capability_gap",
        target_module="evidence_obligation",
        action_spec={"missing_capability": "construct_certificate"},
        structural_result="capability_gap",
        compiled=False,
        injected=False,
        applied=False,
        observable_delta=(),
        downstream_court_delta=(),
        no_effect_reason="",
        decision="semantic_unavailable",
        proposal_content="capability_gap:construct_certificate",
    )


def test_capability_gap_attempt_is_retrievable_negative_experience() -> None:
    memory = ExperienceMemory()
    episode_a = _episode("case_a")
    memory.upsert_observation(episode_a)
    memory.upsert_attempt(episode_a, _gap_attempt())

    refs = memory.retrieve(episode=_episode("case_b"), exclude_episode_id="case_b")
    gap_refs = [
        ref for ref in refs
        if (ref.local_outcome or {}).get("decision") == "semantic_unavailable"
    ]
    assert gap_refs, "same-mechanism retrieval must surface the gap attempt"
    assert gap_refs[0].episode_id == "case_a"
    assert gap_refs[0].action_spec == {
        "missing_capability": "construct_certificate"
    }
    assert gap_refs[0].local_outcome["structural_result"] == "capability_gap"


def test_checkpoint_from_before_the_gap_slot_loads() -> None:
    memory = ExperienceMemory()
    episode_a = _episode("case_a")
    memory.upsert_observation(episode_a)
    memory.upsert_attempt(episode_a, _gap_attempt())
    payload = memory.to_dict()
    # A checkpoint written before the slot existed has no such key.
    for mechanism in payload["failure_mechanisms"]:
        mechanism["representatives"].pop("semantic_unavailable", None)

    reloaded = ExperienceMemory()
    reloaded.from_dict(payload)
    refs = reloaded.retrieve(
        episode=_episode("case_b"), exclude_episode_id="case_b",
    )
    assert refs == []
