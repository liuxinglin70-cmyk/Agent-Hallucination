"""SEHD v6 — L2 adjudication-policy reflection prompt and parsing.

The deterministic failure classifier first maps an attributed failure to
the executable repairs owned by the current policy runtime. A model may
select one precomputed edit or abstain, but it cannot compose predicates,
actions, parameters, or semantics. Candidate execution remains owned by
``policy_evaluator`` and the proof-court policy hook.

The legacy free-form prompt/parser remain available only for reading older
artifacts and exact candidate matching; the online evolution runner does not
use them as a policy source.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from .v6_contracts import (
    ACTION_CONSUME_VERIFIED_CLEAN_BOUNDARY,
    FEEDBACK_POLICY_ROUTE_UNAVAILABLE,
    POLICY_ROUTES,
    ROUTE_CANDIDATE_ALREADY_PRESENT,
    ROUTE_ENGINE_LEVEL_INTERVENTION_REQUIRED,
    ROUTE_EXECUTED,
    ROUTE_NO_REGISTERED_RUNTIME_ACTION,
    FailureAttribution,
    PolicyItem,
    validate_policy_params,
)
from .policy_genome import PolicyEdit, PolicyGenome, PolicyOp
from .vocabulary_registry import L2_REGISTRY


@dataclass(frozen=True)
class PolicyPlan:
    """Outcome of the deterministic L2 planner for one attribution.

    ``candidates`` are the executable edits the model may select from;
    ``route_result`` is the closed-vocabulary explanation of the planning
    outcome (ROUTE_EXECUTED means at least one candidate exists) and
    ``feedback_code`` is the prompt-safe categorical companion.
    """
    candidates: tuple[PolicyEdit, ...] = ()
    route_result: str = ROUTE_EXECUTED
    feedback_code: str = ""

    def __post_init__(self) -> None:
        if self.route_result not in POLICY_ROUTES:
            raise ValueError(f"unknown route_result: {self.route_result}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "route_result": self.route_result,
            "feedback_code": self.feedback_code,
        }


def _planned_edit_id(
    *,
    operation: str,
    item_index: int | None,
    precondition: str = "",
    action: str = "",
    params: Mapping[str, Any] | None = None,
) -> str:
    payload = json.dumps({
        "operation": operation,
        "item_index": item_index,
        "precondition": precondition,
        "action": action,
        "params": dict(params or {}),
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"policy_edit_{digest}"


def plan_policy_candidates(
    *,
    attribution: FailureAttribution,
    genome: PolicyGenome,
) -> PolicyPlan:
    """Enumerate executable policy repairs owned by one attribution.

    The model may rank these candidates but cannot compose predicate/action
    pairs. When no candidate exists the plan still carries a structured
    route result so the audit trail explains, in closed vocabulary, why the
    attribution could not be executed.
    """
    reason = attribution.reason_code
    terminal_unresolved = (
        str(attribution.evidence.get("terminal_state") or "") == "unresolved"
    )
    if reason == "consensus_gate_blocked" and terminal_unresolved:
        # The consensus gate sits before the occurrence ledger, so a blocked
        # certificate cannot be repaired by any post-ledger action: the
        # restore route requires a pre-ledger engine intervention that the
        # current runtime does not implement. The route is only meaningful
        # while the terminal is unresolved; a consensus attribution that
        # already carries a verdict falls through to the fail-closed
        # no-registered-action route instead of claiming an engine repair.
        return PolicyPlan(
            candidates=(),
            route_result=ROUTE_ENGINE_LEVEL_INTERVENTION_REQUIRED,
            feedback_code=FEEDBACK_POLICY_ROUTE_UNAVAILABLE,
        )
    templates: list[tuple[str, str, dict[str, Any], str]] = []
    if reason == "hollow_complete_prosecution":
        templates.append((
            "prosecution_coverage_complete_and_zero_certificates",
            "invalidate_coverage_claim",
            {"side": "prosecution"},
            "Invalidate a prosecution coverage claim with no valid certificate.",
        ))
    elif reason == "hollow_complete_defense":
        templates.append((
            "defense_coverage_complete_and_zero_certificates",
            "invalidate_coverage_claim",
            {"side": "defense"},
            "Invalidate a defense coverage claim with no valid certificate.",
        ))
    elif reason == "all_candidates_quarantined":
        templates.append((
            "all_candidates_quarantined",
            "downgrade_coverage",
            {"side": "prosecution", "to_state": "unresolved"},
            "Keep prosecution coverage unresolved when all candidates are quarantined.",
        ))
    elif reason == "boundary_terminal_conflict":
        templates.append((
            "boundary_state_conflicts_with_terminal",
            ACTION_CONSUME_VERIFIED_CLEAN_BOUNDARY,
            {},
            "Consume the verified-clean boundary path as the terminal verdict.",
        ))
    if not templates:
        return PolicyPlan(
            candidates=(),
            route_result=ROUTE_NO_REGISTERED_RUNTIME_ACTION,
            feedback_code=FEEDBACK_POLICY_ROUTE_UNAVAILABLE,
        )

    existing = {
        (item.precondition, item.action, tuple(sorted(item.params.items())))
        for item in genome.items
    }
    templates_blocked_by_dedup = 0
    candidates: list[PolicyEdit] = []
    for precondition, action, params, rationale in templates:
        identity = (precondition, action, tuple(sorted(params.items())))
        if identity in existing:
            templates_blocked_by_dedup += 1
            continue
        candidates.append(PolicyEdit(
            edit_id=_planned_edit_id(
                operation=PolicyOp.ADD_ITEM.value,
                item_index=None,
                precondition=precondition,
                action=action,
                params=params,
            ),
            operation=PolicyOp.ADD_ITEM.value,
            new_item=PolicyItem(
                item_id="",
                precondition=precondition,
                action=action,
                params=params,
                rationale=rationale,
            ),
            rationale=rationale,
        ))
    for index, item in enumerate(genome.items):
        if item.is_baseline or item.precondition not in {
            template[0] for template in templates
        }:
            continue
        alternatives: tuple[tuple[str, dict[str, Any]], ...]
        if item.action == ACTION_CONSUME_VERIFIED_CLEAN_BOUNDARY:
            # Boundary consumption is parameterless; the only executable
            # alternative is removing the item again.
            alternatives = ()
        else:
            alternatives = (
                ("invalidate_coverage_claim", {"side": item.params.get("side")}),
                (
                    "downgrade_coverage",
                    {"side": item.params.get("side"), "to_state": "incomplete"},
                ),
                (
                    "downgrade_coverage",
                    {"side": item.params.get("side"), "to_state": "unresolved"},
                ),
            )
        for action, params in alternatives:
            if not params.get("side"):
                continue
            identity = (item.precondition, action, tuple(sorted(params.items())))
            current_identity = (
                item.precondition, item.action, tuple(sorted(item.params.items()))
            )
            if identity == current_identity:
                continue
            rationale = "Replace the existing correction with this executable alternative."
            candidates.append(PolicyEdit(
                edit_id=_planned_edit_id(
                    operation=PolicyOp.MODIFY_ITEM.value,
                    item_index=index,
                    precondition=item.precondition,
                    action=action,
                    params=params,
                ),
                operation=PolicyOp.MODIFY_ITEM.value,
                item_index=index,
                new_item=PolicyItem(
                    item_id="",
                    precondition=item.precondition,
                    action=action,
                    params=params,
                    rationale=rationale,
                ),
                rationale=rationale,
            ))
        candidates.append(PolicyEdit(
            edit_id=_planned_edit_id(
                operation=PolicyOp.REMOVE_ITEM.value,
                item_index=index,
                precondition=item.precondition,
                action=item.action,
                params=item.params,
            ),
            operation=PolicyOp.REMOVE_ITEM.value,
            item_index=index,
            rationale="Remove the existing correction if it no longer repairs the attribution.",
        ))
    if not candidates and templates and templates_blocked_by_dedup == len(templates):
        return PolicyPlan(
            candidates=(),
            route_result=ROUTE_CANDIDATE_ALREADY_PRESENT,
            feedback_code=FEEDBACK_POLICY_ROUTE_UNAVAILABLE,
        )
    return PolicyPlan(candidates=tuple(candidates))


def build_policy_selection_prompt(
    *,
    attribution: FailureAttribution,
    facts_summary: Mapping[str, Any],
    candidates: tuple[PolicyEdit, ...],
    meta_guidance: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Ask the model only to select a deterministic candidate or abstain.

    ``meta_guidance`` is the bounded, prompt-safe Meta projection
    (``project_meta_review_for_prompt``; closed vocabulary, identity-free,
    gold-free, ``authority="evolution_guidance_only"``).  It can inform the
    selection but never enlarges the candidate set, never changes an owner
    route, and never relaxes the gate.  Omitted by default so existing call
    sites and provider cache keys are unchanged.
    """
    import json

    system = (
        "Select at most one precomputed L2 intervention. Runtime semantics, "
        "predicate/action pairing and parameters are fixed by the framework. "
        "Return JSON only: {\"candidate_index\": <int>, \"reason\": \"...\"} "
        "or {\"no_proposal\": true, \"reason\": \"...\"}."
    )
    payload = {
        "attribution": attribution.to_dict(),
        "facts": dict(facts_summary),
        "candidates": [candidate.to_dict() for candidate in candidates],
    }
    if meta_guidance:
        # Only the closed-vocabulary projection keys are forwarded.
        payload["meta_guidance"] = {
            "authority": "evolution_guidance_only",
            "guidance_tokens": list(meta_guidance.get("guidance_tokens") or ()),
            "ranking_preferences": list(
                meta_guidance.get("ranking_preferences") or ()
            ),
        }
    return system, json.dumps(payload, ensure_ascii=False, sort_keys=True)


