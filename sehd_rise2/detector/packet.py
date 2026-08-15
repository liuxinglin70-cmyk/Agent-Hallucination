"""Canonical trajectory packet and the label-free input firewall.

The packet is the only representation the provider stages ever see. Gold
labels and dataset identity never enter it: ``EVALUATOR_ONLY_FIELDS`` are
the evaluator's business, and ``FORBIDDEN_PACKET_KEYS`` additionally strip
dataset identity. The ordinal history position is the primary key, so
repeated or absent source steps cannot corrupt evidence references.
"""

from __future__ import annotations

from typing import Any, Mapping

PACKET_SCHEMA = "sehd2.ocse_packet.v1"


class PacketError(ValueError):
    """A raw record cannot safely enter the detection runtime."""


EVALUATOR_ONLY_FIELDS = frozenset({
    "is_hallucination",
    "hallucination_step",
    "hallucination_reason",
    "hallucination_category",
    "hallucination_subcategory",
    "gold_label",
    "gold_steps",
    "gold_explanation",
    "outer_feedback",
})

FORBIDDEN_PACKET_KEYS = EVALUATOR_ONLY_FIELDS | {
    "case_id",
    "source",
    "source_dir",
    "agent_type",
    "model_id",
}

_MAX_EVENT_CONTENT = 16000
_MAX_TOOL_TEXT = 8000
_MAX_QUESTION_CHARS = 4000
_MAX_PACKET_CHARS = 96000

# Two-phase evidence-access budgets.
# Phase 1 (census): a complete event INDEX plus the chronological tail at
# full detail. Every event is nameable (id/role/preview/length/tool lines),
# and the tail carries the final answer — the most hallucination-relevant
# part — so it is rendered at full detail first.
INDEX_VIEW_CHARS = 48000
INDEX_TAIL_EVENTS = 8
INDEX_EVENT_PREVIEW = 300
INDEX_TOOL_PREVIEW = 120
# Phase 2 (verification/defense): ONLY the events cited by the candidates
# (locus + evidence refs) plus their chronological neighbours, each at a
# large per-event budget. A cited event that still cannot fit marks the
# view truncated: the court fails closed on an incomplete evidence view.
# The ceilings are RESOURCE budgets for full-fidelity evidence rendering,
# not decision thresholds: the court stays fail-closed beyond them, and
# only the rare cases that cite very large tool observations pay the
# larger prompts.
EVIDENCE_VIEW_CHARS = 400_000
EVIDENCE_EVENT_CHARS = 200_000
EVIDENCE_WINDOW = 1


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


def _forbidden_key_hits(packet: Mapping[str, Any]) -> set[str]:
    """Audit only packet control layers, never opaque tool payload content."""
    hits = {str(key) for key in packet if str(key) in FORBIDDEN_PACKET_KEYS}
    for event in packet.get("events") or []:
        if not isinstance(event, dict):
            continue
        hits.update(str(key) for key in event if str(key) in FORBIDDEN_PACKET_KEYS)
        for interaction in event.get("tool_interactions") or []:
            if isinstance(interaction, dict):
                hits.update(
                    str(key) for key in interaction
                    if str(key) in FORBIDDEN_PACKET_KEYS
                )
    return hits


def build_packet(record: Mapping[str, Any]) -> dict[str, Any]:
    """Compile a provider-safe packet from one complete raw record."""
    if not isinstance(record, dict):
        raise PacketError("record must be an object")
    history = record.get("history")
    if not isinstance(history, list) or not history:
        raise PacketError("history must be a non-empty list")

    events: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(history, start=1):
        row = raw if isinstance(raw, dict) else {"content": raw}
        event_id = f"event_{ordinal:03d}"
        calls = _as_list(row.get("tool_calls"))
        responses = _as_list(row.get("tool_responses"))
        interactions = []
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
        "question": record.get("question"),
        "events": events,
    }
    checks = validate_packet(packet)
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise PacketError("invalid packet: " + ",".join(failed))
    return packet


