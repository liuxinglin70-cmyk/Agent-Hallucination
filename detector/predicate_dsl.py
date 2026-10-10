"""Typed, non-Turing-complete declarative DSL for self-evolved predicates.

An ``evidence_check`` intervention may carry ``body.predicate_definition``: a
JSON program whose only executable semantics are the fixed operators in this
module. There is no ``eval``, no import, no attribute traversal, and no loop:
the interpreter selects one whitelisted typed view of the packet/context,
applies one fixed operator, and maps the result to a declared verdict.

Regex matching runs in a killable subprocess under a shared program budget,
case deadline and run cancellation. Validation also rejects known ambiguous
patterns and bounds the pattern and input sizes.

The proposer can grow the observation vocabulary of the detector at runtime,
while the surface stays fail-closed: unknown fields, operators, verdicts,
oversized literals, non-integer aggregate thresholds, a conviction
``default_verdict``, and unsafe regexes are all rejected at registry
validation time.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

from detector.packet import (
    PACKET_FEATURE_NAMES,
    delivered_result as packet_delivered_result,
    packet_features,
)
from detector.runtime import check_request_deadline, current_control

PROGRAM_SCHEMA = "detector.predicate_program.v1"
PROGRAM_RUNNER = "typed_dsl"
PROGRAM_NAME_RE = re.compile(r"^evo_[a-z][a-z0-9_]{2,63}$")

MAX_RULES = 16
MAX_VERDICTS = 6
MAX_LITERAL_CHARS = 400
MAX_REGEX_CHARS = 300
MAX_CONDITIONS = 4

MAX_REGEX_HAYSTACK = 4096
MAX_REGEX_SECONDS = 0.5
MAX_PROGRAM_SECONDS = 1.0

_REGEX_WORKER = (
    "import json,re,sys; pattern,text=json.load(sys.stdin); "
    "print(json.dumps(re.search(pattern,text,re.IGNORECASE) is not None))"
)


class PredicateResourceExceeded(RuntimeError):
    """A regex could not establish a result within its execution budget."""


def _check_execution() -> None:
    control = current_control()
    if control is not None:
        control.check()
    check_request_deadline()


def _regex_matches(pattern: str, text: str, program_deadline: float) -> bool:
    _check_execution()
    limit = min(program_deadline, time.monotonic() + MAX_REGEX_SECONDS)
    if time.monotonic() >= limit:
        raise PredicateResourceExceeded("predicate regex budget exhausted")
    process = subprocess.Popen(
        [sys.executable, "-c", _REGEX_WORKER],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    payload = json.dumps([pattern, text[:MAX_REGEX_HAYSTACK]], ensure_ascii=True)
    try:
        while True:
            _check_execution()
            remaining = limit - time.monotonic()
            if remaining <= 0:
                raise PredicateResourceExceeded("predicate regex budget exhausted")
            try:
                stdout, _ = process.communicate(payload, timeout=min(0.02, remaining))
                break
            except subprocess.TimeoutExpired:
                # communicate retains its input buffer across polls.
                payload = None
        _check_execution()
        if time.monotonic() >= limit:
            raise PredicateResourceExceeded("predicate regex budget exhausted")
        if process.returncode != 0:
            raise PredicateResourceExceeded("predicate regex worker failed")
        result = json.loads(stdout)
        if not isinstance(result, bool):
            raise PredicateResourceExceeded("predicate regex worker returned an invalid result")
        return result
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()

_BACKREFERENCE_RE = re.compile(r"\\[1-9]")

_WHEN_FACT_FIELDS = {
    "judge_positive": "context_judge_positive",
    "judge_confidence": "context_judge_confidence",
    "judge_hallucination_reason": "context_judge_hallucination_reason",
    "delivered_result": "context_judge_delivered_result",
    "quote": "context_quote",
}

# Scalar views are string/number values. List views return a flat list of
# strings. Aggregate views return an integer and may only be combined with
# numeric-count operators.
FIELD_TYPES: dict[str, str] = {
    "question_text": "scalar",
    "last_prose_text": "scalar",
    "last_answer_tool_name": "scalar",
    "last_answer_tool_arguments": "scalar",
    "last_answer_tool_observation": "scalar",
    "answer_anchor_status": "scalar",
    "event_texts": "list",
    "prose_texts": "list",
    "tool_names": "list",
    "tool_arguments": "list",
    "tool_observations": "list",
    "interaction_texts": "list",
    "event_ids": "list",
    "interaction_ids": "list",
    "packet_features": "list",
    "context_evidence_refs": "list",
    "context_contract_kinds": "list",
    "context_contract_requirements": "list",
    "context_quote": "scalar",
    "context_judge_delivered_result": "scalar",
    "context_judge_positive": "scalar",
    "context_judge_confidence": "scalar",
    "context_judge_hallucination_reason": "scalar",
    "event_count": "aggregate",
    "tool_call_count": "aggregate",
    "interaction_count": "aggregate",
    "prose_event_count": "aggregate",
}

SCALAR_OPERATORS = frozenset({
    "equals", "not_equals",
    "contains", "not_contains",
    "starts_with", "not_starts_with",
    "ends_with", "not_ends_with",
    "matches", "not_matches",
    "exists", "missing",
})
LIST_OPERATORS = frozenset({
    "any_contains", "all_contain", "none_contains",
    "any_equals", "all_equal", "none_equals",
    "exists", "missing",
})
AGGREGATE_OPERATORS = frozenset({
    "equals", "not_equals",
    "greater_than", "greater_than_or_equal",
    "less_than", "less_than_or_equal",
    "exists", "missing",
})
ALL_OPERATORS = SCALAR_OPERATORS | LIST_OPERATORS | AGGREGATE_OPERATORS

_VALUE_OPERATORS = frozenset({
    "equals", "not_equals",
    "contains", "not_contains",
    "starts_with", "not_starts_with",
    "ends_with", "not_ends_with",
    "matches", "not_matches",
    "any_contains", "all_contain", "none_contains",
    "any_equals", "all_equal", "none_equals",
    "greater_than", "greater_than_or_equal",
    "less_than", "less_than_or_equal",
})


def _require_text(value: Any, field: str, *, limit: int = MAX_LITERAL_CHARS) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"predicate program field {field} must be non-empty text")
    if len(text) > limit:
        raise ValueError(
            f"predicate program field {field} exceeds {limit} characters"
        )
    return text


def _require_literal(value: Any, field: str, *, limit: int = MAX_LITERAL_CHARS) -> str:
    """Normalise one comparison literal, accepting numbers and booleans.

    ``_require_text`` collapses ``0`` / ``False`` to ``""`` and then rejects
    them, which made ``event_count equals 0`` inexpressible. A condition value
    is a literal, so a scalar of any JSON type is meaningful; only ``null``,
    containers and an empty string are refused.
    """
    if value is None or isinstance(value, (Mapping, list, tuple, set, bytes)):
        raise ValueError(
            f"predicate program field {field} must be a scalar literal"
        )
    text = ("true" if value else "false") if isinstance(value, bool) else str(value).strip()
    if not text:
        raise ValueError(f"predicate program field {field} must be non-empty text")
    if len(text) > limit:
        raise ValueError(
            f"predicate program field {field} exceeds {limit} characters"
        )
    return text


def _regex_has_quantified_ambiguity(pattern: str) -> bool:
    """True when a quantified group can backtrack catastrophically.

    This conservative syntax check rejects repeated groups containing another
    quantifier or alternation. It is not a complete ambiguity analysis; the
    subprocess execution budget bounds accepted patterns.
    """
    stack: list[list[Any]] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            index += 1
            if index < length and pattern[index] == "^":
                index += 1
            if index < length and pattern[index] == "]":
                index += 1
            while index < length and pattern[index] != "]":
                if pattern[index] == "\\":
                    index += 1
                index += 1
            index += 1
            continue
        if char == "(":
            stack.append([False, False])
            index += 1
            continue
        if char == ")":
            if not stack:
                index += 1
                continue
            has_quantifier, has_alternation = stack.pop()
            quantified = index + 1 < length and pattern[index + 1] in "*+?{"
            if quantified and (has_quantifier or has_alternation):
                return True
            if stack and (has_quantifier or has_alternation or quantified):
                stack[-1][0] = True
            index += 1
            continue
        if char in "*+{":
            if stack:
                stack[-1][0] = True
            index += 1
            continue
        if char == "|" and stack:
            stack[-1][1] = True
        index += 1
    return False


def _compile_safe_regex(pattern: str, field: str) -> re.Pattern[str]:
    if _BACKREFERENCE_RE.search(pattern):
        raise ValueError(
            f"predicate program field {field} must not use backreferences"
        )
    if _regex_has_quantified_ambiguity(pattern):
        raise ValueError(
            f"predicate program field {field} must not use a quantified group "
            "with an inner quantifier or alternation (catastrophic backtracking)"
        )
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(
            f"predicate program field {field} is not a valid regex: {exc}"
        ) from exc


def _clean_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        raise ValueError(f"predicate program field {field} must be a list")
    try:
        items = list(value)
    except TypeError as exc:
        raise ValueError(f"predicate program field {field} must be a list") from exc
    if any(not isinstance(item, str) for item in items):
        raise ValueError(
            f"predicate program field {field} must contain only strings"
        )
    return [str(item) for item in items]


def _norm_verdicts(value: Any, field: str) -> list[str]:
    verdicts = _clean_list(value, field)
    if not 2 <= len(verdicts) <= MAX_VERDICTS:
        raise ValueError(
            f"predicate program field {field} needs 2..{MAX_VERDICTS} verdicts"
        )
    if len(verdicts) != len(set(verdicts)):
        raise ValueError(f"predicate program field {field} must be unique")
    return verdicts


def _validate_condition(
    condition: Any,
    index: int,
    name: str,
    *,
    condition_index: int | None = None,
) -> dict[str, Any]:
    label = f"rules[{index}]" if condition_index is None else (
        f"rules[{index}].conditions[{condition_index}]"
    )
    if not isinstance(condition, Mapping):
        raise ValueError(f"{name}.{label} must be an object")
    field = _require_text(condition.get("field"), f"{label}.field")
    if field not in FIELD_TYPES:
        raise ValueError(
            f"{name}.{label}.field {field!r} is outside the typed field "
            f"vocabulary {sorted(FIELD_TYPES)}"
        )
    field_type = FIELD_TYPES[field]
    operator = _require_text(condition.get("operator"), f"{label}.operator")
    allowed = (
        SCALAR_OPERATORS if field_type == "scalar"
        else LIST_OPERATORS if field_type == "list"
        else AGGREGATE_OPERATORS
    )
    if operator not in allowed:
        raise ValueError(
            f"{name}.{label}.operator {operator!r} is not valid for "
            f"{field_type} field {field!r}; allowed: {sorted(allowed)}"
        )
    value = condition.get("value")
    if operator in _VALUE_OPERATORS:
        text = _require_literal(value, f"{label}.value")
        if operator in {"matches", "not_matches"}:
            if len(text) > MAX_REGEX_CHARS:
                raise ValueError(
                    f"{name}.{label}.value exceeds {MAX_REGEX_CHARS} "
                    "characters for regex match"
                )
            _compile_safe_regex(text, f"{name}.{label}.value")
        if field_type == "aggregate" and operator in {
            "equals", "not_equals",
            "greater_than", "greater_than_or_equal",
            "less_than", "less_than_or_equal",
        }:
            # Every aggregate operator parses its threshold as an int at
            # runtime; validate them all here so validation and execution
            # cannot disagree and crash the whole batch later.
            try:
                int(text)
            except ValueError as exc:
                raise ValueError(
                    f"{name}.{label}.value must be an integer for "
                    f"aggregate operator {operator!r}"
                ) from exc
        value = text
    elif value is not None:
        raise ValueError(
            f"{name}.{label} has a value but operator {operator!r} "
            "does not consume one"
        )
    else:
        value = None
    return {"field": field, "operator": operator, "value": value}


def _validate_rule(rule: Any, index: int, verdicts: list[str], name: str) -> dict[str, Any]:
    if not isinstance(rule, Mapping):
        raise ValueError(f"{name}.rules[{index}] must be an object")
    unknown = set(rule) - {"field", "operator", "value", "conditions", "verdict", "detail", "when"}
    if unknown:
        raise ValueError(f"{name}.rules[{index}] has unknown fields {sorted(unknown)}")
    has_single = ("field" in rule) or ("operator" in rule)
    has_conditions = "conditions" in rule
    if has_single and has_conditions:
        raise ValueError(
            f"{name}.rules[{index}] must use either field/operator/value "
            "or conditions, not both"
        )
    if has_conditions:
        raw_conditions = rule.get("conditions")
        if not isinstance(raw_conditions, list) or not raw_conditions:
            raise ValueError(
                f"{name}.rules[{index}].conditions must be a non-empty list"
            )
        if len(raw_conditions) > MAX_CONDITIONS:
            raise ValueError(
                f"{name}.rules[{index}] has {len(raw_conditions)} conditions; "
                f"limit is {MAX_CONDITIONS}"
            )
        conditions = [
            _validate_condition(
                item, index, name, condition_index=condition_index,
            )
            for condition_index, item in enumerate(raw_conditions)
        ]
        detail_source = " AND ".join(
            f"{item['field']} {item['operator']}" + (
                f" {item['value']!r}" if item["value"] is not None else ""
            )
            for item in conditions
        )
    elif has_single:
        conditions = [_validate_condition(rule, index, name)]
        detail_source = (
            f"{conditions[0]['field']} {conditions[0]['operator']}" + (
                f" {conditions[0]['value']!r}"
                if conditions[0]["value"] is not None else ""
            )
        )
    else:
        raise ValueError(
            f"{name}.rules[{index}] must declare field/operator/value "
            "or a conditions list"
        )
    verdict = _require_text(rule.get("verdict"), f"rules[{index}].verdict")
    if verdict not in verdicts:
        raise ValueError(
            f"{name}.rules[{index}].verdict {verdict!r} is not declared in "
            f"program verdicts {verdicts}"
        )
    detail = str(rule.get("detail") or "").strip()
    if not detail:
        detail = detail_source
    if len(detail) > MAX_LITERAL_CHARS:
        raise ValueError(
            f"{name}.rules[{index}].detail exceeds {MAX_LITERAL_CHARS} characters"
        )
    when = rule.get("when")
    if when is not None and not isinstance(when, Mapping):
        raise ValueError(f"{name}.rules[{index}].when must be an object")
    when = when or {}
    unknown_when = set(when) - {"packet_features", "facts"}
    if unknown_when:
        raise ValueError(f"{name}.rules[{index}].when has unknown fields {sorted(unknown_when)}")
    when_features = set(_clean_list(when.get("packet_features"), f"rules[{index}].when.packet_features"))
    if when_features and not when_features <= PACKET_FEATURE_NAMES:
        raise ValueError(
            f"{name}.rules[{index}].when.packet_features contains an unknown "
            "packet feature"
        )
    when_facts = _clean_list(when.get("facts"), f"rules[{index}].when.facts")
    fact_conditions = []
    for fact in when_facts:
        if "=" not in fact:
            raise ValueError(
                f"{name}.rules[{index}].when.facts item {fact!r} must be name=value"
            )
        fact_name, fact_value = fact.split("=", 1)
        field = _WHEN_FACT_FIELDS.get(fact_name, fact_name)
        fact_conditions.append(_validate_condition(
            {"field": field, "operator": "equals", "value": fact_value},
            index, name,
        ))
    conditions = fact_conditions + conditions
    if len(conditions) > MAX_CONDITIONS:
        raise ValueError(f"{name}.rules[{index}] has {len(conditions)} combined conditions; limit is {MAX_CONDITIONS}")
    return {
        "conditions": conditions,
        "verdict": verdict,
        "detail": detail,
        "when": {"packet_features": sorted(when_features)},
    }


def validate_predicate_program(value: Any) -> dict[str, Any]:
    """Validate and normalize one predicate program (fail-closed)."""
    if not isinstance(value, Mapping):
        raise ValueError("predicate_definition must be an object")
    schema = str(value.get("schema_version") or PROGRAM_SCHEMA)
    if schema != PROGRAM_SCHEMA:
        raise ValueError(
            f"unsupported predicate program schema {schema!r}"
        )
    name = _require_text(value.get("name"), "name")
    if not PROGRAM_NAME_RE.match(name):
        raise ValueError(
            f"predicate program name {name!r} must match {PROGRAM_NAME_RE.pattern}"
        )
    description = _require_text(value.get("description"), "description")
    verdicts = _norm_verdicts(value.get("verdicts"), "verdicts")
    if "not_applicable" not in verdicts:
        raise ValueError(
            f"predicate program {name} must declare not_applicable in verdicts"
        )
    conviction = _clean_list(value.get("conviction_verdicts"), "conviction_verdicts")
    if not conviction:
        raise ValueError(
            f"predicate program {name} must declare at least one "
            "conviction-authorized verdict"
        )
    if not set(conviction) <= set(verdicts):
        raise ValueError(
            f"predicate program {name} conviction_verdicts {conviction} must "
            f"be a subset of verdicts {verdicts}"
        )
    default_verdict = _require_text(value.get("default_verdict"), "default_verdict")
    if default_verdict not in verdicts:
        raise ValueError(
            f"predicate program {name} default_verdict {default_verdict!r} is "
            f"outside declared verdicts {verdicts}"
        )
    if default_verdict in conviction:
        # The default fires when NO rule matched. Authorising it as a
        # conviction would let "nothing matched" become mechanical evidence
        # for a terminal conviction, which defeats the evidence requirement.
        raise ValueError(
            f"predicate program {name} default_verdict {default_verdict!r} must "
            f"not be a conviction verdict {sorted(conviction)}"
        )
    raw_rules = value.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError(f"predicate program {name} needs at least one rule")
    if len(raw_rules) > MAX_RULES:
        raise ValueError(
            f"predicate program {name} has {len(raw_rules)} rules; limit is {MAX_RULES}"
        )
    rules = [
        _validate_rule(rule, index, verdicts, name)
        for index, rule in enumerate(raw_rules)
    ]
    program = {
        "schema_version": PROGRAM_SCHEMA,
        "name": name,
        "description": description,
        "verdicts": verdicts,
        "conviction_verdicts": conviction,
        "default_verdict": default_verdict,
        "rules": rules,
    }
    return program


def predicate_program_hash(program: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        program, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]




# ---------------------------------------------------------------------------
# Typed view extraction (the complete runtime surface of the DSL).
# ---------------------------------------------------------------------------

def _flat_texts(events: Sequence[Any]) -> list[str]:
    rows: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        chunks = [str(event.get("content") or "")]
        for interaction in event.get("tool_interactions") or []:
            if not isinstance(interaction, Mapping):
                continue
            chunks.extend((
                str(interaction.get("tool_name") or ""),
                str(interaction.get("arguments") or ""),
                str(interaction.get("observation") or ""),
            ))
        rows.append("\n".join(chunks))
    return rows


def _scalarize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "\n".join(str(item) for item in value)
    return str(value)


def _extract_view(field: str, packet: Mapping[str, Any], context: Mapping[str, Any]) -> Any:
    events = list(packet.get("events") or ())
    question = str(packet.get("question") or "")
    features = packet_features(packet)
    anchors = packet_delivered_result(packet)
    answer_tool = anchors.get("last_answer_tool_call") or {}
    event_texts = _flat_texts(events)
    prose_texts = [
        str(event.get("content") or "")
        for event in events
        if isinstance(event, Mapping) and str(event.get("content") or "").strip()
    ]
    interactions = [
        interaction
        for event in events
        if isinstance(event, Mapping)
        for interaction in event.get("tool_interactions") or ()
        if isinstance(interaction, Mapping)
    ]
    interaction_texts = [
        "\n".join((
            str(interaction.get("tool_name") or ""),
            str(interaction.get("arguments") or ""),
            str(interaction.get("observation") or ""),
        ))
        for interaction in interactions
    ]
    tool_names = [str(interaction.get("tool_name") or "") for interaction in interactions]
    tool_arguments = [
        _scalarize(interaction.get("arguments"))
        for interaction in interactions
    ]
    tool_observations = [
        _scalarize(interaction.get("observation"))
        for interaction in interactions
    ]
    contract_rows = list(context.get("contracts") or ())
    if field == "question_text":
        return question
    if field == "last_prose_text":
        last_prose_id = anchors.get("last_prose_event_id")
        for event in events:
            if isinstance(event, Mapping) and str(event.get("event_id") or "") == last_prose_id:
                return str(event.get("content") or "")
        return ""
    if field == "last_answer_tool_name":
        return str(answer_tool.get("tool_name") or "")
    if field == "last_answer_tool_arguments":
        return _scalarize(answer_tool.get("arguments"))
    if field == "last_answer_tool_observation":
        return _scalarize(answer_tool.get("observation"))
    if field == "answer_anchor_status":
        return str(anchors.get("answer_anchor_status") or "unknown")
    if field == "event_texts":
        return event_texts
    if field == "prose_texts":
        return prose_texts
    if field == "tool_names":
        return tool_names
    if field == "tool_arguments":
        return tool_arguments
    if field == "tool_observations":
        return tool_observations
    if field == "interaction_texts":
        return interaction_texts
    if field == "event_ids":
        return [
            str(event.get("event_id") or "")
            for event in events if isinstance(event, Mapping)
        ]
    if field == "interaction_ids":
        return [str(item.get("interaction_id") or "") for item in interactions]
    if field == "packet_features":
        return sorted(features)
    if field == "context_evidence_refs":
        return [str(item) for item in context.get("evidence_refs") or ()]
    if field == "context_contract_kinds":
        return [
            str(item.get("kind") or "")
            for item in contract_rows
            if isinstance(item, Mapping) and item.get("kind")
        ]
    if field == "context_contract_requirements":
        return [
            str(item.get("requirement") or "")
            for item in contract_rows
            if isinstance(item, Mapping) and item.get("requirement")
        ]
    if field == "context_quote":
        return str(context.get("quote") or context.get("root_cause_quote") or "")
    if field == "context_judge_delivered_result":
        return str(context.get("delivered_result") or "")
    if field == "context_judge_positive":
        judge_parsed = context.get("judge_parsed")
        positive = (
            judge_parsed.get("is_hallucination")
            if isinstance(judge_parsed, Mapping)
            else context.get("judge_positive")
        )
        if positive is None:
            return ""
        if isinstance(positive, str):
            # A stringly "false" must not read as truthy; the judge parser
            # normally enforces a real bool, but the typed view is also fed
            # from replayed contexts and a fact string.
            return "true" if positive.strip().lower() in {"true", "1", "yes"} else "false"
        return "true" if bool(positive) else "false"
    if field == "context_judge_confidence":
        judge_parsed = context.get("judge_parsed")
        if isinstance(judge_parsed, Mapping):
            return str(judge_parsed.get("confidence") or "")
        return str(context.get("judge_confidence") or "")
    if field == "context_judge_hallucination_reason":
        judge_parsed = context.get("judge_parsed")
        if isinstance(judge_parsed, Mapping):
            return str(judge_parsed.get("hallucination_reason") or "")
        return str(context.get("judge_hallucination_reason") or "")
    if field == "event_count":
        return len(events)
    if field == "tool_call_count":
        return len(interactions)
    if field == "interaction_count":
        return len(interactions)
    if field == "prose_event_count":
        return len(prose_texts)
    raise ValueError(f"unknown typed field {field!r}")  # unreachable after validation


PROFILE_SCALAR_LIMIT = 240
PROFILE_LIST_ITEMS = 6
PROFILE_LIST_ITEM_LIMIT = 160


def _clip(value: str, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + " ..."


def typed_field_profile(
    packet: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compact de-identified literal profile for proposer diagnostics.

    This is the same typed view the interpreter executes. Scalars are
    truncated, lists are capped per item and per length, and aggregate
    fields keep their integer. No case id or gold label is ever present.
    """
    profile: dict[str, Any] = {}
    for field, field_type in FIELD_TYPES.items():
        view = _extract_view(field, packet, dict(context or {}))
        if field_type == "scalar":
            profile[field] = _clip(str(view), PROFILE_SCALAR_LIMIT)
        elif field_type == "list":
            profile[field] = [
                _clip(item, PROFILE_LIST_ITEM_LIMIT)
                for item in list(view)[:PROFILE_LIST_ITEMS]
            ]
        else:
            profile[field] = int(view)
    return profile


