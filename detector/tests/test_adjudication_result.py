from __future__ import annotations

from detector.adjudication import (
    AUTHORITY_AUTHORITATIVE,
    AUTHORITY_DEGRADED_JUDGE_ONLY,
    AUTHORITY_INVALID,
    STATE_CLEAN,
    STATE_ERROR,
    STATE_UNRESOLVED,
    adjudicate,
)
from detector.result import build_detection_output, prediction_state

JUDGE_TRUE = {
    "ok": True,
    "parsed": {
        "is_hallucination": True,
        "hallucination_step": 2,
        "hallucination_reason": "wrong value",
        "delivered_result": "5",
        "correct_result": "4",
        "basis": "derivation",
        "confidence": "high",
        "evidence_refs": ["event_002"],
        "root_cause": {"step": 2, "quote": "5", "referent": "4"},
        "contracts": [{"kind": "exact_value", "requirement": "the result 4"}],
    },
}
JUDGE_FALSE = {
    "ok": True,
    "parsed": {
        "is_hallucination": False,
        "hallucination_step": None,
        "hallucination_reason": None,
        "delivered_result": "4",
        "correct_result": "4",
        "basis": None,
        "confidence": "high",
        "evidence_refs": [],
        "root_cause": None,
        "contracts": [{"kind": "exact_value", "requirement": "the result 4"}],
    },
}
JUDGE_OK = {"stage": "judge", "state": "ok", "attempts": 1, "view": "complete", "warnings": []}
JUDGE_BAD = {"stage": "judge", "state": "provider_error", "attempts": 1, "view": "complete", "warnings": []}

NONE_RECALL = ({}, {"stage": "recall_scanner", "state": "not_needed"})
NONE_UPGRADE = ({}, {"stage": "causal_upgrade", "state": "not_needed"})
NONE_TRACER = ({}, {"stage": "step_tracer", "state": "not_needed"})
NONE_DEFENSE = ({}, {"stage": "defense", "state": "not_run"})


def _defense(verdict, state="ok"):
    return (
        {"ok": True, "parsed": {
            "verdict": verdict,
            "confirmed_step": 2 if verdict == "substantiated" else None,
            "evidence_refs": ["event_002"],
            "reason": "r",
        }},
        {"stage": "defense", "state": state, "attempts": 1, "view": "complete", "warnings": []},
    )


def _hearings(judge_result, defense_result, tracer_result=None):
    """One hearing per preliminary positive, judge-shaped by default."""
    parsed = judge_result.get("parsed") or {}
    return [{
        "source": "judge",
        "step": parsed.get("hallucination_step"),
        "reason": parsed.get("hallucination_reason"),
        "delivered_result": parsed.get("delivered_result"),
        "correct_result": parsed.get("correct_result"),
        "evidence_refs": parsed.get("evidence_refs") or [],
        "quote": (parsed.get("root_cause") or {}).get("quote") or "",
        "tracer_result": tracer_result,
        "defense_result": defense_result,
    }]


def _adjudicate(judge_result, judge_status, defense, recall_ok=True):
    defense_result, defense_status = defense
    recall_result, recall_status = NONE_RECALL
    upgrade_result, upgrade_status = NONE_UPGRADE
    if recall_ok:
        recall_result = {"ok": True, "candidates": []}
        upgrade_result = {"ok": True, "upgrades": [], "upgraded_candidate": None}
        recall_status = {"stage": "recall_scanner", "state": "ok"}
        upgrade_status = {"stage": "causal_upgrade", "state": "not_needed"}
    tracer_result, tracer_status = NONE_TRACER
    return adjudicate(
        packet=sample_packet if False else None,
        judge_result=judge_result, judge_status=judge_status,
        recall_result=recall_result, recall_status=recall_status,
        upgrade_result=upgrade_result, upgrade_status=upgrade_status,
        tracer_result=tracer_result, tracer_status=tracer_status,
        defense_result=defense_result, defense_status=defense_status,
        hearings=_hearings(judge_result, defense_result, tracer_result),
    )


