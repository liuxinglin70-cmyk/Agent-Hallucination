"""SEHD v6 — Meta Review aggregation, validation, guidance and drift (P7).

Closes the Meta loop over the audited gate events:

    GateExperience + OutcomeValidation
      -> deterministic MetaEdit candidates
      -> shadow recommendation
      -> validation
      -> commit / rollback

Everything here is deterministic and prompt-safe.  The single prompt
projection (``project_meta_review_for_prompt``) is bounded, canonical,
closed-vocabulary and free of identity, gold, raw trajectories and provider
responses; it always carries ``authority="evolution_guidance_only"`` so it
can never widen an ActionSpec, change an owner route, or relax the
StructuralGate.  Meta never enlarges a candidate set — it only re-ranks or
guides within the candidates the planner already produced.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .gate_feedback import (
    FUZZY_FEEDBACK_CODES,
    GateEvent,
    project_cross_case_guidance,
)
from .meta_skill import (
    GUIDANCE_TOKENS,
    RANKING_PREFERENCES,
    MetaEdit,
    MetaSkillState,
    OP_ADD_GUIDANCE,
    OP_ENABLE_RANKING,
)

SCHEMA_VERSION = "sehd.meta_review.v1"

# ============================================================
# OutcomeValidation closed vocabulary (§13.8)
# ============================================================

OUTCOME_SUPPORTS_ACCEPT = "supports_accept"
OUTCOME_CONTRADICTS_ACCEPT = "contradicts_accept"
OUTCOME_SUPPORTS_REJECT = "supports_reject"
OUTCOME_EXACT_FALSE_REJECT = "exact_false_reject"
OUTCOME_GATE_CLASS_COUNTEREVIDENCE = "gate_class_counterevidence"
OUTCOME_INCONCLUSIVE = "inconclusive"
OUTCOME_VALIDATIONS = frozenset({
    OUTCOME_SUPPORTS_ACCEPT,
    OUTCOME_CONTRADICTS_ACCEPT,
    OUTCOME_SUPPORTS_REJECT,
    OUTCOME_EXACT_FALSE_REJECT,
    OUTCOME_GATE_CLASS_COUNTEREVIDENCE,
    OUTCOME_INCONCLUSIVE,
})

# Follow-up status for a GateExperience (closed).
FOLLOW_UP_PENDING = "pending"
FOLLOW_UP_VALIDATED = "validated"
FOLLOW_UP_SUPERSEDED = "superseded"
FOLLOW_UP_STATUSES = frozenset({
    FOLLOW_UP_PENDING, FOLLOW_UP_VALIDATED, FOLLOW_UP_SUPERSEDED,
})


# ============================================================
# GateExperience (§13.7)
# ============================================================

@dataclass(frozen=True)
class GateExperience:
    """An aggregated, mechanism-keyed view of one audited gate event."""

    gate_event_id: str
    mechanism_signature: tuple[str, ...]
    layer: str
    artifact: str
    stage: str
    evidence_grade: str
    activation_state: str
    feedback_codes: tuple[str, ...]
    execution_trace_class: str
    follow_up_status: str = FOLLOW_UP_PENDING
    validation_source: str = ""
    sequence: int = 0

    def __post_init__(self) -> None:
        if self.follow_up_status not in FOLLOW_UP_STATUSES:
            object.__setattr__(self, "follow_up_status", FOLLOW_UP_PENDING)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_event_id": self.gate_event_id,
            "mechanism_signature": list(self.mechanism_signature),
            "layer": self.layer,
            "artifact": self.artifact,
            "stage": self.stage,
            "evidence_grade": self.evidence_grade,
            "activation_state": self.activation_state,
            "feedback_codes": list(self.feedback_codes),
            "execution_trace_class": self.execution_trace_class,
            "follow_up_status": self.follow_up_status,
            "validation_source": self.validation_source,
            "sequence": self.sequence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GateExperience":
        if not isinstance(value, Mapping):
            raise ValueError("gate experience must contain an object")
        return cls(
            gate_event_id=str(value.get("gate_event_id") or ""),
            mechanism_signature=tuple(
                str(v) for v in (value.get("mechanism_signature") or ())
            ),
            layer=str(value.get("layer") or ""),
            artifact=str(value.get("artifact") or ""),
            stage=str(value.get("stage") or ""),
            evidence_grade=str(value.get("evidence_grade") or ""),
            activation_state=str(value.get("activation_state") or ""),
            feedback_codes=tuple(
                str(c) for c in (value.get("feedback_codes") or ())
                if str(c) in FUZZY_FEEDBACK_CODES
            ),
            execution_trace_class=str(value.get("execution_trace_class") or ""),
            follow_up_status=str(value.get("follow_up_status") or FOLLOW_UP_PENDING),
            validation_source=str(value.get("validation_source") or ""),
            sequence=int(value.get("sequence") or 0),
        )


def aggregate_gate_experience(
    event: GateEvent,
    *,
    mechanism_signature: Sequence[str] = (),
    sequence: int = 0,
) -> GateExperience:
    """Aggregate one audited GateEvent into its GateExperience (P7.1).

    When no explicit ``mechanism_signature`` is supplied, one is derived from
    the event's stable structural fields (layer/artifact/stage/operation) so
    the Meta Review can attribute outcomes to a mechanism class instead of a
    bare sequence of decisions.  Identities and case ids never participate.
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