def _matches_scalar(view: str, operator: str, value: str | None, program_deadline: float) -> bool:
    hay = view.lower()
    needle = str(value or "").lower()
    if operator == "equals":
        return hay == needle
    if operator == "not_equals":
        return hay != needle
    if operator == "contains":
        return needle in hay
    if operator == "not_contains":
        return needle not in hay
    if operator == "starts_with":
        return hay.startswith(needle)
    if operator == "not_starts_with":
        return not hay.startswith(needle)
    if operator == "ends_with":
        return hay.endswith(needle)
    if operator == "not_ends_with":
        return not hay.endswith(needle)
    if operator in {"matches", "not_matches"}:
        matched = _regex_matches(value or "", view, program_deadline)
        return matched if operator == "matches" else not matched
    if operator == "exists":
        return bool(view)
    if operator == "missing":
        return not bool(view)
    raise ValueError(f"unsupported scalar operator {operator!r}")


def _matches_list(view: list[str], operator: str, value: str | None) -> bool:
    rows = [row.lower() for row in view]
    needle = str(value or "").lower()
    if operator == "any_contains":
        return any(needle in row for row in rows)
    if operator == "all_contain":
        return bool(rows) and all(needle in row for row in rows)
    if operator == "none_contains":
        return all(needle not in row for row in rows)
    if operator == "any_equals":
        return any(row == needle for row in rows)
    if operator == "all_equal":
        return bool(rows) and all(row == needle for row in rows)
    if operator == "none_equals":
        return all(row != needle for row in rows)
    if operator == "exists":
        return bool(rows)
    if operator == "missing":
        return not bool(rows)
    raise ValueError(f"unsupported list operator {operator!r}")


