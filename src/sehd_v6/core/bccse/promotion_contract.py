"""Unified promotion contract for the L1/L2 evolvable surfaces.

One decision model for both layers: every legacy gate outcome maps to
exactly one ``PromotionDecision`` carrying an evidence table, an
``EvidenceGrade`` (how much support the artifact earned) and an
``ActivationState`` (how far the artifact may run).  The mapping lives
only here — audit, memory and runner consumers read ``evidence_grade`` /
``activation_state`` from the decision and never re-derive them.

Grading rules (single owner):

- any mapped hard-gate reason forces ``reject`` with no activation, under
  every gate profile;
- accept-class decisions earn ``strong``/``deployable`` only from explicit
  positive evidence facts (execution established, terminal effect, gold
  polarity or independent effect, no regression); otherwise they earn
  ``weak``/``working``;
- provisional/telemetry-only outcomes are ``conditional``/``pending``;
- reject-class and retry-class attempts are ``reject``;
- skip/no-edit/halt outcomes carry no artifact and grade ``skip``.

Schema: ``sehd.promotion_decision.v1``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "sehd.promotion_decision.v1"

# ============================================================
# Closed vocabularies
# ============================================================

EVIDENCE_POSITIVE = "positive"
EVIDENCE_NEUTRAL = "neutral"
EVIDENCE_NEGATIVE = "negative"
EVIDENCE_UNAVAILABLE = "unavailable"
EVIDENCE_STATUSES = frozenset({
    EVIDENCE_POSITIVE,
    EVIDENCE_NEUTRAL,
    EVIDENCE_NEGATIVE,
    EVIDENCE_UNAVAILABLE,
})

EVIDENCE_DIMENSIONS = (
    "structure",
    "ownership",
    "execution",
    "local_effect",
    "terminal_effect",
    "gold_polarity",
    "independent_effect",
    "non_regression",
    "historical_support",
    "cost_efficiency",
)

GRADE_STRONG = "strong"
GRADE_WEAK = "weak"
GRADE_CONDITIONAL = "conditional"
GRADE_REJECT = "reject"
GRADE_SKIP = "skip"
EVIDENCE_GRADES = frozenset({
    GRADE_STRONG,
    GRADE_WEAK,
    GRADE_CONDITIONAL,
    GRADE_REJECT,
    GRADE_SKIP,
})

ACTIVATION_PENDING = "pending"
ACTIVATION_WORKING = "working"
ACTIVATION_DEPLOYABLE = "deployable"
ACTIVATION_DISABLED = "disabled"
ACTIVATION_NONE = ""
ACTIVATION_STATES = frozenset({
    ACTIVATION_PENDING,
    ACTIVATION_WORKING,
    ACTIVATION_DEPLOYABLE,
    ACTIVATION_DISABLED,
})

# Hard gate (plan v3 §6.4): these always reject, under any profile.
HARD_INVALID_SCHEMA = "invalid_schema"
HARD_UNKNOWN_VOCABULARY = "unknown_vocabulary"
HARD_INVALID_PARAMETER = "invalid_parameter"
HARD_OWNERSHIP_MISMATCH = "ownership_mismatch"
HARD_ACTION_SPEC_INVALID = "action_spec_invalid"
HARD_GOLD_OR_IDENTITY_LEAKAGE = "gold_or_identity_leakage"
HARD_DETECTOR_AUTHORITY_INVALID = "detector_authority_invalid"
HARD_REPLAY_RUNTIME_INVALID = "replay_runtime_invalid"
HARD_GOLD_POLARITY_REGRESSION = "gold_polarity_regression"
HARD_CANARY_REGRESSION = "canary_regression"
HARD_PROTECTED_BASELINE_VIOLATION = "protected_baseline_violation"
HARD_UNKNOWN_EXECUTION_AUTHORITY = "unknown_execution_authority"
HARD_REJECT_CODES = frozenset({
    HARD_INVALID_SCHEMA,
    HARD_UNKNOWN_VOCABULARY,
    HARD_INVALID_PARAMETER,
    HARD_OWNERSHIP_MISMATCH,
    HARD_ACTION_SPEC_INVALID,
    HARD_GOLD_OR_IDENTITY_LEAKAGE,
    HARD_DETECTOR_AUTHORITY_INVALID,
    HARD_REPLAY_RUNTIME_INVALID,
    HARD_GOLD_POLARITY_REGRESSION,
    HARD_CANARY_REGRESSION,
    HARD_PROTECTED_BASELINE_VIOLATION,
    HARD_UNKNOWN_EXECUTION_AUTHORITY,
})

PROFILE_CONSERVATIVE = "conservative"
PROFILE_STEADY = "steady"
GATE_PROFILES = frozenset({PROFILE_CONSERVATIVE, PROFILE_STEADY})

AUTHORITY_L1_STRUCTURAL = "l1_structural_gate"
AUTHORITY_L1_FITNESS = "l1_fitness_gate"
AUTHORITY_L2_POLICY = "l2_policy_gate"
DECISION_AUTHORITIES = frozenset({
    AUTHORITY_L1_STRUCTURAL,
    AUTHORITY_L1_FITNESS,
    AUTHORITY_L2_POLICY,
})

# ============================================================
# Legacy decision classes (exhaustive catalogue of the runner's
# gate outcomes; anything outside this catalogue is unknown and
# fails closed).
# ============================================================

_ACCEPT_DECISIONS = frozenset({
    "accept_batch",
    "accept_fitness_disabled",
    "accept_policy",
})
_PROVISIONAL_DECISIONS = frozenset({
    "provisional",
    "provisional_accept",
    "provisional_label_free",
    "provisional_no_gold_telemetry",
})
_REJECT_DECISIONS = frozenset({
    "reject_apply",
    "reject_fitness",
    "reject_fitness_no_effect",
    "reject_fitness_runtime",
    "reject_policy_fitness",
    "reject_policy_no_effect",
    "structural_reject",
    "rollback_batch",
    "rollback_probe",
    "rollback_resume",
    "retry_structural",
    "retry_fitness_no_effect",
    "retry_invalid_mutation",
})
_SKIP_DECISIONS = frozenset({
    "skip_invalid_mutation",
    "skip_mutation_budget_exhausted",
    "skip_no_edit",
    "skip_no_policy",
    "skip_route_conflict",
    "skip_runtime_invalid",
    "skip_stable",
    "skip_unaddressable_failure",
    "no_edit",
    "halted",
})
LEGACY_DECISIONS = frozenset(
    _ACCEPT_DECISIONS
    | _PROVISIONAL_DECISIONS
    | _REJECT_DECISIONS
    | _SKIP_DECISIONS
)

# Reason-prefix -> hard reject code.  Lookup tests longest prefixes first
# so a specific prefix always wins over a more general one.  Reasons that
# match nothing stay quality reasons — never silently promoted to hard.
_HARD_REASON_PREFIXES: tuple[tuple[str, str], ...] = tuple(sorted(
    (
        ("replay_runtime_invalid", HARD_REPLAY_RUNTIME_INVALID),
        ("state_change_against_gold_polarity", HARD_GOLD_POLARITY_REGRESSION),
        ("gold_polarity_regression", HARD_GOLD_POLARITY_REGRESSION),
        ("stable_canary_regression", HARD_CANARY_REGRESSION),
        ("canary_regression", HARD_CANARY_REGRESSION),
        ("action_spec", HARD_ACTION_SPEC_INVALID),
        ("unknown_action", HARD_UNKNOWN_VOCABULARY),
        ("unknown_precondition", HARD_UNKNOWN_VOCABULARY),
        ("unknown_predicate", HARD_UNKNOWN_VOCABULARY),
        ("unknown_operation", HARD_UNKNOWN_VOCABULARY),
        ("unknown_operator", HARD_UNKNOWN_VOCABULARY),
        ("unknown_vocabulary", HARD_UNKNOWN_VOCABULARY),
        ("unknown_execution_authority", HARD_UNKNOWN_EXECUTION_AUTHORITY),
        ("missing_param", HARD_INVALID_PARAMETER),
        ("invalid_param", HARD_INVALID_PARAMETER),
        ("invalid_side", HARD_INVALID_PARAMETER),
        ("invalid_downgrade_state", HARD_INVALID_PARAMETER),
        ("rule_spec", HARD_INVALID_SCHEMA),
        ("invalid_schema", HARD_INVALID_SCHEMA),
        ("immutable_baseline_item", HARD_PROTECTED_BASELINE_VIOLATION),
        ("protected_item", HARD_PROTECTED_BASELINE_VIOLATION),
        ("protected_baseline", HARD_PROTECTED_BASELINE_VIOLATION),
        ("ownership_mismatch", HARD_OWNERSHIP_MISMATCH),
        ("owner_mismatch", HARD_OWNERSHIP_MISMATCH),
        ("authority_invalid", HARD_DETECTOR_AUTHORITY_INVALID),
        ("gold_leak", HARD_GOLD_OR_IDENTITY_LEAKAGE),
        ("identity_leak", HARD_GOLD_OR_IDENTITY_LEAKAGE),
        ("prompt_leak", HARD_GOLD_OR_IDENTITY_LEAKAGE),
    ),
    key=lambda entry: -len(entry[0]),
))


def classify_reason_codes(
    reasons: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split raw gate reasons into (hard reject codes, quality reasons).

    Hard codes come only from the prefix table above; unmapped reasons are
    preserved verbatim as quality reasons.  Deterministic and total.
    """
    hard: list[str] = []
    quality: list[str] = []
    for raw in reasons:
        reason = str(raw or "")
        matched = ""
        for prefix, code in _HARD_REASON_PREFIXES:
            if reason.startswith(prefix):
                matched = code
                break
        if matched:
            if matched not in hard:
                hard.append(matched)
        elif reason and reason not in quality:
            quality.append(reason)
    return tuple(hard), tuple(quality)


