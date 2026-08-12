"""Cross-Case Strategy Learner review: candidate generation, commit gate,
prompt projection and drift (plan §2.8).

C7 closes the strategy loop over audited gate events and verified RepairPlan
outcomes:

    GateEvent projection + verified RepairPlan outcomes
      -> deterministic StrategyEdit candidates
      -> shadow recommendation
      -> evidence gate
      -> commit / rollback

Everything here is deterministic and prompt-safe. The single prompt projection
(``project_strategy_for_prompt``) is bounded, canonical, closed-vocabulary and
free of identity, gold, raw trajectories and provider responses; it always
carries ``authority="evolution_guidance_only"`` so it can never widen an
ActionSpec, change an owner route, or relax a gate. The learner only re-ranks
or guides within the candidates the planner already produced — it never
enlarges a candidate set and never touches the owner, the causal cut,
repairability or the hard gate (§2.8 "Meta 不负责").

Interface rewire versus the v6 meta review (C7): the GateEvent projection is
consumed as a pure-data ``GateExperience`` owned here; the GateEvent ->
GateExperience projection itself lives in ``loop/ledger`` (Phase 9), which is
the only layer that may import the GateEvent type. Verified outcomes now come
from ``experience/memory.MechanismEntry`` (per-mechanism representative slots)
instead of a separate follow-up validation classification — §2.8 "从多次已验证
的 RepairPlan outcome 中学习".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sehd_rise.experience.memory import MechanismEntry
from sehd_rise.meta.strategy import (
    DOMAIN_TOKENS,
    OP_ENABLE,
    STRATEGY_DOMAINS,
    StrategyEdit,
    StrategyState,
)

SCHEMA_VERSION = "sehd_rise.strategy_review.v1"

# ============================================================
# Fuzzy feedback closed vocabulary
# ============================================================
# Owned here so the consumer (this learner) validates the projection
# fail-closed; the producer in ``loop/ledger`` (Phase 9) imports the same set
# so a gate-event reason code can never invent a feedback code neither side
# recognises. The codes are coarse and abstract: they may inform a
# reflection but never name a threshold, a gold polarity or an identity.

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

# Follow-up status for a GateExperience (closed).
FOLLOW_UP_PENDING = "pending"
FOLLOW_UP_VALIDATED = "validated"
FOLLOW_UP_SUPERSEDED = "superseded"
FOLLOW_UP_STATUSES = frozenset({
    FOLLOW_UP_PENDING, FOLLOW_UP_VALIDATED, FOLLOW_UP_SUPERSEDED,
})


def _mechanism_key(signature: Any) -> tuple[str, ...]:
    """Normalize a mechanism signature to its tuple key form.

    ``loop/ledger`` always produces tuples, but persisted or hand-built
    experiences may carry a bare string; both must hash to the same
    per-mechanism bucket so evidence never splits across representations.
    """
    if isinstance(signature, str):
        return (signature,)
    return tuple(str(value) for value in (signature or ()))


# ============================================================
# GateExperience — the GateEvent projection consumed here
# ============================================================

@dataclass(frozen=True)
class GateExperience:
    """An aggregated, mechanism-keyed view of one audited gate event.

    This is the projection the strategy learner consumes; it is pure data so
    the producer (``loop/ledger``) and any replay feed the same shape.
    """

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


# ============================================================
# Verified-outcome signals (plan §2.8 — learn from verified outcomes)
# ============================================================

# Representative slot names are the verified RepairPlan outcome classes
# ``experience/memory`` already stores per mechanism; they are the only
# outcome vocabulary this learner reads, so a free-form decision string can
# never become a strategy signal here.
_OUTCOME_SLOTS = ("effective", "no_effect", "structural_reject", "no_proposal")


def _outcome_signals(entry: MechanismEntry) -> set[str]:
    """Verified RepairPlan outcome classes present for one mechanism."""
    reps = entry.representatives
    return {slot for slot in _OUTCOME_SLOTS if reps.get(slot) is not None}


# ============================================================
# Deterministic StrategyEdit candidate generation (plan §2.8)
# ============================================================

# Feedback-code -> strategy token. Registered up front so the gate can never
# be satisfied by an unrelated code (fail-closed: a target absent from this
# table is not committable at all).
FEEDBACK_EVIDENCE_SOURCES: Mapping[tuple[str, str], tuple[str, ...]] = {
    ("operation_preference", "prefer_registered_runtime_route"): (
        FEEDBACK_POLICY_ROUTE_UNAVAILABLE,
    ),
    ("operation_preference", "avoid_pending_registry_dependency"): (
        FEEDBACK_REGISTRY_ENTRY_PENDING,
    ),
    ("operation_preference", "avoid_redundant_intervention"): (
        FEEDBACK_REDUNDANT_INTERVENTION,
    ),
    # Wiring for search/diversity/retry tokens that were declared in the
    # closed vocabulary but had no evidence source, so a recurring,
    # audited feedback pattern could never enable them. Each mapping stays
    # strictly "how to search better" — it never names an owner, a cut, a
    # gate, a threshold, or a verdict.
    ("search_prior", "prefer_observable_discriminator"): (
        FEEDBACK_INSUFFICIENT_GROUNDING,
    ),
    ("search_prior", "require_independent_anchor"): (
        FEEDBACK_INDEPENDENT_REGRESSION_RISK,
    ),
    ("candidate_diversity", "seek_distinct_candidate_direction"): (
        FEEDBACK_REDUNDANT_INTERVENTION,
    ),
    ("candidate_diversity", "duplicate_risk"): (
        FEEDBACK_REDUNDANT_INTERVENTION,
        FEEDBACK_CANDIDATE_QUALITY_INCONCLUSIVE,
    ),
    ("retry_strategy", "avoid_unexecuted_action_shape"): (
        FEEDBACK_EXECUTION_NOT_ESTABLISHED,
    ),
}

# Verified-outcome signal -> strategy token. The C7 rewire: outcome evidence
# now flows from per-mechanism verified representatives rather than a
# separate follow-up validation classification (§2.8).
OUTCOME_EVIDENCE_SOURCES: Mapping[tuple[str, str], tuple[str, ...]] = {
    ("mechanism_ranking", "action_shape_previously_effective"): ("effective",),
    ("mechanism_ranking", "action_shape_previously_no_effect"): ("no_effect",),
    ("retry_strategy", "reuse_effective_action_shape"): ("effective",),
    ("retry_strategy", "avoid_action_shape_previously_failed"): (
        "no_effect", "structural_reject",
    ),
}


def generate_strategy_suggestions(
    experiences: Sequence[GateExperience],
    outcomes: Sequence[MechanismEntry],
    state: StrategyState,
) -> list[StrategyEdit]:
    """Enumerate deterministic StrategyEdit candidates from aggregated evidence.

    Conservative first version: each candidate maps one recurring feedback
    pattern or one verified outcome class to one closed-vocabulary strategy
    token. No candidate ever touches the hard gate, a numeric threshold, an
    owner, the causal cut or a verdict. Candidates the state already
    satisfies are not re-proposed.
    """
    candidates: list[StrategyEdit] = []
    counter = [0]

    def _next_id(domain: str, target: str) -> str:
        counter[0] += 1
        digest = hashlib.sha256(
            f"{domain}:{target}:{counter[0]}".encode("utf-8")
        ).hexdigest()[:10]
        return f"strategy_edit_{digest}"

    feedback_counts: dict[str, int] = {}
    for experience in experiences:
        for code in experience.feedback_codes:
            feedback_counts[code] = feedback_counts.get(code, 0) + 1

    for (domain, token), sources in sorted(FEEDBACK_EVIDENCE_SOURCES.items()):
        if (
            any(feedback_counts.get(code, 0) >= 1 for code in sources)
            and token not in state.enabled_in(domain)
        ):
            candidates.append(StrategyEdit(
                edit_id=_next_id(domain, token),
                operation=OP_ENABLE,
                domain=domain,
                target=token,
                rationale=f"recurring {sources[0]}",
            ))

    signal_counts: dict[str, int] = {}
    for entry in outcomes:
        for signal in _outcome_signals(entry):
            signal_counts[signal] = signal_counts.get(signal, 0) + 1

    for (domain, token), signals in sorted(OUTCOME_EVIDENCE_SOURCES.items()):
        if (
            any(signal_counts.get(signal, 0) >= 1 for signal in signals)
            and token not in state.enabled_in(domain)
        ):
            candidates.append(StrategyEdit(
                edit_id=_next_id(domain, token),
                operation=OP_ENABLE,
                domain=domain,
                target=token,
                rationale=f"verified {signals[0]} outcome",
            ))
    return candidates


# ============================================================
# Commit evidence gate — the single auto-commit authority
# ============================================================

# A candidate must be backed by at least this many independent audited
# occurrences before it may leave shadow mode. One occurrence is enough to
# *propose* (see generate_strategy_suggestions) but never enough to commit.
MIN_COMMIT_EVIDENCE = 3

# Operations that may be auto-committed once the evidence threshold is met.
# Deliberately narrow: enabling a strategy token only re-ranks or guides an
# existing candidate set, so its worst case is a slightly worse hint. Any
# disable stays manual — it is a lineage-visible policy decision, not an
# evidence-driven inference.
AUTO_COMMITTABLE_OPERATIONS = frozenset({OP_ENABLE})


def evaluate_commit_evidence(
    edit: StrategyEdit,
    experiences: Sequence[GateExperience],
    outcomes: Sequence[MechanismEntry],
    *,
    min_evidence: int = MIN_COMMIT_EVIDENCE,
) -> dict[str, Any]:
    """Decide whether one shadow candidate has earned a commit. Fail-closed.

    Returns an audit row rather than a bare bool so the decision — and the
    exact evidence count behind it — is always persisted, whether the
    candidate commits or stays in shadow.

    Feedback evidence accumulates per mechanism class (max within one
    mechanism), so a pattern that repeats on the same mechanism reaches the
    threshold while scattered single occurrences never sum into a commit.
    Outcome evidence accumulates across mechanism classes: each mechanism
    whose verified representatives show the signal adds one — the §2.8
    cross-case learning signal.
    """
    feedback_sources = FEEDBACK_EVIDENCE_SOURCES.get((edit.domain, edit.target), ())
    outcome_sources = OUTCOME_EVIDENCE_SOURCES.get((edit.domain, edit.target), ())
    outcome_set = set(outcome_sources)

    per_mechanism: dict[tuple[str, ...], int] = {}
    for experience in experiences:
        for code in feedback_sources:
            if code in experience.feedback_codes:
                signature = _mechanism_key(experience.mechanism_signature)
                per_mechanism[signature] = per_mechanism.get(signature, 0) + 1
    observed = max(per_mechanism.values(), default=0)
    observed += sum(
        1 for entry in outcomes if outcome_set & _outcome_signals(entry)
    )

    threshold = max(1, int(min_evidence))
    if edit.operation not in AUTO_COMMITTABLE_OPERATIONS:
        blocked = "operation_requires_manual_review"
    elif not (feedback_sources or outcome_sources):
        blocked = "target_has_no_registered_evidence_source"
    elif observed < threshold:
        blocked = "insufficient_evidence"
    else:
        blocked = ""

    return {
        "edit_id": edit.edit_id,
        "operation": edit.operation,
        "domain": edit.domain,
        "target": edit.target,
        "feedback_evidence_codes": list(feedback_sources),
        "outcome_evidence_signals": list(outcome_sources),
        "evidence_observed": observed,
        "evidence_required": threshold,
        "evidence_by_mechanism": [
            {"mechanism_signature": list(signature), "observed": count}
            for signature, count in sorted(per_mechanism.items())
        ],
        "committed": not blocked,
        "blocked_reason": blocked,
    }


def commit_validated_strategy_edits(
    state: StrategyState,
    candidates: Sequence[StrategyEdit],
    experiences: Sequence[GateExperience],
    outcomes: Sequence[MechanismEntry],
    *,
    min_evidence: int = MIN_COMMIT_EVIDENCE,
) -> tuple[StrategyState, list[dict[str, Any]]]:
    """Apply every candidate that clears the evidence gate, in order.

    Closes the strategy loop: without it the review re-derives and re-discards
    the same candidates every run. Returns the new state (unchanged if nothing
    qualified) plus one audit row per candidate, so a shadow rejection is as
    traceable as a commit. An edit that fails ``validate_strategy_edit``
    never propagates: it is recorded as blocked and the state is untouched.
    """
    from sehd_rise.meta.strategy import apply_strategy_edit, validate_strategy_edit

    current = state
    audit: list[dict[str, Any]] = []
    for edit in candidates:
        row = evaluate_commit_evidence(
            edit, experiences, outcomes, min_evidence=min_evidence,
        )
        if row["committed"]:
            # Re-validate against the evolving state: an earlier commit in
            # this same loop may have already satisfied this candidate.
            valid, reason = validate_strategy_edit(edit, current)
            if valid:
                current = apply_strategy_edit(current, edit)
                row["after_hash"] = current.content_hash
                row["generation"] = current.generation
            else:
                row["committed"] = False
                row["blocked_reason"] = f"invalid_against_current_state:{reason}"
        audit.append(row)
    return current, audit


# ============================================================
# Prompt projection — the single authority
# ============================================================

def project_strategy_for_prompt(
    state: StrategyState,
    *,
    max_tokens: int = 8,
) -> dict[str, Any]:
    """Bounded, canonical, prompt-safe strategy projection.

    Closed vocabulary only; identity-free, gold-free, raw-trajectory-free,
    provider-response-free. Always ``authority="evolution_guidance_only"`` so
    the guidance can inform a reflection but never change an owner route, an
    ActionSpec or a gate.
    """
    cap = max(0, int(max_tokens))
    strategy: dict[str, list[str]] = {}
    for domain in STRATEGY_DOMAINS:
        tokens = [
            token for token in sorted(state.enabled_in(domain))
            if token in DOMAIN_TOKENS[domain]
        ][:cap]
        if tokens:
            strategy[domain] = tokens
    return {
        "authority": "evolution_guidance_only",
        "strategy": strategy,
    }


def rank_candidates(
    candidate_ids: Sequence[str],
    features: Mapping[str, Mapping[str, bool]],
    state: StrategyState,
) -> list[str]:
    """Re-rank (never enlarge) a candidate set using the enabled strategy tokens.

    ``features`` maps candidate_id -> {strategy_token: bool}. Every enabled
    token contributes equally: a candidate matching more of the learner's
    current strategy ranks higher. C7 unifies advisory and ranking tokens into
    one strategy surface — they all bias selection. The ordering is
    deterministic (match count desc, then candidate_id asc) and returns the
    SAME set of ids, only reordered.
    """
    enabled: list[str] = []
    for domain in STRATEGY_DOMAINS:
        enabled.extend(state.enabled_in(domain))

    def score(cid: str) -> tuple[int, str]:
        row = features.get(cid, {})
        matches = sum(1 for token in enabled if bool(row.get(token)))
        return (-matches, cid)

    return sorted((str(c) for c in candidate_ids), key=score)


# ============================================================
# Drift snapshot (reporting only)
# ============================================================

@dataclass(frozen=True)
class DriftSnapshot:
    """A reporting-only drift summary. The composite index is for reports
    only and is NEVER a black-box promotion authority."""

    feedback_code_distribution: Mapping[str, int] = field(default_factory=dict)
    evidence_grade_distribution: Mapping[str, int] = field(default_factory=dict)
    activation_state_distribution: Mapping[str, int] = field(default_factory=dict)
    outcome_distribution: Mapping[str, int] = field(default_factory=dict)
    mechanism_count: int = 0
    registry_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback_code_distribution": dict(self.feedback_code_distribution),
            "evidence_grade_distribution": dict(self.evidence_grade_distribution),
            "activation_state_distribution": dict(self.activation_state_distribution),
            "outcome_distribution": dict(self.outcome_distribution),
            "mechanism_count": self.mechanism_count,
            "registry_size": self.registry_size,
        }


def build_drift_snapshot(
    experiences: Sequence[GateExperience],
    outcomes: Sequence[MechanismEntry],
    *,
    registry_size: int = 0,
) -> DriftSnapshot:
    """Aggregate a reporting-only drift snapshot from experiences and verified
    outcomes. C7 narrows the snapshot to strategy-relevant distributions; the
    correctness/infra metrics (false-reject, unsupported-accept, runtime-invalid
    rate) that needed a separate validation authority are gone (§2.8 "只学策略
    不学真相")."""
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
    outcome_distribution: dict[str, int] = {}
    for entry in outcomes:
        for decision, count in entry.outcome_counts.items():
            outcome_distribution[decision] = (
                outcome_distribution.get(decision, 0) + count
            )
    return DriftSnapshot(
        feedback_code_distribution=feedback,
        evidence_grade_distribution=grades,
        activation_state_distribution=activations,
        outcome_distribution=outcome_distribution,
        mechanism_count=len(outcomes),
        registry_size=registry_size,
    )
