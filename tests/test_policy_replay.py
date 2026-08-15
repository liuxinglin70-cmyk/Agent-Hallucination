"""Offline policy replay: binding validation and adjudication semantics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sehd_rise2.evolution.replay import (
    BINDINGS_SCHEMA,
    baseline_matches_records,
    class_actions,
    evaluate_config,
    must_survive_violations,
    replay_case,
    surviving_ids,
    validate_bindings,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DIR = REPO_ROOT / "datasets/policy_candidates/v1"
REAL_RUN = REPO_ROOT / "runs/random100_20260815_142446"


def _result(*, state, supported, upheld):
    return {
        "state": state,
        "proof_adjudication": {
            "census": [
                {"candidate_id": f"c{i + 1}", "claim": "claim"}
                for i in range(max(len(supported), len(upheld)))
            ],
            "certificates": [
                {"candidate_id": cid, "verdict": "supported",
                 "rationale": "r"}
                for cid in supported
            ],
            "defense_decisions": {
                cid: {"verdict": verdict, "rationale": "r"}
                for cid, verdict in upheld.items()
            },
        },
    }


def _tree():
    return {
        "A/001.json": {
            "gold": False,
            "result": _result(
                state="verified_error", supported=["c1"],
                upheld={"c1": "upheld"},
            ),
        },
        "A/002.json": {
            "gold": True,
            "result": _result(
                state="verified_error", supported=["c1", "c2"],
                upheld={"c1": "upheld", "c2": "upheld"},
            ),
        },
        "A/003.json": {
            "gold": True,
            "result": _result(
                state="verified_clean", supported=["c1"],
                upheld={"c1": "overturned"},
            ),
        },
    }


def _bindings(tree):
    return {
        (case_id, candidate_id): {
            "certificate_class": "content_contradiction",
            "basis": "b",
        }
        for case_id, entry in tree.items()
        for candidate_id in surviving_ids(entry["result"])
    }


def _payload():
    return {"schema_version": BINDINGS_SCHEMA, "bindings": None}


def test_surviving_ids_intersect_support_and_uphold():
    result = _result(
        state="verified_error", supported=["c1", "c2"],
        upheld={"c1": "upheld", "c2": "overturned"},
    )
    assert surviving_ids(result) == ["c1"]


def test_withhold_removes_sole_conviction():
    outcome = replay_case(
        ["presentational_addition"],
        {"presentational_addition": "withhold_conviction"},
    )
    assert outcome["state"] == "verified_clean"
    assert outcome["policy_evaluation"]["fired"][0]["effect"] == "withheld"


def test_corroboration_keeps_conviction_with_content_certificate():
    outcome = replay_case(
        ["requirement_compliance", "content_contradiction"],
        {"requirement_compliance":
         "require_corroborating_content_error"},
    )
    assert outcome["state"] == "verified_error"
    effects = {
        entry["effect"]
        for block in outcome["policy_evaluation"].values()
        for entry in block
    }
    assert "corroborated" in effects


def test_corroboration_removes_sole_basis():
    outcome = replay_case(
        ["requirement_compliance"],
        {"requirement_compliance":
         "require_corroborating_content_error"},
    )
    assert outcome["state"] == "verified_clean"
    assert outcome["policy_evaluation"]["fired"][0][
        "effect"
    ] == "sole_basis_removed"


def test_inactive_class_passes_through():
    outcome = replay_case(["presentational_addition"], {})
    assert outcome["state"] == "verified_error"


def test_withhold_dominates_corroboration_for_same_class():
    actions = class_actions([
        {"trigger": {"certificate_class": "x"},
         "action": "require_corroborating_content_error"},
        {"trigger": {"certificate_class": "x"},
         "action": "withhold_conviction"},
    ])
    assert actions["x"] == "withhold_conviction"


def test_baseline_rule_must_reproduce_recorded_states():
    tree = _tree()
    assert baseline_matches_records(tree)
    tree["A/003.json"]["result"]["state"] = "verified_error"
    assert not baseline_matches_records(tree)


def test_bindings_must_cover_exactly_the_surviving_set():
    tree = _tree()
    payload = _payload()
    bindings = _bindings(tree)
    checks = validate_bindings(payload, bindings, tree)
    assert all(checks.values())
    del bindings[("A/001.json", "c1")]
    assert not validate_bindings(
        payload, bindings, tree,
    )["surviving_fully_bound"]
    bindings = _bindings(tree)
    bindings[("A/003.json", "c1")] = {
        "certificate_class": "content_contradiction", "basis": "b",
    }
    assert not validate_bindings(
        payload, bindings, tree,
    )["surviving_fully_bound"]
    bindings = _bindings(tree)
    bindings[("A/001.json", "c1")]["certificate_class"] = "vague"
    assert not validate_bindings(
        payload, bindings, tree,
    )["classes_in_vocabulary"]
    bindings = _bindings(tree)
    bindings[("A/001.json", "c1")]["basis"] = ""
    assert not validate_bindings(payload, bindings, tree)["basis_stated"]


def test_evaluate_config_scores_against_gold():
    tree = _tree()
    bindings = _bindings(tree)
    baseline = {
        case_id: entry["result"]["state"]
        for case_id, entry in tree.items()
    }
    config = evaluate_config(
        name="x", policies=[], run_tree=tree, bindings=bindings,
        baseline=baseline,
    )
    assert (config["tp"], config["fp"], config["fn"], config["tn"]) == (
        1, 1, 1, 0,
    )
    bindings[("A/001.json", "c1")]["certificate_class"] = (
        "presentational_addition"
    )
    config = evaluate_config(
        name="y",
        policies=[{
            "policy_id": "P", "trigger": {
                "certificate_class": "presentational_addition",
            }, "action": "withhold_conviction",
        }],
        run_tree=tree, bindings=bindings, baseline=baseline,
    )
    assert config["fp"] == 0
    assert config["fp_fixed"] == ["A/001.json"]
    assert config["decision_changes"] == 1


def test_must_survive_violation_recorded_when_acquitted():
    policies = [{
        "policy_id": "P",
        "trigger": {"certificate_class": "x"},
        "action": "withhold_conviction",
        "expected_effect": {
            "tp_must_survive": [{"case_id": "A/002.json"}],
        },
    }]
    assert must_survive_violations(
        policies, {"A/002.json": "verified_clean"},
    ) == [{"policy_id": "P", "case_id": "A/002.json"}]
    assert must_survive_violations(
        policies, {"A/002.json": "verified_error"},
    ) == []


@pytest.mark.skipif(
    not REAL_RUN.exists() or not (REAL_DIR / "certificate_bindings.json"
                                  ).exists(),
    reason="run tree or binding layer not present",
)
def test_real_binding_layer_validates():
    from sehd_rise2.evolution.replay import load_run_tree, load_bindings
    tree = load_run_tree(REAL_RUN)
    bindings, payload = load_bindings(
        REAL_DIR / "certificate_bindings.json",
    )
    checks = validate_bindings(payload, bindings, tree)
    failed = [name for name, ok in checks.items() if not ok]
    assert failed == []


@pytest.mark.skipif(
    not (REAL_DIR / "replay_result.json").exists(),
    reason="replay result not generated",
)
def test_real_replay_metrics_match_reported_summary():
    result = json.loads(
        (REAL_DIR / "replay_result.json").read_text(encoding="utf-8"),
    )
    configs = {
        config["configuration"]: config
        for config in result["configurations"]
    }
    baseline = configs["baseline"]
    assert (baseline["tp"], baseline["fp"]) == (50, 12)
    assert baseline["accuracy"] == 0.76
    full = configs["full_set"]
    assert full["tp"] == 50
    assert full["fp"] == 1
    assert full["fn"] == 12
    assert full["tp_lost"] == []
    assert full["accuracy"] == 0.87
    for name in ("L2-P1", "L2-P2", "L2-P3", "L2-P4", "L2-P5"):
        assert configs[name]["tp_lost"] == []
        assert configs[name]["fn"] == baseline["fn"]
        assert configs[name]["accuracy"] >= baseline["accuracy"]
    for config in result["configurations"]:
        assert config["must_survive_violations"] == []
        assert config["tp_lost"] == []