# ============================================================
# OutcomeValidation (§13.8)
# ============================================================

@dataclass(frozen=True)
class OutcomeValidation:
    """The validated follow-up outcome of a gate decision.

    ``exact_false_reject`` is the strongest claim and is heavily gated (see
    ``classify_outcome``); ``gate_class_counterevidence`` only ever flags a
    gate CLASS as possibly too strict — it never proves the original
    proposal was wrongly rejected.
    """

    outcome: str
    gate_event_id: str
    validation_event_id: str = ""
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOME_VALIDATIONS:
            raise ValueError(f"unknown outcome validation: {self.outcome!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "gate_event_id": self.gate_event_id,
            "validation_event_id": self.validation_event_id,
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "OutcomeValidation":
        if not isinstance(value, Mapping):
            raise ValueError("outcome validation must contain an object")
        return cls(
            outcome=str(value.get("outcome") or ""),
            gate_event_id=str(value.get("gate_event_id") or ""),
            validation_event_id=str(value.get("validation_event_id") or ""),
            evidence=tuple(str(v) for v in (value.get("evidence") or ())),
        )


def classify_outcome(
    *,
    gate_event_id: str,
    validation_event_id: str,
    same_canonical_intervention: bool,
    compatible_discriminator: bool,
    independent_validation: bool,
    positive_effect: bool,
    no_regression: bool,
    same_mechanism: bool,
    forbidden_source: bool = False,
) -> OutcomeValidation:
    """Classify one validation follow-up (§13.8).

    ``exact_false_reject`` requires every condition: same canonical
    intervention, a compatible condition discriminator, an independent
    development validation case/probe, a positive effect, no regression, and
    an idempotent validation event.  Forbidden evidence sources (same-source
    retry, same-prompt resampling, telemetry-only, low acceptance rate, model
    rationale, final-holdout writeback) can never establish a false reject.
    """
    if forbidden_source:
        return OutcomeValidation(
            outcome=OUTCOME_INCONCLUSIVE,
            gate_event_id=gate_event_id,
            validation_event_id=validation_event_id,
        )
    exact = (
        same_canonical_intervention
        and compatible_discriminator
        and independent_validation
        and positive_effect
        and no_regression
        and bool(validation_event_id)
    )
    if exact:
        return OutcomeValidation(
            outcome=OUTCOME_EXACT_FALSE_REJECT,
            gate_event_id=gate_event_id,
            validation_event_id=validation_event_id,
            evidence=("independent_validation", "positive_effect", "no_regression"),
        )
    if same_mechanism and not same_canonical_intervention and positive_effect:
        # Same mechanism, different intervention: a gate CLASS may be too
        # strict, but the original rejection is not disproven.
        return OutcomeValidation(
            outcome=OUTCOME_GATE_CLASS_COUNTEREVIDENCE,
            gate_event_id=gate_event_id,
            validation_event_id=validation_event_id,
            evidence=("same_mechanism",),
        )
    if positive_effect and no_regression:
        return OutcomeValidation(
            outcome=OUTCOME_SUPPORTS_ACCEPT,
            gate_event_id=gate_event_id,
            validation_event_id=validation_event_id,
        )
    if not positive_effect:
        return OutcomeValidation(
            outcome=OUTCOME_SUPPORTS_REJECT,
            gate_event_id=gate_event_id,
            validation_event_id=validation_event_id,
        )
    return OutcomeValidation(
        outcome=OUTCOME_INCONCLUSIVE,
        gate_event_id=gate_event_id,
        validation_event_id=validation_event_id,
    )


# ============================================================
# Deterministic MetaEdit candidate generation (§13.9)
# ============================================================

def generate_meta_edit_candidates(
    experiences: Sequence[GateExperience],
    validations: Sequence[OutcomeValidation],
    state: MetaSkillState,
) -> list[MetaEdit]:
    """Enumerate deterministic MetaEdit candidates from aggregated evidence.

    First version is deliberately conservative: each candidate maps one
    recurring feedback pattern to one closed-vocabulary guidance token or
    ranking preference.  No candidate ever touches the hard gate, a numeric
    threshold or a verdict.  Candidates the state already satisfies are not
    re-proposed.
    """
    candidates: list[MetaEdit] = []
    counter = [0]

    def _next_id(operation: str, target: str) -> str:
        counter[0] += 1
        digest = hashlib.sha256(
            f"{operation}:{target}:{counter[0]}".encode("utf-8")
        ).hexdigest()[:10]
        return f"meta_edit_{digest}"

    feedback_counts: dict[str, int] = {}
    for experience in experiences:
        for code in experience.feedback_codes:
            feedback_counts[code] = feedback_counts.get(code, 0) + 1

    # Recurring route-unavailability -> prefer a registered runtime route.
    if (
        feedback_counts.get("policy_route_unavailable", 0) >= 1
        and "prefer_registered_runtime_route" not in state.guidance_tokens
    ):
        candidates.append(MetaEdit(
            edit_id=_next_id(OP_ADD_GUIDANCE, "prefer_registered_runtime_route"),
            operation=OP_ADD_GUIDANCE,
            target="prefer_registered_runtime_route",
            rationale="recurring policy_route_unavailable",
        ))
    # Recurring pending-registry dependency -> avoid pending dependencies.
    if (
        feedback_counts.get("registry_entry_pending", 0) >= 1
        and "avoid_pending_registry_dependency" not in state.guidance_tokens
    ):
        candidates.append(MetaEdit(
            edit_id=_next_id(OP_ADD_GUIDANCE, "avoid_pending_registry_dependency"),
            operation=OP_ADD_GUIDANCE,
            target="avoid_pending_registry_dependency",
            rationale="recurring registry_entry_pending",
        ))
    # An exact false reject on a mechanism -> enable the effectiveness
    # ranking signal so similar candidates rank higher next time.
    has_exact_false_reject = any(
        validation.outcome == OUTCOME_EXACT_FALSE_REJECT
        for validation in validations
    )
    if (
        has_exact_false_reject
        and "action_shape_previously_effective" not in state.ranking_preferences
    ):
        candidates.append(MetaEdit(
            edit_id=_next_id(OP_ENABLE_RANKING, "action_shape_previously_effective"),
            operation=OP_ENABLE_RANKING,
            target="action_shape_previously_effective",
            rationale="exact_false_reject evidence",
        ))
    return candidates


# ============================================================
# Prompt projection (§13.12) — the single authority
# ============================================================

def project_meta_review_for_prompt(
    state: MetaSkillState,
    *,
    max_tokens: int = 8,
) -> dict[str, Any]:
    """Bounded, canonical, prompt-safe Meta projection.

    Closed vocabulary only; identity-free, gold-free, raw-trajectory-free,
    provider-response-free.  Always ``authority="evolution_guidance_only"``
    so the guidance can inform a reflection but never change an owner route,
    an ActionSpec or the StructuralGate.
    """
    tokens = sorted(state.guidance_tokens)[: max(0, int(max_tokens))]
    return {
        "authority": "evolution_guidance_only",
        "gate_profile": state.gate_profile,
        "guidance_tokens": [
            token for token in tokens if token in GUIDANCE_TOKENS
        ],
        "ranking_preferences": [
            pref for pref in sorted(state.ranking_preferences)
            if pref in RANKING_PREFERENCES
        ],
    }


def rank_candidates(
    candidate_ids: Sequence[str],
    features: Mapping[str, Mapping[str, bool]],
    state: MetaSkillState,
) -> list[str]:
    """Re-rank (never enlarge) a candidate set using the enabled preferences.

    ``features`` maps candidate_id -> {preference_name: bool}.  Only
    preferences enabled in the state contribute; the ordering is
    deterministic (match count desc, then candidate_id asc) and returns the
    SAME set of ids, only reordered.
    """
    enabled = [p for p in state.ranking_preferences if p in RANKING_PREFERENCES]

    def score(cid: str) -> tuple[int, str]:
        row = features.get(cid, {})
        matches = sum(1 for p in enabled if bool(row.get(p)))
        return (-matches, cid)

    return sorted((str(c) for c in candidate_ids), key=score)


# ============================================================
# DriftSnapshot (§13.16)
# ============================================================

@dataclass(frozen=True)
class DriftSnapshot:
    """A reporting-only drift summary.  The composite index is for reports
    only and is NEVER a black-box promotion authority."""

    feedback_code_distribution: Mapping[str, int] = field(default_factory=dict)
    evidence_grade_distribution: Mapping[str, int] = field(default_factory=dict)
    activation_state_distribution: Mapping[str, int] = field(default_factory=dict)
    false_reject_count: int = 0
    unsupported_accept_count: int = 0
    runtime_invalid_rate: float = 0.0
    attempts_to_accept: float = 0.0
    mutation_calls_per_supported_edit: float = 0.0
    registry_size: int = 0
    new_registration_rate: float = 0.0
    unknown_vocabulary_skip_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback_code_distribution": dict(self.feedback_code_distribution),
            "evidence_grade_distribution": dict(self.evidence_grade_distribution),
            "activation_state_distribution": dict(self.activation_state_distribution),
            "false_reject_count": self.false_reject_count,
            "unsupported_accept_count": self.unsupported_accept_count,
            "runtime_invalid_rate": self.runtime_invalid_rate,
            "attempts_to_accept": self.attempts_to_accept,
            "mutation_calls_per_supported_edit": self.mutation_calls_per_supported_edit,
            "registry_size": self.registry_size,
            "new_registration_rate": self.new_registration_rate,
            "unknown_vocabulary_skip_rate": self.unknown_vocabulary_skip_rate,
        }


def build_drift_snapshot(
    experiences: Sequence[GateExperience],
    validations: Sequence[OutcomeValidation],
    *,
    registry_size: int = 0,
) -> DriftSnapshot:
    """Aggregate a reporting-only drift snapshot from experiences."""
    feedback: dict[str, int] = {}
    grades: dict[str, int] = {}
    activations: dict[str, int] = {}
    for experience in experiences:
        grades[experience.evidence_grade] = grades.get(experience.evidence_grade, 0) + 1
        activations[experience.activation_state] = (
            activations.get(experience.activation_state, 0) + 1
        )
        for code in experience.feedback_codes:
            feedback[code] = feedback.get(code, 0) + 1
    false_rejects = sum(
        1 for v in validations if v.outcome == OUTCOME_EXACT_FALSE_REJECT
    )
    return DriftSnapshot(
        feedback_code_distribution=feedback,
        evidence_grade_distribution=grades,
        activation_state_distribution=activations,
        false_reject_count=false_rejects,
        registry_size=registry_size,
    )
