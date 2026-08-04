"""Regression tests for the first-round framework hardening pass.

Covers contracts that the original suite did not lock down:

1. String gold labels (``"false"``) must parse as booleans, not as truthy.
2. Abstentions (``unresolved``) are excluded from the binary confusion
   matrix instead of being counted as true negatives.
3. The run-local provider cache (``cache_dir``) is actually used by the
   transport, without touching the global cache.
4. Standalone batch detection treats ``runtime_valid=false`` as a failed
   (retryable) case, never as a completed prediction.
5. Evolution checkpoints bind run identity (input content + config) and
   never resurrect an uncommitted shadow genome.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.sehd_v6.core.paca_tcl import basic_runner
from src.sehd_v6.core.paca_tcl import layered_engine
from src.sehd_v6.core.paca_tcl import proof_evaluator
from src.sehd_v6.detector import batch


# ---------------------------------------------------------------
# 1. Gold label parsing
# ---------------------------------------------------------------


def test_parse_gold_label_string_false_is_false() -> None:
    assert basic_runner.parse_gold_label("false") is False
    assert basic_runner.parse_gold_label("False") is False
    assert basic_runner.parse_gold_label("true") is True
    assert basic_runner.parse_gold_label(1) is True
    assert basic_runner.parse_gold_label(0) is False
    with pytest.raises(ValueError):
        basic_runner.parse_gold_label("maybe")


# ---------------------------------------------------------------
# 2. Abstention accounting
# ---------------------------------------------------------------


def test_basic_evaluate_counts_unresolved_abstention_separately() -> None:
    metrics = basic_runner.evaluate([
        {"gold_label": "false", "state": "unresolved", "runtime_valid": True},
        {"gold_label": True, "state": "verified_error", "runtime_valid": True},
    ])
    assert metrics["confusion"].get("tn", 0) == 0
    assert metrics["confusion"].get("tp", 0) == 1
    assert metrics["confusion"].get("fn", 0) == 0
    assert metrics["confusion"]["unresolved"] == 1
    assert metrics["tri_state_counts"] == {"unresolved": 1, "verified_error": 1}


def test_basic_evaluate_parses_string_gold_without_inflating_tn() -> None:
    metrics = basic_runner.evaluate([
        {"gold_label": "false", "state": "verified_clean", "runtime_valid": True},
    ])
    assert metrics["confusion"].get("tn", 0) == 1
    assert metrics["confusion"].get("tp", 0) == 0


def test_layered_evaluate_excludes_unresolved_from_tn() -> None:
    metrics = layered_engine.evaluate([
        {
            "gold_label": "false",
            "state": "unresolved",
            "runtime_valid": True,
            "candidate_path": {"state": "unresolved"},
            "boundary_path": {"state": "unresolved"},
        },
    ])
    assert metrics["confusion"].get("tn", 0) == 0
    assert metrics["confusion"]["unresolved"] == 1


def test_metric_snapshot_parses_string_gold_label() -> None:
    row = {
        "gold_label": "false",
        "state": "verified_error",
        "occurrence_state": "verified_error",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "census": [],
        "gold_steps": [],
        "candidate_path": {"state": "verified_error"},
        "boundary_path": {"state": "verified_clean"},
    }
    snapshot = proof_evaluator.metric_snapshot([row])
    # Predicted positive on a clean gold -> FP; the old bool("false") bug
    # would have reported a TP instead.
    assert snapshot.precision == 0.0
    assert snapshot.hallucination_recall == 0.0
    assert snapshot.macro_f1 == 0.0
    assert snapshot.unresolved_rate == 0.0


def test_metric_snapshot_abstention_is_not_true_negative() -> None:
    row = {
        "gold_label": False,
        "state": "unresolved",
        "occurrence_state": "unresolved",
        "runtime_valid": True,
        "census": [],
        "gold_steps": [],
        "candidate_path": {"state": "unresolved"},
        "boundary_path": {"state": "unresolved"},
    }
    snapshot = proof_evaluator.metric_snapshot([row])
    assert snapshot.unresolved_rate == 1.0
    assert snapshot.clean_fpr == 0.0
    assert snapshot.macro_f1 == 0.0


def test_metric_snapshot_rejects_missing_authority_envelope() -> None:
    row = {
        "gold_label": True,
        "state": "verified_error",
        "occurrence_state": "verified_error",
        "runtime_valid": True,
        "census": [],
        "gold_steps": [],
        "candidate_path": {"state": "verified_error"},
        "boundary_path": {"state": "verified_clean"},
    }
    snapshot = proof_evaluator.metric_snapshot([row])
    assert snapshot.valid_output_rate == 0.0
    assert snapshot.invalid_rate == 1.0
    assert snapshot.unresolved_rate == 1.0
    assert snapshot.hallucination_recall == 0.0


# ---------------------------------------------------------------
# 3. Run-local provider cache
# ---------------------------------------------------------------


def _resolved_route() -> dict[str, object]:
    return {
        "_provider_route_resolved": True,
        "endpoint": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "api_key_environment": "DEEPSEEK_API_KEY",
        "transport_retries": 0,
        "timeout_seconds": 10,
    }


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({
            "choices": [{"message": {"content": "{}"}}], "usage": {},
        }).encode()


def test_provider_transport_reuses_run_local_cache(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-placeholder")
    calls: list[str] = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return _FakeResponse()

    monkeypatch.setattr(basic_runner.DIRECT_PROVIDER_OPENER, "open", fake_urlopen)
    # Global cache must be explicitly off for this run; an .env file may
    # otherwise re-inject a root through load_local_env.
    monkeypatch.setenv("PACA_GLOBAL_CACHE_ROOT", "off")
    assert basic_runner._global_cache_root() is None

    cache_dir = tmp_path / "run_cache"
    first, meta_first = basic_runner.provider_call_with_schema(
        route=_resolved_route(),
        system="return json",
        user="{}",
        cache_dir=cache_dir,
        require_candidates=False,
    )
    second, meta_second = basic_runner.provider_call_with_schema(
        route=_resolved_route(),
        system="return json",
        user="{}",
        cache_dir=cache_dir,
        require_candidates=False,
    )
    assert first == second == {}
    assert len(calls) == 1
    assert meta_first["cache_hit"] is False
    assert meta_second["cache_hit"] is True
    assert meta_second["cache_source"] == "local"
    cached_files = list(cache_dir.rglob("*.json"))
    assert len(cached_files) == 1


def test_mutation_transport_reuses_run_local_cache(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-placeholder")
    calls: list[str] = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return _FakeResponse()

    monkeypatch.setattr(proof_evaluator.DIRECT_PROVIDER_OPENER, "open", fake_urlopen)
    monkeypatch.setenv("PACA_GLOBAL_CACHE_ROOT", "off")

    route = {
        "_provider_route_resolved": True,
        "endpoint": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "api_key_environment": "DEEPSEEK_API_KEY",
        "transport_retries": 0,
        "timeout_seconds": 10,
    }
    cache_dir = tmp_path / "mutation_cache"
    _, meta_first = proof_evaluator._json_object_provider_call(
        route=route, system="return json", user="{}", cache_dir=cache_dir,
    )
    _, meta_second = proof_evaluator._json_object_provider_call(
        route=route, system="return json", user="{}", cache_dir=cache_dir,
    )
    assert len(calls) == 1
    assert meta_first["cache_hit"] is False
    assert meta_second["cache_hit"] is True
    assert meta_second["cache_source"] == "local"


def test_local_cache_is_scoped_to_its_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-placeholder")
    calls: list[str] = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return _FakeResponse()

    monkeypatch.setattr(basic_runner.DIRECT_PROVIDER_OPENER, "open", fake_urlopen)
    monkeypatch.setenv("PACA_GLOBAL_CACHE_ROOT", "off")

    route = _resolved_route()
    basic_runner.provider_call_with_schema(
        route=route, system="return json", user="{}",
        cache_dir=tmp_path / "a", require_candidates=False,
    )
    basic_runner.provider_call_with_schema(
        route=route, system="return json", user="{}",
        cache_dir=tmp_path / "b", require_candidates=False,
    )
    assert len(calls) == 2


# ---------------------------------------------------------------
# 4. Batch completion semantics
# ---------------------------------------------------------------


def _detector_predictions(records: dict[str, dict]) -> dict[str, dict]:
    return {
        case_id: {
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "state": "verified_clean",
        }
        for case_id in records
    }


def test_batch_runtime_invalid_is_failed_and_retried(monkeypatch, tmp_path: Path) -> None:
    attempts: list[str] = []

    def detector(*, case_id, **kwargs):
        attempts.append(case_id)
        if case_id == "case/0":
            return {
                "runtime_valid": False,
                "decision_authority": "authoritative",
                "state": "verified_clean",
            }
        return {
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "state": "verified_clean",
        }

    monkeypatch.setattr(batch, "detect_record", detector)
    records = _detector_predictions(
        {"case/0": {}, "case/1": {}},
    )
    first = batch.run_batch(
        records=records, config={}, output_dir=tmp_path, max_parallel=2,
    )
    assert first["completed_case_count"] == 1
    assert first["failed_case_count"] == 1
    failed_dir = Path(first["cases"]["case/0"]["case_dir"])
    assert (failed_dir / "failure.json").is_file()
    assert not (failed_dir / "completion.json").is_file()
    assert not (failed_dir / "prediction.json").is_file()

    def detector_ok(*, case_id, **kwargs):
        attempts.append(case_id)
        return {
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "state": "verified_clean",
        }

    monkeypatch.setattr(batch, "detect_record", detector_ok)
    second = batch.run_batch(
        records=records, config={}, output_dir=tmp_path, max_parallel=2,
    )
    assert second["completed_case_count"] == 2
    assert second["resumed_case_count"] == 1
    assert attempts[-1] == "case/0"


# ---------------------------------------------------------------
# 5. Checkpoint identity binding and shadow safety
# ---------------------------------------------------------------


def test_checkpoint_without_run_identity_is_rejected(tmp_path: Path) -> None:
    from src.sehd_v6.core.bccse import evolution_runner as runner

    payload = {
        "schema_version": "agenthallu.bccse_v5_checkpoint.v4",
        "next_case_index": 0,
        "evolution_case_ids": ["source"],
        "steps": [],
        "counters": {},
    }
    (tmp_path / "evolution_checkpoint.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )
    (tmp_path / "champion_checkpoint.json").write_text("{}", encoding="utf-8")
    (tmp_path / "shadow_checkpoint.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="run identity"):
        runner.load_evolution_checkpoint(tmp_path)

def test_restore_never_resurrects_uncommitted_shadow() -> None:
    from src.sehd_v6.core.bccse import evolution_runner as runner
    from src.sehd_v6.core.bccse.genome import (
        SkillGenome,
        create_baseline_genome,
    )
    from src.sehd_v6.core.htc_tes.type_aware_fitness import TypeAwareFitness

    champion = create_baseline_genome()
    shadow = champion.clone()
    # A plausible uncommitted edit on the shadow genome.
    shadow.metadata = {**(shadow.metadata or {}), "uncommitted": True}
    checkpoint = {
        "champion_genome_payload": champion.to_dict(),
        "shadow_genome_payload": shadow.to_dict(),
        "steps": [],
        "accepted_edits_history": [],
        "batch_history": [],
        "probe_history": [],
        "baseline_canary_audit": [],
        "budget_usage": {},
        "rollback_reason": "",
        "pending_probe_step_indexes": [],
        "counters": {},
        "next_case_index": 1,
    }
    restored_champion = SkillGenome.from_dict(champion.to_dict())
    restored_current = SkillGenome.from_dict(champion.to_dict())
    restored_stable = SkillGenome.from_dict(champion.to_dict())
    restored = runner._restore_state_from_checkpoint(
        checkpoint,
        champion_genome=restored_champion,
        current_genome=restored_current,
        stable_genome=restored_stable,
        type_fitness=TypeAwareFitness(),
        evolution_case_ids=("source",),
    )
    assert restored["next_case_index"] == 1
    assert restored_current.content_hash == restored_champion.content_hash
    assert restored_stable.content_hash == restored_champion.content_hash
    assert "uncommitted" not in (restored_current.metadata or {})
