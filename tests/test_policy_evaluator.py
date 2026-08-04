"""Tests for the deterministic L2 policy evaluator."""

from src.sehd_v6.core.bccse.v6_contracts import (
    ACTION_DOWNGRADE_COVERAGE,
    ACTION_EMIT_TELEMETRY_ONLY,
    ACTION_INVALIDATE_COVERAGE_CLAIM,
    ACTION_RECLASSIFY_CANDIDATE_STATE,
    ACTION_REQUIRE_ROUTE_REPLAY,
    AdjudicationFacts,
    PolicyItem,
)
from src.sehd_v6.core.paca_tcl.policy_evaluator import (
    PolicyEvaluationResult,
    _coverage_rank,
    _lower_coverage,
    evaluate_policies,
)


def _facts(**overrides) -> AdjudicationFacts:
    base = dict(
        prosecution_coverage="complete",
        defense_coverage="complete",
        valid_error_certificate_count=1,
        valid_defense_certificate_count=1,
        surviving_error_count=0,
        defeated_error_count=1,
        quarantined_error_count=0,
        quarantined_defense_count=0,
        consensus_gate_applied=False,
        consensus_gate_blocked_count=0,
        required_candidate_count=1,
        fully_quarantined=False,
        boundary_path_state="",
        terminal_state="verified_clean",
    )
    base.update(overrides)
    return AdjudicationFacts(**base)


def test_empty_items_keeps_facts_and_recomputes_terminal():
    facts = _facts()
    result = evaluate_policies(facts, [])
    assert isinstance(result, PolicyEvaluationResult)
    assert result.applied_item_ids == ()
    assert result.fired == ()
    assert result.skipped == ()
    assert result.telemetry == ()
    assert result.effective_prosecution_coverage == "complete"
    assert result.effective_defense_coverage == "complete"
    assert result.candidate_reclassifications == {}
    assert result.route_replay_requests == ()
    assert result.terminal_state == "verified_clean"


def test_consensus_gate_blocked_fires_telemetry_only():
    facts = _facts(
        consensus_gate_applied=True,
        consensus_gate_blocked_count=2,
        surviving_error_count=0,
    )
    item = PolicyItem(
        item_id="p1",
        precondition="consensus_gate_blocked_nonempty",
        action=ACTION_EMIT_TELEMETRY_ONLY,
        rationale="blocked certificates must not vote",
    )
    result = evaluate_policies(facts, [item])
    assert result.applied_item_ids == ("p1",)
    assert result.fired[0]["action"] == ACTION_EMIT_TELEMETRY_ONLY
    assert result.telemetry == ({
        "kind": "telemetry_only",
        "item_id": "p1",
        "rationale": "blocked certificates must not vote",
    },)
    # telemetry-only must not touch coverage or terminal state
    assert result.effective_prosecution_coverage == "complete"
    assert result.effective_defense_coverage == "complete"
    assert result.terminal_state == "verified_clean"


def test_downgrade_only_lowers_never_raises():
    facts = _facts(valid_defense_certificate_count=0)
    items = [
        PolicyItem(
            item_id="p1",
            precondition="defense_coverage_complete_and_zero_certificates",
            action=ACTION_DOWNGRADE_COVERAGE,
            params={"side": "defense", "to_state": "incomplete"},
        ),
        PolicyItem(
            item_id="p2",
            precondition="defense_coverage_complete_and_zero_certificates",
            action=ACTION_DOWNGRADE_COVERAGE,
            params={"side": "defense", "to_state": "complete"},
        ),
    ]
    result = evaluate_policies(facts, items)
    # p1 downgrades complete -> incomplete; p2 targets "complete", which is
    # outside the downgrade vocabulary, so it is skipped and never re-raises.
    assert result.applied_item_ids == ("p1",)
    assert result.effective_defense_coverage == "incomplete"
    assert result.skipped[0]["item_id"] == "p2"
    assert result.skipped[0]["reason"] == "invalid_downgrade_state:complete"


def test_downgrade_takes_lower_of_current_and_target():
    facts = _facts(defense_coverage="incomplete", valid_defense_certificate_count=0)
    item = PolicyItem(
        item_id="p1",
        precondition="defense_coverage_complete_and_zero_certificates",
        action=ACTION_DOWNGRADE_COVERAGE,
        params={"side": "defense", "to_state": "unresolved"},
    )
    # precondition is false on incomplete coverage, so nothing fires
    assert evaluate_policies(facts, [item]).effective_defense_coverage == "incomplete"
    assert _coverage_rank("unresolved") < _coverage_rank("incomplete") \
        < _coverage_rank("complete")
    assert _lower_coverage("complete", "incomplete") == "incomplete"
    assert _lower_coverage("incomplete", "complete") == "incomplete"


def test_invalidate_coverage_claim_sets_unresolved():
    facts = _facts(valid_defense_certificate_count=0)
    item = PolicyItem(
        item_id="p1",
        precondition="defense_coverage_complete_and_zero_certificates",
        action=ACTION_INVALIDATE_COVERAGE_CLAIM,
        params={"side": "defense"},
    )
    result = evaluate_policies(facts, [item])
    assert result.applied_item_ids == ("p1",)
    assert result.effective_defense_coverage == "unresolved"
    assert result.effective_prosecution_coverage == "complete"
    assert result.terminal_state == "unresolved"


