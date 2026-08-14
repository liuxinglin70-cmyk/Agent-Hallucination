"""One L1 rule, two halves: the slot contract and the applicability contract.

The slot contract (RuleSpec) stores a rule as typed slots instead of free
text so the if-then shape is structurally guaranteed: persistence is a
deterministic render, and permission/pollution checks are semantic gates
rather than after-the-fact shape guesses. All free-text slots are English by
contract.

The applicability contract (ConditionSpec) is the framework-owned execution
boundary. A natural-language condition is an explanation, not an execution
boundary. The typed condition uses a deliberately small closed vocabulary of
proof-court result features projected before the deterministic L1 transform;
the prose condition remains a human-readable refinement only.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from sehd_rise2.detector.skills.vocabulary.charters import (
    MODULE_ACTION_OPCODES,
    validate_action_surface,
)
from sehd_rise2.detector.skills.vocabulary.contracts import (
    ALLOWED_VERDICTS,
    CONDITION_FEATURES,
    CONDITION_STAGE_RETRY_DIAGNOSTIC,
    DOMAIN_CONDITION_FEATURES,
    FINAL_VERDICT_RE,
    FORBIDDEN_CONTENT_MARKERS,
    INSTANCE_ID_RE,
    LOCAL_NEGATION_MODULES,
    LOCAL_NEGATION_RE,
    MODULE_IDS,
    semantic_overlap,
    validate_feature_description,
)


# ============================================================
# Slot contract
# ============================================================

# Direction vocabulary mirrors rule_meta["direction"] in the genome so
# slot-rendered rules and free-text rules stay comparable on one scale.
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

# Action-slot pollution patterns. A local copy instead of the shared
# feature-description validator because that validator also rejects text
# STARTING with an imperative verb (a prompt-injection heuristic), which
# would falsely reject legitimate verdict actions such as "return
# unresolved". Conditions and anchors never start with imperatives, so they
# keep reusing the shared validator verbatim.
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

# Authority-boundary markers for route-guidance text (design D1): a skill
# may steer a target provider route's verification principle but never
# instruct it to author evidence, certificates, or a verdict — those stay
# with the deterministic validator and proof court. The optional-article
# group absorbs "a"/"an"/"the" so "create a certificate" and "create
# certificate" are the same violation.
_GUIDANCE_AUTHORITY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"create\s+(?:a\s+|an\s+|the\s+)?certificate"), "create_certificate"),
    (re.compile(r"construct\s+(?:a\s+|an\s+|the\s+)?certificate"), "construct_certificate"),
    (re.compile(r"emit\s+(?:a\s+|an\s+|the\s+)?certificate"), "emit_certificate"),
    (re.compile(r"(?:create|fabricate|write|invent)\s+(?:a\s+|an\s+|the\s+)?evidence"), "author_evidence"),
    (re.compile(r"mark\s+this\s+example"), "mark_this_example"),
    (re.compile(r"(?:force|set)\s+verdict"), "force_verdict"),
    (re.compile(r"return\s+(?:clean|error|unresolved)"), "return_verdict"),
    (re.compile(r"创建证书|构造证书|伪造证据|写入证据|标记为幻觉|强制判定|直接判定|直接输出"), "zh_authority_violation"),
)


def validate_route_guidance(content: str) -> tuple[bool, str]:
    """Fail-closed firewall for compiled route-guidance text (design D1).

    The guidance is a typed skill's ``local_guidance`` field compiled into a
    provider-route block; it must never carry verdict emission, evidence or
    certificate construction, case/gold identity, or a prompt-injection
    override. The rejection reason is closed vocabulary so a poisoned skill
    is discarded at compile time, never surfaced to the provider.
    """
    normalized = " ".join(str(content or "").split())
    if not normalized:
        return True, ""
    if len(normalized) > 2000:
        return False, "route_guidance_too_long"
    lowered = normalized.lower()
    for marker in FORBIDDEN_CONTENT_MARKERS:
        if marker in lowered:
            return False, f"route_guidance_forbidden_content:{marker}"
    if _ACTION_POISON_RE.search(normalized):
        return False, "route_guidance_prompt_or_case_pollution"
    if FINAL_VERDICT_RE.search(normalized):
        return False, "route_guidance_verdict_instruction"
    for pattern, token in _GUIDANCE_AUTHORITY_PATTERNS:
        if pattern.search(lowered):
            return False, f"route_guidance_authority_violation:{token}"
    return True, ""


def sanitize_route_guidance(content: str) -> str:
    """Return a firewall-clean route-guidance text or an empty sentinel."""
    normalized = " ".join(str(content or "").split())[:2000]
    valid, _ = validate_route_guidance(normalized)
    return normalized if valid else ""


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
    sees: ``"<opcode>: <target>"`` maps to the module's template. An action
    that fails to parse still renders as stored — a checkpoint payload that
    predates the explicit-target requirement keeps its original wording and
    therefore its identity; validation requires the explicit target for
    every new rule.
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
    # Re-label shared validator reasons so rejections point at this contract
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

    # Pollution firewall: conditions and anchors reuse the shared validator
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
    """Dedup distance between two specs on the shared bigram metric, so slot
    rules and free-text rules share one duplicate scale."""
    return semantic_overlap(rule_text_identity(spec_a), rule_text_identity(spec_b))


# ============================================================
# Applicability contract (typed executable condition)
# ============================================================

SCHEMA_VERSION = "sehd.condition_spec.v3"

# The condition predicate vocabulary (feature names, operators, value
# domains, emitting binding domains, call stage) is owned by
# skills.vocabulary.contracts.CONDITION_FEATURES; everything below derives
# from it so this module can never drift from the projection contract.

# Boolean-typed atom features: their ``value`` must be a real bool, and the
# value_type must be "boolean".
_BOOLEAN_FEATURES = frozenset(
    name for name, spec in CONDITION_FEATURES.items()
    if spec.value_type == "boolean"
)

# Typed boolean composition: ``atom`` is the single-predicate form;
# ``all_of``/``any_of``/``not`` compose child specs.
_COMBINATORS = frozenset({"atom", "all_of", "any_of", "not"})

# L1 evaluates one proof-court result projection. Instance operations then
# resolve a matched atom to an existing candidate/certificate/origin.
_BINDINGS = frozenset({"entity_scoped"})


@dataclass(frozen=True)
class ConditionSpec:
    feature: str
    operator: str
    value: str | bool
    feature_source: str = "court_result"
    value_type: str = "string"
    scope: str = "l1_court_transform"
    evidence_binding: str = "post_court_projection"
    schema_version: str = SCHEMA_VERSION
    combinator: str = "atom"
    children: tuple[dict[str, Any], ...] = ()
    binding: str = "entity_scoped"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConditionSpec":
        raw_children = value.get("children")
        children = ()
        if isinstance(raw_children, (list, tuple)):
            children = tuple(
                dict(item) if isinstance(item, Mapping) else {}
                for item in raw_children
            )
        return cls(
            feature=str(value.get("feature") or ""),
            operator=str(value.get("operator") or ""),
            value=value.get("value", ""),
            feature_source=str(
                value.get("feature_source") or "court_result"
            ),
            value_type=str(value.get("value_type") or "string"),
            scope=str(value.get("scope") or "l1_court_transform"),
            evidence_binding=str(
                value.get("evidence_binding") or "post_court_projection"
            ),
            schema_version=str(value.get("schema_version") or SCHEMA_VERSION),
            combinator=str(value.get("combinator") or "atom"),
            children=children,
            binding=str(value.get("binding") or "entity_scoped"),
        )


@dataclass(frozen=True)
class ConditionEvaluation:
    matched: bool
    reason: str
    feature: str
    operator: str
    expected: str | bool
    observed: Any

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validate_atom_spec(spec: ConditionSpec) -> tuple[bool, str]:
    """Validate a single atomic predicate (v1 form or v2 atom combinator)."""
    contract = CONDITION_FEATURES.get(spec.feature)
    if contract is None:
        return False, f"condition_feature_unregistered:{spec.feature or 'missing'}"
    if spec.operator not in contract.operators:
        return False, (
            f"condition_operator_invalid:{spec.feature}:{spec.operator or 'missing'}"
        )
    normalized_value: Any = spec.value
    if spec.feature in _BOOLEAN_FEATURES:
        if spec.value_type != "boolean":
            return False, f"condition_value_type_invalid:{spec.feature}:{spec.value_type}"
        if not isinstance(normalized_value, bool):
            return False, f"condition_value_not_boolean:{spec.feature}"
    else:
        if spec.value_type != "string":
            return False, f"condition_value_type_invalid:{spec.feature}:{spec.value_type}"
        normalized_value = str(normalized_value)
    if normalized_value not in contract.values:
        return False, f"condition_value_unregistered:{spec.feature}:{normalized_value}"
    return True, ""


def validate_condition_spec(value: Any) -> tuple[bool, str]:
    if not isinstance(value, Mapping):
        return False, "condition_spec_not_object"
    spec = ConditionSpec.from_dict(value)
    if spec.schema_version != SCHEMA_VERSION:
        return False, f"condition_spec_schema:{spec.schema_version}"
    if spec.feature_source != "court_result":
        return False, f"condition_feature_source_invalid:{spec.feature_source}"
    if spec.scope != "l1_court_transform":
        return False, f"condition_scope_invalid:{spec.scope}"
    if spec.evidence_binding != "post_court_projection":
        return False, f"condition_evidence_binding_invalid:{spec.evidence_binding}"
    if spec.combinator not in _COMBINATORS:
        return False, f"condition_combinator_invalid:{spec.combinator}"
    if spec.binding not in _BINDINGS:
        return False, f"condition_binding_invalid:{spec.binding}"
    combinator = spec.combinator
    if combinator == "atom":
        required = {
            "feature_source", "value_type", "scope", "evidence_binding",
            "feature", "operator", "value", "schema_version",
        }
        missing = sorted(required - set(str(key) for key in value))
        if missing:
            return False, "condition_spec_missing_fields:" + ",".join(missing)
        return _validate_atom_spec(spec)
    # Combinator form: validate children recursively.
    if not spec.children:
        return False, f"condition_combinator_no_children:{combinator}"
    if combinator == "not" and len(spec.children) != 1:
        return False, "condition_not_requires_single_child"
    for child in spec.children:
        ok, reason = validate_condition_spec(child)
        if not ok:
            return False, f"condition_child_invalid:{reason}"
    return True, ""


def sanitize_condition_spec(value: Any) -> dict[str, Any]:
    valid, _reason = validate_condition_spec(value)
    if not valid:
        return {}
    spec = ConditionSpec.from_dict(value)
    return spec.to_dict()


def _collect_spec_features(spec: ConditionSpec) -> frozenset[str]:
    """Collect every feature name referenced by one (possibly nested) spec."""
    if spec.combinator == "atom":
        return frozenset({spec.feature})
    features: set[str] = set()
    for child in spec.children:
        features.update(_collect_spec_features(ConditionSpec.from_dict(child)))
    return frozenset(features)


def validate_condition_spec_for_domain(
    value: Any, domain: str,
) -> tuple[bool, str]:
    """Reject a condition that names a feature its target domain cannot bind.

    ``validate_condition_spec`` checks the global closed vocabulary; this
    checks the second, domain-scoped axis that the binding projector enforces
    at runtime. A ``candidate_kind`` predicate on a ``claim_anchor`` capability
    passes the global contract but can never bind, so it is rejected here with
    a precise reason instead of surfacing downstream as ``BINDING_NOT_FOUND``.
    """
    if not isinstance(value, Mapping):
        return False, "condition_spec_not_object"
    domain_features = DOMAIN_CONDITION_FEATURES.get(str(domain))
    if domain_features is None:
        return False, f"condition_domain_unregistered:{domain or 'missing'}"
    spec = ConditionSpec.from_dict(value)
    foreign = sorted(_collect_spec_features(spec) - domain_features)
    if foreign:
        return False, (
            f"condition_feature_not_bindable_in_domain:{domain}:"
            + ",".join(foreign)
        )
    return True, ""


def _render_feature_lines(features: frozenset[str]) -> list[str]:
    """Render one predicate line per feature, ordered for determinism."""
    lines: list[str] = []
    for feature in sorted(features):
        contract = CONDITION_FEATURES[feature]
        operators = " | ".join(sorted(contract.operators))
        rendered = ", ".join(
            json.dumps(value, ensure_ascii=False)
            for value in sorted(contract.values, key=lambda item: str(item))
        )
        tag = (
            " (retry-diagnostic: only present after a failure)"
            if contract.stage == CONDITION_STAGE_RETRY_DIAGNOSTIC
            else " (first-call: present before any failure)"
        )
        lines.append(f"- {feature} / {operators} / one of: {rendered}{tag}")
    return lines


def render_condition_spec_contract(
    binding_domains: Sequence[str] = (),
) -> str:
    """Prompt contract for the reflection model; values are closed.

    When ``binding_domains`` is non-empty, only the features those domains
    can actually bind are rendered, so the model is never invited to write a
    predicate that is globally legal but un-bindable on the target entity
    (e.g. ``candidate_kind`` on a ``claim_anchor`` capability). An empty
    argument renders the full vocabulary for a domain-agnostic caller.
    """
    lines = [
        "condition_spec is mandatory for add_rule/modify_rule. The atom form: ",
        '{"schema_version":"sehd.condition_spec.v3",',
        ' "feature_source":"court_result","feature":"...",',
        ' "operator":"...","value_type":"string|boolean","value":"...",',
        ' "scope":"l1_court_transform",',
        ' "evidence_binding":"post_court_projection",',
        ' "combinator":"atom","binding":"entity_scoped"}. Select one predicate:',
    ]
    domains = tuple(dict.fromkeys(str(d) for d in binding_domains if str(d)))
    if domains:
        features: set[str] = set()
        for domain in domains:
            features.update(DOMAIN_CONDITION_FEATURES.get(domain, ()))
        lines.extend(_render_feature_lines(frozenset(features)))
        foreign = sorted(set(CONDITION_FEATURES) - features)
        if foreign:
            # Naming the legal-but-unbindable features explicitly is cheaper
            # than failing closed on the model's guess and spending a retry.
            lines.append("")
            joined = "/".join(domains)
            lines.extend(
                f"- {feature} is NOT bindable in the {joined} domain"
                for feature in foreign
            )
    else:
        lines.extend(_render_feature_lines(frozenset(CONDITION_FEATURES)))
    lines.extend([
        "",
        "For typed boolean composition, use combinator all_of / any_of / not",
        "with a children array of condition_spec objects (each a valid atom).",
        "not takes exactly one child. Prefer first-call features so the rule",
        "can prevent the first failure, not just steer a retry.",
        "binding: entity_scoped is mandatory; atoms and combinators return typed",
        "entity bindings, all_of joins by entity, and not is an anti-join;",
        "any other binding value is rejected.",
    ])
    return "\n".join(lines)


def normalize_candidate_kind(value: Any) -> str:
    lowered = " ".join(str(value or "").lower().split())
    if any(token in lowered for token in ("omission", "missing", "omit")):
        return "omission"
    if any(token in lowered for token in ("contradiction", "inconsistent", "conflict")):
        return "contradiction"
    if any(token in lowered for token in (
        "tool argument", "tool misuse", "invalid tool", "wrong tool",
    )):
        return "invalid_tool_use"
    if any(token in lowered for token in (
        "invalid action", "unauthorized action", "action error",
    )):
        return "invalid_action"
    if any(token in lowered for token in (
        "unsupported", "fabricat", "hallucin", "false claim", "claim",
    )):
        return "unsupported_claim"
    return "other"


def normalize_module_tag(value: Any) -> str:
    """Fold the census ``module_tag`` enum to its canonical closed value.

    The census emits hyphenated, Title-Case tags ("Tool-Use"); normalization
    lowercases and collapses separators so the closed module-tag set matches
    without fuzzy keyword guessing. Anything unregistered returns "" so a
    stray tag never becomes a spurious match.
    """
    tag = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return tag if tag in CONDITION_FEATURES["module_tag"].values else ""


def project_result_condition_features(
    result: Mapping[str, Any], record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project closed condition features for the post-court L1 executor.

    The deterministic L1 executor evaluates a rule's ``condition_spec``
    against this snapshot. Result-observable features (candidate_kind /
    candidate_axis / module_tag / coverage_state / missing_field /
    failed_invariant) come from the court result; trajectory-observable
    features (event_role / has_tool_*) come from the raw record, which the
    court result does not carry. No provider prompt or response controls
    applicability.
    """
    sets: dict[str, set[str]] = {
        "anchor_kind": set(), "anchor_role": set(),
        "candidate_kind": set(), "candidate_axis": set(),
        "module_tag": set(), "coverage_state": set(), "missing_field": set(),
        "failed_invariant": set(), "event_role": set(),
    }
    axis_values = CONDITION_FEATURES["candidate_axis"].values
    coverage_values = CONDITION_FEATURES["coverage_state"].values
    runtime_fields = CONDITION_FEATURES["missing_field"].values
    invariants = CONDITION_FEATURES["failed_invariant"].values
    event_roles = CONDITION_FEATURES["event_role"].values

    census = result.get("census")
    if isinstance(census, list):
        for row in census:
            if not isinstance(row, Mapping):
                continue
            sets["candidate_kind"].add(normalize_candidate_kind(row.get("candidate_kind")))
            normalized_tag = normalize_module_tag(row.get("module_tag"))
            if normalized_tag:
                sets["module_tag"].add(normalized_tag)
            axes = row.get("axes")
            values = axes if isinstance(axes, (list, tuple, set)) else (axes,)
            sets["candidate_axis"].update(
                str(item) for item in values if str(item) in axis_values
            )

    proof = result.get("proof_adjudication")
    if isinstance(proof, Mapping):
        for key in ("prosecution_coverage", "defense_coverage"):
            value = str(proof.get(key) or "unknown").lower()
            if value in coverage_values:
                sets["coverage_state"].add(value)
        for audit in proof.get("error_audits") or []:
            if not isinstance(audit, Mapping):
                continue
            for value in audit.get("missing_fields") or []:
                if str(value) in runtime_fields:
                    sets["missing_field"].add(str(value))
            for value in audit.get("failed_invariants") or []:
                if str(value) in invariants:
                    sets["failed_invariant"].add(str(value))
            for value in audit.get("failed_checks") or []:
                if str(value) in invariants:
                    sets["failed_invariant"].add(str(value))

    has_tool_call = False
    has_tool_error = False
    tool_names: list[str] = []
    if isinstance(record, Mapping):
        for row in record.get("history") or []:
            if not isinstance(row, Mapping):
                continue
            role = str(row.get("role") or "")
            if role in event_roles:
                sets["event_role"].add(role)
            calls = row.get("tool_calls")
            if isinstance(calls, list) and calls:
                has_tool_call = True
                for call in calls:
                    if isinstance(call, Mapping):
                        name = str(call.get("name") or "")
                        if name:
                            tool_names.append(name)
            responses = row.get("tool_responses")
            if isinstance(responses, (list, tuple)) and responses:
                for response in responses:
                    text = str(response or "")
                    if re.search(
                        r"\b(error|exception|failed|failure|rejected)\b",
                        text, re.IGNORECASE,
                    ):
                        has_tool_error = True
    has_tool_retry = len(tool_names) != len(set(tool_names))
    return {
        "schema_version": "sehd.result_condition_features.v1",
        **{key: sorted(values) for key, values in sets.items()},
        "has_tool_call": has_tool_call,
        "has_tool_error": has_tool_error,
        "has_tool_retry": has_tool_retry,
    }


