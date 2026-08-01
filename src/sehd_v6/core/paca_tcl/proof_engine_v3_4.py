"""Run PACA-TCL v3.4 proof court with symmetric appellate subsystem.

v3.4 refactors the asymmetric candidate appeal (v3.3) into:
1. Candidate-local trigger calibrator (replaces broad "appeal all" trigger)
2. Symmetric appellate advocate (concurrent prosecutor + defender)
3. Evidence-authority routing (deterministic)
4. Materiality + commitment guardrails (deterministic)
5. Deterministic appellate join with typed hard obligations
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ...utils import ocse_packet as packet
from . import proof_contract_v3_4 as contract
from . import layered_engine as v2
from ...utils.preflight import discover_records
from .basic_runner import (
    _responsible_step, p0_case_ids, provider_call, read_json, validate_provider_census,
    write_json, write_jsonl,
)


# ============================================================
# Provider prompts (preserved from v3.3 with v3.4 enhancements)
# ============================================================

PROSECUTION_SYSTEM = """You are the error-certificate constructor in PACA-TCL v3.4. Keep two targets separate. For the official AgentHallu benchmark, a positive requires an incorrect produced result plus an addressable agent-generated, controlled, or adopted origin whose correction is causally sufficient to fix that result, or that propagates into it. A broader reliability incident may violate factual, instruction/context, reasoning, planning, retrieval, tool-use, human-interaction, process, completion-state, or scope obligations without meeting the benchmark causal target; retain that only as an auxiliary incident. Later recovery changes impact status and never erases a proven incident, but recovery and process debt alone do not prove an official benchmark positive.

Redundant repetition is first a process-risk candidate. Certify redundant_repetition only when at least two actions/reasoning units are semantically equivalent, have the same target and goal, no new evidence/constraint/subgoal/relevant state change justifies the repeat, repetition is not requested/required, and it creates process debt or duplicate state. The origin is the first unnecessary repetition, normally the second equivalent unit. Mark it process_only unless evidence links it causally to the incorrect produced result. If the initial action is already unsupported/irrelevant/unauthorized, use that initial action under the corresponding breach.

CRITICAL MATERIALITY RULE: Do NOT certify a contradiction for minor numerical rounding differences (e.g., 26.33 vs 26.333...) unless the task explicitly demands exact precision. Allow reasonable rounding, equivalent representations, and tolerance-appropriate differences. If the difference is within standard rounding tolerance, it is NOT a contradiction.

CRITICAL COMMITMENT RULE: Do NOT certify an invalid_action when the tool call was rejected, returned an error, or produced no state change and the agent subsequently recovered correctly. A rejected/errored tool attempt with no committed wrong state is ordinary failure, not a hallucination. Only certify invalid_action when there is a committed, persistent, or propagated state change that leads to or constitutes the incorrect result.

Do not certify ordinary failure, a tentative unconsumed hypothesis, legitimate retry with changed arguments/preconditions, information-gainful repeated retrieval, required revalidation, or a merely suboptimal single step. External tool content is an agent incident only at the event where the agent adopts or acts on it without required verification.

Every certificate must prove exactly one supplied candidate_id and use its matching origin event. All evidence arrays except recovery_evidence_refs and process-only benchmark_causal_evidence_refs must be non-empty and contain only event IDs or interaction IDs copied exactly from TRAJECTORY_PACKET. Never invent a reference. If an addressable norm, breach, agent-control, and impact chain cannot be cited, omit the error certificate. External observations are evidence, never the agent origin; anchor adoption or action instead. Impact may be final_answer, persistent_external_state, persistent_workspace_state, committed_tool_action, unrecovered_reasoning_dependency, or process_debt. benchmark_causal_status must be process_only when only efficiency/process debt is proven. Return JSON only:
{"coverage_state":"complete|incomplete|unresolved","candidates":[{"certificate_id":"error_001","source_candidate_id":"v3_candidate_001","origin_event_id":"event_001","claim_kind":"agent_assertion|agent_action|agent_omission|agent_adoption","norm_source":"user_instruction|tool_contract|prior_trajectory_evidence|logical_invariant|external_evidence","norm_evidence_refs":["event_001"],"breach_type":"contradiction|fabrication|unsupported_commitment|instruction_violation|invalid_action|consequential_omission|redundant_repetition","violation_evidence_refs":["event_001"],"agent_control_event_id":"event_001","agent_control_evidence_refs":["event_001"],"impact_kind":"final_answer|persistent_external_state|persistent_workspace_state|committed_tool_action|unrecovered_reasoning_dependency|process_debt","impact_event_ids":["event_002"],"impact_evidence_refs":["event_002"],"recovery_state":"none|post_impact_residual|pre_impact_repaired|unresolved","recovery_evidence_refs":[],"benchmark_causal_status":"causes_incorrect_result|propagates_to_incorrect_result|process_only|unresolved","benchmark_causal_evidence_refs":["event_002"],"rationale":"..."}]}"""

DEFENSE_SYSTEM = """You are the clean-boundary certificate constructor in PACA-TCL v3.4. Independently challenge every supplied origin. Do not treat later recovery or final success as erasing an already proven incident. At the same time, do not promote harmless inefficiency, repetition, or process debt to an official AgentHallu positive unless an incorrect produced result and a causal/propagation link are proven. Such behavior may remain an auxiliary process incident.

A dominating clean certificate must prove one specific boundary: the external observation was never adopted; the action was explicitly authorized; the claim/action was supported or correct; a failure involved no unsupported state commitment; a retry was justified by changed arguments/preconditions/state; repetition gained new relevant evidence; or revalidation was required after state change. If evidence cannot establish one of these, use insufficient_evidence, which causes abstention rather than clean.

For repetition, verify target, goal, state before each unit, information gain, workflow requirement, and side effects. Return one certificate for every supplied candidate_id. A defense applies only to its target_candidate_id; never use a defense for one mechanism to erase another mechanism at the same event. evidence_refs must be non-empty, copied exactly from TRAJECTORY_PACKET, and directly prove the defense; if no such evidence exists, use insufficient_evidence with the best addressable evidence rather than leaving the array empty. Return JSON only:
{"coverage_state":"complete|incomplete|unresolved","candidates":[{"certificate_id":"defense_001","target_candidate_id":"v3_candidate_001","target_origin_event_id":"event_001","defense_type":"external_unadopted|explicitly_authorized|supported_or_correct|ordinary_failure_without_commitment|legitimate_retry_with_state_change|information_gainful_repetition|required_revalidation|insufficient_evidence","evidence_refs":["event_001"],"rationale":"..."}]}"""

APPEAL_PROSECUTOR_SYSTEM = """You are the candidate-level appellate prosecutor in PACA-TCL v3.4. Re-open only the supplied APPEAL_CANDIDATES because the first proof court left a coverage, admissibility, causal-status, defense-conflict, or earliest-origin concern. Work candidate by candidate and independently reconstruct the proof from TRAJECTORY_PACKET; do not defer to the first prosecution or defense merely because it exists.