def test_unknown_predicate_is_fail_closed():
    facts = _facts()
    item = PolicyItem(
        item_id="p1",
        precondition="predicate_that_does_not_exist",
        action=ACTION_INVALIDATE_COVERAGE_CLAIM,
        params={"side": "defense"},
    )
    result = evaluate_policies(facts, [item])
    assert result.applied_item_ids == ()
    assert result.fired == ()
    assert result.skipped == ({
        "item_id": "p1",
        "precondition": "predicate_that_does_not_exist",
        "action": ACTION_INVALIDATE_COVERAGE_CLAIM,
        "reason": "unknown_predicate",
    },)
    assert result.effective_defense_coverage == "complete"


def test_false_precondition_neither_fires_nor_skips():
    facts = _facts(consensus_gate_applied=False, consensus_gate_blocked_count=0)
    item = PolicyItem(
        item_id="p1",
        precondition="consensus_gate_blocked_nonempty",
        action=ACTION_EMIT_TELEMETRY_ONLY,
    )
    result = evaluate_policies(facts, [item])
    assert result.applied_item_ids == ()
    assert result.fired == ()
    assert result.skipped == ()
    assert result.telemetry == ()


def test_engine_level_actions_do_not_touch_court_state():
    facts = _facts(
        boundary_path_state="verified_clean",
        terminal_state="unresolved",
        prosecution_coverage="incomplete",
    )
    items = [
        PolicyItem(
            item_id="p1",
            precondition="boundary_state_conflicts_with_terminal",
            action=ACTION_RECLASSIFY_CANDIDATE_STATE,
            params={"candidate_ref": "cand-1", "state": "verified_clean"},
        ),
        PolicyItem(
            item_id="p2",
            precondition="boundary_state_conflicts_with_terminal",
            action=ACTION_REQUIRE_ROUTE_REPLAY,
            params={"route_key": "route.alpha"},
        ),
    ]
    result = evaluate_policies(facts, items)
    assert result.applied_item_ids == ("p1", "p2")
    assert result.candidate_reclassifications == {"cand-1": "verified_clean"}
    assert result.route_replay_requests == ("route.alpha",)
    # court-local coverage and terminal recomputation remain untouched
    assert result.effective_prosecution_coverage == "incomplete"
    assert result.effective_defense_coverage == "complete"
    assert result.terminal_state == "unresolved"


def test_reclassify_last_write_wins_and_is_traced():
    facts = _facts(fully_quarantined=True, required_candidate_count=1,
                   quarantined_error_count=1)
    items = [
        PolicyItem(
            item_id="p1",
            precondition="all_candidates_quarantined",
            action=ACTION_RECLASSIFY_CANDIDATE_STATE,
            params={"candidate_ref": "cand-1", "state": "unresolved"},
        ),
        PolicyItem(
            item_id="p2",
            precondition="all_candidates_quarantined",
            action=ACTION_RECLASSIFY_CANDIDATE_STATE,
            params={"candidate_ref": "cand-1", "state": "verified_error"},
        ),
    ]
    result = evaluate_policies(facts, items)
    assert result.candidate_reclassifications == {"cand-1": "verified_error"}
    traced = [row for row in result.telemetry
              if row["kind"] == "candidate_reclassification"]
    assert [row["item_id"] for row in traced] == ["p1", "p2"]
    assert [row["state"] for row in traced] == ["unresolved", "verified_error"]


def test_evaluation_is_deterministic():
    facts = _facts(
        consensus_gate_applied=True,
        consensus_gate_blocked_count=1,
        valid_defense_certificate_count=0,
    )
    items = [
        PolicyItem(
            item_id="p1",
            precondition="consensus_gate_blocked_nonempty",
            action=ACTION_EMIT_TELEMETRY_ONLY,
            rationale="r1",
        ),
        PolicyItem(
            item_id="p2",
            precondition="defense_coverage_complete_and_zero_certificates",
            action=ACTION_DOWNGRADE_COVERAGE,
            params={"side": "defense", "to_state": "incomplete"},
        ),
        PolicyItem(
            item_id="p3",
            precondition="no_such_predicate",
            action=ACTION_EMIT_TELEMETRY_ONLY,
        ),
    ]
    first = evaluate_policies(facts, items)
    second = evaluate_policies(facts, items)
    assert first.to_dict() == second.to_dict()


def test_terminal_recompute_verified_error_branch():
    facts = _facts(surviving_error_count=2)
    result = evaluate_policies(facts, [])
    assert result.terminal_state == "verified_error"


def test_terminal_recompute_verified_clean_branch():
    facts = _facts(surviving_error_count=0,
                   prosecution_coverage="complete",
                   defense_coverage="complete")
    result = evaluate_policies(facts, [])
    assert result.terminal_state == "verified_clean"


def test_terminal_recompute_unresolved_branch():
    facts = _facts(surviving_error_count=0, defense_coverage="incomplete")
    result = evaluate_policies(facts, [])
    assert result.terminal_state == "unresolved"


def test_to_dict_roundtrip_shape():
    facts = _facts(consensus_gate_applied=True, consensus_gate_blocked_count=1)
    item = PolicyItem(
        item_id="p1",
        precondition="consensus_gate_blocked_nonempty",
        action=ACTION_EMIT_TELEMETRY_ONLY,
        rationale="r",
    )
    payload = evaluate_policies(facts, [item]).to_dict()
    assert set(payload) == {
        "applied_item_ids", "fired", "skipped",
        "effective_prosecution_coverage", "effective_defense_coverage",
        "candidate_reclassifications", "route_replay_requests",
        "telemetry", "terminal_state",
    }
    assert payload["applied_item_ids"] == ["p1"]
