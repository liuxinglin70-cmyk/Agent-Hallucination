"""Deterministic executor for L2 adjudication policy items.

L2 policy items are closed-vocabulary (precondition predicate -> enumerated
action) pairs proposed by the LLM during evolution. Their runtime semantics
are fixed entirely by this module: the LLM never interprets or executes an
item, so policy application cannot reintroduce nondeterminism into the
proof court. Pure functions, no I/O, evaluation order equals genome order.

The addressable observation surface (``AdjudicationFacts``) and the action /
predicate vocabulary live in ``skills.vocabulary``; this module derives the
fact snapshot from one proof-court result and executes items against it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sehd_rise.skills.vocabulary.contracts import (
    ACTION_CONSUME_VERIFIED_CLEAN_BOUNDARY,
    ACTION_DOWNGRADE_COVERAGE,
    ACTION_EMIT_TELEMETRY_ONLY,
    ACTION_INVALIDATE_COVERAGE_CLAIM,
    ACTION_REQUIRE_ADDITIONAL_VERIFICATION,
    ACTION_ESCALATE_TO_APPEAL,
    ACTION_MARK_CANDIDATE_AMBIGUOUS,
    ROUTE_RUNTIME_PRECONDITION_NOT_SATISFIED,
    PolicyItem,
    validate_policy_params,
)
from sehd_rise.skills.vocabulary.registry import (
    AdjudicationFacts,
    L2_REGISTRY,
)

ACTION_RESTORE_AUDITED_CONSENSUS = "restore_audited_consensus"

# Machine-readable executor/fitness surface consumed by the semantic-closure
# validator. Telemetry-only branches intentionally do not appear here.
EXECUTED_L2_REPAIR_OPERATIONS = frozenset({
    ACTION_DOWNGRADE_COVERAGE,
    ACTION_INVALIDATE_COVERAGE_CLAIM,
    ACTION_REQUIRE_ADDITIONAL_VERIFICATION,
    ACTION_CONSUME_VERIFIED_CLEAN_BOUNDARY,
})
L2_FITNESS_POSTCONDITIONS = frozenset({
    "policy_item_fired_and_coverage_changed",
    "policy_item_fired_and_terminal_changed",
})

_COVERAGE_RANK = {"unresolved": 0, "incomplete": 1, "complete": 2}


def _coverage_rank(state: str) -> int:
    # Unrecognized strings rank below "unresolved" so a policy can never
    # "upgrade" a coverage value the vocabulary does not know (fail-closed).
    return _COVERAGE_RANK.get(state, -1)


def _lower_coverage(a: str, b: str) -> str:
    return a if _coverage_rank(a) <= _coverage_rank(b) else b


def build_adjudication_facts(
    court_result: dict[str, Any],
    *,
    boundary_path_state: str = "",
    boundary_authority_valid: bool = False,
) -> AdjudicationFacts:
    """Derive the addressable fact snapshot from one proof-court result.

    The snapshot is built once from the court result plus the engine-level
    context (boundary path), so neither the evaluator nor failure
    classification ever reaches into raw provider payloads.
    """
    def sequence_count(value: Any) -> int:
        return len(value) if isinstance(value, (list, tuple, set)) else 0

    def nonnegative_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    blocked = court_result.get("consensus_gate_blocked_ids") or []
    quarantined_errors = court_result.get("quarantined_error_certificate_ids") or []
    required_count = nonnegative_int(court_result.get("required_candidate_count"))
    # The quarantine-reason buckets classify by the actual failed-check names
    # emitted by the court certificate validators; the vocabulary is derived
    # from those validators, not invented here.
    _SCHEMA_FAIL_CHECKS = frozenset({
        "required_fields", "certificate_id", "source_candidate",
        "target_candidate", "target_origin", "origin", "claim_kind",
        "norm_source", "breach_type", "impact_kind", "recovery_state",
        "benchmark_causal_status", "defense_type", "rationale",
        "agent_control", "impact_events",
    })
    _EVIDENCE_FAIL_CHECKS = frozenset({
        "norm_evidence", "violation_evidence", "agent_control_evidence",
        "impact_evidence", "recovery_evidence", "benchmark_causal_evidence",
        "evidence",
    })
    _CAUSAL_FAIL_CHECKS = frozenset({
        "ordered_chain", "agent_control_evidence_binding",
        "impact_evidence_binding", "benchmark_causal_evidence_binding",
    })
    quarantine_schema_mismatch = 0
    quarantine_evidence_missing = 0
    quarantine_causal_broken = 0
    quarantine_other = 0

    def _bucket_failed_checks(failed: Any) -> None:
        nonlocal quarantine_schema_mismatch, quarantine_evidence_missing
        nonlocal quarantine_causal_broken, quarantine_other
        if not isinstance(failed, (list, tuple)):
            quarantine_other += 1
            return
        failed_set = {str(f) for f in failed}
        # One certificate can fail several check families at once; count each
        # family independently so an earlier bucket never swallows another's
        # signal in the quarantine-reason distribution.
        if failed_set & _SCHEMA_FAIL_CHECKS:
            quarantine_schema_mismatch += 1
        if failed_set & _EVIDENCE_FAIL_CHECKS:
            quarantine_evidence_missing += 1
        if failed_set & _CAUSAL_FAIL_CHECKS:
            quarantine_causal_broken += 1
        if not failed_set & (_SCHEMA_FAIL_CHECKS | _EVIDENCE_FAIL_CHECKS | _CAUSAL_FAIL_CHECKS):
            quarantine_other += 1

    for audit in court_result.get("error_audits") or []:
        if isinstance(audit, Mapping) and str(audit.get("decision") or "") != "pass":
            _bucket_failed_checks(audit.get("failed_checks"))

    for audit in court_result.get("defense_audits") or []:
        if isinstance(audit, Mapping) and str(audit.get("decision") or "") != "pass":
            _bucket_failed_checks(audit.get("failed_checks"))

    return AdjudicationFacts(
        prosecution_coverage=str(court_result.get("prosecution_coverage") or ""),
        defense_coverage=str(court_result.get("defense_coverage") or ""),
        valid_error_certificate_count=nonnegative_int(
            court_result.get("valid_error_certificate_count")
        ),
        valid_defense_certificate_count=nonnegative_int(
            court_result.get("valid_defense_certificate_count")
        ),
        surviving_error_count=sequence_count(
            court_result.get("surviving_error_certificate_ids")
        ),
        defeated_error_count=sequence_count(
            court_result.get("defeated_error_certificate_ids")
        ),
        quarantined_error_count=sequence_count(quarantined_errors),
        quarantined_defense_count=sequence_count(
            court_result.get("quarantined_defense_certificate_ids") or []
        ),
        consensus_gate_applied=bool(court_result.get("consensus_gate_applied")),
        consensus_gate_blocked_count=sequence_count(blocked),
        required_candidate_count=required_count,
        fully_quarantined=(
            required_count > 0
            and sequence_count(quarantined_errors) >= required_count
        ),
        boundary_path_state=str(boundary_path_state or ""),
        boundary_authority_valid=bool(boundary_authority_valid),
        terminal_state=str(court_result.get("state") or ""),
        appeal_status=str(court_result.get("appeal_status") or ""),
        appeal_triggered=bool(court_result.get("appeal_triggered", False)),
        localization_status=str(court_result.get("localization_status") or ""),
        # The proof court anchors localization on ``responsible_event_id``
        # (always present for a verified_error); ``responsible_step`` is the
        # source-step projection of that anchor and only exists on the
        # top-level result, never inside ``proof_adjudication``. Reading only
        # ``responsible_step`` made this flag always-false, which let the
        # ``responsible_step_missing_and_verdict_error`` predicate phantom-fire
        # on every verified_error case and mis-attribute it to
        # ``earliest_origin_tiebreak``.
        responsible_step_available=bool(
            court_result.get("responsible_step") is not None
            or court_result.get("responsible_event_id") is not None
        ),
        quarantine_reason_schema_mismatch=quarantine_schema_mismatch,
        quarantine_reason_evidence_missing=quarantine_evidence_missing,
        quarantine_reason_causal_broken=quarantine_causal_broken,
        quarantine_reason_other=quarantine_other,
    )


@dataclass(frozen=True)
class PolicyEvaluationResult:
    applied_item_ids: tuple
    fired: tuple
    skipped: tuple
    effective_prosecution_coverage: str
    effective_defense_coverage: str
    telemetry: tuple
    terminal_override: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied_item_ids": list(self.applied_item_ids),
            "fired": [dict(row) for row in self.fired],
            "skipped": [dict(row) for row in self.skipped],
            "effective_prosecution_coverage": self.effective_prosecution_coverage,
            "effective_defense_coverage": self.effective_defense_coverage,
            "telemetry": [dict(row) for row in self.telemetry],
            "terminal_override": self.terminal_override,
        }


def evaluate_policies(
    facts: AdjudicationFacts,
    items: Sequence[PolicyItem],
    registry=None,
) -> PolicyEvaluationResult:
    """Apply L2 policy items to adjudication facts, deterministically.

    Items are evaluated in list order (genome order = deterministic
    priority). Unresolvable preconditions and invalid actions fail closed:
    they are recorded in ``skipped`` and never applied. Actions outside the
    active vocabulary (including the reserved engine-level actions, which
    the runtime does not implement) fail closed as ``unknown_action``.

    ``registry`` is the vocabulary authority the items are resolved against;
    it defaults to the baseline ``L2_REGISTRY`` so existing callers keep the
    baseline behavior. A genome carrying an evolved registry resolves its
    items against that registry, so a pending or disabled entry is never
    executed.
    """
    active_registry = registry if registry is not None else L2_REGISTRY
    eff_prosecution = facts.prosecution_coverage
    eff_defense = facts.defense_coverage
    applied_item_ids: list[str] = []
    fired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    telemetry: list[dict[str, Any]] = []
    terminal_override = ""

    for item in items:
        predicate = active_registry.predicate_fn(item.precondition)
        if predicate is None:
            skipped.append({
                "item_id": item.item_id,
                "precondition": item.precondition,
                "action": item.action,
                "reason": "unknown_predicate",
            })
            continue
        if not predicate(facts):
            continue
        if item.action not in active_registry.action_names():
            skipped.append({
                "item_id": item.item_id,
                "precondition": item.precondition,
                "action": item.action,
                "reason": f"unknown_action:{item.action}",
            })
            continue
        # Parameter validation must resolve against the same registry the
        # vocabulary check used: an evolved-registry action would otherwise
        # pass the active-name check and then fail here against the baseline
        # table, silently never executing.
        params_ok, detail = validate_policy_params(
            item.action, item.params, registry=active_registry,
        )
        if not params_ok:
            skipped.append({
                "item_id": item.item_id,
                "precondition": item.precondition,
                "action": item.action,
                "reason": detail,
            })
            continue

        # A promoted action instance is an alias bound to an operator; the
        # registry is the single authority that resolves alias -> operator,
        # so an enabled alias executes the operator's semantics and a name
        # with no instance resolves to itself (already excluded above).
        operator = active_registry.operator_of(item.action)

        if operator == ACTION_CONSUME_VERIFIED_CLEAN_BOUNDARY:
            # Consume is fail-closed on every runtime precondition: the
            # boundary must itself be verified clean, produced by the
            # authoritative deterministic boundary path, the terminal must
            # still be unresolved, and no error certificate may survive.
            # A fired-but-blocked consume is auditable, never silent.
            consume_guards = (
                facts.boundary_path_state == "verified_clean"
                and facts.boundary_authority_valid
                and facts.terminal_state == "unresolved"
                and facts.surviving_error_count == 0
            )
            if not consume_guards:
                skipped.append({
                    "item_id": item.item_id,
                    "precondition": item.precondition,
                    "action": item.action,
                    "reason": ROUTE_RUNTIME_PRECONDITION_NOT_SATISFIED,
                })
                continue
            applied_item_ids.append(item.item_id)
            fired.append({
                "item_id": item.item_id,
                "precondition": item.precondition,
                "action": item.action,
                "params": {},
            })
            # The court hook owns the actual terminal transition; the
            # evaluator records the audited request plus before/after here
            # so the consumption is reconstructable from the evaluation
            # record alone.
            telemetry.append({
                "kind": "consume_verified_clean_boundary",
                "item_id": item.item_id,
                "terminal_before": facts.terminal_state,
                "terminal_after": "verified_clean",
                "boundary_path_state": facts.boundary_path_state,
                "boundary_authority_valid": facts.boundary_authority_valid,
                "policy_stage": facts.policy_stage,
            })
            terminal_override = "verified_clean"
            continue

        params = dict(item.params)
        applied_item_ids.append(item.item_id)
        fired.append({
            "item_id": item.item_id,
            "precondition": item.precondition,
            "action": item.action,
            "params": dict(params),
        })

        if operator == ACTION_DOWNGRADE_COVERAGE:
            if params["side"] == "prosecution":
                eff_prosecution = _lower_coverage(eff_prosecution, params["to_state"])
            else:
                eff_defense = _lower_coverage(eff_defense, params["to_state"])
        elif operator == ACTION_INVALIDATE_COVERAGE_CLAIM:
            if params["side"] == "prosecution":
                eff_prosecution = "unresolved"
            else:
                eff_defense = "unresolved"
        elif operator == ACTION_EMIT_TELEMETRY_ONLY:
            telemetry.append({
                "kind": "telemetry_only",
                "item_id": item.item_id,
                "rationale": item.rationale,
            })
        elif operator == ACTION_REQUIRE_ADDITIONAL_VERIFICATION:
            telemetry.append({
                "kind": "require_additional_verification",
                "item_id": item.item_id,
                "verification_scope": params.get("verification_scope", ""),
                "current_prosecution_coverage": eff_prosecution,
                "current_defense_coverage": eff_defense,
            })
            # Asking for more verification — of any scope — means a current
            # "complete" claim is not yet earned: suspend it to incomplete.
            # The action carries no side parameter, so the paired
            # precondition scopes the intent and the suspension applies to
            # every side still claiming completeness.
            if eff_prosecution == "complete":
                eff_prosecution = "incomplete"
            if eff_defense == "complete":
                eff_defense = "incomplete"
        elif operator == ACTION_ESCALATE_TO_APPEAL:
            # Telemetry-only: the appeal itself is owned by the calibration
            # path and the court recomputes any terminal edge, so a policy
            # must not fabricate an "under appeal" state here.
            telemetry.append({
                "kind": "escalate_to_appeal",
                "item_id": item.item_id,
                "terminal_state_before": facts.terminal_state,
                "surviving_error_count": facts.surviving_error_count,
            })
        elif operator == ACTION_MARK_CANDIDATE_AMBIGUOUS:
            # Telemetry-only: ambiguity is recorded for later review and
            # never changes coverage or the terminal state — the verdict
            # stays exactly as the court computed it.
            telemetry.append({
                "kind": "mark_candidate_ambiguous",
                "item_id": item.item_id,
                "ambiguity_reason": params.get("ambiguity_reason", ""),
            })

    return PolicyEvaluationResult(
        applied_item_ids=tuple(applied_item_ids),
        fired=tuple(fired),
        skipped=tuple(skipped),
        effective_prosecution_coverage=eff_prosecution,
        effective_defense_coverage=eff_defense,
        telemetry=tuple(telemetry),
        terminal_override=terminal_override,
    )


@dataclass(frozen=True)
class PreLedgerPolicyFacts:
    """Deterministic runtime facts a pre-ledger policy may read.

    Raw certificates are NEVER exposed to a model; only these precomputed,
    court-derived facts are addressable.  ``blocked_*`` mappings are keyed by
    certificate id and carry only closed-vocabulary status values.
    """

    consensus_gate_applied: bool = False
    blocked_certificate_ids: tuple = ()
    blocked_certificate_audit_status: Mapping = None
    blocked_certificate_causal_status: Mapping = None
    blocked_certificate_impact_kind: Mapping = None
    blocked_certificate_defense_status: Mapping = None
    blocked_certificate_quarantine_status: Mapping = None
    terminal_without_restore: str = ""

    def __post_init__(self) -> None:
        # Normalize None mappings to empty dicts for a frozen, total shape.
        for name in (
            "blocked_certificate_audit_status",
            "blocked_certificate_causal_status",
            "blocked_certificate_impact_kind",
            "blocked_certificate_defense_status",
            "blocked_certificate_quarantine_status",
        ):
            if getattr(self, name) is None:
                object.__setattr__(self, name, {})


@dataclass(frozen=True)
class PreLedgerPolicyResult:
    """The outcome of a pre-ledger policy pass: which blocked certificate
    ids an audited restore returned to the surviving set, plus audit rows."""

    restored_certificate_ids: tuple
    fired: tuple
    skipped: tuple

    def to_dict(self) -> dict[str, Any]:
        return {
            "restored_certificate_ids": list(self.restored_certificate_ids),
            "fired": [dict(row) for row in self.fired],
            "skipped": [dict(row) for row in self.skipped],
        }


def _restore_guards(facts: PreLedgerPolicyFacts, certificate_id: str) -> tuple[bool, str]:
    """Fail-closed restore guards.  Every condition must hold."""
    if not facts.consensus_gate_applied:
        return False, "consensus_gate_not_applied"
    if certificate_id not in set(facts.blocked_certificate_ids):
        return False, "certificate_not_in_blocked_set"
    audit_status = facts.blocked_certificate_audit_status.get(certificate_id)
    if audit_status != "pass":
        return False, "schema_validation_not_passed"
    causal = facts.blocked_certificate_causal_status.get(certificate_id)
    if causal not in ("causes_incorrect_result", "propagates_to_incorrect_result"):
        return False, "causal_status_not_causal"
    impact = facts.blocked_certificate_impact_kind.get(certificate_id)
    if impact in ("process_only", "process_debt"):
        return False, "impact_process_only"
    defense = facts.blocked_certificate_defense_status.get(certificate_id)
    if defense == "dominated":
        return False, "defeated_by_dominating_defense"
    quarantine = facts.blocked_certificate_quarantine_status.get(certificate_id)
    if quarantine == "quarantined":
        return False, "quarantined_raw_output"
    return True, ""


def evaluate_pre_ledger_policies(
    facts: PreLedgerPolicyFacts,
    items: Sequence[PolicyItem],
    registry=None,
) -> PreLedgerPolicyResult:
    """Apply pre-ledger policies to the blocked consensus set.

    Only the ``restore_audited_consensus`` operator acts here, and only on a
    certificate that passes every fail-closed guard.  A defeated /
    quarantined / process-only certificate is never restored.  The action
    surfaces the certificate id; the caller returns it to the surviving set
    so the occurrence ledger and earliest-origin localization flow from the
    final surviving set naturally.

    ``registry`` is the vocabulary authority (defaults to the baseline
    ``L2_REGISTRY``); a promoted action alias resolves to its operator here
    exactly as in the post-ledger evaluator.
    """
    active = registry if registry is not None else L2_REGISTRY
    restored: list[str] = []
    fired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in items:
        operator = active.operator_of(item.action)
        if operator != ACTION_RESTORE_AUDITED_CONSENSUS:
            # The pre-ledger evaluator never executes a post-ledger operator.
            skipped.append({
                "item_id": item.item_id,
                "action": item.action,
                "reason": "unknown_stage_for_pre_ledger",
            })
            continue
        certificate_id = str(item.params.get("certificate_id") or "")
        ok, reason = _restore_guards(facts, certificate_id)
        if not ok:
            skipped.append({
                "item_id": item.item_id,
                "action": item.action,
                "certificate_id": certificate_id,
                "reason": reason,
            })
            continue
        restored.append(certificate_id)
        fired.append({
            "item_id": item.item_id,
            "action": item.action,
            "certificate_id": certificate_id,
        })
    return PreLedgerPolicyResult(
        restored_certificate_ids=tuple(restored),
        fired=tuple(fired),
        skipped=tuple(skipped),
    )
