"""Bounded, replayable inputs for causal diagnostics."""

from __future__ import annotations

import ast
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Mapping

from detector.identity import digest

RISK_PROFILE_SCHEMA = "detector.step_risk_profile.v1"
DEPENDENCY_GRAPH_SCHEMA = "detector.dependency_graph.v2"
CAUSAL_PROJECTION_SCHEMA = "detector.causal_projection.v1"
SCORE_STATUSES = frozenset({"scored", "unknown", "unsupported", "truncated"})
SIGNAL_PRIORITY = {
    "verified_deviation": 0, "bound_field_conflict": 1, "observed_tool_failure": 1,
    "external_guidance": 1, "action_precondition": 2,
    "unverified_action": 2, "unverified_observation": 2, "unsupported_claim": 3,
}
SCORER = {"id": "semantic_proxy.v2", "priority": SIGNAL_PRIORITY}


def seal(payload: Mapping[str, Any], key: str = "digest") -> dict[str, Any]:
    result = deepcopy(dict(payload))
    result[key] = digest(result)
    return result


def validate_seal(payload: Mapping[str, Any], *, schema: str) -> None:
    if payload.get("schema_version") != schema or payload.get("digest") != digest({
        key: value for key, value in payload.items() if key != "digest"
    }):
        raise ValueError("causal contract identity mismatch")


def bound_fields(value: Any, *, limit: int = 64) -> tuple[list[dict[str, Any]], bool]:
    """Read explicit entity/attribute/value records; never infer units or entities."""
    fields: list[dict[str, Any]] = []
    truncated = False
    visits = 0

    def visit(item: Any, path: list[str], depth: int) -> None:
        nonlocal truncated, visits
        visits += 1
        if visits > limit * 8 or depth > 8 or len(fields) >= limit:
            truncated = True
            return
        if isinstance(item, Mapping):
            if (isinstance(item.get("entity"), str) and item["entity"].strip()
                    and isinstance(item.get("attribute"), str) and item["attribute"].strip()
                    and "value" in item and isinstance(item["value"], (str, int, float))
                    and not isinstance(item["value"], bool)
                    and all(item.get(key) is None or isinstance(item[key], (str, int, float))
                            and not isinstance(item[key], bool) for key in ("unit", "time"))):
                fields.append({
                    "path": path, "entity": item["entity"], "attribute": item["attribute"],
                    "value": item["value"], "unit": item.get("unit"), "time": item.get("time"),
                    "source_ref": item.get("source_ref"),
                })
                if "source_binding" in item:
                    alias = item["source_binding"]
                    valid = (isinstance(alias, Mapping) and set(alias) == {"entity", "attribute", "unit", "time"}
                             and all(isinstance(alias[key], str) and alias[key].strip() for key in ("entity", "attribute"))
                             and all(alias[key] is None or type(alias[key]) in {str, int, float}
                                     for key in ("unit", "time")))
                    fields[-1]["source_binding"] = [alias[key] for key in ("entity", "attribute", "unit", "time")] if valid else []
            for key, nested in sorted(item.items(), key=lambda pair: str(pair[0])):
                if isinstance(nested, (Mapping, list)):
                    visit(nested, [*path, str(key)], depth + 1)
        elif isinstance(item, list):
            for index, nested in enumerate(item):
                visit(nested, [*path, str(index)], depth + 1)

    visit(value, [], 0)
    return deepcopy(fields), truncated


