"""SEHD v6 — L2 adjudication-policy reflection prompt and parsing.

L2 policy evolution closes the adjudication-layer proposal loop: when the
deterministic failure classifier attributes a failure to the L2 policy
surface, one reflection call proposes a single closed-vocabulary policy
edit (add_item / modify_item / remove_item). The LLM only *proposes*;
runtime semantics are owned by ``policy_evaluator`` and the proof-court
policy hook, so every vocabulary token shown to the model is generated
from ``v6_contracts`` at call time and every parse result is re-validated
against the same closed sets.
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping

from .v6_contracts import (
    ACTION_PARAMS,
    COVERAGE_DOWNGRADE_STATES,
    L2_ACTIONS,
    L2_PREDICATE_NAMES,
    POLICY_SIDES,
    FailureAttribution,
    PolicyItem,
    validate_policy_params,
)
from .policy_genome import PolicyEdit, PolicyGenome, PolicyOp


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


def _vocabulary_text() -> str:
    """Closed-vocabulary table generated from v6_contracts (never a copied
    literal), so prompt text cannot drift from the evaluator vocabulary."""
    predicate_lines = "\n".join(f"- {name}" for name in L2_PREDICATE_NAMES)
    action_lines = "\n".join(
        f"- {action} required_params={sorted(ACTION_PARAMS.get(action, ()))}"
        for action in sorted(L2_ACTIONS)
    )
    return (
        "CLOSED PRECONDITION PREDICATES (exactly one, over adjudication facts):\n"
        f"{predicate_lines}\n"
        "ENUMERATED ACTIONS (exactly one):\n"
        f"{action_lines}\n"
        f"PARAM VOCABULARY: side in {sorted(POLICY_SIDES)}; "
        f"downgrade_coverage.to_state in {sorted(COVERAGE_DOWNGRADE_STATES)}."
    )


_SYSTEM_PROMPT = (
    "You propose exactly ONE L2 adjudication policy edit for the PACA-TCL "
    "proof court. A policy item is a pair (closed precondition predicate, "
    "enumerated action with closed-vocabulary parameters) over the "
    "adjudication facts of one detection. Its runtime semantics are fixed "
    "by a deterministic evaluator inside the proof court: you only propose "
    "the edit, you never interpret or execute it, and anything outside the "
    "closed vocabulary is discarded without effect.\n"
    "Operations: add_item proposes a new item; modify_item replaces the "
    "item at item_index with a corrected item; remove_item deletes the item "
    "at item_index. Use modify_item or remove_item only on an item from the "
    "CURRENT POLICY SET whose observed effect is wrong, harmful, or dead; "
    "otherwise propose add_item for a newly observed adjudication-layer "
    "failure, or no_proposal when no vocabulary edit is justified. Prefer "
    "conservative actions that weaken an unsupported coverage claim over "
    "actions that rewrite verdicts.\n\n"
    "{vocabulary}\n\n"
    "RESPONSE CONTRACT (single JSON object):\n"
    '{{"operation":"add_item","precondition": "<predicate name>", '
    '"action": "<action name>", "params": {{"<key>": "<closed value>"}}, '
    '"rationale": "<one sentence>"}}\n'
    'or {{"operation":"modify_item","item_index": <int>, '
    '"precondition": "<predicate name>", "action": "<action name>", '
    '"params": {{"<key>": "<closed value>"}}, "rationale": "<one sentence>"}}\n'
    'or {{"operation":"remove_item","item_index": <int>, '
    '"rationale": "<one sentence>"}}\n'
    'or {{"no_proposal": true, "reason": "<one sentence>"}}'
)


def build_policy_reflection_prompt(
    *,
    attribution: FailureAttribution,
    facts_summary: Mapping[str, Any],
    current_policy_text: str,
    rejected_summary: str = "",
) -> tuple[str, str]:
    """Build the (system, user) prompt pair for one L2 policy reflection.

    The user block carries the attribution triple, the addressable fact
    counts, the current policy set and the rejected-history summary; no
    gold label or evaluator metadata is included.
    """
    import json

    system = _SYSTEM_PROMPT.format(vocabulary=_vocabulary_text())
    facts_text = json.dumps(dict(facts_summary), ensure_ascii=False, sort_keys=True)
    evidence_text = json.dumps(
        dict(attribution.evidence), ensure_ascii=False, sort_keys=True,
    )
    rejected_block = _sanitize_rejected_summary(rejected_summary) or "(none)"
    user = (
        "FAILURE ATTRIBUTION TRIPLE:\n"
        f"- layer: {attribution.layer}\n"
        f"- artifact: {attribution.artifact}\n"
        f"- reason_code: {attribution.reason_code}\n"
        f"- evidence: {evidence_text}\n\n"
        f"ADJUDICATION FACTS (addressable counts and states):\n{facts_text}\n\n"
        f"CURRENT POLICY SET (genome order):\n{current_policy_text}\n\n"
        f"REJECTED POLICY HISTORY (do not repeat these failures):\n{rejected_block}\n\n"
        "Return exactly one JSON object following the response contract."
    )
    return system, user


def parse_policy_proposal(
    response: Mapping[str, Any],
) -> tuple[PolicyEdit | None, str]:
    """Parse one reflection response into a closed-vocabulary PolicyEdit.

    Returns ``(None, reason)`` for no_proposal, unknown operations,
    unaddressable targets and vocabulary violations. The caller assigns
    item identity (``item_id`` / ``source_case_id``) because identity
    belongs to the evolution loop, not to the model output. Legacy
    add-only responses without an ``operation`` field stay addressable.
    """
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
        if precondition not in L2_PREDICATE_NAMES:
            return None, f"unknown_precondition:{precondition}"
        action = str(response.get("action") or "")
        if action not in L2_ACTIONS:
            return None, f"unknown_action:{action}"
        raw_params = response.get("params") or {}
        if not isinstance(raw_params, Mapping):
            return None, "invalid_params_type"
        params = {str(key): value for key, value in raw_params.items()}
        params_valid, params_reason = validate_policy_params(action, params)
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
