"""Complete source inventory and lossless expansion of requested packet references."""
from copy import deepcopy
import json
from typing import Any, Mapping, Sequence

from detector.code_lineage import build_code_lineage, code_ancestor_events
from detector.identity import digest
from detector.packet import packet_reference_error, validate_packet
from detector.runtime import prepare_once
from detector.semantic_trace import build_semantic_trace, referent_provenance, semantic_ancestors, task_sources


def evidence_inventory(packet: Mapping[str, Any]) -> dict[str, Any]:
    def build():
        validate_packet(packet)
        rows = []
        for event in packet["events"]:
            rows.append({"reference": event["event_id"], "event_id": event["event_id"],
                         "position": event["position"], "source_step_id": event.get("source_step_id"),
                         "source_role": event.get("role"), "channel": "content",
                         "value_digest": digest(event.get("content"))})
            for item in (event.get("tool_interactions") or []):
                for channel in ("arguments", "observation"):
                    rows.append({"reference": item["interaction_id"], "event_id": event["event_id"],
                                 "position": event["position"], "source_step_id": event.get("source_step_id"),
                                 "source_role": event.get("role"), "channel": channel,
                                 "tool_name": item.get("tool_name"), "value_digest": digest(item.get(channel))})
        return {"schema_version": "detector.evidence_inventory.v1", "packet_digest": digest(packet),
                "channels": rows, "inventory_complete": True}
    return prepare_once("evidence_inventory", packet, build)


def read_evidence(packet: Mapping[str, Any], references: Sequence[str]) -> dict[str, Any]:
    error = packet_reference_error(packet, list(references), required=True)
    if error:
        raise ValueError(error)
    requested = set(references)
    rows = []
    for event in packet["events"]:
        if event["event_id"] in requested:
            rows.append(deepcopy(event))
        else:
            tools = [deepcopy(item) for item in (event.get("tool_interactions") or []) if item["interaction_id"] in requested]
            if tools:
                rows.append({"event_id": event["event_id"], "position": event["position"],
                             "source_step_id": event.get("source_step_id"), "role": event.get("role"),
                             "content": None, "tool_interactions": tools})
    selected_ids = {row["event_id"] for row in rows}
    return {"packet_digest": digest(packet), "requested_references": list(references),
            "events": rows, "expanded_channels_complete": True,
            "unrequested_event_ids": [e["event_id"] for e in packet["events"] if e["event_id"] not in selected_ids]}


def expand_view(packet: Mapping[str, Any], text: str, meta: Mapping[str, Any], *,
                positive: Mapping[str, Any], include_ancestors: bool = False) -> tuple[str, dict[str, Any]]:
    if not meta.get("degraded") and not meta.get("packet_truncated") and not meta.get("truncated_event_ids"):
        return text, deepcopy(dict(meta))
    refs = list(dict.fromkeys(positive.get("evidence_refs") or []))
    event = next((e for e in packet["events"] if e["position"] == positive.get("step")), None)
    if event is not None:
        refs.append(event["event_id"])
        if include_ancestors:
            refs.extend(code_ancestor_events(build_code_lineage(packet), event["event_id"]))
            refs.extend(semantic_ancestors(build_semantic_trace(packet), event["event_id"]))
    refs = list(dict.fromkeys(refs))
    updated = deepcopy(dict(meta))
    if not refs or packet_reference_error(packet, refs, required=True):
        updated["evidence_expansion"] = {"state": "unavailable", "reason": "no_valid_target_references"}
        return text, updated
    expanded = read_evidence(packet, refs)
    inventory = evidence_inventory(packet)
    index = [{k: row[k] for k in ("reference", "position", "source_role", "channel", "value_digest")}
             for row in inventory["channels"]]
    channels = []
    for row in expanded["events"]:
        channels.append(f"EVENT {row['event_id']} (position={row['position']}, role={row.get('role') or '?'})")
        if row.get("content") is not None:
            channels.append(str(row["content"]))
        for item in (row.get("tool_interactions") or []):
            channels.append(f"TOOL {item['interaction_id']} name={item.get('tool_name')}")
            channels.append("arguments=" + str(item.get("arguments")))
            channels.append("observation=" + str(item.get("observation")))
    block = ("\n\nCOMPLETE CHANNEL INVENTORY (coordinates and hashes, not proof of truth):\n"
             + json.dumps(index, ensure_ascii=False)
             + "\nLOSSLESS REQUESTED EVIDENCE (unrequested channels may remain excerpted):\n"
             + "\n".join(channels)
             + "\nUNABRIDGED TASK SOURCES (instructions/assertions are not environment facts):\n"
             + json.dumps(task_sources(packet), ensure_ascii=False))
    updated["evidence_expansion"] = {"state": "expanded", "references": refs,
                                     "packet_digest": expanded["packet_digest"],
                                     "inventory_digest": digest(inventory), "expanded_characters": len(block),
                                     "expanded_channels_complete": True}
    # Complete requested channels do not make the remaining trajectory complete.
    updated["degraded"] = True
    return text + block, updated


def task_source_block(packet: Mapping[str, Any]) -> str:
    return ("\n\nTASK/USER SOURCE AND ORDER INDEX (literal spans; interpret instructions versus assertions; not an obligation verdict):\n"
            + json.dumps(task_sources(packet), ensure_ascii=False))


def referent_source_block(packet: Mapping[str, Any], positive: Mapping[str, Any], snapshot=None) -> str:
    provenance = referent_provenance(packet, positive, snapshot)
    records = []
    for row in provenance["recorded_literals"]:
        record = {key: value for key, value in row.items() if key != "value"}
        if "value" in row:
            record["value_digest"] = digest(row["value"])
            record["value_type"] = type(row["value"]).__name__
            record["value"] = row["value"]
        records.append(record)
    return ("\n\nREFERENT SOURCE INDEX (recorded values, not independent truth; string values are in the cited raw channels):\n"
            + json.dumps({**provenance, "recorded_literals": records}, ensure_ascii=False)
            + "\nMissing source units remain unknown. A model's asserted correct referent is not an observed source. "
            "Check actual task constraints, cited raw channels and applicable independent evidence.")
