"""P9: audited pre-ledger consensus-restore policy (§15).

Covers the acceptance contract: an empty pre-policy is byte-identical; a
legal restore re-enters the surviving set; defeated/quarantined/process-only
certificates are never restored; the post-ledger evaluator never executes a
pre operator and vice versa; an unknown stage fails closed.
"""

from __future__ import annotations

import pytest

from src.sehd_v6.core.paca_tcl.policy_evaluator import (
    ACTION_RESTORE_AUDITED_CONSENSUS,
    PreLedgerPolicyFacts,
    evaluate_policies,
    evaluate_pre_ledger_policies,
)
from src.sehd_v6.core.bccse.v6_contracts import PolicyItem
from src.sehd_v6.core.bccse.vocabulary_registry import L2_REGISTRY


def make_restore_item(certificate_id: str, item_id: str = "restore_1") -> PolicyItem:
    return PolicyItem(
        item_id=item_id,
        precondition="consensus_gate_blocked_nonempty",
        action=ACTION_RESTORE_AUDITED_CONSENSUS,
        params={"certificate_id": certificate_id},
        rationale="restore an audited consensus-blocked certificate",
    )


def make_facts(
    blocked_ids=("cert_a",),
    causal="causes_incorrect_result",
    impact="missing_evidence",
    defense="standing",
    quarantine="clean",
    audit="pass",
) -> PreLedgerPolicyFacts:
    return PreLedgerPolicyFacts(
        consensus_gate_applied=True,
        blocked_certificate_ids=tuple(blocked_ids),
        blocked_certificate_audit_status={cid: audit for cid in blocked_ids},
        blocked_certificate_causal_status={cid: causal for cid in blocked_ids},
        blocked_certificate_impact_kind={cid: impact for cid in blocked_ids},
        blocked_certificate_defense_status={cid: defense for cid in blocked_ids},
        blocked_certificate_quarantine_status={cid: quarantine for cid in blocked_ids},
    )


# ============================================================
# operator registration (§15.5)
# ============================================================

def test_restore_operator_registered_pre_ledger_court():
    operator = L2_REGISTRY.raw_operator(ACTION_RESTORE_AUDITED_CONSENSUS)
    assert operator is not None
    assert operator.policy_stage == "pre_ledger"
    assert operator.runtime_owner == "court"


def test_restore_operator_not_a_post_ledger_proposable_action():
    # The pre-ledger operator is never a post-ledger proposable action, so
    # the two stages can never share an execution path.
    assert ACTION_RESTORE_AUDITED_CONSENSUS not in L2_REGISTRY.action_names()


# ============================================================
# legal restore (§15.4)
# ============================================================

def test_legal_restore_returns_certificate_to_surviving():
    result = evaluate_pre_ledger_policies(make_facts(), [make_restore_item("cert_a")])
    assert result.restored_certificate_ids == ("cert_a",)
    assert result.fired[0]["certificate_id"] == "cert_a"


def test_empty_pre_policy_restores_nothing():
    result = evaluate_pre_ledger_policies(make_facts(), [])
    assert result.restored_certificate_ids == ()
    assert result.fired == ()


# ============================================================
# fail-closed guards (§15.4 / §15.7)
# ============================================================

def test_certificate_not_in_blocked_set_rejected():
    result = evaluate_pre_ledger_policies(make_facts(), [make_restore_item("cert_zzz")])
    assert result.restored_certificate_ids == ()
    assert result.skipped[0]["reason"] == "certificate_not_in_blocked_set"


def test_defeated_never_restored():
    result = evaluate_pre_ledger_policies(
        make_facts(defense="dominated"), [make_restore_item("cert_a")],
    )
    assert result.restored_certificate_ids == ()
    assert result.skipped[0]["reason"] == "defeated_by_dominating_defense"


def test_quarantined_never_restored():
    result = evaluate_pre_ledger_policies(
        make_facts(quarantine="quarantined"), [make_restore_item("cert_a")],
    )
    assert result.restored_certificate_ids == ()
    assert result.skipped[0]["reason"] == "quarantined_raw_output"


def test_process_only_never_restored():
    for impact in ("process_only", "process_debt"):
        result = evaluate_pre_ledger_policies(
            make_facts(impact=impact), [make_restore_item("cert_a")],
        )
        assert result.restored_certificate_ids == ()
        assert result.skipped[0]["reason"] == "impact_process_only"


def test_non_causal_never_restored():
    result = evaluate_pre_ledger_policies(
        make_facts(causal="no_causal_link"), [make_restore_item("cert_a")],
    )
    assert result.restored_certificate_ids == ()
    assert result.skipped[0]["reason"] == "causal_status_not_causal"


def test_schema_not_passed_never_restored():
    result = evaluate_pre_ledger_policies(
        make_facts(audit="fail"), [make_restore_item("cert_a")],
    )
    assert result.restored_certificate_ids == ()
    assert result.skipped[0]["reason"] == "schema_validation_not_passed"


def test_consensus_gate_not_applied_blocks_restore():
    facts = make_facts()
    facts = PreLedgerPolicyFacts(
        consensus_gate_applied=False,
        blocked_certificate_ids=facts.blocked_certificate_ids,
        blocked_certificate_audit_status=facts.blocked_certificate_audit_status,
        blocked_certificate_causal_status=facts.blocked_certificate_causal_status,
        blocked_certificate_impact_kind=facts.blocked_certificate_impact_kind,
        blocked_certificate_defense_status=facts.blocked_certificate_defense_status,
        blocked_certificate_quarantine_status=facts.blocked_certificate_quarantine_status,
    )
    result = evaluate_pre_ledger_policies(facts, [make_restore_item("cert_a")])
    assert result.restored_certificate_ids == ()
    assert result.skipped[0]["reason"] == "consensus_gate_not_applied"


# ============================================================
# stage separation (§15.7)
# ============================================================

def test_pre_ledger_evaluator_rejects_post_operator():
    post_item = PolicyItem(
        item_id="post_1",
        precondition="consensus_gate_blocked_nonempty",
        action="downgrade_coverage",
        params={"side": "prosecution", "to_state": "incomplete"},
        rationale="post-ledger action",
    )
    result = evaluate_pre_ledger_policies(make_facts(), [post_item])
    assert result.restored_certificate_ids == ()
    assert result.skipped[0]["reason"] == "unknown_stage_for_pre_ledger"


def test_post_ledger_evaluator_rejects_pre_operator():
    from src.sehd_v6.core.bccse.v6_contracts import build_adjudication_facts
    # Make the precondition fire so the evaluator reaches the action check.
    facts = build_adjudication_facts({
        "consensus_gate_applied": True,
        "consensus_gate_blocked_ids": ["cert_a"],
    })
    result = evaluate_policies(facts, [make_restore_item("cert_a")])
    # The pre-ledger action is not in the post-ledger active vocabulary, so
    # it fails closed as an unknown action and is never applied.
    assert result.applied_item_ids == ()
    assert any(
        row["reason"].startswith("unknown_action") for row in result.skipped
    )
