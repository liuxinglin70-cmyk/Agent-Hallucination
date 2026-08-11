"""Executable applicability contract for evolved L1 rules.

Natural-language rule conditions are useful explanations, but they are not an
execution boundary: historically every evolved rule bound to a provider route
was injected and the provider was asked to decide whether the prose applied.
That made ``condition_not_matched`` indistinguishable from provider non-
compliance and let irrelevant rules perturb unrelated calls.

``ConditionSpec`` is the single framework-owned applicability contract.  It
uses a deliberately small closed vocabulary of provider-input features, all of
which are projected before the provider call.  The prose condition remains a
human-readable refinement; it no longer decides whether the rule is injected.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .module_charter import MODULE_OPERATION_TARGETS
from ..paca_tcl.audit_contract import (
    DEFENSE_RELATIONAL_INVARIANTS,
    ERROR_RELATIONAL_INVARIANTS,
)


SCHEMA_VERSION = "sehd.condition_spec.v2"
SCHEMA_VERSION_V1 = "sehd.condition_spec.v1"

_CANDIDATE_KINDS = frozenset({
    "omission", "unsupported_claim", "contradiction", "invalid_tool_use",
    "invalid_action", "other",
})
_AXES = frozenset({"F", "Fa", "P", "S"})
_COVERAGE_STATES = frozenset({"none", "incomplete", "complete", "unknown"})
_EVENT_ROLES = frozenset({"system", "user", "assistant", "tool"})
_RUNTIME_FIELDS = frozenset({
    str(target)
    for operations in MODULE_OPERATION_TARGETS.values()
    for targets in operations.values()
    for target in targets
})
_INVARIANTS = frozenset(
    set(ERROR_RELATIONAL_INVARIANTS) | set(DEFENSE_RELATIONAL_INVARIANTS)
)

# Features available in the FIRST provider call (pre-call causal antecedents).
# These exist in the initial provider input before any failure or retry, so a
# Skill built on them can prevent the first failure, not just steer a retry.
_FIRST_CALL_FEATURES = frozenset({
    "candidate_kind", "candidate_axis", "event_role",
    "has_tool_call", "has_tool_error",
})

# Features that only appear in retry-diagnostic prompts (after a failure).
# ``missing_field`` and ``failed_invariant`` are projected from audit failures
# that exist only after the validator has run and rejected content. A Skill
# built on them can only trigger in the repair-retry stage, not prevent the
# first failure — they are retry-diagnostic, not causal antecedents.
_RETRY_DIAGNOSTIC_FEATURES = frozenset({
    "missing_field", "failed_invariant",
})

# Set-valued features use ``contains``. Scalar/boolean features use ``equals``.
_FEATURE_CONTRACT: Mapping[str, tuple[str, frozenset[Any]]] = {
    "candidate_kind": ("contains", _CANDIDATE_KINDS),
    "candidate_axis": ("contains", _AXES),
    "missing_field": ("contains", _RUNTIME_FIELDS),
    "failed_invariant": ("contains", _INVARIANTS),
    "event_role": ("contains", _EVENT_ROLES),
    "has_tool_call": ("equals", frozenset({True, False})),
    "has_tool_error": ("equals", frozenset({True, False})),
}

# Combinators for typed boolean composition (Fix #5). ``atom`` is the v1
# single-predicate form; ``all_of``/``any_of``/``not`` compose child specs.
_COMBINATORS = frozenset({"atom", "all_of", "any_of", "not"})

# Binding scopes (Fix #5). ``route_scoped`` is the v1 default (any object on
# the route satisfying the condition triggers). ``candidate_bound`` and
# ``obligation_bound`` bind the condition to a specific candidate or
# obligation identity, preventing unrelated objects on the same route from
# triggering the rule.
_BINDINGS = frozenset({"route_scoped", "candidate_bound", "obligation_bound"})


@dataclass(frozen=True)
class ConditionSpec:
    feature: str
    operator: str
    value: str | bool
    feature_source: str = "provider_input"
    value_type: str = "string"
    scope: str = "action_spec_routes"
    evidence_binding: str = "pre_call_projection"
    schema_version: str = SCHEMA_VERSION
    # Fix #5: typed boolean composition + binding scope.
    combinator: str = "atom"
    children: tuple[dict[str, Any], ...] = ()
    binding: str = "route_scoped"

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
                value.get("feature_source") or "provider_input"
            ),
            value_type=str(value.get("value_type") or "string"),
            scope=str(value.get("scope") or "action_spec_routes"),
            evidence_binding=str(
                value.get("evidence_binding") or "pre_call_projection"
            ),
            schema_version=str(value.get("schema_version") or SCHEMA_VERSION),
            combinator=str(value.get("combinator") or "atom"),
            children=children,
            binding=str(value.get("binding") or "route_scoped"),
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
    contract = _FEATURE_CONTRACT.get(spec.feature)
    if contract is None:
        return False, f"condition_feature_unregistered:{spec.feature or 'missing'}"
    operator, allowed_values = contract
    if spec.operator != operator:
        return False, (
            f"condition_operator_invalid:{spec.feature}:{spec.operator or 'missing'}"
        )
    normalized_value: Any = spec.value
    if spec.feature in {"has_tool_call", "has_tool_error"}:
        if spec.value_type != "boolean":
            return False, f"condition_value_type_invalid:{spec.feature}:{spec.value_type}"
        if not isinstance(normalized_value, bool):
            return False, f"condition_value_not_boolean:{spec.feature}"
    else:
        if spec.value_type != "string":
            return False, f"condition_value_type_invalid:{spec.feature}:{spec.value_type}"
        normalized_value = str(normalized_value)
    if normalized_value not in allowed_values:
        return False, f"condition_value_unregistered:{spec.feature}:{normalized_value}"
    return True, ""


def validate_condition_spec(value: Any) -> tuple[bool, str]:
    if not isinstance(value, Mapping):
        return False, "condition_spec_not_object"
    spec = ConditionSpec.from_dict(value)
    # Accept both v1 (atom-only) and v2 (combinator) schema versions.
    if spec.schema_version not in (SCHEMA_VERSION, SCHEMA_VERSION_V1):
        return False, f"condition_spec_schema:{spec.schema_version}"
    if spec.feature_source != "provider_input":
        return False, f"condition_feature_source_invalid:{spec.feature_source}"
    if spec.scope != "action_spec_routes":
        return False, f"condition_scope_invalid:{spec.scope}"
    if spec.evidence_binding != "pre_call_projection":
        return False, f"condition_evidence_binding_invalid:{spec.evidence_binding}"
    # Fix #5: validate combinator + binding.
    if spec.combinator not in _COMBINATORS:
        return False, f"condition_combinator_invalid:{spec.combinator}"
    if spec.binding not in _BINDINGS:
        return False, f"condition_binding_invalid:{spec.binding}"
    # v1 schema: atom only (no combinator field). Treat as atom.
    combinator = spec.combinator if spec.schema_version == SCHEMA_VERSION else "atom"
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


def render_condition_spec_contract() -> str:
    """Prompt contract for the reflection model; values are closed."""
    lines = [
        "condition_spec is mandatory for add_rule/modify_rule. The atom form: ",
        '{"schema_version":"sehd.condition_spec.v2",',
        ' "feature_source":"provider_input","feature":"...",',
        ' "operator":"...","value_type":"string|boolean","value":"...",',
        ' "scope":"action_spec_routes",',
        ' "evidence_binding":"pre_call_projection",',
        ' "combinator":"atom","binding":"route_scoped"}. Select one predicate:',
    ]
    for feature, (operator, values) in _FEATURE_CONTRACT.items():
        rendered = ", ".join(
            json.dumps(value, ensure_ascii=False)
            for value in sorted(values, key=lambda item: str(item))
        )
        tag = (
            " (retry-diagnostic: only present after a failure)"
            if feature in _RETRY_DIAGNOSTIC_FEATURES
            else " (first-call: present before any failure)"
        )
        lines.append(f"- {feature} / {operator} / one of: {rendered}{tag}")
    lines.extend([
        "",
        "For typed boolean composition, use combinator all_of / any_of / not",
        "with a children array of condition_spec objects (each a valid atom).",
        "not takes exactly one child. Prefer first-call features so the rule",
        "can prevent the first failure, not just steer a retry.",
        "binding: route_scoped (default) | candidate_bound | obligation_bound.",
        "candidate_bound/obligation_bound prevent unrelated objects on the same",
        "route from triggering the rule.",
    ])
    return "\n".join(lines)


def _normalize_candidate_kind(value: Any) -> str:
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


def _json_payloads(text: str) -> list[Any]:
    """Decode top-level JSON values embedded in tagged provider input."""
    decoder = json.JSONDecoder()
    payloads: list[Any] = []
    index = 0
    while index < len(text):
        starts = [position for position in (text.find("{", index), text.find("[", index)) if position >= 0]
        if not starts:
            break
        start = min(starts)
        try:
            value, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        payloads.append(value)
        index = start + consumed
    return payloads


def project_route_condition_features(route_id: str, user: str) -> dict[str, Any]:
    """Project bounded structural features before a provider call.

    The projector reads only the provider's already-constructed input. It does
    not consume gold labels, evolution state, case ids, CRS, or post-call
    outputs. Unknown/open text is reduced to registered categories.
    """
    sets: dict[str, set[str]] = {
        "candidate_kind": set(), "candidate_axis": set(),
        "coverage_state": set(), "missing_field": set(),
        "failed_invariant": set(), "event_role": set(),
    }
    has_tool_call = False
    has_tool_error = False

    def visit(node: Any) -> None:
        nonlocal has_tool_call, has_tool_error
        if isinstance(node, Mapping):
            for raw_key, raw_value in node.items():
                key = str(raw_key)
                if key == "candidate_kind":
                    sets["candidate_kind"].add(_normalize_candidate_kind(raw_value))
                elif key in {"axes", "axis", "candidate_axis"}:
                    values = raw_value if isinstance(raw_value, (list, tuple, set)) else (raw_value,)
                    sets["candidate_axis"].update(
                        str(item) for item in values if str(item) in _AXES
                    )
                elif key in {"coverage_state", "prosecution_coverage", "defense_coverage"}:
                    value = str(raw_value or "unknown").lower()
                    if value in _COVERAGE_STATES:
                        sets["coverage_state"].add(value)
                elif key in {"missing_fields", "missing_field"}:
                    values = raw_value if isinstance(raw_value, (list, tuple, set)) else (raw_value,)
                    sets["missing_field"].update(
                        str(item) for item in values if str(item) in _RUNTIME_FIELDS
                    )
                elif key in {"failed_invariants", "failed_checks", "failed_invariant"}:
                    values = raw_value if isinstance(raw_value, (list, tuple, set)) else (raw_value,)
                    sets["failed_invariant"].update(
                        str(item) for item in values if str(item) in _INVARIANTS
                    )
                elif key == "role" and str(raw_value) in _EVENT_ROLES:
                    sets["event_role"].add(str(raw_value))
                if key in {"tool_name", "tool_call", "tool_calls", "tool_interactions"}:
                    if raw_value not in (None, "", [], {}):
                        has_tool_call = True
                if key in {"error", "exception", "tool_error"} and raw_value not in (None, "", False, [], {}):
                    has_tool_error = True
                if isinstance(raw_value, str) and key in {"observation", "result", "error", "message"}:
                    if re.search(r"\b(error|exception|failed|failure|rejected)\b", raw_value, re.IGNORECASE):
                        has_tool_error = True
                visit(raw_value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                visit(item)

    for payload in _json_payloads(str(user or "")):
        visit(payload)
    return {
        "schema_version": "sehd.route_condition_features.v1",
        "route_id": str(route_id),
        **{key: sorted(values) for key, values in sets.items()},
        "has_tool_call": has_tool_call,
        "has_tool_error": has_tool_error,
    }


def evaluate_condition_spec(
    value: Mapping[str, Any], features: Mapping[str, Any],
) -> ConditionEvaluation:
    valid, reason = validate_condition_spec(value)
    if not valid:
        return ConditionEvaluation(False, reason, "", "", "", None)
    spec = ConditionSpec.from_dict(value)
    combinator = spec.combinator if spec.schema_version == SCHEMA_VERSION else "atom"
    if combinator == "atom":
        observed = features.get(spec.feature)
        if spec.operator == "contains":
            matched = (
                isinstance(observed, (list, tuple, set, frozenset))
                and spec.value in observed
            )
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
