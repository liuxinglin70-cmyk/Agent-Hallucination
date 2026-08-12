"""ExecutionTrace: the single execution truth for one replay.

The provider's own bookkeeping (``bccse_injected_rule_ids`` /
``bccse_applied_rule_ids`` / ``bccse_condition_matched_rule_ids`` /
``bccse_condition_not_matched_rule_ids`` / ``bccse_candidate_rule_ids`` /
``bccse_rule_effects`` / ``bccse_condition_features``) is walked into
route-keyed trace buckets, so a no-effect replay can distinguish "rule not
injected" from "injected but ignored" and a regression audit can read the
exact pre-call feature projection. ``_project_execution_trace`` maps a
step's outcome onto the closed TRACE_CLASSES vocabulary.

These are pure readers of the detection result and the step view; no
candidate text or provider output is constructed here. The ``EvolutionStep``
dataclass lands in the loop layer (Phase 9); this module types the step as a
structural ``_StepView`` Protocol (``gate_decision`` / ``edit_proposal``),
so the loop's step satisfies it without an upward import.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from sehd_rise.skills.genome import SkillEdit
from sehd_rise.detection.injection import _rule_stable_id


class _StepView(Protocol):
    """Read surface ``_project_execution_trace`` consumes off a step."""

    gate_decision: str
    edit_proposal: Mapping[str, Any] | None


def _rule_in_trace(rule_id: str, trace: Any) -> bool:
    """Whether a stable rule id appears in an injected/applied rule trace."""
    if not rule_id:
        return False
    if not isinstance(trace, Mapping):
        return False
    return any(
        str(value) == rule_id
        for bucket in trace.values()
        if isinstance(bucket, (list, tuple))
        for value in bucket
    )


def _injected_applied_flags(
    edit: SkillEdit,
    candidate_result: Mapping[str, Any] | None,
) -> tuple[bool, bool]:
    """Execution evidence for one edit on its source replay.

    The rule id is derived from module + normalized condition (same stable
    id the provider traces use), so injected/applied are read from the
    provider's own self-report, never guessed from the final state.
    """
    candidate_result = candidate_result or {}
    condition = str(edit.rule_slots.get("condition") or "")
    rule_id = _rule_stable_id(
        edit.target_module,
        edit.new_content,
        condition=condition or None,
        condition_spec=edit.condition_spec,
    )
    injected = _rule_in_trace(
        rule_id, candidate_result.get("injected_rule_trace")
    )
    applied = _rule_in_trace(
        rule_id, candidate_result.get("applied_rule_trace")
    )
    return injected, applied


def _collect_rule_trace(
    result: Mapping[str, Any],
    meta_key: str,
) -> dict[str, list[str]]:
    """Aggregate which evolved rule ids each provider route reported.

    The skill-aware provider wrapper records rule ids in every call's meta
    (``bccse_injected_rule_ids`` for what it injected, ``bccse_applied_rule_ids``
    for what the provider self-reported applying); this walks the detection
    result's provider bookkeeping and returns route -> rule ids (order-
    preserving, deduplicated). Distinct from the compiled trace: a route that
    never ran contributes nothing, so a no-effect replay can distinguish
    "rule not injected" from "injected but ignored".
    """
    providers = result.get("providers")
    if not isinstance(providers, Mapping):
        return {}
    trace: dict[str, list[str]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            rule_ids = node.get(meta_key)
            if isinstance(rule_ids, list) and rule_ids:
                route_key = str(node.get("bccse_route") or "unknown_route")
                bucket = trace.setdefault(route_key, [])
                for rule_id in rule_ids:
                    if str(rule_id) not in bucket:
                        bucket.append(str(rule_id))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(providers)
    return trace


def _collect_injected_rule_trace(
    result: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Which evolved rule ids each provider route actually received."""
    return _collect_rule_trace(result, "bccse_injected_rule_ids")


