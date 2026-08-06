"""Deterministic evaluator for L2 adjudication policy items.

L2 policy items are closed-vocabulary (precondition predicate -> enumerated
action) pairs proposed by the LLM during evolution. Their runtime semantics
are fixed entirely by this module: the LLM never interprets or executes an
item, so policy application cannot reintroduce nondeterminism into the
proof court. Pure functions, no I/O, evaluation order equals genome order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ..bccse.v6_contracts import (
    ACTION_CONSUME_VERIFIED_CLEAN_BOUNDARY,
    ACTION_DOWNGRADE_COVERAGE,
    ACTION_EMIT_TELEMETRY_ONLY,
    ACTION_INVALIDATE_COVERAGE_CLAIM,
    ROUTE_RUNTIME_PRECONDITION_NOT_SATISFIED,
    AdjudicationFacts,
    L2_ACTIONS,
    L2_PREDICATES,
    PolicyItem,
    validate_policy_params,
)
from ..bccse.vocabulary_registry import L2_REGISTRY

_COVERAGE_RANK = {"unresolved": 0, "incomplete": 1, "complete": 2}


def _coverage_rank(state: str) -> int:
    # Unrecognized strings rank below "unresolved" so a policy can never
    # "upgrade" a coverage value the vocabulary does not know (fail-closed).
    return _COVERAGE_RANK.get(state, -1)


def _lower_coverage(a: str, b: str) -> str:
    return a if _coverage_rank(a) <= _coverage_rank(b) else b


@dataclass(frozen=True)
class PolicyEvaluationResult:
    applied_item_ids: tuple
    fired: tuple
    skipped: tuple
    effective_prosecution_coverage: str
    effective_defense_coverage: str
    telemetry: tuple
    terminal_state: str
    terminal_override: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied_item_ids": list(self.applied_item_ids),
            "fired": [dict(row) for row in self.fired],
            "skipped": [dict(row) for row in self.skipped],
            "effective_prosecution_coverage": self.effective_prosecution_coverage,
            "effective_defense_coverage": self.effective_defense_coverage,
            "telemetry": [dict(row) for row in self.telemetry],
            "terminal_state": self.terminal_state,
            "terminal_override": self.terminal_override,
        }


def _recompute_terminal_state(
    surviving_error_count: int,
    prosecution_coverage: str,
    defense_coverage: str,
) -> str:
    # Simplified recomputation over court-addressable facts only: the
    # candidate-terminal coverage condition of the full court is not
    # addressable here; the proof-court policy hook applies the full rule.
    if surviving_error_count > 0:
        return "verified_error"
    if prosecution_coverage == "complete" and defense_coverage == "complete":
        return "verified_clean"
    return "unresolved"


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
        params_ok, detail = validate_policy_params(item.action, item.params)
        if not params_ok:
            skipped.append({
                "item_id": item.item_id,
                "precondition": item.precondition,
                "action": item.action,
                "reason": detail,
            })
            continue

        if item.action == ACTION_CONSUME_VERIFIED_CLEAN_BOUNDARY:
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

        if item.action == ACTION_DOWNGRADE_COVERAGE:
            if params["side"] == "prosecution":
                eff_prosecution = _lower_coverage(eff_prosecution, params["to_state"])
            else:
                eff_defense = _lower_coverage(eff_defense, params["to_state"])
        elif item.action == ACTION_INVALIDATE_COVERAGE_CLAIM:
            if params["side"] == "prosecution":
                eff_prosecution = "unresolved"
            else:
                eff_defense = "unresolved"
        elif item.action == ACTION_EMIT_TELEMETRY_ONLY:
            telemetry.append({
                "kind": "telemetry_only",
                "item_id": item.item_id,
                "rationale": item.rationale,
            })

    return PolicyEvaluationResult(
        applied_item_ids=tuple(applied_item_ids),
        fired=tuple(fired),
        skipped=tuple(skipped),
        effective_prosecution_coverage=eff_prosecution,
        effective_defense_coverage=eff_defense,
        telemetry=tuple(telemetry),
        terminal_state=_recompute_terminal_state(
            facts.surviving_error_count, eff_prosecution, eff_defense,
        ),
        terminal_override=terminal_override,
    )
