from __future__ import annotations

import json
from pathlib import Path

import pytest

from evolution_v3.backends import MockDetectorBackend
from evolution_v3.config import load_config
from evolution_v3.contracts import empty_registry, make_intervention
from evolution_v3.engine import EvolutionEngine
from evolution_v3.gates import marginal_selection
from evolution_v3.splits import EvolutionSplit, FinalHoldout


def _make_dataset(root: Path, n: int = 20) -> list[str]:
    cases = []
    for index in range(n):
        framework = "A"
        case_id = f"{framework}/{index:03d}.json"
        cases.append(case_id)
        path = root / case_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "question": f"task {index}",
                "history": [
                    {"step": 1, "role": "assistant", "content": "answer"}
                ],
                "is_hallucination": bool(index % 2 == 0),
                "hallucination_step": 1 if index % 2 == 0 else None,
                "true_answer": "gold",
            }),
            encoding="utf-8",
        )
    return cases


def _config(tmp_path: Path) -> dict:
    config = load_config()
    config["dataset_root"] = str(tmp_path / "data")
    config["split_manifest"] = str(tmp_path / "manifest.json")
    config["mini_split_manifest"] = str(tmp_path / "mini_manifest.json")
    config["work_root"] = str(tmp_path / "work")
    config["registry_dir"] = str(tmp_path / "registry")
    config["ledger_path"] = str(tmp_path / "ledger.jsonl")
    config["backend"]["name"] = "mock"
    config["backend"]["cache"] = False
    config["budget"]["patrol_cases"] = 8
    return config


def test_one_full_cycle_admits_only_marginal_candidate(tmp_path):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    patrol_true = [c for c in cases if c.endswith(("012.json", "014.json"))]
    gain_true = [c for c in cases if c.endswith(("004.json", "006.json"))]
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    base = {case: False for case in patrol_true + gain_true}
    overrides = {"IV-1-001": {case: True for case in gain_true}}
    backend = MockDetectorBackend(
        base_predictions=base,
        overrides=overrides,
    )
    engine = EvolutionEngine(
        config,
        backend=backend,
        split=split,
        mini=True,
    )
    report = engine.run_cycle(1)

    assert report["audit"]["passed"] is True
    assert report["patrol"]["stable_failure_families"], report["patrol"]
    assert [p["intervention_id"] for p in report["proposals"]][0] == "IV-1-001"
    assert len(report["proposals"]) >= 1
    assert report["marginal_selection"]["selected"] == ["IV-1-001"]
    assert report["active_after_cycle"] == ["IV-1-001"]
    assert report["ledger_integrity"]["valid"] is True
    assert report["empty_vs_active_on_g"]["marginal_cases"] >= 1
    assert engine.registry.entries[0]["type"] == "text_boundary"
    assert engine.registry.entries[0]["hook"] == "scanner.system"


def test_patrol_bootstrap_recovers_missed_failure_family(tmp_path, monkeypatch):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    config["budget"]["patrol_cases"] = 2
    config["budget"]["stable_failure_rerun_cases"] = 10
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    # The random patrol sample only sees two clean-looking cases; the planted
    # failures live deeper in the patrol pool and must be found by bootstrap.
    planted = [c for c in cases if c.endswith(("016.json", "018.json"))]
    gain_true = [c for c in cases if c.endswith(("004.json", "006.json"))]
    backend = MockDetectorBackend(
        base_predictions={case: False for case in planted + gain_true},
        overrides={"IV-1-001": {case: True for case in gain_true}},
    )
    engine = EvolutionEngine(config, backend=backend, split=split, mini=True)
    monkeypatch.setattr(
        engine.split, "patrol_sample", lambda cycle, n: cases[12:14]
    )
    report = engine.run_cycle(1)
    assert report["patrol"]["bootstrap"]["bootstrap_count"] > 0
    assert report["patrol"]["bootstrap"]["family_found"] is True
    assert report["marginal_selection"]["selected"] == ["IV-1-001"]


