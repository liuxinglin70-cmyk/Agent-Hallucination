"""SEHD v6 — L2 adjudication-policy reflection prompt and parsing.

L2 policy evolution closes the adjudication-layer proposal loop: when the
deterministic failure classifier attributes a failure to the L2 policy
surface, one reflection call proposes a single closed-vocabulary policy
item. The LLM only *proposes*; runtime semantics are owned by
``policy_evaluator`` and the proof-court policy hook, so every vocabulary
token shown to the model is generated from ``v6_contracts`` at call time
and every parse result is re-validated against the same closed sets.
"""

from __future__ import annotations

from typing import Any, Mapping

from .v6_contracts import (
    ACTION_PARAMS,
    CANDIDATE_TRI_STATES,
    COVERAGE_DOWNGRADE_STATES,
    L2_ACTIONS,
    L2_PREDICATE_NAMES,
    POLICY_SIDES,
    FailureAttribution,
    PolicyItem,
    validate_policy_params,
)
from .policy_genome import PolicyGenome


def current_policy_text(genome: PolicyGenome) -> str:
    """Render the policy genome as one ``precondition -> action(params)``
    line per item; the empty baseline renders an explicit placeholder so
    the reflection prompt never carries a blank context block."""
    if not genome.items:
        return "(empty policy set)"
    lines = []
    for item in genome.items:
        params = ", ".join(
            f"{key}={value}" for key, value in sorted(item.params.items())
        )
        lines.append(f"{item.precondition} -> {item.action}({params})")
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
        f"downgrade_coverage.to_state in {sorted(COVERAGE_DOWNGRADE_STATES)}; "
        f"reclassify_candidate_state.state in {sorted(CANDIDATE_TRI_STATES)}; "
        "candidate_ref and route_key are non-empty strings."
    )


_SYSTEM_PROMPT = (
    "You propose exactly ONE L2 adjudication policy item for the PACA-TCL "
    "proof court. An L2 policy item is a pair (closed precondition "
    "predicate, enumerated action with closed-vocabulary parameters) over "
    "the adjudication facts of one detection. Its runtime semantics are "
    "fixed by a deterministic evaluator inside the proof court: you only "
    "propose the item, you never interpret or execute it, and an item "
    "outside the closed vocabulary is discarded without effect.\n"
    "Propose an item only when the attribution triple and the fact counts "
    "show an adjudication-layer failure that one vocabulary item can "
    "address (hollow complete coverage, consensus-gate blocking, boundary "
    "conflict). Prefer conservative actions that weaken an unsupported "
    "coverage claim over actions that rewrite verdicts. If no vocabulary "
    "item is justified, return a no_proposal response instead.\n\n"
    "{vocabulary}\n\n"
    "RESPONSE CONTRACT (single JSON object):\n"
    '{{"precondition": "<predicate name>", "action": "<action name>", '
    '"params": {{"<key>": "<closed value>"}}, "rationale": "<one sentence>"}}\n'
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
    rejected_block = rejected_summary.strip() or "(none)"
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
) -> tuple[PolicyItem | None, str]:
    """Parse one reflection response into a PolicyItem.

    Returns ``(None, reason)`` for no_proposal, unknown vocabulary tokens
    and invalid parameters; the caller generates ``item_id`` (and
    ``source_case_id``) because identity belongs to the evolution loop,
    not to the model output.
    """
    if not isinstance(response, Mapping) or not response:
        return None, "empty_response"
    if response.get("no_proposal"):
        return None, str(response.get("reason") or "no_proposal")
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
    item = PolicyItem(
        item_id="",
        precondition=precondition,
        action=action,
        params=dict(params),
        rationale=str(response.get("rationale") or "")[:500],
    )
    return item, ""
