"""SEHD v6 shared contracts: closed vocabularies and core data types.

Single source of truth for the two-layer evolvable surface introduced in
the v6 direction (the L2 adjudication-policy layer, see
``docs/design/TECHNICAL_DESIGN.md``):

- L1 provider-rule layer: natural-language rules with slot structure,
  injected into provider prompts (see ``rule_contract`` / ``genome``).
- L2 adjudication-policy layer: closed-vocabulary policy items evaluated
  deterministically by the proof court (see ``policy_genome`` /
  ``policy_evaluator``).

The L2 precondition predicates, action operators/instances, and parameter
tables are owned by the versioned baseline registry in
``vocabulary_registry`` (the single authority for the L2 vocabulary); this
module re-exports them as derived views and adds the layer/artifact/route
tokens, so the attribution vocabulary cannot drift between modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .genome import MODULE_IDS, MODULE_STAGE

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
# v6.1 扩展：上诉机制策略
L2_ARTIFACT_APPEAL_MECHANISM = "appeal_mechanism_policy"
L2_ARTIFACTS = frozenset({
    L2_ARTIFACT_CONSENSUS_GATE,
    L2_ARTIFACT_COVERAGE_VALIDITY,
    L2_ARTIFACT_BOUNDARY_CONSUMPTION,
    L2_ARTIFACT_APPEAL_MECHANISM,
})

# L1 artifacts are exactly the eight registered provider-rule modules.
L1_ARTIFACTS = frozenset(MODULE_IDS)


# ============================================================
# Adjudication facts: the addressable observation surface
# ============================================================
# A flat, frozen snapshot of everything a policy precondition or a failure
# classification may look at. Built once from the proof-court result plus
# the engine-level context (boundary path), so neither the evaluator nor
# the classifier ever reaches into raw provider payloads.

# The fact snapshot type and the policy-stage constant live in the
# versioned baseline registry (single source of truth for the L2
# vocabulary); they are re-exported here so the shared contracts keep one
# import surface. ``build_adjudication_facts`` below derives the snapshot
# from one proof-court result.
from .vocabulary_registry import (  # noqa: E402
    AdjudicationFacts,
)


def build_adjudication_facts(
    court_result: dict[str, Any],
    *,
    boundary_path_state: str = "",
    boundary_authority_valid: bool = False,
) -> AdjudicationFacts:
    """Derive the addressable fact snapshot from one proof-court result."""
    def sequence_count(value: Any) -> int:
        return len(value) if isinstance(value, (list, tuple, set)) else 0

    def nonnegative_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    blocked = court_result.get("consensus_gate_blocked_ids") or []
    quarantined_errors = court_result.get("quarantined_error_certificate_ids") or []
    quarantined_defenses = court_result.get("quarantined_defense_certificate_ids") or []
    required_count = nonnegative_int(court_result.get("required_candidate_count"))
    # v6.1 扩展：从审计记录中推导隔离原因分布
    quarantine_schema_mismatch = 0
    quarantine_evidence_missing = 0
    quarantine_causal_broken = 0
    quarantine_other = 0
    
    for audit in court_result.get("error_audits") or []:
        if not isinstance(audit, Mapping):
            continue
        if str(audit.get("decision") or "") == "pass":
            continue
        failed = audit.get("failed_checks") or []
        if not isinstance(failed, (list, tuple)):
            continue
        failed_set = {str(f) for f in failed}
        if "schema_validation" in failed_set or "required_fields" in failed_set:
            quarantine_schema_mismatch += 1
        elif "evidence_refs" in failed_set or "evidence_quality" in failed_set:
            quarantine_evidence_missing += 1
        elif "causal_chain" in failed_set or "propagation" in failed_set:
            quarantine_causal_broken += 1
        else:
            quarantine_other += 1
    
    for audit in court_result.get("defense_audits") or []:
        if not isinstance(audit, Mapping):
            continue
        if str(audit.get("decision") or "") == "pass":
            continue
        failed = audit.get("failed_checks") or []
        if not isinstance(failed, (list, tuple)):
            continue
        failed_set = {str(f) for f in failed}
        if "schema_validation" in failed_set or "required_fields" in failed_set:
            quarantine_schema_mismatch += 1
        elif "evidence_refs" in failed_set or "evidence_quality" in failed_set:
            quarantine_evidence_missing += 1
        elif "causal_chain" in failed_set or "propagation" in failed_set:
            quarantine_causal_broken += 1
        else:
            quarantine_other += 1

    return AdjudicationFacts(
        prosecution_coverage=str(court_result.get("prosecution_coverage") or ""),
        defense_coverage=str(court_result.get("defense_coverage") or ""),
        valid_error_certificate_count=nonnegative_int(
            court_result.get("valid_error_certificate_count")
        ),
        valid_defense_certificate_count=nonnegative_int(
            court_result.get("valid_defense_certificate_count")
        ),
        surviving_error_count=sequence_count(
            court_result.get("surviving_error_certificate_ids")
        ),
        defeated_error_count=sequence_count(
            court_result.get("defeated_error_certificate_ids")
        ),
        quarantined_error_count=sequence_count(quarantined_errors),
        quarantined_defense_count=sequence_count(quarantined_defenses),
        consensus_gate_applied=bool(court_result.get("consensus_gate_applied")),
        consensus_gate_blocked_count=sequence_count(blocked),
        required_candidate_count=required_count,
        fully_quarantined=(
            required_count > 0
            and sequence_count(quarantined_errors) >= required_count
        ),
        boundary_path_state=str(boundary_path_state or ""),
        boundary_authority_valid=bool(boundary_authority_valid),
        terminal_state=str(court_result.get("state") or ""),
        # v6.1 扩展：上诉与定位状态
        appeal_status=str(court_result.get("appeal_status") or ""),
        appeal_triggered=bool(court_result.get("appeal_triggered", False)),
        localization_status=str(court_result.get("localization_status") or ""),
        responsible_step_available=bool(
            court_result.get("responsible_step") is not None
        ),
        # v6.1 扩展：隔离原因分布（从审计记录推导）
        quarantine_reason_schema_mismatch=quarantine_schema_mismatch,
        quarantine_reason_evidence_missing=quarantine_evidence_missing,
        quarantine_reason_causal_broken=quarantine_causal_broken,
        quarantine_reason_other=quarantine_other,
    )


# ============================================================
# L2 precondition predicates (closed vocabulary)
# ============================================================
# Each predicate is a pure function of AdjudicationFacts. Policy items may
# reference ONLY these names; the evaluator resolves them mechanically. The
# names and their semantics are owned by the versioned baseline registry in
# ``vocabulary_registry`` (single source of truth); the dict below is a
# derived read-only view kept for callers that resolve predicates by name.

from .vocabulary_registry import L2_REGISTRY  # noqa: E402

L2_PREDICATES: dict[str, Any] = {
    name: L2_REGISTRY.predicate_fn(name)
    for name in L2_REGISTRY.predicate_names()
}
L2_PREDICATE_NAMES = tuple(L2_PREDICATES)


# ============================================================
# L2 actions (closed vocabulary)
# ============================================================
# The versioned baseline registry (``vocabulary_registry.L2_REGISTRY``) is
# the single source of truth for what the reflection prompt may propose AND
# what the runtime can execute; the constants below are registry-derived
# views. Every listed action is applied deterministically by the policy
# evaluator inside the proof court. Actions that would require provider
# re-execution (``reclassify_candidate_state``, ``require_route_replay``)
# are reserved in ``ENGINE_LEVEL_ACTIONS`` but excluded from ``L2_ACTIONS``
# until the runtime engine implements them, so a proposal for one fails at
# parse time as ``unknown_action`` instead of reaching a runtime rejection.

POLICY_SIDES = frozenset({"prosecution", "defense"})
COVERAGE_DOWNGRADE_STATES = frozenset({"incomplete", "unresolved"})

ACTION_DOWNGRADE_COVERAGE = "downgrade_coverage"
ACTION_INVALIDATE_COVERAGE_CLAIM = "invalidate_coverage_claim"
ACTION_RECLASSIFY_CANDIDATE_STATE = "reclassify_candidate_state"
ACTION_REQUIRE_ROUTE_REPLAY = "require_route_replay"
ACTION_EMIT_TELEMETRY_ONLY = "emit_telemetry_only"
ACTION_CONSUME_VERIFIED_CLEAN_BOUNDARY = "consume_verified_clean_boundary"
# v6.1 扩展：新动作常量
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
    # v6.1 扩展：新动作参数验证
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

# Prompt-facing feedback codes accompanying a non-executed route.
FEEDBACK_POLICY_ROUTE_UNAVAILABLE = "policy_route_unavailable"


# ============================================================
# L2 policy item and attribution types
# ============================================================

POLICY_GENOME_CAPACITY = 12


@dataclass(frozen=True)
class PolicyItem:
    """One evolvable adjudication policy: (observable precondition, action).

    The precondition references only closed predicate names over
    ``AdjudicationFacts``; the action is one of the enumerated actions with
    closed-vocabulary parameters. Semantics are fixed by the evaluator —
    the LLM proposes items but never interprets them at runtime.
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


@dataclass(frozen=True)
class FailureAttribution:
    """Outcome of the deterministic failure classifier.

    layer: one of FAILURE_LAYERS. artifact: an L1 module id when layer is
    L1, an L2 artifact name when layer is L2, empty otherwise. evidence:
    addressable field names/values backing the classification.
    """
    layer: str
    artifact: str
    reason_code: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.layer in (LAYER_L1, LAYER_L2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "artifact": self.artifact,
            "reason_code": self.reason_code,
            "evidence": dict(self.evidence),
        }


# ============================================================
# L1 rule slot contract (vocabulary anchors)
# ============================================================
# L1 rules keep natural-language text for prompt injection but are stored
# and gated as slots. The condition slot is free English text describing a
# trajectory-observable situation; the action slot is free English text
# within the owning module's charter. Structural validation lives in
# ``rule_contract``; the charter (allowed surfaces per module) lives in
# ``module_charter``. Both consume these shared anchors.

RULE_SLOTS = ("condition", "action", "evidence_anchor")
MODULE_STAGE_BY_ID: dict[str, str] = dict(MODULE_STAGE)
