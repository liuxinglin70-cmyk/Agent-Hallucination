"""Canonical packet, strict input firewall, and stage views.

The detector consumes exactly two record fields: ``question`` and
``history``. Anything else at the top level is rejected; gold answer fields
never enter or persist in a detector input.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

PACKET_SCHEMA = "detector_v2.packet.v1"

ALLOWED_RECORD_KEYS = frozenset({"question", "history"})

EVALUATOR_ONLY_FIELDS = frozenset({
    "is_hallucination",
    "hallucination_step",
    "hallucination_reason",
    "hallucination_category",
    "hallucination_subcategory",
    "true_answer",
    "explanation",
    "gold_label",
    "gold_steps",
    "gold_explanation",
})

FORBIDDEN_KEYS = EVALUATOR_ONLY_FIELDS | {
    "case_id", "source", "source_dir", "agent_type", "model_id",
    "question_source", "question_domain", "agent_answer",
}

_ANSWER_TOOL_HINTS = (
    "final_answer", "answer", "finish", "terminate", "submit",
    "respond", "conclude",
)

PACKET_FEATURE_NAMES = frozenset({
    "has_tool_calls",
    "has_retrieval_tool",
    "has_answer_tool",
    "has_numeric_content",
    "has_long_prose",
    "multi_turn_question",
    "question_has_numbers",
})

_JUDGE_VIEW_CHARS = 400_000
_JUDGE_EVENT_CHARS = 100_000
_JUDGE_TOOL_CHARS = 100_000

_DEFENSE_VIEW_CHARS = 200_000
_DEFENSE_EVENT_CHARS = 100_000
_DEFENSE_WINDOW = 2

_INDEX_VIEW_CHARS = 48_000
_INDEX_TAIL_EVENTS = 8
_INDEX_EVENT_PREVIEW = 300
_INDEX_TOOL_PREVIEW = 120


class PacketError(ValueError):
    """A raw record cannot safely enter the detector."""


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(row: Mapping[str, Any]) -> Any:
    for key in (
        "content", "message", "text", "thought", "response",
        "assistant_content",
    ):
        if key in row:
            return row[key]
    return None


def _question_text(value: Any) -> str:
    """Flatten a benchmark question into prompt text.

    AgentHallu stores single-turn questions as a string and multi-turn
    ones as a list of turns, each turn a list of strings. Turn numbering
    keeps the multi-turn structure visible to the judge.
    """
    if isinstance(value, str):
        return value
    if not isinstance(value, list) or not value:
        raise PacketError("question must be a non-empty string or turn list")
    turns = [
        " ".join(str(part).strip() for part in _as_list(turn))
        for turn in value
    ]
    if any(not turn for turn in turns):
        raise PacketError("question turns must be non-empty")
    if len(turns) == 1:
        return turns[0]
    return "\n".join(
        f"[turn {index}] {turn}" for index, turn in enumerate(turns, 1)
    )


def _tool_name(call: Any) -> str:
    if isinstance(call, str):
        return call
    if not isinstance(call, dict):
        return "unknown_tool"
    nested = call.get("function")
    if isinstance(nested, dict):
        return str(nested.get("name") or "unknown_tool")
    return str(
        call.get("name") or call.get("tool_name") or call.get("tool")
        or "unknown_tool"
    )


def _tool_arguments(call: Any) -> Any:
    if not isinstance(call, dict):
        return None
    nested = call.get("function")
    if isinstance(nested, dict) and "arguments" in nested:
        return nested["arguments"]
    for key in ("arguments", "args", "input", "parameters"):
        if key in call:
            return call[key]
    return None


def _forbidden_nested_keys(record: Mapping[str, Any]) -> set[str]:
    hits: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            hits.update(str(key) for key in value if str(key) in FORBIDDEN_KEYS)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                visit(nested)

    visit(record)
    return hits


def validate_record(record: Mapping[str, Any]) -> None:
    """Reject anything that is not ``{question, history}`` (fail-closed)."""
    if not isinstance(record, Mapping):
        raise PacketError("record must be an object")
    extra = sorted(set(record) - ALLOWED_RECORD_KEYS)
    if extra:
        raise PacketError(
            "detector input allows only {question, history}; "
            f"rejected top-level fields: {extra}"
        )
    hits = _forbidden_nested_keys(record)
    if hits:
        raise PacketError(
            "detector input must be label-free: " + ", ".join(sorted(hits))
        )
    build_packet(record)


def build_packet(record: Mapping[str, Any]) -> dict[str, Any]:
    """Compile ``{question, history}`` into the provider-safe packet."""
    if not isinstance(record, dict):
        raise PacketError("record must be an object")
    history = record.get("history")
    if not isinstance(history, list) or not history:
        raise PacketError("history must be a non-empty list")
    question = _question_text(record.get("question"))
    if not question.strip():
        raise PacketError("question must be a non-empty string")

    events: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(history, start=1):
        row = raw if isinstance(raw, dict) else {"content": raw}
        event_id = f"event_{ordinal:03d}"
        calls = _as_list(row.get("tool_calls"))
        responses = _as_list(row.get("tool_responses"))
        interactions: list[dict[str, Any]] = []
        for call_ordinal, call in enumerate(calls, start=1):
            interactions.append({
                "interaction_id": f"{event_id}_tool_{call_ordinal:02d}",
                "tool_name": _tool_name(call),
                "arguments": _tool_arguments(call),
                "observation": (
                    responses[call_ordinal - 1]
                    if call_ordinal <= len(responses) else None
                ),
            })
        events.append({
            "event_id": event_id,
            "position": ordinal,
            "source_step_id": row.get("step"),
            "role": row.get("role"),
            "content": _text(row),
            "tool_interactions": interactions,
        })

    packet = {
        "schema_version": PACKET_SCHEMA,
        "question": question,
        "events": events,
    }
    validate_packet(packet)
    return packet


def validate_packet(packet: Mapping[str, Any]) -> None:
    events = packet.get("events")
    if not isinstance(events, list) or not events:
        raise PacketError("packet events must be a non-empty list")
    ids: list[str] = []
    positions: list[int] = []
    for event in events:
        if not isinstance(event, dict):
            raise PacketError("packet events must be objects")
        ids.append(str(event.get("event_id") or ""))
        positions.append(int(event.get("position") or 0))
        parent = str(event.get("event_id") or "")
        for interaction in event.get("tool_interactions") or []:
            iid = str(interaction.get("interaction_id") or "")
            if not iid.startswith(parent + "_tool_"):
                raise PacketError(f"bad interaction id {iid!r}")
            ids.append(iid)
    if positions != list(range(1, len(events) + 1)):
        raise PacketError("packet positions must be 1..N")
    if len(ids) != len(set(ids)):
        raise PacketError("packet ids must be unique")


def delivered_result(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Mechanical extraction of the delivered-result anchors.

    This is deliberately dumb: it finds the last prose event and the last
    answer-like tool call. Semantic judgement belongs to the judge.
    """
    events = list(packet.get("events") or [])
    last_prose = None
    for event in reversed(events):
        content = event.get("content")
        if content not in (None, ""):
            last_prose = event
            break
    last_answer_tool = None
    for event in reversed(events):
        for interaction in event.get("tool_interactions") or []:
            name = str(interaction.get("tool_name") or "").lower()
            if any(hint in name for hint in _ANSWER_TOOL_HINTS):
                last_answer_tool = {
                    "event_id": event.get("event_id"),
                    "position": event.get("position"),
                    "tool_name": interaction.get("tool_name"),
                    "arguments": interaction.get("arguments"),
                    "observation": interaction.get("observation"),
                }
                break
        if last_answer_tool is not None:
            break
    return {
        "last_prose_event_id": (
            last_prose.get("event_id") if last_prose else None
        ),
        "last_answer_tool_call": last_answer_tool,
        "has_explicit_final_answer": last_answer_tool is not None,
    }


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "…[truncated]"


