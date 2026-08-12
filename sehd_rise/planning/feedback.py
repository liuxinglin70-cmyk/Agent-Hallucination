"""Retry/rejection feedback constructors for the L1/L2 planners.

Ten pure functions moved off the old evolution god-object: rejection-stage
classification, structural hard constraints for bounded retries, polarity
retry hints, and the MUTATION_RETRY_FEEDBACK block. They consume only
closed-vocabulary projections — reject reasons, prior failure categories,
and step records — so no raw provider text, case identity or evaluator
metadata can re-enter a retry prompt.

The step record is consumed through a structural view (``StepView``): the
runner's per-step ledger shape (``gate_decision`` / ``fitness_check`` /
``l2_policy``) is projected by the caller, and this module never imports
the loop layer it will eventually live beside.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Protocol, Sequence

from sehd_rise.skills.action import CLEAN_TO_ERROR_ACTIONS, ERROR_TO_CLEAN_ACTIONS
from sehd_rise.skills.vocabulary.contracts import MODULE_IDS

_RE = re.compile(
    r"action_polarity_mismatch:(error_to_clean|clean_to_error):"
    r"([a-z_]+):([a-z_]+)"
)


class StepView(Protocol):
    """Structural view of one evolution step's rejection-relevant fields."""

    gate_decision: str
    fitness_check: Mapping[str, Any] | None


def _classify_rejection_stage(reasons: Sequence[str]) -> str:
    """Map a set of reject reasons onto the closed rejection-stage vocabulary.

    Single owner for both StructuralGate certificates and L1 fitness / L2
    policy reject steps, so the same reason lands in the same stage
    regardless of which surface produced it.
    """
    if any("runtime" in value or "apply_edit_failed" in value for value in reasons):
        return "runtime"
    if any("llm_conflict" in value for value in reasons):
        return "conflict"
    if any(
        "no_effect" in value or "did_not_fire" in value or "did_not_move" in value
        for value in reasons
    ):
        return "evidence_no_effect"
    if any(
        "directional_evidence" in value or "without_gold" in value
        or "unscorable" in value
        for value in reasons
    ):
        return "evidence_unverified"
    if any(
        "worse" in value or "regression" in value or "against_gold" in value
        for value in reasons
    ):
        return "regression"
    return "structural"


def _step_rejection_reasons(fitness: Mapping[str, Any]) -> list[str]:
    """Collect every closed-vocabulary reason a reject-class step recorded."""
    reasons: list[str] = []
    for key in ("reject_reasons", "retry_reason"):
        value = fitness.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            reasons.extend(str(item) for item in value if str(item))
        elif str(value):
            reasons.append(str(value))
    assessment = fitness.get("assessment")
    if isinstance(assessment, Mapping):
        reasons.extend(
            str(item) for item in (assessment.get("reasons") or ())
            if str(item)
        )
    l2 = fitness.get("l2_policy")
    if isinstance(l2, Mapping):
        if str(l2.get("reason") or ""):
            reasons.append(str(l2["reason"]))
        if str(l2.get("feedback_code") or ""):
            reasons.append(str(l2["feedback_code"]))
    return reasons


# L1 structural rejections are already covered by StructuralGate certificates,
# so they are excluded from the per-step aggregation to avoid double-counting.
_L1_STRUCTURAL_REJECT_DECISIONS = frozenset({"reject", "structural_reject"})


def _build_rejection_summary(
    gate_summary: Mapping[str, Any],
    steps: Sequence[StepView],
) -> dict[str, Any]:
    """Separate legality, evidence, regression and runtime rejection causes.

    StructuralGate certificates contribute L1 structural rejections; the
    per-step pass contributes L1 fitness and L2 policy rejections (whose
    reasons live in ``fitness_check`` / ``l2_policy`` and were previously
    invisible to this summary).  ``by_decision`` is preserved for existing
    batch reports.
    """
    reason_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    for certificate in gate_summary.get("certificates") or ():
        if certificate.get("decision") != "reject":
            continue
        reasons = [str(value) for value in certificate.get("reject_reasons") or ()]
        stage = _classify_rejection_stage(reasons)
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        for reason in reasons:
            key = reason.split(":", 1)[0]
            reason_counts[key] = reason_counts.get(key, 0) + 1
    for step in steps:
        decision = str(step.gate_decision or "")
        if not decision.startswith("reject"):
            continue
        if decision in _L1_STRUCTURAL_REJECT_DECISIONS:
            continue
        fitness = step.fitness_check if isinstance(step.fitness_check, Mapping) else {}
        reasons = _step_rejection_reasons(fitness)
        if not reasons:
            continue
        stage = _classify_rejection_stage(reasons)
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        for reason in reasons:
            key = reason.split(":", 1)[0]
            reason_counts[key] = reason_counts.get(key, 0) + 1
    decision_counts: dict[str, int] = {}
    for step in steps:
        decision_counts[step.gate_decision] = decision_counts.get(step.gate_decision, 0) + 1
    return {
        "by_stage": stage_counts,
        "by_reason": reason_counts,
        "by_decision": decision_counts,
    }


