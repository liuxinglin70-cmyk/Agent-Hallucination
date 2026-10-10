"""Observed structural and explicit field dependencies, with bounded closure."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Mapping

from detector.causal_contracts import (
    CAUSAL_PROJECTION_SCHEMA, DEPENDENCY_GRAPH_SCHEMA, arithmetic_check,
    bound_fields, field_binding, numeric_literal, seal, validate_seal,
)
from detector.identity import digest
from detector.packet import answer_tool_is_delivery, delivered_result, validate_packet


def build_observed_dependencies(packet: Mapping[str, Any], *, policy: Mapping[str, Any]) -> dict[str, Any]:
    validate_packet(packet)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    omitted_nodes: list[str] = []
    omitted_edges: list[dict[str, Any]] = []
    fields_by_ref: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    literal_sources: dict[str, tuple[dict[str, Any], Any]] = {}
    node_limit = policy["max_projection_events"] * policy["max_dependency_depth"] * 3
    char_limit = policy["max_added_prompt_tokens_per_case"]

    def add_node(event, node_id, kind, ref, value, phase):
        if len(nodes) >= node_limit:
            omitted_nodes.append(node_id)
            return None
        node = {"node_id": node_id, "event_id": event["event_id"],
                "position": event["position"], "phase": phase, "kind": kind,
                "producer": "packet_structure.v1", "evidence_refs": [ref]}
        nodes.append(node)
        if len(json.dumps(value, ensure_ascii=False)) > char_limit:
            unresolved.append({"node_id": node_id, "reason": "field_input_budget"})
            return node
        fields, truncated = bound_fields(value)
        if truncated:
            unresolved.append({"node_id": node_id, "reason": "field_scan_budget"})
        fields_by_ref.setdefault(ref, []).extend((node, field) for field in fields)
        if value not in (None, "", {}) and not fields and kind != "observation":
            unresolved.append({"node_id": node_id, "reason": "dependency_source_unspecified"})
        return node

    def add_edge(source, target, relation, status, refs, *, field=None):
        edge = {"source": source["node_id"], "target": target["node_id"],
                "relation": relation, "verification_status": status,
                "producer": {"call_result": "packet_structure.v1",
                             "declared_literal_consumption": "literal_arithmetic.v1"}.get(
                                 relation, "explicit_field.v1"),
                "evidence_refs": list(dict.fromkeys(refs))}
        if field is not None:
            edge["field"] = field
        (edges if len(edges) < policy["max_graph_edges"] else omitted_edges).append(edge)

    for event in packet["events"]:
        role = event.get("role")
        add_node(event, event["event_id"], "observation" if role == "tool" else "content",
                 event["event_id"], event.get("content"), 0)
        if role not in {"assistant", "tool", "user", "system"}:
            unresolved.append({"node_id": event["event_id"], "reason": "unknown_role"})
        for interaction in event.get("tool_interactions") or ():
            iid = interaction["interaction_id"]
            arguments = add_node(event, iid + ":arguments", "arguments", iid,
                                 interaction.get("arguments"), 1)
            if interaction.get("observation") is None:
                unresolved.append({"node_id": iid + ":arguments", "reason": "missing_observation"})
                continue
            observation = add_node(event, iid + ":observation", "observation", iid,
                                   interaction["observation"], 2)
            if arguments and observation:
                add_edge(arguments, observation, "call_result", "verified", [iid])
                if arithmetic_check(interaction):
                    literal_sources[iid] = (observation, numeric_literal(interaction["observation"]))
                    unresolved[:] = [row for row in unresolved if not (
                        row["node_id"] == arguments["node_id"]
                        and row["reason"] == "dependency_source_unspecified")]

    for ref, bindings in fields_by_ref.items():
        for target, field in bindings:
            source_ref = field["source_ref"]
            if source_ref is None:
                if target["kind"] != "observation":
                    unresolved.append({"node_id": target["node_id"], "reason": "field_source_missing"})
                continue
            sources = fields_by_ref.get(source_ref, ()) if isinstance(source_ref, str) else ()
            matches = [(node, "declared_field_consumption", source) for node, source in sources
                       if field_binding(source) == field_binding(field)
                       and source["value"] == field["value"]]
            literal = literal_sources.get(source_ref) if isinstance(source_ref, str) else None
            # A scalar has no unit or time binding; a declaration cannot supply either as evidence.
            if (literal is not None and field["unit"] is None and field["time"] is None
                    and numeric_literal(field["value"]) == literal[1]):
                matches.append((literal[0], "declared_literal_consumption", None))
            preceding = [(node, relation, source_field) for node, relation, source_field in matches
                         if (node["position"], node["phase"]) < (target["position"], target["phase"])]
            if len(preceding) != 1:
                reason = "source_ambiguous" if len(preceding) > 1 else (
                    "source_not_preceding" if matches else "source_missing_or_binding_mismatch")
                unresolved.append({"node_id": target["node_id"], "reason": reason})
                continue
            source, relation, source_field = preceding[0]
            # A matching declaration verifies copied fields, not the source's truth or materiality.
            add_edge(source, target, relation, "verified", [source_ref, ref], field={
                "binding": list(field_binding(field)), "value": field["value"],
                "source_path": source_field["path"] if source_field is not None else None,
                "target_path": field["path"],
            })
    return seal({
        "schema_version": DEPENDENCY_GRAPH_SCHEMA, "packet_digest": digest(packet),
        "policy_digest": digest(policy), "nodes": nodes, "edges": edges,
        "unresolved_dependencies": unresolved, "omitted_node_ids": omitted_nodes,
        "omitted_edges": omitted_edges,
        "complete": not (unresolved or omitted_nodes or omitted_edges),
    })


def build_observed_claim_lineage(
    packet: Mapping[str, Any], *, binding: Mapping[str, Any], policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Follow declared field copies of one bound result, without inferring task effects."""
    validate_seal(binding, schema="detector.local_claim_binding.v1")
    if binding["packet_digest"] != digest(packet):
        raise ValueError("observed claim lineage packet identity differs")
    graph = build_observed_dependencies(packet, policy=policy)
    nodes = {node["node_id"]: node for node in graph["nodes"]}
    events = {event["event_id"]: event for event in packet["events"]}
    source_id = binding["interaction_id"] + ":observation"
    source_event = events.get(binding["event_id"])
    if source_event is None:
        raise ValueError("observed claim lineage source event is missing")
    interaction = next((item for item in source_event.get("tool_interactions") or ()
                        if item["interaction_id"] == binding["interaction_id"]), None)
    check = arithmetic_check(interaction) if interaction is not None else None
    if (check is None or check["status"] != "deviation"
            or any(check[key] != binding[key] for key in ("expression", "observed", "expected"))):
        raise ValueError("observed claim lineage arithmetic input differs")
    edges = []
    reasons = []
    if graph["omitted_node_ids"] or graph["omitted_edges"]:
        reasons.append("graph_budget")
    if graph["unresolved_dependencies"]:
        reasons.append("unresolved_dependencies")
    if source_id not in nodes:
        reasons.append("source_node_omitted")
    frontier = {(source_id, None, None)} if source_id in nodes else set()
    visited = set(frontier)
    reachable = set()

    def successor(edge, state):
        if edge["source"] != state[0] or edge["verification_status"] != "verified":
            return None
        field = edge.get("field")
        if field is None or numeric_literal(field["value"]) != numeric_literal(binding["observed"]):
            return None
        if state[1] is None:
            if edge["relation"] != "declared_literal_consumption":
                return None
        elif (edge["relation"] != "declared_field_consumption"
              or tuple(field["source_path"]) != state[1]
              or digest(field["binding"]) != state[2]):
            return None
        return (edge["target"], tuple(field["target_path"]), digest(field["binding"]))

    for _ in range(policy["max_dependency_depth"]):
        following = set()
        for edge in graph["edges"]:
            for state in sorted(frontier):
                target = successor(edge, state)
                if target is not None:
                    edges.append(edge)
                    reachable.add(target[0])
                    if target not in visited:
                        following.add(target)
                        visited.add(target)
        frontier = following
        if not frontier:
            break
    if any(successor(edge, state) is not None and successor(edge, state) not in visited
           for edge in graph["edges"] for state in frontier):
        reasons.append("lineage_depth_budget")
    ordered = sorted({nodes[node_id]["event_id"] for node_id in reachable},
                     key=lambda eid: events[eid]["position"])
    assistant_events = [eid for eid in ordered if events[eid].get("role") == "assistant"
                        and any(node_id in reachable and nodes[node_id]["event_id"] == eid
                                and nodes[node_id]["kind"] != "observation" for node_id in reachable)]
    anchors = delivered_result(packet)
    answer = anchors["last_answer_tool_call"] or {}
    delivery_nodes = {anchors["last_prose_event_id"]}
    if answer_tool_is_delivery(answer) and answer.get("interaction_id") is not None:
        delivery_nodes.add(answer["interaction_id"] + ":arguments")
    delivery_ids = [eid for eid in assistant_events
                    if any(node_id in delivery_nodes and nodes[node_id]["event_id"] == eid
                           for node_id in reachable)]
    consistent_refs = []
    for node in graph["nodes"]:
        if (node["kind"] != "observation" or node["position"] <= source_event["position"]
                or ":observation" not in node["node_id"]):
            continue
        iid = node["node_id"].removesuffix(":observation")
        item = next(item for item in events[node["event_id"]]["tool_interactions"]
                    if item["interaction_id"] == iid)
        correction = arithmetic_check(item)
        if (correction is not None and correction["status"] == "consistent"
                and correction["expression"] == binding["expression"]):
            consistent_refs.append(iid)
    return seal({
        "schema_version": "detector.observed_claim_lineage.v1",
        "packet_digest": graph["packet_digest"], "policy_digest": graph["policy_digest"],
        "target_digest": binding["target_digest"], "claim_binding_digest": binding["digest"],
        "graph_digest": graph["digest"], "source_interaction_id": binding["interaction_id"],
        "scope": "declared_literal_field_copies", "edges": edges,
        "observed_event_ids": ordered,
        "first_observed_assistant_event_id": next(iter(assistant_events), None),
        "delivery_event_ids": delivery_ids, "consistent_observation_refs": consistent_refs,
        "closure_state": "unknown" if reasons else "complete", "reasons": reasons,
        "materiality": "unknown", "first_causal_introduction": "unknown", "correction_effect": "unknown",
        "decision_authority": "none", "localization_authority": "none",
    })


