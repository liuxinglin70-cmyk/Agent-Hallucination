"""Fail-closed authority reading of detector results.

Extracted from the old ``sehd_rise.causal.signals`` helpers so the frozen
detector package can validate its own replay results without importing any
evolution layer. The future evolution framework re-derives all causal
semantics on top of this vocabulary; this module only reads the detector's
own output.

Reading rules (frozen):
- ``decision_authority_invalid``: a present authority that is not
  ``authoritative`` (or ``runtime_valid is False``) fails closed; an ABSENT
  authority is not classified here (see ``missing_decision_authority``).
- ``resolve_authoritative_state``: invalid authority exposes ``unresolved``
  to downstream consumers without rewriting the factual occurrence ledger.
- ``resolve_evolution_state``: the court's terminal verdict (not the
  occurrence ledger) is the evolution-facing terminal state.
"""

from __future__ import annotations

from typing import Any, Mapping

VALID_DETECTION_STATES = frozenset({
    "verified_error", "verified_clean", "unresolved",
})

# Earlier vocabulary occasionally carried verified_hallucination /
# verified_incident / verified_non_hallucination; map them through the same
# normalizer so resume/replay of historical payloads cannot crash here.
_LEGACY_INCIDENT_STATE_MAP = {
    "verified_error": "verified_error",
    "verified_hallucination": "verified_error",
    "verified_incident": "verified_error",
    "verified_clean": "verified_clean",
    "verified_non_hallucination": "verified_clean",
    "no_verified_incident": "verified_clean",
    "unresolved": "unresolved",
}


def decision_authority_invalid(row: Mapping[str, Any]) -> bool:
    """Whether a detector result is barred from authoritative decisions.

    A present authority that is not ``authoritative`` fails closed. An ABSENT
    authority is not classified here: the replay boundary treats a missing
    authority as a hard error, and ``resolve_authoritative_state`` needs to
    distinguish "no authority recorded" from "authority rejected".
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


def missing_decision_authority(row: Mapping[str, Any]) -> bool:
    """Whether the row carries no top-level decision authority at all."""
    return not str(row.get("decision_authority") or "").strip()


def has_explicit_authority(row: Mapping[str, Any]) -> bool:
    """Whether the row carries the authoritative envelope required for scoring."""
    proof = row.get("proof_adjudication")
    proof = proof if isinstance(proof, Mapping) else {}
    top = str(row.get("decision_authority") or "").strip().lower()
    inner = str(proof.get("decision_authority") or "").strip().lower()
    return top == "authoritative" and (not inner or inner == "authoritative")


def resolve_authoritative_state(row: Mapping[str, Any]) -> str:
    """Resolve the decision state without rewriting factual occurrence data.

    Invalid decision authority exposes ``unresolved`` to attribution and
    promotion consumers even if the occurrence ledger records an error. The
    original row/ledger stays untouched for incident audit.
    """
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
        incident = incident.get("state") or incident.get("verdict")
    if incident not in (None, ""):
        normalized = _LEGACY_INCIDENT_STATE_MAP.get(str(incident))
        if normalized is None:
            raise ValueError(f"Invalid incident_state: {incident!r}")
        return normalized

    legacy_state = str(row.get("state") or "")
    if legacy_state not in VALID_DETECTION_STATES:
        normalized_legacy = _LEGACY_INCIDENT_STATE_MAP.get(legacy_state)
        if normalized_legacy is not None:
            return normalized_legacy
        raise ValueError(f"Invalid detector state: {legacy_state!r}")
    return legacy_state


def resolve_evolution_state(row: Mapping[str, Any]) -> str:
    """Resolve the terminal state consumed by the evolution loop.

    The evolution loop reads the COURT's terminal verdict, not the occurrence
    ledger. The ledger is an append-only factual record that intentionally
    stays unchanged when an L2 policy alters the court's terminal edge.
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
    # Terminal fields absent (historical/partial payloads): fall back to the
    # occurrence-first projection, which is at least a decisive reading.
    return resolve_authoritative_state(row)


def prediction_state(row: Mapping[str, Any]) -> bool | None:
    """Predicted polarity of a detector row; ``None`` for unresolved."""
    state = resolve_evolution_state(row)
    if state == "verified_error":
        return True
    if state == "verified_clean":
        return False
    return None


__all__ = [
    "VALID_DETECTION_STATES",
    "decision_authority_invalid",
    "has_explicit_authority",
    "missing_decision_authority",
    "prediction_state",
    "resolve_authoritative_state",
    "resolve_evolution_state",
]