# ============================================================
# Evidence facts -> evidence table
# ============================================================

@dataclass(frozen=True)
class PromotionEvidence:
    """Measured facts behind one gate outcome.

    Tri-state per dimension: True = positive measurement, False = negative
    measurement, None = not measured.  Callers report measurements only;
    the contract turns them into evidence statuses and grades.
    """

    structure_ok: bool | None = None
    ownership_ok: bool | None = None
    execution_established: bool | None = None
    local_effect: bool | None = None
    terminal_effect: bool | None = None
    gold_polarity: bool | None = None
    independent_effect: bool | None = None
    non_regression: bool | None = None
    historical_support: bool | None = None
    cost_efficiency: bool | None = None

    def evidence_table(self) -> dict[str, str]:
        def status(value: bool | None) -> str:
            if value is None:
                return EVIDENCE_UNAVAILABLE
            return EVIDENCE_POSITIVE if value else EVIDENCE_NEGATIVE

        return {
            "structure": status(self.structure_ok),
            "ownership": status(self.ownership_ok),
            "execution": status(self.execution_established),
            "local_effect": status(self.local_effect),
            "terminal_effect": status(self.terminal_effect),
            "gold_polarity": status(self.gold_polarity),
            "independent_effect": status(self.independent_effect),
            "non_regression": status(self.non_regression),
            "historical_support": status(self.historical_support),
            "cost_efficiency": status(self.cost_efficiency),
        }