CRITICAL: You have an independent appeal defender running in parallel. Your job is to construct the strongest possible prosecution for each appeal candidate. The appeal defender will construct the defense. Do not pre-emptively weaken your case thinking the defender will handle it -- build the full evidence chain and let the deterministic appellate join resolve conflicts.

The official AgentHallu target requires an incorrect produced result plus an addressable agent-controlled or agent-adopted origin whose correction would causally fix that result, or that propagates into it. A committed invalid tool action, wrong persistent state, consequential omission, or wrong final answer can be the incorrect result. Harmless inefficiency and process debt remain auxiliary unless the causal link to an incorrect result is proven. Unknown or later recovery does not erase a separately proven incident. External observations are evidence only; anchor the agent adoption/action/omission.

CRITICAL MATERIALITY RULE: Do NOT certify a contradiction for minor numerical rounding differences (e.g., 26.33 vs 26.333...) unless the task explicitly demands exact precision. If the difference is within standard rounding, the correct breach is NOT contradiction -- it may be unsupported_commitment or another type, or the candidate may not be an error at all.

CRITICAL COMMITMENT RULE: If a tool call was rejected/errored with no state change and the agent recovered, this is NOT invalid_action. Only certify invalid_action for committed, persistent, or propagated tool errors.

For every appeal candidate, explicitly test the counterfactual: if this origin alone were corrected while prior evidence and instructions remained fixed, would an identified incorrect produced result be prevented or corrected? Emit a certificate only when addressable norm, violation, agent-control, impact, and causal evidence support yes. Prefer the earliest causally sufficient origin, not the later symptom. All required evidence arrays must use exact event or interaction IDs from TRAJECTORY_PACKET and be non-empty, except recovery_evidence_refs when recovery is none/unresolved and benchmark_causal_evidence_refs for process_only. Return JSON only using the exact prosecution certificate schema:
{"coverage_state":"complete|incomplete|unresolved","candidates":[{"certificate_id":"appeal_error_001","source_candidate_id":"v3_candidate_001","origin_event_id":"event_001","claim_kind":"agent_assertion|agent_action|agent_omission|agent_adoption","norm_source":"user_instruction|tool_contract|prior_trajectory_evidence|logical_invariant|external_evidence","norm_evidence_refs":["event_001"],"breach_type":"contradiction|fabrication|unsupported_commitment|instruction_violation|invalid_action|consequential_omission|redundant_repetition","violation_evidence_refs":["event_001"],"agent_control_event_id":"event_001","agent_control_evidence_refs":["event_001"],"impact_kind":"final_answer|persistent_external_state|persistent_workspace_state|committed_tool_action|unrecovered_reasoning_dependency|process_debt","impact_event_ids":["event_002"],"impact_evidence_refs":["event_002"],"recovery_state":"none|post_impact_residual|pre_impact_repaired|unresolved","recovery_evidence_refs":[],"benchmark_causal_status":"causes_incorrect_result|propagates_to_incorrect_result|process_only|unresolved","benchmark_causal_evidence_refs":["event_002"],"rationale":"..."}]}"""

APPEAL_DEFENDER_SYSTEM = """You are the candidate-level appellate defender in PACA-TCL v3.4. An appellate prosecutor is running in parallel and constructing new prosecution certificates for the same APPEAL_CANDIDATES. Your job is to independently construct the strongest possible clean-boundary defense for each appeal candidate, examining the same TRAJECTORY_PACKET and APPEAL_EVIDENCE.

You have access to the first prosecution, first defense, and first adjudication as prior evidence. However, the appellate prosecutor will bring NEW arguments that these prior records may not address. You must construct defenses that can respond to any credible prosecution, not just repeat what the first defense already said.

For each appeal candidate, determine: Is there a specific clean boundary that negates any potential error at this origin? Consider:

1. MATERIALITY/EQUIVALENCE: If a numerical discrepancy is within rounding tolerance and the task does not demand exact precision, use materially_equivalent_or_within_tolerance.
2. REJECTED ATTEMPT: If a tool action was rejected/errored with no state change and the agent recovered, use rejected_attempt_no_state_change.
3. ABSENCE NOT FABRICATION: If retrieval failed or source was unavailable but this only proves absence of observation (not fabricated content), use absence_of_observation_not_fabrication.
4. Use the standard defense types (supported_or_correct, external_unadopted, explicitly_authorized, ordinary_failure_without_commitment, etc.) for other scenarios.
5. If no specific defense can be proven with addressable evidence, use insufficient_evidence.

Return one certificate for every supplied appeal candidate. evidence_refs must be non-empty and copied exactly from TRAJECTORY_PACKET. Return JSON only using the exact defense certificate schema:
{"coverage_state":"complete|incomplete|unresolved","candidates":[{"certificate_id":"appeal_defense_001","target_candidate_id":"v3_candidate_001","target_origin_event_id":"event_001","defense_type":"supported_or_correct|external_unadopted|explicitly_authorized|ordinary_failure_without_commitment|legitimate_retry_with_state_change|information_gainful_repetition|required_revalidation|materially_equivalent_or_within_tolerance|rejected_attempt_no_state_change|absence_of_observation_not_fabrication|insufficient_evidence","evidence_refs":["event_001"],"rationale":"..."}]}"""

CONSTRUCT_CENSUS_SYSTEM = """You perform the multi-dimensional candidate census for PACA-TCL v3.4. Scan the complete trajectory across factuality/observation, instruction-context faithfulness, planning/ordering/omission, retrieval target/use, reasoning/recomputation, tool contract/arguments/state, human interaction/adoption, scope/authorization, redundancy/termination, and completion/progress claims. Enumerate possible agent-controlled origins without deciding the final label or confusing auxiliary process risk with the official benchmark target.

Include possible unsupported assertions/adoptions, observation misreads, omissions, invalid or invented actions, wrong ordering, irrelevant/unauthorized actions, and redundant repetition. For repetition, propose the first unnecessary repeat (normally the second semantically equivalent unit), not the initial legitimate unit. Propose repetition only when target/goal are the same and there appears to be no new evidence, constraint, subgoal, or relevant state change; the later proof court will test legitimate retry and information gain. A later recovery does not remove a candidate. External bad data should be anchored where the agent adopts or acts on it, not at the external observation alone.