def select_policy_candidate(
    response: Mapping[str, Any],
    candidates: tuple[PolicyEdit, ...],
    registry=None,
) -> tuple[PolicyEdit | None, str]:
    if response.get("no_proposal"):
        return None, str(response.get("reason") or "no_proposal")
    index = response.get("candidate_index")
    if isinstance(index, int) and not isinstance(index, bool):
        if not 0 <= index < len(candidates):
            return None, "candidate_index_out_of_range"
        return candidates[index], ""

    legacy, reason = parse_policy_proposal(response, registry=registry)
    if legacy is None:
        return None, reason or "missing_candidate_index"

    def signature(edit: PolicyEdit) -> tuple[Any, ...]:
        item = edit.new_item
        return (
            str(edit.operation),
            edit.item_index,
            str(item.precondition) if item else "",
            str(item.action) if item else "",
            tuple(sorted(item.params.items())) if item else (),
        )

    legacy_signature = signature(legacy)
    for candidate in candidates:
        if signature(candidate) == legacy_signature:
            return candidate, ""
    return None, "response_not_in_planned_candidates"


def _sanitize_rejected_summary(value: str) -> str:
    """Keep policy feedback categorical before it enters the next prompt."""
    import re

    text = " ".join(str(value or "").split())
    text = re.sub(
        r"\b(?:case|source)[_/ -][A-Za-z0-9_.:/-]+",
        "[case]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b[A-Za-z][A-Za-z0-9_.-]*/\d+\b", "[case]", text)
    return text[:1200]