# ============================================================
# PromotionDecision
# ============================================================

@dataclass(frozen=True)
class PromotionDecision:
    schema_version: str
    layer: str
    artifact: str
    legacy_decision: str
    evidence_table: dict[str, str]
    evidence_grade: str
    activation_state: str
    hard_reject_codes: tuple[str, ...] = ()
    quality_reason_codes: tuple[str, ...] = ()
    decision_authority: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "layer": self.layer,
            "artifact": self.artifact,
            "legacy_decision": self.legacy_decision,
            "evidence_table": dict(self.evidence_table),
            "evidence_grade": self.evidence_grade,
            "activation_state": self.activation_state,
            "hard_reject_codes": list(self.hard_reject_codes),
            "quality_reason_codes": list(self.quality_reason_codes),
            "decision_authority": self.decision_authority,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PromotionDecision":
        if not isinstance(value, Mapping):
            raise ValueError("promotion decision must contain an object")
        schema = str(value.get("schema_version") or "")
        if schema != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported promotion decision schema_version: {schema!r} "
                f"(expected {SCHEMA_VERSION!r})"
            )
        grade = str(value.get("evidence_grade") or "")
        if grade not in EVIDENCE_GRADES:
            raise ValueError(f"unknown evidence_grade: {grade!r}")
        activation = str(value.get("activation_state") or "")
        if activation and activation not in ACTIVATION_STATES:
            raise ValueError(f"unknown activation_state: {activation!r}")
        hard = tuple(str(code) for code in value.get("hard_reject_codes") or ())
        unknown_hard = [code for code in hard if code not in HARD_REJECT_CODES]
        if unknown_hard:
            raise ValueError(f"unknown hard reject codes: {unknown_hard}")
        table = {
            str(key): str(status)
            for key, status in dict(value.get("evidence_table") or {}).items()
        }
        unknown_dims = [key for key in table if key not in EVIDENCE_DIMENSIONS]
        if unknown_dims:
            raise ValueError(f"unknown evidence dimensions: {unknown_dims}")
        unknown_status = [
            status for status in table.values() if status not in EVIDENCE_STATUSES
        ]
        if unknown_status:
            raise ValueError(f"unknown evidence statuses: {unknown_status}")
        authority = str(value.get("decision_authority") or "")
        if authority and authority not in DECISION_AUTHORITIES:
            raise ValueError(f"unknown decision_authority: {authority!r}")
        return cls(
            schema_version=schema,
            layer=str(value.get("layer") or ""),
            artifact=str(value.get("artifact") or ""),
            legacy_decision=str(value.get("legacy_decision") or ""),
            evidence_table=table,
            evidence_grade=grade,
            activation_state=activation,
            hard_reject_codes=hard,
            quality_reason_codes=tuple(
                str(code) for code in value.get("quality_reason_codes") or ()
            ),
            decision_authority=authority,
        )