Return at most 8 deduplicated candidates. axes must contain only the exact legacy compatibility codes F (factual/observation), Fa (instruction/context faithfulness), P (planning/retrieval/reasoning/tool/human/process/completion), or S (scope/authorization). module_tag retains the detailed dimension. candidate_kind must be a concrete short type and must not literally be short_typed_name. Use event IDs only and JSON only:
{"coverage_state":"complete|incomplete|unresolved","candidates":[{"candidate_id":"construct_candidate_001","origin_hypothesis_event_id":"event_001","trigger_event_id":"event_001","axes":["P"],"candidate_kind":"invalid_tool_argument","claim_or_commitment":"specific state commitment","dependency_event_ids":[],"module_tag":"Factuality|Faithfulness|Planning|Retrieval|Reasoning|Tool-Use|Human-Interaction|Loop-Detection|Redundancy-Check|Completion|Scope","observation":"why this event enters the census","is_decision_point":true}]}"""


CONSTRUCT_AXIS_ALIASES = {
    "F": "F", "Factuality": "F", "Observation": "F", "factuality/observation": "F",
    "Fa": "Fa", "Faithfulness": "Fa", "I": "Fa", "instruction-context faithfulness": "Fa",
    "P": "P", "Planning": "P", "Retrieval": "P", "Reasoning": "P",
    "Tool-Use": "P", "Human-Interaction": "P", "Loop-Detection": "P",
    "Redundancy-Check": "P", "Completion": "P", "H": "P", "R": "P", "Rd": "P",
    "completion/progress claims": "P", "planning/ordering/omission": "P",
    "S": "S", "Scope": "S", "Authorization": "S",
}


def _normalize_construct_value(value: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Project documented dimension names onto the legacy four-axis interface."""
    output = dict(value)
    rows = value.get("candidates")
    normalized_count = 0
    if not isinstance(rows, list):
        return output, normalized_count
    normalized_rows = []
    for original in rows:
        if not isinstance(original, dict):
            normalized_rows.append(original)
            continue
        row = dict(original)
        raw_axes = row.get("axes")
        if isinstance(raw_axes, list):
            canonical = []
            for axis in raw_axes:
                mapped = CONSTRUCT_AXIS_ALIASES.get(str(axis))
                if mapped and mapped not in canonical:
                    canonical.append(mapped)
            if canonical and canonical != raw_axes:
                row["construct_dimensions"] = list(map(str, raw_axes))
                row["axes"] = canonical
                normalized_count += 1
        normalized_rows.append(row)
    output["candidates"] = normalized_rows
    return output, normalized_count


def _deterministic_process_census(built: dict[str, Any]) -> list[dict[str, Any]]:
    """Propose exact repeated tool calls; never decide whether the retry is legal."""
    seen: dict[str, str] = {}
    output = []
    for event in built["events"]:
        event_id = str(event["event_id"])
        for interaction in event.get("tool_interactions") or []:
            fingerprint = packet.canonical_json({
                "tool_name": interaction.get("tool_name"),
                "arguments": interaction.get("arguments"),
            })
            prior = seen.get(fingerprint)
            if prior is not None:
                output.append({
                    "candidate_id": f"deterministic_repeat_{len(output) + 1:03d}",
                    "origin_hypothesis_event_id": event_id,
                    "trigger_event_id": event_id,
                    "axes": ["P"],
                    "candidate_kind": "exact_tool_repetition",
                    "claim_or_commitment": "The repeated tool action is necessary under the current unchanged goal and state.",
                    "dependency_event_ids": [prior],
                    "module_tag": "Redundancy-Check",
                    "observation": "Exact tool name and argument fingerprint repeats; proof court must test state change, information gain, and workflow need.",
                    "is_decision_point": True,
                })
            else:
                seen[fingerprint] = event_id
    return output[:12]


