from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from src.sehd_v6.core.bccse.genome import create_baseline_genome
from src.sehd_v6.core.bccse.step_attributor import (
    AttributionSignal,
    ErrorType,
    ReflectionType,
    TriggerSource,
)
from src.sehd_v6.core.paca_tcl.detector import REQUIRED_ROUTES
from src.sehd_v6.evolution.runner import load_records, run_evolution


def _signal(state: str) -> AttributionSignal:
    stable = state == "verified_clean"
    return AttributionSignal(
        error_type=(
            ErrorType.CORRECT if stable else ErrorType.EVIDENCE_INCOMPLETE
        ),
        target_module="evidence_obligation",
        secondary_module="atomic_verifier_instruction",
        attribution_confidence="medium",
        reflection_type=(
            ReflectionType.STABLE_SUCCESS if stable else ReflectionType.OPTIMIZATION
        ),
        trigger_source=(
            TriggerSource.NONE if stable else TriggerSource.EVIDENCE_INCOMPLETE
        ),
        evidence_summary="mock evidence process",
        prosecution_coverage=1.0,
        defense_coverage=1.0,
        prosecution_coverage_state="complete",
        defense_coverage_state="complete",
        coverage_quantitative=False,
        crs_scores={},
        crs_aggregate=0.0,
        crs_source="mock",
        crs_complete=True,
        consensus_gate_applied=False,
        candidate_count=1,
        state=state,
        gold_label=None,
        has_gold=False,
        gold_enhanced=False,
        responsible_step_correct=False,
    )


def _detector(*, genome, case_id, **kwargs):
    state = (
        "verified_clean"
        if case_id == "stable" or genome.generation > 0
        else "unresolved"
    )
    return {
        "state": state,
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "census": [],
    }


def _edit_payload() -> dict:
    return {
        "target_module": "evidence_obligation",
        "operation": "add_rule",
        "rule_index": None,
        "condition": "a factual claim lacks a cited trajectory event",
        "action": "require_evidence_field: impact_evidence_refs",
        "evidence_anchor": "uncited factual claim accepted without trajectory evidence",
        "rationale": "Require observable support.",
        "feature_description": "factual claim without cited trajectory evidence",
        "expected_effect": "prosecution evidence coverage increases",
        "expected_effect_contract": {
            "observable_field": "prosecution_coverage",
            "expected_direction": "increase",
            "expected_scope": "all_cases",
            "trigger_condition": "prosecution evidence is incomplete",
        },
    }


def _write_config(tmp_path) -> object:
    config = {name: {} for name in REQUIRED_ROUTES}
    path = tmp_path / "detector_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _write_records(tmp_path, *, jsonl: bool) -> object:
    stable = {"case_id": "stable", "history": [{"role": "assistant", "content": "ok"}]}
    source = {"case_id": "source", "history": [{"role": "assistant", "content": "bad"}]}
    if jsonl:
        path = tmp_path / "records.jsonl"
        path.write_text(
            json.dumps(stable) + "\n" + json.dumps(source) + "\n",
            encoding="utf-8",
        )
    else:
        path = tmp_path / "records.json"
        path.write_text(
            json.dumps({"stable": stable, "source": source}),
            encoding="utf-8",
        )
    return path


@pytest.fixture
def offline_evolution(monkeypatch, tmp_path, addressable_l1_failure):
    from src.sehd_v6.core.bccse import evolution_runner as runner

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-placeholder")

    @contextmanager
    def provider_context(*, genome, config):
        yield None

    monkeypatch.setattr(runner, "_run_detection_with_genome", _detector)
    monkeypatch.setattr(runner, "integrated_v35_provider", provider_context)
    monkeypatch.setattr(
        runner,
        "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: _signal(str(result["state"])),
    )
    monkeypatch.setattr(
        runner,
        "json_object_provider_call",
        lambda **kwargs: (_edit_payload(), {}),
    )
    return runner


def test_load_records_json_mapping(tmp_path) -> None:
    path = _write_records(tmp_path, jsonl=False)
    records = load_records(path)
    assert set(records) == {"stable", "source"}
    assert records["stable"]["history"][0]["content"] == "ok"


def test_load_records_jsonl(tmp_path) -> None:
    path = _write_records(tmp_path, jsonl=True)
    records = load_records(path)
    assert set(records) == {"stable", "source"}


def test_standalone_evolution_loop_runs_offline_jsonl(
    tmp_path, offline_evolution,
) -> None:
    records_path = _write_records(tmp_path, jsonl=True)
    config_path = _write_config(tmp_path)
    output_dir = tmp_path / "evolution_out"

    result = run_evolution(
        records=records_path,
        detector_config=config_path,
        output_dir=output_dir,
        run_id="standalone_loop",
    )

    assert result.total_cases == 2
    assert result.edits_accepted >= 0
    assert result.champion_genome.generation >= 0
    assert (output_dir / "evolution_result.json").is_file()
    assert (output_dir / "champion_genome.json").is_file()
    assert (output_dir / "htc_tes_experience_memory.json").is_file()


def test_standalone_evolution_accepts_mapping_input(tmp_path, offline_evolution) -> None:
    config_path = _write_config(tmp_path)
    records = {
        "stable": {"history": [{"role": "assistant", "content": "ok"}]},
        "source": {"history": [{"role": "assistant", "content": "bad"}]},
    }

    result = run_evolution(
        records=records,
        detector_config=config_path,
        output_dir=tmp_path / "out2",
    )

    assert result.total_cases == 2
    assert (tmp_path / "out2" / "champion_genome.json").is_file()


