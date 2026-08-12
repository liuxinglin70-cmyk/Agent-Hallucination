"""SEHD-RISE loop audited gate events and the single projection authority.

One append-only ``GateEvent`` ledger records every terminal gate decision
(L1 and L2) so the evolution loop is auditable and resumable. Two strictly
separated, prompt-safe projections are derived from those events: a precise
closed-vocabulary trace for a same-source retry and a coarse fuzzy-code
projection for other cases. The feedback channel is one-way and bounded: it
can inform a reflection but never change an owner route, widen an
ActionSpec, or relax the StructuralGate.

This module is also the single projection authority: ``_build_gate_event_ledger``
deterministically rebuilds the ledger from the final steps list, so both
checkpoint writes and the Meta Review read the same per-step final state
(including in-place commits from ``provisional`` to an accepted outcome).
Resume stays idempotent because the projection is a pure function of the
steps list.

Schema: ``sehd.gate_event.v1``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

from sehd_rise.causal.episode import (
    CausalFailureEpisode,
    FailureAttempt,
    attempt_id_for,
    proposal_signature_for,
)
from sehd_rise.evaluation.promotion import (
    ACTIVATION_STATES,
    AUTHORITY_L1_FITNESS,
    AUTHORITY_L2_POLICY,
    EVIDENCE_GRADES,
    PromotionEvidence,
    promote,
)
from sehd_rise.loop.machine import Outcome
from sehd_rise.loop.result import EvolutionStep
from sehd_rise.loop.state import l2_promotion_evidence
from sehd_rise.meta.review import (
    FEEDBACK_CANDIDATE_QUALITY_INCONCLUSIVE,
    FEEDBACK_DOWNSTREAM_EFFECT_NOT_ESTABLISHED,
    FEEDBACK_EXECUTION_NOT_ESTABLISHED,
    FEEDBACK_INDEPENDENT_REGRESSION_RISK,
    FEEDBACK_INSUFFICIENT_GROUNDING,
    FEEDBACK_POLICY_ROUTE_UNAVAILABLE,
    FEEDBACK_REDUNDANT_INTERVENTION,
    FEEDBACK_REGISTRY_ENTRY_PENDING,
    FEEDBACK_SCOPE_OR_OWNERSHIP_MISMATCH,
    FEEDBACK_UNSAFE_OR_UNREPRESENTABLE,
    FUZZY_FEEDBACK_CODES,
    GateExperience,
)
from sehd_rise.skills.vocabulary.contracts import LAYER_L2

SCHEMA_VERSION = "sehd.gate_event.v1"

# Closed vocabularies for the event trace/delta classification fields.
TRACE_CLASSES = frozenset({
    "compiled", "applied", "fired", "skipped", "no_proposal", "runtime_invalid",
})
DELTA_CLASSES = frozenset({
    "none", "local", "downstream", "terminal", "unknown",
})


@dataclass(frozen=True)
class GateEvent:
    """One audited terminal gate decision.

    ``sequence`` orders the ledger; it never participates in the stable
    identity (``event_id``). The event is pure telemetry: it records a
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
    gate_decision: str
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
            "gate_decision": self.gate_decision,
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
            gate_decision=str(value.get("gate_decision") or ""),
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
    """Resolve the stable event identity.

    Priority: an ``attempt_id`` (proposal present) wins; otherwise the
    episode id plus the decision kind; otherwise a deterministic runtime
    ``observation_id``. ``sequence`` is never part of the identity.
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


def _fuzzy_codes_for(event: GateEvent) -> tuple[str, ...]:
    """Map a gate event's reason codes onto the coarse fuzzy vocabulary.

    Only the closed abstract codes are produced; raw reasons, thresholds,
    gold polarity and identities never appear. Deterministic and total.
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