def current_policy_text(genome: PolicyGenome) -> str:
    """Render the policy genome as one addressable line per item.

    The bracketed index is the edit address for modify_item/remove_item,
    matching the ``item_index`` contract of ``validate_policy_edit``; the
    empty baseline renders an explicit placeholder so the reflection prompt
    never carries a blank context block.
    """
    if not genome.items:
        return "(empty policy set)"
    lines = []
    for index, item in enumerate(genome.items):
        params = ", ".join(
            f"{key}={value}" for key, value in sorted(item.params.items())
        )
        lines.append(
            f"[{index}] {item.precondition} -> {item.action}({params})"
        )
    return "\n".join(lines)




def parse_policy_proposal(
    response: Mapping[str, Any],
    registry=None,
) -> tuple[PolicyEdit | None, str]:
    """Parse one reflection response into a closed-vocabulary PolicyEdit.

    Returns ``(None, reason)`` for no_proposal, unknown operations,
    unaddressable targets and vocabulary violations. The caller assigns
    item identity (``item_id`` / ``source_case_id``) because identity
    belongs to the evolution loop, not to the model output. Legacy
    add-only responses without an ``operation`` field stay addressable.
    ``registry`` is the vocabulary authority the response is validated
    against; it defaults to the baseline registry.
    """
    active = registry if registry is not None else L2_REGISTRY
    if not isinstance(response, Mapping) or not response:
        return None, "empty_response"
    if response.get("no_proposal"):
        return None, str(response.get("reason") or "no_proposal")
    operation = str(response.get("operation") or "").strip()
    if not operation:
        operation = PolicyOp.ADD_ITEM.value
    if operation not in {op.value for op in PolicyOp}:
        return None, f"unknown_operation:{operation}"
    rationale = str(response.get("rationale") or "")[:500]

    item_index = response.get("item_index")
    if operation in {PolicyOp.MODIFY_ITEM.value, PolicyOp.REMOVE_ITEM.value}:
        if (
            not isinstance(item_index, int)
            or isinstance(item_index, bool)
            or item_index < 0
        ):
            return None, "missing_item_index"

    new_item: PolicyItem | None = None
    if operation in {PolicyOp.ADD_ITEM.value, PolicyOp.MODIFY_ITEM.value}:
        precondition = str(response.get("precondition") or "")
        if precondition not in active.predicate_names():
            return None, f"unknown_precondition:{precondition}"
        action = str(response.get("action") or "")
        if action not in active.action_names():
            return None, f"unknown_action:{action}"
        raw_params = response.get("params") or {}
        if not isinstance(raw_params, Mapping):
            return None, "invalid_params_type"
        params = {str(key): value for key, value in raw_params.items()}
        params_valid, params_reason = validate_policy_params(
            action, params, registry=active,
        )
        if not params_valid:
            return None, params_reason
        new_item = PolicyItem(
            item_id="",
            precondition=precondition,
            action=action,
            params=dict(params),
            rationale=rationale,
        )

    return PolicyEdit(
        edit_id=f"policy_edit_{uuid.uuid4().hex[:8]}",
        operation=operation,
        item_index=(
            item_index if operation != PolicyOp.ADD_ITEM.value else None
        ),
        new_item=new_item,
        rationale=rationale,
    ), ""