def test_joint_l1_l2_candidates_are_only_admitted_together(tmp_path):
    cases = _make_dataset(tmp_path / "data", n=8)
    config = _config(tmp_path)
    gain_cases = cases[2:6]
    gold_map = {
        case: bool(int(case.split("/")[1].split(".")[0]) % 2 == 0)
        for case in cases
    }
    l1 = make_intervention(
        intervention_id="IV-L1",
        intervention_type="text_boundary",
        hook="scanner.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": "Report missing required actions.",
            "not_when": "Never when all required actions are present.",
        },
        expected_behavior="recall gain on planted FN",
        target_polarity="recall",
        cycle=1,
        failure_family="FAM-fn",
        failure_cases=cases[:2],
    )
    l2 = make_intervention(
        intervention_id="IV-L2",
        intervention_type="decision_guard",
        hook="adjudicate.guard",
        packet_features=["has_tool_calls"],
        facts=["positive_source=recall_upgrade"],
        body={
            "action": "withhold_conviction",
            "action_not_when": "Never when defense substantiated.",
        },
        expected_behavior="precision gain on planted FP",
        target_polarity="precision",
        cycle=1,
        failure_family="FAM-fp",
        failure_cases=cases[:2],
    )
    base = {
        gain_cases[0]: False,  # planted FN: gold true
        gain_cases[3]: True,   # planted FP: gold false
    }
    overrides = {
        "IV-L1": {gain_cases[0]: True},
        "IV-L2": {gain_cases[3]: False},
    }
    backend = MockDetectorBackend(base_predictions=base, overrides=overrides)
    active_rows = backend.evaluate(
        gain_cases,
        dataset_root=config["dataset_root"],
        registry=empty_registry(),
        gold_map=gold_map,
    )
    selection = marginal_selection(
        [l1, l2],
        active_entries=[],
        backend=backend,
        dataset_root=config["dataset_root"],
        gain_cases=gain_cases,
        active_rows=active_rows,
        config=config,
        gold_map=gold_map,
        max_admits=2,
        joint_groups=[["IV-L1", "IV-L2"]],
    )
    assert selection["reports"] == []  # joint members are not sold separately
    assert selection["joint_reports"][0]["passed"] is True
    assert selection["joint_reports"][0]["selected"] is True
    assert {c["intervention_id"] for c in selection["selected"]} == {
        "IV-L1", "IV-L2"
    }


def test_final_verification_is_read_once_and_writes_report(tmp_path):
    cases = _make_dataset(tmp_path / "data", n=6)
    config = _config(tmp_path)
    config["final_manifest"] = str(tmp_path / "final.json")
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:2],
        gain=cases[2:4],
        patrol_pool=cases[4:],
        final=[{"case_id": case, "gold": bool(i % 2 == 0)} for i, case in enumerate(cases)],
        final_holdout=FinalHoldout([
            {"case_id": case, "gold": bool(i % 2 == 0)} for i, case in enumerate(cases)
        ]),
        mini=False,
    )
    backend = MockDetectorBackend()
    engine = EvolutionEngine(config, backend=backend, split=split, mini=False)
    report = engine.run_final(noise_reruns=2)
    assert report["final_cases"] == 6
    assert report["verdict"]["unresolved_not_increased"] is True
    assert engine.split.final_holdout.reads == 1
    with pytest.raises(RuntimeError):
        engine.run_final(noise_reruns=2)


def test_defect_injection_audit_freezes_on_leak_through(tmp_path):
    # A defect candidate that would pass M is simulated by giving the noise
    # defect an override that actually gains a case. The engine must freeze
    # before accepting or running normal proposals.
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    gain_true = [c for c in cases if c.endswith(("004.json", "006.json"))]
    overrides = {"IV-DEFECT-1-002": {case: True for case in gain_true}}
    backend = MockDetectorBackend(
        base_predictions={case: False for case in gain_true},
        overrides=overrides,
    )
    engine = EvolutionEngine(
        config,
        backend=backend,
        split=split,
        mini=True,
    )
    report = engine.run_cycle(1)
    assert report["state"] == "frozen_after_defect_injection_audit"
    assert report["audit"]["passed"] is False
    assert engine.registry.entries == []
