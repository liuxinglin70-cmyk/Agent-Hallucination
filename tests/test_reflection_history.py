"""Phase 3: reflection consumes the causal episode and historical references
without adding model calls or leaking identity."""

from __future__ import annotations

import json

from src.sehd_v6.core.bccse.action_spec import compile_action_spec
from src.sehd_v6.core.bccse.evolution_experience_memory import (
    EvolutionExperienceMemory,
)
from src.sehd_v6.core.bccse.failure_episode import (
    FailureAttempt,
    build_failure_episode,
    project_failure_episode_for_prompt,
)
from src.sehd_v6.core.bccse.reflection_engine import build_causal_reflection_prompt
from src.sehd_v6.core.bccse.safety_gate import create_structural_gate
from src.sehd_v6.core.bccse.step_attributor import AttributionSignal
from src.sehd_v6.core.bccse.v6_contracts import LAYER_L1, FailureAttribution

from tests.fixtures.causal_evolution import (
    malformed_certificate,
    missing_evidence_field,
    signal_for,
)


def _episode(
    *,
    record_key: str = "case_a",
    detection=None,
    reason: str = "provider_artifact_missing",
):
    return build_failure_episode(
        detection_result=detection or missing_evidence_field(),
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
        evolution_direction="unresolved_to_decisive",
        record_key=record_key,
        allowed_modules=("evidence_obligation", "atomic_verifier_instruction"),
    )


def _attempt(**overrides: object) -> FailureAttempt:
    values = {
        "attempt_id": "a1",
        "proposal_signature": "s1",
        "operation": "add_rule",
        "target_module": "evidence_obligation",
        "action_spec": {
            "module_id": "evidence_obligation",
            "operation": "require_evidence_field",
            "output_field": "impact_evidence_refs",
        },
        "structural_result": "accepted",
        "compiled": True,
        "injected": True,
        "applied": True,
        "observable_delta": ("prosecution_coverage:increased",),
        "downstream_court_delta": ("state:unresolved->verified_error",),
        "no_effect_reason": "expected_delta_observed",
        "decision": "accept_batch",
    }
    values.update(overrides)
    return FailureAttempt(**values)


def _prompt_for(episode, refs=()):
    projection = project_failure_episode_for_prompt(episode, refs)
    signal = signal_for("unresolved")
    system, user = build_causal_reflection_prompt(
        signal,
        current_skill_text="rules",
        detection_context="trace",
        allowed_target_modules=("evidence_obligation",),
        episode_projection=projection,
        candidate_count=1,
    )
    return system, user, projection


def test_prompt_without_history_completes() -> None:
    episode = _episode(record_key="case_a")
    system, user, projection = _prompt_for(episode)
    assert "historical_references" not in user
    assert "failure_edge" in user
    assert "obligation_vs_observed" in user
    assert "action_envelope" in user
    assert system and user


def test_prompt_includes_history_refs_with_local_outcome() -> None:
    memory = EvolutionExperienceMemory()
    first = _episode(record_key="case_a")
    memory.upsert_observation(first)
    memory.upsert_attempt(first, _attempt())
    current = _episode(record_key="case_b")
    refs = memory.retrieve(episode=current, exclude_episode_id=current.episode_id)
    assert len(refs) == 1
    system, user, projection = _prompt_for(current, refs)
    assert "historical_references" in user
    assert "accept_batch" in user
    assert "expected_delta_observed" in user
    assert "impact_evidence_refs" in user
    assert "secret" not in user


def test_only_rejected_history_is_offered() -> None:
    memory = EvolutionExperienceMemory()
    first = _episode(record_key="case_a")
    memory.upsert_observation(first)
    memory.upsert_attempt(
        first,
        _attempt(
            decision="reject_fitness_no_effect",
            no_effect_reason="condition_not_matched",
            applied=False,
        ),
    )
    current = _episode(record_key="case_b")
    refs = memory.retrieve(episode=current, exclude_episode_id=current.episode_id)
    assert len(refs) == 1
    assert refs[0].local_outcome["decision"] == "reject_fitness_no_effect"
    _, user, _ = _prompt_for(current, refs)
    assert "condition_not_matched" in user


