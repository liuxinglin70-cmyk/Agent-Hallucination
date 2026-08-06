"""L2 adjudication policy hook on the v3.5 proof court.

Covers the contract that ``adjudicate_proof_court`` stays byte-identical for
existing callers and that deterministic coverage-validity policies flip only
the terminal state, recomputed with the court's own full rule.
"""

from __future__ import annotations

import copy

from src.sehd_v6.core.bccse.v6_contracts import PolicyItem
from src.sehd_v6.core.bccse.signal_adapter import (
    resolve_authoritative_state,
    resolve_evolution_state,
)
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


def test_policy_terminal_state_is_visible_to_evolution_not_occurrence_metrics() -> None:
    policy = PolicyItem(
        item_id="policy_downgrade_defense",
        precondition="defense_coverage_complete_and_zero_certificates",
        action="downgrade_coverage",
        params={"side": "defense", "to_state": "incomplete"},
    )
    result = _adjudicate(policy_items=[policy])
    row = {
        "state": result["state"],
        "occurrence_state": result["occurrence_state"],
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "proof_adjudication": result,
    }
    assert resolve_authoritative_state(row) == "verified_clean"
    assert resolve_evolution_state(row) == "unresolved"


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


def _unresolved_boundary_conflict_inputs() -> dict:
    # Incomplete defense coverage keeps the court unresolved with zero
    # surviving errors while the engine-level boundary path verified clean.
    return {
        "required_candidates": [],
        "prosecution": {"coverage_state": "complete", "candidates": []},
        "defense": {"coverage_state": "incomplete", "candidates": []},
        "packet": _packet(),
    }


def _consume_boundary_policy() -> PolicyItem:
    return PolicyItem(
        item_id="policy_consume_boundary",
        precondition="boundary_state_conflicts_with_terminal",
        action="consume_verified_clean_boundary",
        params={},
    )


def test_consume_boundary_moves_unresolved_to_verified_clean() -> None:
    inputs = _unresolved_boundary_conflict_inputs()
    baseline = proof_contract.adjudicate_proof_court(**inputs)
    assert baseline["state"] == "unresolved"

    result = proof_contract.adjudicate_proof_court(
        **inputs,
        policy_items=[_consume_boundary_policy()],
        boundary_path_state="verified_clean",
        boundary_authority_valid=True,
    )

    assert result["state"] == "verified_clean"
    assert result["pre_policy_state"] == "unresolved"
    evaluation = result["policy_evaluation"]
    assert evaluation["fired"][0]["item_id"] == "policy_consume_boundary"
    assert evaluation["terminal_override"] == "verified_clean"
    consume_telemetry = [
        row for row in evaluation["telemetry"]
        if row["kind"] == "consume_verified_clean_boundary"
    ]
    assert len(consume_telemetry) == 1
    assert consume_telemetry[0]["terminal_before"] == "unresolved"
    assert consume_telemetry[0]["terminal_after"] == "verified_clean"
    # The occurrence ledger records pre-policy evidence and must not move.
    assert result["occurrence_ledger"] == baseline["occurrence_ledger"]
    assert result["occurrence_state"] == baseline["occurrence_state"]
    assert result["occurrence_ledger"]["state"] == "unresolved"


def test_consume_boundary_fail_closed_without_authority() -> None:
    inputs = _unresolved_boundary_conflict_inputs()
    for authority in (False,):
        result = proof_contract.adjudicate_proof_court(
            **inputs,
            policy_items=[_consume_boundary_policy()],
            boundary_path_state="verified_clean",
            boundary_authority_valid=authority,
        )
        assert result["state"] == "unresolved"
        assert "pre_policy_state" not in result
        evaluation = result["policy_evaluation"]
        assert evaluation["fired"] == []
        assert evaluation["skipped"][0]["reason"] == (
            "runtime_precondition_not_satisfied"
        )