def evaluate_condition_spec(
    value: Mapping[str, Any], features: Mapping[str, Any],
) -> ConditionEvaluation:
    valid, reason = validate_condition_spec(value)
    if not valid:
        return ConditionEvaluation(False, reason, "", "", "", None)
    spec = ConditionSpec.from_dict(value)
    combinator = spec.combinator
    if combinator == "atom":
        observed = features.get(spec.feature)
        if spec.operator == "contains":
            matched = (
                isinstance(observed, (list, tuple, set, frozenset))
                and spec.value in observed
            )
        elif spec.operator == "not_equals":
            # An absent feature never satisfies not_equals: absence is not
            # a value.
            matched = observed is not None and observed != spec.value
        else:
            matched = observed == spec.value
        return ConditionEvaluation(
            matched=matched,
            reason="matched" if matched else "predicate_false",
            feature=spec.feature,
            operator=spec.operator,
            expected=spec.value,
            observed=observed,
        )
    # Combinator form: evaluate children recursively and combine.
    child_results = [
        evaluate_condition_spec(child, features) for child in spec.children
    ]
    if combinator == "all_of":
        matched = all(r.matched for r in child_results)
        reason = "all_of_matched" if matched else "all_of_predicate_false"
    elif combinator == "any_of":
        matched = any(r.matched for r in child_results)
        reason = "any_of_matched" if matched else "any_of_predicate_false"
    elif combinator == "not":
        matched = not child_results[0].matched
        reason = "not_matched" if matched else "not_predicate_false"
    else:
        matched = False
        reason = f"unknown_combinator:{combinator}"
    return ConditionEvaluation(
        matched=matched,
        reason=reason,
        feature=spec.combinator,
        operator="combine",
        expected=str([r.matched for r in child_results]),
        observed=None,
    )
