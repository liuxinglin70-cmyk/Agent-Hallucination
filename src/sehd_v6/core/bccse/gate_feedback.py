"""SEHD v6 — audited gate events and bounded feedback (P5).

A single, append-only GateEvent ledger records every terminal gate decision
(L1 and L2) so the evolution loop is auditable and resumable.  From those
events two strictly separated, prompt-safe projections are derived:

- ``project_same_source_retry``: precise closed-vocabulary state for a retry
  on the SAME source case (may name the exact failing stage);
- ``project_cross_case_guidance``: coarse abstract codes for OTHER cases —
  never exposes thresholds, gold polarity, raw identities, prompts or
  provider responses.

The feedback channel is deliberately one-way and bounded: it can inform a
reflection, but it never changes the owner route, never widens an
ActionSpec, and never relaxes the StructuralGate.  No provider call is added
by this module.

Schema: ``sehd.gate_event.v1``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .promotion_contract import ACTIVATION_STATES, EVIDENCE_GRADES

SCHEMA_VERSION = "sehd.gate_event.v1"

# ============================================================
# Bounded fuzzy feedback closed vocabulary (§11.5)
# ============================================================

FEEDBACK_INSUFFICIENT_GROUNDING = "insufficient_grounding"
FEEDBACK_EXECUTION_NOT_ESTABLISHED = "execution_not_established"
FEEDBACK_INDEPENDENT_REGRESSION_RISK = "independent_regression_risk"
FEEDBACK_SCOPE_OR_OWNERSHIP_MISMATCH = "scope_or_ownership_mismatch"
FEEDBACK_UNSAFE_OR_UNREPRESENTABLE = "unsafe_or_unrepresentable"
FEEDBACK_REDUNDANT_INTERVENTION = "redundant_intervention"
FEEDBACK_POLICY_ROUTE_UNAVAILABLE = "policy_route_unavailable"
FEEDBACK_CANDIDATE_QUALITY_INCONCLUSIVE = "candidate_quality_inconclusive"
FEEDBACK_REGISTRY_ENTRY_PENDING = "registry_entry_pending"
FEEDBACK_DOWNSTREAM_EFFECT_NOT_ESTABLISHED = "downstream_effect_not_established"

FUZZY_FEEDBACK_CODES = frozenset({
    FEEDBACK_INSUFFICIENT_GROUNDING,
    FEEDBACK_EXECUTION_NOT_ESTABLISHED,
    FEEDBACK_INDEPENDENT_REGRESSION_RISK,
    FEEDBACK_SCOPE_OR_OWNERSHIP_MISMATCH,
    FEEDBACK_UNSAFE_OR_UNREPRESENTABLE,
    FEEDBACK_REDUNDANT_INTERVENTION,
    FEEDBACK_POLICY_ROUTE_UNAVAILABLE,
    FEEDBACK_CANDIDATE_QUALITY_INCONCLUSIVE,
    FEEDBACK_REGISTRY_ENTRY_PENDING,
    FEEDBACK_DOWNSTREAM_EFFECT_NOT_ESTABLISHED,
})

# Closed vocabularies for the event trace/delta classification fields.
TRACE_CLASSES = frozenset({
    "compiled", "applied", "fired", "skipped", "no_proposal", "runtime_invalid",
})
DELTA_CLASSES = frozenset({
    "none", "local", "downstream", "terminal", "unknown",
})


# ============================================================
# GateEvent
# ============================================================

@dataclass(frozen=True)
class GateEvent:
    """One audited terminal gate decision.

    ``sequence`` orders the ledger; it never participates in the stable
    identity (``event_id``).  The event is pure telemetry: it records a
    decision that has already been made by the gate, so it can never
    influence that decision.
    """

    schema_version: str
    event_id: str
    subject_id: str
    episode_id: str
    attempt_id: str
    layer: str
    artifact: str
    operation: str
    stage: str
    legacy_decision: str
    evidence_grade: str
    activation_state: str
    hard_reject_codes: tuple[str, ...] = ()
    quality_reason_codes: tuple[str, ...] = ()
    execution_trace_class: str = "no_proposal"
    observable_delta_class: str = "none"
    downstream_delta_class: str = "none"
    sequence: int = 0

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"unknown gate event schema_version: {self.schema_version!r}"
            )
        if self.evidence_grade not in EVIDENCE_GRADES:
            raise ValueError(f"unknown evidence_grade: {self.evidence_grade!r}")
        if self.activation_state and self.activation_state not in ACTIVATION_STATES:
            raise ValueError(
                f"unknown activation_state: {self.activation_state!r}"
            )
        if self.execution_trace_class not in TRACE_CLASSES:
            raise ValueError(
                f"unknown execution_trace_class: {self.execution_trace_class!r}"
            )
        if self.observable_delta_class not in DELTA_CLASSES:
            raise ValueError(
                f"unknown observable_delta_class: {self.observable_delta_class!r}"
            )
        if self.downstream_delta_class not in DELTA_CLASSES:
            raise ValueError(
                f"unknown downstream_delta_class: {self.downstream_delta_class!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "subject_id": self.subject_id,
            "episode_id": self.episode_id,
            "attempt_id": self.attempt_id,
            "layer": self.layer,
            "artifact": self.artifact,
            "operation": self.operation,
            "stage": self.stage,
            "legacy_decision": self.legacy_decision,
            "evidence_grade": self.evidence_grade,
            "activation_state": self.activation_state,
            "hard_reject_codes": list(self.hard_reject_codes),
            "quality_reason_codes": list(self.quality_reason_codes),
            "execution_trace_class": self.execution_trace_class,
            "observable_delta_class": self.observable_delta_class,
            "downstream_delta_class": self.downstream_delta_class,
            "sequence": self.sequence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GateEvent":
        if not isinstance(value, Mapping):
            raise ValueError("gate event must contain an object")
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            event_id=str(value.get("event_id") or ""),
            subject_id=str(value.get("subject_id") or ""),
            episode_id=str(value.get("episode_id") or ""),
            attempt_id=str(value.get("attempt_id") or ""),
            layer=str(value.get("layer") or ""),
            artifact=str(value.get("artifact") or ""),
            operation=str(value.get("operation") or ""),
            stage=str(value.get("stage") or ""),
            legacy_decision=str(value.get("legacy_decision") or ""),
            evidence_grade=str(value.get("evidence_grade") or ""),
            activation_state=str(value.get("activation_state") or ""),
            hard_reject_codes=tuple(
                str(c) for c in (value.get("hard_reject_codes") or ())
            ),
            quality_reason_codes=tuple(
                str(c) for c in (value.get("quality_reason_codes") or ())
            ),
            execution_trace_class=str(
                value.get("execution_trace_class") or "no_proposal"
            ),
            observable_delta_class=str(
                value.get("observable_delta_class") or "none"
            ),
            downstream_delta_class=str(
                value.get("downstream_delta_class") or "none"
            ),
            sequence=int(value.get("sequence") or 0),
        )


def _stable_identity(payload: Mapping[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def gate_event_id(
    *,
    attempt_id: str = "",
    episode_id: str = "",
    decision_kind: str = "",
    observation_id: str = "",
) -> str:
    """Resolve the stable event identity (§11.4).

    Priority: an ``attempt_id`` (proposal present) wins; otherwise the
    episode id plus the decision kind; otherwise a deterministic runtime
    ``observation_id``.  ``sequence`` is never part of the identity.
    """
    if attempt_id:
        return _stable_identity({"kind": "attempt", "attempt_id": attempt_id})
    if episode_id:
        return _stable_identity({
            "kind": "episode_decision",
            "episode_id": episode_id,
            "decision_kind": decision_kind,
        })
    return _stable_identity({"kind": "observation", "observation_id": observation_id})


# ============================================================
# Feedback projections (bounded, prompt-safe)
# ============================================================

def _fuzzy_codes_for(event: GateEvent) -> tuple[str, ...]:
    """Map a gate event's reason codes onto the coarse fuzzy vocabulary.

    Only the closed abstract codes are produced; raw reasons, thresholds,
    gold polarity and identities never appear.  Deterministic and total.
    """
    codes: list[str] = []
    hard = set(event.hard_reject_codes)
    quality = " ".join(event.quality_reason_codes)

    if "ownership_mismatch" in hard or "detector_authority_invalid" in hard:
        codes.append(FEEDBACK_SCOPE_OR_OWNERSHIP_MISMATCH)
    if "gold_or_identity_leakage" in hard or "invalid_schema" in hard:
        codes.append(FEEDBACK_UNSAFE_OR_UNREPRESENTABLE)
    if "canary_regression" in hard or "gold_polarity_regression" in hard:
        codes.append(FEEDBACK_INDEPENDENT_REGRESSION_RISK)
    if "protected_baseline_violation" in hard:
        codes.append(FEEDBACK_POLICY_ROUTE_UNAVAILABLE)

    if "no_effect" in quality or "did_not_move" in quality:
        codes.append(FEEDBACK_DOWNSTREAM_EFFECT_NOT_ESTABLISHED)
    if "execution" in quality or "not_established" in quality:
        codes.append(FEEDBACK_EXECUTION_NOT_ESTABLISHED)
    if "registry" in quality or "pending" in quality:
        codes.append(FEEDBACK_REGISTRY_ENTRY_PENDING)
    if "route_unavailable" in quality or "blocked_by_protection" in quality:
        codes.append(FEEDBACK_POLICY_ROUTE_UNAVAILABLE)
    if "duplicate" in quality or "already_present" in quality:
        codes.append(FEEDBACK_REDUNDANT_INTERVENTION)
    if "grounding" in quality or "insufficient" in quality:
        codes.append(FEEDBACK_INSUFFICIENT_GROUNDING)

    if not codes and event.evidence_grade in ("weak", "conditional"):
        codes.append(FEEDBACK_CANDIDATE_QUALITY_INCONCLUSIVE)

    # Stable, deduped order.
    seen: list[str] = []
    for code in codes:
        if code in FUZZY_FEEDBACK_CODES and code not in seen:
            seen.append(code)
    return tuple(seen)


def project_cross_case_guidance(event: GateEvent) -> dict[str, Any]:
    """Bounded guidance projection for OTHER cases.

    Only the coarse abstract codes are exposed; a cross-case prompt can
    never reverse-engineer thresholds or gold polarity.
    """
    return {
        "kind": "cross_case_guidance",
        "feedback_codes": list(_fuzzy_codes_for(event)),
    }


# ============================================================
# Append-only ledger (resume-idempotent)
# ============================================================

@dataclass
class GateEventLedger:
    """Append-only GateEvent store with idempotent resume.

    An event whose stable ``event_id`` is already recorded is not duplicated,
    so replaying a checkpoint never double-writes the same decision.
    """

    events: list[GateEvent] = field(default_factory=list)
    _ids: set[str] = field(default_factory=set)

    def _next_sequence(self) -> int:
        return (self.events[-1].sequence + 1) if self.events else 1

    def record(self, event: GateEvent) -> bool:
        """Append one event; returns True when it is new (idempotent)."""
        if event.event_id in self._ids:
            return False
        sequenced = GateEvent.from_dict({
            **event.to_dict(),
            "sequence": self._next_sequence(),
        })
        self.events.append(sequenced)
        self._ids.add(event.event_id)
        return True

    def drop_event(self, event_id: str) -> bool:
        """Remove one recorded event; returns True when it was present.

        Resume rewind uses this to purge ledger events of placeholder
        steps that were popped back into the work queue; a re-recorded
        decision must not coexist with the phantom "halted" entry.
        """
        if event_id not in self._ids:
            return False
        self.events = [
            event for event in self.events if event.event_id != event_id
        ]
        self._ids.discard(event_id)
        return True

    def to_list(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.events]

    @classmethod
    def from_list(cls, rows: Sequence[Mapping[str, Any]]) -> "GateEventLedger":
        """Rebuild the ledger from persisted rows.  Fail-closed: a malformed
        or schema-unknown row raises instead of being silently dropped, so a
        resume never loses an audited decision."""
        ledger = cls()
        for row in rows or ():
            if not isinstance(row, Mapping):
                raise ValueError("gate event row must be an object")
            event = GateEvent.from_dict(row)
            ledger.events.append(event)
            ledger._ids.add(event.event_id)
        return ledger