def validate_packet(packet: Mapping[str, Any]) -> dict[str, bool]:
    events = packet.get("events")
    if not isinstance(events, list) or not events:
        return {
            "nonempty_events": False,
            "ordinal_positions": False,
            "event_ids_unique": False,
            "interaction_ids_unique": False,
            "interaction_parent_valid": False,
            "provider_firewall": False,
        }
    event_ids: list[str] = []
    interaction_ids: list[str] = []
    parent_valid = True
    positions: list[int] = []
    for event in events:
        if not isinstance(event, dict):
            parent_valid = False
            continue
        event_id = event.get("event_id")
        position = event.get("position")
        if not isinstance(event_id, str) or not event_id or not isinstance(position, int):
            parent_valid = False
            continue
        event_ids.append(event_id)
        positions.append(position)
        for interaction in event.get("tool_interactions") or []:
            if not isinstance(interaction, dict):
                parent_valid = False
                continue
            interaction_id = interaction.get("interaction_id")
            if (
                not isinstance(interaction_id, str)
                or not interaction_id
                or not interaction_id.startswith(event_id + "_tool_")
            ):
                parent_valid = False
                continue
            interaction_ids.append(interaction_id)
    all_ids = event_ids + interaction_ids
    return {
        "nonempty_events": bool(event_ids),
        "ordinal_positions": positions == list(range(1, len(events) + 1)),
        "event_ids_unique": len(event_ids) == len(events)
        and len(event_ids) == len(set(event_ids)),
        "interaction_ids_unique": len(all_ids) == len(set(all_ids)),
        "interaction_parent_valid": parent_valid,
        "provider_firewall": not _forbidden_key_hits(packet),
    }


def validate_record(record: Mapping[str, Any]) -> None:
    """Validate one label-free raw trajectory before any provider call."""
    if not isinstance(record, dict):
        raise TypeError("record must be an object")
    leaked: set[str] = set()
    for scope in (record,):
        leaked.update(
            str(key) for key in scope if str(key) in EVALUATOR_ONLY_FIELDS
        )
    for row in record.get("history") or ():
        if isinstance(row, Mapping):
            leaked.update(
                str(key) for key in row
                if str(key) in EVALUATOR_ONLY_FIELDS
            )
    if leaked:
        raise ValueError(
            "detector input must be label-free: " + ", ".join(sorted(leaked))
        )
    build_packet(record)


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "…[truncated]"


def _render_event(
    event: Mapping[str, Any], *, event_cap: int, tool_cap: int,
) -> tuple[str, bool]:
    """Render one event at full detail; returns (block, field_truncated)."""
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


def _fresh_view_meta() -> dict[str, Any]:
    return {
        "rendered_event_ids": [],
        "truncated_event_ids": [],
        "packet_truncated": False,
    }


