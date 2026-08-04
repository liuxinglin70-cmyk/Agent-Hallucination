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
    ACTION_DOWNGRADE_COVERAGE,
    ACTION_EMIT_TELEMETRY_ONLY,
    ACTION_INVALIDATE_COVERAGE_CLAIM,
    ACTION_RECLASSIFY_CANDIDATE_STATE,
    ACTION_REQUIRE_ROUTE_REPLAY,
    AdjudicationFacts,
    L2_ACTIONS,
    L2_PREDICATES,
    PolicyItem,
    validate_policy_params,
)

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
    candidate_reclassifications: dict
    route_replay_requests: tuple
    telemetry: tuple
    terminal_state: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied_item_ids": list(self.applied_item_ids),
            "fired": [dict(row) for row in self.fired],
            "skipped": [dict(row) for row in self.skipped],
            "effective_prosecution_coverage": self.effective_prosecution_coverage,
            "effective_defense_coverage": self.effective_defense_coverage,
            "candidate_reclassifications": dict(self.candidate_reclassifications),
            "route_replay_requests": list(self.route_replay_requests),
            "telemetry": [dict(row) for row in self.telemetry],
            "terminal_state": self.terminal_state,
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
) -> PolicyEvaluationResult:
    """Apply L2 policy items to adjudication facts, deterministically.

    Items are evaluated in list order (genome order = deterministic
    priority). Unresolvable preconditions and invalid actions fail closed:
    they are recorded in ``skipped`` and never applied. Engine-level actions
    are surfaced as effects without touching court-local state.
    """
    eff_prosecution = facts.prosecution_coverage
    eff_defense = facts.defense_coverage
    applied_item_ids: list[str] = []
    fired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    telemetry: list[dict[str, Any]] = []
    reclassifications: dict[str, str] = {}
    replay_requests: list[str] = []

    for item in items:
        predicate = L2_PREDICATES.get(item.precondition)
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
        if item.action not in L2_ACTIONS:
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
        elif item.action == ACTION_RECLASSIFY_CANDIDATE_STATE:
            # Last write wins; every write is traced so overrides are auditable.
            reclassifications[params["candidate_ref"]] = params["state"]
            telemetry.append({
                "kind": "candidate_reclassification",
                "item_id": item.item_id,
                "candidate_ref": params["candidate_ref"],
                "state": params["state"],
            })
        elif item.action == ACTION_REQUIRE_ROUTE_REPLAY:
            replay_requests.append(params["route_key"])

    return PolicyEvaluationResult(
        applied_item_ids=tuple(applied_item_ids),
        fired=tuple(fired),
        skipped=tuple(skipped),
        effective_prosecution_coverage=eff_prosecution,
        effective_defense_coverage=eff_defense,
        candidate_reclassifications=reclassifications,
        route_replay_requests=tuple(replay_requests),
        telemetry=tuple(telemetry),
        terminal_state=_recompute_terminal_state(
            facts.surviving_error_count, eff_prosecution, eff_defense,
        ),
    )