_REPAIRABLE_STRUCTURAL_REASONS = frozenset({
    "runtime_rule_contract",
    "rule_spec_contract",
    "content_too_short",
    "not_if_then_instruction",
    "multi_step_pseudocode",
    "condition_overload",
    "multiple_actions",
    "action_target_outside_schema",
    "cross_module_near_duplicate",
    "cross_module_merge_required",
    "merge_required",
    "diagnostic_only_condition",
    "cross_module_reference",
    "modify_bloat",
    "duplicate_content",
    "source_no_effect",
})


def _structural_rejection_retryable(reasons: Sequence[str]) -> bool:
    """Whether a fresh proposal can safely repair this structural failure."""
    categories = {str(reason).split(":", 1)[0] for reason in reasons}
    return bool(categories) and categories <= _REPAIRABLE_STRUCTURAL_REASONS


_RUNTIME_RULE_CONSTRAINT_TEXT: dict[str, str] = {
    "content_too_short": (
        "rule content must be a substantive natural-language instruction"
    ),
    "not_if_then_instruction": (
        "the rule must be an observable 'if/when <condition> -> <one action>'"
        " instruction"
    ),
    "multi_step_pseudocode": (
        "no multi-step pseudocode or procedures; prescribe one observable"
        " action"
    ),
    "condition_overload": (
        "the rule condition must be ONE clause with at most one conjunction"
        " (and/or/且/并且/以及/或); the structural gate rejects conditions"
        " with two or more conjunctions, so keep one decisive observable"
        " pattern instead of a compound enumeration"
    ),
    "multiple_actions": (
        "the rule must prescribe exactly one observable action"
    ),
}
_STRUCTURAL_CATEGORY_CONSTRAINT_TEXT: dict[str, str] = {
    "duplicate_content": (
        "the content duplicates an existing rule; propose a genuinely"
        " different discriminator or use modify_rule/remove_rule"
    ),
    "cross_module_near_duplicate": (
        "the content is a near-duplicate of another module's rule; propose a"
        " genuinely distinct discriminator"
    ),
    "cross_module_merge_required": (
        "the proposal semantically overlaps an existing rule; return a"
        " distinct single-action discriminator, never a compound merge"
    ),
    "merge_required": (
        "the proposal semantically overlaps an existing rule; return a"
        " distinct single-action discriminator, never a compound merge"
    ),
    "semantic_overlap_requires_distinct_discriminator": (
        "the proposal semantically overlaps an existing rule; return a"
        " distinct single-action discriminator, never a compound merge"
    ),
    "diagnostic_only_condition": (
        "conditions must not reference internal diagnostic state, dataset"
        " labels or case identifiers"
    ),
    "cross_module_reference": (
        "the rule must not reference other modules or their rules"
    ),
    "modify_bloat": (
        "modify_rule must keep a comparable length and structure to the"
        " original rule"
    ),
    "source_no_effect": (
        "the previous edit produced no observable target change; propose an"
        " intervention with a concrete observable effect contract"
    ),
    "action_target_outside_schema": (
        "the action target must be one of the module's registered"
        " provider-schema output fields (see the valid output-field targets"
        " list); a target the provider has no schema slot for is inert"
    ),
    "case_specific_pattern": (
        "the rule must be general: no case-specific identifiers or dataset"
        " labels"
    ),
    "label_or_case_leakage": (
        "the rule must not mention dataset labels, case identifiers or"
        " evaluator fields"
    ),
}