def test_judge_false_is_clean(sample_packet):
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_FALSE, judge_status=JUDGE_OK,
        recall_result={"ok": True, "candidates": []},
        recall_status={"stage": "recall_scanner", "state": "ok"},
        upgrade_result={"ok": True, "upgrades": [], "upgraded_candidate": None},
        upgrade_status={"stage": "causal_upgrade", "state": "not_needed"},
        tracer_result=None, tracer_status=None,
        defense_result=None, defense_status=None,
    )
    assert proof["state"] == STATE_CLEAN
    assert proof["decision_authority"] == AUTHORITY_AUTHORITATIVE
    assert proof["responsible_step"] is None


def test_substantiated_is_error(sample_packet):
    defense_result, defense_status = _defense("substantiated")
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_TRUE, judge_status=JUDGE_OK,
        recall_result=None, recall_status=None,
        upgrade_result=None, upgrade_status=None,
        tracer_result=None, tracer_status=None,
        defense_result=defense_result, defense_status=defense_status,
        hearings=_hearings(JUDGE_TRUE, defense_result),
    )
    assert proof["state"] == STATE_ERROR
    assert proof["decision_authority"] == AUTHORITY_AUTHORITATIVE
    assert proof["responsible_step"] == 2
    assert proof["responsible_event_id"] == "event_002"


def test_adjudication_rejects_hearing_evidence_for_another_step(sample_packet):
    defense_result, defense_status = _defense("substantiated")
    hearing = _hearings(JUDGE_TRUE, defense_result)[0]
    hearing["defense_result"]["parsed"]["confirmed_step"] = 1
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_TRUE, judge_status=JUDGE_OK,
        recall_result=None, recall_status=None,
        upgrade_result=None, upgrade_status=None,
        tracer_result=None, tracer_status=None,
        defense_result=defense_result, defense_status=defense_status,
        hearings=[hearing],
    )
    assert proof["state"] == STATE_ERROR
    assert proof["decision_authority"] == AUTHORITY_DEGRADED_JUDGE_ONLY
    assert proof["responsible_step"] == 2


def test_refuted_overturns(sample_packet):
    defense_result, defense_status = _defense("refuted")
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_TRUE, judge_status=JUDGE_OK,
        recall_result=None, recall_status=None,
        upgrade_result=None, upgrade_status=None,
        tracer_result=None, tracer_status=None,
        defense_result=defense_result, defense_status=defense_status,
        hearings=_hearings(JUDGE_TRUE, defense_result),
    )
    assert proof["state"] == STATE_CLEAN
    assert proof["authority_failure_reasons"][0]["code"] == "refuted"


def test_inconclusive_keeps_error_but_degrades(sample_packet):
    defense_result, defense_status = _defense("inconclusive")
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_TRUE, judge_status=JUDGE_OK,
        recall_result=None, recall_status=None,
        upgrade_result=None, upgrade_status=None,
        tracer_result=None, tracer_status=None,
        defense_result=defense_result, defense_status=defense_status,
        hearings=_hearings(JUDGE_TRUE, defense_result),
    )
    assert proof["state"] == STATE_ERROR
    assert proof["decision_authority"] == AUTHORITY_DEGRADED_JUDGE_ONLY


def test_judge_failure_is_unresolved(sample_packet):
    proof = adjudicate(
        packet=sample_packet,
        judge_result={"ok": False, "error": "boom", "error_type": "provider_error"},
        judge_status=JUDGE_BAD,
        recall_result=None, recall_status=None,
        upgrade_result=None, upgrade_status=None,
        tracer_result=None, tracer_status=None,
        defense_result=None, defense_status=None,
    )
    assert proof["state"] == STATE_UNRESOLVED
    assert proof["decision_authority"] == AUTHORITY_INVALID


