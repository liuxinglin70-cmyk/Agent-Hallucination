"""Phase 1: FailureEpisode builder, stable identity, mechanism signature,
prompt firewall and runner audit wiring."""

from __future__ import annotations

import json

import pytest

from src.sehd_v6.core.bccse.failure_episode import (
    SCHEMA_VERSION,
    FailureAttempt,
    UnaddressableFailure,
    attempt_id_for,
    build_failure_episode,
    build_mechanism_signature,
    project_failure_episode_for_prompt,
    validate_failure_episode,
)
from src.sehd_v6.core.bccse.v6_contracts import LAYER_L1, FailureAttribution

from tests.fixtures.causal_evolution import (
    detection_result,
    l2_policy_movement,
    malformed_certificate,
    missing_evidence_field,
    signal_for,
    stable_clean,
    unconsumed_provider_output,
)


def _l1_attribution(
    artifact: str = "evidence_obligation",
    reason: str = "provider_artifact_missing",
    **evidence: object,
) -> FailureAttribution:
    default_evidence: dict[str, object] = {
        "kind": "error_certificate",
        "missing_fields": ["certificate"],
    }
    default_evidence.update(evidence)
    return FailureAttribution(
        layer=LAYER_L1,
        artifact=artifact,
        reason_code=reason,
        evidence=default_evidence,
    )


def _build(
    detection,
    *,
    attribution=None,
    record_key: str = "case_source",
    allowed=("evidence_obligation", "atomic_verifier_instruction"),
    direction: str = "unresolved_to_decisive",
    attempts=(),
    signal=None,
):
    return build_failure_episode(
        detection_result=detection,
        signal=signal or signal_for("unresolved"),
        attribution=attribution or _l1_attribution(),
        evolution_direction=direction,
        record_key=record_key,
        allowed_modules=allowed,
        attempts=attempts,
    )


def test_single_candidate_complete_chain() -> None:
    episode = _build(
        missing_evidence_field(),
        signal=signal_for("unresolved", target_module="candidate_trigger"),
    )
    valid, reason = validate_failure_episode(episode)
    assert valid, reason
    assert episode.schema_version == SCHEMA_VERSION
    assert episode.episode_id
    assert episode.causal_slice.completeness == "complete"
    assert episode.causal_slice.candidate_id == "error_1"
    assert episode.causal_slice.candidate_kind == "error_certificate"
    assert episode.causal_slice.missing_fields == ("certificate",)
    assert episode.causal_slice.failure_edge is not None
    edge = episode.causal_slice.failure_edge
    assert edge.producer_route == "propagation_route"
    assert edge.output_object == "error_certificate"
    assert edge.consumer == "proof_court"
    assert edge.observed == "absent"
    assert edge.required == "present"
    assert episode.attribution["owner_module"] == "evidence_obligation"
    assert episode.attribution["fallback_owner_module"] == "candidate_trigger"
    assert "evidence_obligation" in episode.action_envelope.legal_modules
    assert "require_evidence_field" in episode.action_envelope.legal_operations
    assert "impact_evidence_refs" in episode.action_envelope.legal_output_fields
    assert "causal_field_coverage" in episode.action_envelope.legal_observables


def test_multi_candidate_court_specified_primary() -> None:
    detection = detection_result(
        state="unresolved",
        census=[
            {"candidate_id": "candidate_1", "origin_refs": ["event_1"]},
            {"candidate_id": "candidate_2", "origin_refs": ["event_9"]},
        ],
        adjudication={
            "state": "unresolved",
            "prosecution_coverage": "incomplete",
            "defense_coverage": "incomplete",
            "valid_error_certificate_count": 0,
            "required_candidate_count": 2,
            "positive_candidate_ids": ["candidate_2"],
        },
    )
    episode = _build(detection)
    assert episode.causal_slice.candidate_id == "candidate_2"
    assert episode.causal_slice.origin_refs == ("event_9",)
    assert episode.causal_slice.completeness == "complete"


