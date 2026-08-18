"""Deterministic predicates for detector_v2 intervention hooks.

These are the M1 predicates from the V3 self-evolution design. They are
deliberately mechanical: no LLM call, no case ids, no hallucination verdicts.
An ``evidence_check`` intervention may only append entries to ``proof.evidence``;
these predicates are the only executable evidence checkers until a new runner
is registered here.

Every predicate returns ``{"predicate", "verdict", "detail"}`` where ``verdict``
is from the predicate's own closed vocabulary.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from detector_v2.packet import delivered_result as packet_delivered_result

PREDICATE_VERDICTS: dict[str, frozenset[str]] = {
    "required_action_present": frozenset({
        "satisfied", "violated", "not_applicable",
    }),
    "verbatim_fidelity": frozenset({
        "satisfied", "violated", "not_applicable",
    }),
    "evidence_refs_resolve": frozenset({
        "resolved", "unresolved", "not_applicable",
    }),
    "contract_coverage": frozenset({
        "covered", "uncovered", "not_applicable",
    }),
    "delivered_anchor_consistency": frozenset({
        "consistent", "inconsistent", "insufficient",
    }),
}

PREDICATES = frozenset(PREDICATE_VERDICTS)

_ACTION_MARKERS = (
    "must use", "required to use", "must call", "must invoke",
    "required to call", "should use", "has to use", "shall use",
    "use the", "call the", "run the", "query the", "execute the",
)

_VERBATIM_MARKERS = (
    "verbatim", "exactly", "exact text", "exact phrase", "literal",
    "word-for-word", "word for word", "must say", "must write",
    "must output", "must return", "must reply", "has to say",
    "required to say", "required to write",
)

_QUOTE_RE = re.compile(r"[\"'\u201c]([^\"'\u201d\n]{1,400})[\"'\u201d]")

_EXACT_VALUE_RE = re.compile(
    r"\b(?:exactly|exact value|must equal|must be|equals?)\s+"
    r"([+-]?\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _flatten_question(value: Any, parts: list[str]) -> None:
    if isinstance(value, str):
        parts.append(value)
    elif isinstance(value, Mapping):
        parts.append(str(value.get("content") or value.get("text") or ""))
    elif isinstance(value, (list, tuple)):
        for item in value:
            _flatten_question(item, parts)


def _question_text(packet: Mapping[str, Any]) -> str:
    question = packet.get("question")
    if isinstance(question, str):
        return question
    if isinstance(question, list):
        parts: list[str] = []
        _flatten_question(question, parts)
        return "\n".join(parts)
    return str(question or "")


def _event_texts(packet: Mapping[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in packet.get("events") or []:
        chunks = [str(event.get("content") or "")]
        for interaction in event.get("tool_interactions") or []:
            chunks.extend((
                str(interaction.get("tool_name") or ""),
                str(interaction.get("arguments") or ""),
                str(interaction.get("observation") or ""),
            ))
        rows.append({
            "event_id": str(event.get("event_id") or ""),
            "text": "\n".join(chunks),
        })
    return rows


def _tool_names(packet: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for event in packet.get("events") or []:
        for interaction in event.get("tool_interactions") or []:
            name = str(interaction.get("tool_name") or "").lower()
            if name:
                names.add(name)
    return names


def _detect_required_actions(question: str) -> set[str]:
    """Conservative extraction of explicitly required tool actions."""
    actions: set[str] = set()
    lowered = question.lower()
    for marker in _ACTION_MARKERS:
        start = 0
        while True:
            pos = lowered.find(marker, start)
            if pos < 0:
                break
            tail = question[pos + len(marker): pos + len(marker) + 80]
            match = re.match(
                r"[\s:\-]*([A-Za-z_][A-Za-z0-9_ /.-]*?)(?=\s+(?:tool|"
                r"function|api|command)|\s+(?:to|for|before|when|while|"
                r"with|and|or|from|by|in|on|\.|,|$))",
                tail,
            )
            start = pos + max(1, len(marker))
            if not match:
                continue
            name = match.group(1).strip(" .-")
            name = re.sub(r"\s+(tool|function|api|command)$", "", name, flags=re.I)
            name = name.lower().replace(" the ", " ")
            if name and not any(ch in name for ch in ("?", "!", ";")):
                actions.add(name.strip())
    # Explicit structured spelling in natural-language questions.
    for match in re.finditer(
        r"required_action\s*[:=]\s*[\"']([^\"']+)[\"']", question, re.I,
    ):
        actions.add(match.group(1).strip().lower())
    return {name for name in actions if name}


def _detect_verbatim_phrases(question: str) -> list[str]:
    phrases: list[str] = []
    for match in _QUOTE_RE.finditer(question):
        prefix = question[max(0, match.start() - 90): match.start()].lower()
        if any(marker in prefix for marker in _VERBATIM_MARKERS):
            phrase = _clean(match.group(1))
            if len(phrase) >= 2:
                phrases.append(phrase)
    return phrases


def _detect_contract_kinds(question: str) -> set[str]:
    kinds: set[str] = set()
    if _detect_required_actions(question):
        kinds.add("required_action")
    if _detect_verbatim_phrases(question):
        kinds.add("verbatim_content")
    if _EXACT_VALUE_RE.search(question):
        kinds.add("exact_value")
    lowered = question.lower()
    if any(
        marker in lowered
        for marker in ("json format", "in the format", "exact form", "as yaml")
    ):
        kinds.add("exact_form")
    return kinds


def _delivered_texts(packet: Mapping[str, Any]) -> list[str]:
    anchors = packet_delivered_result(packet)
    rows: list[str] = []
    texts = _event_texts(packet)
    for row in texts:
        if row["event_id"] == anchors.get("last_prose_event_id"):
            rows.append(row["text"])
    tool = anchors.get("last_answer_tool_call") or {}
    if tool:
        rows.append(str(tool.get("arguments") or ""))
        rows.append(str(tool.get("observation") or ""))
    return [_clean(row) for row in rows if _clean(row)]


def _movement_requirements(texts: Iterable[str]) -> list[str]:
    patterns = (
        r"(?:swing by|head over to|go to|drive to|walk to|visit|stop by|"
        r"drop by|proceed to|travel to)\s+(?:the\s+)?"
        r"([A-Za-z0-9_ .'-]+?)"
        r"(?=\s+(?:and|or|to|for|before|while|with|when|,|\.)|[.?]|$)"
    )
    found: list[str] = []
    for text in texts:
        for match in re.finditer(patterns, text, re.IGNORECASE):
            target = " ".join(match.group(1).split())
            if target and target not in found:
                found.append(target.lower())
    return found


def _permission_pending(packet: Mapping[str, Any]) -> bool:
    events = list(packet.get("events") or [])
    for event in events:
        text = str(event.get("content") or "").lower()
        if "?" in text and any(
            phrase in text
            for phrase in (
                "would you like me to", "should i", "do you want me to",
                "shall i", "may i",
            )
        ):
            return True
    return False


def predicate_required_action_present(
    packet: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    question = _question_text(packet)
    required = _detect_required_actions(question)
    names = _tool_names(packet)
    missing = sorted(
        action for action in required
        if not any(action in name for name in names)
    )

    # Conservative completion signals from explicit task contracts. These are
    # deliberately narrow: only movement actions and a pending-permission
    # engine-start are considered mechanically checkable.
    contracts = [
        str(item.get("requirement") or "")
        for item in context.get("contracts") or ()
        if isinstance(item, Mapping)
        and str(item.get("kind") or "") == "required_action"
    ]
    contract_texts = [question, *contracts]
    movements = _movement_requirements(contract_texts)
    movement_violated = []
    movement_tools = {
        "cd", "chdir", "navigate", "goto", "go", "travel", "drive",
        "move", "visit", "head", "swing", "walk",
    }
    for target in movements:
        satisfied = False
        tokens = {
            token for token in target.split()
            if token not in {"the", "a", "an", "to", "and", "of", "in", "on"}
        }
        for event in packet.get("events") or []:
            for interaction in event.get("tool_interactions") or []:
                name = str(interaction.get("tool_name") or "").lower()
                args = str(interaction.get("arguments") or "").lower()
                if any(verb in name for verb in movement_tools) and any(
                    token in args or token in name for token in tokens
                ):
                    satisfied = True
        if not satisfied:
            movement_violated.append(target)

    start_pending = False
    engine_requested = any(
        "start" in text.lower() and (
            "engine" in text.lower() or "ignition" in text.lower()
        )
        for text in contract_texts
    )
    if engine_requested and _permission_pending(packet):
        start_pending = True

    if movement_violated:
        return {
            "predicate": "required_action_present",
            "verdict": "violated",
            "detail": f"movement action(s) never completed: {movement_violated}",
        }
    if start_pending:
        return {
            "predicate": "required_action_present",
            "verdict": "violated",
            "detail": "engine start was requested but trajectory ends asking permission",
        }
    if missing:
        return {
            "predicate": "required_action_present",
            "verdict": "violated",
            "detail": f"required actions missing from tool calls: {missing}",
        }
    if not required and contracts:
        return {
            "predicate": "required_action_present",
            "verdict": "not_applicable",
            "detail": "required_action contract is not covered by a mechanical parser",
        }
    if not required and not contracts:
        return {
            "predicate": "required_action_present",
            "verdict": "not_applicable",
            "detail": "no explicit required_action found in question or contracts",
        }
    return {
        "predicate": "required_action_present",
        "verdict": "satisfied",
        "detail": "explicit required actions are present or completed",
    }


def predicate_verbatim_fidelity(
    packet: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    phrases = _detect_verbatim_phrases(_question_text(packet))
    if not phrases:
        return {
            "predicate": "verbatim_fidelity",
            "verdict": "not_applicable",
            "detail": "no explicit verbatim_content contract found",
        }
    delivered = " ".join(_delivered_texts(packet))
    missing = [phrase for phrase in phrases if phrase not in delivered]
    if not missing:
        return {
            "predicate": "verbatim_fidelity",
            "verdict": "satisfied",
            "detail": f"all {len(phrases)} verbatim phrases delivered literally",
        }
    return {
        "predicate": "verbatim_fidelity",
        "verdict": "violated",
        "detail": f"verbatim phrase(s) missing from delivered text: {missing[:3]}",
    }


def _packet_ids(packet: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for event in packet.get("events") or []:
        ids.add(str(event.get("event_id") or ""))
        for interaction in event.get("tool_interactions") or []:
            ids.add(str(interaction.get("interaction_id") or ""))
    return ids


def predicate_evidence_refs_resolve(
    packet: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    refs = context.get("evidence_refs") or []
    refs = [str(ref) for ref in refs if str(ref)]
    if not refs:
        return {
            "predicate": "evidence_refs_resolve",
            "verdict": "not_applicable",
            "detail": "no evidence refs supplied",
        }
    ids = _packet_ids(packet)
    unknown = [ref for ref in refs if ref not in ids]
    if unknown:
        return {
            "predicate": "evidence_refs_resolve",
            "verdict": "unresolved",
            "detail": f"evidence refs not in packet: {unknown[:5]}",
        }
    quote = _clean(context.get("quote") or context.get("root_cause_quote") or "")
    if quote:
        texts = _event_texts(packet)
        ref_prefixes = {ref.rsplit("_tool_", 1)[0] for ref in refs}
        located = any(
            row["event_id"] in ref_prefixes and quote in row["text"]
            for row in texts
        )
        if not located:
            return {
                "predicate": "evidence_refs_resolve",
                "verdict": "unresolved",
                "detail": "refs exist but the cited quote cannot be located",
            }
    return {
        "predicate": "evidence_refs_resolve",
        "verdict": "resolved",
        "detail": f"all {len(refs)} evidence refs resolve to packet ids",
    }


def predicate_contract_coverage(
    packet: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    detected = _detect_contract_kinds(_question_text(packet))
    if not detected:
        return {
            "predicate": "contract_coverage",
            "verdict": "not_applicable",
            "detail": "no explicit mechanical contract markers detected",
        }
    contracts = context.get("contracts") or []
    covered = {
        str(item.get("kind") or "")
        for item in contracts
        if isinstance(item, Mapping) and item.get("kind")
    }
    missing = sorted(detected - covered)
    if not missing:
        return {
            "predicate": "contract_coverage",
            "verdict": "covered",
            "detail": f"judge contracts cover detected kinds: {sorted(detected)}",
        }
    return {
        "predicate": "contract_coverage",
        "verdict": "uncovered",
        "detail": f"judge contracts omit detected kinds: {missing}",
    }


def predicate_delivered_anchor_consistency(
    packet: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    delivered = _clean(context.get("delivered_result") or "")
    if not delivered:
        return {
            "predicate": "delivered_anchor_consistency",
            "verdict": "insufficient",
            "detail": "judge delivered_result is empty",
        }
    anchors = _delivered_texts(packet)
    if not anchors:
        return {
            "predicate": "delivered_anchor_consistency",
            "verdict": "insufficient",
            "detail": "no mechanical delivery anchors in packet",
        }
    consistent = any(
        delivered == anchor
        or (len(delivered) >= 6 and anchor in delivered)
        for anchor in anchors
    )
    if consistent:
        return {
            "predicate": "delivered_anchor_consistency",
            "verdict": "consistent",
            "detail": "judge delivered_result matches a mechanical delivery anchor",
        }
    return {
        "predicate": "delivered_anchor_consistency",
        "verdict": "inconsistent",
        "detail": "judge delivered_result does not match last prose/answer-tool anchors",
    }


_RUNNERS: dict[str, Any] = {
    "required_action_present": predicate_required_action_present,
    "verbatim_fidelity": predicate_verbatim_fidelity,
    "evidence_refs_resolve": predicate_evidence_refs_resolve,
    "contract_coverage": predicate_contract_coverage,
    "delivered_anchor_consistency": predicate_delivered_anchor_consistency,
}


def run_predicate(
    name: str,
    packet: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one deterministic predicate and enforce its verdict vocabulary."""
    runner = _RUNNERS.get(name)
    if runner is None:
        raise ValueError(f"predicate {name!r} has no deterministic executor")
    result = dict(runner(packet, dict(context or {})))
    verdict = str(result.get("verdict") or "")
    if verdict not in PREDICATE_VERDICTS[name]:
        raise ValueError(
            f"predicate {name} returned verdict {verdict!r} outside its vocabulary"
        )
    result["runner"] = "deterministic"
    return result


__all__ = [
    "PREDICATE_VERDICTS",
    "PREDICATES",
    "run_predicate",
]
