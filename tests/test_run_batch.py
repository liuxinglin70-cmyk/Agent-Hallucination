from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments import run_batch


def test_manifest_replay_preserves_case_order_and_validates_identity(
    tmp_path: Path,
) -> None:
    pool = {
        "BFCL/001": {"path": "one", "hallucination": True},
        "Camel/002": {"path": "two", "hallucination": False},
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "case_ids": ["Camel/002", "BFCL/001"],
    }), encoding="utf-8")

    assert run_batch.load_manifest_case_ids(manifest, pool, 2) == [
        "Camel/002", "BFCL/001",
    ]

    manifest.write_text(json.dumps({
        "case_ids": ["BFCL/001", "BFCL/001"],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        run_batch.load_manifest_case_ids(manifest, pool, 2)

    manifest.write_text(json.dumps({
        "case_ids": ["BFCL/001", "Camel/002"],
        "gold_polarity": {"BFCL/001": False, "Camel/002": False},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="gold_polarity"):
        run_batch.load_manifest_case_ids(manifest, pool, 2)


def test_main_seals_manifest_and_propagates_child_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = {
        "BFCL/001": {"path": "one", "hallucination": True},
        "Camel/002": {"path": "two", "hallucination": False},
    }
    results_root = tmp_path / "results"
    monkeypatch.setattr(run_batch, "RESULTS_ROOT", results_root)
    monkeypatch.setattr(run_batch, "LOGS_ROOT", tmp_path / "logs")
    monkeypatch.setattr(run_batch, "pool_index", lambda: pool)
    monkeypatch.setattr(run_batch, "load_used_cases", lambda: set())

    def fake_run_case(
        case_id: str,
        record_path: str,
        batch_dir: Path,
        log_dir: Path,
        max_mutation_calls: int,
        reflection_candidates: int,
        provider_cache_mode: str,
        canary_records_path: Path | None,
        canary_limit: int,
    ) -> dict[str, object]:
        return {
            "case_id": case_id,
            "returncode": 1 if case_id == "BFCL/001" else 0,
            "elapsed_seconds": 0.0,
            "log": "unused.log",
            "output_dir": str(batch_dir / case_id.replace("/", "__")),
        }

    monkeypatch.setattr(run_batch, "run_case", fake_run_case)
    monkeypatch.setattr(sys, "argv", [
        "run_batch.py", "--batch-index", "99", "--size", "2",
        "--workers", "2",
    ])

    assert run_batch.main() == 1
    manifest = json.loads(
        (results_root / "batch99" / "batch_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    report = json.loads(
        (results_root / "batch99" / "batch_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["case_ids"] == report["case_ids"]
    assert manifest["gold_polarity"] == {
        "BFCL/001": True,
        "Camel/002": False,
    }
    assert report["protocol"]["manifest_source"] == (
        "sampled_from_unused_pool"
    )


@pytest.mark.parametrize(
    ("cache_mode", "inherited_root", "expected_root"),
    (("fresh", "shared-cache", "off"), ("global", "shared-cache", "shared-cache")),
)
def test_run_case_makes_cross_run_cache_policy_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cache_mode: str,
    inherited_root: str,
    expected_root: str,
) -> None:
    record_path = tmp_path / "source.json"
    record_path.write_text(json.dumps({"is_hallucination": "true"}), encoding="utf-8")
    canary_path = tmp_path / "canaries.json"
    canary_path.write_text(json.dumps({"canary": {}}), encoding="utf-8")
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        observed["command"] = command
        observed.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("PACA_GLOBAL_CACHE_ROOT", inherited_root)
    monkeypatch.setattr(run_batch.subprocess, "run", fake_run)

    outcome = run_batch.run_case(
        "BFCL/001",
        str(record_path),
        tmp_path / "batch06",
        tmp_path / "logs",
        100,
        4,
        cache_mode,
        canary_path,
        3,
    )

    child_env = observed["env"]
    assert isinstance(child_env, dict)
    assert child_env["PACA_GLOBAL_CACHE_ROOT"] == expected_root
    assert "--use-gold" in observed["command"]
    assert "--fitness-use-gold" in observed["command"]
    assert observed["command"][observed["command"].index("--canary-records") + 1] == (
        str(canary_path)
    )
    assert observed["command"][observed["command"].index("--canary-limit") + 1] == "3"
    assert outcome["returncode"] == 0


def _write_case_output(
    output_dir: Path, *,
    total_cases: int = 1,
    triggered: int = 1,
    stable: int = 0,
    runtime_invalid: int = 0,
    edits_accepted: int = 0,
    edits_rejected: int = 0,
    l2_accepts: int = 0,
    l2_audits: list[dict] | None = None,
    detector_calls: int = 1,
    mutation_calls: int = 0,
    policy_replays: int = 0,
) -> None:
    """Write minimal evolution_result.json for summarize_case to read."""
    output_dir.mkdir(parents=True, exist_ok=True)
    l2_audits = l2_audits or []
    l2_rejects = sum(
        1 for a in l2_audits if str(a.get("decision", "")).startswith("reject")
    )
    l2_skips = sum(
        1 for a in l2_audits if str(a.get("decision", "")).startswith("skip")
    )
    payload = {
        "total_cases": total_cases,
        "triggered_cases": triggered,
        "stable_cases": stable,
        "runtime_invalid_cases": runtime_invalid,
        "edits_accepted": edits_accepted,
        "edits_rejected": edits_rejected,
        "halted": False,
        "halt_reason": "",
        "evolution_audit": {
            "l2_policy_audit": l2_audits,
            "l2_policy_accepts": l2_accepts,
            "rejection_summary": {"by_reason": {}, "by_stage": {}, "by_decision": {}},
            "detector_calls": detector_calls,
            "mutation_calls": mutation_calls,
            "mutation_errors": 0,
            "mutation_cache_hits": 0,
            "mutation_transport_attempts": 0,
            "mutation_repairs": 0,
            "mutation_retry_attempts": 0,
            "mutation_budget_exhausted": 0,
            "provider_calls": detector_calls,
            "source_replays": 0,
            "policy_replays": policy_replays,
            "policy_replay_errors": 0,
            "contrast_replays": 0,
            "contrast_replay_errors": 0,
            "probe_cases": 0,
            "total_provider_calls": detector_calls + mutation_calls,
        },
    }
    (output_dir / "evolution_result.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    # suppress unused-variable warnings for computed counters
    _ = l2_rejects + l2_skips


def test_summarize_case_includes_l2_runtime_and_budget_fields(
    tmp_path: Path,
) -> None:
    """summarize_case must surface total_cases, runtime_invalid_cases,
    l2 counts, rejection_summary, and budget — not just the old
    triggered/edits/stable subset (P2-E)."""
    output = tmp_path / "case" / "output"
    _write_case_output(
        output,
        total_cases=1, triggered=1, runtime_invalid=0,
        edits_accepted=0, edits_rejected=0,
        l2_accepts=1,
        l2_audits=[
            {"decision": "accept_policy", "reason": ""},
            {"decision": "reject_policy_fitness",
             "reason": "state_change_against_gold_polarity"},
        ],
        detector_calls=5, mutation_calls=1, policy_replays=2,
    )
    summary = run_batch.summarize_case("case", output)
    assert summary["total_cases"] == 1
    assert summary["runtime_invalid_cases"] == 0
    assert summary["l2_policy_accepts"] == 1
    assert summary["l2_policy_rejects"] == 1
    assert "rejection_summary" in summary
    assert isinstance(summary["budget"], dict)
    assert summary["budget"]["detector_calls"] == 5
    assert summary["budget"]["policy_replays"] == 2


def test_build_batch_totals_aggregates_counts_and_checks_invariant(
    tmp_path: Path,
) -> None:
    """The batch-level totals aggregate per-case counters and verify the
    count invariant total = triggered + stable + runtime_invalid (P2-E)."""
    output_a = tmp_path / "a" / "output"
    output_b = tmp_path / "b" / "output"
    _write_case_output(
        output_a, total_cases=1, triggered=1, stable=0,
        l2_accepts=1, detector_calls=3, policy_replays=1,
    )
    _write_case_output(
        output_b, total_cases=1, triggered=0, stable=1,
        l2_audits=[{"decision": "reject_policy_no_effect", "reason": "x"}],
        detector_calls=2, policy_replays=1,
    )
    cases = [
        run_batch.summarize_case("a", output_a),
        run_batch.summarize_case("b", output_b),
    ]
    totals = run_batch._build_batch_totals(cases)
    assert totals["total_cases"] == 2
    assert totals["triggered_cases"] == 1
    assert totals["stable_cases"] == 1
    assert totals["runtime_invalid_cases"] == 0
    assert totals["l2_policy_accepts"] == 1
    assert totals["l2_policy_rejects"] == 1
    assert totals["count_invariant_ok"] is True
    assert totals["budget"]["detector_calls"] == 5
    assert totals["budget"]["policy_replays"] == 2


def test_build_batch_totals_detects_broken_invariant(tmp_path: Path) -> None:
    """A broken count invariant (total != triggered + stable + runtime_invalid)
    is surfaced as count_invariant_ok=False."""
    output = tmp_path / "case" / "output"
    _write_case_output(
        output, total_cases=1, triggered=1, stable=1, runtime_invalid=0,
    )
    cases = [run_batch.summarize_case("case", output)]
    totals = run_batch._build_batch_totals(cases)
    assert totals["count_invariant_ok"] is False


def test_batch_report_includes_totals_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The batch_report.json written by main() includes a ``totals`` block
    with L2/runtime/budget aggregates and the count invariant (P2-E)."""
    pool = {
        "BFCL/001": {"path": "one", "hallucination": True},
        "Camel/002": {"path": "two", "hallucination": False},
    }
    results_root = tmp_path / "results"
    monkeypatch.setattr(run_batch, "RESULTS_ROOT", results_root)
    monkeypatch.setattr(run_batch, "LOGS_ROOT", tmp_path / "logs")
    monkeypatch.setattr(run_batch, "pool_index", lambda: pool)
    monkeypatch.setattr(run_batch, "load_used_cases", lambda: set())

    def fake_run_case(case_id, record_path, batch_dir, log_dir, *args, **kwargs):
        case_output = batch_dir / case_id.replace("/", "__") / "output"
        _write_case_output(
            case_output,
            total_cases=1,
            triggered=1 if case_id == "BFCL/001" else 0,
            stable=0 if case_id == "BFCL/001" else 1,
            l2_audits=[{"decision": "reject_policy_no_effect", "reason": "x"}]
            if case_id == "BFCL/001" else [],
            detector_calls=2,
        )
        return {
            "case_id": case_id,
            "returncode": 0,
            "elapsed_seconds": 0.0,
            "log": "unused.log",
            "output_dir": str(case_output),
        }

    monkeypatch.setattr(run_batch, "run_case", fake_run_case)
    monkeypatch.setattr(sys, "argv", [
        "run_batch.py", "--batch-index", "98", "--size", "2",
        "--workers", "2",
    ])
    assert run_batch.main() == 0
    report = json.loads(
        (results_root / "batch98" / "batch_report.json").read_text("utf-8")
    )
    assert "totals" in report
    totals = report["totals"]
    assert totals["total_cases"] == 2
    assert totals["triggered_cases"] == 1
    assert totals["stable_cases"] == 1
    assert totals["l2_policy_rejects"] == 1
    assert totals["count_invariant_ok"] is True
    assert totals["budget"]["detector_calls"] == 4

