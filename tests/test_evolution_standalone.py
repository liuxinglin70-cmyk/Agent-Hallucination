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
        "new_content": (
            "When a factual claim lacks a cited trajectory event, require "
            "one event-backed certificate."
        ),
        "rationale": "Require observable support.",
        "feature_description": "factual claim without cited trajectory evidence",
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
def offline_evolution(monkeypatch, tmp_path):
    from src.sehd_v6.core.bccse import evolution_runner as runner

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
