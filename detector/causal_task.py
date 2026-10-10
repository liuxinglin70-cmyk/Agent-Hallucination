"""Task-bound evidence for single literal arithmetic result declarations."""

from __future__ import annotations

from fractions import Fraction
from decimal import Decimal, localcontext
import re
from typing import Any, Mapping

from detector.causal_contracts import arithmetic_check, literal_arithmetic_value, numeric_literal, seal, validate_seal
from detector.causal_graph import build_observed_claim_lineage
from detector.identity import digest
from detector.packet import delivered_result, packet_reference_index


TASK_CLAIM_SCHEMA = "detector.task_claim_certificate.v1"
_ARITHMETIC_COMMAND = re.compile(r"(?:calculate|compute|evaluate)\s+([^\r\n]+?)[.?]?", re.IGNORECASE)
_RESULT_FIELDS = frozenset({"entity", "attribute", "value", "source_ref", "unit", "time"})


def _result_value(content: Any):
    if (not isinstance(content, Mapping) or set(content) - _RESULT_FIELDS
            or content.get("entity") != "result" or content.get("attribute") != "value"
            or content.get("unit") is not None or content.get("time") is not None):
        return None
    return numeric_literal(content.get("value"))


def build_task_claim_certificate(
    packet: Mapping[str, Any], *, binding: Mapping[str, Any],
    lineage: Mapping[str, Any], policy: Mapping[str, Any],
) -> dict[str, Any]:
    validate_seal(binding, schema="detector.local_claim_binding.v1")
    if lineage != build_observed_claim_lineage(packet, binding=binding, policy=policy):
        raise ValueError("task claim certificate observed lineage differs")
    question = packet["question"]
    command = _ARITHMETIC_COMMAND.fullmatch(question.strip()) if isinstance(question, str) and len(question) <= 280 else None
    expected = literal_arithmetic_value(command[1]) if command is not None else None
    with localcontext() as context:
        context.prec = 1024
        expected_text = str((Decimal(expected.numerator) / Decimal(expected.denominator)).normalize()) if expected is not None else None
    result = {
        "schema_version": TASK_CLAIM_SCHEMA, "scope": "single_literal_arithmetic_result",
        "packet_digest": digest(packet), "target_digest": binding["target_digest"],
        "task_digest": digest(question), "claim_binding_digest": binding["digest"],
        "lineage_digest": lineage["digest"], "policy_digest": digest(policy),
        "verifier": "literal_arithmetic_delivery.v1", "task_status": "unknown",
        "materiality": "unknown", "first_introduction": "unknown",
        "earliest_supported_event_id": None, "correction_effect": "unknown",
        "delivery": {"event_id": None, "observed": None, "expected": expected_text},
        "counterfactual": {"kind": "none", "status": "not_run", "repaired_value": None,
                           "repaired_event_ids": [], "assumptions": [], "state_changes_verified": False},
        "decision_authority": "none", "localization_authority": "none", "reasons": [],
    }

    def unknown(reason):
        result["reasons"].append(reason)
        return seal(result)

    if expected is None:
        return unknown("unsupported_task_contract")
    if literal_arithmetic_value(binding["expression"]) != expected:
        return unknown("task_result_does_not_match_bound_claim")
    if any(event.get("role") not in {"assistant", "tool"} for event in packet["events"]):
        return unknown("additional_instruction_context")
    anchors = delivered_result(packet)
    if anchors["last_answer_tool_call"] is not None:
        return unknown("tool_delivery_contract_unsupported")
    events = {event["event_id"]: event for event in packet["events"]}
    final = events.get(anchors["last_prose_event_id"])
    observed = _result_value(final.get("content")) if final is not None and final.get("role") == "assistant" else None
    if observed is None:
        return unknown("unsupported_result_declaration")
    result["delivery"].update(event_id=final["event_id"], observed=str(observed))
    if Fraction(observed) == expected:
        result.update(task_status="satisfied", materiality="refuted",
                      correction_effect="verified_task_delivery_consistent")
        return seal(result)
    if lineage["closure_state"] != "complete":
        return unknown("incomplete_claim_lineage")
    if observed != numeric_literal(binding["observed"]) or final["event_id"] not in lineage["delivery_event_ids"]:
        return unknown("delivery_not_bound_claim")
    result.update(task_status="violated", materiality="verified")
    declarations = []
    for event in packet["events"]:
        if (event.get("role") == "assistant" and event["event_id"] in lineage["observed_event_ids"]
                and _result_value(event.get("content")) == observed
                and any(edge["target"] == event["event_id"] and edge["field"]["target_path"] == []
                        and edge["field"]["binding"] == ["result", "value", None, None]
                        for edge in lineage["edges"])):
            declarations.append(event)
    if not declarations:
        return unknown("first_result_declaration_not_verified")
    first = declarations[0]
    for event in packet["events"]:
        if event["position"] >= first["position"]:
            break
        if event.get("role") != "assistant":
            continue
        content = event.get("content")
        if content not in (None, ""):
            value = _result_value(content)
            if value is None or Fraction(value) != expected:
                return unknown("unverified_or_distinct_prefix_claim")
        for interaction in event.get("tool_interactions") or ():
            arguments = interaction.get("arguments")
            if (not isinstance(arguments, Mapping) or set(arguments) != {"expr"}
                    or arithmetic_check(interaction) is None):
                return unknown("unverified_prefix_action")
    result.update(first_introduction="verified_declared_result", earliest_supported_event_id=first["event_id"])
    binding_id = digest(["result", "value", None, None])
    repaired = {(first["event_id"], (), binding_id)}
    for _ in range(policy["max_dependency_depth"]):
        added = set()
        for edge in lineage["edges"]:
            if edge["relation"] != "declared_field_consumption":
                continue
            field = edge["field"]
            key = digest(field["binding"])
            if (edge["source"], tuple(field["source_path"]), key) in repaired:
                added.add((edge["target"], tuple(field["target_path"]), key))
        added -= repaired
        if not added:
            break
        repaired.update(added)
    if (final["event_id"], (), binding_id) not in repaired:
        return unknown("repair_does_not_reach_delivery")
    if numeric_literal(expected_text) is None:
        return unknown("repaired_result_outside_scalar_budget")
    owners = packet_reference_index(packet)
    repaired_ids = {owners[node_id.partition(":")[0]] for node_id, _, _ in repaired}
    result["counterfactual"].update(
        kind="declared_value_repair", status="verified_symbolic", repaired_value=expected_text,
        repaired_event_ids=[eid for eid in events if eid in repaired_ids],
        assumptions=["same explicit field-copy links", "same single literal arithmetic obligation"],
    )
    return seal(result)
