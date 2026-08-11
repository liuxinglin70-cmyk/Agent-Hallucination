"""Phase 4 causal parity harness (plan §3.3 level 2 -- offline contract check).

Two verification surfaces, both deterministic and offline (no provider
calls), per plan §3.3 R2:

1. Failure-shape scenario table.
   For every enumerated failure shape (missing/malformed artifact, consensus
   blocked, hollow coverage, boundary conflict, appeal pending/failed,
   localization fallback, responsible step missing, quarantine pattern,
   runtime invalid, internal inconsistency, clean/error no-fall, etc.) one
   synthetic detection row is built and routed through the new causal
   pipeline:

       project_detection -> build_causal_cut -> build_causal_episode
                                                     -> repairability.analyze

   The triple ``(owner, skill_layer, repairability_status)`` is asserted
   against the plan §2.3 / §2.5 semantics. Unaddressable scenarios MUST
   yield ``UNADDRESSABLE_FRAMEWORK_DEFECT`` (or ``FRAMEWORK_DEFECT`` for
   detector boundary origins), never a no-edit / abstention verdict.

2. Classification consistency against the frozen old classifier.
   For every shape the old ``failure_classifier.classify_failure`` can
   process, the new ``build_causal_cut`` is required to agree on
   ``skill_layer`` and ``reason_code``. ``owner`` parity is asserted
   unless the case is a documented C3/C4 deviation; in those scenarios the
   harness accepts the change and prints the §-clause that justifies it.

Plan §3.3 R2: the warm-cache detection replay is unavailable on this
machine (cache root empty), so this harness degrades to offline contract
checks. The degradation is reported loudly below, never silently skipped.

Run:  python tools/parity/compare_causal.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# tools/parity is the only location that may import both packages.
from src.sehd_v6.core.bccse import failure_classifier as old_classifier  # noqa: E402
from src.sehd_v6.core.bccse import failure_episode as old_episode  # noqa: E402
from src.sehd_v6.core.bccse import step_attributor as old_attributor  # noqa: E402
from src.sehd_v6.core.bccse import v6_contracts as old_v6  # noqa: E402

from sehd_rise.causal.cut import (  # noqa: E402
    ORIGIN_PROVIDER_GENERATION,
    ORIGIN_ROUTE_CONFIG,
    ORIGIN_TRANSPORT_PARSE,
    ORIGIN_VALIDATOR_CONTRACT,
)
from sehd_rise.causal.episode import (  # noqa: E402
    build_causal_episode,
    validate_causal_episode,
)
from sehd_rise.causal.repairability import (  # noqa: E402
    RepairabilityStatus,
    analyze,
)
from sehd_rise.causal.signals import project_detection  # noqa: E402
from sehd_rise.causal.cut import build_causal_cut  # noqa: E402
from sehd_rise.platform.jsonio import canonical_json  # noqa: E402
from sehd_rise.skills.vocabulary.charters import (  # noqa: E402
    MODULE_ACTION_OPCODES,
    MODULE_IDS,
)
from sehd_rise.skills.vocabulary.contracts import (  # noqa: E402
    L2_ARTIFACT_APPEAL_MECHANISM,
    L2_ARTIFACT_BOUNDARY_CONSUMPTION,
    L2_ARTIFACT_CONSENSUS_GATE,
    L2_ARTIFACT_COVERAGE_VALIDITY,
    LAYER_DETECTOR,
    LAYER_L1,
    LAYER_L2,
    LAYER_NONE,
)

CHECKS: list[str] = []
FAILURES: list[str] = []
DEV: list[str] = []


# ============================================================
# Assertion helpers
# ============================================================

def _ok(name: str) -> None:
    CHECKS.append(name)
    print(f"  ok   {name}")


def _fail(name: str, detail: str) -> None:
    FAILURES.append(name)
    print(f"  FAIL {name}")
    print(f"       {detail}")


def _dev(name: str, ref: str) -> None:
    DEV.append(name)
    print(f"  DEV  {name}")
    print(f"       deviation: {ref}")


def _check_eq(name: str, expected: Any, actual: Any) -> bool:
    if canonical_json(expected) == canonical_json(actual):
        _ok(name)
        return True
    _fail(name, f"expected={expected!r} actual={actual!r}")
    return False


def _check_true(name: str, condition: bool, detail: str = "") -> bool:
    if condition:
        _ok(name)
        return True
    _fail(name, detail or "condition false")
    return False


# ============================================================
# Scenario record
# ============================================================

@dataclass
class Scenario:
    """One synthetic failure-shape scenario."""

    case_id: str
    description: str
    row: dict[str, Any]
    boundary_path_state: str = ""
    use_gold: bool = False
    gold_label: bool | None = None
    allowed_modules: tuple[str, ...] = ()
    evolution_direction: str = ""
    expected_old_layer: str = ""
    expected_old_artifact: str = ""
    expected_old_reason: str = ""
    expected_new_layer: str = ""
    expected_new_owner: str = ""
    expected_new_reason: str = ""
    expected_repairability: RepairabilityStatus = RepairabilityStatus.NON_EVOLVABLE
    deviation_note: str = ""


# ============================================================
# Synthetic packet / certificate builders (kept self-contained)
# ============================================================

def _valid_error_cert(cert_id: str, candidate: str, origin: str, impact: str) -> dict[str, Any]:
    return {
        "certificate_id": cert_id,
        "source_candidate_id": candidate,
        "origin_event_id": origin,
        "claim_kind": "agent_assertion",
        "norm_source": "external_evidence",
        "norm_evidence_refs": [origin],
        "breach_type": "fabrication",
        "violation_evidence_refs": [origin],
        "agent_control_event_id": origin,
        "agent_control_evidence_refs": [origin],
        "impact_kind": "final_answer",
        "impact_event_ids": [impact],
        "impact_evidence_refs": [impact],
        "recovery_state": "none",
        "recovery_evidence_refs": [],
        "benchmark_causal_status": "causes_incorrect_result",
        "benchmark_causal_evidence_refs": [impact],
        "rationale": "the fabricated claim reached the final answer",
    }


def _valid_defense_cert(
    cert_id: str, candidate: str, origin: str,
    defense_type: str = "insufficient_evidence",
) -> dict[str, Any]:
    return {
        "certificate_id": cert_id,
        "target_candidate_id": candidate,
        "target_origin_event_id": origin,
        "defense_type": defense_type,
        "evidence_refs": [origin],
        "rationale": "no dominating boundary is provable from the trajectory",
    }


def _base_row(
    *,
    state: str = "verified_error",
    runtime_valid: bool = True,
    decision_authority: str = "authoritative",
    prosecution_coverage: str = "complete",
    defense_coverage: str = "incomplete",
    prosecution_candidates: list[dict[str, Any]] | None = None,
    defense_candidates: list[dict[str, Any]] | None = None,
    census: list[dict[str, Any]] | None = None,
    proof_adjudication_extras: dict[str, Any] | None = None,
    evidence_health_extras: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A detector result row with sensible defaults for parity scenarios."""
    if prosecution_candidates is None:
        prosecution_candidates = [_valid_error_cert("err_1", "cand_1", "event_001", "event_003")]
    if defense_candidates is None:
        defense_candidates = [_valid_defense_cert("def_1", "cand_1", "event_001")]
    if census is None:
        census = [
            {"candidate_id": "cand_1", "candidate_kind": "agent_assertion",
             "origin_hypothesis_event_id": "event_001"},
            {"candidate_id": "cand_2", "candidate_kind": "agent_assertion",
             "origin_hypothesis_event_id": "event_002"},
        ]
    proof_adjudication = {
        "state": state,
        "prosecution_coverage": prosecution_coverage,
        "defense_coverage": defense_coverage,
        "valid_error_certificate_count": len(prosecution_candidates),
        "valid_defense_certificate_count": len(defense_candidates),
        "surviving_error_certificate_ids": (
            ["err_1"] if state == "verified_error" and prosecution_candidates else []
        ),
        "quarantined_error_certificate_ids": [],
        "quarantined_defense_certificate_ids": [],
        "consensus_gate_applied": False,
        "consensus_gate_blocked_ids": [],
        "required_candidate_count": 1,
        "error_audits": [],
        "defense_audits": [],
    }
    if proof_adjudication_extras:
        proof_adjudication.update(proof_adjudication_extras)
    evidence_health = {
        "required_candidate_count": 2,
        "construct_accepted_candidate_count": 2,
        "construct_raw_candidate_count": 2,
        "appeal_status": "not_required",
        "quarantined_error_certificate_count": 0,
        "quarantined_defense_certificate_count": 0,
        "focused_prosecution_quarantined_candidate_count": 0,
    }
    if evidence_health_extras:
        evidence_health.update(evidence_health_extras)
    row: dict[str, Any] = {
        "state": state,
        "runtime_valid": runtime_valid,
        "decision_authority": decision_authority,
        "census": census,
        "combined_prosecution": {
            "coverage_state": prosecution_coverage,
            "candidates": prosecution_candidates,
        },
        "combined_defense": {
            "coverage_state": defense_coverage,
            "candidates": defense_candidates,
        },
        "proof_adjudication": proof_adjudication,
        "evidence_health": evidence_health,
        "responsible_event_id": "event_001" if state != "unresolved" else None,
        "responsible_step": 1 if state != "unresolved" else None,
    }
    if extra:
        row.update(extra)
    return row


