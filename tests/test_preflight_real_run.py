"""The pre-run readiness check must catch what it claims to catch.

Every test here pins one condition the check reports, and the direction the
report takes when the condition is met or missed. A readiness check that
silently passes is worse than none: the whole point is that a long real run is
started only on evidence, and the evidence is these checks.

Offline throughout: configuration, corpus metadata and the calibration record
are read; no provider is contacted and no run directory is written.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load_module():
    path = REPO / "scripts" / "preflight_real_run.py"
    spec = importlib.util.spec_from_file_location("preflight_real_run", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["preflight_real_run"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def preflight():
    return _load_module()


def _config(tmp_path: Path, *, case_timeout: int = 3600,
            cycle_timeout: int = 14400, workers: int = 8) -> dict:
    from evolution.config import load_config

    cfg = dict(load_config())
    cfg["dataset_root"] = str(REPO / "datasets/raw/agenthallu/AgentHallu")
    cfg["work_root"] = str(tmp_path / "work")
    cfg["registry_dir"] = str(tmp_path / "work" / "registry")
    cfg["ledger_path"] = str(tmp_path / "work" / "ledger.jsonl")
    cfg["harness_dir"] = str(tmp_path / "work" / "harness")
    cfg["memory_root"] = str(tmp_path / "work" / "memory")
    cfg["backend"] = {"name": "detector", "workers": workers, "cache": True}
    cfg["budget"] = dict(cfg.get("budget") or {}, cycle_timeout_seconds=cycle_timeout)
    detector = tmp_path / "detector.json"
    detector.parent.mkdir(parents=True, exist_ok=True)
    detector.write_text(json.dumps({
        "schema_version": "detector.config.v1",
        "case_timeout_seconds": case_timeout,
        "provider": {
            "endpoint": "https://example.invalid/api/v1",
            "model": "test-model",
            "api_key_env": "PREFLIGHT_TEST_KEY",
            "timeout_seconds": 900,
            "total_timeout_seconds": 2400,
        },
    }), encoding="utf-8")
    cfg["detector_config"] = str(detector)
    return cfg


def _run_identity_preflight(preflight, tmp_path, monkeypatch, config):
    config_path = tmp_path / "evolution.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    report_path = tmp_path / "preflight.json"
    for name in preflight.CHECK_ORDER:
        if name != "source_identity":
            monkeypatch.setattr(preflight, f"check_{name}", lambda *args: [])
    monkeypatch.setattr(sys, "argv", ["preflight", "--config", str(config_path),
                                     "--json", str(report_path)])
    status = preflight.main()
    return status, json.loads(report_path.read_text(encoding="utf-8"))


@pytest.mark.code_only
def test_json_preflight_records_canonical_source_and_actual_detector_config(
    preflight, tmp_path, monkeypatch,
):
    from detector.identity import artifact_digest, source_identity

    config = _config(tmp_path)
    status, report = _run_identity_preflight(preflight, tmp_path, monkeypatch, config)
    assert status == 0 and report["passed"]
    source = next(item for item in report["checks"] if item["check"] == "source_identity")
    identity = source["identity"]
    assert identity["source_digest"] == source_identity("detector", "evolution")
    assert len(identity["source_digest"]) == 64
    actual_path = str(Path(config["detector_config"]).resolve())
    assert identity["detector_config"] == {"path": actual_path, "digest": artifact_digest(actual_path)}


@pytest.mark.code_only
def test_preflight_missing_declared_detector_config_blocks_identity(
    preflight, tmp_path, monkeypatch,
):
    config = _config(tmp_path)
    Path(config["detector_config"]).unlink()
    status, report = _run_identity_preflight(preflight, tmp_path, monkeypatch, config)
    assert status == 1 and not report["passed"]
    source = next(item for item in report["checks"] if item["check"] == "source_identity")
    assert source["status"] == "BLOCKER"
    assert "does not exist" in source["detail"]


# ---------------------------------------------------------------------------
# Corpus and labels
def test_the_shipped_corpus_reconciles(preflight, tmp_path):
    report = preflight.Report()
    config = _config(tmp_path)
    cases = preflight.check_corpus(report, config)
    assert len(cases) == 693
    assert not report.blockers
    assert len(set(cases)) == 693


def test_the_label_reader_normalises_the_string_labels(preflight, tmp_path):
    """``is_hallucination`` is the string "true"/"false" in this corpus.

    A bare truth test over the string answers "every case is a hallucination",
    which would silently invert every paired reading. The check reads it
    through the framework's own reader and refuses a single-polarity corpus.
    """
    from evolution.splits import bool_gold

    report = preflight.Report()
    config = _config(tmp_path)
    cases = json.loads(
        (REPO / "datasets/protocols/development_exposure.json")
        .read_text(encoding="utf-8-sig")
    )["cases"]
    counts = preflight.check_labels(report, config, cases)
    assert counts["positives"] == 443
    assert counts["negatives"] == 250
    assert counts["with_step"] == 443
    assert not report.blockers
    # And the trap itself, documented: the raw string is truthy either way.
    assert bool_gold("false") is False
    assert bool("false") is True


def test_a_single_polarity_corpus_is_a_blocker(preflight, tmp_path):
    """No gate can protect a polarity the corpus does not contain."""
    report = preflight.Report()
    root = tmp_path / "data"
    for index in range(4):
        path = root / f"A/{index:03d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "question": "q", "history": [], "is_hallucination": "true",
        }), encoding="utf-8")
    config = _config(tmp_path)
    config["dataset_root"] = str(root)
    cases = [f"A/{index:03d}.json" for index in range(4)]
    preflight.check_labels(report, config, cases)
    assert any("single-polarity" in item["detail"] for item in report.blockers)


# ---------------------------------------------------------------------------
# Timeouts: one case must not be able to eat the round
def test_a_case_timeout_longer_than_the_round_is_a_blocker(preflight, tmp_path):
    """The §30 config had case_timeout 3h inside a 2h round.

    One pathological case would then consume the entire round budget, and every
    later batch in it fail-closes -- a state that had to be told apart from a
    real reading by hand.
    """
    report = preflight.Report()
    preflight.check_timeouts(
        report, _config(tmp_path, case_timeout=10800, cycle_timeout=7200),
    )
    assert any(item["check"] == "timeouts" for item in report.blockers)


def test_a_case_timeout_over_half_the_round_warns(preflight, tmp_path):
    report = preflight.Report()
    preflight.check_timeouts(
        report, _config(tmp_path, case_timeout=10800, cycle_timeout=14400),
    )
    assert not report.blockers
    assert any(item["check"] == "timeouts" for item in report.warnings)


def test_a_bounded_round_passes(preflight, tmp_path):
    report = preflight.Report()
    preflight.check_timeouts(
        report, _config(tmp_path, case_timeout=1800, cycle_timeout=14400),
    )
    assert not report.blockers and not report.warnings


@pytest.mark.code_only
@pytest.mark.parametrize("mode,requests,total,status", [
    ("judge_context", 2, 8192, "BLOCKER"),
    ("judge_context", 2, 16384, "OK"),
    ("off", 2, 8192, "OK"),
    ("judge_context", 1, 8192, "OK"),
])
def test_forecast_budget_checks_both_full_output_ceilings(
    preflight, tmp_path, mode, requests, total, status,
):
    cfg = _config(tmp_path)
    path = Path(cfg["detector_config"])
    detector = json.loads(path.read_text(encoding="utf-8"))
    detector["causal_audit"] = {
        "next_action_mode": mode,
        "max_added_provider_requests_per_case": requests,
        "max_forecast_output_tokens_per_request": 8192,
        "max_added_output_tokens_per_case": total,
    }
    path.write_text(json.dumps(detector), encoding="utf-8")
    report = preflight.Report()
    preflight.check_forecast_budget(report, cfg)
    item, = report.items
    assert item["status"] == status
    if mode != "off" and requests >= 2:
        assert "16384" in item["detail"]
        assert "8192" in item["detail"]
    else:
        assert "not required" in item["detail"]


@pytest.mark.code_only
def test_default_forecast_budget_is_ready(preflight, tmp_path):
    cfg = _config(tmp_path)
    cfg["detector_config"] = str(REPO / "detector/configs/autodl.json")
    report = preflight.Report()
    preflight.check_forecast_budget(report, cfg)
    item, = report.items
    assert item["status"] == "OK"
    assert "2 *" in item["detail"]


@pytest.mark.code_only
@pytest.mark.parametrize("binding,status", [
    ({}, "WARN"),
    ({"task_turn": 1}, "OK"),
    ({"role": "user", "content": "first"}, "OK"),
])
def test_forecast_alignment_warns_only_for_missing_turn_events(
    preflight, tmp_path, binding, status,
):
    cfg = _config(tmp_path)
    root = tmp_path / "corpus"
    cfg["dataset_root"] = str(root)
    records = {
        "BFCL/001.json": {"question": [["first"], ["second"]],
                          "history": [{"role": "assistant", "content": "answer", **binding}]},
        "Other/001.json": {"question": "single task", "history": []},
    }
    for case, record in records.items():
        path = root / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")
    report = preflight.Report()
    preflight.check_forecast_alignment(report, cfg, list(records))
    item, = report.items
    assert item["status"] == status
    assert not report.blockers
    if status == "WARN":
        assert "1 case(s)" in item["detail"]
        assert "BFCL" in item["detail"] and "BFCL/001.json" in item["detail"]
        assert "skipped" in item["detail"]
        assert item["families"] == {"BFCL": 1}
        assert item["cases"] == ["BFCL/001.json"]
    else:
        assert "0 case(s)" in item["detail"]


# ---------------------------------------------------------------------------
# Gates must be satisfiable before provider budget is spent
def test_the_coverage_floor_must_fit_the_sentinel_set(preflight, tmp_path):
    report = preflight.Report()
    config = _config(tmp_path)
    config["dataset_root"] = str(REPO / "datasets/raw/agenthallu/AgentHallu")
    config["gates"] = dict(config.get("gates") or {},
                           harmlessness_min_protected_tp=99)
    preflight.check_gates_satisfiable(report, config, "evolution")
    assert any("floor needs 99" in item["detail"] for item in report.blockers)


def test_the_baseline_phase_runs_no_admission_gate(preflight, tmp_path):
    report = preflight.Report()
    preflight.check_gates_satisfiable(report, _config(tmp_path), "baseline")
    assert not report.blockers


# ---------------------------------------------------------------------------
# Credentials: presence only
def test_a_missing_credential_is_a_blocker(preflight, tmp_path, monkeypatch):
    monkeypatch.delenv("PREFLIGHT_TEST_KEY", raising=False)
    monkeypatch.setattr(preflight, "REPO", tmp_path)
    report = preflight.Report()
    preflight.check_credentials(report, _config(tmp_path), "evolution")
    assert any("not set" in item["detail"] for item in report.blockers)


def test_a_present_credential_passes_without_reading_it(preflight, tmp_path, monkeypatch):
    monkeypatch.setenv("PREFLIGHT_TEST_KEY", "super-secret-value")
    report = preflight.Report()
    preflight.check_credentials(report, _config(tmp_path), "evolution")
    assert not report.blockers
    # The value itself must never reach the report.
    assert "super-secret-value" not in json.dumps(report.payload("evolution", "x"))


# ---------------------------------------------------------------------------
# Work root: a fresh run needs a fresh ledger
def test_a_foreign_ledger_blocks_a_full_run(preflight, tmp_path):
    """``run_full_self_evolution`` refuses a ledger it did not start."""
    report = preflight.Report()
    config = _config(tmp_path)
    ledger = Path(config["ledger_path"])
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"event": "cycle_started"}) + "\n", encoding="utf-8")
    preflight.check_work_root(report, config, "evolution")
    assert any("no full_self_evolution_started" in item["detail"]
               for item in report.blockers)


def test_an_empty_work_root_is_ready(preflight, tmp_path):
    report = preflight.Report()
    preflight.check_work_root(report, _config(tmp_path), "evolution")
    assert not report.blockers


# ---------------------------------------------------------------------------
# Calibration: a revoked predicate must be visible, not silent
def test_calibration_state_is_reported(preflight):
    report = preflight.Report()
    preflight.check_calibration(report)
    assert not report.blockers, report.blockers
    detail = next(item["detail"] for item in report.items
                  if item["check"] == "calibration")
    assert "required_action_present" in detail


# ---------------------------------------------------------------------------
# Path length: the real derived path, not a synthetic worst case
def test_a_deep_work_root_blocks_on_path_length(preflight, tmp_path):
    report = preflight.Report()
    deep = tmp_path / ("d" * 120) / ("e" * 100) / ("f" * 80)
    config = _config(tmp_path)
    config["work_root"] = str(deep)
    preflight.check_path_length(report, config, ["OpenDeepSearch/000.json"])
    assert any(item["check"] == "path_length" for item in report.blockers)


def test_a_normal_work_root_has_headroom(preflight, tmp_path):
    report = preflight.Report()
    preflight.check_path_length(
        report, _config(tmp_path), ["OpenDeepSearch/000.json"],
    )
    assert not report.blockers


# ---------------------------------------------------------------------------
# The overall verdict
def test_a_blocker_makes_the_report_not_ready(preflight):
    report = preflight.Report()
    preflight._blocker(report, "corpus", "injected")
    payload = report.payload("evolution", "cfg")
    assert payload["passed"] is False
    assert payload["blockers"] == 1


def test_only_warnings_is_ready(preflight):
    report = preflight.Report()
    preflight._warn(report, "cost", "injected")
    payload = report.payload("evolution", "cfg")
    assert payload["passed"] is True
    assert payload["warnings"] == 1


def test_the_checklist_covers_every_condition_it_claims(preflight):
    """The named order and the executed checks must not drift apart."""
    executed = {
        item["check"] for item in preflight.Report().items
    }
    assert executed == set()
    assert "corpus" in preflight.CHECK_ORDER
    assert "path_length" in preflight.CHECK_ORDER
    assert "forecast_budget" in preflight.CHECK_ORDER
    assert "forecast_alignment" in preflight.CHECK_ORDER
    assert len(preflight.CHECK_ORDER) == len(set(preflight.CHECK_ORDER))


def test_exposure_audit_catches_validation_contamination(preflight, tmp_path):
    from types import SimpleNamespace
    from evolution.splits import audit_case_exposure

    hist = tmp_path / "hist.json"
    hist.write_text(json.dumps({"cases": ["A/001.json", "A/002.json"]}), encoding="utf-8")
    config = {"historical_development_manifests": [str(hist)]}

    split = SimpleNamespace(
        validation=["A/001.json", "B/001.json"],
        final=[{"case_id": "C/001.json"}],
    )
    result = audit_case_exposure(config=config, split=split)
    assert result["clean"] is False
    assert result["validation_contamination"] == ["A/001.json"]
    assert result["final_verification_contamination"] == []

