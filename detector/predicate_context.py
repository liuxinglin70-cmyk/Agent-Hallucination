"""Hook-specific predicate inputs shared by runtime and offline evaluation."""
from typing import Any, Mapping


def judge_predicate_context(judge: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "judge_parsed": dict(judge),
        "judge_positive": judge.get("is_hallucination"),
        "judge_confidence": judge.get("confidence") or "",
        "contracts": judge.get("contracts") or [],
        "evidence_refs": judge.get("evidence_refs") or [],
        "delivered_result": judge.get("delivered_result") or "",
        "quote": (judge.get("root_cause") or {}).get("quote") or "",
    }


_HEARING_FIELDS = frozenset({
    "context_evidence_refs", "context_contract_kinds",
    "context_contract_requirements", "context_quote",
})
_JUDGE_FIELDS = _HEARING_FIELDS | frozenset({
    "context_judge_delivered_result", "context_judge_positive",
    "context_judge_confidence", "context_judge_hallucination_reason",
})
HOOK_CONTEXT_FIELDS = {
    "judge.post": _JUDGE_FIELDS,
    "recall.trigger": _JUDGE_FIELDS,
    # Upgrade sees candidates, never the judge verdict or its answer.
    "upgrade.pre": frozenset(),
    "defense.pre": _HEARING_FIELDS,
}


def validate_program_hook(program: Mapping[str, Any], hook: str) -> None:
    """Reject unavailable inputs, rather than silently interpreting them as empty."""
    allowed = HOOK_CONTEXT_FIELDS.get(hook, frozenset())
    fields = {
        str(condition.get("field") or "")
        for rule in program.get("rules") or []
        for condition in (rule.get("conditions") or [rule])
    }
    unavailable = sorted(field for field in fields
                         if field.startswith("context_") and field not in allowed)
    if unavailable:
        raise ValueError(f"predicate context fields unavailable at {hook}: {unavailable}")


def missing_judge_inputs(program: Mapping[str, Any], context: Mapping[str, Any]) -> list[str]:
    """Distinguish an absent recorded judge from a legitimate empty value."""
    judge = context.get("judge_parsed") or {}
    required = {
        "context_judge_positive": "is_hallucination",
        "context_judge_confidence": "confidence",
        "context_judge_hallucination_reason": "hallucination_reason",
        "context_judge_delivered_result": "delivered_result",
    }
    fields = {str(condition.get("field") or "")
              for rule in program.get("rules") or []
              for condition in (rule.get("conditions") or [rule])}
    return sorted(field for field in fields if field in required and required[field] not in judge)