def test_malformed_field_episode() -> None:
    detection = malformed_certificate()
    episode = _build(detection, attribution=_l1_attribution(
        artifact="atomic_verifier_instruction",
        reason="provider_artifact_malformed",
        kind="error_certificate",
        missing_fields=["evidence_refs"],
    ))
    assert episode.causal_slice.malformed_fields == ("evidence_refs",)
    assert episode.causal_slice.failure_edge is not None
    assert episode.causal_slice.failure_edge.observed == "malformed"
    assert episode.causal_slice.failure_edge.required == "well_formed"
    assert episode.causal_slice.failure_edge.field == "evidence_refs"
    valid, reason = validate_failure_episode(episode)
    assert valid, reason


def test_provider_output_unconsumed_downstream() -> None:
    detection = unconsumed_provider_output()
    episode = _build(
        detection,
        attribution=_l1_attribution(
            reason="provider_output_unconsumed",
            kind="error_certificate",
        ),
    )
    assert episode.causal_slice.court_effect == "provider_output_unconsumed"
    assert episode.causal_slice.certificate_state == "valid_error:1,valid_defense:1"
    edge = episode.causal_slice.failure_edge
    assert edge is not None
    assert edge.observed == "emitted_but_unconsumed"
    assert edge.required == "moves_court_edge"
    assert episode.causal_slice.completeness == "complete"


def test_broken_refs_produce_partial_episode() -> None:
    detection = detection_result(
        state="unresolved",
        adjudication={
            "state": "unresolved",
            "prosecution_coverage": "incomplete",
            "defense_coverage": "incomplete",
            "valid_error_certificate_count": 0,
            "required_candidate_count": 1,
        },
    )
    episode = _build(detection)
    assert episode.causal_slice.candidate_id == ""
    assert episode.causal_slice.completeness == "partial"
    valid, reason = validate_failure_episode(episode)
    assert valid, reason


def test_unaddressable_without_actionable_attribution() -> None:
    with pytest.raises(UnaddressableFailure, match="attribution_not_actionable"):
        _build(stable_clean(), attribution=FailureAttribution(
            layer="no_actionable", artifact="", reason_code="no_failure_shape",
        ))
    with pytest.raises(UnaddressableFailure, match="owner_no_legal_surface"):
        _build(
            missing_evidence_field(),
            attribution=_l1_attribution(artifact="not_a_module"),
        )


def test_episode_id_stable_and_attempt_excluded() -> None:
    first = _build(missing_evidence_field())
    second = _build(missing_evidence_field())
    assert first.episode_id == second.episode_id

    other_key = _build(missing_evidence_field(), record_key="case_other")
    assert other_key.episode_id != first.episode_id

    with_attempt = _build(
        missing_evidence_field(),
        attempts=(
            FailureAttempt(
                attempt_id="a",
                proposal_signature="s",
                operation="add_rule",
                target_module="evidence_obligation",
                action_spec={},
                structural_result="accepted",
                compiled=True,
                injected=False,
                applied=False,
                observable_delta=(),
                downstream_court_delta=(),
                no_effect_reason="",
                decision="accepted",
            ),
        ),
    )
    assert with_attempt.episode_id == first.episode_id
    valid, reason = validate_failure_episode(with_attempt)
    assert valid, reason


def test_attempt_id_is_stable_and_bound_to_episode() -> None:
    first = attempt_id_for("ep1", "sig1", 0)
    second = attempt_id_for("ep1", "sig1", 0)
    assert first == second
    assert attempt_id_for("ep1", "sig1", 1) != first
    assert attempt_id_for("ep1", "sig2", 0) != first