def _structural_reject_category(reason: str) -> str:
    """Reduce one structural reject reason to a stable case-agnostic category.

    ``runtime_rule_contract:condition_overload: 3 rules already share ...``
    becomes ``runtime_rule_contract:condition_overload``; the free-text tail
    is dropped so retry feedback never carries case-specific content.
    """
    text = str(reason or "").strip()
    if not text:
        return ""
    prefix, _, detail = text.partition(":")
    prefix = prefix.strip()
    if not detail.strip():
        return prefix
    key = detail.strip().split(" ", 1)[0].strip(":").strip()
    if not key:
        return prefix
    if (
        prefix in ("runtime_rule_contract", "rule_spec_contract")
        and key in _RUNTIME_RULE_CONSTRAINT_TEXT
    ):
        return f"{prefix}:{key}"
    if key in _STRUCTURAL_CATEGORY_CONSTRAINT_TEXT:
        return f"{prefix}:{key}" if prefix else key
    return prefix


def _structural_hard_constraint(category: str) -> str | None:
    """Map a stable structural category to its retry hard-constraint text."""
    value = str(category or "")
    prefix, _, detail = value.partition(":")
    if prefix in ("runtime_rule_contract", "rule_spec_contract"):
        runtime_text = _RUNTIME_RULE_CONSTRAINT_TEXT.get(detail)
        if runtime_text:
            return runtime_text
    return (
        _STRUCTURAL_CATEGORY_CONSTRAINT_TEXT.get(value)
        or _STRUCTURAL_CATEGORY_CONSTRAINT_TEXT.get(detail)
        or _STRUCTURAL_CATEGORY_CONSTRAINT_TEXT.get(prefix)
    )


def _structural_retry_hard_constraints(
    prior_categories: Sequence[str],
) -> list[str]:
    """Collect the binding hard-constraint texts for prior categories."""
    return sorted({
        constraint
        for category in prior_categories
        if str(category).startswith("structural:")
        for constraint in (
            _structural_hard_constraint(
                str(category)[len("structural:"):],
            ),
        )
        if constraint
    })


def _build_structural_system_constraints(
    prior_categories: Sequence[str],
) -> str:
    """Render binding structural constraints for the retry SYSTEM prompt.

    A bounded retry that only carried the hard constraints in a trailing
    user-prompt feedback block still repeated the same ``condition_overload``
    defect.  On a retry the constraints are therefore also promoted into the
    system prompt, where they hold instruction-level priority.  Deterministic
    and case-agnostic: only registered category texts exist.
    """
    constraints = _structural_retry_hard_constraints(prior_categories)
    if not constraints:
        return ""
    lines = "\n".join(f"- {constraint}" for constraint in constraints)
    return (
        "\n\n[STRUCTURAL HARD CONSTRAINTS - binding for this attempt]\n"
        "A previous proposal for this source was rejected by the structural"
        " gate. The constraints below are binding; violating any of them"
        " will reject this attempt as well:\n" + lines
    )


# Differential retry guidance per deterministic no-effect class: the next
# reflection must know WHY nothing moved, not only that nothing moved.
_NO_EFFECT_RETRY_GUIDANCE: dict[str, str] = {
    "output_changed_then_quarantined": (
        "the previous edit moved provider output but the court quarantined "
        "it: tighten the condition or fix the emitted fields, do not switch "
        "the intervention surface"
    ),
    "provider_output_changed_but_court_invariant": (
        "provider-visible counts moved but the court terminal did not: the "
        "edit acts below the terminal edge; retarget it at a field the "
        "proof court actually consumes"
    ),
    "no_observable_change_any_layer": (
        "nothing addressable moved: the condition likely never matched or "
        "the action was not injected; re-check the opcode and condition "
        "against the module's observable fields"
    ),
}


def _polarity_retry_hint(prior_categories: Sequence[str]) -> dict[str, Any] | None:
    """Translate a prior polarity-mismatch into a binding "stay on primary"
    hint so the next attempt does not defensively switch modules.

    When attribution routes a gold-locked FP/FN to the primary owner, the
    LLM has historically picked an opcode of the OPPOSITE polarity.
    Prevalidation rejected the cross-polarity pick, and the retry feedback
    block then suggested "if possible a different owner module" — actively
    encouraging the LLM to abandon attribution's owner for the secondary.

    This helper parses the deterministic ``action_polarity_mismatch`` category
    out of ``prior_categories`` and returns a structured hint carrying the
    polarity-correct opcode set for the same primary module.
    """
    for category in prior_categories:
        match = _RE.search(str(category or ""))
        if match is None:
            continue
        direction, module, _operation = match.groups()
        if module not in MODULE_IDS:
            continue
        operations = (
            ERROR_TO_CLEAN_ACTIONS.get(module, frozenset())
            if direction == "error_to_clean"
            else CLEAN_TO_ERROR_ACTIONS.get(module, frozenset())
        )
        if not operations:
            continue
        return {
            "module": module,
            "direction": direction,
            "valid_opcodes": sorted(operations),
        }
    return None


