"""Wave2-F: L2 adjudication policy hook on the v3.5 proof court.

Covers the contract that ``adjudicate_proof_court`` stays byte-identical for
existing callers and that deterministic coverage-validity policies flip only
the terminal state, recomputed with the court's own full rule.
"""

from __future__ import annotations

import copy

from src.sehd_v6.core.bccse.v6_contracts import PolicyItem
from src.sehd_v6.core.paca_tcl import proof_contract


def _packet() -> dict:
    return {
        "events": [
            {"event_id": "event_001", "tool_interactions": []},
            {"event_id": "event_002", "tool_interactions": []},
        ]
    }


def _candidate() -> dict:
    return {
        "candidate_id": "candidate_001",
        "origin_hypothesis_event_id": "event_001",
    }


def _process_only_error_certificate() -> dict:
    # process_only keeps the certificate out of the surviving pool while it
    # still counts the candidate as auxiliary-covered, which is the only way
    # to reach the empty-shell complete verdict (defense coverage complete,
    # zero valid defense certificates, no surviving errors).
    return {
        "certificate_id": "error_001",
        "source_candidate_id": "candidate_001",
        "origin_event_id": "event_001",
        "claim_kind": "agent_assertion",
        "norm_source": "prior_trajectory_evidence",
        "norm_evidence_refs": ["event_001"],
        "breach_type": "fabrication",
        "violation_evidence_refs": ["event_001"],
        "agent_control_event_id": "event_001",
        "agent_control_evidence_refs": ["event_001"],
        "impact_kind": "process_debt",
        "impact_event_ids": ["event_002"],
        "impact_evidence_refs": ["event_002"],
        "recovery_state": "none",
        "recovery_evidence_refs": [],
        "benchmark_causal_status": "process_only",
        "benchmark_causal_evidence_refs": [],
        "rationale": "The assertion lacks trajectory support but carries no causal impact.",
    }


def _court_inputs() -> dict:
    return {
        "required_candidates": [_candidate()],
        "prosecution": {
            "coverage_state": "complete",
            "candidates": [_process_only_error_certificate()],
        },
        "defense": {"coverage_state": "complete", "candidates": []},
        "packet": _packet(),
    }


def _adjudicate(**overrides):
    kwargs = _court_inputs()
    kwargs.update(overrides)
    return proof_contract.adjudicate_proof_court(**kwargs)


def test_policy_items_none_matches_baseline_exactly() -> None:
    baseline = _adjudicate()
    with_none = _adjudicate(policy_items=None)
    assert baseline == with_none
    assert "policy_evaluation" not in baseline
    assert "pre_policy_state" not in baseline


def test_empty_policy_list_matches_baseline_exactly() -> None:
    baseline = _adjudicate()
    with_empty = _adjudicate(policy_items=[])
    assert baseline == with_empty


def test_empty_shell_clean_baseline_then_invalidate_coverage_flips_unresolved() -> None:
    baseline = _adjudicate()
    # Guard that the fixture really exercises the empty-shell complete branch.
    assert baseline["state"] == "verified_clean"
    assert baseline["valid_defense_certificate_count"] == 0
    assert baseline["surviving_error_certificate_ids"] == []
    assert baseline["defense_coverage"] == "complete"

    policy = PolicyItem(
        item_id="policy_invalidate_defense",
        precondition="defense_coverage_complete_and_zero_certificates",
        action="invalidate_coverage_claim",
        params={"side": "defense"},
    )
    result = _adjudicate(policy_items=[policy])

    assert result["state"] == "unresolved"
    assert result["pre_policy_state"] == "verified_clean"
    evaluation = result["policy_evaluation"]
    assert evaluation["fired"][0]["item_id"] == "policy_invalidate_defense"
    assert evaluation["effective_defense_coverage"] == "unresolved"
    # The recorded court coverages are facts and must not be rewritten.
    assert result["defense_coverage"] == "complete"
    assert result["prosecution_coverage"] == "complete"


def test_downgrade_coverage_defense_flips_unresolved() -> None:
    policy = PolicyItem(
        item_id="policy_downgrade_defense",
        precondition="defense_coverage_complete_and_zero_certificates",
        action="downgrade_coverage",
        params={"side": "defense", "to_state": "incomplete"},
    )
    result = _adjudicate(policy_items=[policy])

    assert result["state"] == "unresolved"
    assert result["pre_policy_state"] == "verified_clean"
    evaluation = result["policy_evaluation"]
    assert evaluation["fired"][0]["item_id"] == "policy_downgrade_defense"
    assert evaluation["effective_defense_coverage"] == "incomplete"


def test_telemetry_only_policy_leaves_state_untouched() -> None:
    policy = PolicyItem(
        item_id="policy_telemetry",
        precondition="defense_coverage_complete_and_zero_certificates",
        action="emit_telemetry_only",
        params={},
        rationale="flag empty-shell complete verdicts for review",
    )
    baseline = _adjudicate()
    result = _adjudicate(policy_items=[policy])

    evaluation = result["policy_evaluation"]
    assert evaluation["fired"][0]["item_id"] == "policy_telemetry"
    assert evaluation["telemetry"][0]["item_id"] == "policy_telemetry"
    assert evaluation["effective_defense_coverage"] == "complete"
    assert evaluation["effective_prosecution_coverage"] == "complete"
    assert result["state"] == baseline["state"] == "verified_clean"
    assert "pre_policy_state" not in result
    telemetry_only = copy.deepcopy(result)
    del telemetry_only["policy_evaluation"]
    assert telemetry_only == baseline


def test_boundary_path_state_enables_conflict_predicate_telemetry() -> None:
    # Incomplete defense coverage yields an unresolved verdict with no
    # certificates at all, so the boundary conflict is observable but the
    # telemetry-only action must not change the terminal state.
    inputs = {
        "required_candidates": [],
        "prosecution": {"coverage_state": "complete", "candidates": []},
        "defense": {"coverage_state": "incomplete", "candidates": []},
        "packet": _packet(),
    }
    baseline = proof_contract.adjudicate_proof_court(**inputs)
    assert baseline["state"] == "unresolved"

    policy = PolicyItem(
        item_id="policy_boundary_conflict",
        precondition="boundary_state_conflicts_with_terminal",
        action="emit_telemetry_only",
        params={},
    )
    result = proof_contract.adjudicate_proof_court(
        **inputs, policy_items=[policy], boundary_path_state="verified_clean",
    )

    evaluation = result["policy_evaluation"]
    assert evaluation["fired"][0]["item_id"] == "policy_boundary_conflict"
    assert evaluation["fired"][0]["precondition"] == "boundary_state_conflicts_with_terminal"
    assert evaluation["telemetry"][0]["item_id"] == "policy_boundary_conflict"
    assert result["state"] == "unresolved"
    assert "pre_policy_state" not in result

    # Without the engine-level boundary fact the same predicate cannot fire.
    without_boundary = proof_contract.adjudicate_proof_court(
        **inputs, policy_items=[policy],
    )
    assert without_boundary["policy_evaluation"]["fired"] == []
