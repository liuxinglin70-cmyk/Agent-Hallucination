"""Bounded, replayable inputs for causal diagnostics."""

from __future__ import annotations

import ast
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Mapping

from detector.identity import digest

RISK_PROFILE_SCHEMA = "detector.step_risk_profile.v1"
DEPENDENCY_GRAPH_SCHEMA = "detector.dependency_graph.v1"
CAUSAL_PROJECTION_SCHEMA = "detector.causal_projection.v1"
SCORE_STATUSES = frozenset({"scored", "unknown", "unsupported", "truncated"})
SIGNAL_PRIORITY = {
    "verified_deviation": 0, "bound_field_conflict": 1,
    "action_precondition": 2, "unsupported_claim": 3,
}
SCORER = {"id": "semantic_proxy.v1", "priority": SIGNAL_PRIORITY}


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
            for key, nested in item.items():
                if isinstance(nested, (Mapping, list)):
                    visit(nested, [*path, str(key)], depth + 1)
        elif isinstance(item, list):
            for index, nested in enumerate(item):
                visit(nested, [*path, str(index)], depth + 1)

    visit(value, [], 0)
    return deepcopy(fields), truncated


def field_binding(field: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(field[key] for key in ("entity", "attribute", "unit", "time"))


def arithmetic_check(interaction: Mapping[str, Any]) -> dict[str, Any] | None:
    """Check a calculator's literal arithmetic result, without executing tool code."""
    if str(interaction.get("tool_name") or "").lower() != "calculator":
        return None
    arguments = interaction.get("arguments")
    expression = arguments.get("expr") if isinstance(arguments, Mapping) else None
    if not isinstance(expression, str) or len(expression) > 256:
        return None
    try:
        tree = ast.parse(expression, mode="eval")
        if len(list(ast.walk(tree))) > 64:
            return None

        def evaluate(node: ast.AST) -> Fraction:
            if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
                return Fraction(ast.get_source_segment(expression, node))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = evaluate(node.operand)
                return -value if isinstance(node.op, ast.USub) else value
            if isinstance(node, ast.BinOp):
                left, right = evaluate(node.left), evaluate(node.right)
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.Div):
                    return left / right
            raise ValueError("unsupported arithmetic")

        expected = evaluate(tree.body)
        denominator = expected.denominator
        for factor in (2, 5):
            while denominator % factor == 0:
                denominator //= factor
        if denominator != 1:
            return None
        observation = interaction.get("observation")
        if type(observation) not in {str, int, float}:
            return None
        observed = Decimal(str(observation))
        if not observed.is_finite():
            return None
    except (SyntaxError, ValueError, InvalidOperation, ArithmeticError):
        return None
    return {
        "verifier": "literal_arithmetic.v1", "expression": expression,
        "expected": str(expected), "observed": str(observed),
        "status": "consistent" if expected == Fraction(observed) else "deviation",
        "scope": "local_result", "evidence_refs": [interaction["interaction_id"]],
    }