def render_full_view(packet: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Bounded full-detail render; returns (text, meta)."""
    lines: list[str] = []
    meta = _fresh_view_meta()
    question = packet.get("question")
    if question:
        lines.append(f"QUESTION: {_truncate(question, _MAX_QUESTION_CHARS)}")
    budget = _MAX_PACKET_CHARS
    for event in packet.get("events") or []:
        chunk, truncated = _render_event(
            event, event_cap=_MAX_EVENT_CONTENT, tool_cap=_MAX_TOOL_TEXT,
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


def render_index_view(packet: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Census view: complete event index + chronological tail at full detail.

    Every event gets one index line (id, position, role, content preview
    and length, tool names with argument previews and observation lengths)
    so the census can name ANY event as locus/evidence. The last
    ``INDEX_TAIL_EVENTS`` events are additionally rendered at full detail:
    the tail carries the final answer, the most hallucination-relevant
    part, and must be visible even when the middle is only indexed.
    """
    events = list(packet.get("events") or [])
    lines: list[str] = []
    meta = _fresh_view_meta()
    question = packet.get("question")
    if question:
        lines.append(f"QUESTION: {_truncate(question, _MAX_QUESTION_CHARS)}")
    lines.append("\nEVENT INDEX:")
    budget = INDEX_VIEW_CHARS
    index_hit_budget = False
    for event in events:
        content = event.get("content")
        content_text = str(content) if content not in (None, "") else ""
        preview = (
            _truncate(content_text, INDEX_EVENT_PREVIEW)
            if content_text else "(no content)"
        )
        tools: list[str] = []
        for interaction in event.get("tool_interactions") or []:
            args = interaction.get("arguments")
            args_preview = (
                _truncate(args, INDEX_TOOL_PREVIEW)
                if args not in (None, "") else "{}"
            )
            obs = interaction.get("observation")
            obs_len = len(str(obs)) if obs not in (None, "") else 0
            tools.append(
                f"tool={interaction.get('tool_name')} args={args_preview} "
                f"obs_chars={obs_len}"
            )
        tool_txt = (" tools=[" + "; ".join(tools) + "]") if tools else ""
        line = (
            f"{event['event_id']} (position={event['position']}, "
            f"role={event.get('role') or '?'}, content_chars={len(content_text)}, "
            f"preview={preview!r}){tool_txt}"
        )
        if len(line) > budget:
            index_hit_budget = True
            break
        lines.append(line)
        budget -= len(line)
    if index_hit_budget:
        lines.append("…[event index truncated]")
        meta["packet_truncated"] = True
    lines.append("\nTAIL EVENTS (full detail, chronological end):")
    for event in events[-INDEX_TAIL_EVENTS:]:
        chunk, truncated = _render_event(
            event, event_cap=_MAX_EVENT_CONTENT, tool_cap=_MAX_TOOL_TEXT,
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


def render_evidence_view(
    packet: Mapping[str, Any], event_ids: Sequence[str],
    *, window: int = EVIDENCE_WINDOW,
) -> tuple[str, dict[str, Any]]:
    """Verification/defense view: cited events at full detail + neighbours.

    Only the events named by candidate locus/evidence refs (interaction
    ids resolve to their parent event) plus ``window`` chronological
    neighbours are rendered, each at the large ``EVIDENCE_EVENT_CHARS``
    budget. If a CITED event still cannot fit fully (budget cut or field
    truncation), the view is marked ``packet_truncated`` and the court
    must fail closed: a verdict built on an incomplete evidence view is
    not authoritative. Window-only events may truncate without failing
    the view (they are context, not conviction basis).
    """
    events = list(packet.get("events") or [])
    by_id = {str(e.get("event_id")): e for e in events}
    cited: set[str] = set()
    for event_id in event_ids:
        eid = str(event_id)
        parent = eid.rsplit("_tool_", 1)[0] if "_tool_" in eid else eid
        if parent in by_id:
            cited.add(parent)
    expanded: set[str] = set(cited)
    for event in events:
        if event["event_id"] not in cited:
            continue
        pos = int(event.get("position") or 0)
        for other in events:
            if abs(int(other.get("position") or 0) - pos) <= window:
                expanded.add(other["event_id"])
    lines: list[str] = []
    meta = _fresh_view_meta()
    question = packet.get("question")
    if question:
        lines.append(f"QUESTION: {_truncate(question, _MAX_QUESTION_CHARS)}")
    budget = EVIDENCE_VIEW_CHARS
    for event in events:
        if event["event_id"] not in expanded:
            continue
        tag = "CITED" if event["event_id"] in cited else "WINDOW"
        chunk, truncated = _render_event(
            event, event_cap=EVIDENCE_EVENT_CHARS, tool_cap=EVIDENCE_EVENT_CHARS,
        )
        if tag == "CITED" and truncated:
            # A cited event that could not be rendered in full means the
            # conviction basis is incomplete: fail closed upstream.
            meta["packet_truncated"] = True
        if len(chunk) > budget:
            lines.append(chunk[:budget] + "\n…[packet truncated]")
            meta["packet_truncated"] = True
            break
        block = f"[{tag}] " + chunk
        lines.append(block)
        meta["rendered_event_ids"].append(event["event_id"])
        if truncated:
            meta["truncated_event_ids"].append(event["event_id"])
        budget -= len(block)
    meta["omitted_event_ids"] = [
        e["event_id"] for e in events if e["event_id"] not in expanded
    ]
    return "\n".join(lines), meta


def render_packet(packet: Mapping[str, Any]) -> str:
    """Render a bounded, provider-safe text view of the packet."""
    text, _meta = render_full_view(packet)
    return text
