from __future__ import annotations

from evolution_v3.worksheet import classify_family_actionability


def _fn_family(predicate_signals=None, defense_verdict="none", recall_path="ok"):
    return {
        "failure_family": "FAM-fn-judge_false_no_recall_candidates-a41-Pa007",
        "count": 2,
        "mechanism_signature": {
            "judge_positive": False,
            "stages": {
                "judge": "ok", "recall_scanner": "not_run",
                "causal_upgrade": "not_run", "step_tracer": "not_run",
                "defense": "not_run",
            },
            "recall_path": recall_path,
            "upgrade_positive": False,
            "defense_verdict": defense_verdict,
            "positive_source": "none",
        },
        "diagnostics": {
            "predicate_signals": predicate_signals or {
                "delivered_anchor_consistency": {
                    "applicable": 2, "violations": 2,
                },
            },
        },
    }


def test_v10b_family_conviction_route_closed_but_recall_open():
    """The exact family from the v10b deadlock: the only applicable
    predicate is not conviction-authorized, so the conviction route is
    closed while the recall/upgrade directive routes stay open."""
    verdict = classify_family_actionability(_fn_family())
    assert verdict["classification"] == "actionable"
    assert "mechanical_conviction" not in verdict["routes"]
    assert "recall_directive" in verdict["routes"]
    assert "upgrade_directive" in verdict["routes"]
    assert "evidence_only" in verdict["routes"]


def test_unanimous_authorized_predicate_opens_conviction_route():
    verdict = classify_family_actionability(_fn_family(predicate_signals={
        "required_action_present": {"applicable": 2, "violations": 2},
    }))
    assert verdict["classification"] == "actionable"
    assert "mechanical_conviction" in verdict["routes"]


def test_defense_refuted_fn_opens_restore_route():
    verdict = classify_family_actionability(
        _fn_family(defense_verdict="refuted"),
    )
    assert "restore_guard" in verdict["routes"]


def test_unresolved_family_is_capability_gap():
    verdict = classify_family_actionability({
        "failure_family": "FAM-unresolved-abc123",
        "count": 3,
        "mechanism_signature": {},
        "diagnostics": {"predicate_signals": {}},
    })
    assert verdict["classification"] == "capability_gap"
    assert verdict["routes"] == []


def test_fp_family_with_positive_source_opens_withhold():
    verdict = classify_family_actionability({
        "failure_family": "FAM-fp-judge_conviction",
        "count": 2,
        "mechanism_signature": {
            "judge_positive": True,
            "positive_source": "judge",
            "defense_verdict": "substantiated",
            "recall_path": "positive",
            "upgrade_positive": False,
        },
        "diagnostics": {"predicate_signals": {}},
    })
    assert verdict["classification"] == "actionable"
    assert "withhold_guard" in verdict["routes"]