def test_partial_current_episode_projects_and_prompts() -> None:
    from tests.fixtures.causal_evolution import detection_result

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
    episode = _episode(record_key="case_partial", detection=detection)
    assert episode.causal_slice.completeness == "partial"
    _, user, projection = _prompt_for(episode)
    assert projection["completeness"] == "partial"
    assert "partial" in user


def test_action_envelope_aligns_with_candidate_schema() -> None:
    episode = _episode(record_key="case_a")
    gate = create_structural_gate()
    envelope = episode.action_envelope
    assert envelope.legal_modules
    for module in envelope.legal_modules:
        for operation in envelope.legal_operations:
            try:
                spec = compile_action_spec(module, operation)
            except (KeyError, ValueError):
                continue
            assert spec.module_id == module
            assert spec.operation == operation
    assert envelope.legal_output_fields
    assert envelope.legal_observables


def test_prompt_length_stays_bounded() -> None:
    memory = EvolutionExperienceMemory()
    first = _episode(record_key="case_a")
    memory.upsert_observation(first)
    memory.upsert_attempt(first, _attempt())
    current = _episode(record_key="case_b")
    refs = memory.retrieve(episode=current, exclude_episode_id=current.episode_id)
    _, user, _ = _prompt_for(current, refs)
    assert len(user) < 40000
    assert len(user) > 0


def test_history_never_includes_raw_identity() -> None:
    memory = EvolutionExperienceMemory()
    first = _episode(record_key="secret_case_id")
    memory.upsert_observation(first)
    memory.upsert_attempt(first, _attempt())
    current = _episode(record_key="case_b")
    refs = memory.retrieve(episode=current, exclude_episode_id=current.episode_id)
    _, user, _ = _prompt_for(current, refs)
    assert "secret_case_id" not in user
    serialized = json.dumps([reference.to_dict() for reference in refs])
    assert "secret_case_id" not in serialized


def test_runner_call_budget_unchanged_with_episode_wiring(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """Episode context and history retrieval add zero model or replay calls."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    mutation_calls = 0
    detector_calls = 0

    def detector(*, genome, case_id, **kwargs):
        nonlocal detector_calls
        detector_calls += 1
        if case_id == "anchor":
            return {
                "state": "verified_clean",
                "runtime_valid": True,
                "decision_authority": "authoritative",
                "census": [],
            }
        return missing_evidence_field()

    def mutation(**kwargs):
        nonlocal mutation_calls
        mutation_calls += 1
        return {"operation": "no_edit", "rationale": "declined"}, {}

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: signal_for(
            str(result["state"])
        ),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    result = runner.run_online_evolution(
        evolution_case_ids=("source",),
        canary_case_ids=("anchor",),
        records={"source": {}, "anchor": {}},
        config={},
        output_dir=tmp_path,
        census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        run_id="budget_unchanged",
    )
    # A no_edit primary answer escalates to one diagnosis call, exactly as
    # the baseline flow did: episode context adds no call of any kind.
    assert mutation_calls == 2
    assert detector_calls == 2  # source + canary, no extra replay
    assert result.evolution_audit["mutation_calls"] == mutation_calls
    assert result.evolution_audit["detector_calls"] == detector_calls
    assert result.evolution_audit["contrast_replays"] == 0


def test_two_case_sequential_history_reaches_prompt(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """Case B's reflection prompt contains case A's local outcome."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    prompts: list[str] = []
    order = {"a": 0}

    def detector(*, genome, case_id, **kwargs):
        if case_id == "anchor":
            return {
                "state": "verified_clean",
                "runtime_valid": True,
                "decision_authority": "authoritative",
                "census": [],
            }
        return missing_evidence_field()

    def mutation(**kwargs):
        prompts.append(kwargs.get("user") or "")
        return {"operation": "no_edit", "rationale": "declined"}, {}

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: signal_for(
            str(result["state"])
        ),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    runner.run_online_evolution(
        evolution_case_ids=("source_a", "source_b"),
        canary_case_ids=("anchor",),
        records={"source_a": {}, "source_b": {}, "anchor": {}},
        config={},
        output_dir=tmp_path,
        census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        run_id="sequential_history",
    )
    assert len(prompts) >= 2
    # Case B's first prompt carries the prior observation context; no
    # contrast block and no case identity anywhere.
    for prompt in prompts:
        assert "<contrast_cases>" not in prompt
        assert "source_a" not in prompt
    assert any("provider_artifact_missing" in prompt for prompt in prompts)