def _render_event(
    event: Mapping[str, Any],
    *,
    event_cap: int,
    tool_cap: int,
) -> tuple[str, bool]:
    lines = [
        f"EVENT {event['event_id']} (position={event['position']}, "
        f"role={event.get('role') or '?'})",
    ]
    truncated = False
    content = event.get("content")
    if content not in (None, ""):
        text = str(content)
        if len(text) > event_cap:
            text = text[:event_cap] + "…[truncated]"
            truncated = True
        lines.append(text)
    for interaction in event.get("tool_interactions") or []:
        args = interaction.get("arguments")
        args_text = _truncate(args, tool_cap) if args not in (None, "") else "{}"
        if str(args or "") and len(str(args)) > tool_cap:
            truncated = True
        obs = interaction.get("observation")
        obs_text = _truncate(obs, tool_cap) if obs not in (None, "") else "(none)"
        if str(obs or "") and len(str(obs)) > tool_cap:
            truncated = True
        lines.append(
            f"  TOOL {interaction['interaction_id']} "
            f"name={interaction.get('tool_name')} "
            f"arguments={args_text}\n  -> observation: {obs_text}"
        )
    return "\n".join(lines), truncated


def _view_meta() -> dict[str, Any]:
    return {
        "rendered_event_ids": [],
        "truncated_event_ids": [],
        "packet_truncated": False,
    }


