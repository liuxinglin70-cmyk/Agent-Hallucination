"""Slot-contract storage for L1 Skill rules (BCCSE).

Why this module exists: the legacy form stored rules as free text and
guessed their shape with regexes after the fact, which produced ambiguous
rejections and silent shape drift. A slot template makes the if-then shape
structurally guaranteed: rules persist as typed slots, and prompt injection
is a deterministic render. All free-text slots are English by contract;
permission and pollution checks below are semantic gates, not shape guesses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .vocabulary import (
    ALLOWED_VERDICTS,
    FINAL_VERDICT_RE,
    FORBIDDEN_CONTENT_MARKERS,
    INSTANCE_ID_RE,
    LOCAL_NEGATION_MODULES,
    LOCAL_NEGATION_RE,
    MODULE_IDS,
    semantic_overlap,
    validate_feature_description,
)
from .module_charter import (
    MODULE_ACTION_OPCODES,
    validate_action_surface,
)

# Direction vocabulary mirrors rule_meta["direction"] in genome so slot rules
# and legacy rules stay comparable during the migration window.
ALLOWED_DIRECTIONS: tuple[str, ...] = (
    "clean",
    "error",
    "unresolved",
    "deny_causal",
    "process",
)

MAX_SLOT_CHARS = 300
MAX_ANCHOR_CHARS = 240

_TRAILING_PUNCT = ".,;:!?。，；：！？"

# Condition overload: >1 coordinator means the trigger encodes several
# situations, which breaks per-case attribution of fitness signal.
_CONDITION_CONJUNCTION_RE = re.compile(
    r"\band\b|\bor\b|且|或|以及", re.IGNORECASE,
)

# Action splitters: any of these means the rule prescribes several actions,
# which the evolution loop cannot credit/blame individually.  Slot actions
# are a single ``opcode: target`` by contract; the ``and`` form catches a
# natural-language double action before the opcode check labels it.
_ACTION_SPLIT_RE = re.compile(
    r";|\bthen also\b|\band then\b|\band\b|然后|同时|并且|以及|并随后",
    re.IGNORECASE,
)

# Action-slot pollution patterns. Why a local copy instead of routing the
# action through genome.validate_feature_description: that validator also
# rejects text STARTING with an imperative verb (a prompt-injection
# heuristic), which would falsely reject legitimate verdict actions such as
# "return unresolved". Conditions and anchors never start with imperatives,
# so they keep reusing the genome validator verbatim.
_ACTION_POISON_RE = re.compile(
    r"https?://|"
    r"\b(?:case|source|agent|model)[-_ ]?id\b|"
    r"\b(?:case|source)[_-]\d+\b|"
    r"\bag_\w{8,}\b|"
    r"\b0x[0-9a-f]{6,}\b|"
    r"\b(?:gold|ground[ -]?truth|golden label)\b|"
    r"(?:金标|答案|正确标签|真实标签|本案例|这个案例)|"
    r"\b(?:ignore|disregard|override)\s+(?:all\s+)?"
    r"(?:previous|prior|above|system|developer)\b.{0,40}"
    r"\b(?:instruction|prompt|rule|message)s?\b|"
    r"\b(?:system|developer|assistant)\s+(?:prompt|message)\b|"
    r"\b[a-z][a-z_]+/\d+\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RuleSpec:
    """One L1 rule as typed slots instead of free text.

    evidence_anchor is failure-signature bookkeeping: it drives evolution
    triage but is never rendered into runtime prompts, so it cannot leak
    case identity into detection.
    """

    condition: str
    action: str
    evidence_anchor: str
    module_id: str
    direction: str
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "action": self.action,
            "evidence_anchor": self.evidence_anchor,
            "module_id": self.module_id,
            "direction": self.direction,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuleSpec":
        provenance = data.get("provenance") or {}
        if not isinstance(provenance, dict):
            raise ValueError("provenance must be a dict")
        return cls(
            condition=str(data.get("condition") or ""),
            action=str(data.get("action") or ""),
            evidence_anchor=str(data.get("evidence_anchor") or ""),
            module_id=str(data.get("module_id") or ""),
            direction=str(data.get("direction") or ""),
            provenance=dict(provenance),
        )


def _normalize_condition(raw: str) -> str:
    cond = " ".join(str(raw).split())
    # Drop a redundant leading conjunction so "when X" never renders as
    # "When when X".
    cond = re.sub(r"^(?:when|if)\s+", "", cond, flags=re.IGNORECASE)
    cond = cond.rstrip(_TRAILING_PUNCT).strip()
    # The template already supplies "When", so the slot reads as a clause:
    # lowercase its first letter for a single grammatical sentence.
    if cond:
        cond = cond[0].lower() + cond[1:]
    return cond


def condition_is_overloaded(condition: str) -> bool:
    """Whether a condition packs more than one situation into one clause.

    The single place the "one clause" rule is evaluated; the structural gate
    routes through it so future callers cannot disagree about the shape.
    """
    return len(_CONDITION_CONJUNCTION_RE.findall(str(condition or ""))) > 1


def _normalize_action(raw: str) -> str:
    action = " ".join(str(raw).split())
    action = action.rstrip(_TRAILING_PUNCT).strip()
    return action


def _render_action(raw_action: str, module_id: str) -> str:
    """Expand a closed opcode slot into the English template the provider
    sees: ``"<opcode>: <target>"`` maps to the module's template. Unknown
    texts and historical bare opcodes still render verbatim or by template so
    legacy checkpoints keep their original wording; validation requires the
    explicit target for every new rule.
    """
    action = _normalize_action(raw_action)
    opcode, sep, target = action.partition(":")
    opcode = opcode.strip()
    # Case-insensitive like validate_action_surface, so a differently-cased
    # opcode still renders through the deterministic template instead of
    # falling back to verbatim (which would break dedup identity).
    opcode_lower = opcode.lower()
    for code, template in MODULE_ACTION_OPCODES.get(module_id, ()):
        if opcode_lower == code:
            target_part = target.strip() if sep else ""
            return f"{template}: {target_part}" if target_part else template
    return action


def render_rule(spec: RuleSpec) -> str:
    """Deterministically render a spec as exactly one English if-then sentence.

    The action slot is ``"<opcode>: <target>"``; rendering expands it into the module's
    English template so the provider reads natural language while the slot
    stays enumerable and validatable. Punctuation normalization (no trailing
    punctuation on the condition, a single terminal period on the action)
    makes the rendered text a pure function of the slots, which is what
    identity/dedup rely on.
    """
    condition = _normalize_condition(spec.condition)
    action = _render_action(spec.action, spec.module_id)
    return f"When {condition}, {action}."


def _pollution_reason(field_name: str, ok: bool, reason: str) -> str:
    # Re-label genome validator reasons so rejections point at this contract
    # instead of the feature-description contract.
    if ok:
        return ""
    return f"{field_name}:{reason.replace('feature_', 'rule_', 1)}"


def validate_rule_spec(spec: RuleSpec) -> tuple[bool, str]:
    """Validate a RuleSpec; returns (ok, reason) with a stable reason token."""
    if not spec.condition.strip():
        return False, "empty_condition"
    if not spec.action.strip():
        return False, "empty_action"
    if len(spec.condition) > MAX_SLOT_CHARS:
        return False, "condition_too_long"
    if len(spec.action) > MAX_SLOT_CHARS:
        return False, "action_too_long"
    if len(spec.evidence_anchor) > MAX_ANCHOR_CHARS:
        return False, "evidence_anchor_too_long"
    if spec.module_id not in MODULE_IDS:
        return False, f"invalid_module_id:{spec.module_id}"
    if spec.direction not in ALLOWED_DIRECTIONS:
        return False, f"invalid_direction:{spec.direction}"

    if condition_is_overloaded(spec.condition):
        return False, "condition_overload"
    if _ACTION_SPLIT_RE.search(spec.action):
        return False, "multiple_actions"

    # Pollution firewall: conditions and anchors reuse the genome validator
    # (markers + URL/case-id/gold/injection patterns); the action slot uses
    # the imperative-tolerant local pattern set (see _ACTION_POISON_RE why).
    ok, reason = validate_feature_description(spec.condition)
    if not ok:
        return False, _pollution_reason("condition", ok, reason)
    if spec.evidence_anchor:
        ok, reason = validate_feature_description(spec.evidence_anchor)
        if not ok:
            return False, _pollution_reason("evidence_anchor", ok, reason)
    lowered_action = spec.action.lower()
    for marker in FORBIDDEN_CONTENT_MARKERS:
        if marker in lowered_action:
            return False, f"action:rule_forbidden_content:{marker}"
    if _ACTION_POISON_RE.search(lowered_action):
        return False, "action:rule_prompt_or_case_pollution"
    if INSTANCE_ID_RE.search(lowered_action):
        return False, "action:rule_instance_id_pollution"
    # Authority gates run on the rendered text because that is exactly what
    # the provider will see.
    rendered = render_rule(spec).lower()
    verdict_match = FINAL_VERDICT_RE.search(rendered)
    if verdict_match:
        verdict = next(g for g in verdict_match.groups() if g)
        allowed = ALLOWED_VERDICTS.get(spec.module_id, frozenset())
        if verdict not in allowed:
            return False, f"verdict_not_allowed:{verdict}_for_{spec.module_id}"
    elif LOCAL_NEGATION_RE.search(rendered):
        if spec.module_id not in LOCAL_NEGATION_MODULES:
            return False, f"local_negation_not_allowed_for_{spec.module_id}"

    action_surface_ok, action_surface_reason = validate_action_surface(
        spec.module_id, spec.action,
    )
    if not action_surface_ok:
        return False, action_surface_reason

    return True, ""


def rule_text_identity(spec: RuleSpec) -> str:
    """Lowercased whitespace-normal render, the canonical key for dedup."""
    return " ".join(render_rule(spec).lower().split())


def rules_semantic_overlap(spec_a: RuleSpec, spec_b: RuleSpec) -> float:
    """Dedup distance between two specs, delegating to genome's metric so
    slot rules and legacy text rules share one duplicate scale."""
    return semantic_overlap(rule_text_identity(spec_a), rule_text_identity(spec_b))
