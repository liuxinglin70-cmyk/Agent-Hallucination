"""Phase 4: closed execution feedback — FailureAttempt outcomes, cross-case
no-effect dedup, sequential history visibility and interrupted-resume
normalization."""

from __future__ import annotations

import json

import pytest

from src.sehd_v6.core.bccse.evolution_experience_memory import (
    EvolutionExperienceMemory,
)

from tests.fixtures.causal_evolution import (
    edit_payload,
    missing_evidence_field,
    signal_for,
    stable_clean,
)


def _result(state: str) -> dict:
    adjudication = {
        "state": state,
        "prosecution_coverage": (
            "incomplete" if state == "unresolved" else "complete"
        ),
        "defense_coverage": (
            "incomplete" if state == "unresolved" else "complete"
        ),
        "valid_error_certificate_count": 0,
        "valid_defense_certificate_count": (
            1 if state == "verified_clean" else 0
        ),
        "required_candidate_count": 1,
        "surviving_error_certificate_ids": [],
        "clean_covered_candidate_ids": (
            ["candidate_1"] if state == "verified_clean" else []
        ),
    }
    return {
        "state": state,
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "census": [{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        "proof_adjudication": adjudication,
        "evidence_health": {},
        "combined_prosecution": {"candidates": []},
        "combined_defense": {"candidates": []},
        "boundary_path": {"state": ""},
    }


def _detector_for(a_fixed_by_edit: bool = True, b_fixed_by_edit: bool = False):
    def detector(*, genome, case_id, **kwargs):
        if case_id == "anchor":
            return stable_clean()
        if case_id == "source_a":
            state = "verified_clean" if genome.generation > 0 else "unresolved"
        else:
            state = (
                "verified_clean"
                if b_fixed_by_edit and genome.generation > 0
                else "unresolved"
            )
        return _result(state)

    return detector


def _run(tmp_path, monkeypatch, *, detector, mutation, case_ids, run_id):
    from src.sehd_v6.core.bccse import evolution_runner as runner

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner,
        "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: signal_for(
            str(result["state"])
        ),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)
    return runner.run_online_evolution(
        evolution_case_ids=case_ids,
        canary_case_ids=("anchor",),
        records={case_id: {} for case_id in (*case_ids, "anchor")},
        config={},
        output_dir=tmp_path,
        census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        run_id=run_id,
    )


