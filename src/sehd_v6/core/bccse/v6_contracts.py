"""SEHD v6 shared contracts: closed vocabularies and core data types.

Single source of truth for the two-layer evolvable surface introduced in
the v6 direction (HANDOFF_V6_ADJUDICATION_EVOLUTION.md):

- L1 provider-rule layer: natural-language rules with slot structure,
  injected into provider prompts (see ``rule_contract`` / ``genome``).
- L2 adjudication-policy layer: closed-vocabulary policy items evaluated
  deterministically by the proof court (see ``policy_genome`` /
  ``policy_evaluator``).

Every predicate, action, layer, and artifact token used by the failure
classifier, the policy genome, and the policy evaluator is defined here so
the attribution vocabulary cannot drift between modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
L2_ARTIFACTS = frozenset({
    L2_ARTIFACT_CONSENSUS_GATE,
    L2_ARTIFACT_COVERAGE_VALIDITY,
    L2_ARTIFACT_BOUNDARY_CONSUMPTION,
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

@dataclass(frozen=True)
class AdjudicationFacts:
    prosecution_coverage: str = ""
    defense_coverage: str = ""
    valid_error_certificate_count: int = 0
    valid_defense_certificate_count: int = 0
    surviving_error_count: int = 0
    defeated_error_count: int = 0
    quarantined_error_count: int = 0
    quarantined_defense_count: int = 0
    consensus_gate_applied: bool = False
    consensus_gate_blocked_count: int = 0
    required_candidate_count: int = 0
    fully_quarantined: bool = False
    boundary_path_state: str = ""
    terminal_state: str = ""

    @property
    def total_certificate_count(self) -> int:
        return (
            self.valid_error_certificate_count
            + self.valid_defense_certificate_count
        )


def build_adjudication_facts(
    court_result: dict[str, Any],
    *,
    boundary_path_state: str = "",
) -> AdjudicationFacts:
    """Derive the addressable fact snapshot from one proof-court result."""
    blocked = court_result.get("consensus_gate_blocked_ids") or []
    quarantined_errors = court_result.get("quarantined_error_certificate_ids") or []
    quarantined_defenses = court_result.get("quarantined_defense_certificate_ids") or []
    required_count = int(court_result.get("required_candidate_count") or 0)
    return AdjudicationFacts(
        prosecution_coverage=str(court_result.get("prosecution_coverage") or ""),
        defense_coverage=str(court_result.get("defense_coverage") or ""),
        valid_error_certificate_count=int(
            court_result.get("valid_error_certificate_count") or 0
        ),
        valid_defense_certificate_count=int(
            court_result.get("valid_defense_certificate_count") or 0
        ),
        surviving_error_count=len(
            court_result.get("surviving_error_certificate_ids") or []
        ),
        defeated_error_count=len(
            court_result.get("defeated_error_certificate_ids") or []
        ),
        quarantined_error_count=len(quarantined_errors),
        quarantined_defense_count=len(quarantined_defenses),
        consensus_gate_applied=bool(court_result.get("consensus_gate_applied")),
        consensus_gate_blocked_count=len(blocked),
        required_candidate_count=required_count,
        fully_quarantined=(
            required_count > 0
            and len(quarantined_errors) >= required_count
        ),
        boundary_path_state=str(boundary_path_state or ""),
        terminal_state=str(court_result.get("state") or ""),
    )


# ============================================================
# L2 precondition predicates (closed vocabulary)
# ============================================================
# Each predicate is a pure function of AdjudicationFacts. Policy items may
# reference ONLY these names; the evaluator resolves them mechanically.

def _pred_consensus_gate_blocked_nonempty(facts: AdjudicationFacts) -> bool:
    return facts.consensus_gate_applied and facts.consensus_gate_blocked_count > 0


def _pred_defense_coverage_complete_and_zero_certificates(
    facts: AdjudicationFacts,
) -> bool:
    return (
        facts.defense_coverage == "complete"
        and facts.valid_defense_certificate_count == 0
    )


def _pred_boundary_state_conflicts_with_terminal(facts: AdjudicationFacts) -> bool:
    return (
        facts.boundary_path_state == "verified_clean"
        and facts.terminal_state == "unresolved"
    )


def _pred_surviving_errors_nonempty_and_verdict_clean(
    facts: AdjudicationFacts,
) -> bool:
    return facts.surviving_error_count > 0 and facts.terminal_state == "verified_clean"


def _pred_all_candidates_quarantined(facts: AdjudicationFacts) -> bool:
    return facts.fully_quarantined


L2_PREDICATES: dict[str, Any] = {
    "consensus_gate_blocked_nonempty": _pred_consensus_gate_blocked_nonempty,
    "defense_coverage_complete_and_zero_certificates": (
        _pred_defense_coverage_complete_and_zero_certificates
    ),
    "boundary_state_conflicts_with_terminal": _pred_boundary_state_conflicts_with_terminal,
    "surviving_errors_nonempty_and_verdict_clean": (
        _pred_surviving_errors_nonempty_and_verdict_clean
    ),
    "all_candidates_quarantined": _pred_all_candidates_quarantined,
}
L2_PREDICATE_NAMES = tuple(L2_PREDICATES)


# ============================================================
# L2 actions (closed vocabulary)
# ============================================================
# Court-local actions are applied by the deterministic evaluator inside
# adjudication. Engine-level actions are surfaced as effects for the
# runtime engine to interpret (they require provider re-execution and
# therefore cannot be evaluated inside the court).

POLICY_SIDES = frozenset({"prosecution", "defense"})
COVERAGE_DOWNGRADE_STATES = frozenset({"incomplete", "unresolved"})
CANDIDATE_TRI_STATES = frozenset({"verified_clean", "verified_error", "unresolved"})

ACTION_DOWNGRADE_COVERAGE = "downgrade_coverage"
ACTION_INVALIDATE_COVERAGE_CLAIM = "invalidate_coverage_claim"
ACTION_RECLASSIFY_CANDIDATE_STATE = "reclassify_candidate_state"
ACTION_REQUIRE_ROUTE_REPLAY = "require_route_replay"
ACTION_EMIT_TELEMETRY_ONLY = "emit_telemetry_only"

COURT_LOCAL_ACTIONS = frozenset({
    ACTION_DOWNGRADE_COVERAGE,
    ACTION_INVALIDATE_COVERAGE_CLAIM,
    ACTION_EMIT_TELEMETRY_ONLY,
})
ENGINE_LEVEL_ACTIONS = frozenset({
    ACTION_RECLASSIFY_CANDIDATE_STATE,
    ACTION_REQUIRE_ROUTE_REPLAY,
})
L2_ACTIONS = COURT_LOCAL_ACTIONS | ENGINE_LEVEL_ACTIONS

# Required parameter keys per action; values are validated against the
# vocabularies below the table.
ACTION_PARAMS: dict[str, tuple[str, ...]] = {
    ACTION_DOWNGRADE_COVERAGE: ("side", "to_state"),
    ACTION_INVALIDATE_COVERAGE_CLAIM: ("side",),
    ACTION_RECLASSIFY_CANDIDATE_STATE: ("candidate_ref", "state"),
    ACTION_REQUIRE_ROUTE_REPLAY: ("route_key",),
    ACTION_EMIT_TELEMETRY_ONLY: (),
}


def validate_policy_params(action: str, params: dict[str, Any]) -> tuple[bool, str]:
    """Check action parameters against the closed vocabularies."""
    if action not in L2_ACTIONS:
        return False, f"unknown_action:{action}"
    required = ACTION_PARAMS.get(action, ())
    for key in required:
        if key not in params:
            return False, f"missing_param:{key}"
    side = params.get("side")
    if "side" in required and side not in POLICY_SIDES:
        return False, f"invalid_side:{side}"
    to_state = params.get("to_state")
    if action == ACTION_DOWNGRADE_COVERAGE and to_state not in COVERAGE_DOWNGRADE_STATES:
        return False, f"invalid_downgrade_state:{to_state}"
    state = params.get("state")
    if action == ACTION_RECLASSIFY_CANDIDATE_STATE and state not in CANDIDATE_TRI_STATES:
        return False, f"invalid_candidate_state:{state}"
    if action == ACTION_RECLASSIFY_CANDIDATE_STATE and not str(
        params.get("candidate_ref") or ""
    ).strip():
        return False, "missing_param:candidate_ref"
    if action == ACTION_REQUIRE_ROUTE_REPLAY and not str(
        params.get("route_key") or ""
    ).strip():
        return False, "missing_param:route_key"
    return True, ""


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