def promote(
    *,
    layer: str,
    artifact: str,
    legacy_decision: str,
    decision_authority: str,
    reasons: Sequence[str] = (),
    evidence: PromotionEvidence | None = None,
    profile: str = PROFILE_CONSERVATIVE,
) -> PromotionDecision:
    """Map one legacy gate outcome to its PromotionDecision.

    The only place where grade and activation are derived.  Fail-closed on
    unknown decisions, authorities or profiles.  Hard-gate reasons force
    ``reject`` with no activation under every profile — a profile may
    reinterpret quality evidence, never the hard gate.
    """
    decision = str(legacy_decision or "")
    if decision not in LEGACY_DECISIONS:
        raise ValueError(f"unknown legacy_decision: {decision!r}")
    if decision_authority not in DECISION_AUTHORITIES:
        raise ValueError(
            f"unknown decision_authority: {decision_authority!r}"
        )
    if profile not in GATE_PROFILES:
        raise ValueError(f"unknown gate profile: {profile!r}")

    hard, quality = classify_reason_codes(reasons)
    table = (
        evidence.evidence_table()
        if evidence is not None
        else PromotionEvidence().evidence_table()
    )

    if hard:
        grade, activation = GRADE_REJECT, ACTIVATION_NONE
    elif decision in _SKIP_DECISIONS:
        grade, activation = GRADE_SKIP, ACTIVATION_NONE
    elif decision in _REJECT_DECISIONS:
        grade, activation = GRADE_REJECT, ACTIVATION_NONE
    elif decision in _PROVISIONAL_DECISIONS:
        grade, activation = GRADE_CONDITIONAL, ACTIVATION_PENDING
    else:
        # Accept class: strong requires measured, independent positive
        # evidence; anything less stays weak.  Canary evidence alone never
        # promotes (non_regression is a veto, not a correctness authority).
        facts = evidence or PromotionEvidence()
        strong = (
            facts.execution_established is True
            and facts.terminal_effect is True
            and (facts.gold_polarity is True or facts.independent_effect is True)
            and facts.non_regression is not False
            and facts.independent_effect is not False
        )
        if strong:
            grade, activation = GRADE_STRONG, ACTIVATION_DEPLOYABLE
        else:
            grade, activation = GRADE_WEAK, ACTIVATION_WORKING

    return PromotionDecision(
        schema_version=SCHEMA_VERSION,
        layer=str(layer or ""),
        artifact=str(artifact or ""),
        legacy_decision=decision,
        evidence_table=table,
        evidence_grade=grade,
        activation_state=activation,
        hard_reject_codes=hard,
        quality_reason_codes=quality,
        decision_authority=decision_authority,
    )
