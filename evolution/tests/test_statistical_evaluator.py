"""Tests for statistical evaluation, repeated A/B harness, and V3 promotion protocol (PR-08, PR-09 / C13, C14, C15)."""

from __future__ import annotations

import copy
import pytest

from evolution.contracts import make_intervention, make_registry
from evolution.gates import (
    meets_resolution,
    resolution_behavior_gate,
)
from evolution.promotion_alignment import (
    ALIGNED_CANDIDATE_SCHEMA,
    ALIGNED_PROTOCOL_V3_SCHEMA,
    MEMBERSHIP_SCHEMA,
    build_aligned_protocol_v3,
    digest,
    verify_alignment,
)
from evolution.statistical_evaluator import (
    REPEATED_AB_SCHEMA,
    STATISTICAL_GATE_SCHEMA,
    STATUS_FAIL,
    STATUS_INCONCLUSIVE,
    STATUS_PASS,
    cluster_cases_paired_delta,
    repeated_ab_evaluate,
    statistical_gate_evaluate,
)


def _make_run_row(case: str, gold: bool, pred: bool | None, *, healthy: bool = True) -> dict:
    row = {
        "case": case,
        "gold": gold,
        "prediction": pred,
        "authority": "authoritative" if healthy and pred is not None else "degraded",
        "measurement_status": "healthy" if healthy else "infrastructure_failure",
        "result": {
            "proof": {
                "deployment": {
                    "fired": [
                        {
                            "intervention_id": "IV-RES-01",
                            "hook": "judge.post",
                            "event": "guard_fired",
                        }
                    ]
                }
            }
        },
    }
    return row


def test_cluster_cases_paired_delta_empty():
    res = cluster_cases_paired_delta([], [])
    assert res["n_cases"] == 0
    assert res["tau_c"] == 0.0
    assert res["lcb_tau_c"] == 0.0
    assert res["delta_fp"] == 0.0
    assert res["delta_fn"] == 0.0


def test_cluster_cases_paired_delta_aa_null():
    # Repeated A/A runs: baseline and candidate are identical
    cases = [f"case_{i:02d}.json" for i in range(12)]
    run1 = [_make_run_row(c, gold=True, pred=True) for c in cases]
    run2 = [_make_run_row(c, gold=True, pred=True) for c in cases]

    res = cluster_cases_paired_delta([run1, run2], [run1, run2])
    assert res["n_cases"] == 12
    assert res["tau_c"] == 0.0
    assert res["lcb_tau_c"] <= 0.0
    assert res["delta_fp"] == 0.0
    assert res["delta_fn"] == 0.0


def test_cluster_cases_paired_delta_positive_effect():
    # 15 positive cases: baseline gets wrong/unresolved (pred=False), candidate gets right (pred=True)
    cases = [f"case_{i:02d}.json" for i in range(15)]
    run_base = [_make_run_row(c, gold=True, pred=False) for c in cases]
    run_cand = [_make_run_row(c, gold=True, pred=True) for c in cases]

    res = cluster_cases_paired_delta([run_base], [run_cand])
    assert res["n_cases"] == 15
    assert res["tau_c"] == 1.0
    assert res["lcb_tau_c"] == 1.0  # Zero variance across cases -> LCB == tau_c == 1.0
    assert res["delta_fn"] == -1.0  # FN reduced by 1.0


def test_cluster_cases_paired_delta_harmful_fp():
    # 10 negative cases: baseline pred=False (clean, correct), candidate pred=True (FP)
    cases = [f"neg_{i:02d}.json" for i in range(10)]
    run_base = [_make_run_row(c, gold=False, pred=False) for c in cases]
    run_cand = [_make_run_row(c, gold=False, pred=True) for c in cases]

    res = cluster_cases_paired_delta([run_base], [run_cand])
    assert res["n_negative_cases"] == 10
    assert res["delta_fp"] == 1.0
    assert res["ucb_delta_fp"] >= 1.0


def test_statistical_gate_inconclusive_sample_size():
    # Only 4 cases, min_effective_sample_size=10
    cases = [f"case_{i}.json" for i in range(4)]
    run_base = [_make_run_row(c, gold=True, pred=False) for c in cases]
    run_cand = [_make_run_row(c, gold=True, pred=True) for c in cases]

    verdict = statistical_gate_evaluate(
        [run_base], [run_cand], min_effective_sample_size=10,
    )
    assert verdict["status"] == STATUS_INCONCLUSIVE
    assert verdict["passed"] is False
    assert verdict["evaluable"] is False
    assert "insufficient_sample_size" in verdict["reason"]


def test_statistical_gate_health_regression():
    cases = [f"case_{i}.json" for i in range(12)]
    run_base = [_make_run_row(c, gold=True, pred=True) for c in cases]
    # Candidate experiences infrastructure failure on one case
    run_cand = [_make_run_row(c, gold=True, pred=True, healthy=(i != 0)) for i, c in enumerate(cases)]

    verdict = statistical_gate_evaluate([run_base], [run_cand], min_effective_sample_size=10)
    assert verdict["status"] == STATUS_FAIL
    assert verdict["passed"] is False
    assert "health_regressed" in verdict["reason"]