def test_result_envelope_and_prediction(sample_packet):
    defense_result, defense_status = _defense("substantiated")
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_TRUE, judge_status=JUDGE_OK,
        recall_result=None, recall_status=None,
        upgrade_result=None, upgrade_status=None,
        tracer_result=None, tracer_status=None,
        defense_result=defense_result, defense_status=defense_status,
        hearings=_hearings(JUDGE_TRUE, defense_result),
    )
    stage_status = {
        "judge": JUDGE_OK,
        "recall_scanner": None,
        "causal_upgrade": None,
        "step_tracer": None,
        "defense": defense_status,
    }
    providers = {
        "judge": None,
        "recall_scanner": None,
        "causal_upgrade": None,
        "step_tracer": None,
        "defense": None,
    }
    proof["judge"] = JUDGE_TRUE["parsed"]
    proof["defense"] = defense_result["parsed"]
    output = build_detection_output(
        case_id="c", packet=sample_packet, proof=proof,
        stage_status=stage_status, providers=providers, provenance={},
    )
    assert output["schema_version"] == "detector.detection_result.v1"
    assert prediction_state(output) is True


def test_convict_mechanical_violation_promotes_clean_to_error(sample_packet, synthetic_conviction_calibration):
    """The recall-side execution surface: a guard may convict on mechanical
    evidence even when no preliminary positive exists."""
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_FALSE, judge_status=JUDGE_OK,
        recall_result={"ok": True, "candidates": []},
        recall_status={"stage": "recall_scanner", "state": "ok"},
        upgrade_result={"ok": True, "upgrades": [], "upgraded_candidate": None},
        upgrade_status={"stage": "causal_upgrade", "state": "not_needed"},
        tracer_result=None, tracer_status=None,
        defense_result=None, defense_status=None,
        guard_actions=["convict_mechanical_violation"],
        mechanical_evidence=[{
            "action": "convict_mechanical_violation",
            "intervention_id": "IV-G",
            "evidence_intervention_id": "IV-E",
            "failure_family": "FAM-test",
            "predicate": "required_action_present",
            "verdict": "violated",
        }],
    )
    assert proof["state"] == STATE_ERROR
    assert proof["decision_authority"] == AUTHORITY_AUTHORITATIVE
    assert proof["incident_occurred"] is True
    assert proof["terminal_basis"]["decisive_facts"]["positive_source"] == (
        "mechanical_violation"
    )
    assert proof["terminal_basis"]["decisive_facts"]["guard_action"] == (
        "convict_mechanical_violation"
    )


def test_convict_guard_localizes_to_delivered_anchor(sample_packet, synthetic_conviction_calibration):
    """The conviction anchors on the delivered-answer event, not step 1."""
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_FALSE, judge_status=JUDGE_OK,
        recall_result={"ok": True, "candidates": []},
        recall_status={"stage": "recall_scanner", "state": "ok"},
        upgrade_result={"ok": True, "upgrades": [], "upgraded_candidate": None},
        upgrade_status={"stage": "causal_upgrade", "state": "not_needed"},
        tracer_result=None, tracer_status=None,
        defense_result=None, defense_status=None,
        guard_actions=["convict_mechanical_violation"],
        mechanical_evidence=[{
            "action": "convict_mechanical_violation",
            "intervention_id": "IV-G",
            "evidence_intervention_id": "IV-E",
            "failure_family": "FAM-test",
            "predicate": "required_action_present",
            "verdict": "violated",
        }],
    )
    # sample_packet's last prose event is event_002 at position 2.
    assert proof["responsible_event_id"] == "event_002"
    assert proof["responsible_step"] == 2


def test_without_convict_guard_judge_false_stays_clean(sample_packet):
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_FALSE, judge_status=JUDGE_OK,
        recall_result={"ok": True, "candidates": []},
        recall_status={"stage": "recall_scanner", "state": "ok"},
        upgrade_result={"ok": True, "upgrades": [], "upgraded_candidate": None},
        upgrade_status={"stage": "causal_upgrade", "state": "not_needed"},
        tracer_result=None, tracer_status=None,
        defense_result=None, defense_status=None,
        guard_actions=["require_full_view_defense"],
    )
    assert proof["state"] == STATE_CLEAN