def project_dependency_closure(
    packet: Mapping[str, Any], *, candidates: Mapping[str, Any],
    graph: Mapping[str, Any], policy: Mapping[str, Any],
) -> dict[str, Any]:
    validate_packet(packet)
    validate_seal(graph, schema=DEPENDENCY_GRAPH_SCHEMA)
    validate_seal(candidates, schema="detector.causal_candidates.v1")
    if any(value != digest(packet) for value in (graph["packet_digest"], candidates["packet_digest"])):
        raise ValueError("causal inputs belong to a different packet")
    if any(value != digest(policy) for value in (graph["policy_digest"], candidates["policy_digest"])):
        raise ValueError("causal inputs use a different policy")
    by_id = {event["event_id"]: event for event in packet["events"]}
    nodes = {node["node_id"]: node for node in graph["nodes"]}
    if any(edge["source"] not in nodes or edge["target"] not in nodes for edge in graph["edges"]):
        raise ValueError("dependency edge has an unknown node")
    reasons = []
    target_ids = candidates["selected_event_ids"]
    selected = set(eid for eid in target_ids if eid in by_id)
    if any(eid not in by_id for eid in target_ids) or candidates["state"] != "selected":
        reasons.append("candidate_selection_unknown")
    frontier = {node_id for node_id, node in nodes.items() if node["event_id"] in selected}
    visited = set(frontier)
    for _ in range(policy["max_dependency_depth"]):
        parents = {edge["source"] for edge in graph["edges"] if edge["target"] in frontier
                   and edge["verification_status"] == "verified"} - visited
        if not parents:
            frontier = set()
            break
        selected.update(nodes[node_id]["event_id"] for node_id in parents)
        visited.update(parents)
        frontier = parents
    if any(edge["source"] not in visited and edge["target"] in frontier for edge in graph["edges"]):
        reasons.append("dependency_depth_budget")
    if graph["omitted_node_ids"] or graph["omitted_edges"]:
        reasons.append("graph_budget")
    if graph["unresolved_dependencies"]:
        reasons.append("unresolved_dependencies")
    ancestors = sorted(selected - set(target_ids), key=lambda eid: by_id[eid]["position"])
    anchors = delivered_result(packet)
    delivery = list(dict.fromkeys(eid for eid in (
        anchors["last_prose_event_id"], (anchors["last_answer_tool_call"] or {}).get("event_id"),
    ) if eid is not None))
    first = min((by_id[eid]["position"] for eid in selected), default=len(by_id) + 1)
    suffix = [event["event_id"] for event in packet["events"] if event["position"] >= first]
    requested = list(dict.fromkeys([*candidates["mandatory_event_ids"], *target_ids, *ancestors, *delivery, *suffix]))
    projected = []
    omitted = []
    char_limit = policy["max_added_prompt_tokens_per_case"]
    question = packet["question"]
    chars = len(json.dumps(question, ensure_ascii=False))
    if chars > char_limit:
        question, chars = "", 0
        reasons.append("question_budget")
    for eid in requested:
        if eid not in by_id:
            omitted.append(eid)
            continue
        event = by_id[eid]
        size = len(json.dumps(event, ensure_ascii=False))
        if len(projected) >= policy["max_projection_events"] or chars + size > char_limit:
            omitted.append(eid)
            continue
        chars += size
        projected.append(deepcopy(event))
    if omitted:
        reasons.append("projection_budget_or_missing_event")
    projected.sort(key=lambda event: event["position"])
    selected_ids = [event["event_id"] for event in projected]
    return seal({
        "schema_version": CAUSAL_PROJECTION_SCHEMA, "packet_digest": digest(packet),
        "graph_digest": graph["digest"], "policy_digest": digest(policy),
        "target_event_ids": list(target_ids), "ancestor_event_ids": ancestors,
        "delivery_event_ids": delivery, "selected_event_ids": selected_ids,
        "question": question, "events": projected,
        "content_digest": digest(projected), "omitted_event_ids": omitted,
        "closure_state": "unknown" if reasons else "complete", "reasons": list(dict.fromkeys(reasons)),
        "budget": {"events": len(projected), "characters": chars,
                   "input_budget_unit": "characters", "max_characters": char_limit},
    })
