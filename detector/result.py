"""Detection result envelope and authority readers."""

from __future__ import annotations

from typing import Any, Mapping

RESULT_SCHEMA = "detector.detection_result.v1"
VALID_STATES = frozenset({"verified_error", "verified_clean", "unresolved"})


def build_detection_output(
    *,
    case_id: str,
    packet: Mapping[str, Any],
    proof: Mapping[str, Any],
    stage_status: Mapping[str, Any],
    providers: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    state = str(proof.get("state") or "unresolved")
    if state not in VALID_STATES:
        raise ValueError(f"invalid terminal state {state!r}")
    return {
        "schema_version": RESULT_SCHEMA,
        "case_id": case_id,
        "state": state,
        "decision_authority": proof.get("decision_authority"),
        "runtime_valid": bool(proof.get("runtime_valid")),
        "responsible_event_id": proof.get("responsible_event_id"),
        "responsible_step": proof.get("responsible_step"),
        "incident_occurred": proof.get("incident_occurred"),
        "occurrence_state": state,
        "proof": dict(proof),
        "stage_status": {key: dict(value) for key, value in stage_status.items() if value is not None},
        "providers": {
            key: value for key, value in providers.items()
            if value is not None
        },
        "authority_failure_reasons": list(
            proof.get("authority_failure_reasons") or []
        ),
        "evidence_health": {
            "judge_view": (stage_status.get("judge") or {}).get("view"),
            "recall_view": (stage_status.get("recall_scanner") or {}).get("view"),
            "upgrade_view": (stage_status.get("causal_upgrade") or {}).get("view"),
            "tracer_view": (stage_status.get("step_tracer") or {}).get("view"),
            "defense_view": (stage_status.get("defense") or {}).get("view"),
        },
        "provenance": dict(provenance),
        "_packet_event_count": len(list(packet.get("events") or [])),
    }


def prediction_state(
    row: Mapping[str, Any], *, require_authoritative: bool = True,
) -> bool | None:
    if str(row.get("schema_version") or "") != RESULT_SCHEMA:
        return None
    if require_authoritative and str(
        row.get("decision_authority") or ""
    ) != "authoritative":
        return None
    if row.get("runtime_valid") is not True:
        return None
    state = str(row.get("state") or "")
    if state not in VALID_STATES or row.get("occurrence_state") != state:
        return None
    if state == "verified_error":
        return True
    if state == "verified_clean":
        return False
    return None


__all__ = [
    "RESULT_SCHEMA",
    "VALID_STATES",
    "build_detection_output",
    "prediction_state",
]
