"""Contract tests for the v6 shared vocabulary and core types."""

import pytest

from src.sehd_v6.core.bccse import v6_contracts as contracts


def test_layers_and_artifacts_are_disjoint_vocabulary():
    assert len(contracts.FAILURE_LAYERS) == 4
    assert contracts.L2_ARTIFACTS.isdisjoint(contracts.L1_ARTIFACTS)
    assert len(contracts.L1_ARTIFACTS) == 8


def test_predicate_registry_is_closed_and_pure():
    facts = contracts.AdjudicationFacts(
        consensus_gate_applied=True,
        consensus_gate_blocked_count=2,
        defense_coverage="complete",
        valid_defense_certificate_count=0,
        boundary_path_state="verified_clean",
        terminal_state="unresolved",
        surviving_error_count=0,
        required_candidate_count=1,
        quarantined_error_count=0,
    )
    assert contracts.L2_PREDICATES["consensus_gate_blocked_nonempty"](facts)
    assert contracts.L2_PREDICATES[
        "defense_coverage_complete_and_zero_certificates"
    ](facts)
    assert contracts.L2_PREDICATES["boundary_state_conflicts_with_terminal"](facts)
    assert not contracts.L2_PREDICATES[
        "surviving_errors_nonempty_and_verdict_clean"
    ](facts)
    assert not contracts.L2_PREDICATES["all_candidates_quarantined"](facts)


def test_build_adjudication_facts_from_court_result():
    court = {
        "state": "unresolved",
        "prosecution_coverage": "complete",
        "defense_coverage": "complete",
        "valid_error_certificate_count": 1,
        "valid_defense_certificate_count": 0,
        "surviving_error_certificate_ids": ["e1"],
        "defeated_error_certificate_ids": [],
        "quarantined_error_certificate_ids": [],
        "quarantined_defense_certificate_ids": ["d1"],
        "consensus_gate_applied": True,
        "consensus_gate_blocked_ids": ["e1"],
        "required_candidate_count": 2,
    }
    facts = contracts.build_adjudication_facts(court, boundary_path_state="cleared")
    assert facts.terminal_state == "unresolved"
    assert facts.consensus_gate_blocked_count == 1
    assert facts.boundary_path_state == "cleared"
    assert not facts.fully_quarantined
    assert facts.total_certificate_count == 1


def test_policy_params_validation_enforces_closed_vocabularies():
    ok, reason = contracts.validate_policy_params(
        contracts.ACTION_DOWNGRADE_COVERAGE,
        {"side": "defense", "to_state": "incomplete"},
    )
    assert ok and reason == ""

    ok, reason = contracts.validate_policy_params(
        contracts.ACTION_DOWNGRADE_COVERAGE, {"side": "defense"}
    )
    assert not ok and "missing_param:to_state" in reason

    ok, reason = contracts.validate_policy_params(
        contracts.ACTION_DOWNGRADE_COVERAGE,
        {"side": "both", "to_state": "incomplete"},
    )
    assert not ok and "invalid_side" in reason

    ok, reason = contracts.validate_policy_params("tune_threshold", {})
    assert not ok and "unknown_action" in reason

    ok, reason = contracts.validate_policy_params(
        contracts.ACTION_RECLASSIFY_CANDIDATE_STATE,
        {"candidate_ref": "c1", "state": "verified_clean"},
    )
    assert ok


def test_policy_item_roundtrip():
    item = contracts.PolicyItem(
        item_id="p1",
        precondition="consensus_gate_blocked_nonempty",
        action=contracts.ACTION_EMIT_TELEMETRY_ONLY,
        rationale="telemetry only",
    )
    restored = contracts.PolicyItem.from_dict(item.to_dict())
    assert restored == item


def test_failure_attribution_actionable_only_for_evolvable_layers():
    l2 = contracts.FailureAttribution(
        layer=contracts.LAYER_L2,
        artifact=contracts.L2_ARTIFACT_CONSENSUS_GATE,
        reason_code="consensus_gate_blocked",
    )
    none = contracts.FailureAttribution(
        layer=contracts.LAYER_NONE, artifact="", reason_code="no_pattern"
    )
    assert l2.actionable
    assert not none.actionable


def test_actions_split_court_local_and_engine_level():
    assert contracts.COURT_LOCAL_ACTIONS.isdisjoint(contracts.ENGINE_LEVEL_ACTIONS)
    assert contracts.L2_ACTIONS == (
        contracts.COURT_LOCAL_ACTIONS | contracts.ENGINE_LEVEL_ACTIONS
    )
    for action, params in contracts.ACTION_PARAMS.items():
        assert action in contracts.L2_ACTIONS
        assert isinstance(params, tuple)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