def _to_record(row: Mapping[str, Any], *, boundary_path_state: str = "", provider_artifacts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a `record` the old classifier reads from a row."""
    record: dict[str, Any] = {
        "adjudication": dict(row.get("proof_adjudication") or {}),
        "boundary_path_state": boundary_path_state,
    }
    if provider_artifacts is not None:
        record["provider_artifacts"] = provider_artifacts
    return record


# ============================================================
# Scenario library (failure-shape table)
# ============================================================

ALL_MODULES = tuple(MODULE_IDS)
L2_ALLOWED = (
    L2_ARTIFACT_BOUNDARY_CONSUMPTION,
    L2_ARTIFACT_CONSENSUS_GATE,
    L2_ARTIFACT_COVERAGE_VALIDITY,
    L2_ARTIFACT_APPEAL_MECHANISM,
)


def _all_scenarios() -> list[Scenario]:
    """Enumerate one synthetic row per failure shape, plus parity cases."""
    scenarios: list[Scenario] = []

    # --- 1. Missing provider artifacts (L1 via Rule 1) ---
    err_cert = _valid_error_cert("err_1", "cand_1", "event_001", "event_003")
    def_cert = _valid_defense_cert("def_1", "cand_1", "event_001")

    # 1a. Missing candidate (no census / accepted=0): old = L1 candidate_trigger;
    #     new = LAYER_NONE because no candidate artifact surfaces the
    #     provider_artifacts entry for the missing case simply by an empty
    #     census. Build with required=1, accepted=0, raw=0 -> missing candidate.
    scenarios.append(Scenario(
        case_id="missing_candidate",
        description="required candidate never produced",
        row=_base_row(
            state="unresolved",
            census=[],
            prosecution_coverage="incomplete",
            defense_coverage="incomplete",
            prosecution_candidates=[],
            defense_candidates=[],
            evidence_health_extras={
                "required_candidate_count": 1,
                "construct_accepted_candidate_count": 0,
                "construct_raw_candidate_count": 0,
            },
        ),
        allowed_modules=ALL_MODULES,
        expected_old_layer=LAYER_L1,
        expected_old_artifact="candidate_trigger",
        expected_old_reason="provider_artifact_missing",
        expected_new_layer=LAYER_L1,
        expected_new_owner="candidate_trigger",
        expected_new_reason="provider_artifact_missing",
        expected_repairability=RepairabilityStatus.REPAIRABLE,
    ))

    # 1b. Malformed error_certificate (provider_generation_nonconformance):
    #     failed_invariants on an emitted error certificate.
    bad_error_cert_audit = {
        "certificate_id": "err_1",
        "candidate_id": "cand_1",
        "decision": "quarantine",
        "failed_checks": ["ordered_chain"],
        "failed_invariants": ["ordered_chain"],
        "failed_fields": ["ordered_chain"],
        "missing_fields": [],
    }
    scenarios.append(Scenario(
        case_id="malformed_error_cert_provider_generation",
        description="emitted error certificate fails a registered relational invariant",
        row=_base_row(
            state="verified_clean",
            prosecution_coverage="incomplete",
            defense_coverage="complete",
            defense_candidates=[_valid_defense_cert("def_2", "cand_1", "event_001")],
            proof_adjudication_extras={
                "valid_error_certificate_count": 0,
                "surviving_error_certificate_ids": [],
                "quarantined_error_certificate_ids": ["err_1"],
                "error_audits": [bad_error_cert_audit],
            },
            evidence_health_extras={
                "quarantined_error_certificate_count": 1,
            },
        ),
        allowed_modules=ALL_MODULES,
        expected_old_layer=LAYER_L1,
        expected_old_artifact="atomic_verifier_instruction",
        expected_old_reason="provider_artifact_malformed",
        expected_new_layer=LAYER_L1,
        expected_new_owner="atomic_verifier_instruction",
        expected_new_reason="provider_artifact_malformed",
        expected_repairability=RepairabilityStatus.REPAIRABLE,
    ))

    # 1c. Malformed error_certificate (validator_contract_fault):
    #     failed_fields only, no invariants / no missing fields.
    bad_validator_audit = {
        "certificate_id": "err_1",
        "candidate_id": "cand_1",
        "decision": "quarantine",
        "failed_checks": ["breach_type"],
        "failed_fields": ["breach_type"],
        "failed_invariants": [],
        "missing_fields": [],
    }
    scenarios.append(Scenario(
        case_id="malformed_error_cert_validator_contract",
        description="emitted error certificate fields rejected by the validator contract",
        row=_base_row(
            state="verified_clean",
            prosecution_coverage="incomplete",
            defense_coverage="complete",
            defense_candidates=[_valid_defense_cert("def_2", "cand_1", "event_001")],
            proof_adjudication_extras={
                "valid_error_certificate_count": 0,
                "surviving_error_certificate_ids": [],
                "quarantined_error_certificate_ids": ["err_1"],
                "error_audits": [bad_validator_audit],
            },
            evidence_health_extras={
                "quarantined_error_certificate_count": 1,
            },
        ),
        allowed_modules=ALL_MODULES,
        expected_old_layer=LAYER_DETECTOR,
        expected_old_artifact="inference_contract_boundary",
        expected_old_reason="provider_artifact_malformed",
        expected_new_layer=LAYER_DETECTOR,
        expected_new_owner="validator_contract_repair",
        expected_new_reason="provider_artifact_malformed",
        expected_repairability=RepairabilityStatus.FRAMEWORK_DEFECT,
        deviation_note=(
            "C4 (plan §2.5): the new cut names the boundary repair owner "
            "(validator_contract_repair) instead of the old generic "
            "'inference_contract_boundary' label. repairability.analyze "
            "classifies it as FRAMEWORK_DEFECT (never generates a Skill)."
        ),
    ))

    # 1d. Malformed error_certificate (transport_or_parse_fault): an appeal
    #     ``provider_error`` status with no surviving errors. The OLD and
    #     NEW ``project_provider_artifacts`` BOTH emit a malformed
    #     ``error_certificate`` artifact for this status (the artifact
    #     carries no ``source``, no failed_fields, no failed_invariants,
    #     no missing_fields); ``classify_malformed_origin`` therefore
    #     falls closed to ORIGIN_ROUTE_CONFIG (not ORIGIN_TRANSPORT_PARSE),
    #     and the cut routes it to the detector boundary as a malformed
    #     artifact. Documented: the ``transport_or_parse_fault`` branch in
    #     ``classify_malformed_origin`` is structurally unreachable through
    #     the standard projection path (it would require a non-empty
    #     ``source`` containing ``"provider_error"`` / ``"appeal_status"``
    #     that ``project_provider_artifacts`` never sets). The closed-vocab
    #     branch is preserved for cases that construct artifacts directly.
    scenarios.append(Scenario(
        case_id="malformed_error_cert_appeal_provider_error",
        description="appeal provider error emits a malformed error_certificate artifact",
        row=_base_row(
            state="verified_clean",
            prosecution_coverage="incomplete",
            defense_coverage="complete",
            defense_candidates=[_valid_defense_cert("def_2", "cand_1", "event_001")],
            proof_adjudication_extras={
                "valid_error_certificate_count": 0,
                "surviving_error_certificate_ids": [],
                "appeal_status": "provider_error",
                "appeal_triggered": True,
            },
            evidence_health_extras={
                "appeal_status": "provider_error",
                "quarantined_error_certificate_count": 0,
            },
        ),
        allowed_modules=ALL_MODULES,
        expected_old_layer=LAYER_DETECTOR,
        expected_old_artifact="inference_contract_boundary",
        expected_old_reason="provider_artifact_malformed",
        expected_new_layer=LAYER_DETECTOR,
        expected_new_owner="route_config_repair",
        expected_new_reason="provider_artifact_malformed",
        expected_repairability=RepairabilityStatus.FRAMEWORK_DEFECT,
        deviation_note=(
            "C4 (plan §2.5): both old and new ``project_provider_artifacts`` "
            "emit a malformed error_certificate for "
            "``appeal_status==\"provider_error\"`` (no source set by the "
            "projection), so ``classify_malformed_origin`` falls closed to "
            "ORIGIN_ROUTE_CONFIG rather than ORIGIN_TRANSPORT_PARSE. The new "
            "cut names the boundary repair owner (route_config_repair) "
            "directly instead of the old ``inference_contract_boundary`` "
            "alias; the verdict is FRAMEWORK_DEFECT, not a Skill. The "
            "transport_or_parse_fault branch is structurally unreachable via "
            "the standard projection path; both packages preserve it as a "
            "closed-vocabulary alias for direct-construction callers."
        ),
    ))

    # 1e. Malformed candidate with no typed audit (route_or_config_fault
    #     fall-closed default): the runner's malformed-candidate path emits
    #     ``malformed=True`` with empty failed_invariants/failed_fields/
    #     missing_fields when construct-stage raw candidates existed but none
    #     was accepted. ``classify_malformed_origin`` returns
    #     ORIGIN_ROUTE_CONFIG (fall-closed default); the cut routes it to
    #     the detector boundary as a FRAMEWORK_DEFECT.
    scenarios.append(Scenario(
        case_id="malformed_candidate_route_config",
        description="construct produced malformed raw candidate with no typed audit",
        row=_base_row(
            state="unresolved",
            census=[],
            prosecution_coverage="incomplete",
            defense_coverage="incomplete",
            prosecution_candidates=[],
            defense_candidates=[],
            evidence_health_extras={
                "required_candidate_count": 1,
                "construct_accepted_candidate_count": 0,
                "construct_raw_candidate_count": 1,
            },
        ),
        allowed_modules=ALL_MODULES,
        expected_old_layer=LAYER_DETECTOR,
        expected_old_artifact="inference_contract_boundary",
        expected_old_reason="provider_artifact_malformed",
        expected_new_layer=LAYER_DETECTOR,
        expected_new_owner="route_config_repair",
        expected_new_reason="provider_artifact_malformed",
        expected_repairability=RepairabilityStatus.FRAMEWORK_DEFECT,
        deviation_note=(
            "C4 (plan §2.5): malformed candidate with no typed audit "
            "falls closed to the route/config boundary; the new cut names "
            "the boundary repair owner directly (route_config_repair) "
            "instead of 'inference_contract_boundary'. The verdict is "
            "FRAMEWORK_DEFECT, never a Skill."
        ),
    ))

    # --- 2. L2 policy surfaces ---
    # 2a. Boundary conflict: boundary path=verified_clean but terminal unresolved.
    scenarios.append(Scenario(
        case_id="boundary_conflict",
        description="boundary path verdict clean but terminal unresolved",
        row=_base_row(
            state="unresolved",
            prosecution_coverage="complete",
            defense_coverage="complete",
            proof_adjudication_extras={
                "valid_error_certificate_count": 0,
                "surviving_error_certificate_ids": [],
            },
        ),
        boundary_path_state="verified_clean",
        allowed_modules=L2_ALLOWED,
        expected_old_layer=LAYER_L2,
        expected_old_artifact=L2_ARTIFACT_BOUNDARY_CONSUMPTION,
        expected_old_reason="boundary_terminal_conflict",
        expected_new_layer=LAYER_L2,
        expected_new_owner=L2_ARTIFACT_BOUNDARY_CONSUMPTION,
        expected_new_reason="boundary_terminal_conflict",
        expected_repairability=RepairabilityStatus.REPAIRABLE,
    ))

    # 2b. Consensus gate blocked nonempty + terminal unresolved.
    scenarios.append(Scenario(
        case_id="consensus_blocked",
        description="consensus gate blocked candidates and the terminal is unresolved",
        row=_base_row(
            state="unresolved",
            prosecution_coverage="incomplete",
            defense_coverage="incomplete",
            proof_adjudication_extras={
                "consensus_gate_applied": True,
                "consensus_gate_blocked_ids": ["cand_1", "cand_2"],
                "valid_error_certificate_count": 0,
                "surviving_error_certificate_ids": [],
            },
        ),
        allowed_modules=L2_ALLOWED,
        expected_old_layer=LAYER_L2,
        expected_old_artifact=L2_ARTIFACT_CONSENSUS_GATE,
        expected_old_reason="consensus_gate_blocked",
        expected_new_layer=LAYER_L2,
        expected_new_owner=L2_ARTIFACT_CONSENSUS_GATE,
        expected_new_reason="consensus_gate_blocked",
        expected_repairability=RepairabilityStatus.REPAIRABLE,
    ))

    # 2c. Hollow defense coverage.
    scenarios.append(Scenario(
        case_id="hollow_defense",
        description="defense coverage complete with zero valid defense certificates",
        row=_base_row(
            state="verified_error",
            defense_coverage="complete",
            defense_candidates=[],
            proof_adjudication_extras={
                "valid_defense_certificate_count": 0,
            },
        ),
        allowed_modules=L2_ALLOWED,
        expected_old_layer=LAYER_L2,
        expected_old_artifact=L2_ARTIFACT_COVERAGE_VALIDITY,
        expected_old_reason="hollow_complete_defense",
        expected_new_layer=LAYER_L2,
        expected_new_owner=L2_ARTIFACT_COVERAGE_VALIDITY,
        expected_new_reason="hollow_complete_defense",
        expected_repairability=RepairabilityStatus.REPAIRABLE,
    ))

    # 2d. Hollow prosecution coverage.
    scenarios.append(Scenario(
        case_id="hollow_prosecution",
        description="prosecution coverage complete with zero valid error certificates",
        row=_base_row(
            state="unresolved",
            prosecution_coverage="complete",
            prosecution_candidates=[],
            defense_coverage="complete",
            proof_adjudication_extras={
                "valid_error_certificate_count": 0,
                "surviving_error_certificate_ids": [],
            },
        ),
        allowed_modules=L2_ALLOWED,
        expected_old_layer=LAYER_L2,
        expected_old_artifact=L2_ARTIFACT_COVERAGE_VALIDITY,
        expected_old_reason="hollow_complete_prosecution",
        expected_new_layer=LAYER_L2,
        expected_new_owner=L2_ARTIFACT_COVERAGE_VALIDITY,
        expected_new_reason="hollow_complete_prosecution",
        expected_repairability=RepairabilityStatus.REPAIRABLE,
    ))

    # 2e. Appeal pending (terminal unresolved). The registered predicate
    #     ``appeal_pending_and_terminal_unresolved`` only fires when
    #     ``appeal_status == "incomplete_fail_closed_unresolved"`` so we match
    #     that exact value.
    scenarios.append(Scenario(
        case_id="appeal_pending",
        description="appeal pending blocks the terminal; terminal unresolved",
        row=_base_row(
            state="unresolved",
            prosecution_coverage="incomplete",
            defense_coverage="incomplete",
            proof_adjudication_extras={
                "appeal_status": "incomplete_fail_closed_unresolved",
                "appeal_triggered": True,
                "valid_error_certificate_count": 0,
                "surviving_error_certificate_ids": [],
            },
            evidence_health_extras={
                "appeal_status": "incomplete_fail_closed_unresolved",
            },
        ),
        allowed_modules=L2_ALLOWED,
        expected_old_layer=LAYER_L2,
        expected_old_artifact=L2_ARTIFACT_APPEAL_MECHANISM,
        expected_old_reason="appeal_pending_blocked",
        expected_new_layer=LAYER_L2,
        expected_new_owner=L2_ARTIFACT_APPEAL_MECHANISM,
        expected_new_reason="appeal_pending_blocked",
        expected_repairability=RepairabilityStatus.REPAIRABLE,
    ))

    # 2f. Appeal failed with surviving errors. The OLD projection emits a
    #     malformed ``error_certificate`` artifact for
    #     ``appeal_status==\"provider_error\"`` (the standard projection
    #     path), which makes Rule 1 (provider-artifact integrity) fire
    #     before Rule 4.2 (appeal_failed_with_surviving_errors). Both old
    #     and new cut bypass Rule 4.2 via the same path: the result is a
    #     detector-boundary malformed attribution, classified
    #     FRAMEWORK_DEFECT. Rule 4.2 remains in code as the registered
    #     predicate for callers that supply a non-shadowing artifacts list.
    scenarios.append(Scenario(
        case_id="appeal_failed",
        description="appeal failed with surviving errors still carrying the verdict",
        row=_base_row(
            state="verified_error",
            prosecution_coverage="complete",
            defense_coverage="incomplete",
            proof_adjudication_extras={
                "appeal_status": "provider_error",
                "appeal_triggered": True,
                "surviving_error_certificate_ids": ["err_1"],
            },
            evidence_health_extras={
                "appeal_status": "provider_error",
            },
        ),
        allowed_modules=ALL_MODULES,
        expected_old_layer=LAYER_DETECTOR,
        expected_old_artifact="inference_contract_boundary",
        expected_old_reason="provider_artifact_malformed",
        expected_new_layer=LAYER_DETECTOR,
        expected_new_owner="route_config_repair",
        expected_new_reason="provider_artifact_malformed",
        expected_repairability=RepairabilityStatus.FRAMEWORK_DEFECT,
        deviation_note=(
            "C4 (plan §2.5): the standard ``project_provider_artifacts`` "
            "emits a malformed error_certificate artifact for "
            "``appeal_status==\"provider_error\"`` with no source set, "
            "shadowing Rule 4.2 (appeal_failed_with_surviving_errors) via "
            "Rule 1 in both old and new. The new cut names the boundary "
            "repair owner (route_config_repair) directly; the verdict is "
            "FRAMEWORK_DEFECT. Rule 4.2 is a registered-closed-vocabulary "
            "branch preserved for callers that construct artifacts "
            "directly (no shadow from the projection)."
        ),
    ))

    # --- 3. L1 synthesized-field reasons ---
    # 3a. Localization fallback used.
    scenarios.append(Scenario(
        case_id="localization_fallback",
        description="counterfactual ranking fell back to temporal order",
        row=_base_row(
            state="verified_error",
            prosecution_coverage="complete",
            defense_coverage="incomplete",
            proof_adjudication_extras={
                "localization_status": "failed_temporal_fallback",
                "surviving_error_certificate_ids": ["err_1", "err_2"],
            },
        ),
        allowed_modules=ALL_MODULES,
        expected_old_layer=LAYER_L1,
        expected_old_artifact="earliest_origin_tiebreak",
        expected_old_reason="localization_temporal_fallback",
        expected_new_layer=LAYER_L1,
        expected_new_owner="earliest_origin_tiebreak",
        expected_new_reason="localization_temporal_fallback",
        expected_repairability=RepairabilityStatus.REPAIRABLE,
    ))

    # 3b. Responsible step missing with error verdict and localization attempted.
    #     Rule 4.4 ``responsible_step_missing_and_verdict_error`` requires
    #     ``localization_status != ""`` so the rule only fires when the
    #     court actually emitted a localization status. Setting
    #     ``localization_status = "complete"`` (localization succeeded but
    #     ``responsible_step = None``) differentiates this scenario from
    #     Rule 4.3 (which requires
    #     ``localization_status == "failed_temporal_fallback"``).
    scenarios.append(Scenario(
        case_id="responsible_step_missing",
        description="error verdict with localization attempted but no responsible step",
        row=_base_row(
            state="verified_error",
            prosecution_coverage="complete",
            defense_coverage="incomplete",
            extra={
                "responsible_step": None,
                "responsible_event_id": None,
            },
            proof_adjudication_extras={
                "localization_status": "complete",
                "responsible_step": None,
                "surviving_error_certificate_ids": ["err_1"],
            },
        ),
        allowed_modules=ALL_MODULES,
        expected_old_layer=LAYER_L1,
        expected_old_artifact="earliest_origin_tiebreak",
        expected_old_reason="responsible_step_missing",
        expected_new_layer=LAYER_L1,
        expected_new_owner="earliest_origin_tiebreak",
        expected_new_reason="responsible_step_missing",
        expected_repairability=RepairabilityStatus.REPAIRABLE,
    ))

    # 3c. Quarantine pattern dominant -- schema mismatch. Set
    #     ``failed_fields=[]`` explicitly so ``project_provider_artifacts``
    #     does NOT emit a malformed artifact (the legacy path that would
    #     derive fields from ``failed_checks`` is bypassed); the
    #     AdjudicationFacts bucket classifier still sees
    #     ``failed_checks=["breach_type"]`` and buckets it into schema
    #     mismatch, so Rule 4.5 fires (quarantine_schema_mismatch_dominant).
    err_audit_schema = {
        "certificate_id": "err_1",
        "candidate_id": "cand_1",
        "decision": "quarantine",
        "failed_checks": ["breach_type"],
        "failed_fields": [],
        "failed_invariants": [],
        "missing_fields": [],
    }
    scenarios.append(Scenario(
        case_id="quarantine_schema_mismatch",
        description="quarantine audit failed-checks dominated by schema mismatch fields",
        row=_base_row(
            state="unresolved",
            prosecution_coverage="incomplete",
            defense_coverage="incomplete",
            proof_adjudication_extras={
                "valid_error_certificate_count": 0,
                "surviving_error_certificate_ids": [],
                "quarantined_error_certificate_ids": ["err_1"],
                "error_audits": [err_audit_schema],
            },
            evidence_health_extras={
                "quarantined_error_certificate_count": 1,
            },
        ),
        allowed_modules=ALL_MODULES,
        expected_old_layer=LAYER_L1,
        expected_old_artifact="atomic_verifier_instruction",
        expected_old_reason="quarantine_pattern_dominant",
        expected_new_layer=LAYER_L1,
        expected_new_owner="atomic_verifier_instruction",
        expected_new_reason="quarantine_pattern_dominant",
        expected_repairability=RepairabilityStatus.REPAIRABLE,
    ))

    # 3d. Quarantine pattern dominant -- evidence missing. Same pattern:
    #     explicit ``failed_fields=[]`` so no malformed artifact is emitted;
    #     ``failed_checks=["norm_evidence"]`` buckets into evidence_missing.
    err_audit_evidence = {
        "certificate_id": "err_1",
        "candidate_id": "cand_1",
        "decision": "quarantine",
        "failed_checks": ["norm_evidence"],
        "failed_fields": [],
        "failed_invariants": [],
        "missing_fields": [],
    }
    scenarios.append(Scenario(
        case_id="quarantine_evidence_missing",
        description="quarantine audit failed-checks dominated by evidence-missing fields",
        row=_base_row(
            state="unresolved",
            prosecution_coverage="incomplete",
            defense_coverage="incomplete",
            proof_adjudication_extras={
                "valid_error_certificate_count": 0,
                "surviving_error_certificate_ids": [],
                "quarantined_error_certificate_ids": ["err_1"],
                "error_audits": [err_audit_evidence],
            },
            evidence_health_extras={
                "quarantined_error_certificate_count": 1,
            },
        ),
        allowed_modules=ALL_MODULES,
        expected_old_layer=LAYER_L1,
        expected_old_artifact="evidence_obligation",
        expected_old_reason="quarantine_pattern_dominant",
        expected_new_layer=LAYER_L1,
        expected_new_owner="evidence_obligation",
        expected_new_reason="quarantine_pattern_dominant",
        expected_repairability=RepairabilityStatus.REPAIRABLE,
    ))

    # --- 4. Fall-through / DETECTOR / runtime ---
    # 4a. Unresolved with no failure shape: NONE / no_failure_shape / NON_EVOLVABLE.
    scenarios.append(Scenario(
        case_id="unresolved_no_failure_shape",
        description="unresolved terminal but no addressable failure shape",
        row=_base_row(
            state="unresolved",
            prosecution_coverage="complete",
            defense_coverage="complete",
            prosecution_candidates=[
                _valid_error_cert("err_1", "cand_1", "event_001", "event_003")
            ],
            defense_candidates=[_valid_defense_cert("def_1", "cand_1", "event_001")],
            proof_adjudication_extras={
                "valid_error_certificate_count": 1,
                "surviving_error_certificate_ids": ["err_1"],
            },
        ),
        allowed_modules=ALL_MODULES,
        expected_old_layer=LAYER_NONE,
        expected_old_artifact="",
        expected_old_reason="no_failure_shape",
        expected_new_layer=LAYER_NONE,
        expected_new_owner="",
        expected_new_reason="no_failure_shape",
        expected_repairability=RepairabilityStatus.NON_EVOLVABLE,
    ))

    # 4b. Internal inconsistency: surviving errors nonempty + verified_clean.
    scenarios.append(Scenario(
        case_id="internal_inconsistency_surviving_clean",
        description="verified_clean verdict with surviving error certificates",
        row=_base_row(
            state="verified_clean",
            prosecution_coverage="incomplete",
            defense_coverage="complete",
            proof_adjudication_extras={
                "valid_error_certificate_count": 0,
                "surviving_error_certificate_ids": ["err_1"],
            },
        ),
        allowed_modules=ALL_MODULES,
        expected_old_layer=LAYER_DETECTOR,
        expected_old_artifact="",
        expected_old_reason="internal_inconsistency",
        expected_new_layer=LAYER_DETECTOR,
        expected_new_owner="",
        expected_new_reason="internal_inconsistency",
        expected_repairability=RepairabilityStatus.NON_EVOLVABLE,
    ))

    # 4c. Runtime invalid: NON_EVOLVABLE. The old classifier fell through to
    #     Rule 5 (``LAYER_NONE / no_failure_shape``) because the
    #     adjudication facts' terminal_state is "unresolved" with no
    #     blocking predicates; the new cut reads the diagnostic_only signal
    #     at projection time and yields the same ``LAYER_NONE /
    #     no_failure_shape``. Repairability then returns NON_EVOLVABLE (no
    #     evolvable surface was ever claimed). The deviation note records
    #     that the trigger/repairability split (plan §2.1) absorbs the
    #     ``should_evolve`` decision here, even though the layer/reason
    #     pair is identical between old and new.
    scenarios.append(Scenario(
        case_id="runtime_invalid",
        description="runtime invalid row carries no authoritative verdict",
        row=_base_row(
            state="unresolved",
            runtime_valid=False,
            decision_authority="invalid",
            extra={
                "decision_authority": "invalid",
            },
        ),
        allowed_modules=ALL_MODULES,
        expected_old_layer=LAYER_NONE,
        expected_old_artifact="",
        expected_old_reason="no_failure_shape",
        expected_new_layer=LAYER_NONE,
        expected_new_owner="",
        expected_new_reason="no_failure_shape",
        expected_repairability=RepairabilityStatus.NON_EVOLVABLE,
        deviation_note=(
            "C1 (plan §2.1): the runtime-invalid row never claimed an "
            "evolvable surface, so the repairability authority emits "
            "NON_EVOLVABLE -- consistent with old LAYER_NONE/no_failure_shape "
            "but now via the single should_evolve-absorbing authority. "
            "No (layer, owner, reason) deviation occurs."
        ),
    ))

    # --- 5. No-abstention success cases ---
    # 5a. Verified clean with full coverage: NONE / no_failure_shape.
    scenarios.append(Scenario(
        case_id="verified_clean_no_failure",
        description="verified_clean with full adversarial coverage and no surviving errors",
        row=_base_row(
            state="verified_clean",
            prosecution_coverage="complete",
            defense_coverage="complete",
            prosecution_candidates=[
                _valid_error_cert("err_1", "cand_1", "event_001", "event_003"),
            ],
            defense_candidates=[
                _valid_defense_cert("def_1", "cand_1", "event_001"),
            ],
            proof_adjudication_extras={
                "valid_error_certificate_count": 1,
                "valid_defense_certificate_count": 1,
                "surviving_error_certificate_ids": [],
                "defeated_error_certificate_ids": ["err_1"],
            },
        ),
        allowed_modules=ALL_MODULES,
        expected_old_layer=LAYER_NONE,
        expected_old_artifact="",
        expected_old_reason="no_failure_shape",
        expected_new_layer=LAYER_NONE,
        expected_new_owner="",
        expected_new_reason="no_failure_shape",
        expected_repairability=RepairabilityStatus.NON_EVOLVABLE,
    ))

    return scenarios


# ============================================================
# Scenario runner
# ============================================================

def _run_scenario(scenario: Scenario) -> None:
    print(f"-- {scenario.case_id} -- {scenario.description}")
    row = dict(scenario.row)
    if scenario.use_gold and scenario.gold_label is not None:
        row["gold_label"] = scenario.gold_label

    # Project the old signal so the old classifier's provider_artifacts
    # input matches what the runner would have built (it used
    # failure_episode.project_provider_artifacts from the old package).
    # We do NOT need a "primary_type" hint here because hints default to ""
    # and the scenarios do not rely on the FN+omission path.
    old_signal = old_attributor.attribute_failure(row)
    old_artifacts = old_episode.project_provider_artifacts(
        detection_result=row,
        signal=old_signal,
        primary_type="",
    )
    record = _to_record(row, boundary_path_state=scenario.boundary_path_state, provider_artifacts=old_artifacts)
    old_attr = old_classifier.classify_failure(record)
    _check_eq(
        f"{scenario.case_id} old classifier layer",
        scenario.expected_old_layer, old_attr.layer,
    )
    _check_eq(
        f"{scenario.case_id} old classifier artifact",
        scenario.expected_old_artifact, old_attr.artifact,
    )
    _check_eq(
        f"{scenario.case_id} old classifier reason_code",
        scenario.expected_old_reason, old_attr.reason_code,
    )

    # New causal pipeline.
    new_signal = project_detection(
        row, use_gold=scenario.use_gold, candidate_kind_hint="",
    )
    new_cut = build_causal_cut(
        new_signal, boundary_path_state=scenario.boundary_path_state,
    )
    new_layer_ok = _check_eq(
        f"{scenario.case_id} new cut layer",
        scenario.expected_new_layer, new_cut.skill_layer,
    )
    new_owner_ok = _check_eq(
        f"{scenario.case_id} new cut owner",
        scenario.expected_new_owner, new_cut.owner,
    )
    _check_eq(
        f"{scenario.case_id} new cut reason_code",
        scenario.expected_new_reason, new_cut.reason_code,
    )

    episode = build_causal_episode(
        signal=new_signal,
        cut=new_cut,
        record_key=scenario.case_id,
        evolution_direction=scenario.evolution_direction,
        allowed_modules=scenario.allowed_modules,
    )
    valid, reason = validate_causal_episode(episode)
    _check_true(
        f"{scenario.case_id} episode validates", valid, reason,
    )

    verdict = analyze(
        episode,
        allowed_modules=scenario.allowed_modules,
        evolution_direction=scenario.evolution_direction,
    )
    _check_eq(
        f"{scenario.case_id} repairability status",
        scenario.expected_repairability.value, verdict.status.value,
    )

    # Unaddressable scenarios must NOT produce a no-edit-ish verdict: the
    # verdict's status string must explicitly be one of the four closed
    # vocabulary tokens (it already is, by construction); additionally
    # assert that the recorded reason is non-empty whenever the status is
    # a framework-defect verdict (no silent skip).
    if verdict.status in (
        RepairabilityStatus.UNADDRESSABLE_FRAMEWORK_DEFECT,
        RepairabilityStatus.FRAMEWORK_DEFECT,
    ):
        _check_true(
            f"{scenario.case_id} framework_defect has non-empty reason",
            bool(verdict.reason),
            "framework-defect verdict must record a closed-vocab reason",
        )

    # Deviation annotation: when the new (layer/owner/reason) differs from
    # the old (layer/artifact/reason) on something other than the
    # documented C3/C4 deviation, surface it.
    old_tuple = (old_attr.layer, old_attr.artifact, old_attr.reason_code)
    new_tuple = (new_cut.skill_layer, new_cut.owner, new_cut.reason_code)
    if old_tuple != new_tuple:
        if scenario.deviation_note:
            _dev(
                f"{scenario.case_id} C3/C4 deviation",
                scenario.deviation_note,
            )
        else:
            _fail(
                f"{scenario.case_id} undocumented deviation",
                f"old={old_tuple!r} new={new_tuple!r}",
            )
    elif scenario.deviation_note:
        # Deviation note recorded but no real deviation occurred; that is
        # fine -- the note was conservative documentation.
        _ok(f"{scenario.case_id} no deviation (note was conservative)")

    _ = (new_layer_ok, new_owner_ok)  # checked above; silence unused warnings


# Sanity: the empty-record assertion used by step 4c requires the old
# ``failure_classifier.classify_failure`` to be called with a real
# provider_artifacts list. When the row is runtime-invalid, the
# ``old_signal`` carries diagnostic_only; ``project_provider_artifacts``
# will not emit artifacts for it. The record path is therefore the
# honest offline-contract comparison.
def check_scenario_table() -> None:
    print("scenario table (failure shapes -> repairability)")
    for scenario in _all_scenarios():
        _run_scenario(scenario)


# ============================================================
# RepairPlan compilation sanity
# ============================================================

def check_repair_plan_compilation() -> None:
    """Each REPAIRABLE scenario must compile at least one RepairPlan."""
    print("RepairPlan compilation sanity")
    from sehd_rise.causal.plan import build_repair_plan

    for scenario in _all_scenarios():
        if scenario.expected_repairability is not RepairabilityStatus.REPAIRABLE:
            continue
        row = dict(scenario.row)
        if scenario.use_gold and scenario.gold_label is not None:
            row["gold_label"] = scenario.gold_label
        new_signal = project_detection(row, use_gold=scenario.use_gold)
        new_cut = build_causal_cut(
            new_signal, boundary_path_state=scenario.boundary_path_state,
        )
        episode = build_causal_episode(
            signal=new_signal,
            cut=new_cut,
            record_key=scenario.case_id,
            evolution_direction=scenario.evolution_direction,
            allowed_modules=scenario.allowed_modules,
        )
        plans = build_repair_plan(
            episode,
            allowed_modules=scenario.allowed_modules,
            evolution_direction=scenario.evolution_direction,
        )
        _check_true(
            f"{scenario.case_id} compiles >=1 RepairPlan",
            len(plans) >= 1,
            f"frontier_size={len(episode.causal_failure_edge_frontier)} "
            f"allowed_modules={list(scenario.allowed_modules)}",
        )


# ============================================================
# Demonstrate C2: an evolvable surface declared but no legal plan
# compiles -> UNADDRESSABLE_FRAMEWORK_DEFECT (not no-edit).
# ============================================================

def check_c2_unaddressable_emission() -> None:
    """Plan §2.2: declared L1/L2 surface without a legal RepairPlan must
    surface ``UNADDRESSABLE_FRAMEWORK_DEFECT`` rather than a no-edit verdict.
    Constructed via a missing-candidate case where ``allowed_modules`` is
    narrowed to an L2-only surface the cut cannot compile a plan for.
    """
    print("C2 unaddressable-framework-defect emission")
    err_cert = _valid_error_cert("err_1", "cand_1", "event_001", "event_003")
    row = _base_row(
        state="unresolved",
        census=[],
        prosecution_coverage="incomplete",
        defense_coverage="incomplete",
        prosecution_candidates=[],
        defense_candidates=[],
        evidence_health_extras={
            "required_candidate_count": 1,
            "construct_accepted_candidate_count": 0,
            "construct_raw_candidate_count": 0,
        },
    )
    new_signal = project_detection(row)
    new_cut = build_causal_cut(new_signal)
    # allowed_modules excludes the L1 owner the cut picked, so no RepairPlan
    # compiles -> UNADDRESSABLE_FRAMEWORK_DEFECT (per §2.2).
    episode = build_causal_episode(
        signal=new_signal,
        cut=new_cut,
        record_key="c2_demonstration",
        allowed_modules=("clean_defense_boundary", "abstention_rule"),
    )
    verdict = analyze(
        episode,
        allowed_modules=("clean_defense_boundary", "abstention_rule"),
    )
    _check_eq(
        "c2 unaddressable status",
        RepairabilityStatus.UNADDRESSABLE_FRAMEWORK_DEFECT.value,
        verdict.status.value,
    )
    _check_true(
        "c2 unaddressable reason non-empty",
        bool(verdict.reason),
    )
    _check_true(
        "c2 verdict is NOT a no-edit token",
        verdict.status.value != "no_edit",
        "no-edit path must never absorb an UNADDRESSABLE emission (plan §2.2)",
    )


# ============================================================
# Demonstrate C4: detector-boundary origin always FRAMEWORK_DEFECT
# ============================================================

def check_c4_framework_defect_always() -> None:
    """Plan §2.5: validator/transport/parse/route/config defects always
    return ``FRAMEWORK_DEFECT``; never ``REPAIRABLE``.
    """
    print("C4 framework-defect boundary verdicts")
    bad_validator_audit = {
        "certificate_id": "err_1",
        "candidate_id": "cand_1",
        "decision": "quarantine",
        "failed_checks": ["breach_type"],
        "failed_fields": ["breach_type"],
        "failed_invariants": [],
        "missing_fields": [],
    }
    row = _base_row(
        state="verified_clean",
        prosecution_coverage="incomplete",
        defense_coverage="complete",
        defense_candidates=[_valid_defense_cert("def_2", "cand_1", "event_001")],
        proof_adjudication_extras={
            "valid_error_certificate_count": 0,
            "surviving_error_certificate_ids": [],
            "quarantined_error_certificate_ids": ["err_1"],
            "error_audits": [bad_validator_audit],
        },
        evidence_health_extras={"quarantined_error_certificate_count": 1},
    )
    new_signal = project_detection(row)
    new_cut = build_causal_cut(new_signal)
    episode = build_causal_episode(
        signal=new_signal,
        cut=new_cut,
        record_key="c4_demonstration",
        allowed_modules=ALL_MODULES,
    )
    verdict = analyze(episode, allowed_modules=ALL_MODULES)
    _check_eq(
        "c4 validator_contract_fault status",
        RepairabilityStatus.FRAMEWORK_DEFECT.value,
        verdict.status.value,
    )
    _check_true(
        "c4 verdict is never REPAIRABLE",
        verdict.status is not RepairabilityStatus.REPAIRABLE,
    )


# ============================================================
# Main
# ============================================================

def main() -> int:
    print("== compare_causal ==")
    print()
    print("DEGRADED (plan §3.3 R2): PACA_GLOBAL_CACHE_ROOT warm cache")
    print("unavailable; provider-level byte-identity replay NOT executed in")
    print("this run. Offline deterministic contract checks below were")
    print("executed instead.")
    print()
    check_scenario_table()
    print()
    check_repair_plan_compilation()
    print()
    check_c2_unaddressable_emission()
    print()
    check_c4_framework_defect_always()
    print()
    print(f"{len(CHECKS)} checks passed, {len(FAILURES)} failed, {len(DEV)} deviations documented")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())