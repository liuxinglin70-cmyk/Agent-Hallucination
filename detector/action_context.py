"""Task-scoped prefixes and only the event order established by the log."""

from copy import deepcopy
from typing import Any, Mapping

from detector.packet import validate_packet
from detector.semantic_trace import task_sources


def forecast_prefix(packet: Mapping[str, Any], position: int) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_packet(packet)
    if type(position) is not int or not 1 <= position <= len(packet["events"]):
        raise ValueError("forecast prefix position is outside the packet")
    sources = [row for row in task_sources(packet) if row["source_ref"] == "question"]
    prefix = {**packet, "events": deepcopy(packet["events"][:position])}
    if len(sources) <= 1:
        return prefix, {"state": "single_turn", "active_turn": 1, "visible_turns": [1]}
    active = None
    for event in prefix["events"]:
        if event.get("task_turn") is not None:
            active = event["task_turn"]
        elif event.get("role") == "user":
            matches = [row["turn"] for row in sources
                       if row["text"].strip() == str(event.get("content") or "").strip()]
            active = matches[0] if len(matches) == 1 else None
    if active is None:
        prefix["question"] = "[task turn cannot be aligned to this recorded prefix]"
        return prefix, {"state": "unknown", "active_turn": None, "visible_turns": []}
    visible = [row for row in sources if row["turn"] <= active]
    prefix["question"] = "\n".join(f"[turn {row['turn']}] {row['text']}" for row in visible)
    return prefix, {"state": "established", "active_turn": active,
                    "visible_turns": [row["turn"] for row in visible]}


def event_order(event: Mapping[str, Any]) -> dict[str, Any]:
    tools = event.get("tool_interactions") or []
    return {"call_precedes_own_observation": [tool["interaction_id"] for tool in tools[:4]],
            "omitted_interactions": max(0, len(tools) - 4),
            "content_vs_tools": "unknown" if tools and event.get("content") else "not_mixed",
            "between_tool_interactions": "unknown" if len(tools) > 1 else "not_multiple"}


def action_channels(event: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """Return action/claim channels; observations cannot attest to the earlier action."""
    channels = [(event["event_id"], "content", str(event.get("content") or ""))]
    for tool in event.get("tool_interactions") or []:
        channels.extend((tool["interaction_id"], key, str(tool.get(key)))
                        for key in ("tool_name", "arguments"))
    return channels