def test_convict_guard_without_bound_evidence_stays_clean(sample_packet):
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_FALSE, judge_status=JUDGE_OK,
        recall_result={"ok": True, "candidates": []},
        recall_status={"stage": "recall_scanner", "state": "ok"},
        upgrade_result={"ok": True, "upgrades": [], "upgraded_candidate": None},
        upgrade_status={"stage": "causal_upgrade", "state": "not_needed"},
        tracer_result=None, tracer_status=None,
        defense_result=None, defense_status=None,
        guard_actions=["convict_mechanical_violation"],
        mechanical_evidence=[],
    )
    assert proof["state"] == STATE_CLEAN


def test_convict_guard_rejects_incomplete_evidence_provenance(sample_packet):
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_FALSE, judge_status=JUDGE_OK,
        recall_result={"ok": True, "candidates": []},
        recall_status={"stage": "recall_scanner", "state": "ok"},
        upgrade_result={"ok": True, "upgrades": [], "upgraded_candidate": None},
        upgrade_status={"stage": "causal_upgrade", "state": "not_needed"},
        tracer_result=None, tracer_status=None,
        defense_result=None, defense_status=None,
        guard_actions=["convict_mechanical_violation"],
        mechanical_evidence=[{
            "predicate": "required_action_present",
            "verdict": "violated",
        }],
    )
    assert proof["state"] == STATE_CLEAN


def test_refuted_with_failed_recall_degrades_instead_of_claiming_authority(sample_packet):
    """A scanner outage must not be laundered into an authoritative clear.

    The all-refuted branch returned ``authoritative`` without consulting the
    recall net, so a recall failure on a judge-positive case produced a fully
    authoritative ``verified_clean`` whose authority arguments hid the failure.
    """
    defense_result, defense_status = _defense("refuted")
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_TRUE, judge_status=JUDGE_OK,
        recall_result={"ok": False, "error": "scanner down",
                       "error_type": "provider_error"},
        recall_status={"stage": "recall_scanner", "state": "provider_error"},
        upgrade_result=None, upgrade_status=None,
        tracer_result=None, tracer_status=None,
        defense_result=defense_result, defense_status=defense_status,
        hearings=_hearings(JUDGE_TRUE, defense_result),
    )
    assert proof["state"] == STATE_CLEAN
    assert proof["decision_authority"] == AUTHORITY_DEGRADED_JUDGE_ONLY
    codes = [reason["code"] for reason in proof["authority_failure_reasons"]]
    assert "recall_unavailable_after_refutation" in codes


def test_refuted_with_successful_recall_stays_authoritative(sample_packet):
    defense_result, defense_status = _defense("refuted")
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_TRUE, judge_status=JUDGE_OK,
        recall_result={"ok": True, "candidates": []},
        recall_status={"stage": "recall_scanner", "state": "ok"},
        upgrade_result={"ok": True, "upgrades": [], "upgraded_candidate": None},
        upgrade_status={"stage": "causal_upgrade", "state": "not_needed"},
        tracer_result=None, tracer_status=None,
        defense_result=defense_result, defense_status=defense_status,
        hearings=_hearings(JUDGE_TRUE, defense_result),
    )
    assert proof["state"] == STATE_CLEAN
    assert proof["decision_authority"] == AUTHORITY_AUTHORITATIVE


def test_unhashable_basis_is_a_contract_failure_not_a_crash(sample_packet):
    """A non-string ``basis`` must abstain the ballot, not raise TypeError.

    ``basis not in JUDGE_BASIS_VALUES`` raised ``TypeError: unhashable type``
    for a list, which escaped the vote loop and aborted the whole batch.
    """
    from detector.judge import _parse_judge

    bad = dict(JUDGE_TRUE["parsed"])
    bad["basis"] = ["derivation"]
    parsed, error = _parse_judge(bad, sample_packet)
    assert parsed is None
    assert "must be a string" in error