def field_binding(field: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(field[key] for key in ("entity", "attribute", "unit", "time"))


def numeric_literal(value: Any) -> Decimal | None:
    if type(value) not in {str, int, float}:
        return None
    if ((isinstance(value, str) and len(value) > 256)
            or (type(value) is int and value.bit_length() > 512)):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and abs(number.adjusted()) <= 128 else None
    except InvalidOperation:
        return None


def literal_arithmetic_value(expression: Any) -> Fraction | None:
    """Evaluate only bounded finite-decimal literal arithmetic."""
    if not isinstance(expression, str) or len(expression) > 256:
        return None
    try:
        tree = ast.parse(expression, mode="eval")
        if len(list(ast.walk(tree))) > 64:
            return None

        def bounded(value: Fraction) -> Fraction:
            if max(value.numerator.bit_length(), value.denominator.bit_length()) > 512:
                raise ValueError("arithmetic result budget")
            return value

        def evaluate(node: ast.AST) -> Fraction:
            if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
                literal = Decimal(ast.get_source_segment(expression, node))
                if not literal.is_finite() or abs(literal.adjusted()) > 128:
                    raise ValueError("arithmetic literal budget")
                return bounded(Fraction(literal))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = evaluate(node.operand)
                return -value if isinstance(node.op, ast.USub) else value
            if isinstance(node, ast.BinOp):
                left, right = evaluate(node.left), evaluate(node.right)
                if isinstance(node.op, ast.Add):
                    return bounded(left + right)
                if isinstance(node.op, ast.Sub):
                    return bounded(left - right)
                if isinstance(node.op, ast.Mult):
                    return bounded(left * right)
                if isinstance(node.op, ast.Div):
                    return bounded(left / right)
            raise ValueError("unsupported arithmetic")

        expected = evaluate(tree.body)
        denominator = expected.denominator
        for factor in (2, 5):
            while denominator % factor == 0:
                denominator //= factor
        if denominator != 1:
            return None
    except (SyntaxError, ValueError, InvalidOperation, ArithmeticError):
        return None
    return expected


def arithmetic_check(interaction: Mapping[str, Any]) -> dict[str, Any] | None:
    """Check a calculator's literal arithmetic result, without executing tool code."""
    if str(interaction.get("tool_name") or "").lower() != "calculator":
        return None
    arguments = interaction.get("arguments")
    expression = arguments.get("expr") if isinstance(arguments, Mapping) else None
    expected = literal_arithmetic_value(expression)
    observed = numeric_literal(interaction.get("observation"))
    if expected is None or observed is None:
        return None
    return {
        "verifier": "literal_arithmetic.v1", "expression": expression,
        "expected": str(expected), "observed": str(observed),
        "status": "consistent" if expected == Fraction(observed) else "deviation",
        "scope": "local_result", "evidence_refs": [interaction["interaction_id"]],
    }


def bind_arithmetic_claim(
    packet: Mapping[str, Any], positive: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Bind a referenced literal result, without inferring semantic propagation."""
    delivered = numeric_literal(positive.get("delivered_result"))
    corrected = numeric_literal(positive.get("correct_result"))
    quoted = numeric_literal(positive.get("quote"))
    if (positive.get("source") != "judge" or delivered is None or corrected is None
            or quoted != delivered or corrected == delivered):
        return None
    refs = set(positive.get("evidence_refs") or ())
    matches = []
    for event in packet["events"]:
        eid = event["event_id"]
        interactions = event.get("tool_interactions") or ()
        direct_refs = {item["interaction_id"] for item in interactions}.intersection(refs)
        if not direct_refs and (eid not in refs or len(interactions) != 1):
            continue
        if len(direct_refs) > 32:
            return None
        for interaction in interactions:
            iid = interaction["interaction_id"]
            if direct_refs and iid not in direct_refs:
                continue
            check = arithmetic_check(interaction)
            if (check is not None and check["status"] == "deviation"
                    and numeric_literal(check["observed"]) == delivered
                    and Fraction(check["expected"]) == Fraction(corrected)):
                matches.append((eid, iid, check))
    if len(matches) != 1:
        return None
    eid, iid, check = matches[0]
    return seal({
        "schema_version": "detector.local_claim_binding.v1",
        "packet_digest": digest(packet), "target_digest": digest(positive),
        "event_id": eid, "interaction_id": iid, "expression": check["expression"],
        "observed": check["observed"], "expected": check["expected"],
        "status": "verified_local_result", "scope": "literal_arithmetic_result",
        "decision_authority": "none", "localization_authority": "none",
    }), check
