"""Regression coverage for public entry points and repository boundaries."""
from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from detector.config import load_config as detector_config
from evolution import cli
from evolution.config import load_config
from evolution.splits import build_split, split_manifest_report

ROOT = Path(__file__).resolve().parents[1]


def test_verification_identity_tracks_experiment_implementation(tmp_path, monkeypatch):
    from scripts import verify

    (tmp_path / "datasets").mkdir()
    (tmp_path / "datasets/integrity.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "experiments").mkdir()
    implementation = tmp_path / "experiments/runner.py"
    implementation.write_text("VERSION = 1\n", encoding="utf-8")
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    before = verify.source_manifest()
    implementation.write_text("VERSION = 2\n", encoding="utf-8")
    after = verify.source_manifest()
    assert before["experiments/runner.py"] != after["experiments/runner.py"]


def test_detector_has_no_dependency_on_evolution():
    from scripts.source_audit import audit_sources

    assert audit_sources(ROOT)["boundary_violations"] == []


def test_no_circular_dependencies():
    from scripts.source_audit import audit_sources

    assert audit_sources(ROOT)["import_cycles"] == []


@pytest.mark.local_data
def test_current_configuration_resolves_real_development_partition():
    config = load_config()
    split = build_split(config=config, dataset_root=config["dataset_root"], mini=True)
    assert len(split.pool) == 30
    assert len(split.validation) == 10
    assert not set(split.pool) & set(split.validation)
    assert set(split.sentinel + split.gain + split.patrol_pool) == set(split.pool)
    assert Path(config["detector_config"]).is_file()
    assert all(Path(p).is_file() for p in config["historical_development_manifests"])


def test_marginal_set_carries_both_polarities(tmp_path):
    """The instrument must be able to score either claim direction.

    ``marginal_cases`` counts all-case correct predictions gained, so a claim
    can only be scored if the marginal set contains the gold polarity it
    repairs. Source-quota sampling alone left that to chance; the polarity
    floor makes it a property of the split.
    """
    dataset_root = tmp_path / "data"
    dataset_root.mkdir()
    cases = []
    for index in range(12):
        gold = "true" if index < 8 else "false"
        source = "A" if index % 2 == 0 else "B"
        case_id = f"{source}/{index:03d}.json"
        cases.append(case_id)
        (dataset_root / source).mkdir(exist_ok=True)
        (dataset_root / case_id).write_text(json.dumps({
            "question": "q", "history": [],
            "is_hallucination": gold,
        }), encoding="utf-8")
    manifest = {
        "evolution_set": {"size": len(cases), "cases": cases},
        "validation_set": {"size": 0, "cases": []},
    }
    manifest_path = tmp_path / "development.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    split = build_split(config={
        "mini_split_manifest": str(manifest_path),
        "split": {
            "seed": 7,
            "sentinel_positives": 1,
            "sentinel_negatives": 1,
            "mini_pool_size": len(cases),
            "mini_gain_size": 4,
            "mini_gain_min_positives": 2,
            "mini_gain_min_negatives": 2,
        },
    }, dataset_root=dataset_root, mini=True)
    report = split_manifest_report(split)
    assert report["gain_size"] == 4
    assert report["gain_polarity"]["error"] >= 2
    assert report["gain_polarity"]["clean"] >= 2
    assert report["sentinel_polarity"]["error"] == 1
    assert report["sentinel_polarity"]["clean"] == 1