def test_consume_boundary_default_authority_is_fail_closed() -> None:
    # Callers that predate the authority channel keep the terminal edge
    # untouched: the parameter defaults to False.
    inputs = _unresolved_boundary_conflict_inputs()
    result = proof_contract.adjudicate_proof_court(
        **inputs,
        policy_items=[_consume_boundary_policy()],
        boundary_path_state="verified_clean",
    )
    assert result["state"] == "unresolved"
    assert "pre_policy_state" not in result


def test_consume_boundary_fail_closed_with_surviving_errors() -> None:
    surviving_certificate = _process_only_error_certificate()
    surviving_certificate.update({
        "impact_kind": "final_answer",
        "benchmark_causal_status": "causes_incorrect_result",
        "benchmark_causal_evidence_refs": ["event_002"],
    })
    inputs = {
        "required_candidates": [_candidate()],
        "prosecution": {
            "coverage_state": "complete",
            "candidates": [surviving_certificate],
        },
        "defense": {"coverage_state": "incomplete", "candidates": []},
        "packet": _packet(),
    }
    baseline = proof_contract.adjudicate_proof_court(**inputs)
    assert baseline["state"] == "verified_error"

    result = proof_contract.adjudicate_proof_court(
        **inputs,
        policy_items=[_consume_boundary_policy()],
        boundary_path_state="verified_clean",
        boundary_authority_valid=True,
    )
    # Surviving errors dominate: the conflict precondition never fires, the
    # terminal stays at the evidence verdict, nothing is overridden.
    assert result["state"] == "verified_error"
    assert "pre_policy_state" not in result
    evaluation = result["policy_evaluation"]
    assert evaluation["fired"] == []
    assert evaluation["terminal_override"] == ""


def test_consume_boundary_does_not_restore_consensus_blocked() -> None:
    # A consensus-blocked unresolved case has no verified-clean boundary
    # behind it in this fixture, so the consume item must stay inert: the
    # post-ledger hook never resurrects gate-blocked certificates.
    inputs = _unresolved_boundary_conflict_inputs()
    policy = PolicyItem(
        item_id="policy_consume_boundary",
        precondition="boundary_state_conflicts_with_terminal",
        action="consume_verified_clean_boundary",
        params={},
    )
    result = proof_contract.adjudicate_proof_court(
        **inputs,
        policy_items=[policy],
        boundary_path_state="unresolved",
        boundary_authority_valid=True,
    )
    assert result["state"] == "unresolved"
    assert "pre_policy_state" not in result
    assert result["policy_evaluation"]["fired"] == []


def _surviving_error_certificate() -> dict:
    cert = _process_only_error_certificate()
    cert.update({
        "impact_kind": "final_answer",
        "benchmark_causal_status": "causes_incorrect_result",
        "benchmark_causal_evidence_refs": ["event_002"],
    })
    return cert


def test_surviving_errors_keep_verified_error_under_coverage_downgrade() -> None:
    inputs = {
        "required_candidates": [_candidate()],
        "prosecution": {
            "coverage_state": "complete",
            "candidates": [_surviving_error_certificate()],
        },
        "defense": {"coverage_state": "complete", "candidates": []},
        "packet": _packet(),
    }
    baseline = proof_contract.adjudicate_proof_court(**inputs)
    assert baseline["state"] == "verified_error"

    policy = PolicyItem(
        item_id="policy_downgrade_defense",
        precondition="defense_coverage_complete_and_zero_certificates",
        action="downgrade_coverage",
        params={"side": "defense", "to_state": "incomplete"},
    )
    result = proof_contract.adjudicate_proof_court(**inputs, policy_items=[policy])

    # The coverage shift is recorded, but surviving errors dominate the
    # court's full terminal rule, so the verdict must not move.
    assert result["state"] == "verified_error"
    assert result["pre_policy_state"] == "verified_error"
    evaluation = result["policy_evaluation"]
    assert evaluation["effective_defense_coverage"] == "incomplete"
    assert evaluation["terminal_state"] == "verified_error"