def test_mechanism_signature_is_identity_free() -> None:
    def signature(reason: str) -> tuple[str, ...]:
        return build_mechanism_signature(
            layer=LAYER_L1,
            artifact="evidence_obligation",
            reason_code=reason,
            evolution_direction="unresolved_to_decisive",
            candidate_kind="error_certificate",
            failure_edge_consumer="proof_court",
            missing_or_malformed_fields=("certificate",),
        )

    assert signature("provider_artifact_missing") == signature(
        "provider_artifact_missing"
    )
    assert signature("provider_artifact_missing") != signature(
        "provider_artifact_malformed"
    )


def test_mechanism_signature_matches_builder_output() -> None:
    episode = _build(missing_evidence_field())
    expected = build_mechanism_signature(
        layer=episode.attribution["layer"],
        artifact=episode.attribution["artifact"],
        reason_code=episode.attribution["reason_code"],
        evolution_direction=episode.detection["evolution_direction"],
        candidate_kind=episode.causal_slice.candidate_kind,
        failure_edge_consumer=episode.causal_slice.consumer,
        missing_or_malformed_fields=episode.causal_slice.missing_fields,
    )
    assert expected == (
        "l1_provider_rule",
        "evidence_obligation",
        "provider_artifact_missing",
        "unresolved_to_decisive",
        "error_certificate",
        "proof_court",
        "certificate",
    )


def test_prompt_projection_firewall() -> None:
    episode = _build(missing_evidence_field(), record_key="secret_case_id")
    projection = project_failure_episode_for_prompt(episode)
    serialized = json.dumps(projection, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "record_key", "secret_case_id", "gold_label", "gold_explanation",
        "is_hallucination", "attempts", "true_answer",
    ):
        assert forbidden not in serialized, forbidden
    assert projection["failure_edge"]["observed"] == "absent"
    assert projection["action_envelope"]["legal_modules"]
    valid, reason = validate_failure_episode(episode)
    assert valid, reason


def test_round_trip_preserves_episode() -> None:
    episode = _build(missing_evidence_field())
    restored = build_failure_episode.__globals__["FailureEpisode"].from_dict(
        episode.to_dict()
    )
    assert restored.to_dict() == episode.to_dict()
    valid, reason = validate_failure_episode(restored)
    assert valid, reason


def test_l2_episode_builds_with_policy_attribution() -> None:
    detection = l2_policy_movement()
    episode = build_failure_episode(
        detection_result=detection,
        signal=signal_for("unresolved"),
        attribution=FailureAttribution(
            layer="l2_adjudication_policy",
            artifact="consensus_gate_policy",
            reason_code="consensus_gate_blocked",
            evidence={"consensus_gate_blocked_count": 1},
        ),
        evolution_direction="unresolved_to_decisive",
        record_key="case_l2",
        allowed_modules=(),
    )
    assert episode.causal_slice.consumer == "adjudication_policy"
    assert episode.causal_slice.court_effect == "policy_fired_moved_terminal"
    assert episode.causal_slice.failure_edge is not None
    assert episode.causal_slice.failure_edge.producer_route == "policy_evaluator"
    valid, reason = validate_failure_episode(episode)
    assert valid, reason


def test_runner_attaches_episode_audit_and_is_deterministic(
    tmp_path, monkeypatch,
) -> None:
    from src.sehd_v6.core.bccse import evolution_runner as runner

    def detector(*, genome, case_id, **kwargs):
        if case_id == "anchor":
            return stable_clean()
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

    def run(output_dir):
        return runner.run_online_evolution(
            evolution_case_ids=("source",),
            canary_case_ids=("anchor",),
            records={"source": {}, "anchor": {}},
            config={},
            output_dir=output_dir,
            census_root=output_dir,
            mutation_route=None,
            run_id="episode_audit",
        )

    first = run(tmp_path / "first")
    second = run(tmp_path / "second")
    step = first.steps[0]
    assert step.episode is not None
    assert step.episode["episode_id"]
    assert step.episode["attribution"]["owner_module"] == "evidence_obligation"
    assert step.episode["schema_version"] == SCHEMA_VERSION
    assert second.steps[0].episode["episode_id"] == step.episode["episode_id"]