def render_full_view(packet: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Full-fidelity view for the judge."""
    lines: list[str] = []
    meta = _view_meta()
    question = packet.get("question")
    if question:
        lines.append(f"QUESTION: {_truncate(question, 4000)}")
    budget = _JUDGE_VIEW_CHARS
    for event in packet.get("events") or []:
        chunk, truncated = _render_event(
            event, event_cap=_JUDGE_EVENT_CHARS, tool_cap=_JUDGE_TOOL_CHARS,
        )
        if len(chunk) > budget:
            lines.append(chunk[:budget] + "\n…[packet truncated]")
            meta["packet_truncated"] = True
            break
        lines.append(chunk)
        meta["rendered_event_ids"].append(event["event_id"])
        if truncated:
            meta["truncated_event_ids"].append(event["event_id"])
        budget -= len(chunk)
    return "\n".join(lines), meta


def render_index_tail_view(
    packet: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Degraded judge fallback: complete index + chronological tail."""
    events = list(packet.get("events") or [])
    lines: list[str] = []
    meta = _view_meta()
    question = packet.get("question")
    if question:
        lines.append(f"QUESTION: {_truncate(question, 4000)}")
    lines.append("\nEVENT INDEX:")
    budget = _INDEX_VIEW_CHARS
    for event in events:
        content = event.get("content")
        content_text = str(content) if content not in (None, "") else ""
        tools = []
        for interaction in event.get("tool_interactions") or []:
            args = interaction.get("arguments")
            args_preview = (
                _truncate(args, _INDEX_TOOL_PREVIEW)
                if args not in (None, "") else "{}"
            )
            obs = interaction.get("observation")
            obs_len = len(str(obs)) if obs not in (None, "") else 0
            tools.append(
                f"tool={interaction.get('tool_name')} args={args_preview} "
                f"obs_chars={obs_len}"
            )
        line = (
            f"{event['event_id']} (position={event['position']}, "
            f"role={event.get('role') or '?'}, content_chars={len(content_text)}, "
            f"preview={_truncate(content_text, _INDEX_EVENT_PREVIEW)!r}) "
            f"tools=[{'; '.join(tools)}]"
        )
        if len(line) > budget:
            meta["packet_truncated"] = True
            break
        lines.append(line)
        budget -= len(line)
    lines.append("\nTAIL EVENTS (full detail, chronological end):")
    for event in events[-_INDEX_TAIL_EVENTS:]:
        chunk, truncated = _render_event(
            event, event_cap=_JUDGE_EVENT_CHARS, tool_cap=_JUDGE_TOOL_CHARS,
        )
        if len(chunk) > budget:
            lines.append(chunk[:budget] + "\n…[packet truncated]")
            meta["packet_truncated"] = True
            break
        lines.append(chunk)
        meta["rendered_event_ids"].append(event["event_id"])
        if truncated:
            meta["truncated_event_ids"].append(event["event_id"])
        budget -= len(chunk)
    return "\n".join(lines), meta


def render_judge_view(packet: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Judge view: full fidelity, degrading to index+tail beyond budget."""
    text, meta = render_full_view(packet)
    if not meta["packet_truncated"]:
        meta["degraded"] = False
        return text, meta
    degraded_text, degraded_meta = render_index_tail_view(packet)
    degraded_meta["degraded"] = True
    degraded_meta["full_render_chars"] = len(text)
    return degraded_text, degraded_meta


def _render_delivered_block(packet: Mapping[str, Any]) -> str:
    result = delivered_result(packet)
    lines = ["\nDELIVERED RESULT ANCHORS:"]
    lines.append(f"last_prose_event_id={result['last_prose_event_id']}")
    lines.append(f"has_explicit_final_answer={result['has_explicit_final_answer']}")
    tool = result["last_answer_tool_call"]
    if tool:
        lines.append(
            f"last_answer_tool_call={tool['event_id']} name={tool['tool_name']} "
            f"arguments={_truncate(tool.get('arguments'), 20000)}"
        )
        lines.append(
            f"last_answer_observation={_truncate(tool.get('observation'), 20000)}"
        )
    return "\n".join(lines)


def render_defense_view(
    packet: Mapping[str, Any],
    step: int,
    *,
    window: int = _DEFENSE_WINDOW,
    evidence_refs: Sequence[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Defense view: alleged step + cited evidence + neighbours + anchors."""
    events = list(packet.get("events") or [])
    cited = [event for event in events if event.get("position") == step]
    cited_ids = {str(event.get("event_id") or "") for event in cited}
    ref_ids = {
        str(ref).rsplit("_tool_", 1)[0]
        for ref in (evidence_refs or ())
        if str(ref)
    }
    cited_ids.update(
        event_id for event_id in ref_ids
        if any(e.get("event_id") == event_id for e in events)
    )
    expanded = set(cited_ids)
    for event in cited:
        pos = int(event.get("position") or 0)
        expanded.update(
            str(other.get("event_id") or "") for other in events
            if abs(int(other.get("position") or 0) - pos) <= window
        )
    lines: list[str] = []
    meta = _view_meta()
    question = packet.get("question")
    if question:
        lines.append(f"QUESTION: {_truncate(question, 4000)}")
    budget = _DEFENSE_VIEW_CHARS
    for event in events:
        event_id = str(event.get("event_id") or "")
        if event_id not in expanded:
            continue
        tag = "CITED" if event_id in cited_ids else "WINDOW"
        chunk, truncated = _render_event(
            event, event_cap=_DEFENSE_EVENT_CHARS, tool_cap=_DEFENSE_EVENT_CHARS,
        )
        if tag == "CITED" and truncated:
            meta["packet_truncated"] = True
        if len(chunk) > budget:
            lines.append(chunk[:budget] + "\n…[packet truncated]")
            meta["packet_truncated"] = True
            break
        lines.append(f"[{tag}] " + chunk)
        meta["rendered_event_ids"].append(event["event_id"])
        if truncated:
            meta["truncated_event_ids"].append(event["event_id"])
        budget -= len(chunk)
    lines.append(_render_delivered_block(packet))
    omitted = [
        str(event.get("event_id") or "") for event in events
        if str(event.get("event_id") or "") not in expanded
    ]
    if omitted:
        shown = omitted[:50]
        suffix = f" …(+{len(omitted) - len(shown)})" if len(omitted) > 50 else ""
        lines.append(
            "\nEVENTS OMITTED FROM THIS VIEW (present in trajectory, not "
            "shown here): " + ", ".join(shown) + suffix
        )
    meta["omitted_event_ids"] = omitted
    return "\n".join(lines), meta


def packet_features(packet: Mapping[str, Any]) -> set[str]:
    """Closed vocabulary of observable features for intervention routing."""
    events = list(packet.get("events") or [])
    question = str(packet.get("question") or "")
    multi_turn = "[turn 2]" in question
    tool_count = 0
    has_retrieval = False
    has_answer_tool = False
    has_numeric = False
    max_content_chars = 0
    for event in events:
        content = str(event.get("content") or "")
        max_content_chars = max(max_content_chars, len(content))
        if any(char.isdigit() for char in content):
            has_numeric = True
        for interaction in event.get("tool_interactions") or []:
            tool_count += 1
            name = str(interaction.get("tool_name") or "").lower()
            if any(word in name for word in ("search", "retriev", "fetch", "browse", "lookup")):
                has_retrieval = True
            if any(hint in name for hint in _ANSWER_TOOL_HINTS):
                has_answer_tool = True
    features: set[str] = set()
    if tool_count > 0:
        features.add("has_tool_calls")
    if has_retrieval:
        features.add("has_retrieval_tool")
    if has_answer_tool:
        features.add("has_answer_tool")
    if has_numeric:
        features.add("has_numeric_content")
    if max_content_chars > 400:
        features.add("has_long_prose")
    if multi_turn:
        features.add("multi_turn_question")
    if any(char.isdigit() for char in question):
        features.add("question_has_numbers")
    return features


__all__ = [
    "ALLOWED_RECORD_KEYS",
    "EVALUATOR_ONLY_FIELDS",
    "FORBIDDEN_KEYS",
    "PACKET_FEATURE_NAMES",
    "PACKET_SCHEMA",
    "PacketError",
    "build_packet",
    "delivered_result",
    "packet_features",
    "render_defense_view",
    "render_full_view",
    "render_judge_view",
    "validate_packet",
    "validate_record",
]