def test_statistical_gate_fp_regression_fails():
    # 10 positive cases + 10 negative cases
    pos_cases = [f"pos_{i}.json" for i in range(10)]
    neg_cases = [f"neg_{i}.json" for i in range(10)]
    all_cases = pos_cases + neg_cases

    run_base = (
        [_make_run_row(c, gold=True, pred=True) for c in pos_cases]
        + [_make_run_row(c, gold=False, pred=False) for c in neg_cases]
    )
    # Candidate introduces FP on 3 negative cases
    run_cand = (
        [_make_run_row(c, gold=True, pred=True) for c in pos_cases]
        + [_make_run_row(c, gold=False, pred=(i < 3)) for i, c in enumerate(neg_cases)]
    )

    verdict = statistical_gate_evaluate(
        [run_base], [run_cand],
        min_effective_sample_size=10,
        epsilon_fp=0.035,
    )
    assert verdict["status"] == STATUS_FAIL
    assert "ucb_delta_fp" in verdict["reason"]


def test_statistical_gate_insufficient_gain_fails():
    # Moderate gain: 2 cases improved out of 12, but delta_min requires 0.3
    cases = [f"case_{i}.json" for i in range(12)]
    run_base = [_make_run_row(c, gold=True, pred=False) for c in cases]
    run_cand = [_make_run_row(c, gold=True, pred=(i < 2)) for i, c in enumerate(cases)]

    verdict = statistical_gate_evaluate(
        [run_base], [run_cand],
        min_effective_sample_size=10,
        delta_min=0.3,
    )
    assert verdict["status"] == STATUS_FAIL
    assert "lcb_tau_c" in verdict["reason"]


def test_statistical_gate_pass_and_repeated_ab_evaluate():
    # 15 positive cases: baseline gets 0 right, candidate gets all right
    cases = [f"case_{i}.json" for i in range(15)]
    run_base1 = [_make_run_row(c, gold=True, pred=False) for c in cases]
    run_base2 = [_make_run_row(c, gold=True, pred=False) for c in cases]
    run_cand1 = [_make_run_row(c, gold=True, pred=True) for c in cases]
    run_cand2 = [_make_run_row(c, gold=True, pred=True) for c in cases]

    verdict = statistical_gate_evaluate(
        [run_base1, run_base2],
        [run_cand1, run_cand2],
        min_effective_sample_size=10,
        delta_min=0.0,
    )
    assert verdict["status"] == STATUS_PASS
    assert verdict["passed"] is True
    assert verdict["schema_version"] == STATISTICAL_GATE_SCHEMA

    # repeated_ab_evaluate wrapper
    protocol = {
        "protocol_hash": "test_protocol_hash_123",
        "statistical_params": {
            "alpha_g": 0.05,
            "alpha_h": 0.05,
            "delta_min": 0.0,
            "epsilon_fp": 0.035,
            "epsilon_fn": 0.035,
            "min_effective_sample_size": 10,
        },
    }
    ab_report = repeated_ab_evaluate(
        [run_base1, run_base2],
        [run_cand1, run_cand2],
        protocol,
    )
    assert ab_report["schema_version"] == REPEATED_AB_SCHEMA
    assert ab_report["protocol_hash"] == "test_protocol_hash_123"
    assert ab_report["repeats_baseline"] == 2
    assert ab_report["repeats_candidate"] == 2
    assert ab_report["passed"] is True


