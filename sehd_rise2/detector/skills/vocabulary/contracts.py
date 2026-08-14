"""Single vocabulary truth for the evolvable surface.

One module owns every closed vocabulary the L1 rule layer and the L2
adjudication-policy layer share:

* L1 module/stage/verdict/negation vocabularies and the prompt-pollution
  firewall. The verdict-emission and local-negation patterns below are
  enforced identically by the free-text runtime gate (skills.genome) and
  the slot-contract gate (skills.rule): a split vocabulary once let a
  phrasing pass one gate and fail the other, silently changing module
  authority by rule form.
* L2 layer/artifact/route tokens, action operators and the parameter
  validation table, as derived views over the versioned baseline registry
  (skills.vocabulary.registry) so the attribution vocabulary cannot drift
  between modules.
* The audit translation vocabulary (check -> participating provider fields,
  relational invariants): it is the language the proof court uses to report
  failure and the evolution side uses to route repair, so by the plan §2.2
  ownership ruling it lives here as the single source; the detection court's
  audit translation imports it from here instead of owning a second copy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from sehd_rise2.detector.skills.capabilities import BINDING_DOMAINS, SKILL_CAPABILITY_REGISTRY
from sehd_rise2.detector.skills.vocabulary.registry import L2_REGISTRY


# ============================================================
# L1 modules
# ============================================================

MODULE_IDS = (
    "candidate_trigger",
    "evidence_obligation",
    "candidate_to_verifier_route",
    "atomic_verifier_instruction",
    "causal_link_test",
    "earliest_origin_tiebreak",
)

# Stage each module's rules are injected into.
MODULE_STAGE: dict[str, str] = {
    "candidate_trigger": "stage_1_census",
    "evidence_obligation": "stage_2_verification",
    "candidate_to_verifier_route": "stage_2_verification",
    "atomic_verifier_instruction": "stage_2_verification",
    "causal_link_test": "stage_3_causal_court",
    "earliest_origin_tiebreak": "stage_3_causal_court",
}

# Verdict emission is owned solely by the deterministic proof court: no L1
# module may emit a final verdict. The two verdict-bearing modules
# (``clean_defense_boundary``, ``abstention_rule``) were removed with their
# empty opcode surfaces, so this table is empty by construction.
ALLOWED_VERDICTS: dict[str, frozenset[str]] = {}

LOCAL_NEGATION_MODULES: frozenset[str] = frozenset({"causal_link_test"})

# Covers the English slot render plus the Chinese free-text forms so a
# verdict phrasing has exactly one permission answer regardless of rule form.
FINAL_VERDICT_RE = re.compile(
    r"判定为\s*(clean|error|unresolved)|"
    r"直接输出\s*(clean|error|unresolved)|"
    r"(?:return|output|classify as|label as)\s+(clean|error|unresolved)\b|"
    r"(?:treat|regard|consider|deem)\s+[^,，。；;]{0,40}\s+as\s+(clean|error|unresolved)\b|"
    r"(?:视为|视作|当作|认定为|判别为)\s*(clean|error|unresolved)|"
    r"应判(?:定为|为)?\s*(clean|error|unresolved)|"
    r"结论(?:为|是)\s*(clean|error|unresolved)",
    re.IGNORECASE,
)

LOCAL_NEGATION_RE = re.compile(
    r"\b(?:does|do|did|will|would|can|could|should|may|might) not hold\b|"
    r"不成立|不应视为|不足以构成|非因果|因果链不成立|不构成因果",
    re.IGNORECASE,
)

FORBIDDEN_CONTENT_MARKERS = frozenset({
    "case_id", "source_id", "agent_id", "model_id",
    "gold_explanation_text", "outer_metric", "outer_error",
    "example_specific_fact", "continuous_threshold_search",
})

INSTANCE_ID_PATTERN = r"\b(?:[a-z][a-z0-9]*[-_])+\d+\b"
INSTANCE_ID_RE = re.compile(INSTANCE_ID_PATTERN, re.IGNORECASE)

_FEATURE_POISON_PATTERNS = (
    r"https?://",
    r"\b(?:case|source|agent|model)[-_ ]?id\b",
    r"\b(?:case|source)[_-]\d+\b",
    r"\bag_\w{8,}\b",
    r"\b0x[0-9a-f]{6,}\b",
    r"\b(?:gold|ground[ -]?truth|golden label)\b",
    r"(?:金标|答案|正确标签|"
    r"真实标签|本案例|这个案例)",
    r"\b(?:ignore|disregard|override)\s+(?:all\s+)?"
    r"(?:previous|prior|above|system|developer)\b.{0,40}"
    r"\b(?:instruction|prompt|rule|message)s?\b",
    r"\b(?:system|developer|assistant)\s+(?:prompt|message)\b",
    r"\b(?:you are|follow these instructions|output only|return json)\b",
    r"\b(?:instruction|prompt)\s*:",
    r"^(?:ignore|follow|output|return|write|add|remove|modify|delete|always|never|must)\b",
    r"\b[a-z][a-z_]+/\d+\b",
    INSTANCE_ID_PATTERN,
)


def validate_feature_description(content: str) -> tuple[bool, str]:
    """Validate label-free feature provenance before it can reach prompts.

    Feature descriptions are persisted and later included in reflection
    context, so they need the same case/gold/URL boundary as runtime rules plus
    an explicit prompt-instruction boundary. Empty descriptions are allowed;
    the typed trajectory extractor can supply a safe fallback.
    """
    normalized = " ".join(str(content or "").split())
    if len(normalized) > 500:
        return False, "feature_description_too_long"
    lowered = normalized.lower()
    for marker in FORBIDDEN_CONTENT_MARKERS:
        if marker in lowered:
            return False, f"feature_forbidden_content:{marker}"
    for pattern in _FEATURE_POISON_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE | re.DOTALL):
            return False, f"feature_prompt_or_case_pollution:{pattern}"
    return True, ""


def semantic_overlap(text_a: str, text_b: str) -> float:
    """Compute language-agnostic character-bigram Jaccard similarity."""
    def tokens(text: str) -> set[str]:
        normalized = re.sub(r"[\W_]+", "", text.lower(), flags=re.UNICODE)
        if len(normalized) < 2:
            return {normalized} if normalized else set()
        return {
            normalized[index:index + 2]
            for index in range(len(normalized) - 1)
        }

    tokens_a = tokens(text_a)
    tokens_b = tokens(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


# ============================================================
# Evolvable layers
# ============================================================

LAYER_L1 = "l1_provider_rule"
LAYER_L2 = "l2_adjudication_policy"
LAYER_DETECTOR = "detector_layer_out_of_scope"
LAYER_NONE = "no_actionable"
FAILURE_LAYERS = frozenset({LAYER_L1, LAYER_L2, LAYER_DETECTOR, LAYER_NONE})


# ============================================================
# L2 artifacts: the adjudication policy surfaces that can own a failure
# ============================================================

L2_ARTIFACT_CONSENSUS_GATE = "consensus_gate_policy"
L2_ARTIFACT_COVERAGE_VALIDITY = "coverage_validity_policy"
L2_ARTIFACT_BOUNDARY_CONSUMPTION = "boundary_consumption_policy"
L2_ARTIFACT_APPEAL_MECHANISM = "appeal_mechanism_policy"
L2_ARTIFACTS = frozenset({
    L2_ARTIFACT_CONSENSUS_GATE,
    L2_ARTIFACT_COVERAGE_VALIDITY,
    L2_ARTIFACT_BOUNDARY_CONSUMPTION,
    L2_ARTIFACT_APPEAL_MECHANISM,
})

# L1 artifacts are exactly the registered post-court Skill modules.
L1_ARTIFACTS = frozenset(MODULE_IDS)


# ============================================================
# L2 precondition predicates (closed vocabulary)
# ============================================================
# Policy items may reference only these names; the evaluator resolves them
# mechanically. The names and their semantics are owned by the versioned
# registry; this dict is a derived read-only view kept for callers that
# resolve predicates by name.

L2_PREDICATES: dict[str, Any] = {
    name: L2_REGISTRY.predicate_fn(name)
    for name in L2_REGISTRY.predicate_names()
}
L2_PREDICATE_NAMES = tuple(L2_PREDICATES)


# ============================================================
# L2 actions (closed vocabulary)
# ============================================================
# The registry is the single source of truth for what the reflection prompt
# may propose AND what the runtime can execute; the constants below are
# registry-derived views. Every listed action is applied deterministically by
# the policy evaluator inside the proof court. Actions that would require
# provider re-execution (``reclassify_candidate_state``,
# ``require_route_replay``) are reserved in ``ENGINE_LEVEL_ACTIONS`` but
# excluded from ``L2_ACTIONS`` until the runtime engine implements them, so a
# proposal for one fails at parse time as ``unknown_action`` instead of
# reaching a runtime rejection.

POLICY_SIDES = frozenset({"prosecution", "defense"})
COVERAGE_DOWNGRADE_STATES = frozenset({"incomplete", "unresolved"})

ACTION_DOWNGRADE_COVERAGE = "downgrade_coverage"
ACTION_INVALIDATE_COVERAGE_CLAIM = "invalidate_coverage_claim"
ACTION_RECLASSIFY_CANDIDATE_STATE = "reclassify_candidate_state"
ACTION_REQUIRE_ROUTE_REPLAY = "require_route_replay"
ACTION_EMIT_TELEMETRY_ONLY = "emit_telemetry_only"
ACTION_CONSUME_VERIFIED_CLEAN_BOUNDARY = "consume_verified_clean_boundary"
ACTION_REQUIRE_ADDITIONAL_VERIFICATION = "require_additional_verification"
ACTION_ESCALATE_TO_APPEAL = "escalate_to_appeal"
ACTION_MARK_CANDIDATE_AMBIGUOUS = "mark_candidate_ambiguous"

COURT_LOCAL_ACTIONS = L2_REGISTRY.action_names()
# Reserved for a future engine that can re-execute providers; not part of
# the proposable/executable vocabulary until implemented.
ENGINE_LEVEL_ACTIONS = L2_REGISTRY.engine_level_action_names()
L2_ACTIONS = COURT_LOCAL_ACTIONS

# Required parameter keys per action, owned by the registry; values are
# validated against the vocabularies below the table.
ACTION_PARAMS: dict[str, tuple[str, ...]] = dict(L2_REGISTRY.action_params())


def validate_policy_params(
    action: str, params: dict[str, Any], registry=None,
) -> tuple[bool, str]:
    """Check action parameters against the closed vocabularies.

    ``registry`` selects the vocabulary authority (defaults to the baseline
    ``L2_REGISTRY``); a genome carrying an evolved registry validates its
    items against that registry.
    """
    active = registry if registry is not None else L2_REGISTRY
    required = active.action_params().get(action)
    if required is None:
        return False, f"unknown_action:{action}"
    for key in required:
        if key not in params:
            return False, f"missing_param:{key}"
    side = params.get("side")
    if "side" in required and side not in POLICY_SIDES:
        return False, f"invalid_side:{side}"
    to_state = params.get("to_state")
    if action == ACTION_DOWNGRADE_COVERAGE and to_state not in COVERAGE_DOWNGRADE_STATES:
        return False, f"invalid_downgrade_state:{to_state}"
    if action == ACTION_REQUIRE_ADDITIONAL_VERIFICATION:
        scope = params.get("verification_scope")
        if scope not in ("candidate", "certificate", "evidence_chain"):
            return False, f"invalid_verification_scope:{scope}"
    if action == ACTION_MARK_CANDIDATE_AMBIGUOUS:
        reason = params.get("ambiguity_reason")
        if reason not in ("conflicting_evidence", "boundary_unclear", "multiple_interpretations"):
            return False, f"invalid_ambiguity_reason:{reason}"
    return True, ""


# ============================================================
# Policy route taxonomy: why an L2 attribution did or did not yield an
# executable intervention
# ============================================================
# Every L2 attribution resolves to exactly one route. "executed" means a
# candidate was proposed, validated and replayed; the other five explain,
# in closed vocabulary, why no executable candidate reached the model.

ROUTE_NO_REGISTERED_RUNTIME_ACTION = "no_registered_runtime_action"
ROUTE_RUNTIME_PRECONDITION_NOT_SATISFIED = "runtime_precondition_not_satisfied"
ROUTE_CANDIDATE_BLOCKED_BY_PROTECTION = "candidate_blocked_by_protection"
ROUTE_CANDIDATE_ALREADY_PRESENT = "candidate_already_present"
ROUTE_ENGINE_LEVEL_INTERVENTION_REQUIRED = "engine_level_intervention_required"
ROUTE_EXECUTED = "executed"
POLICY_ROUTES = frozenset({
    ROUTE_NO_REGISTERED_RUNTIME_ACTION,
    ROUTE_RUNTIME_PRECONDITION_NOT_SATISFIED,
    ROUTE_CANDIDATE_BLOCKED_BY_PROTECTION,
    ROUTE_CANDIDATE_ALREADY_PRESENT,
    ROUTE_ENGINE_LEVEL_INTERVENTION_REQUIRED,
    ROUTE_EXECUTED,
})

# Prompt-facing feedback code accompanying a non-executed route.
FEEDBACK_POLICY_ROUTE_UNAVAILABLE = "policy_route_unavailable"


# ============================================================
# L2 policy item
# ============================================================

POLICY_GENOME_CAPACITY = 12


@dataclass(frozen=True)
class PolicyItem:
    """One evolvable adjudication policy: (observable precondition, action).

    The precondition references only closed predicate names over the
    adjudication-facts snapshot; the action is one of the enumerated actions
    with closed-vocabulary parameters. Semantics are fixed by the evaluator —
    the model proposes items but never interprets them at runtime.
    """
    item_id: str
    precondition: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    source_case_id: str = ""
    is_baseline: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "precondition": self.precondition,
            "action": self.action,
            "params": dict(self.params),
            "rationale": self.rationale,
            "source_case_id": self.source_case_id,
            "is_baseline": self.is_baseline,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyItem":
        return cls(
            item_id=str(data.get("item_id") or ""),
            precondition=str(data.get("precondition") or ""),
            action=str(data.get("action") or ""),
            params=dict(data.get("params") or {}),
            rationale=str(data.get("rationale") or ""),
            source_case_id=str(data.get("source_case_id") or ""),
            is_baseline=bool(data.get("is_baseline", False)),
        )


# ============================================================
# Audit translation vocabulary (plan §2.2 ownership ruling)
# ============================================================
# The proof court reports predicate names (for example ``ordered_chain``),
# whereas providers emit certificate fields. Treating those two namespaces as
# the same made reflection propose field edits for non-existent fields, so
# the check-to-field mapping has exactly one owner for both the court and the
# evolution side. The detection court's audit translation derives its
# projections from these tables rather than duplicating them.

ERROR_CHECK_FIELDS: Mapping[str, tuple[str, ...]] = {
    "certificate_id": ("certificate_id",),
    "source_candidate": ("source_candidate_id",),
    "origin": ("origin_event_id",),
    "claim_kind": ("claim_kind",),
    "norm_source": ("norm_source",),
    "norm_evidence": ("norm_evidence_refs",),
    "breach_type": ("breach_type",),
    "violation_evidence": ("violation_evidence_refs",),
    "agent_control": ("agent_control_event_id",),
    "agent_control_evidence": ("agent_control_evidence_refs",),
    "agent_control_evidence_binding": (
        "agent_control_event_id", "agent_control_evidence_refs",
    ),
    "impact_kind": ("impact_kind",),
    "impact_events": ("impact_event_ids",),
    "impact_evidence": ("impact_evidence_refs",),
    "impact_evidence_binding": ("impact_event_ids", "impact_evidence_refs"),
    "recovery_state": ("recovery_state",),
    "recovery_evidence": ("recovery_evidence_refs",),
    "benchmark_causal_status": ("benchmark_causal_status",),
    "benchmark_causal_evidence": ("benchmark_causal_evidence_refs",),
    "benchmark_causal_evidence_binding": (
        "impact_event_ids", "benchmark_causal_evidence_refs",
    ),
    "ordered_chain": (
        "origin_event_id", "agent_control_event_id", "impact_event_ids",
    ),
    "rationale": ("rationale",),
}

DEFENSE_CHECK_FIELDS: Mapping[str, tuple[str, ...]] = {
    "certificate_id": ("certificate_id",),
    "target_candidate": ("target_candidate_id",),
    "target_origin": ("target_origin_event_id",),
    "defense_type": ("defense_type",),
    "evidence": ("evidence_refs",),
    "rationale": ("rationale",),
}

# These checks cannot be repaired by merely making one field non-empty. Their
# semantics depend on two or more fields, scope membership, or trajectory
# addressability, so they form the closed invariant action surface.
ERROR_RELATIONAL_INVARIANTS = frozenset({
    "source_candidate", "origin", "norm_evidence", "violation_evidence",
    "agent_control", "agent_control_evidence",
    "agent_control_evidence_binding", "impact_events", "impact_evidence",
    "impact_evidence_binding", "recovery_evidence",
    "benchmark_causal_evidence", "benchmark_causal_evidence_binding",
    "ordered_chain",
})
DEFENSE_RELATIONAL_INVARIANTS = frozenset({
    "target_candidate", "target_origin", "evidence",
})

ERROR_INVARIANT_TARGETS = tuple(sorted(ERROR_CHECK_FIELDS))
DEFENSE_INVARIANT_TARGETS = tuple(sorted(DEFENSE_CHECK_FIELDS))


# ============================================================
# L1 condition-feature contract
# ============================================================
# The single source of the predicate vocabulary a typed L1 condition
# (skills.rule.ConditionSpec) may speak. For every feature the table owns
# the allowed operators, the closed value domain, the binding domains whose
# projection emits it, and the call stage at which the fact exists.
# skills.rule derives validation and the prompt contract from this table,
# and each branch of skills.binding.project_binding_universe emits exactly
# the per-domain sets derived here, so a new projected feature is declared
# in this table once and every consumer follows.

CONDITION_STAGE_FIRST_CALL = "first_call"
CONDITION_STAGE_RETRY_DIAGNOSTIC = "retry_diagnostic"

_CONDITION_OPERATORS_MEMBERSHIP = frozenset({"contains"})
_CONDITION_OPERATORS_SCALAR = frozenset({"equals", "not_equals"})

_CANDIDATE_KINDS = frozenset({
    "omission", "unsupported_claim", "contradiction", "invalid_tool_use",
    "invalid_action", "other",
})
_CANDIDATE_AXES = frozenset({"F", "Fa", "P", "S"})
# The AgentHallu hallucination category the census assigned, normalized from
# the schema's hyphenated tags ("Tool-Use" -> "tool_use").
_MODULE_TAGS = frozenset({
    "planning", "retrieval", "reasoning", "tool_use", "human_interaction",
    "loop_detection", "redundancy_check", "factuality", "faithfulness",
    "completion", "scope",
})
_COVERAGE_STATES = frozenset({"none", "incomplete", "complete", "unknown"})
_QUALIFICATION_STATES = frozenset({"held", "qualified", "rejected", "unknown"})
_EVENT_ROLES = frozenset({"system", "user", "assistant", "tool"})
_ANCHOR_KINDS = frozenset({
    "assistant_assertion", "tool_grounded_claim", "question", "code",
})
# Every runtime field any registered operation can target; the set follows
# the capability registry, the field vocabulary's owner.
_RUNTIME_TARGET_FIELDS = frozenset({
    target
    for module_id in MODULE_IDS
    for capability in SKILL_CAPABILITY_REGISTRY.for_module(module_id)
    for target in capability.operation_targets
})
_AUDIT_INVARIANTS = ERROR_RELATIONAL_INVARIANTS | DEFENSE_RELATIONAL_INVARIANTS
_BOOLEANS = frozenset({True, False})

_COMMON_DOMAINS = frozenset({
    "candidate", "certificate", "origin", "coverage", "boundary", "event",
})
_ENTITY_DOMAINS = frozenset({"candidate", "certificate", "origin"})


@dataclass(frozen=True)
class ConditionFeatureSpec:
    """Closed contract of one condition-predicate feature."""

    operators: frozenset[str]
    values: frozenset[Any]
    domains: frozenset[str]
    value_type: str
    stage: str


def _scalar(
    values: frozenset[Any],
    domains: frozenset[str],
    *,
    value_type: str = "boolean",
) -> ConditionFeatureSpec:
    return ConditionFeatureSpec(
        operators=_CONDITION_OPERATORS_SCALAR, values=values,
        domains=domains, value_type=value_type,
        stage=CONDITION_STAGE_FIRST_CALL,
    )


def _membership(
    values: frozenset[Any],
    domains: frozenset[str],
    *,
    stage: str = CONDITION_STAGE_FIRST_CALL,
) -> ConditionFeatureSpec:
    return ConditionFeatureSpec(
        operators=_CONDITION_OPERATORS_MEMBERSHIP, values=values,
        domains=domains, value_type="string", stage=stage,
    )


CONDITION_FEATURES: Mapping[str, ConditionFeatureSpec] = {
    "anchor_kind": _membership(_ANCHOR_KINDS, frozenset({"claim_anchor"})),
    "anchor_role": _membership(
        frozenset({"assistant"}), frozenset({"claim_anchor"}),
    ),
    "anchor_depends_on_tool": _scalar(_BOOLEANS, frozenset({"claim_anchor"})),
    "anchor_has_number": _scalar(_BOOLEANS, frozenset({"claim_anchor"})),
    "anchor_has_citation": _scalar(_BOOLEANS, frozenset({"claim_anchor"})),
    "anchor_is_final_assistant": _scalar(_BOOLEANS, frozenset({"claim_anchor"})),
    "anchor_is_first_in_event": _scalar(_BOOLEANS, frozenset({"claim_anchor"})),
    "anchor_is_last_in_event": _scalar(_BOOLEANS, frozenset({"claim_anchor"})),
    "candidate_kind": _membership(_CANDIDATE_KINDS, _ENTITY_DOMAINS),
    "candidate_axis": _membership(_CANDIDATE_AXES, _ENTITY_DOMAINS),
    "module_tag": _membership(_MODULE_TAGS, _ENTITY_DOMAINS),
    "missing_field": _membership(
        _RUNTIME_TARGET_FIELDS, _ENTITY_DOMAINS,
        stage=CONDITION_STAGE_RETRY_DIAGNOSTIC,
    ),
    "failed_invariant": _membership(
        _AUDIT_INVARIANTS, _ENTITY_DOMAINS,
        stage=CONDITION_STAGE_RETRY_DIAGNOSTIC,
    ),
    "coverage_state": _membership(
        _COVERAGE_STATES, frozenset({"coverage"}),
    ),
    "coverage_side": _membership(
        frozenset({"prosecution", "defense"}), frozenset({"coverage"}),
    ),
    "qualification_state": _membership(_QUALIFICATION_STATES, _ENTITY_DOMAINS),
    "evidence_verified": _scalar(_BOOLEANS, _ENTITY_DOMAINS),
    "event_role": _membership(_EVENT_ROLES, _COMMON_DOMAINS),
    "has_tool_call": _scalar(_BOOLEANS, _COMMON_DOMAINS),
    "has_tool_error": _scalar(_BOOLEANS, _COMMON_DOMAINS),
    "has_tool_retry": _scalar(_BOOLEANS, _COMMON_DOMAINS),
}

DOMAIN_CONDITION_FEATURES: Mapping[str, frozenset[str]] = {
    domain: frozenset(
        name for name, spec in CONDITION_FEATURES.items()
        if domain in spec.domains
    )
    for domain in sorted(BINDING_DOMAINS)
}
