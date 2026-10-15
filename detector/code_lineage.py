"""Recorded code def-use hints; parsing never executes code or proves materiality."""

from __future__ import annotations

import ast
from typing import Any, Mapping

from detector.causal_contracts import seal
from detector.identity import digest
from detector.packet import structured_payload, validate_packet


def build_code_lineage(packet: Mapping[str, Any], *, max_edges: int = 64) -> dict[str, Any]:
    validate_packet(packet)
    bindings: dict[str, str] = {}
    edges: list[dict[str, Any]] = []
    omitted: list[str] = []
    for event in packet["events"]:
        for item in (event.get("tool_interactions") or []):
            arguments = structured_payload(item.get("arguments"))
            code = arguments.get("code") if isinstance(arguments, Mapping) else arguments
            if not isinstance(code, str) or not code.strip():
                continue
            if len(code) > 32000:
                omitted.append(item["interaction_id"])
                continue
            try:
                tree = ast.parse(code)
            except (SyntaxError, ValueError, RecursionError):
                omitted.append(item["interaction_id"])
                continue
            for statement in tree.body:
                if not isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)):
                    # Branches, loops and function calls do not establish an executed path.
                    for node in ast.walk(statement):
                        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                            bindings.pop(node.id, None)
                    continue
                nodes = list(ast.walk(statement))
                if len(nodes) > 10000:
                    omitted.append(item["interaction_id"])
                    continue
                reads = {node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
                if isinstance(statement, ast.AugAssign) and isinstance(statement.target, ast.Name):
                    reads.add(statement.target.id)
                sources: dict[str, list[str]] = {}
                for name in sorted(reads):
                    if name in bindings and bindings[name] != event["event_id"]:
                        sources.setdefault(bindings[name], []).append(name)
                for source, names in sources.items():
                    if len(edges) >= max_edges:
                        omitted.append(item["interaction_id"])
                        continue
                    edges.append({"source_event_id": source, "target_event_id": event["event_id"],
                                  "variables": names[:8], "evidence_refs": [source, item["interaction_id"]],
                                  "relation": "recorded_code_use", "verification_status": "hypothesis"})
                targets = {node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
                for name in targets:
                    bindings[name] = event["event_id"]
    return seal({"schema_version": "detector.code_lineage.v1", "packet_digest": digest(packet),
                 "event_positions": {event["event_id"]: event["position"] for event in packet["events"]},
                 "edges": edges, "omitted_interaction_ids": list(dict.fromkeys(omitted)),
                 "scope": "recorded_code_def_use", "execution_state": "unverified",
                 "decision_authority": "none", "localization_authority": "none"})


def code_ancestor_events(lineage: Mapping[str, Any], event_id: str) -> list[str]:
    reached = {event_id}
    parents: dict[str, set[str]] = {}
    for edge in lineage["edges"]:
        parents.setdefault(edge["target_event_id"], set()).add(edge["source_event_id"])
    frontier = [event_id]
    while frontier:
        for source in parents.get(frontier.pop(), ()):
            if source not in reached:
                reached.add(source)
                frontier.append(source)
    return sorted(reached - {event_id}, key=lambda eid: lineage["event_positions"][eid])