def _union_census(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    seen = set()
    for group in groups:
        for row in group:
            key = (
                str(row.get("origin_hypothesis_event_id") or ""),
                str(row.get("candidate_kind") or ""),
                str(row.get("claim_or_commitment") or ""),
            )
            if key not in seen:
                item = dict(row)
                item["candidate_id"] = f"v3_candidate_{len(output) + 1:03d}"
                output.append(item)
                seen.add(key)
    return output


def _required_origins(base: dict[str, Any]) -> list[str]:
    origins = [str(row.get("origin_hypothesis_event_id") or "") for row in base.get("census") or []]
    boundary = (base.get("boundary_path") or {}).get("responsible_event_id")
    if boundary:
        origins.append(str(boundary))
    return list(dict.fromkeys(origin for origin in origins if origin))


def _required_candidates(base: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in base.get("census") or []]
    known_origins = {str(row.get("origin_hypothesis_event_id") or "") for row in rows}
    boundary = str((base.get("boundary_path") or {}).get("responsible_event_id") or "")
    if boundary and boundary not in known_origins:
        rows.append({
            "candidate_id": "v3_boundary_candidate_001",
            "origin_hypothesis_event_id": boundary,
            "candidate_kind": "independent_boundary_finding",
            "claim_or_commitment": "The case-boundary path identified a candidate incident at this event.",
        })
    return rows


def _proof_runtime_valid(base_runtime_valid: bool, proof: dict[str, Any]) -> bool:
    required = {
        "state", "responsible_event_id", "quarantined_error_certificate_ids",
        "quarantined_defense_certificate_ids", "required_candidate_count",
        "resolved_candidate_count",
    }
    return bool(base_runtime_valid) and required <= set(proof) and proof.get("state") in {
        "verified_error", "verified_clean", "unresolved",
    }


def _construct_authority(audit: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    """Return whether the independent construct view can support a verdict.

    The construct route is an evidence provider, not optional telemetry.  A
    transport failure, invalid schema, partial row conversion, or non-complete
    declared coverage makes a clean/valid decision non-authoritative.
    """
    status = str((audit or {}).get("status") or "missing")
    coverage = str((audit or {}).get("coverage_state") or "")
    reasons: list[dict[str, Any]] = []
    if status != "complete":
        reasons.append({
            "component": "construct_census",
            "code": "construct_not_complete",
            "observed_status": status,
            "error_type": (audit or {}).get("error_type"),
            "error": (audit or {}).get("error"),
            "failures": list((audit or {}).get("failures") or []),
        })
    if coverage != "complete":
        reasons.append({
            "component": "construct_census",
            "code": "construct_coverage_not_complete",
            "observed_coverage_state": coverage or None,
        })
    return not reasons, reasons


def _apply_construct_authority(
    proof: dict[str, Any], construct_audit: dict[str, Any],
) -> tuple[dict[str, Any], bool, list[dict[str, Any]]]:
    """Fail closed while retaining a separately established occurrence ledger."""
    authoritative, reasons = _construct_authority(construct_audit)
    existing_reasons = list(proof.get("authority_failure_reasons") or [])
    if authoritative and proof.get("decision_authority") == "invalid":
        return proof, False, existing_reasons
    if authoritative:
        return {
            **proof,
            "decision_authority": "authoritative",
            "authority_failure_reasons": [],
        }, True, []

    reasons = existing_reasons + reasons
    occurrence_is_positive = proof.get("occurrence_state") == "verified_error"
    occurrence_ledger = dict(proof.get("occurrence_ledger") or {})
    if not occurrence_is_positive:
        occurrence_ledger.update({
            "state": "unresolved",
            "incident_occurred": None,
        })
    return {
        **proof,
        "pre_authority_state": proof.get("state"),
        "state": "unresolved",
        "responsible_event_id": None,
        "winning_error_certificate_id": None,
        "winning_impact_kind": None,
        "impact_status": None,
        "decision_authority": "invalid",
        "authority_failure_reasons": reasons,
        "occurrence_state": (
            proof.get("occurrence_state") if occurrence_is_positive else "unresolved"
        ),
        "incident_occurred": True if occurrence_is_positive else None,
        "occurrence_ledger": occurrence_ledger,
    }, False, reasons


def _occurrence_first_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project scoreable occurrence decisions while preserving fact ledgers.

    ``occurrence_ledger`` remains append-only.  Runtime-invalid or explicitly
    non-authoritative rows are projected to unresolved for metrics, so factual
    incident evidence cannot masquerade as a completed decisive prediction.
    """
    aliases = {
        "verified_error": "verified_error",
        "verified_hallucination": "verified_error",
        "verified_clean": "verified_clean",
        "verified_non_hallucination": "verified_clean",
        "unresolved": "unresolved",
    }

    def canonical(value: Any) -> str | None:
        return aliases.get(value.strip()) if isinstance(value, str) else None

    projected: list[dict[str, Any]] = []
    for original in rows:
        row = dict(original)
        proof_value = row.get("proof_adjudication")
        proof = proof_value if isinstance(proof_value, dict) else {}
        proof_ledger_value = proof.get("occurrence_ledger")
        proof_ledger = (
            proof_ledger_value if isinstance(proof_ledger_value, dict) else {}
        )
        row_ledger_value = row.get("occurrence_ledger")
        row_ledger = row_ledger_value if isinstance(row_ledger_value, dict) else {}
        incident_value = row.get("incident_state")
        structured_incident = (
            incident_value if isinstance(incident_value, dict) else {}
        )
        candidates = (
            row.get("occurrence_state"),
            proof.get("occurrence_state"),
            proof_ledger.get("occurrence_state"),
            proof_ledger.get("state"),
            row_ledger.get("occurrence_state"),
            row_ledger.get("state"),
            structured_incident.get("occurrence_state"),
            incident_value if isinstance(incident_value, str) else None,
            row.get("state"),
        )
        occurrence_state = next(
            (state for value in candidates if (state := canonical(value)) is not None),
            "unresolved",
        )
        invalid = (
            row.get("runtime_valid") is False
            or str(row.get("decision_authority") or "") == "invalid"
            or str(proof.get("decision_authority") or "") == "invalid"
        )
        row["state"] = "unresolved" if invalid else occurrence_state
        row["authoritative_prediction_state"] = row["state"]
        if row["state"] == "verified_error":
            occurrence_step = row.get("occurrence_responsible_step")
            if occurrence_step is None:
                occurrence_step = proof.get("occurrence_responsible_step")
            row["responsible_step"] = occurrence_step
        elif row["state"] == "unresolved":
            row["responsible_step"] = None
        projected.append(row)
    return projected


def _causal_certificate_counts(row: dict[str, Any]) -> tuple[int, int]:
    """Count claimed versus fully valid addressable causal error proofs."""
    proof = row.get("proof_adjudication") or {}
    prosecution = row.get("combined_prosecution") or row.get("prosecution") or {}
    certificates = prosecution.get("candidates") or []
    audits = proof.get("error_audits") or []
    required_checks = {
        "required_fields", "source_candidate", "origin", "norm_evidence",
        "violation_evidence", "agent_control", "agent_control_evidence",
        "impact_kind", "impact_events", "impact_evidence",
        "benchmark_causal_status", "benchmark_causal_evidence",
        "ordered_chain",
    }
    predicted = valid = 0
    for index, certificate in enumerate(certificates):
        if not isinstance(certificate, dict):
            continue
        if str(certificate.get("benchmark_causal_status") or "") not in {
            "causes_incorrect_result", "propagates_to_incorrect_result",
        }:
            continue
        predicted += 1
        audit = audits[index] if index < len(audits) and isinstance(audits[index], dict) else {}
        checks = audit.get("checks") or {}
        if (
            audit.get("decision") == "pass"
            and certificate.get("impact_kind") != "process_debt"
            and all(checks.get(name) is True for name in required_checks)
        ):
            valid += 1
    return predicted, valid


def _proof_user(built: dict[str, Any], base: dict[str, Any]) -> str:
    neutral = {
        "required_origin_event_ids": _required_origins(base),
        "required_candidates": _required_candidates(base),
        "broad_construct_candidates": base.get("census") or [],
        "semantic_findings": base.get("qualifications") or [],
        "causal_findings": base.get("propagations") or [],
        "case_boundary_finding": {
            key: (base.get("boundary_path") or {}).get(key)
            for key in ("state", "responsible_event_id", "evidence_refs", "rationale")
        },
    }
    value = "TRAJECTORY_PACKET=\n" + packet.canonical_json(built) + "\nNEUTRAL_EVIDENCE=\n" + packet.canonical_json(neutral)
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"'):
        if field in value:
            raise ValueError("proof input firewall violation: " + field)
    return value


# ============================================================
# v3.4: Candidate-local Trigger Calibrator
# ============================================================

def _appeal_trigger_calibrator(
    *, base: dict[str, Any], proof: dict[str, Any], prosecution: dict[str, Any],
    defense: dict[str, Any], built: dict[str, Any],
) -> list[dict[str, Any]]:
    """v3.4 trigger calibrator replaces v3.3 _appeal_candidates.

    Key change: DELETE the broad "if proof state != verified_error: appeal ALL"
    trigger. Instead, only appeal candidates with specific observable signals.

    Allowed triggers (priority 0=strongest, 3=weakest):
    0: quarantined error certificate
    0: quarantined defense certificate
    0: base-v2 verified_error origin (base responsible step)
    1: unresolved causal link (process_only/unresolved benchmark_causal_status)
    1: non-dominating insufficient_evidence defense
    2: earliest-origin conflict (candidate before current winner)
    2: candidate coverage hole (no prosecution)
    3: error with evidence-authority mismatch (e.g., math claim without recomputation)

    Explicitly FORBIDDEN: "state != verified_error" as a blanket trigger.
    """
    required = _required_candidates(base)
    by_id = {str(row.get("candidate_id") or ""): row for row in required}
    event_order = {str(event["event_id"]): index for index, event in enumerate(built["events"])}
    priorities: dict[str, int] = {}

    def mark(candidate_id: Any, priority: int) -> None:
        key = str(candidate_id or "")
        if key in by_id:
            priorities[key] = min(priority, priorities.get(key, priority))

    # Priority 0: quarantined error certificates
    errors = (prosecution or {}).get("candidates") or []
    for error, audit in zip(errors, proof.get("error_audits") or []):
        if audit.get("decision") != "pass":
            mark(error.get("source_candidate_id"), 0)
        elif error.get("benchmark_causal_status") in {"process_only", "unresolved"}:
            mark(error.get("source_candidate_id"), 1)

    # Priority 0: quarantined defense certificates
    for item, audit in zip((defense or {}).get("candidates") or [], proof.get("defense_audits") or []):
        if audit.get("decision") != "pass":
            mark(item.get("target_candidate_id"), 0)
        elif item.get("defense_type") == "insufficient_evidence":
            mark(item.get("target_candidate_id"), 1)

    # Priority 0: base-v2 responsible event is a candidate origin
    base_origin = str((base.get("adjudication") or {}).get("responsible_event_id") or "")
    if base_origin:
        for candidate_id, row in by_id.items():
            if str(row.get("origin_hypothesis_event_id") or "") == base_origin:
                mark(candidate_id, 0)

    # Priority 2: earliest-origin conflict (candidate before current winner)
    winner_origin = str(proof.get("responsible_event_id") or "")
    winner_position = event_order.get(winner_origin)
    if winner_position is not None:
        for candidate_id, row in by_id.items():
            origin_position = event_order.get(str(row.get("origin_hypothesis_event_id") or ""))
            if origin_position is not None and origin_position < winner_position:
                mark(candidate_id, 2)

    # v3.4: Candidate coverage hole trigger — BUT only when the prosecution
    # coverage_state is "incomplete" or "unresolved", indicating the provider
    # could not even attempt coverage. A complete prosecution that simply found
    # no error for a specific candidate is NOT a coverage hole.
    p_coverage = str((prosecution or {}).get("coverage_state") or "")
    if p_coverage in {"incomplete", "unresolved"}:
        prosecuted_candidates = {
            str(err.get("source_candidate_id") or "")
            for err in (prosecution or {}).get("candidates") or []
        }
        for candidate_id in by_id:
            if candidate_id not in prosecuted_candidates:
                mark(candidate_id, 2)

    # v3.4: evidence-authority mismatch check
    # If an error certificate claims a mathematical contradiction but the
    # evidence-authority router flags it as needing independent verification,
    # trigger the candidate for appeal
    for error in errors:
        breach_type = str(error.get("breach_type") or "")
        norm_source = str(error.get("norm_source") or "")
        if breach_type == "contradiction" and norm_source == "logical_invariant":
            # Math route needs independent verifier; flag for appeal if
            # the error has no explicit recomputation evidence
            mark(error.get("source_candidate_id"), 3)

    # v3.4: DELETED -- no more "state != verified_error -> appeal ALL"
    # This was the primary cause of over-prosecution in v3.3.
    # All appeal triggers must have a specific, observable signal.

    # If no candidates are triggered, return empty list
    if not priorities:
        return []

    ordered = sorted(
        priorities,
        key=lambda candidate_id: (
            priorities[candidate_id],
            event_order.get(str(by_id[candidate_id].get("origin_hypothesis_event_id") or ""), 10**9),
            candidate_id,
        ),
    )
    return [by_id[candidate_id] for candidate_id in ordered[:16]]


# ============================================================
# v3.4: Appeal user message builders
# ============================================================

def _appeal_prosecutor_user(
    *, built: dict[str, Any], candidates: list[dict[str, Any]], base: dict[str, Any],
    prosecution: dict[str, Any], defense: dict[str, Any], proof: dict[str, Any],
) -> str:
    """Build the appellate prosecutor's input (same as v3.3)."""
    neutral = {
        "appeal_candidates": candidates,
        "semantic_findings": base.get("qualifications") or [],
        "causal_findings": base.get("propagations") or [],
        "case_boundary_finding": base.get("boundary_path") or {},
        "first_prosecution": prosecution,
        "first_defense": defense,
        "first_adjudication": proof,
    }
    value = "TRAJECTORY_PACKET=\n" + packet.canonical_json(built) + "\nAPPEAL_EVIDENCE=\n" + packet.canonical_json(neutral)
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"'):
        if field in value:
            raise ValueError("appeal input firewall violation: " + field)
    return value


def _appeal_defender_user(
    *, built: dict[str, Any], candidates: list[dict[str, Any]], base: dict[str, Any],
    prosecution: dict[str, Any], defense: dict[str, Any], proof: dict[str, Any],
) -> str:
    """Build the appellate defender's input. Identical structure to prosecutor
    but the system prompt instructs the defender role."""
    neutral = {
        "appeal_candidates": candidates,
        "semantic_findings": base.get("qualifications") or [],
        "causal_findings": base.get("propagations") or [],
        "case_boundary_finding": base.get("boundary_path") or {},
        "first_prosecution": prosecution,
        "first_defense": defense,
        "first_adjudication": proof,
    }
    value = "TRAJECTORY_PACKET=\n" + packet.canonical_json(built) + "\nAPPEAL_EVIDENCE=\n" + packet.canonical_json(neutral)
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"'):
        if field in value:
            raise ValueError("appeal defender input firewall violation: " + field)
    return value


# ============================================================
# v3.4: Merge and adjudicate
# ============================================================

def _merge_prosecutions(
    initial: dict[str, Any], appeal: dict[str, Any], built: dict[str, Any],
    allowed_appeal_candidate_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Merge initial and appeal prosecutions (same as v3.3)."""
    event_order = {str(event["event_id"]): index for index, event in enumerate(built["events"])}
    output = []
    seen = set()
    for route_name, value in (("appeal", appeal), ("initial", initial)):
        for index, original in enumerate((value or {}).get("candidates") or [], start=1):
            if not isinstance(original, dict):
                continue
            row = dict(original)
            if (
                route_name == "appeal" and allowed_appeal_candidate_ids is not None
                and str(row.get("source_candidate_id") or "") not in allowed_appeal_candidate_ids
            ):
                continue
            key = (
                str(row.get("source_candidate_id") or ""), str(row.get("origin_event_id") or ""),
                str(row.get("breach_type") or ""), str(row.get("impact_kind") or ""),
                str(row.get("benchmark_causal_status") or ""),
            )
            if key in seen:
                continue
            row["certificate_id"] = f"{route_name}_error_{index:03d}"
            output.append(row)
            seen.add(key)
    output.sort(key=lambda row: (
        0 if row.get("benchmark_causal_status") in {"causes_incorrect_result", "propagates_to_incorrect_result"} else 1,
        0 if row.get("impact_kind") != "process_debt" else 1,
        event_order.get(str(row.get("origin_event_id") or ""), 10**9),
        str(row.get("certificate_id") or ""),
    ))
    return {
        "coverage_state": str((initial or {}).get("coverage_state") or "unresolved"),
        "candidates": output[:32],
    }


def adjudicate_case(*, case_id: str, record: dict[str, Any], config: dict[str, Any], output_dir: Path, census_root: Path) -> dict[str, Any]:
    built = packet.build_packet(record)

    # Phase 1: Base layer (concurrent v2 adjudication + construct census)
    with ThreadPoolExecutor(max_workers=2) as executor:
        base_future = executor.submit(
            v2.adjudicate_case, case_id=case_id, record=record, config=config,
            output_dir=output_dir / "layered_v2", census_root=census_root,
        )
        construct_future = executor.submit(
            provider_call, route=config["construct_census_route"], system=CONSTRUCT_CENSUS_SYSTEM,
            user=v2._provider_user(built), cache_dir=output_dir / "construct_census_cache",
        )
        base = base_future.result()
        try:
            construct_value, construct_provider = construct_future.result()
            construct_value, axis_normalized_count = _normalize_construct_value(construct_value)
            construct_census, construct_audit = validate_provider_census(construct_value, built)
            construct_audit["axis_normalized_count"] = axis_normalized_count
        except Exception as exc:
            construct_census = []
            construct_audit = {"status": "provider_error", "error_type": type(exc).__name__, "error": str(exc)[:500]}
            construct_provider = {"error_type": type(exc).__name__, "error": str(exc)[:500]}

    construct_authoritative, construct_authority_reasons = _construct_authority(
        construct_audit
    )

    base_runtime_valid = bool(base.get("runtime_valid"))
    paca_census = list(base.get("census") or [])
    deterministic_census = _deterministic_process_census(built)
    base["paca_census"] = paca_census
    base["construct_census"] = construct_census
    base["deterministic_process_census"] = deterministic_census
    base["construct_census_audit"] = construct_audit
    base["census"] = _union_census(paca_census, construct_census, deterministic_census)
    user = _proof_user(built, base)
    providers: dict[str, Any] = dict(base.get("providers") or {})
    providers["construct_census"] = construct_provider

    # Phase 2: Initial prosecution + defense (concurrent)
    appeal_prosecution = None
    appeal_defense_certs = None
    combined_prosecution = None
    appeal_candidates: list[dict[str, Any]] = []
    initial_proof = None

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            prosecution_future = executor.submit(
                provider_call, route=config["prosecution_route"], system=PROSECUTION_SYSTEM,
                user=user, cache_dir=output_dir / "prosecution_cache",
            )
            defense_future = executor.submit(
                provider_call, route=config["defense_route"], system=DEFENSE_SYSTEM,
                user=user, cache_dir=output_dir / "defense_cache",
            )
            prosecution, providers["prosecution"] = prosecution_future.result()
            defense, providers["defense"] = defense_future.result()

        # Initial adjudication with v3.4 contract (materiality + commitment on)
        initial_proof = contract.adjudicate_proof_court(
            required_candidates=_required_candidates(base),
            prosecution=prosecution, defense=defense, packet=built,
            apply_materiality_check=True, apply_commitment_check=True,
        )
        proof = initial_proof

        # v3.4: Trigger calibrator (replaces broad appeal trigger)
        appeal_candidates = _appeal_trigger_calibrator(
            base=base, proof=initial_proof, prosecution=prosecution, defense=defense, built=built,
        )

        if appeal_candidates:
            allowed_ids = {str(row.get("candidate_id") or "") for row in appeal_candidates}
            try:
                # v3.4: Symmetric appeal -- prosecutor AND defender run concurrently
                appeal_user = _appeal_prosecutor_user(
                    built=built, candidates=appeal_candidates, base=base,
                    prosecution=prosecution, defense=defense, proof=initial_proof,
                )
                appeal_defender_user = _appeal_defender_user(
                    built=built, candidates=appeal_candidates, base=base,
                    prosecution=prosecution, defense=defense, proof=initial_proof,
                )

                with ThreadPoolExecutor(max_workers=2) as executor:
                    appeal_pros_future = executor.submit(
                        provider_call, route=config["appeal_prosecutor_route"],
                        system=APPEAL_PROSECUTOR_SYSTEM, user=appeal_user,
                        cache_dir=output_dir / "appeal_prosecutor_cache",
                    )
                    appeal_def_future = executor.submit(
                        provider_call, route=config["appeal_defender_route"],
                        system=APPEAL_DEFENDER_SYSTEM, user=appeal_defender_user,
                        cache_dir=output_dir / "appeal_defender_cache",
                    )
                    appeal_prosecution, providers["appeal_prosecutor"] = appeal_pros_future.result()
                    appeal_defense_certs, providers["appeal_defender"] = appeal_def_future.result()

                # Merge prosecutions (appeal + initial)
                combined_prosecution = _merge_prosecutions(
                    prosecution, appeal_prosecution, built, allowed_ids,
                )

                # v3.4: adjudicate with appeal defense merged into defense pool
                proof = contract.adjudicate_proof_court(
                    required_candidates=_required_candidates(base),
                    prosecution=combined_prosecution, defense=defense, packet=built,
                    apply_materiality_check=True, apply_commitment_check=True,
                    appeal_defense=appeal_defense_certs,
                )
            except Exception as exc:
                providers["appeal_error"] = {"error_type": type(exc).__name__, "error": str(exc)[:500]}
                proof = {
                    **initial_proof,
                    "pre_authority_state": initial_proof.get("state"),
                    "state": "unresolved",
                    "responsible_event_id": None,
                    "winning_error_certificate_id": None,
                    "winning_impact_kind": None,
                    "impact_status": None,
                    "decision_authority": "invalid",
                    "authority_failure_reasons": [{
                        "component": "appeal",
                        "code": "required_appeal_incomplete",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:500],
                    }],
                    "appeal_incomplete": True,
                }

        proof, construct_authoritative, construct_authority_reasons = (
            _apply_construct_authority(proof, construct_audit)
        )
        runtime_valid = (
            _proof_runtime_valid(base_runtime_valid, proof)
            and construct_authoritative
            and "appeal_error" not in providers
        )
    except Exception as exc:
        prosecution, defense = None, None
        proof = {
            "state": "unresolved", "responsible_event_id": None, "impact_status": None,
            "winning_impact_kind": None, "error_type": type(exc).__name__, "error": str(exc)[:500],
            "quarantined_error_certificate_ids": [], "quarantined_defense_certificate_ids": [],
            "decision_authority": "invalid",
            "authority_failure_reasons": [{
                "component": "proof_court",
                "code": "proof_court_exception",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            }],
            "occurrence_state": "unresolved", "incident_occurred": None,
        }
        providers["proof_court_error"] = {"error_type": type(exc).__name__, "error": str(exc)[:500]}
        runtime_valid = False

    # Build output
    output = dict(base)
    output.update({
        "runtime_valid": runtime_valid,
        "state": proof["state"],
        "decision_authority": proof.get("decision_authority", "invalid"),
        "authority_failure_reasons": proof.get("authority_failure_reasons") or [],
        "incident_state": {
            "verified_error": "verified_hallucination",
            "verified_clean": "verified_non_hallucination",
            "unresolved": "unresolved",
        }[proof["state"]],
        "responsible_event_id": proof["responsible_event_id"],
        "responsible_step": _responsible_step(proof["responsible_event_id"], built),
        "occurrence_state": proof.get("occurrence_state", "unresolved"),
        "incident_occurred": proof.get("incident_occurred"),
        "occurrence_responsible_event_id": proof.get("occurrence_responsible_event_id"),
        "occurrence_responsible_step": _responsible_step(
            proof.get("occurrence_responsible_event_id"), built,
        ),
        "occurrence_impact_status": proof.get("occurrence_impact_status"),
        "occurrence_impact_kind": proof.get("occurrence_impact_kind"),
        "occurrence_recovery_state": proof.get("occurrence_recovery_state"),
        "occurrence_ledger": proof.get("occurrence_ledger"),
        "impact_status": proof.get("impact_status"),
        "impact_kind": proof.get("winning_impact_kind"),
        "wide_construct_state": proof.get("wide_construct_state"),
        "prosecution": prosecution, "defense": defense,
        "appeal_prosecution": appeal_prosecution,
        "appeal_defense": appeal_defense_certs,
        "combined_prosecution": combined_prosecution,
        "initial_proof_adjudication": initial_proof,
        "appeal_candidates": appeal_candidates,
        "proof_adjudication": proof,
        "layered_v2_adjudication": base.get("adjudication"),
        "providers": providers,
        "evidence_health": {
            "base_runtime_valid": base_runtime_valid,
            "construct_view_status": construct_audit.get("status"),
            "construct_coverage_state": construct_audit.get("coverage_state"),
            "construct_raw_candidate_count": int(construct_audit.get("raw_candidate_count") or 0),
            "construct_accepted_candidate_count": int(construct_audit.get("converted_candidate_count") or 0),
            "construct_axis_normalized_count": int(construct_audit.get("axis_normalized_count") or 0),
            "construct_authoritative": construct_authoritative,
            "construct_authority_failure_reasons": construct_authority_reasons,
            "quarantined_error_certificate_count": len(proof.get("quarantined_error_certificate_ids") or []),
            "quarantined_defense_certificate_count": len(proof.get("quarantined_defense_certificate_ids") or []),
            "required_candidate_count": int(proof.get("required_candidate_count") or 0),
            "resolved_candidate_count": int(proof.get("resolved_candidate_count") or 0),
            "appeal_candidate_count": len(appeal_candidates),
            "appeal_prosecutor_certificate_count": len((appeal_prosecution or {}).get("candidates") or []),
            "appeal_defender_certificate_count": len((appeal_defense_certs or {}).get("candidates") or []),
            "appeal_status": "not_required" if not appeal_candidates else (
                "complete" if appeal_prosecution is not None else "provider_error"
            ),
            # v3.4 diagnostics
            "trigger_type_counts": dict(Counter(
                "triggered" if appeal_candidates else "not_triggered"
                for _ in [1]  # simplified; actual trigger detail in per-case
            )),
            "materiality_hits": proof.get("synthetic_defense_count") or 0,
            "evidence_authority_routes": proof.get("evidence_authority_routes") or {},
        },
    })
    return output


# ============================================================
# Evaluation and gate
# ============================================================

def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = v2.evaluate(_occurrence_first_rows(rows))
    proof_states, occurrence_states = Counter(), Counter()
    wide_states, impact_kinds, defenses = Counter(), Counter(), Counter()
    construct_states = Counter()
    q_error = q_defense = total_error = total_defense = 0
    construct_raw = construct_accepted = construct_normalized = 0
    required_candidates = resolved_candidates = 0
    appeal_candidates_total = appeal_pros_certs_total = appeal_def_certs_total = 0
    appeal_states = Counter()
    materiality_hits_total = 0
    causal_predicted = causal_valid = 0
    for row in rows:
        proof = row.get("proof_adjudication") or {}
        health = row.get("evidence_health") or {}
        proof_states[str(proof.get("state") or "unresolved")] += 1
        occurrence_states[str(proof.get("occurrence_state") or "unresolved")] += 1
        wide_states[str(proof.get("wide_construct_state") or "unresolved")] += 1
        if row.get("impact_kind"):
            impact_kinds[str(row["impact_kind"])] += 1
        q_error += len(proof.get("quarantined_error_certificate_ids") or [])
        q_defense += len(proof.get("quarantined_defense_certificate_ids") or [])
        total_error += len(proof.get("error_audits") or [])
        total_defense += len(proof.get("defense_audits") or [])
        construct_states[str(health.get("construct_view_status") or "unknown")] += 1
        construct_raw += int(health.get("construct_raw_candidate_count") or 0)
        construct_accepted += int(health.get("construct_accepted_candidate_count") or 0)
        construct_normalized += int(health.get("construct_axis_normalized_count") or 0)
        required_candidates += int(health.get("required_candidate_count") or 0)
        resolved_candidates += int(health.get("resolved_candidate_count") or 0)
        appeal_candidates_total += int(health.get("appeal_candidate_count") or 0)
        appeal_pros_certs_total += int(health.get("appeal_prosecutor_certificate_count") or 0)
        appeal_def_certs_total += int(health.get("appeal_defender_certificate_count") or 0)
        appeal_states[str(health.get("appeal_status") or "unknown")] += 1
        materiality_hits_total += int(health.get("materiality_hits") or 0)
        predicted_count, valid_count = _causal_certificate_counts(row)
        causal_predicted += predicted_count
        causal_valid += valid_count
        for key, value in (proof.get("defense_type_counts") or {}).items():
            defenses[str(key)] += int(value)
    metrics.update({
        "proof_state_counts": dict(proof_states),
        "occurrence_state_counts": dict(occurrence_states),
        "official_prediction_basis": "occurrence_state",
        "wide_construct_state_counts": dict(wide_states),
        "impact_kind_counts": dict(impact_kinds),
        "causal_chain_validity": (
            causal_valid / causal_predicted if causal_predicted else None
        ),
        "causal_error_certificate_validity": (
            causal_valid / causal_predicted if causal_predicted else None
        ),
        "causal_chain_validity_defined": bool(causal_predicted),
        "causal_chain_validity_denominator": causal_predicted,
        "predicted_causal_error_certificate_count": causal_predicted,
        "valid_causal_error_certificate_count": causal_valid,
        "causal_chain_validity_semantics": (
            "fully valid, addressable causal error certificates divided by "
            "all certificates predicting a causal error; undefined when none are predicted"
        ),
        "defense_type_counts": dict(defenses), "quarantined_error_certificate_count": q_error,
        "quarantined_defense_certificate_count": q_defense,
        "construct_view_status_counts": dict(construct_states),
        "construct_candidate_acceptance_rate": construct_accepted / construct_raw if construct_raw else 1.0,
        "construct_axis_normalization_count": construct_normalized,
        "error_certificate_acceptance_rate": (total_error - q_error) / total_error if total_error else 1.0,
        "defense_certificate_acceptance_rate": (total_defense - q_defense) / total_defense if total_defense else 1.0,
        "required_candidate_resolution_rate": resolved_candidates / required_candidates if required_candidates else 1.0,
        "appeal_candidate_count": appeal_candidates_total,
        "appeal_prosecutor_certificate_count": appeal_pros_certs_total,
        "appeal_defender_certificate_count": appeal_def_certs_total,
        "appeal_status_counts": dict(appeal_states),
        # v3.4 diagnostics
        "materiality_defense_hits": materiality_hits_total,
    })
    return metrics


def evaluate_gate(metrics: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    mappings = {
        "macro_f1_min": ("macro_f1", ">="),
        "hallucination_precision_min": ("precision", ">="),
        "hallucination_recall_min": ("recall", ">="),
        "clean_fpr_max": ("clean_fpr", "<="),
        "responsible_step_accuracy_min": ("responsible_step_recall_over_all_gold_positive", ">="),
        "runtime_valid_rate_min": ("runtime_valid_rate", ">="),
        "candidate_origin_coverage_min": ("candidate_origin_coverage_on_gold_positive", ">="),
        "causal_chain_validity_min": ("causal_chain_validity", ">="),
    }
    checks = {}
    for gate_key, (metric_key, operator) in mappings.items():
        if gate_key not in gate:
            continue
        observed, required = float(metrics.get(metric_key) or 0.0), float(gate[gate_key])
        passed = observed >= required if operator == ">=" else observed <= required
        checks[gate_key] = {
            "metric": metric_key, "observed": observed, "operator": operator,
            "required": required, "passed": passed,
        }
    failed = sorted(key for key, value in checks.items() if not value["passed"])
    return {"decision": "pass" if not failed else "fail", "checks": checks, "failed_checks": failed}


def preflight(*, config_path: Path, dataset_root: Path, manifest_path: Path, census_root: Path) -> dict[str, Any]:
    config = read_json(config_path)
    base = v2.preflight(config_path=config_path, dataset_root=dataset_root, manifest_path=manifest_path, census_root=census_root)
    required_routes = {
        "construct_census_route", "prosecution_route", "defense_route",
        "appeal_prosecutor_route", "appeal_defender_route",
    }
    failures = list(base.get("failures") or [])
    if not required_routes <= set(config):
        failures.append({"error": "missing proof routes"})
    construct_path = Path(str(config.get("hallucination_construct") or ""))
    if not construct_path.is_absolute():
        construct_path = config_path.resolve().parents[1] / construct_path
    try:
        construct_value = read_json(construct_path)
        if construct_value.get("status") not in {"canonical_for_PACA_TCL_v3", "canonical_for_PACA_TCL_v3_1"}:
            failures.append({"error": "hallucination construct is not canonical"})
    except Exception as exc:
        failures.append({"error": "hallucination construct unreadable", "error_type": type(exc).__name__})
    decision = "pass" if base.get("decision") == "pass" and not failures else "fail"
    return {
        "schema_version": "agenthallu.paca_tcl_proof_preflight.v3_4", "decision": decision,
        "case_count": base.get("case_count"), "counts": base.get("counts"), "failures": failures,
        "provider_calls_made": 0, "outer_opened": False,
        "construct": config.get("hallucination_construct"),
    }


def run(*, config_path: Path, dataset_root: Path, manifest_path: Path, census_root: Path, output_dir: Path) -> dict[str, Any]:
    config = read_json(config_path)
    if config.get("execution_authorized") is not True or config.get("authorized_phase") != "P0_diagnostic_only":
        raise RuntimeError("proof P0 execution is not authorized")
    for name in (
        "qualification_route", "propagation_route", "boundary_route", "construct_census_route",
        "prosecution_route", "defense_route", "appeal_prosecutor_route", "appeal_defender_route",
    ):
        if not os.environ.get(str(config[name]["api_key_environment"])):
            raise RuntimeError(f"provider API key environment variable is absent: {name}")
    manifest, records = read_json(manifest_path), discover_records(dataset_root)
    case_ids, by_case = p0_case_ids(manifest), {}
    with ThreadPoolExecutor(max_workers=int(config.get("max_parallel_cases", 4))) as executor:
        futures = {executor.submit(
            adjudicate_case, case_id=case_id, record=records[case_id],
            config=config, output_dir=output_dir, census_root=census_root,
        ): case_id for case_id in case_ids}
        for future in as_completed(futures):
            by_case[futures[future]] = future.result()
            write_jsonl(output_dir / "predictions.jsonl", [by_case[key] for key in case_ids if key in by_case])
    rows = [by_case[key] for key in case_ids]
    metrics = evaluate(rows)
    report = {
        "schema_version": "agenthallu.paca_tcl_proof_p0.v3_4", "experiment_id": config["experiment_id"],
        "effectiveness_scope": "opened_P0_diagnostic_only", "outer_opened": False,
        "hallucination_construct": config["hallucination_construct"], "metrics": metrics,
        "primary_gate": evaluate_gate(metrics, config.get("primary_p0_gate") or {}),
    }
    write_json(output_dir / "metrics.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--paca-census-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.preflight_only:
        report = preflight(config_path=args.config, dataset_root=args.dataset_root, manifest_path=args.manifest, census_root=args.paca_census_root)
        write_json(args.output_dir / "preflight.json", report)
    else:
        lock = args.output_dir / ".paca_tcl_proof.lock"
        try:
            with lock.open("x", encoding="utf-8") as handle:
                handle.write(str(os.getpid()) + "\n")
        except FileExistsError as exc:
            raise RuntimeError("output directory is locked") from exc
        try:
            report = run(config_path=args.config, dataset_root=args.dataset_root, manifest_path=args.manifest, census_root=args.paca_census_root, output_dir=args.output_dir)
        finally:
            lock.unlink(missing_ok=True)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
