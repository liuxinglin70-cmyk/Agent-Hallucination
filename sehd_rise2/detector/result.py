"""Detection result envelope and fail-closed authority reading.

The envelope is the frozen output contract every consumer reads:

- ``state`` / ``decision_authority`` / ``runtime_valid``: the fail-closed
  authority triple. An invalid run never carries a decisive verdict.
- ``proof_adjudication``: the court's terminal record (verdict, terminal
  basis, coverage, policy evaluation, certificates).
- ``providers`` / ``authority_failure_reasons``: machine-readable failure
  diagnostics.
- ``provenance``: which deployed evolution-artifact registry ran.

The authority readers below are the ONLY legal way for the evolution
framework to read a terminal state; they never rewrite factual records.
"""

from __future__ import annotations

from typing import Any, Mapping

VALID_DETECTION_STATES = frozenset({
    "verified_error", "verified_clean", "unresolved",
})


def decision_authority_invalid(row: Mapping[str, Any]) -> bool:
    """A present authority that is not ``authoritative`` fails closed.

    An ABSENT authority is not classified here: the API boundary treats a
    missing authority as a hard error separately.
    """
    proof = row.get("proof_adjudication")
    proof = proof if isinstance(proof, Mapping) else {}
    top = str(row.get("decision_authority") or "").strip().lower()
    inner = str(proof.get("decision_authority") or "").strip().lower()
    return (
        row.get("runtime_valid") is False
        or bool(top and top != "authoritative")
        or bool(inner and inner != "authoritative")
    )


def resolve_authoritative_state(row: Mapping[str, Any]) -> str:
    """Resolve the decision state without rewriting factual occurrence data."""
    proof = row.get("proof_adjudication")
    proof = proof if isinstance(proof, Mapping) else {}
    if decision_authority_invalid(row):
        return "unresolved"
    top_ledger = row.get("occurrence_ledger")
    top_ledger = top_ledger if isinstance(top_ledger, Mapping) else {}
    proof_ledger = proof.get("occurrence_ledger")
    proof_ledger = proof_ledger if isinstance(proof_ledger, Mapping) else {}
    occurrence_candidates = (
        ("occurrence_state", row.get("occurrence_state")),
        ("proof_adjudication.occurrence_state", proof.get("occurrence_state")),
        ("occurrence_ledger.occurrence_state", top_ledger.get("occurrence_state")),
        ("occurrence_ledger.state", top_ledger.get("state")),
        (
            "proof_adjudication.occurrence_ledger.occurrence_state",
            proof_ledger.get("occurrence_state"),
        ),
        (
            "proof_adjudication.occurrence_ledger.state",
            proof_ledger.get("state"),
        ),
    )
    for source, value in occurrence_candidates:
        if value in (None, ""):
            continue
        normalized = str(value)
        if normalized not in VALID_DETECTION_STATES:
            raise ValueError(f"Invalid {source}: {value!r}")
        return normalized
    incident = row.get("incident_state")
    if isinstance(incident, Mapping):
        occurrence = incident.get("occurrence_state")
        if occurrence not in (None, ""):
            normalized = str(occurrence)
            if normalized not in VALID_DETECTION_STATES:
                raise ValueError(
                    f"Invalid incident_state.occurrence_state: {occurrence!r}"
                )
            return normalized
        incident = incident.get("state")
    if incident not in (None, ""):
        normalized = str(incident)
        if normalized not in VALID_DETECTION_STATES:
            raise ValueError(f"Invalid incident_state: {incident!r}")
        return normalized
    state = str(row.get("state") or "")
    if state not in VALID_DETECTION_STATES:
        raise ValueError(f"Invalid detector state: {state!r}")
    return state


def resolve_evolution_state(row: Mapping[str, Any]) -> str:
    """Resolve the terminal state consumed by the evolution loop.

    The evolution loop reads the COURT's terminal verdict, never the
    occurrence ledger (which is an append-only factual record).
    """
    if decision_authority_invalid(row):
        return "unresolved"
    proof = row.get("proof_adjudication")
    proof = proof if isinstance(proof, Mapping) else {}
    for value in (row.get("state"), proof.get("state")):
        if value in (None, ""):
            continue
        normalized = str(value)
        if normalized not in VALID_DETECTION_STATES:
            raise ValueError(f"Invalid evolution terminal state: {value!r}")
        return normalized
    return resolve_authoritative_state(row)


def prediction_state(row: Mapping[str, Any]) -> bool | None:
    """Predicted polarity of a detector row; ``None`` for unresolved."""
    state = resolve_evolution_state(row)
    if state == "verified_error":
        return True
    if state == "verified_clean":
        return False
    return None


def build_detection_output(
    *, case_id: str, packet: Mapping[str, Any], proof: Mapping[str, Any],
    providers: Mapping[str, Any], provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble the frozen detection envelope from the court's proof."""
    state = str(proof.get("state") or "unresolved")
    runtime_valid = bool(proof.get("runtime_valid", True))
    authority = (
        "authoritative" if runtime_valid else "invalid"
    )
    responsible_event_id = proof.get("responsible_event_id")
    responsible_step = _event_step(packet, responsible_event_id)
    proof_out = dict(proof)
    proof_out.pop("_packet_event_count", None)
    output: dict[str, Any] = {
        "schema_version": "sehd2.detection_result.v1",
        "case_id": case_id,
        "runtime_valid": runtime_valid,
        "state": state,
        "decision_authority": authority,
        "incident_state": {"state": state},
        "responsible_event_id": responsible_event_id,
        "responsible_step": responsible_step,
        "occurrence_state": state,
        "incident_occurred": state == "verified_error",
        "occurrence_ledger": {
            "state": state,
            "occurrence_state": state,
            "responsible_event_id": responsible_event_id,
        },
        "proof_adjudication": proof_out,
        "providers": dict(providers),
        "authority_failure_reasons": list(
            proof_out.get("authority_failure_reasons") or []
        ),
        "evidence_health": {
            "candidate_count": int(
                proof_out.get("candidate_count") or 0
            ),
            "certificate_count": int(
                proof_out.get("certificate_count") or 0
            ),
            "error_certificate_count": int(
                proof_out.get("error_certificate_count") or 0
            ),
        },
        "provenance": dict(provenance),
    }
    if not runtime_valid:
        # Fail closed: an invalid run must not carry a decisive prediction.
        output["state"] = "unresolved"
        output["decision_authority"] = "invalid"
        output["responsible_event_id"] = None
        output["responsible_step"] = None
        output["incident_state"] = {"state": "unresolved"}
        output["occurrence_state"] = "unresolved"
        output["incident_occurred"] = None
    return output


def _event_step(packet: Mapping[str, Any], event_id: Any) -> int | None:
    if not event_id:
        return None
    for event in packet.get("events") or []:
        if isinstance(event, Mapping) and event.get("event_id") == event_id:
            return event.get("position")
    return None


__all__ = [
    "VALID_DETECTION_STATES",
    "build_detection_output",
    "decision_authority_invalid",
    "prediction_state",
    "resolve_authoritative_state",
    "resolve_evolution_state",
]
