"""Task sources, recorded state order and premise links without verdict authority."""
from __future__ import annotations

import ast
import json
from copy import deepcopy
import re
from typing import Any, Mapping

from detector.causal_contracts import bound_fields, field_binding, seal
from detector.identity import digest
from detector.packet import structured_payload, validate_packet
from detector.runtime import prepare_once

SCHEMA = "detector.semantic_trace.v1"
_REF = re.compile(r"\bevent_\d+(?:_tool_\d+)?\b")
_CONDITION = re.compile(r"\b([A-Za-z]\w*)\s*(?:>=|<=|!=|>|<|=)\s*-?\d+(?:\.\d+)?")
_QUALIFIERS = ("entity", "entity_id", "unit", "currency", "time", "date")
_RESOURCE_FIELDS = ("resource_id", "path", "directory", "filename", "file_name", "order_id", "vehicle_id")


def task_sources(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    question = packet.get("question")
    question = question if isinstance(question, str) else ""
    starts = list(re.finditer(r"(?m)^\[turn (\d+)\] ", question))
    sources = []
    if starts:
        for i, match in enumerate(starts):
            end = starts[i + 1].start() if i + 1 < len(starts) else len(question)
            sources.append({"source_ref": "question", "turn": int(match[1]),
                            "source_role": "user",
                            "text": question[match.end():end].rstrip("\n"), "position": None,
                            "alignment": "event_alignment_unestablished"})
    elif question:
        sources.append({"source_ref": "question", "turn": 1, "text": question,
                        "source_role": "user",
                        "position": None, "alignment": "event_alignment_unestablished"})
    for event in packet["events"]:
        if event.get("role") in {"user", "human"} and isinstance(event.get("content"), str):
            sources.append({"source_ref": event["event_id"], "turn": None,
                            "source_role": event.get("role"),
                            "text": event["content"], "position": event["position"],
                            "alignment": "recorded_event"})
    for source in sources:
        source["source_digest"] = digest(source["text"])
        source["temporal_clauses"] = [match[0] for match in re.finditer(
            r"(?im)\b(?:after|before|then|once|until)\b[^\n.;!?]*|(?:之后|以后|之前|然后)[^\n。；！？]*", source["text"])]
        source["truth_status"] = "instruction_or_assertion_not_environment_truth"
    return sources


def _literal_fields(value: Any, reference: str):
    stack = [("", value, {})]
    while stack:
        path, item, inherited = stack.pop()
        if isinstance(item, Mapping):
            qualifiers = {**inherited, **{key: deepcopy(item[key]) for key in _QUALIFIERS
                                          if key in item and not isinstance(item[key], (Mapping, list))}}
            for key, child in reversed(list(item.items())):
                stack.append((f"{path}.{key}" if path else str(key), child, qualifiers))
        elif isinstance(item, list):
            stack.extend((f"{path}[{i}]", child, inherited) for i, child in reversed(list(enumerate(item))))
        elif item is None or type(item) in (str, int, float, bool):
            yield {"source_ref": reference, "field": path,
                   **({"value_digest": digest(item), "value_type": "str"} if isinstance(item, str) else {"value": item}),
                   "qualifiers": inherited, "unit_state": "declared" if inherited.get("unit") not in (None, "") else "unknown",
                   "truth_status": "recorded_value_not_independent_truth"}


def _build_semantic_trace(packet: Mapping[str, Any]) -> dict[str, Any]:
    validate_packet(packet)
    events = {event["event_id"]: event for event in packet["events"]}
    refs = {event["event_id"]: event["event_id"] for event in packet["events"]}
    refs.update({item["interaction_id"]: event["event_id"] for event in packet["events"]
                 for item in (event.get("tool_interactions") or [])})
    actions, facts, conditions, edges, omissions = [], [], [], [], []
    prior_conditions: dict[str, dict[str, Any]] = {}
    versions: dict[str, int] = {}
    prior_queries: dict[str, dict[str, Any]] = {}
    state_order = []
    task_text = packet.get("question") if isinstance(packet.get("question"), str) else ""
    field_records = []
    symbols: dict[str, dict[str, Any]] = {}
    notes: dict[str, list[dict[str, Any]]] = {}
    read_documents: dict[str, dict[str, Any]] = {}
    for event in packet["events"]:
        eid = event["event_id"]
        channels = [(eid, event.get("content"))]
        for item in (event.get("tool_interactions") or []):
            reference = item["interaction_id"]
            raw_arguments, raw_observation = item.get("arguments"), item.get("observation")
            arguments, observation = structured_payload(raw_arguments), structured_payload(raw_observation)
            channels.extend([(reference, arguments), (reference, observation)])
            name = str(item.get("tool_name") or "")
            parts = set(re.findall(r"[a-z]+", name.lower()))
            kind = ("query" if parts & {"get", "read", "list", "ls", "query", "retrieve", "status", "search"}
                    else "mutation" if parts & {"delete", "remove", "set", "update", "cancel", "write", "book"}
                    else "unknown")
            resource = ({key: arguments[key] for key in _RESOURCE_FIELDS if key in arguments}
                        if isinstance(arguments, Mapping) else {})
            resource_digest = digest(resource) if resource else None
            before = versions.get(resource_digest, 0) if resource_digest else None
            action = {"source_ref": reference, "event_id": eid, "position": event["position"],
                      "source_role": event.get("role"), "tool_name": name,
                      "resource": deepcopy(resource), "operation_hint": kind,
                      "state_version_hint": before, "verification_status": "hypothesis",
                      "arguments_digest": digest(arguments), "observation_digest": digest(observation)}
            actions.append(action)
            if resource_digest and kind == "query":
                prior_queries[resource_digest] = action
            if resource_digest and kind == "mutation":
                if resource_digest in prior_queries:
                    query = prior_queries[resource_digest]
                    state_order.append({"query_ref": query["source_ref"], "mutation_ref": reference,
                                        "relation": "query_precedes_possible_state_change",
                                        "resource": deepcopy(resource), "verification_status": "hypothesis"})
                versions[resource_digest] = before + 1
            note_name = arguments.get("note_name") if isinstance(arguments, Mapping) else None
            note_content = arguments.get("content") if isinstance(arguments, Mapping) else None
            if name == "append_note" and isinstance(note_name, str) and isinstance(note_content, str):
                for url in set(re.findall(r'https?://[^\s<>"\)]+', note_content)):
                    source = read_documents.get(url)
                    if source is not None and source["event_id"] != eid:
                        edges.append({"source_event_id": source["event_id"], "target_event_id": eid,
                                      "source_ref": source["reference"], "target_ref": reference,
                                      "relation": "possible_document_adoption", "document_identifiers": [url],
                                      "verification_status": "hypothesis"})
                notes.setdefault(note_name, []).append({"event_id": eid, "reference": reference, "content": note_content})
            if name == "read_note" and isinstance(note_name, str) and isinstance(raw_observation, str):
                for source in notes.get(note_name, ()):
                    if source["content"] and source["content"] in raw_observation and source["event_id"] != eid:
                        edges.append({"source_event_id": source["event_id"], "target_event_id": eid,
                                      "source_ref": source["reference"], "target_ref": reference,
                                      "relation": "recorded_note_copy", "note_name": note_name,
                                      "verification_status": "verified_recorded_copy_not_truth"})
                for url in set(re.findall(r'https?://[^\s<>"\)]+', raw_observation)):
                    read_documents[url] = {"event_id": eid, "reference": reference}
            if isinstance(observation, (Mapping, list)):
                rows = list(_literal_fields(observation, reference))
                for row in rows:
                    row["source_format"] = "json_string" if isinstance(raw_observation, str) else "structured"
                facts.extend(rows)
            elif isinstance(raw_observation, str):
                facts.append({"source_ref": reference, "field": "", "value_digest": digest(raw_observation), "value_type": "str",
                              "qualifiers": {}, "unit_state": "unknown", "source_format": "source_text",
                              "truth_status": "recorded_value_not_independent_truth"})
            code = arguments.get("code") if isinstance(arguments, Mapping) else None
            if isinstance(code, str):
                # The existing code reader also refuses oversized/unknown code paths.
                if len(code) > 32000:
                    omissions.append(reference)
                else:
                    try:
                        tree = ast.parse(code)
                        for statement in tree.body:
                            nodes = list(ast.walk(statement))
                            declared_symbols = set()
                            if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr)):
                                for node in nodes:
                                    if (isinstance(node, ast.Call) and ast.unparse(node.func) in {"symbols", "sympy.symbols"}
                                            and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                                        domain = {kw.arg: kw.value.value for kw in node.keywords
                                                  if kw.arg in {"real", "integer", "positive", "nonnegative", "nonzero"}
                                                  and isinstance(kw.value, ast.Constant) and type(kw.value.value) is bool}
                                        for symbol in re.findall(r"\b[A-Za-z]\w*\b", node.args[0].value):
                                            declared_symbols.add(symbol)
                                            prior = symbols.get(symbol)
                                            if prior is not None and prior["event_id"] != eid:
                                                edges.append({"source_event_id": prior["event_id"], "target_event_id": eid,
                                                              "source_ref": prior["reference"], "target_ref": reference,
                                                              "relation": "recorded_symbol_domain_change" if prior["domain"] != domain else "repeated_symbol_declaration",
                                                              "symbol": symbol, "prior_domain": prior["domain"], "domain": domain,
                                                              "verification_status": "hypothesis"})
                                            symbols[symbol] = {"event_id": eid, "reference": reference, "domain": domain}
                                for variable in (node.id for node in nodes if isinstance(node, ast.Name)):
                                    for symbol, prior in symbols.items():
                                        if (prior["event_id"] != eid and variable.endswith("_of_" + symbol)
                                                and re.search(r"\b" + re.escape(symbol) + r"\b", task_text)):
                                            edges.append({"source_event_id": prior["event_id"], "target_event_id": eid,
                                                          "source_ref": prior["reference"], "target_ref": reference,
                                                          "relation": "possible_named_symbol_consumption", "symbol": symbol,
                                                          "domain": prior["domain"], "verification_status": "hypothesis"})
                            inherited_alias = (deepcopy(prior_conditions.get(statement.value.id))
                                               if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Name)
                                               else None)
                            if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)):
                                variables = {node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
                                for variable in sorted(variables):
                                    origin = prior_conditions.get(variable)
                                    if origin is not None and origin["event_id"] != eid:
                                        edges.append({"source_event_id": origin["event_id"], "target_event_id": eid,
                                                      "source_ref": origin["source_ref"], "target_ref": reference,
                                                      "relation": "possible_condition_consumption", "variable": variable,
                                                      "condition": origin["quote"], "verification_status": "hypothesis"})
                            for node in nodes:
                                if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                                    prior_conditions.pop(node.id, None)
                                    if node.id not in declared_symbols:
                                        symbols.pop(node.id, None)
                            if inherited_alias is not None:
                                for target in statement.targets:
                                    if isinstance(target, ast.Name):
                                        prior_conditions[target.id] = {"event_id": eid, "source_ref": reference,
                                                                      "quote": ast.get_source_segment(code, statement) or ast.unparse(statement),
                                                                      "condition": inherited_alias.get("condition", inherited_alias["quote"]),
                                                                      "verification_status": "hypothesis"}
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Call) and node.args:
                                function = ast.unparse(node.func)
                                if function in {"log", "ln", "log10", "log2", "math.log", "math.log10", "math.log2"}:
                                    conditions.append({"event_id": eid, "source_ref": reference,
                                                       "quote": ast.get_source_segment(code, node) or ast.unparse(node),
                                                       "condition": ast.unparse(node.args[0]) + " > 0 (real-domain hypothesis)",
                                                       "verification_status": "hypothesis"})
                    except (SyntaxError, ValueError, RecursionError):
                        omissions.append(reference)
        for reference, value in channels:
            fields, truncated = bound_fields(value)
            if truncated:
                omissions.append(reference)
            for field in fields:
                field_records.append({"reference": reference, "event_id": eid,
                                      "position": event["position"], "source_role": event.get("role"),
                                      "field": field})
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False) if isinstance(value, (Mapping, list)) else ""
            for match in _CONDITION.finditer(text):
                row = {"event_id": eid, "source_ref": reference, "quote": match[0],
                       "source_role": event.get("role"), "verification_status": "hypothesis"}
                conditions.append(row)
                prior_conditions[match[1]] = row
            for match in _REF.finditer(text):
                source = refs.get(match[0])
                if source is not None and events[source]["position"] < event["position"]:
                    edges.append({"source_event_id": source, "target_event_id": eid,
                                  "source_ref": match[0], "target_ref": reference,
                                  "relation": "explicit_recorded_reference", "verification_status": "hypothesis"})
    unresolved_fields = []
    fields_by_ref: dict[str, list[dict[str, Any]]] = {}
    for source in field_records:
        fields_by_ref.setdefault(source["reference"], []).append(source)
    for target in field_records:
        field = target["field"]
        if field["source_ref"] is None:
            continue
        binding = tuple(field.get("source_binding", field_binding(field)))
        matches = [source for source in fields_by_ref.get(str(field["source_ref"]), ())
                   if source["position"] < target["position"]
                   and field_binding(source["field"]) == binding
                   and source["field"]["value"] == field["value"]
                   and source["field"]["unit"] == field["unit"]
                   and source["field"]["time"] == field["time"]]
        if len(matches) != 1:
            unresolved_fields.append({"target_ref": target["reference"], "field": field["path"],
                                      "reason": "ambiguous_or_missing_source_binding"})
        else:
            source = matches[0]
            edges.append({"source_event_id": source["event_id"], "target_event_id": target["event_id"],
                          "source_ref": source["reference"], "target_ref": target["reference"],
                          "relation": "declared_field_copy", "source_field": source["field"]["path"],
                          "target_field": field["path"], "binding": list(field_binding(field)),
                          "source_role": source["source_role"], "target_role": target["source_role"],
                          "unit_state": "unknown" if field["unit"] is None else "declared",
                          "verification_status": "verified_recorded_copy_not_truth"})
    return seal({"schema_version": SCHEMA, "packet_digest": digest(packet),
                 "event_positions": {eid: event["position"] for eid, event in events.items()},
                 "task_sources": task_sources(packet), "actions": actions, "state_order_hints": state_order,
                 "literal_facts": facts, "conditions": conditions, "edges": edges,
                 "unresolved_field_bindings": unresolved_fields,
                 "omitted_interaction_ids": list(dict.fromkeys(omissions)),
                 "claim_equivalence": "unestablished", "execution_state": "unverified",
                 "decision_authority": "none", "localization_authority": "none"})