def test_attempt_carries_compiled_injected_applied_evidence(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """An accepted local movement is recorded with full execution evidence."""
    prompts: list[str] = []

    def mutation(**kwargs):
        prompts.append(kwargs.get("user") or "")
        return edit_payload(), {}

    result = _run(
        tmp_path, monkeypatch,
        detector=_detector_for(a_fixed_by_edit=True),
        mutation=mutation,
        case_ids=("source_a",),
        run_id="attempt_evidence",
    )
    assert result.edits_accepted == 1
    step = result.steps[0]
    assert step.gate_decision == "accept_batch"
    assert step.episode is not None
    attempts = step.episode["attempts"]
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["decision"] == "accept_batch"
    assert attempt["compiled"] is True
    assert attempt["target_module"] == "evidence_obligation"
    assert attempt["operation"] == "add_rule"
    assert attempt["action_spec"]["operation"] == "require_evidence_field"
    assert attempt["attempt_id"]


def test_two_case_sequential_history_shows_a_outcome_in_b_prompt(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """Case A's accepted local outcome is visible in case B's reflection
    input; the framework then compiles and replays B's proposal."""
    prompts: list[str] = []
    calls = {"b": 0}

    def mutation(**kwargs):
        prompts.append(kwargs.get("user") or "")
        case = "b" if "historical_references" in kwargs.get("user", "") else "a"
        calls[case] = calls.get(case, 0) + 1
        return edit_payload(), {}

    result = _run(
        tmp_path, monkeypatch,
        detector=_detector_for(a_fixed_by_edit=True),
        mutation=mutation,
        case_ids=("source_a", "source_b"),
        run_id="sequential_a_b",
    )
    assert result.edits_accepted >= 1
    assert calls["a"] >= 1
    assert calls["b"] >= 1
    b_prompts = [
        prompt for prompt in prompts if "historical_references" in prompt
    ]
    assert b_prompts
    assert "accept_batch" in b_prompts[0]
    assert "expected_delta_observed" in b_prompts[0]

    memory_payload = json.loads(
        (tmp_path / "htc_tes_experience_memory.json").read_text(
            encoding="utf-8"
        )
    )
    entry = memory_payload["failure_mechanisms"][0]
    assert entry["observation_count"] == 2
    assert entry["representatives"]["effective"] is not None


def test_cross_case_no_effect_proposal_is_not_replayed(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """A proposal confirmed no-effect on case A is not replayed verbatim on
    the same-mechanism case B; reflection regenerates a distinct proposal."""
    detector_calls = 0
    mutation_calls = 0

    def detector(*, genome, case_id, **kwargs):
        nonlocal detector_calls
        detector_calls += 1
        if case_id == "anchor":
            return stable_clean()
        return _result("unresolved")

    def mutation(**kwargs):
        nonlocal mutation_calls
        mutation_calls += 1
        if mutation_calls in (1, 2, 3):
            return edit_payload(), {}
        return edit_payload(
            condition="a certificate omits a cited support event",
        ), {}

    result = _run(
        tmp_path, monkeypatch,
        detector=detector,
        mutation=mutation,
        case_ids=("source_a", "source_b"),
        run_id="cross_case_dedup",
    )

    assert [step.gate_decision for step in result.steps] == [
        "retry_fitness_no_effect",   # A attempt 0: proposal X confirmed no-effect
        "skip_invalid_mutation",     # A attempt 1: same-case repeat of X
        "retry_invalid_mutation",    # B attempt 0: cross-case repeat of X blocked
        "reject_fitness_no_effect",  # B attempt 1: distinct proposal still no-effect
    ]
    a_step = result.steps[0]
    assert a_step.episode["attempts"][0]["no_effect_reason"] in {
        "condition_not_matched",
        "provider_did_not_apply_action",
        "unresolved_no_effect",
    }
    b_step = result.steps[2]
    assert "cross_case_no_effect_duplicate" in b_step.fitness_check[
        "mutation_error"
    ]
    assert b_step.episode["attempts"][0]["decision"] == "retry_invalid_mutation"

    # The duplicate proposal never reached a source replay: anchor + A
    # source (2 attempts) + A replay + B source (2 attempts) + B replay.
    assert detector_calls == 7
    assert mutation_calls == 4  # A: X, X; B: X, Y


def test_resume_after_observation_does_not_double_count(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """Breakpoint: observation persisted, reflection never called."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    interrupt = True
    prompt_calls = 0
    real_prompt = runner.build_causal_reflection_prompt

    def prompt_with_interrupt(*args, **kwargs):
        nonlocal prompt_calls
        if interrupt:
            raise KeyboardInterrupt("simulated interruption before reflection")
        prompt_calls += 1
        return real_prompt(*args, **kwargs)

    def mutation(**kwargs):
        return edit_payload(), {}

    monkeypatch.setattr(
        runner, "_run_detection_with_genome",
        _detector_for(a_fixed_by_edit=True),
    )
    monkeypatch.setattr(
        runner,
        "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: signal_for(
            str(result["state"])
        ),
    )
    monkeypatch.setattr(
        runner, "json_object_provider_call", mutation,
    )
    monkeypatch.setattr(
        runner, "build_causal_reflection_prompt", prompt_with_interrupt,
    )

    output_dir = tmp_path / "breakpoint_observation"
    case_ids = ("source_a",)
    records = {case_id: {} for case_id in (*case_ids, "anchor")}
    with pytest.raises(KeyboardInterrupt):
        runner.run_online_evolution(
            evolution_case_ids=case_ids,
            canary_case_ids=("anchor",),
            records=records,
            config={},
            output_dir=output_dir,
            census_root=tmp_path,
            mutation_route={"model": "DeepSeek-V4-Flash"},
            run_id="interrupted_observation",
        )

    interrupt = False
    resumed = runner.run_online_evolution(
        evolution_case_ids=case_ids,
        canary_case_ids=("anchor",),
        records=records,
        config={},
        output_dir=output_dir,
        census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        resume_from=output_dir,
        run_id="resumed_observation",
    )
    assert resumed.edits_accepted == 1
    assert [step.gate_decision for step in resumed.steps] == ["accept_batch"]
    memory_payload = json.loads(
        (output_dir / "htc_tes_experience_memory.json").read_text(
            encoding="utf-8"
        )
    )
    entry = memory_payload["failure_mechanisms"][0]
    assert entry["observation_count"] == 1
    assert len(entry["counted_episode_ids"]) == 1
    assert entry["representatives"]["effective"] is not None


def test_resume_matches_uninterrupted_run(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """Interruption at different breakpoints yields the same normalized
    memory, steps and counters as an uninterrupted run."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    def mutation(**kwargs):
        return edit_payload(), {}

    case_ids = ("source_a", "source_b")
    records = {case_id: {} for case_id in (*case_ids, "anchor")}

    def uninterrupted(output_dir):
        return runner.run_online_evolution(
            evolution_case_ids=case_ids,
            canary_case_ids=("anchor",),
            records=records,
            config={},
            output_dir=output_dir,
            census_root=tmp_path,
            mutation_route={"model": "DeepSeek-V4-Flash"},
            run_id="uninterrupted",
        )

    baseline = _run(
        tmp_path / "plain", monkeypatch,
        detector=_detector_for(a_fixed_by_edit=True),
        mutation=mutation,
        case_ids=case_ids,
        run_id="uninterrupted",
    )
    baseline_memory = json.loads(
        (tmp_path / "plain" / "htc_tes_experience_memory.json").read_text(
            encoding="utf-8"
        )
    )
    baseline_steps = [step.gate_decision for step in baseline.steps]

    interrupt = True
    real_prompt = runner.build_causal_reflection_prompt

    def prompt_with_interrupt(*args, **kwargs):
        nonlocal interrupt
        if interrupt:
            interrupt = False
            raise KeyboardInterrupt("simulated interruption at case B reflection")
        return real_prompt(*args, **kwargs)

    monkeypatch.setattr(
        runner, "_run_detection_with_genome",
        _detector_for(a_fixed_by_edit=True),
    )
    monkeypatch.setattr(
        runner,
        "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: signal_for(
            str(result["state"])
        ),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)
    monkeypatch.setattr(
        runner, "build_causal_reflection_prompt", prompt_with_interrupt,
    )

    output_dir = tmp_path / "interrupted_multi"
    with pytest.raises(KeyboardInterrupt):
        runner.run_online_evolution(
            evolution_case_ids=case_ids,
            canary_case_ids=("anchor",),
            records=records,
            config={},
            output_dir=output_dir,
            census_root=tmp_path,
            mutation_route={"model": "DeepSeek-V4-Flash"},
            run_id="interrupted_multi",
        )
    monkeypatch.setattr(runner, "build_causal_reflection_prompt", real_prompt)
    resumed = runner.run_online_evolution(
        evolution_case_ids=case_ids,
        canary_case_ids=("anchor",),
        records=records,
        config={},
        output_dir=output_dir,
        census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        resume_from=output_dir,
        run_id="resumed_multi",
    )

    assert [step.gate_decision for step in resumed.steps] == baseline_steps
    resumed_memory = json.loads(
        (output_dir / "htc_tes_experience_memory.json").read_text(
            encoding="utf-8"
        )
    )
    assert resumed_memory["schema_version"] == "sehd.evolution_experience_memory.v1"
    assert resumed_memory["failure_mechanisms"] == baseline_memory[
        "failure_mechanisms"
    ]


def test_type_memory_does_not_copy_episodes(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """The type-conditioned statistics stay summaries; only the mechanism
    index carries structured causal evidence."""
    result = _run(
        tmp_path, monkeypatch,
        detector=_detector_for(a_fixed_by_edit=True),
        mutation=lambda **kwargs: (edit_payload(), {}),
        case_ids=("source_a",),
        run_id="type_no_episode",
    )
    payload = json.loads(
        (tmp_path / "htc_tes_experience_memory.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    type_entries = [
        entry
        for rows in payload["type_experience_memory"]["types"].values()
        for entry in rows
    ]
    assert all("failure_edge" not in json.dumps(entry) for entry in type_entries)
    assert "causal_slice" in serialized  # lives only in the mechanism index
