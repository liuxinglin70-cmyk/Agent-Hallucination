"""Development-side failure evidence, recomputed from the evaluated trajectory."""

from __future__ import annotations

from typing import Any, Mapping

from detector.causal_audit import validate_hearing_context
from detector.causal_contracts import seal
from detector.identity import digest
from detector.packet import build_packet
from evolution.metrics import mechanism_row_healthy, row_responsible_step


FAILURE_SCHEMA = "evolution.causal_failure_record.v1"


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
    certificate = context["task_certificate"]
    result.update(packet_digest=digest(packet), target_digest=context["target_digest"],
                  context_digest=context["digest"], scope=certificate["scope"],
                  materiality=certificate["materiality"], first_introduction=certificate["first_introduction"],
                  repair_status=certificate["counterfactual"]["status"])
    if (certificate["materiality"] != "verified"
            or certificate["first_introduction"] != "verified_declared_result"
            or certificate["counterfactual"]["status"] != "verified_symbolic"):
        result["evidence_state"] = "unknown"
        return finish("same_claim_first_introduction_and_repair_not_verified")
    first = next(event for event in packet["events"]
                 if event["event_id"] == certificate["earliest_supported_event_id"])
    step = row_responsible_step(row)
    first_step = first["position"]
    result.update(evidence_state="verified", first_event_id=first["event_id"],
                  first_source_step_id=first["source_step_id"], first_position=first_step)
    if step is None:
        return finish("responsible_position_missing")
    classification = "late" if step > first_step else "early" if step < first_step else "aligned"
    result.update(classification=classification, repair_operation=(
        "trace_first_declared_result" if classification in {"late", "early"} else "none"))
    return finish()


def causal_failure_prompt_view(failure: Mapping[str, Any]) -> dict[str, Any]:
    return {key: failure[key] for key in (
        "schema_version", "classification", "evidence_state", "scope", "materiality",
        "first_introduction", "repair_status", "repair_operation", "reasons",
    )}