def test_standalone_evolution_without_credential_skips_mutation_provider(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    from src.sehd_v6.core.bccse import evolution_runner as runner

    calls = 0

    def detector(*, genome, case_id, **kwargs):
        return {
            "state": "unresolved",
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "census": [],
        }

    def mutation(**kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("mutation provider must not be called")

    for key in (
        "SEHD_MUTATION_API_KEY_ENV",
        "SEHD_PROVIDER_API_KEY_ENV",
        "SEHD_API_KEY_ENV",
        "DEEPSEEK_API_KEY",
        "SEHD_TEST_MISSING_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(
        "SEHD_ROUTE_MUTATION_API_KEY_ENV", "SEHD_TEST_MISSING_KEY"
    )
    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner,
        "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: _signal(
            str(result["state"])
        ),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    result = run_evolution(
        records={"source": {}},
        detector_config=_write_config(tmp_path),
        output_dir=tmp_path / "no_credential",
        mutation_route={
            "model": "DeepSeek-V4-Flash",
            "api_key_environment": "SEHD_TEST_MISSING_KEY",
        },
        run_id="no_credential",
    )

    assert calls == 0
    assert result.steps[0].gate_decision == "skip_no_edit"
    assert result.evolution_audit["mutation_calls"] == 0


def test_standalone_evolution_rejects_empty_records(tmp_path) -> None:
    config_path = _write_config(tmp_path)
    with pytest.raises(ValueError, match="must not be empty"):
        run_evolution(
            records={},
            detector_config=config_path,
            output_dir=tmp_path / "out3",
        )


def test_standalone_evolution_accepts_gold_enhanced_records(
    tmp_path, offline_evolution,
) -> None:
    records = {
        "stable": {
            "gold_label": False,
            "history": [{"role": "assistant", "content": "ok"}],
        },
        "source": {
            "gold_label": True,
            "history": [{"role": "assistant", "content": "bad"}],
        },
    }
    result = run_evolution(
        records=records,
        detector_config=_write_config(tmp_path),
        output_dir=tmp_path / "out4",
        use_gold=True,
        fitness_use_gold=True,
    )
    assert result.use_gold is True
    assert result.total_cases == 2


def test_closed_loop_counter_invariants_hold(tmp_path, offline_evolution) -> None:
    """Counter invariants must hold across a full closed-loop run.

    - processed = triggered + stable + runtime_invalid
      (i.e. total_cases == edits_accepted + edits_rejected + stable
       + runtime_invalid, since every case either reaches the gate as
       accept/reject or is skipped as stable/runtime_invalid)
    - gate_summary accepted/rejected mirror the result counters
    - accepted + rejected == total gate evaluations
    - all three product artifacts are written
    """
    records_path = _write_records(tmp_path, jsonl=True)
    config_path = _write_config(tmp_path)
    output_dir = tmp_path / "counter_out"

    result = run_evolution(
        records=records_path,
        detector_config=config_path,
        output_dir=output_dir,
        run_id="counter_invariants",
    )

    # Every case is either a gate accept/reject or a no-edit skip
    # (stable + runtime_invalid). triggered cases are a subset that
    # reached the gate, so accepted+rejected covers the gate side.
    no_edit_skips = result.stable_cases + result.runtime_invalid_cases
    assert (
        result.edits_accepted + result.edits_rejected + no_edit_skips
        == result.total_cases
    ), {
        "accepted": result.edits_accepted,
        "rejected": result.edits_rejected,
        "stable": result.stable_cases,
        "runtime_invalid": result.runtime_invalid_cases,
        "total": result.total_cases,
    }

    gate = result.gate_summary
    assert gate["accepted"] == result.edits_accepted
    assert gate["rejected"] == result.edits_rejected
    assert gate["accepted"] + gate["rejected"] == gate["total_evaluations"]

    for name in (
        "evolution_result.json",
        "champion_genome.json",
        "htc_tes_experience_memory.json",
    ):
        assert (output_dir / name).is_file(), name


def test_online_evolution_passes_committed_genome_to_next_case(
    tmp_path, offline_evolution,
) -> None:
    from src.sehd_v6.core.bccse import evolution_runner as runner

    observed: list[tuple[str, int]] = []

    def detector(*, genome, case_id, **kwargs):
        observed.append((case_id, genome.generation))
        return {
            "state": (
                "verified_clean"
                if case_id == "stable" or genome.generation > 0
                else "unresolved"
            ),
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "census": [],
        }

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
        result = runner.run_online_evolution(
            evolution_case_ids=("stable", "source", "after"),
            records={"stable": {}, "source": {}, "after": {}},
            config={}, output_dir=tmp_path, census_root=tmp_path,
            mutation_route={"model": "DeepSeek-V4-Flash"},
            run_id="sequential_genome_inheritance",
        )
    finally:
        monkeypatch.undo()

    assert result.edits_accepted == 1
    # The source replay sees generation 1, and case after starts with it too.
    assert observed[-1] == ("after", 1)


def test_online_evolution_runs_independent_candidates_and_critic(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    from src.sehd_v6.core.bccse import evolution_runner as runner

    calls: list[tuple[str, str]] = []
    responses = [
        {
            "target_module": "evidence_obligation", "operation": "add_rule",
            "condition": "a certificate cites no trajectory event",
            "action": "require_evidence_field: impact_evidence_refs",
            "evidence_anchor": "certificate without cited event",
            "feature_description": "missing cited support",
        },
        {
            "target_module": "evidence_obligation", "operation": "add_rule",
            "condition": "a certificate event is absent",
            "action": "keep_coverage_incomplete: coverage_state",
            "evidence_anchor": "absent evidence event",
            "feature_description": "absent evidence event",
        },
        {
            "target_module": "evidence_obligation", "operation": "add_rule",
            "condition": "a certificate cites no trajectory event",
            "action": "require_evidence_field: impact_evidence_refs",
            "evidence_anchor": "duplicate sample",
            "feature_description": "duplicate sample",
        },
        {"decision": "select", "selected_index": 1, "reason": "distinct owner-local effect"},
    ]

    def detector(*, genome, case_id, **kwargs):
        state = "verified_clean" if case_id == "anchor" or genome.generation else "unresolved"
        return {
            "state": state, "runtime_valid": True,
            "decision_authority": "authoritative", "census": [],
        }

    def mutation(**kwargs):
        calls.append((kwargs["purpose"], kwargs["user"]))
        response = responses.pop(0)
        if response.get("operation") == "add_rule":
            response.update({
                "expected_effect": "prosecution evidence coverage increases",
                "expected_effect_contract": {
                    "observable_field": "prosecution_coverage",
                    "expected_direction": "increase",
                    "expected_scope": "all_cases",
                    "trigger_condition": "prosecution evidence is incomplete",
                },
            })
        return response, {"cache_hit": False}

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: _signal(str(result["state"])),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    result = runner.run_online_evolution(
        evolution_case_ids=("source",),
        records={"source": {}, "anchor": {}},
        config={}, output_dir=tmp_path, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        canary_case_ids=("anchor",),
        reflection_candidate_count=3,
        run_id="independent_reflection",
    )

    assert result.edits_accepted == 1
    assert [purpose for purpose, _ in calls] == [
        "mutation", "mutation", "mutation", "reflection_critic",
    ]
    assert len({user for _, user in calls[:3]}) == 3
    reflection = result.steps[-1].fitness_check["reflection"]
    # generated_candidates counts the raw generation attempts; the third
    # sample was deduplicated against the first before the critic.
    assert reflection["generated_candidates"] == 3
    assert reflection["prevalidated_candidates"] == 2
    assert reflection["critic"]["selected_index"] == 1


def test_online_evolution_diagnosis_unlocks_no_edit_deadlock(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """v5.9: when every primary candidate retreats to no_edit, the diagnosis
    stage locates an executable owner and the targeted generation stage is
    bound to implement it; the edit still passes the normal gates."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    calls: list[tuple[str, str]] = []
    responses = [
        {"operation": "no_edit", "rationale": "primary pass 1 declined"},
        {"operation": "no_edit", "rationale": "primary pass 2 declined"},
        {
            "owner_executable": True,
            "owner_module": "evidence_obligation",
            "failure_field": "prosecution_coverage",
            "observed_gap": "cited evidence fields never populated",
            "intervention_hint": {
                "condition": "a claim lacks cited event support",
                "action": "keep_coverage_incomplete: coverage_state",
            },
            "reasoning": "evidence construction owns the gap",
        },
        {
            "target_module": "evidence_obligation", "operation": "add_rule",
            "condition": "a claim lacks cited event support",
            "action": "keep_coverage_incomplete: coverage_state",
            "evidence_anchor": "cited evidence fields never populated",
            "feature_description": "uncited claim keeps evidence incomplete",
        },
    ]

    def detector(*, genome, case_id, **kwargs):
        state = "verified_clean" if case_id == "anchor" or genome.generation else "unresolved"
        return {
            "state": state, "runtime_valid": True,
            "decision_authority": "authoritative", "census": [],
        }

    def mutation(**kwargs):
        calls.append((kwargs["purpose"], kwargs["user"]))
        response = responses.pop(0)
        if response.get("operation") == "add_rule":
            response.update({
                "expected_effect": "prosecution evidence coverage increases",
                "expected_effect_contract": {
                    "observable_field": "prosecution_coverage",
                    "expected_direction": "increase",
                    "expected_scope": "all_cases",
                    "trigger_condition": "prosecution evidence is incomplete",
                },
            })
        return response, {"cache_hit": False}

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: _signal(str(result["state"])),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    result = runner.run_online_evolution(
        evolution_case_ids=("source",),
        records={"source": {}, "anchor": {}},
        config={}, output_dir=tmp_path, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        canary_case_ids=("anchor",),
        reflection_candidate_count=2,
        run_id="diagnosis_unlock",
    )

    assert [purpose for purpose, _ in calls] == [
        "mutation", "mutation", "reflection_diagnosis", "mutation",
    ]
    assert "<targeted_diagnosis" in calls[-1][1]
    reflection = result.steps[-1].fitness_check["reflection"]
    assert reflection["diagnosis"]["owner_module"] == "evidence_obligation"
    assert reflection.get("targeted_retry") is True
    assert result.edits_accepted == 1


def test_online_evolution_diagnosis_downgrades_illegal_owner(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """A diagnosis proposing an owner outside the allowed set is downgraded
    to non-executable and the step stays a fail-closed no_edit."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    calls: list[str] = []
    responses = [
        {"operation": "no_edit", "rationale": "declined"},
        {
            "owner_executable": True,
            "owner_module": "earliest_origin_tiebreak",
            "failure_field": "state",
            "observed_gap": "gap",
            "reasoning": "illegal owner",
        },
    ]

    def detector(*, genome, case_id, **kwargs):
        state = "verified_clean" if case_id == "anchor" else "unresolved"
        return {
            "state": state, "runtime_valid": True,
            "decision_authority": "authoritative", "census": [],
        }

    def mutation(**kwargs):
        calls.append(kwargs["purpose"])
        return responses.pop(0), {"cache_hit": False}

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: _signal(str(result["state"])),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    result = runner.run_online_evolution(
        evolution_case_ids=("source",),
        records={"source": {}, "anchor": {}},
        config={}, output_dir=tmp_path, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        canary_case_ids=("anchor",),
        reflection_candidate_count=1,
        run_id="diagnosis_downgrade",
    )

    assert calls == ["mutation", "reflection_diagnosis"]
    reflection = result.steps[-1].edit_proposal["reflection"]
    assert reflection["diagnosis"]["owner_executable"] is False
    assert result.edits_accepted == 0


def test_stable_cases_do_not_advance_convergence_streak(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """v5.9b: convergence is defined over triggered cases only. Stable
    (untriggered) cases and runtime-invalid detections carry no information
    about reflection productivity and must not consume the no-edit window;
    a mixed stream must not halt the queue prematurely."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    responses = iter([
        {"operation": "no_edit", "rationale": "declined"},
        {"operation": "no_edit", "rationale": "declined"},
        {"operation": "no_edit", "rationale": "declined"},
    ])

    def detector(*, genome, case_id, **kwargs):
        state = "verified_clean" if case_id == "anchor" else "unresolved"
        return {
            "state": state, "runtime_valid": True,
            "decision_authority": "authoritative", "census": [],
        }

    def mutation(**kwargs):
        if kwargs.get("purpose") == "reflection_diagnosis":
            # No executable owner: the diagnosis stage must not consume the
            # no-edit window either way; the step stays a plain no_edit.
            return {
                "owner_executable": False, "owner_module": None,
                "failure_field": "", "observed_gap": "",
                "reasoning": "nothing executable",
            }, {"cache_hit": False}
        return next(responses), {"cache_hit": False}

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: _signal(str(result["state"])),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    result = runner.run_online_evolution(
        evolution_case_ids=("source1", "anchor", "source2", "source3"),
        records={"source1": {}, "anchor": {}, "source2": {}, "source3": {}},
        config={}, output_dir=tmp_path, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        reflection_candidate_count=1,
        convergence_window=3,
        run_id="convergence_neutral_stable",
    )

    # Three triggered no-edits with a stable case in between: the stable case
    # must not count, so the streak is 3 only after the last triggered case.
    assert result.gate_summary["consecutive_no_edit"] == 3
    assert result.gate_summary["convergence_signal"] is True
    # Every case was processed; none was skipped by a premature halt.
    decisions = [step.gate_decision for step in result.steps]
    assert "halted" not in decisions
    assert decisions.count("skip_no_edit") == 3
    assert decisions.count("skip_stable") == 1


def test_condition_overload_is_rejected_and_fed_back_for_rewrite(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """A multi-conjunction rule is rejected as condition_overload instead of
    being mechanically simplified: deleting conjuncts would change the rule's
    semantics, so the rejection is fed back and the model must rewrite it."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    def detector(*, genome, case_id, **kwargs):
        state = "verified_clean" if case_id == "anchor" or genome.generation else "unresolved"
        return {
            "state": state, "runtime_valid": True,
            "decision_authority": "authoritative", "census": [],
        }

    def mutation(**kwargs):
        if kwargs.get("purpose") == "reflection_diagnosis":
            return {
                "owner_executable": False, "owner_module": None,
                "failure_field": "", "observed_gap": "",
                "reasoning": "nothing executable",
            }, {"cache_hit": False}
        return {
            "target_module": "evidence_obligation", "operation": "add_rule",
            "condition": (
                "the adjudication state is unresolved and quarantined error "
                "certificates exist and defense coverage is complete"
            ),
            "action": "require_evidence_field: impact_evidence_refs",
            "evidence_anchor": "multi-conjunction proposal",
            "feature_description": "multi-conjunction proposal",
            "expected_effect": "prosecution evidence coverage increases",
            "expected_effect_contract": {
                "observable_field": "prosecution_coverage",
                "expected_direction": "increase",
                "expected_scope": "all_cases",
                "trigger_condition": "prosecution evidence is incomplete",
            },
        }, {"cache_hit": False}

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: _signal(str(result["state"])),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    result = runner.run_online_evolution(
        evolution_case_ids=("source",),
        records={"source": {}, "anchor": {}},
        config={}, output_dir=tmp_path, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        canary_case_ids=("anchor",),
        reflection_candidate_count=1,
        run_id="condition_overload_reject",
    )

    # Both attempts are rejected at the slot gate: no mechanical
    # simplification, no accepted edit.
    assert result.edits_accepted == 0
    rejected_steps = [
        step for step in result.steps
        if step.gate_decision in ("retry_invalid_mutation", "skip_invalid_mutation")
    ]
    assert len(rejected_steps) == 2
    assert all(
        str(step.fitness_check.get("mutation_error") or "").startswith(
            "rule_spec:condition_overload"
        )
        for step in rejected_steps
    )
    # The first attempt schedules a bounded retry with structured feedback
    # so the next call can rewrite the condition instead of losing it.
    assert rejected_steps[0].fitness_check.get("retry_scheduled") is True
    assert rejected_steps[0].fitness_check.get("retry_failure_category")
    assert rejected_steps[1].fitness_check.get("retry_scheduled") is False


def test_resume_rewinds_halted_placeholder_steps(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """v5.9b: cases skipped by convergence were persisted as halted
    placeholders with the queue advanced past them, so a resume dropped them
    forever. Resume must rewind trailing placeholder steps and re-examine
    those cases."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    def detector(*, genome, case_id, **kwargs):
        return {
            "state": "unresolved", "runtime_valid": True,
            "decision_authority": "authoritative", "census": [],
        }

    def mutation(**kwargs):
        if kwargs.get("purpose") == "reflection_diagnosis":
            return {
                "owner_executable": False, "owner_module": None,
                "failure_field": "", "observed_gap": "",
                "reasoning": "nothing executable",
            }, {"cache_hit": False}
        return {"operation": "no_edit", "rationale": "declined"}, {
            "cache_hit": False,
        }

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: _signal(str(result["state"])),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    output_dir = tmp_path / "converged_run"
    records = {"s1": {}, "s2": {}, "s3": {}}
    first = runner.run_online_evolution(
        evolution_case_ids=("s1", "s2", "s3"),
        records=records,
        config={}, output_dir=output_dir, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        reflection_candidate_count=1,
        convergence_window=2,
        run_id="resume_rewind_first",
    )
    first_decisions = [step.gate_decision for step in first.steps]
    assert first_decisions.count("skip_no_edit") == 2
    assert first_decisions.count("halted") == 1

    resumed = runner.run_online_evolution(
        evolution_case_ids=("s1", "s2", "s3"),
        records=records,
        config={}, output_dir=output_dir, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        reflection_candidate_count=1,
        convergence_window=2,
        resume_from=output_dir,
        run_id="resume_rewind_second",
    )
    resumed_decisions = [step.gate_decision for step in resumed.steps]
    # The placeholder was rewound and s3 was genuinely re-examined.
    assert "halted" not in resumed_decisions
    assert resumed_decisions.count("skip_no_edit") == 3
    assert len(resumed.steps) == 3


def test_mutation_budget_exhausted_short_circuits_reflection(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """Once the mutation call budget is exhausted, a triggered case must
    short-circuit deterministically before spending the reflection stack:
    no further mutation call happens and the step records the budget skip
    instead of cycling through fail-closed call sites and retries."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    mutation_calls: list[str] = []

    def detector(*, genome, case_id, **kwargs):
        state = "verified_clean" if case_id == "anchor" else "unresolved"
        return {
            "state": state, "runtime_valid": True,
            "decision_authority": "authoritative", "census": [],
        }

    def mutation(**kwargs):
        mutation_calls.append(kwargs.get("purpose"))
        return {"operation": "no_edit", "rationale": "declined"}, {
            "cache_hit": False,
        }

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: _signal(str(result["state"])),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    result = runner.run_online_evolution(
        evolution_case_ids=("s1", "s2"),
        records={"s1": {}, "s2": {}, "anchor": {}},
        config={}, output_dir=tmp_path, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        canary_case_ids=("anchor",),
        reflection_candidate_count=1,
        max_mutation_calls=1,
        run_id="budget_short_circuit",
    )

    decisions = [step.gate_decision for step in result.steps]
    assert decisions == ["skip_no_edit", "skip_mutation_budget_exhausted"]
    # Exactly one reflection call consumed the budget; the second triggered
    # case never entered the reflection stack.
    assert mutation_calls == ["mutation"]
    assert result.evolution_audit["mutation_calls"] == 1


def test_evolution_carries_initial_policy_genome_into_detector(
    tmp_path, monkeypatch,
) -> None:
    """A supplied L2 policy genome seeds the working policy set and reaches
    every detector call: this is the inheritance path that carries L2 skills
    into a fresh run without a checkpoint."""
    from src.sehd_v6.core.bccse import evolution_runner as runner
    from src.sehd_v6.core.bccse.policy_genome import (
        PolicyEdit,
        PolicyItem,
        PolicyOp,
        apply_policy_edit,
        create_baseline_policy_genome,
    )

    inherited = create_baseline_policy_genome()
    inherited = apply_policy_edit(inherited, PolicyEdit(
        edit_id="e1",
        operation=PolicyOp.ADD_ITEM,
        item_index=None,
        new_item=PolicyItem(
            item_id="inherited_1",
            precondition="defense_coverage_complete_and_zero_certificates",
            action="downgrade_coverage",
            params={"side": "defense", "to_state": "incomplete"},
            rationale="inherited from a previous run",
        ),
        rationale="r",
    ))
    # An inherited policy skill enters the run already graded: only a working
    # item (weak or strong) is projected into the evolution detector calls.
    inherited_meta = inherited.get_item_meta(0)
    inherited_meta["fitness_status"] = "weak"
    inherited.set_item_meta(0, inherited_meta)
    seen_policy: list[list] = []

    def detector(*, genome, case_id, policy_genome=None, **kwargs):
        seen_policy.append(
            list(policy_genome.items) if policy_genome is not None else None
        )
        return {
            "state": "unresolved", "runtime_valid": True,
            "decision_authority": "authoritative", "census": [],
        }

    def mutation(**kwargs):
        return {"no_proposal": True, "reason": "nothing to change"}, {
            "cache_hit": False,
        }

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: _signal(
            str(result["state"])
        ),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    result = runner.run_online_evolution(
        evolution_case_ids=("source",),
        records={"source": {}},
        config={}, output_dir=tmp_path, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        reflection_candidate_count=1,
        initial_policy_genome=inherited,
        run_id="policy_inheritance",
    )

    assert seen_policy
    assert all(
        row and [item.item_id for item in row] == ["inherited_1"]
        for row in seen_policy
    )
    assert result.policy_genome.content_hash == inherited.content_hash
    assert result.policy_genome.items[0].item_id == "inherited_1"

    # The working genome is a copy: mutating the caller's genome afterwards
    # cannot change the run's champion.
    assert result.policy_genome is not inherited


def test_pro_route_escalates_when_flash_diagnosis_cannot_find_owner(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """v6 phase 3: when every primary candidate retreats to no_edit and the
    Flash diagnosis cannot find an executable owner, the Pro route gets one
    deeper attempt on the same categorical outcome."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    calls: list[str] = []
    call_count = 0
    pro_call_count = 0

    def detector(*, genome, case_id, **kwargs):
        state = "verified_clean" if case_id == "anchor" or genome.generation else "unresolved"
        return {
            "state": state, "runtime_valid": True,
            "decision_authority": "authoritative", "census": [],
        }

    def mutation(**kwargs):
        nonlocal call_count
        call_count += 1
        purpose = kwargs.get("purpose")
        calls.append(purpose)
        if purpose == "mutation" and call_count <= 2:
            # The two independent candidate generations retreat to no_edit.
            return {"operation": "no_edit", "rationale": "declined"}, {
                "cache_hit": False,
            }
        if purpose == "reflection_diagnosis":
            # Flash: cannot locate an executable owner.
            return {
                "owner_executable": False, "owner_module": None,
                "failure_field": "", "observed_gap": "",
                "reasoning": "flash cannot locate the gap",
            }, {"cache_hit": False}
        if purpose == "reflection_pro":
            nonlocal pro_call_count
            pro_call_count += 1
            if pro_call_count == 1:
                # Pro diagnosis: locates the owner.
                return {
                    "owner_executable": True,
                    "owner_module": "evidence_obligation",
                    "failure_field": "prosecution_coverage",
                    "observed_gap": "cited evidence fields never populated",
                    "intervention_hint": {
                        "condition": "a claim lacks cited event support",
                        "action": "keep_coverage_incomplete: coverage_state",
                    },
                    "reasoning": "pro diagnosis",
                }, {"cache_hit": False}
            # Pro targeted generation.
            return {
                "target_module": "evidence_obligation", "operation": "add_rule",
                "condition": "a claim lacks cited event support",
                "action": "keep_coverage_incomplete: coverage_state",
                "evidence_anchor": "cited evidence fields never populated",
                "feature_description": "uncited claim keeps evidence incomplete",
                "expected_effect": "prosecution evidence coverage increases",
                "expected_effect_contract": {
                    "observable_field": "prosecution_coverage",
                    "expected_direction": "increase",
                    "expected_scope": "all_cases",
                    "trigger_condition": "prosecution evidence is incomplete",
                },
            }, {"cache_hit": False}
        return {"operation": "no_edit", "rationale": "declined"}, {
            "cache_hit": False,
        }

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: _signal(
            str(result["state"])
        ),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    result = runner.run_online_evolution(
        evolution_case_ids=("source",),
        records={"source": {}, "anchor": {}},
        config={}, output_dir=tmp_path, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        pro_mutation_route={"model": "DeepSeek-V4-Pro"},
        canary_case_ids=("anchor",),
        reflection_candidate_count=2,
        run_id="pro_escalation",
    )

    # Flash diagnosis failed -> exactly one Pro diagnosis + Pro targeted
    # generation, and the edit was accepted.
    assert calls.count("reflection_pro") == 2
    assert calls[0] == "mutation" and calls[1] == "mutation"
    assert "reflection_diagnosis" in calls
    assert result.edits_accepted == 1
    reflection = result.steps[-1].fitness_check["reflection"]
    assert reflection.get("diagnosis_route") == "reflection_pro"


def test_pro_route_survives_flash_targeted_no_edit_residue(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """A no_edit recorded by the Flash diagnosis route must not discard the
    candidate the Pro route later generates (stale-reason regression)."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    calls: list[str] = []
    call_count = 0

    def detector(*, genome, case_id, **kwargs):
        state = "verified_clean" if case_id == "anchor" or genome.generation else "unresolved"
        return {
            "state": state, "runtime_valid": True,
            "decision_authority": "authoritative", "census": [],
        }

    def mutation(**kwargs):
        nonlocal call_count
        call_count += 1
        purpose = kwargs.get("purpose")
        calls.append(purpose)
        if purpose == "mutation" and call_count <= 2:
            return {"operation": "no_edit", "rationale": "declined"}, {
                "cache_hit": False,
            }
        if purpose == "reflection_diagnosis":
            # Flash: diagnosis succeeds but the targeted generation no_edits.
            if call_count == 3:
                return {
                    "owner_executable": True,
                    "owner_module": "evidence_obligation",
                    "failure_field": "prosecution_coverage",
                    "observed_gap": "g",
                    "intervention_hint": {
                            "condition": "c", "action": "keep_coverage_incomplete: coverage_state",
                    },
                    "reasoning": "flash diagnosis",
                }, {"cache_hit": False}
            return {"operation": "no_edit", "rationale": "flash targeted declined"}, {
                "cache_hit": False,
            }
        if purpose == "reflection_pro":
            if call_count == 5:
                return {
                    "owner_executable": True,
                    "owner_module": "evidence_obligation",
                    "failure_field": "prosecution_coverage",
                    "observed_gap": "g",
                    "intervention_hint": {
                            "condition": "c", "action": "keep_coverage_incomplete: coverage_state",
                    },
                    "reasoning": "pro diagnosis",
                }, {"cache_hit": False}
            return {
                "target_module": "evidence_obligation", "operation": "add_rule",
                "condition": "a claim lacks cited event support",
                "action": "keep_coverage_incomplete: coverage_state",
                "evidence_anchor": "a",
                "feature_description": "f",
                "expected_effect": "prosecution evidence coverage increases",
                "expected_effect_contract": {
                    "observable_field": "prosecution_coverage",
                    "expected_direction": "increase",
                    "expected_scope": "all_cases",
                    "trigger_condition": "prosecution evidence is incomplete",
                },
            }, {"cache_hit": False}
        return {"operation": "no_edit", "rationale": "declined"}, {
            "cache_hit": False,
        }

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: _signal(
            str(result["state"])
        ),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    result = runner.run_online_evolution(
        evolution_case_ids=("source",),
        records={"source": {}, "anchor": {}},
        config={}, output_dir=tmp_path, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        pro_mutation_route={"model": "DeepSeek-V4-Pro"},
        canary_case_ids=("anchor",),
        reflection_candidate_count=2,
        run_id="pro_residue",
    )

    # The Pro-generated candidate was accepted despite the Flash-round
    # no_edit that would previously have discarded it.
    assert result.edits_accepted == 1
    assert calls.count("reflection_pro") == 2


def test_causal_episode_context_reaches_reflection(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """Phase 3: the reflection prompt receives the causal episode context
    (failure edge, obligation diff, legal envelope) and never the removed
    contrast-condition block; the contrast registry stays replay-side only."""
    from src.sehd_v6.core.bccse import evolution_runner as runner
    from tests.fixtures.causal_evolution import (
        missing_evidence_field,
        signal_for,
        stable_clean,
    )

    prompts: list[str] = []

    def detector(*, genome, case_id, **kwargs):
        return stable_clean() if case_id == "stable" else missing_evidence_field()

    def mutation(**kwargs):
        prompts.append(kwargs.get("user") or "")
        return {"operation": "no_edit", "rationale": "declined"}, {
            "cache_hit": False,
        }

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: signal_for(
            str(result["state"])
        ),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    runner.run_online_evolution(
        evolution_case_ids=("stable", "source"),
        records={"stable": {}, "source": {}},
        config={}, output_dir=tmp_path, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        reflection_candidate_count=1,
        run_id="causal_context",
    )

    assert prompts
    user = prompts[0]
    assert "<contrast_cases>" not in user
    assert "stable counter-case" not in user
    assert all("<contrast_cases>" not in prompt for prompt in prompts)
    assert "failure_edge" in user
    assert "action_envelope" in user
    assert "obligation_vs_observed" in user
    assert "provider_artifact_missing" in user


# ------------------------------------------------------------------
# P0 frozen fixtures: offline end-to-end loop and interruption resume
# ------------------------------------------------------------------

from tests.fixtures import meta_skill_evolution as p0


def _p0_detections() -> dict:
    return p0.evolution_case_detections()


def _run_p0_loop(tmp_path, output_dir, **overrides):
    from src.sehd_v6.core.bccse import evolution_runner as runner

    records = {"source1": {}, "source2": {}, "anchor": {}}
    kwargs = {
        "evolution_case_ids": ("source1", "source2"),
        "records": records,
        "config": {},
        "output_dir": output_dir,
        "census_root": tmp_path,
        "mutation_route": {"model": "DeepSeek-V4-Flash"},
        "canary_case_ids": ("anchor",),
        "reflection_candidate_count": 1,
    }
    kwargs.update(overrides)
    return runner.run_online_evolution(**kwargs)


def test_p0_mocked_loop_l1_accept_path(tmp_path, monkeypatch, addressable_l1_failure):
    calls = p0.install_mocked_evolution(
        monkeypatch, detections=_p0_detections(),
    )
    result = _run_p0_loop(tmp_path, tmp_path / "l1_out", run_id="p0_l1")
    # source1 is repaired by its candidate replay. source2's first proposal
    # duplicates the rule source1 just accepted, so the structural gate
    # rejects it before replay (duplicate discipline) and the retry's
    # distinct proposal shows no effect and is rejected on the second
    # bounded attempt.
    assert result.edits_accepted == 1
    assert result.edits_rejected == 2
    decisions = [step.gate_decision for step in result.steps]
    assert decisions == [
        "accept_batch", "retry_structural", "reject_fitness_no_effect",
    ]
    # Every mutation call was answered by the offline responder; the
    # detector stub saw the canary, both observations and both replays.
    assert {call["purpose"] for call in calls["mutation"]} <= {"mutation"}
    assert calls["observed_cases"] == ["anchor", "source1", "source2"]


def test_p0_mocked_loop_l2_policy_accept_path(tmp_path, monkeypatch, addressable_l1_failure):
    def replay(*, case_id, genome, policy_genome):
        payload = p0.hollow_defense()
        # The effect gate binds the replay's fired row to the proposal's
        # own item_id (runner-assigned, carried on the candidate genome).
        fired_item = policy_genome.items[-1]
        payload["proof_adjudication"]["policy_evaluation"] = {
            "fired": [{
                "item_id": fired_item.item_id,
                "precondition": "defense_coverage_complete_and_zero_certificates",
                "action": "invalidate_coverage_claim",
                "params": {"side": "defense"},
            }],
            "skipped": [],
            "effective_prosecution_coverage": "incomplete",
            "effective_defense_coverage": "unresolved",
            "telemetry": [],
            "terminal_state": "unresolved",
        }
        return payload

    p0.install_mocked_evolution(
        monkeypatch,
        detections={"l2_source": p0.hollow_defense()},
        replay=replay,
    )
    result = _run_p0_loop(
        tmp_path, tmp_path / "l2_out",
        evolution_case_ids=("l2_source",),
        records={"l2_source": {}, "anchor": {}},
        run_id="p0_l2",
    )
    assert result.evolution_audit["l2_policy_accepts"] == 1
    l2_steps = [
        step for step in result.steps
        if step.trigger_source == "l2_policy_reflection"
    ]
    assert [step.gate_decision for step in l2_steps] == ["accept_policy"]
    assert result.policy_genome.total_items == 1


def test_p0_runtime_invalid_fixture_is_diagnostic_only(tmp_path, monkeypatch, addressable_l1_failure):
    p0.install_mocked_evolution(
        monkeypatch,
        detections={"broken": RuntimeError("provider_timeout: offline")},
    )
    result = _run_p0_loop(
        tmp_path, tmp_path / "invalid_out",
        evolution_case_ids=("broken",),
        records={"broken": {}, "anchor": {}},
        run_id="p0_runtime_invalid",
    )
    assert result.runtime_invalid_cases == 1
    assert [step.gate_decision for step in result.steps] == ["skip_runtime_invalid"]
    assert result.evolution_audit["mutation_calls"] == 0


def _strip_volatile(value):
    if isinstance(value, dict):
        return {
            key: _strip_volatile(item)
            for key, item in value.items()
            if key not in {"timestamp", "updated_at", "duration_seconds"}
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _normalized_run_artifacts(output_dir):
    result = json.loads((output_dir / "evolution_result.json").read_text())
    champion = json.loads((output_dir / "champion_genome.json").read_text())
    policy = json.loads((output_dir / "policy_genome.json").read_text())
    memory = json.loads(
        (output_dir / "htc_tes_experience_memory.json").read_text()
    )
    return {
        "champion_hash": champion["content_hash"],
        "policy_hash": policy["content_hash"],
        "steps": [
            (step["case_id"], step["gate_decision"]) for step in result["steps"]
        ],
        "counters": {
            key: result[key]
            for key in (
                "triggered_cases", "stable_cases", "runtime_invalid_cases",
                "edits_accepted", "edits_rejected",
            )
        },
        "memory": _strip_volatile(memory),
    }


@pytest.mark.parametrize("position", p0.INTERRUPTION_POSITIONS)
def test_p0_interrupted_resume_matches_uninterrupted(
    tmp_path, monkeypatch, addressable_l1_failure, position,
):
    """Checkpoint interruption at the observation / proposal / outcome
    positions must resume to artifacts comparable with an uninterrupted
    run (P0: interrupted and uninterrupted fixtures stay comparable)."""
    reference_dir = tmp_path / "reference"
    p0.install_mocked_evolution(
        monkeypatch, detections=_p0_detections(),
    )
    _run_p0_loop(tmp_path, reference_dir, run_id="p0_reference")

    interrupted_dir = tmp_path / f"interrupted_{position}"
    interrupt_case = {
        "observation": "source2",
        "proposal": "source1",
        "outcome": "source1",
    }[position]
    p0.install_mocked_evolution(
        monkeypatch,
        detections=_p0_detections(),
        interrupt_at=position,
        interrupt_case=interrupt_case,
    )
    with pytest.raises(KeyboardInterrupt, match="planned interruption"):
        _run_p0_loop(tmp_path, interrupted_dir, run_id="p0_interrupted")

    p0.install_mocked_evolution(
        monkeypatch, detections=_p0_detections(),
    )
    resumed = _run_p0_loop(
        tmp_path, interrupted_dir,
        resume_from=interrupted_dir,
        run_id="p0_resumed",
    )
    assert resumed.total_cases == 2
    assert (
        _normalized_run_artifacts(interrupted_dir)
        == _normalized_run_artifacts(reference_dir)
    )