@dataclass
class GateEventLedger:
    """Append-only GateEvent store with idempotent resume.

    An event whose stable ``event_id`` is already recorded is not
    duplicated, so replaying a checkpoint never double-writes the same
    decision.
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

        Resume rewind uses this to purge ledger events of placeholder steps
        that were popped back into the work queue; a re-recorded decision
        must not coexist with the phantom entry.
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
        """Rebuild the ledger from persisted rows. Fail-closed: a malformed,
        schema-unknown or duplicated row raises instead of being silently
        dropped, so a resume never loses or double-records an audited
        decision."""
        ledger = cls()
        for row in rows or ():
            if not isinstance(row, Mapping):
                raise ValueError("gate event row must be an object")
            event = GateEvent.from_dict(row)
            if event.event_id in ledger._ids:
                raise ValueError(
                    f"duplicated gate event id in persisted rows: "
                    f"{event.event_id!r}"
                )
            ledger.events.append(event)
            ledger._ids.add(event.event_id)
        return ledger


def aggregate_gate_experience(
    event: GateEvent,
    *,
    mechanism_signature: Sequence[str] = (),
    sequence: int = 0,
) -> GateExperience:
    """Aggregate one audited GateEvent into its GateExperience.

    When no explicit ``mechanism_signature`` is supplied, one is derived from
    the event's stable structural fields (layer/artifact/stage/operation) so
    the Meta Review can attribute outcomes to a mechanism class instead of a
    bare sequence of decisions. Identities and case ids never participate.
    """
    guidance = project_cross_case_guidance(event)
    signature = tuple(str(v) for v in mechanism_signature) or (
        event.layer,
        event.artifact,
        event.stage,
        event.operation,
    )
    return GateExperience(
        gate_event_id=event.event_id,
        mechanism_signature=signature,
        layer=event.layer,
        artifact=event.artifact,
        stage=event.stage,
        evidence_grade=event.evidence_grade,
        activation_state=event.activation_state,
        feedback_codes=tuple(guidance["feedback_codes"]),
        execution_trace_class=event.execution_trace_class,
        sequence=sequence,
    )


def _project_execution_trace(
    step: EvolutionStep, *,
    final_attempt: Mapping[str, Any] | None,
    l2_policy: Mapping[str, Any] | None,
) -> str:
    """Project a step's execution outcome onto the TRACE_CLASSES vocabulary.

    Deterministic and total: a runtime/infra failure maps to
    ``runtime_invalid``; proposal-less steps map to ``no_proposal``; L2
    proposals resolve to ``fired``/``skipped`` from the replay's
    policy_evaluation; L1 proposals fall back to the final attempt's
    compiled/applied flags.
    """
    decision = str(step.gate_decision or "")
    if decision == Outcome.INFRA_FAILURE.value:
        return "runtime_invalid"
    proposal = step.edit_proposal if isinstance(step.edit_proposal, Mapping) else {}
    if not proposal.get("operation"):
        return "no_proposal"
    if l2_policy is not None:
        policy_eval = l2_policy.get("policy_evaluation") or {}
        new_item = proposal.get("new_item")
        proposal_item_id = (
            str(new_item.get("item_id") or "")
            if isinstance(new_item, Mapping) else ""
        )
        if proposal_item_id:
            for row in (policy_eval.get("fired") or ()):
                if isinstance(row, Mapping) and str(
                    row.get("item_id") or ""
                ) == proposal_item_id:
                    return "fired"
            for row in (policy_eval.get("skipped") or ()):
                if isinstance(row, Mapping) and str(
                    row.get("item_id") or ""
                ) == proposal_item_id:
                    return "skipped"
    if isinstance(final_attempt, Mapping):
        if final_attempt.get("applied"):
            return "applied"
        if final_attempt.get("compiled"):
            return "compiled"
    return "no_proposal"


def _project_delta_class(
    deltas: Sequence[Any], *, downstream: bool,
) -> str:
    """Project observable/court delta tuples onto the DELTA_CLASSES vocabulary.

    A delta containing a terminal-state transition (``state:`` or ``->``)
    maps to ``terminal``; any other non-empty delta maps to ``local``
    (observable) or ``downstream`` (court); empty maps to ``none``.
    """
    values = [str(d) for d in deltas or () if str(d)]
    if any("state:" in d or "->" in d for d in values):
        return "terminal"
    if values:
        return "downstream" if downstream else "local"
    return "none"


def _project_gate_event(
    step: EvolutionStep, *, step_ordinal: int,
) -> GateEvent | None:
    """Project one evolution step into its audited GateEvent.

    Pure telemetry: the event records a decision the gate has already made,
    so it can never influence that decision. Reads the step's final state —
    including any in-place commit from ``provisional`` to an accepted
    outcome by ``flush_shadow_batch`` — so the event reflects the final
    decision, not the provisional one captured at step time.

    Identity follows proposal authority: a step that made a proposal is
    keyed by its ``attempt_id`` (or ``episode_id`` when no attempt landed);
    proposal-less placeholder steps fall back to the observation slot,
    which resume rewind uses to drop phantom events.
    """
    episode_id = ""
    attempt_id = ""
    final_attempt: Mapping[str, Any] | None = None
    if isinstance(step.episode, Mapping):
        episode_id = str(step.episode.get("episode_id") or "")
        attempts = step.episode.get("attempts") or ()
        if attempts and isinstance(attempts[-1], Mapping):
            attempt_id = str(attempts[-1].get("attempt_id") or "")
            final_attempt = attempts[-1]
    proposal = step.edit_proposal if isinstance(step.edit_proposal, Mapping) else {}
    operation = str(proposal.get("operation") or "")
    attribution = step.attribution if isinstance(step.attribution, Mapping) else {}
    layer = str(attribution.get("layer") or "")
    # L1 proposals name their target_module; L2 proposals (PolicyEdit) carry
    # no target_module, so the layer attribution's artifact is the L2 event's
    # artifact — without it, promotion grades an artifact-less L2 decision.
    artifact = str(proposal.get("target_module") or "")
    if not artifact:
        artifact = str(attribution.get("artifact") or "")
    # The promotion contract needs the actual gate reasons to classify hard
    # reject codes; every step records them under its own fitness_check keys,
    # so they are aggregated here from the deterministic authority fields
    # instead of one optional key that no construction site ever wrote.
    fitness = step.fitness_check if isinstance(step.fitness_check, Mapping) else {}
    reasons: list[str] = []
    for key in ("reject_reasons", "retry_reason", "runtime_error",
                "mutation_error", "rollback_reason"):
        value = fitness.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            reasons.extend(str(item) for item in value)
        elif str(value):
            reasons.append(str(value))
    assessment = fitness.get("assessment")
    if isinstance(assessment, Mapping):
        reasons.extend(
            str(item)
            for item in (assessment.get("reasons") or ())
            if str(item)
        )
    l2 = fitness.get("l2_policy")
    l2_policy = l2 if isinstance(l2, Mapping) else None
    l2_evidence: PromotionEvidence | None = None
    if l2_policy is not None:
        if str(l2_policy.get("reason") or ""):
            reasons.append(str(l2_policy["reason"]))
        # The L2 audit's closed-vocabulary feedback code must reach the fuzzy
        # evidence mapping, or the Meta loop's evidence gate can never observe
        # the L2 outcomes it was built for.
        if str(l2_policy.get("feedback_code") or ""):
            reasons.append(str(l2_policy["feedback_code"]))
        # The L2 accept path already validated the evidence: it only reaches
        # ACCEPTED when the proposed item fired, the terminal state changed,
        # the gold task score improved, and no canary regressed. Package those
        # measured facts into PromotionEvidence so the grade reflects the
        # actual replay outcome instead of defaulting to weak.
        l2_evidence = l2_promotion_evidence(l2_policy, step)
    try:
        decision = promote(
            layer=layer or "l1",
            artifact=artifact,
            gate_decision=step.gate_decision,
            decision_authority=(
                AUTHORITY_L2_POLICY if layer == LAYER_L2
                else AUTHORITY_L1_FITNESS
            ),
            reasons=reasons,
            evidence=l2_evidence,
        )
    except ValueError:
        # A non-gate step (for example an interim marker) is still audited,
        # with an empty grade; it never reaches the promotion vocabulary.
        decision = None
    has_proposal = bool(operation)
    observation_id = f"{step.case_id}:{step.case_index}:{step_ordinal}"
    if has_proposal and attempt_id:
        event_id = gate_event_id(attempt_id=attempt_id)
    elif has_proposal and episode_id:
        event_id = gate_event_id(
            episode_id=episode_id,
            decision_kind=str(step.gate_decision or ""),
        )
    else:
        event_id = gate_event_id(observation_id=observation_id)
    try:
        return GateEvent(
            schema_version=SCHEMA_VERSION,
            event_id=event_id,
            subject_id=str(step.case_id),
            episode_id=episode_id,
            attempt_id=attempt_id,
            layer=layer,
            artifact=artifact,
            operation=operation,
            stage=str(step.trigger_source or ""),
            gate_decision=str(step.gate_decision or ""),
            evidence_grade=(decision.evidence_grade if decision else "skip"),
            activation_state=(decision.activation_state if decision else ""),
            hard_reject_codes=(decision.hard_reject_codes if decision else ()),
            quality_reason_codes=(decision.quality_reason_codes if decision else ()),
            execution_trace_class=_project_execution_trace(
                step, final_attempt=final_attempt, l2_policy=l2_policy,
            ),
            observable_delta_class=_project_delta_class(
                (final_attempt.get("observable_delta") or ())
                if final_attempt else (),
                downstream=False,
            ),
            downstream_delta_class=_project_delta_class(
                (final_attempt.get("downstream_court_delta") or ())
                if final_attempt else (),
                downstream=True,
            ),
        )
    except ValueError:
        return None


def _build_gate_event_ledger(
    steps: Sequence[EvolutionStep],
) -> GateEventLedger:
    """Deterministically rebuild the gate-event ledger from final steps.

    Single projection authority: both checkpoint writes and the Meta Review
    read from this rebuild, so the persisted and consumed ledger always
    reflects each step's final state — including in-place commits from a
    provisional marker to an accepted outcome — instead of the stale snapshot
    captured at record time. Resume stays idempotent because the projection
    is a pure function of the steps list: the same steps always yield the
    same event identities and sequencing.
    """
    ledger = GateEventLedger()
    for index, step in enumerate(steps):
        event = _project_gate_event(step, step_ordinal=index + 1)
        if event is not None:
            ledger.record(event)
    return ledger


def _record_gate_event(
    step: EvolutionStep, *,
    gate_event_ledger: GateEventLedger, steps: list[Any],
) -> None:
    """Append one step's gate event to the live (in-memory) ledger.

    The live ledger is an incremental buffer used by resume rewind; the
    authoritative projection is ``_build_gate_event_ledger``, which
    checkpoint writes and the finalize path read instead. Recording here
    keeps the live ledger current for the resume ``drop_event`` path.
    """
    event = _project_gate_event(step, step_ordinal=len(steps))
    if event is not None:
        gate_event_ledger.record(event)


def _record_attempt(
    *, active_episode_payload: dict[str, Any] | None, causal_memory: Any,
    decision: str,
    operation: str = "",
    target_module: str = "",
    action_spec: Mapping[str, Any] | None = None,
    structural_result: str = "accepted",
    compiled: bool = False,
    injected: bool = False,
    applied: bool = False,
    observable_delta: Sequence[str] = (),
    downstream_court_delta: Sequence[str] = (),
    no_effect_reason: str = "",
    proposal_content: str = "",
) -> dict[str, Any] | None:
    """Append one execution outcome to the current episode.

    Returns the updated episode payload, or the input unchanged when there
    is no active episode (caller keeps the prior payload).
    """
    if (
        active_episode_payload is None
        or "episode_id" not in active_episode_payload
    ):
        return active_episode_payload
    episode = CausalFailureEpisode.from_dict(active_episode_payload)
    proposal = proposal_signature_for(
        operation=operation,
        target_module=target_module,
        action_spec=action_spec,
        proposal_content=proposal_content,
    )
    attempt = FailureAttempt(
        attempt_id=attempt_id_for(
            episode.episode_id, proposal, len(episode.attempts),
        ),
        proposal_signature=proposal,
        operation=operation,
        target_module=target_module,
        action_spec=dict(action_spec or {}),
        structural_result=structural_result,
        compiled=compiled,
        injected=injected,
        applied=applied,
        observable_delta=tuple(str(value) for value in observable_delta),
        downstream_court_delta=tuple(
            str(value) for value in downstream_court_delta
        ),
        no_effect_reason=no_effect_reason,
        decision=decision,
        proposal_content=" ".join(str(proposal_content).split()),
    )
    updated = replace(episode, attempts=(*episode.attempts, attempt))
    causal_memory.upsert_attempt(updated, attempt)
    return updated.to_dict()