def test_marginal_set_polarity_floor_fails_closed(tmp_path):
    """An unsatisfiable floor is an error, not a silently weaker split."""
    dataset_root = tmp_path / "data"
    dataset_root.mkdir()
    cases = []
    for index in range(6):
        source = "A"
        case_id = f"{source}/{index:03d}.json"
        cases.append(case_id)
        (dataset_root / source).mkdir(exist_ok=True)
        # Every case is an error-gold case: a clean floor cannot be met.
        (dataset_root / case_id).write_text(json.dumps({
            "question": "q", "history": [], "is_hallucination": "true",
        }), encoding="utf-8")
    manifest_path = tmp_path / "development.json"
    manifest_path.write_text(json.dumps({
        "evolution_set": {"size": len(cases), "cases": cases},
        "validation_set": {"size": 0, "cases": []},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="gain_min_negatives"):
        build_split(config={
            "mini_split_manifest": str(manifest_path),
            "split": {
                "seed": 7,
                "sentinel_positives": 1,
                "sentinel_negatives": 0,
                "mini_pool_size": len(cases),
                "mini_gain_size": 4,
                "mini_gain_min_positives": 1,
                "mini_gain_min_negatives": 1,
            },
        }, dataset_root=dataset_root, mini=True)


def test_formal_protocol_is_not_silently_inferred():
    config = load_config()
    with pytest.raises(ValueError, match="No formal split"):
        build_split(config=config, dataset_root=config["dataset_root"])


def test_missing_explicit_configuration_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.json")


def test_missing_exposure_record_cannot_disable_contamination_check(tmp_path):
    manifest = tmp_path / "formal.json"
    manifest.write_text(json.dumps({
        "evolution_pool": {"cases": ["A/001.json"]},
        "final_verification": {"records": [{"case_id": "A/002.json", "gold": True}]},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="historical development manifest is missing"):
        build_split(config={
            "split_manifest": str(manifest),
            "historical_development_manifests": [str(tmp_path / "missing.json")],
        }, dataset_root=tmp_path)


def test_detector_environment_uses_stable_prefix(monkeypatch):
    monkeypatch.setenv("DETECTOR_JUDGE_MODEL", "test-stable-model")
    config = detector_config(require_credentials=False)
    assert config["judge_route"]["model"] == "test-stable-model"


def test_shipped_routes_use_supported_thinking_levels():
    # GLM-5.3-flash always reasons and rejects any explicit thinking value
    # (including "low") with an intermittent 400, so a GLM route must not send
    # the field at all. DeepSeek routes may still disable thinking.
    for name in ("autodl.json", "deepseek.json"):
        raw = json.loads(
            (ROOT / "detector" / "configs" / name).read_text(encoding="utf-8")
        )
        blocks = [raw.get("provider") or {}]
        blocks += [raw[key] for key in raw if key.endswith("_route")]
        for block in blocks:
            if "glm" not in str(block.get("model") or "").lower():
                continue
            assert "thinking" not in (block.get("extra_body") or {}), (
                name, block
            )


def test_shipped_proposer_budget_cannot_truncate_on_glm():
    # GLM-5.3-flash always reasons; measured proposer completions run
    # 6.2k-12.5k tokens with reasoning at 85-92% of that. At the old 4096
    # ceiling the reply was cut off before the JSON array, yielding
    # truncated_output and zero candidates. Keep a ceiling with real headroom.
    config = load_config()
    assert config["proposer"]["max_output_tokens"] >= 32768


def test_shipped_recall_budget_cannot_truncate_on_glm():
    # Observed in the 2026-09-18 error-scope preflight: the recall scanner's
    # 8192-token ceiling cut the reply mid-JSON (truncated_output), and since
    # a truncated scan is a provider error rather than a contract violation it
    # could not be repaired. The stage then read as unavailable, which
    # degraded an otherwise authoritative clean verdict to
    # degraded_judge_only. The scanner enumerates every candidate deviation
    # over a long trajectory and the model always reasons first, so it needs
    # the same headroom the proposer was given for the same reason.
    raw = json.loads(
        (ROOT / "detector" / "configs" / "autodl.json").read_text(encoding="utf-8")
    )
    assert raw["recall_route"]["max_tokens"] >= 32768


def test_shipped_case_budget_fits_a_measured_long_trajectory():
    # Measured on 2026-09-18 (scripts/diagnostic_019_timing.py): the
    # SmolAgents/019 trajectory needs 1480 seconds end to end with generous
    # per-call budgets. At the old 900-second case deadline its judge votes
    # consumed the budget, the tracer and defense never started, and the row
    # fail-closed to non-authoritative. That made the defect-injection audit
    # unable to score its arm, which freezes the cycle by design. The case
    # deadline is a runtime envelope, not an admission threshold: it must fit
    # the trajectories the corpus actually contains.
    #
    # Re-measured on 2026-09-21, and the 300s per-attempt cap turned out to be
    # the binding constraint rather than the case deadline. Reasoning length on
    # one identical prompt ranged from 36 to 13,437 tokens (median 2,113) at
    # 20-46 tok/s, so a legitimate deliberation routinely needs more than 300s;
    # every call that did was killed mid-flight and retried from scratch,
    # paying the first attempt's tokens and wall clock twice. Across the run's
    # cache, judge needed 44 extra attempts over 25 cases, defense 17 over 9,
    # and the causal upgrade 20 over 7 -- and two of 18 rows were cut at
    # exactly 3600s while still working. A cut call returns nothing and burns
    # its whole budget, and because the two arms run at different times a cut
    # can land on one arm alone, which HARMLESSNESS (new_unresolved_allowed=0)
    # then reads as a regression the candidate caused. The ceilings below are
    # the floors that measurement justifies.
    raw = json.loads(
        (ROOT / "detector" / "configs" / "autodl.json").read_text(encoding="utf-8")
    )
    assert raw["case_timeout_seconds"] >= 10800
    assert raw["provider"]["timeout_seconds"] >= 900
    assert raw["provider"]["total_timeout_seconds"] >= 2400
    # The judge route deliberately carries no max_tokens. Measured completions
    # reached 14,090 tokens on one call and the length is heavy-tailed (36 to
    # 13,437 on one identical prompt), so imposing a ceiling here would add a
    # truncation mode the route does not currently have: a truncated reply is
    # a provider error, which resolves the case to unresolved.
    assert "max_tokens" not in raw["judge_route"]


def test_experiment_cli_preserves_optional_cycle_default(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "load_config", lambda _: {"work_root": str(tmp_path)})
    monkeypatch.setattr(cli, "EvolutionEngine", lambda *a, **k: SimpleNamespace(
        run_experiment=lambda cycles: seen.append(cycles) or {
            "cycles": [], "cycles_requested": 3, "cycles_completed": 3,
            "stopped_early": False,
        }, close=lambda: None))
    assert cli.cmd_experiment(SimpleNamespace(config=None, backend=None, mini=True, cycles=None)) == 0
    assert seen == [None]


@pytest.mark.parametrize(
    "state, expected",
    [("completed", 0), ("frozen_after_defect_injection_audit", 2),
     ("blocked_evaluation_preflight", 2),
     ("blocked_patrol_measurement", 2)],
)
def test_cycle_cli_exit_code_matches_cycle_state(monkeypatch, state, expected):
    monkeypatch.setattr(cli, "load_config", lambda _: {})
    monkeypatch.setattr(cli, "_apply_mechanism_binding", lambda config, _args: config)
    monkeypatch.setattr(cli, "EvolutionEngine", lambda *a, **k: SimpleNamespace(
        run_cycle=lambda _cycle, **_kwargs: {"state": state}, close=lambda: None))
    args = SimpleNamespace(
        config=None, backend=None, mini=True, cycle=1, resume=False,
    )

    assert cli.cmd_cycle(args) == expected


def test_experiment_cli_fails_when_a_cycle_stops_early(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_config", lambda _: {"work_root": str(tmp_path)})
    monkeypatch.setattr(cli, "EvolutionEngine", lambda *a, **k: SimpleNamespace(
        run_experiment=lambda _cycles: {
            "cycles": [{"state": "frozen_after_defect_injection_audit"}],
            "cycles_requested": 1, "cycles_completed": 0,
            "stopped_early": True,
        }, close=lambda: None))
    args = SimpleNamespace(
        config=None, backend=None, mini=False, cycles=1, resume=False,
    )

    assert cli.cmd_experiment(args) == 2


def test_governance_cli_preserves_optional_cycle_default(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "load_config", lambda _: {})
    monkeypatch.setattr(cli, "EvolutionEngine", lambda *a, **k: nullcontext(SimpleNamespace(
        run_governance=lambda cycle: seen.append(cycle) or {})))
    assert cli.cmd_governance(SimpleNamespace(config=None, backend=None, mini=True, cycle=None)) == 0
    assert seen == [None]


@pytest.mark.parametrize("valid, expected", [(True, 0), (False, 2)])
def test_ledger_cli_exit_code_matches_verdict(tmp_path, valid, expected):
    from evolution.ledger import Ledger
    path = tmp_path / "ledger.jsonl"
    Ledger(path).append("probe", {"admitted": False})
    if not valid:
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry["payload"]["admitted"] = True
        path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"ledger_path": str(path)}), encoding="utf-8")
    assert cli.cmd_verify_ledger(SimpleNamespace(config=str(config))) == expected


def test_h_t_stores_stay_inside_the_run_root():
    """Harness and memory paths must not be writable side effects on the repo.

    They are run state like the registry and the ledger. A check that isolates
    work_root but not these two would leave the default
    ``artifacts/evolution/harness`` and ``.../memory`` behind after a smoke or
    verification run.
    """
    config = load_config()
    for key in ("harness_dir", "memory_root"):
        assert config[key], f"{key} must be configured"
        resolved = Path(config[key])
        assert "artifacts" in resolved.parts
        # Each must be a distinct location, not an alias of the work root.
        assert resolved != Path(config["work_root"])
    assert Path(config["harness_dir"]) != Path(config["memory_root"])


def test_capability_smoke_isolates_the_h_t_stores(tmp_path):
    """The capability smoke must keep its H_t writes under its own root."""
    from evolution.smoke import build_capability_smoke_engine

    root = tmp_path / "smoke"
    engine = build_capability_smoke_engine(root)
    assert engine.harness_dir.is_relative_to(root)
    assert engine.memory_root.is_relative_to(root)
    assert engine.gap_store.is_relative_to(root)


def test_smoke_cli_rejects_failed_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_config", lambda _: {"work_root": str(tmp_path), "dataset_root": "unused"})
    monkeypatch.setattr(cli, "build_split", lambda **_: SimpleNamespace(
        patrol_pool=["A/patrol1", "A/patrol2"], gain=["A/gain1", "A/gain2"]))
    monkeypatch.setattr(cli, "_gold", lambda *_: True)
    monkeypatch.setattr("evolution.backends.make_backend", lambda *a, **k: object())
    monkeypatch.setattr(cli, "EvolutionEngine", lambda *a, **k: nullcontext(SimpleNamespace(run_cycle=lambda _: {
        "state": "completed", "marginal_selection": {"selected": []},
        "audit": {"passed": True}, "ledger_integrity": {"valid": True},
    })))
    assert cli.cmd_smoke(SimpleNamespace(config=None)) == 2
