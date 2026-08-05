"""Phase 5: complete cross-run inheritance through the state manifest."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.sehd_v6.core.bccse.evolution_experience_memory import SCHEMA_VERSION
from src.sehd_v6.evolution.state_manifest import (
    MANIFEST_NAME,
    MANIFEST_SCHEMA_VERSION,
    load_state_manifest,
    write_state_manifest,
)

from tests.fixtures.causal_evolution import (
    edit_payload,
    signal_for,
    stable_clean,
)

from tests.test_feedback_loop import _result


def _inheritance_env(monkeypatch):
    from src.sehd_v6.core.bccse import evolution_runner as runner

    def detector(*, genome, case_id, **kwargs):
        if case_id == "anchor" or "stable" in case_id:
            return stable_clean()
        state = "verified_clean" if genome.generation > 0 else "unresolved"
        return _result(state)

    def mutation(**kwargs):
        if "新增规则" in str(kwargs.get("user") or ""):
            return {
                "decision": "pass",
                "reason": "ok",
                "conflicting_rule_indices": [],
            }, {}
        return edit_payload(), {}

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner,
        "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: signal_for(
            str(result["state"])
        ),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)
    monkeypatch.setattr(runner, "has_provider_credential", lambda _route: True)
    return runner


def _run_one(tmp_path, monkeypatch, *, name, case_ids, inherit_from=None):
    from src.sehd_v6.core.paca_tcl.detector import REQUIRED_ROUTES
    from src.sehd_v6.evolution.runner import run_evolution

    output_dir = tmp_path / name
    run_evolution(
        records={
            case_id: {"history": [{"role": "assistant", "content": case_id}]}
            for case_id in case_ids
        },
        detector_config={route: {} for route in REQUIRED_ROUTES},
        output_dir=output_dir,
        census_root=tmp_path,
        mutation_route={
            "model": "DeepSeek-V4-Flash",
            "api_key_environment": "DEEPSEEK_API_KEY",
        },
        run_id=name,
        inherit_from=inherit_from,
    )
    return output_dir


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_completed_run_writes_valid_state_manifest(
    tmp_path, monkeypatch,
) -> None:
    _inheritance_env(monkeypatch)
    output_dir = _run_one(
        tmp_path, monkeypatch, name="run_one", case_ids=("stable", "source_a"),
    )
    manifest_path = output_dir / MANIFEST_NAME
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["source_run_id"] == "run_one"
    assert manifest["lineage"] == []
    for key in (
        "provider_rule_genome",
        "adjudication_policy_genome",
        "experience_memory",
    ):
        entry = manifest[key]
        assert (output_dir / entry["path"]).is_file()
        assert entry["sha256"] == _sha256(output_dir / entry["path"])
    loaded = load_state_manifest(output_dir)
    assert loaded["source_run_id"] == "run_one"


def test_inherit_carries_complete_state_to_new_data_stream(
    tmp_path, monkeypatch,
) -> None:
    _inheritance_env(monkeypatch)
    first = _run_one(
        tmp_path, monkeypatch, name="run_one",
        case_ids=("stable", "source_a"),
    )
    first_genome = (first / "champion_genome.json").read_bytes()
    first_policy = (first / "policy_genome.json").read_bytes()
    first_memory = json.loads(
        (first / "htc_tes_experience_memory.json").read_text(encoding="utf-8")
    )
    assert first_memory["failure_mechanisms"]

    second = _run_one(
        tmp_path, monkeypatch, name="run_two",
        case_ids=("new_stable", "new_source"),
        inherit_from=first,
    )
    # The new data stream started from the inherited genome/policy and made
    # no edits, so the final artifacts match the previous manifest exactly.
    assert (second / "champion_genome.json").read_bytes() == first_genome
    assert (second / "policy_genome.json").read_bytes() == first_policy
    # The inherited mechanism experience is still present and grew.
    second_memory = json.loads(
        (second / "htc_tes_experience_memory.json").read_text(
            encoding="utf-8"
        )
    )
    assert second_memory["schema_version"] == SCHEMA_VERSION
    second_entry = second_memory["failure_mechanisms"][0]
    first_entry = first_memory["failure_mechanisms"][0]
    assert second_entry["signature"] == first_entry["signature"]
    assert (
        second_entry["observation_count"] >= first_entry["observation_count"]
    )
    # The second run's manifest carries the lineage of the first.
    second_manifest = json.loads(
        (second / MANIFEST_NAME).read_text(encoding="utf-8")
    )
    assert second_manifest["source_run_id"] == "run_two"
    assert second_manifest["lineage"][0]["run_id"] == "run_one"
    assert second_manifest["lineage"][0]["dir"] == str(first)


def test_inherit_rejects_explicit_parameter_conflict(
    tmp_path, monkeypatch,
) -> None:
    from src.sehd_v6.core.paca_tcl.detector import REQUIRED_ROUTES
    from src.sehd_v6.evolution.runner import run_evolution

    _inheritance_env(monkeypatch)
    first = _run_one(
        tmp_path, monkeypatch, name="run_conflict",
        case_ids=("stable", "source_a"),
    )
    with pytest.raises(ValueError, match="conflicts with the inherited"):
        run_evolution(
            records={"x": {}},
            detector_config={route: {} for route in REQUIRED_ROUTES},
            output_dir=tmp_path / "conflict_out",
            census_root=tmp_path,
            mutation_route=None,
            run_id="conflict",
            inherit_from=first,
            initial_genome=tmp_path / "other_genome.json",
        )


def test_inherit_rejects_corrupt_artifact_hash(
    tmp_path, monkeypatch,
) -> None:
    _inheritance_env(monkeypatch)
    first = _run_one(
        tmp_path, monkeypatch, name="run_corrupt",
        case_ids=("stable", "source_a"),
    )
    (first / "champion_genome.json").write_text(
        "{}", encoding="utf-8",
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        load_state_manifest(first)


def test_inherit_rejects_missing_manifest(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="manifest"):
        load_state_manifest(tmp_path / "no_manifest")


def test_inherit_and_resume_are_distinct(
    tmp_path, monkeypatch,
) -> None:
    """Resume demands the same input queue; inheritance starts a new queue
    with the complete previous state."""
    from src.sehd_v6.core.paca_tcl.detector import REQUIRED_ROUTES
    from src.sehd_v6.evolution.runner import run_evolution

    _inheritance_env(monkeypatch)
    first = _run_one(
        tmp_path, monkeypatch, name="run_distinct",
        case_ids=("stable", "source_a"),
    )
    with pytest.raises(ValueError, match="evolution_case_ids|input_content_hash"):
        run_evolution(
            records={"brand_new": {}},
            detector_config={route: {} for route in REQUIRED_ROUTES},
            output_dir=tmp_path / "resume_wrong",
            census_root=tmp_path,
            mutation_route=None,
            run_id="resume_wrong",
            resume_from=first,
        )
    # Inheritance accepts the same new records without any hash check.
    run_evolution(
        records={"brand_new": {}},
        detector_config={route: {} for route in REQUIRED_ROUTES},
        output_dir=tmp_path / "inherit_ok",
        census_root=tmp_path,
        mutation_route=None,
        run_id="inherit_ok",
        inherit_from=first,
    )
    assert (tmp_path / "inherit_ok" / MANIFEST_NAME).is_file()


def test_manifest_write_rejects_incomplete_run(tmp_path) -> None:
    (tmp_path / "champion_genome.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="artifacts missing"):
        write_state_manifest(tmp_path, run_id="partial")


def test_cli_exposes_inherit_from_flag(tmp_path) -> None:
    completed = subprocess.run(
        [
            sys.executable, "-m", "src.sehd_v6.evolution", "--help",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
        timeout=120,
    )
    assert completed.returncode == 0
    assert "--inherit-from" in completed.stdout
    assert "--resume-from" in completed.stdout
