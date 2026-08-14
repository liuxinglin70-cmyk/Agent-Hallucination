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

_MAX_EVENT_CONTENT = 4000
_MAX_PACKET_CHARS = 24000


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


def render_packet(packet: Mapping[str, Any]) -> str:
    """Render a bounded, provider-safe text view of the packet."""
    lines: list[str] = []
    question = packet.get("question")
    if question:
        lines.append(f"QUESTION: {_truncate(question, 2000)}")
    budget = _MAX_PACKET_CHARS
    for event in packet.get("events") or []:
        block = [
            f"EVENT {event['event_id']} (position={event['position']}, "
            f"role={event.get('role') or '?'})",
        ]
        content = event.get("content")
        if content not in (None, ""):
            block.append(_truncate(content, _MAX_EVENT_CONTENT))
        for interaction in event.get("tool_interactions") or []:
            args = _truncate(
                interaction.get("arguments"), 1500,
            ) if interaction.get("arguments") not in (None, "") else "{}"
            obs = interaction.get("observation")
            obs_text = (
                _truncate(obs, 1500) if obs not in (None, "") else "(none)"
            )
            block.append(
                f"  TOOL {interaction['interaction_id']} "
                f"name={interaction.get('tool_name')} "
                f"arguments={args}\n  -> observation: {obs_text}"
            )
        chunk = "\n".join(block)
        if len(chunk) > budget:
            chunk = chunk[:budget] + "\n…[packet truncated]"
            lines.append(chunk)
            break
        lines.append(chunk)
        budget -= len(chunk)
    return "\n".join(lines)
