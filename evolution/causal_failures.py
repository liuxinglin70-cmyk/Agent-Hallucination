"""Development-side failure evidence, recomputed from the evaluated trajectory."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
import json

from detector.causal_audit import validate_hearing_context
from detector.causal_contracts import seal
from detector.identity import digest
from detector.packet import build_packet, delivered_result
from detector.next_action import build_next_action_audit
from detector.step_uncertainty import build_step_risk_profile, select_causal_candidates
from detector.localization import LOCALIZATION_TARGET
from evolution.metrics import mechanism_row_healthy, row_responsible_step


FAILURE_SCHEMA = "evolution.causal_failure_record.v1"
ACTION_FAILURE_SCHEMA = "evolution.action_failure_triplet.v1"


def shared_action_rule_conditions(family: Mapping[str, Any]) -> list[dict[str, str]]:
    """Intersect diagnostic action patterns across independent family members."""
    members = (family.get("diagnostics") or {}).get("member_summaries") or []
    if len(members) < 2:
        return []
    shared: set[tuple[str, str]] | None = None
    for member in members:
        conditions = (member.get("causal_failure") or {}).get("action_rule_conditions") or []
        patterns = {(condition["risk_pattern"], condition["divergence_pattern"])
                    for condition in conditions
                    if condition.get("action_state") == "divergent"
                    and condition.get("risk_pattern") == "observed_tool_failure"
                    and condition.get("divergence_pattern") == "success_claim_without_observed_recovery"}
        shared = patterns if shared is None else shared.intersection(patterns)
    return [{"risk_pattern": risk, "divergence_pattern": action}
            for risk, action in sorted(shared or ())]


def matching_action_normal_neighbors(
    patterns: Sequence[Mapping[str, str]], neighbors: Sequence[Mapping[str, Any]], *,
    policy: Mapping[str, Any],
) -> list[str]:
    """Select healthy clean records whose same prefix risk has a compliant next event."""
    required = {pattern["risk_pattern"] for pattern in patterns}
    selected: list[str] = []
    for neighbor in neighbors:
        case = str(neighbor.get("case") or "")
        if (not case or neighbor.get("gold") is not False or neighbor.get("prediction") is not False
                or not mechanism_row_healthy(neighbor)):
            continue
        record = neighbor.get("record")
        if not isinstance(record, Mapping):
            continue
        triplets = build_action_failure_triplets(record, policy=policy)
        compliant = {triplet["predecessor_risk"]["risk_pattern"] for triplet in triplets
                     if triplet["next_action"]["action_state"] == "compliant"}
        if required and required <= compliant:
            selected.append(case)
    return list(dict.fromkeys(selected))


def build_action_failure_triplets(
    record: Mapping[str, Any], *, policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Archive prefix risk, recorded next action and actual delivery, never a gold answer."""
    packet = build_packet(record)
    profile = build_step_risk_profile(packet, policy=policy)
    selection = select_causal_candidates(profile, mandatory_event_ids=[], policy=policy)
    audit = build_next_action_audit(packet, selection["top_k_event_ids"])
    delivery = delivered_result(packet)
    prose = next((event for event in packet["events"]
                  if event["event_id"] == delivery["last_prose_event_id"]), None)
    delivery = {**delivery, "last_prose_content": prose["content"] if prose else None}
    encoded_delivery = json.dumps(delivery, ensure_ascii=False)
    return [seal({
        "schema_version": ACTION_FAILURE_SCHEMA, "packet_digest": digest(packet),
        "policy_digest": digest(policy), "audit_digest": audit["digest"],
        "predecessor_risk": comparison["boundary"],
        "next_action": {key: comparison[key] for key in (
            "next_event_id", "next_position", "action_state", "divergence_pattern", "evidence_refs")},
        "terminal_delivery": {"excerpt": encoded_delivery[:1024],
                              "truncated": len(encoded_delivery) > 1024,
                              "delivery_digest": digest(delivery), "deviation_status": "unverified"},
        "decision_authority": "none", "localization_authority": "none",
        "evidence_state": "hypothesis",
    }) for comparison in audit["comparisons"]]


