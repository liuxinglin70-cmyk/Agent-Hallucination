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
