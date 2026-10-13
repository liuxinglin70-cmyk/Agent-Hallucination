"""Dataset-first event coordinates shared by detection and evaluation."""
from typing import Any, Mapping


LOCALIZATION_TARGET = "dataset_first_error_event.v1"


def _step_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def gold_step_mapping(record: Mapping[str, Any], annotation: Any) -> dict[str, Any]:
    value = _step_id(annotation)
    history = record.get("history") or []
    ids = [_step_id(row.get("step")) if isinstance(row, Mapping) else None for row in history]
    matches = [index for index, identifier in enumerate(ids, 1) if identifier == value]
    if value is None:
        state, position = "unannotated", None
    elif any(identifier is not None for identifier in ids):
        state = "source_step" if len(matches) == 1 else "ambiguous" if matches else "unmapped"
        position = matches[0] if len(matches) == 1 else None
    else:
        state = "position" if 1 <= value <= len(history) else "unmapped"
        position = value if state == "position" else None
    return {"target": LOCALIZATION_TARGET, "annotation": value,
            "position": position, "mapping_state": state}


def localization_record(packet: Mapping[str, Any], proof: Mapping[str, Any]) -> dict[str, Any]:
    position = proof.get("responsible_step")
    event = next((e for e in packet["events"] if e.get("position") == position), None) if (
        isinstance(position, int) and not isinstance(position, bool) and position > 0) else None
    source = event.get("source_step_id") if event else None
    channels = []
    quotes = [((h.get("tracer_result") or {}).get("parsed") or {}).get("quote")
              for h in proof.get("hearings") or ()
              if ((h.get("tracer_result") or {}).get("parsed") or {}).get("step") == position]
    if event:
        for quote in quotes:
            if not isinstance(quote, str) or not quote:
                continue
            if quote in str(event.get("content") or ""):
                channels.append({"kind": "content", "reference": event["event_id"]})
            for interaction in event.get("tool_interactions") or ():
                for field in ("arguments", "observation"):
                    if quote in str(interaction.get(field) or ""):
                        channel = {"kind": field, "reference": interaction["interaction_id"]}
                        if channel not in channels:
                            channels.append(channel)
    return {"target": LOCALIZATION_TARGET, "step_unit": "trajectory_event_position",
            "position": position, "source_step_id": source, "source_channels": channels}