def _build_mutation_retry_feedback(
    *,
    attempt_index: int,
    prior_categories: Sequence[str],
    rejected_prior: Sequence[Mapping[str, Any]],
) -> str:
    """Render the MUTATION_RETRY_FEEDBACK block; empty string when nothing.

    Structural rejection categories recorded with the ``structural:`` marker
    are additionally translated into explicit ``structural_hard_constraints``
    so a bounded retry cannot repeat the same structural defect.  The block
    also changes the request body (and thus the provider cache key) relative
    to the initial attempt.  Only stable case-agnostic categories are used;
    raw provider text is never echoed.

    When a prior attempt was rejected by prevalidation as
    ``action_polarity_mismatch``, the generic "different owner module" retry
    language actively encouraged the LLM to abandon attribution's primary
    owner.  When the polarity hint is available, the retry block instead
    binds the LLM to the SAME primary module with a polarity-correct opcode
    list, removing the module-escape escape hatch.
    """
    retry_block: dict[str, Any] = {}
    polarity_hint = _polarity_retry_hint(prior_categories)
    if prior_categories:
        retry_block["attempt_index"] = attempt_index
        retry_block["previous_failure_categories"] = list(prior_categories)
        if polarity_hint is not None:
            retry_block["required_action"] = (
                "previous mutation output was rejected for choosing a "
                "cross-polarity opcode on attribution's primary owner. "
                "STAY on the attribution-primary module="
                f"{polarity_hint['module']} and pick an opcode from this "
                f"{polarity_hint['direction']} list: "
                f"{', '.join(polarity_hint['valid_opcodes'])}. Do NOT "
                "switch to a different owner module; the secondary is only "
                "permissible when the primary has no opcode for this "
                "direction at all. Return a DIFFERENT valid JSON edit "
                "with a distinct trigger condition, the polarity-correct "
                "opcode, and a target_field listed in the module charter; "
                "operation in {add_rule,modify_rule,remove_rule,no_edit} "
                "and non-empty condition/action/evidence_anchor slots."
            )
            retry_block["polarity_correct_opcodes_for_primary"] = {
                "module": polarity_hint["module"],
                "direction": polarity_hint["direction"],
                "opcodes": polarity_hint["valid_opcodes"],
            }
        else:
            retry_block["required_action"] = (
                "previous mutation output was structurally "
                "invalid or had no observable target change; "
                "return a DIFFERENT valid JSON edit with a distinct "
                "trigger and a distinct output action (stay on the "
                "attribution-primary unless its opcode set has nothing "
                "polarity-correct), with operation in "
                "{add_rule,modify_rule,remove_rule,no_edit} and "
                "non-empty condition/action/evidence_anchor slots"
            )
        hard_constraints = _structural_retry_hard_constraints(prior_categories)
        if hard_constraints:
            retry_block["structural_hard_constraints"] = hard_constraints
    if rejected_prior:
        retry_block["rejected_source_attempts"] = [
            {
                "target_module": prior["target_module"],
                "observable_deltas": prior["observable_deltas"],
                **(
                    {
                        "no_effect_guidance": _NO_EFFECT_RETRY_GUIDANCE[
                            prior["no_effect_classification"]
                        ],
                    }
                    if str(prior.get("no_effect_classification") or "")
                    in _NO_EFFECT_RETRY_GUIDANCE else {}
                ),
            }
            for prior in rejected_prior
        ]
        retry_block.setdefault("required_action", (
            "return a DIFFERENT valid JSON edit with a distinct "
            "trigger and a distinct output action; stay on the "
            "attribution-primary unless its opcode set has nothing "
            "polarity-correct"
        ))
    if not retry_block:
        return ""
    return (
        "\n[MUTATION_RETRY_FEEDBACK]\n"
        + json.dumps(retry_block, ensure_ascii=False, sort_keys=True)
    )


__all__ = [
    "StepView",
    "_build_mutation_retry_feedback",
    "_build_rejection_summary",
    "_build_structural_system_constraints",
    "_classify_rejection_stage",
    "_polarity_retry_hint",
    "_step_rejection_reasons",
    "_structural_hard_constraint",
    "_structural_reject_category",
    "_structural_rejection_retryable",
    "_structural_retry_hard_constraints",
]