def build_causal_failure_record(
    row: Mapping[str, Any], record: Mapping[str, Any] | None, *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "schema_version": FAILURE_SCHEMA, "classification": "evidence_missing",
        "evidence_state": "missing", "packet_digest": None, "target_digest": None,
        "context_digest": None, "policy_digest": digest(policy) if policy is not None else None,
        "scope": None, "materiality": "unknown", "first_introduction": "unknown",
        "repair_status": "not_run", "first_event_id": None, "first_source_step_id": None, "first_position": None,
        "repair_operation": "review_evidence", "reasons": [],
    }

    def finish(reason=None):
        if reason is not None:
            result["reasons"].append(reason)
        if record is not None and policy is not None:
            try:
                triplets = build_action_failure_triplets(record, policy=policy)
            except (ValueError, KeyError, TypeError) as exc:
                result["reasons"].append("action_triplet_unavailable:" + str(exc))
            else:
                if triplets:
                    result["action_triplets"] = triplets
        return seal(result)

    if not mechanism_row_healthy(row):
        result.update(classification="runtime_unmeasurable", evidence_state="unmeasurable")
        return finish("measurement_not_healthy")
    if row["gold"] is True and row["prediction"] is False:
        result.update(classification="missed_detection", repair_operation="observe_deviation")
        return finish("no_positive_hearing")
    if row["gold"] is False and row["prediction"] is True:
        result.update(classification="false_positive", repair_operation="review_conviction_evidence")
        return finish("task_error_not_established")
    hearings = ((row.get("result") or {}).get("proof") or {}).get("hearings") or []
    substantiated = [hearing for hearing in hearings
                     if ((hearing.get("defense_result") or {}).get("parsed") or {}).get("verdict") == "substantiated"]
    if len(substantiated) > 1:
        result.update(classification="ambiguous", evidence_state="ambiguous")
        return finish("multiple_substantiated_hearings")
    if len(substantiated) != 1 or record is None or policy is None:
        return finish("current_hearing_record_and_policy_required")
    hearing = substantiated[0]
    tracer = hearing.get("tracer_result") or {}
    context = tracer.get("causal_context")
    positive = hearing.get("positive")
    if (tracer.get("ok") is not True or not isinstance(context, Mapping)
            or not isinstance(positive, Mapping)):
        return finish("current_hearing_context_missing")
    if (hearing.get("tracer_status", {}).get("state") != "ok"
            or hearing.get("defense_status", {}).get("state") != "ok"
            or hearing.get("defense_result", {}).get("ok") is not True
            or (row.get("result") or {}).get("localization_authority") != "authoritative"
            or (tracer.get("parsed") or {}).get("step") != row_responsible_step(row)):
        result["evidence_state"] = "invalid"
        return finish("current_hearing_localization_not_authoritative_or_aligned")
    try:
        packet = build_packet(record)
        if tracer.get("hearing_target") != positive or tracer.get("causal_context_digest") != context.get("digest"):
            raise ValueError("hearing identity differs")
        validate_hearing_context(context, packet=packet, positive=positive, policy=policy)
    except (ValueError, KeyError, TypeError) as exc:
        result["evidence_state"] = "invalid"
        return finish(str(exc))
    if context.get("scope") == "diagnostic_event_priority":
        result.update(evidence_state="hypothesis", scope=context["scope"],
                      packet_digest=digest(packet), target_digest=context["target_digest"],
                      context_digest=context["digest"],
                      risk_candidates=[{key: candidate[key] for key in
                                       ("event_id", "position", "priority", "signals")}
                                      for candidate in context["candidates"]])
        return finish("risk_priorities_do_not_verify_causal_repair")
    certificate = context["task_certificate"]
    result.update(packet_digest=digest(packet), target_digest=context["target_digest"],
                  context_digest=context["digest"], scope=certificate["scope"],
                  materiality=certificate["materiality"], first_introduction=certificate["first_introduction"],
                  repair_status=certificate["counterfactual"]["status"])
    if (certificate["materiality"] != "verified"
            or certificate["first_introduction"] != "verified_declared_result"
            or certificate.get("localization", {}).get("target") != LOCALIZATION_TARGET
            or certificate.get("localization", {}).get("origin_status") != "verified_local_result_origin"
            or certificate["counterfactual"]["status"] != "verified_symbolic"):
        result["evidence_state"] = "unknown"
        return finish("same_claim_first_introduction_and_repair_not_verified")
    first = next(event for event in packet["events"]
                 if event["event_id"] == certificate["localization"]["origin_event_id"])
    step = row_responsible_step(row)
    first_step = first["position"]
    result.update(evidence_state="verified", first_introduction="verified_local_result_origin",
                  localization_target=LOCALIZATION_TARGET, first_event_id=first["event_id"],
                  first_source_step_id=first["source_step_id"], first_position=first_step)
    if step is None:
        return finish("responsible_position_missing")
    classification = "late" if step > first_step else "early" if step < first_step else "aligned"
    result.update(classification=classification, repair_operation=(
        "trace_first_error_event" if classification in {"late", "early"} else "none"))
    return finish()


def causal_failure_prompt_view(failure: Mapping[str, Any]) -> dict[str, Any]:
    view = {key: failure[key] for key in (
        "schema_version", "classification", "evidence_state", "scope", "materiality",
        "first_introduction", "repair_status", "repair_operation", "reasons",
    )}
    if "risk_candidates" in failure:
        view["risk_candidates"] = failure["risk_candidates"]
    if "action_triplets" in failure:
        view["action_rule_conditions"] = [{
            "risk_pattern": triplet["predecessor_risk"]["risk_pattern"],
            "allowed_actions": triplet["predecessor_risk"]["allowed_actions"],
            "action_state": triplet["next_action"]["action_state"],
            "divergence_pattern": triplet["next_action"]["divergence_pattern"],
            "terminal_deviation_status": triplet["terminal_delivery"]["deviation_status"],
            "decision_authority": "none",
        } for triplet in failure["action_triplets"]]
    return view