def build_semantic_trace(packet: Mapping[str, Any]) -> dict[str, Any]:
    return prepare_once("semantic_trace", packet, lambda: _build_semantic_trace(packet))


def semantic_ancestors(trace: Mapping[str, Any], event_id: str) -> list[str]:
    reached = {event_id}
    parents: dict[str, set[str]] = {}
    for edge in trace["edges"]:
        parents.setdefault(edge["target_event_id"], set()).add(edge["source_event_id"])
    frontier = [event_id]
    while frontier:
        for source in parents.get(frontier.pop(), ()):
            if source not in reached:
                reached.add(source)
                frontier.append(source)
    return sorted(reached - {event_id}, key=lambda eid: trace["event_positions"][eid])


def referent_provenance(packet: Mapping[str, Any], positive: Mapping[str, Any], snapshot=None, *, basis=None) -> dict[str, Any]:
    trace = build_semantic_trace(packet)
    refs = positive.get("evidence_refs") or []
    return {"schema_version": "detector.referent_provenance.v1", "packet_digest": digest(packet),
            "target_digest": digest(positive), "asserted_referent": positive.get("referent") or positive.get("correct_result"),
            "basis": basis or positive.get("basis", "unestablished"), "snapshot_digest": digest(snapshot),
            "recorded_literals": [row for row in trace["literal_facts"] if row["source_ref"] in refs
                                  or row["source_ref"].split("_tool_")[0] in refs],
            "truth_status": "requires_independent_verification", "decision_authority": "none"}