def _matches_aggregate(view: int, operator: str, value: str | None) -> bool:
    if operator == "exists":
        return bool(view)
    if operator == "missing":
        return not bool(view)
    threshold = int(str(value or "0"))
    if operator == "equals":
        return view == threshold
    if operator == "not_equals":
        return view != threshold
    if operator == "greater_than":
        return view > threshold
    if operator == "greater_than_or_equal":
        return view >= threshold
    if operator == "less_than":
        return view < threshold
    if operator == "less_than_or_equal":
        return view <= threshold
    raise ValueError(f"unsupported aggregate operator {operator!r}")


def _condition_matches(
    condition: Mapping[str, Any],
    packet: Mapping[str, Any],
    context: Mapping[str, Any],
    program_deadline: float,
) -> bool:
    field = str(condition.get("field") or "")
    field_type = FIELD_TYPES[field]
    view = _extract_view(field, packet, context)
    operator = str(condition.get("operator") or "")
    value = condition.get("value")
    return (
        _matches_scalar(view, operator, value, program_deadline)
        if field_type == "scalar"
        else _matches_list(view, operator, value)
        if field_type == "list"
        else _matches_aggregate(view, operator, value)
    )


def run_predicate_program(
    program: Mapping[str, Any],
    packet: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one validated predicate program deterministically."""
    _check_execution()
    spec = validate_predicate_program(program)
    context = dict(context or {})
    program_deadline = time.monotonic() + MAX_PROGRAM_SECONDS
    for rule in spec["rules"]:
        _check_execution()
        if not set(rule["when"]["packet_features"]) <= packet_features(packet):
            continue
        conditions = list(rule.get("conditions") or ())
        if not conditions:
            continue
        try:
            matched = all(
                _condition_matches(condition, packet, context, program_deadline)
                for condition in conditions
            )
        except PredicateResourceExceeded as exc:
            # Unknown match results cannot authorize the inverse operator or
            # let a later rule convict after the failed observation.
            return {
                "predicate": str(spec["name"]), "verdict": "not_applicable",
                "detail": str(exc), "runner": PROGRAM_RUNNER,
                "definition_hash": predicate_program_hash(spec),
                "execution_status": "resource_exhausted",
            }
        if matched:
            return {
                "predicate": str(spec["name"]),
                "verdict": str(rule["verdict"]),
                "detail": str(rule["detail"]),
                "runner": PROGRAM_RUNNER,
                "definition_hash": predicate_program_hash(spec),
            }
    return {
        "predicate": str(spec["name"]),
        "verdict": str(spec["default_verdict"]),
        "detail": f"no rule matched; default verdict {spec['default_verdict']}",
        "runner": PROGRAM_RUNNER,
        "definition_hash": predicate_program_hash(spec),
    }


__all__ = [
    "AGGREGATE_OPERATORS",
    "ALL_OPERATORS",
    "FIELD_TYPES",
    "LIST_OPERATORS",
    "MAX_LITERAL_CHARS",
    "MAX_REGEX_CHARS",
    "MAX_RULES",
    "MAX_VERDICTS",
    "PROGRAM_RUNNER",
    "PROGRAM_SCHEMA",
    "SCALAR_OPERATORS",
    "predicate_program_hash",
    "run_predicate_program",
    "typed_field_profile",
    "validate_predicate_program",
]