def _collect_applied_rule_trace(
    result: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Which evolved rule ids each provider self-reported applying."""
    return _collect_rule_trace(result, "bccse_applied_rule_ids")


def _collect_condition_matched_rule_trace(
    result: Mapping[str, Any],
) -> dict[str, list[str]]:
    return _collect_rule_trace(result, "bccse_condition_matched_rule_ids")


def _collect_condition_not_matched_rule_trace(
    result: Mapping[str, Any],
) -> dict[str, list[str]]:
    return _collect_rule_trace(result, "bccse_condition_not_matched_rule_ids")


def _collect_candidate_rule_trace(
    result: Mapping[str, Any],
) -> dict[str, list[str]]:
    return _collect_rule_trace(result, "bccse_candidate_rule_ids")


def _collect_rule_effects_trace(
    result: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Field-level binding: which output fields each applied rule affected,
    per provider route, from the provider's ``rule_effects`` self-report."""
    providers = result.get("providers")
    if not isinstance(providers, Mapping):
        return {}
    trace: dict[str, list[dict[str, Any]]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            effects = node.get("bccse_rule_effects")
            if isinstance(effects, list) and effects:
                route_key = str(node.get("bccse_route") or "unknown_route")
                bucket = trace.setdefault(route_key, [])
                for effect in effects:
                    if not isinstance(effect, Mapping):
                        continue
                    rule_id = str(effect.get("rule_id") or "").strip()
                    if not rule_id:
                        continue
                    fields = effect.get("output_fields")
                    field_list = (
                        [str(field) for field in fields]
                        if isinstance(fields, list) else []
                    )
                    entry = {"rule_id": rule_id, "output_fields": field_list}
                    if entry not in bucket:
                        bucket.append(entry)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(providers)
    return trace


def _collect_condition_feature_trace(
    result: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Exact pre-call feature projections for every provider invocation."""
    providers = result.get("providers")
    if not isinstance(providers, Mapping):
        return {}
    trace: dict[str, list[dict[str, Any]]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            features = node.get("bccse_condition_features")
            if isinstance(features, Mapping):
                route_key = str(node.get("bccse_route") or "unknown_route")
                bounded = dict(features)
                bucket = trace.setdefault(route_key, [])
                if bounded not in bucket:
                    bucket.append(bounded)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(providers)
    return trace


def _project_execution_trace(
    step: _StepView, *,
    final_attempt: Mapping[str, Any] | None,
    l2_policy: Mapping[str, Any] | None,
) -> str:
    """Project a step's execution outcome onto the TRACE_CLASSES vocabulary.

    Deterministic and total: runtime-invalid decisions map to
    ``runtime_invalid``; proposal-less steps map to ``no_proposal``; L2
    proposals resolve to ``fired``/``skipped`` from the replay's
    policy_evaluation; L1 proposals fall back to the final attempt's
    compiled/applied flags.
    """
    decision = str(step.gate_decision or "")
    if decision in ("skip_runtime_invalid", "reject_policy_replay_error"):
        return "runtime_invalid"
    proposal = step.edit_proposal if isinstance(step.edit_proposal, Mapping) else {}
    if not proposal.get("operation"):
        return "no_proposal"
    if l2_policy is not None:
        policy_eval = l2_policy.get("policy_evaluation") or {}
        new_item = proposal.get("new_item")
        proposal_item_id = (
            str(new_item.get("item_id") or "")
            if isinstance(new_item, Mapping) else ""
        )
        if proposal_item_id:
            for row in (policy_eval.get("fired") or ()):
                if isinstance(row, Mapping) and str(
                    row.get("item_id") or ""
                ) == proposal_item_id:
                    return "fired"
            for row in (policy_eval.get("skipped") or ()):
                if isinstance(row, Mapping) and str(
                    row.get("item_id") or ""
                ) == proposal_item_id:
                    return "skipped"
    if isinstance(final_attempt, Mapping):
        if final_attempt.get("applied"):
            return "applied"
        if final_attempt.get("compiled"):
            return "compiled"
    return "no_proposal"


__all__ = [
    "_rule_in_trace",
    "_injected_applied_flags",
    "_collect_rule_trace",
    "_collect_injected_rule_trace",
    "_collect_applied_rule_trace",
    "_collect_condition_matched_rule_trace",
    "_collect_condition_not_matched_rule_trace",
    "_collect_candidate_rule_trace",
    "_collect_rule_effects_trace",
    "_collect_condition_feature_trace",
    "_project_execution_trace",
]