def test_v3_protocol_build_and_verify(tmp_path):
    # Setup mock dataset
    case1 = tmp_path / "A" / "001.json"
    case2 = tmp_path / "A" / "002.json"
    case3 = tmp_path / "P" / "001.json"
    case4 = tmp_path / "P" / "002.json"
    case5 = tmp_path / "C" / "001.json"
    for p in (case1, case2, case3, case4, case5):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")

    cases = ["A/001.json", "A/002.json", "P/001.json", "P/002.json", "C/001.json"]
    roles = {
        "A/001.json": "benefit", "A/002.json": "benefit",
        "P/001.json": "protection", "P/002.json": "protection",
        "C/001.json": "challenge",
    }
    gold = {c: True for c in cases}

    mechanism = {
        "schema_version": "evolution.failure_mechanism.v1",
        "status": "confirmed",
        "objective": "resolution",
        "member_cases": ["DISC/001.json", "DISC/002.json"],
        "task_obligation": {"kind": "completion_claim"},
        "observed_deviation": {"kind": "unresolved"},
        "detector_failure": {"stage": "hearing", "failure_mode": "unresolved"},
        "repair_hypothesis": {"operation": "scoped_resolution"},
    }
    from evolution.mechanisms import mechanism_key
    mechanism["mechanism_id"] = mechanism_key(mechanism)
    m_id = mechanism["mechanism_id"]
    membership = {
        "A/001.json": {"evidence": ["doc"], "status": "confirmed", "mechanism_id": m_id, "repairable": True},
        "A/002.json": {"evidence": ["doc"], "status": "confirmed", "mechanism_id": m_id, "repairable": True},
        "P/001.json": {"evidence": ["doc"], "status": "confirmed", "mechanism_id": m_id, "repairable": False},
        "P/002.json": {"evidence": ["doc"], "status": "confirmed", "mechanism_id": m_id, "repairable": False},
        "C/001.json": {"evidence": ["doc"], "status": "external", "mechanism_id": "OTHER", "repairable": False},
    }
    candidate = {
        "schema_version": ALIGNED_CANDIDATE_SCHEMA,
        "content_hash": "cand_hash_123",
        "target_mechanism_id": m_id,
        "mechanism": mechanism,
        "target_kind": "resolution",
        "development_cases": ["DEV/001.json"],
    }
    base_protocol = {
        "cases": cases,
        "roles": roles,
        "gold": gold,
        "development_cases": ["DEV/001.json"],
        "workers": 1,
    }
    config = {
        "gates": {
            "harmlessness_new_fn_allowed": 0,
            "harmlessness_new_fp_allowed": 0,
            "harmlessness_new_unresolved_allowed": 0,
            "m_new_errors_allowed": 0,
            "localization_joint_broken_allowed": 0,
            "harmlessness_min_protected_tp": 2,
            "harmlessness_min_protected_tn": 2,
            "behavior_require_fired": True,
            "m_min_marginal_cases": 1,
            "localization_min_joint_cases": 1,
        },
        "library": {},
    }

    v3_proto = build_aligned_protocol_v3(
        base_protocol,
        candidate=candidate,
        mechanism=mechanism,
        membership=membership,
        dataset_root=tmp_path,
        config=config,
        baseline_registry_hash="base_reg_hash_999",
        statistical_params={
            "alpha_g": 0.05,
            "alpha_h": 0.05,
            "delta_min": 0.01,
            "epsilon_fp": 0.035,
            "epsilon_fn": 0.035,
            "min_effective_sample_size": 10,
        },
    )

    assert v3_proto["schema_version"] == ALIGNED_PROTOCOL_V3_SCHEMA
    assert v3_proto["statistical_params"]["delta_min"] == 0.01
    # Verify alignment succeeds
    verify_alignment(v3_proto)

    # Tampering test: modify statistical params without updating hash
    tampered = copy.deepcopy(v3_proto)
    tampered["statistical_params"]["epsilon_fp"] = 0.5
    with pytest.raises(ValueError, match="content hash mismatch"):
        verify_alignment(tampered)

    # Invalid statistical param value raises ValueError at build
    with pytest.raises(ValueError, match="alpha_g must be in"):
        build_aligned_protocol_v3(
            base_protocol,
            candidate=candidate,
            mechanism=mechanism,
            membership=membership,
            dataset_root=tmp_path,
            config=config,
            baseline_registry_hash="base_reg_hash_999",
            statistical_params={"alpha_g": 1.5},
        )


def test_resolution_behavior_gate_and_meets_resolution():
    candidate = {
        "intervention_id": "IV-RES-01",
        "type": "decision_guard",
        "claim": {"target_polarity": "resolution"},
    }
    config = {
        "gates": {
            "behavior_require_fired": True,
            "resolution_min_resolved_cases": 1,
            "m_new_errors_allowed": 0,
        }
    }

    # Case 1: Unresolved in baseline, resolved to correct True in candidate
    # Case 2: Clean False in both
    row_base_1 = _make_run_row("case_1.json", gold=True, pred=None)  # unresolved
    row_base_2 = _make_run_row("case_2.json", gold=False, pred=False)  # correct clean
    row_cand_1 = _make_run_row("case_1.json", gold=True, pred=True)  # resolved to correct True
    row_cand_2 = _make_run_row("case_2.json", gold=False, pred=False)  # preserved clean

    # Should PASS resolution behavior gate
    res_b = resolution_behavior_gate(
        candidate,
        baseline_rows=[row_base_1, row_base_2],
        candidate_rows=[row_cand_1, row_cand_2],
        config=config,
    )
    assert res_b["passed"] is True
    assert "resolved_correct=1" in res_b["behavior_note"]

    # Should PASS meets_resolution
    passed_m, failures = meets_resolution(res_b["delta"], config=config)
    assert passed_m is True
    assert not failures

    # Failure mode 1: candidate did not resolve any case
    res_no_resolve = resolution_behavior_gate(
        candidate,
        baseline_rows=[row_base_1, row_base_2],
        candidate_rows=[row_base_1, row_base_2],  # identical to baseline
        config=config,
    )
    assert res_no_resolve["passed"] is False
    assert "resolved no unresolved case" in res_no_resolve["reason"]

    # Failure mode 2: candidate broke existing authoritative decision
    row_cand_broken_2 = _make_run_row("case_2.json", gold=False, pred=True)  # new FP
    res_broken = resolution_behavior_gate(
        candidate,
        baseline_rows=[row_base_1, row_base_2],
        candidate_rows=[row_cand_1, row_cand_broken_2],
        config=config,
    )
    assert res_broken["passed"] is False
    assert "harmed existing authoritative decisions" in res_broken["reason"]
