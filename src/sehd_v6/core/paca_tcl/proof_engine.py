"""Run PACA-TCL v3.5 proof court with focused prosecution and calibrated appeal.

v3.5 structural interventions over v3.4:
1. H1: Candidate-Focused Prosecution (per-candidate calls + causal chain scaffolding + iterative refinement)
2. H2: Calibrated Appeal (tightened trigger priority 0-1 + informed defender + consensus gate)
3. H3: Two-Phase Localization (pairwise counterfactual comparison for earliest-origin)

Information flow topology change:
- v3.4: broadcast all candidates → single prosecution call → single defense call
- v3.5: per-candidate focused prosecution (parallel) → merge → defense → calibrated appeal → counterfactual localization
"""

from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping, Sequence

from ...utils import ocse_packet as packet
from . import proof_contract as contract
from . import layered_engine as v2
from .basic_runner import (
    _responsible_step, provider_call, validate_provider_census,
    json_object_provider_call, _addressable_refs,
)
from .proof_engine_v3_4 import (
    CONSTRUCT_CENSUS_SYSTEM, CONSTRUCT_AXIS_ALIASES,
    DEFENSE_SYSTEM,
    _normalize_construct_value, _deterministic_process_census, _union_census,
    _required_origins, _required_candidates, _proof_runtime_valid, _proof_user,
    _merge_prosecutions,
    _construct_authority, _apply_construct_authority,
    _occurrence_first_rows, _causal_certificate_counts,
)


# ============================================================
# H1: Focused Prosecution Prompts (per-candidate, scaffolded)
# ============================================================

FOCUSED_PROSECUTION_SYSTEM = """You are the focused error-certificate constructor in PACA-TCL v3.5. You receive ONE candidate origin at a time and must construct a complete causal chain proof for that specific candidate.

YOUR TASK: For the single FOCUSED_CANDIDATE provided, determine whether it constitutes a verified hallucination under the official AgentHallu benchmark target. Construct a complete causal chain following this exact scaffold:

CAUSAL CHAIN SCAFFOLD (all stages required):
1. NORM_IDENTIFICATION: What specific norm, obligation, instruction, contract, or logical invariant was violated? Cite the exact evidence.
2. ORIGIN_EVENT: What specific agent event is the origin of the error? (Must match the candidate's origin_hypothesis_event_id)
3. AGENT_CONTROL: How did the agent control, adopt, generate, or omit this? (Not external — anchor to agent action/assertion/adoption/omission)
4. PROPAGATION_PATH: How does the error propagate from origin through subsequent events?
5. IMPACT_ON_RESULT: What is the specific impact on the produced result? (final_answer, persistent state, committed action, etc.)
6. COUNTERFACTUAL_TEST: If ONLY this origin were corrected while all prior evidence remained fixed, would the incorrect result be prevented?

OFFICIAL TARGET: A positive requires (a) an incorrect produced result, (b) an addressable agent-controlled origin, (c) verifiable breach against task/context/tools/facts, (d) causal link from origin to incorrect result.

CRITICAL RULES:
- Do NOT certify for minor rounding differences unless exact precision is demanded.
- Do NOT certify invalid_action for rejected/errored tool calls with no state change.
- Do NOT certify ordinary failure, tentative hypotheses, legitimate retries, or harmless inefficiency.
- External observations are evidence only; anchor to agent adoption/action.
- process_debt alone → benchmark_causal_status = "process_only" (auxiliary, not positive).
- If you cannot construct a complete causal chain with addressable evidence, return an empty candidates list.

All evidence arrays must use exact event/interaction IDs from TRAJECTORY_PACKET. Return JSON only:
{"coverage_state":"complete|incomplete|unresolved","candidates":[{"certificate_id":"focused_error_001","source_candidate_id":"<the candidate_id>","origin_event_id":"<origin>","claim_kind":"agent_assertion|agent_action|agent_omission|agent_adoption","norm_source":"user_instruction|tool_contract|prior_trajectory_evidence|logical_invariant|external_evidence","norm_evidence_refs":["event_XXX"],"breach_type":"contradiction|fabrication|unsupported_commitment|instruction_violation|invalid_action|consequential_omission|redundant_repetition","violation_evidence_refs":["event_XXX"],"agent_control_event_id":"event_XXX","agent_control_evidence_refs":["event_XXX"],"impact_kind":"final_answer|persistent_external_state|persistent_workspace_state|committed_tool_action|unrecovered_reasoning_dependency|process_debt","impact_event_ids":["event_XXX"],"impact_evidence_refs":["event_XXX"],"recovery_state":"none|post_impact_residual|pre_impact_repaired|unresolved","recovery_evidence_refs":[],"benchmark_causal_status":"causes_incorrect_result|propagates_to_incorrect_result|process_only|unresolved","benchmark_causal_evidence_refs":["event_XXX"],"rationale":"NORM: ... | ORIGIN: ... | CONTROL: ... | PROPAGATION: ... | IMPACT: ... | COUNTERFACTUAL: ..."}]}"""

FOCUSED_PROSECUTION_RETRY_SYSTEM = """You are the focused error-certificate constructor in PACA-TCL v3.5, RETRY ROUND. Your previous certificate was QUARANTINED. You must reconstruct it addressing the specific failures identified below.

QUARANTINE_FEEDBACK:
{feedback}

Reconstruct the certificate for the same FOCUSED_CANDIDATE, fixing ALL identified issues. Follow the same causal chain scaffold:
1. NORM_IDENTIFICATION → 2. ORIGIN_EVENT → 3. AGENT_CONTROL → 4. PROPAGATION_PATH → 5. IMPACT_ON_RESULT → 6. COUNTERFACTUAL_TEST

If after careful analysis you determine this candidate genuinely does not constitute a verified hallucination, return an empty candidates list — do NOT force a certificate.

All evidence arrays must use exact event/interaction IDs from TRAJECTORY_PACKET. Return JSON only using the same schema as before."""


# ============================================================
# H2: Calibrated Appeal Prompts
# ============================================================

APPEAL_PROSECUTOR_SYSTEM_V35 = """You are the candidate-level appellate prosecutor in PACA-TCL v3.5. Re-open only the supplied APPEAL_CANDIDATES because the first proof court left a specific structural concern (quarantined certificate or unresolved causal link).

IMPORTANT: You have an independent appeal defender running in parallel who can see YOUR certificates. Build the strongest possible prosecution with complete causal chains.

For each appeal candidate, follow the causal chain scaffold:
1. NORM_IDENTIFICATION: What norm was violated?
2. ORIGIN_EVENT: The specific agent event.
3. AGENT_CONTROL: How the agent controlled/adopted this.
4. PROPAGATION_PATH: How the error propagates.
5. IMPACT_ON_RESULT: The specific impact.
6. COUNTERFACTUAL_TEST: Would correcting this alone fix the result?

CRITICAL: The consensus gate requires that your appeal prosecution can only produce a positive if the INITIAL prosecution independently found supporting evidence for the same candidate. If the initial prosecution found nothing for a candidate, your appeal certificate will be blocked. Focus on candidates where initial evidence exists but was quarantined or had unresolved causal status.

CRITICAL MATERIALITY RULE: Do NOT certify contradiction for minor rounding differences.
CRITICAL COMMITMENT RULE: Do NOT certify invalid_action for rejected/errored tool calls.

COVERAGE CONTRACT: You must explicitly record every appeal candidate you reviewed. ``coverage_state="complete"`` means you examined ALL supplied APPEAL_CANDIDATES and either built a prosecution certificate or deliberately decided no prosecution applies. An empty ``candidates`` list is a legitimate "complete review, no prosecution" outcome; ``coverage_state="incomplete"`` means you did not finish reviewing the set.

Return JSON only using the prosecution certificate schema. ``reviewed_candidate_ids`` MUST list every APPEAL_CANDIDATE id you examined (verbatim, including those with no resulting certificate):
{"coverage_state":"complete|incomplete|unresolved","reviewed_candidate_ids":["v3_candidate_001"],"candidates":[{"certificate_id":"appeal_error_001","source_candidate_id":"v3_candidate_001","origin_event_id":"event_001","claim_kind":"agent_assertion|agent_action|agent_omission|agent_adoption","norm_source":"user_instruction|tool_contract|prior_trajectory_evidence|logical_invariant|external_evidence","norm_evidence_refs":["event_001"],"breach_type":"contradiction|fabrication|unsupported_commitment|instruction_violation|invalid_action|consequential_omission|redundant_repetition","violation_evidence_refs":["event_001"],"agent_control_event_id":"event_001","agent_control_evidence_refs":["event_001"],"impact_kind":"final_answer|persistent_external_state|persistent_workspace_state|committed_tool_action|unrecovered_reasoning_dependency|process_debt","impact_event_ids":["event_002"],"impact_evidence_refs":["event_002"],"recovery_state":"none|post_impact_residual|pre_impact_repaired|unresolved","recovery_evidence_refs":[],"benchmark_causal_status":"causes_incorrect_result|propagates_to_incorrect_result|process_only|unresolved","benchmark_causal_evidence_refs":["event_002"],"rationale":"..."}]}"""

APPEAL_PROSECUTOR_RETRY_SYSTEM_V35 = """You are the candidate-level appellate prosecutor in PACA-TCL v3.5, RETRYING after a structurally incomplete first response. Re-open only the supplied APPEAL_CANDIDATES.

Your previous response was rejected for a structured reason supplied in APPEAL_RETRY_FEEDBACK (see the failure_category and missing_field there). Fix that specific defect while keeping the same APPEAL_CANDIDATES and evidence. Do NOT repeat the same malformed output.

TWO HARD REQUIREMENTS (both must hold, no exceptions, no defaults):
1. ``coverage_state`` MUST be exactly the string ``"complete"``. Do not return ``"incomplete"``, ``"unresolved"``, or omit the field.
2. ``reviewed_candidate_ids`` MUST be a JSON array listing EVERY supplied APPEAL_CANDIDATE id verbatim (copy the ids from APPEAL_CANDIDATES; do not omit, rename, or invent any). An empty ``candidates`` list with these two fields satisfied is a valid "reviewed all, no prosecution" outcome.

DEFECT-SPECIFIC FIX (match APPEAL_RETRY_FEEDBACK.failure_category):
- ``missing_coverage_state`` / ``incomplete_coverage_state``: set ``coverage_state="complete"``.
- ``missing_reviewed_candidate_ids``: add ``reviewed_candidate_ids`` listing every supplied APPEAL_CANDIDATE id.
- ``unreviewed_candidate_ids``: re-read the supplied APPEAL_CANDIDATES and set ``reviewed_candidate_ids`` to exactly that id set.
- ``foreign_candidate_id``: drop any ``candidates[].source_candidate_id`` not among the supplied APPEAL_CANDIDATES.
- ``response_truncated``: your previous output was cut off before the JSON closed. Emit the JSON object immediately — do not re-narrate evidence or repeat the trajectory; ``candidates`` may be empty if no prosecution applies.
- ``provider_exception``: re-emit the same structured JSON; do not echo the exception text.

Return JSON only with the same schema as the initial call:
{"coverage_state":"complete","reviewed_candidate_ids":["<every supplied APPEAL_CANDIDATE id>"],"candidates":[...]}"""

APPEAL_DEFENDER_SYSTEM_V35 = """You are the candidate-level appellate defender in PACA-TCL v3.5. An appellate prosecutor is constructing new prosecution certificates for the same APPEAL_CANDIDATES. Your job is to construct the strongest possible clean-boundary defense.

INFORMED DEFENSE: You can see the PROSECUTION_CERTIFICATES below — these are the specific error claims being made. Construct defenses that directly address each prosecution claim's mechanism.

For each appeal candidate, examine the prosecution certificates targeting it and determine:
1. Is the norm identification correct? (If not: supported_or_correct)
2. Is the agent control attribution correct? (If external: external_unadopted)
3. Was the action authorized? (If yes: explicitly_authorized)
4. Is there a committed state change? (If rejected: rejected_attempt_no_state_change)
5. Is the numerical difference material? (If within tolerance: materially_equivalent_or_within_tolerance)
6. Is absence of evidence being confused with fabrication? (If so: absence_of_observation_not_fabrication)
7. Was the failure ordinary without commitment? (If so: ordinary_failure_without_commitment)

If no specific defense can be proven with addressable evidence, use insufficient_evidence.

Return one certificate for every supplied appeal candidate. evidence_refs must be non-empty and copied exactly from TRAJECTORY_PACKET. Return JSON only:
{"coverage_state":"complete|incomplete|unresolved","candidates":[{"certificate_id":"appeal_defense_001","target_candidate_id":"v3_candidate_001","target_origin_event_id":"event_001","defense_type":"supported_or_correct|external_unadopted|explicitly_authorized|ordinary_failure_without_commitment|legitimate_retry_with_state_change|information_gainful_repetition|required_revalidation|materially_equivalent_or_within_tolerance|rejected_attempt_no_state_change|absence_of_observation_not_fabrication|insufficient_evidence","evidence_refs":["event_001"],"rationale":"..."}]}"""


APPEAL_DEFENDER_RETRY_SYSTEM_V35 = """You are the candidate-level appellate defender in PACA-TCL v3.5, RETRYING after a structurally incomplete first response. An appellate prosecutor's certificates are supplied unchanged; construct the strongest possible clean-boundary defense for the supplied APPEAL_CANDIDATES.

Your previous response was rejected for a structured reason supplied in APPEAL_RETRY_FEEDBACK (see the failure_category and missing_field there). Fix that specific defect. Do NOT repeat the same malformed output.

TWO HARD REQUIREMENTS (both must hold, no exceptions, no defaults):
1. ``coverage_state`` MUST be exactly the string ``"complete"``. Do not return ``"incomplete"``, ``"unresolved"``, or omit the field.
2. ``candidates[].target_candidate_id`` MUST cover EVERY supplied APPEAL_CANDIDATE id verbatim (copy the ids from APPEAL_CANDIDATES; do not omit, rename, or invent any). Return exactly one defense certificate per supplied appeal candidate. An ``insufficient_evidence`` certificate is a legitimate defense when no stronger defense applies.

DEFECT-SPECIFIC FIX (match APPEAL_RETRY_FEEDBACK.failure_category):
- ``missing_coverage_state`` / ``incomplete_coverage_state``: set ``coverage_state="complete"``.
- ``missing_defense_candidates``: emit a ``candidates`` array with one defense certificate per supplied APPEAL_CANDIDATE.
- ``unreviewed_candidate_ids`` / ``defense_coverage_mismatch``: re-read the supplied APPEAL_CANDIDATES and ensure ``candidates[].target_candidate_id`` is exactly that id set.
- ``response_truncated``: your previous output was cut off before the JSON closed. Emit the JSON object immediately — do not re-narrate evidence; an ``insufficient_evidence`` certificate per candidate is acceptable.
- ``provider_exception``: re-emit the same structured JSON; do not echo the exception text.

Return JSON only with the same schema as the initial call:
{"coverage_state":"complete","candidates":[{"certificate_id":"appeal_defense_001","target_candidate_id":"<every supplied APPEAL_CANDIDATE id>","target_origin_event_id":"event_001","defense_type":"...","evidence_refs":["event_001"],"rationale":"..."}]}"""


# ============================================================
# H3: Two-Phase Localization Prompt
# ============================================================

COUNTERFACTUAL_LOCALIZATION_SYSTEM = """You are the counterfactual localization analyst in PACA-TCL v3.5. Given multiple surviving error origins for the same trajectory, determine which is the EARLIEST CAUSALLY SUFFICIENT origin — the one whose correction alone would prevent the incorrect result.

For each candidate origin, perform a pairwise counterfactual test:
"If ONLY this origin were corrected (all other events unchanged), would the incorrect produced result be prevented?"

Rank origins by causal sufficiency:
- is_root_cause=true: correcting this origin ALONE is sufficient to prevent the incorrect result
- causal_sufficiency_score: 0.0-1.0, how confident you are that this origin is the root cause

Prefer EARLIER origins when sufficiency is equal (the benchmark definition requires the EARLIEST agent error whose correction fixes the result).

Return JSON only:
{"rankings":{"event_XXX":{"causal_sufficiency_score":0.9,"is_root_cause":true,"counterfactual_reasoning":"..."},"event_YYY":{"causal_sufficiency_score":0.3,"is_root_cause":false,"counterfactual_reasoning":"..."}}}"""


# ============================================================
# H1: Focused Prosecution Implementation
# ============================================================

def _focused_prosecution_user(
    *, built: dict[str, Any], candidate: dict[str, Any], base: dict[str, Any],
) -> str:
    """Build the focused prosecution input for a single candidate."""
    neutral = {
        "focused_candidate": candidate,
        "all_candidates_context": [
            {"candidate_id": c.get("candidate_id"), "origin_hypothesis_event_id": c.get("origin_hypothesis_event_id"),
             "candidate_kind": c.get("candidate_kind")}
            for c in (base.get("census") or [])
        ],
        "semantic_findings": base.get("qualifications") or [],
        "causal_findings": base.get("propagations") or [],
        "case_boundary_finding": {
            key: (base.get("boundary_path") or {}).get(key)
            for key in ("state", "responsible_event_id", "evidence_refs", "rationale")
        },
    }
    value = (
        "TRAJECTORY_PACKET=\n" + packet.canonical_json(built)
        + "\nFOCUSED_CANDIDATE=\n" + packet.canonical_json(candidate)
        + "\nNEUTRAL_EVIDENCE=\n" + packet.canonical_json(neutral)
    )
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"'):
        if field in value:
            raise ValueError("focused prosecution input firewall violation: " + field)
    return value


def _focused_prosecution_retry_user(
    *, built: dict[str, Any], candidate: dict[str, Any], base: dict[str, Any],
    feedback: str,
) -> str:
    """Build the retry input with quarantine feedback."""
    neutral = {
        "focused_candidate": candidate,
        "quarantine_feedback": feedback,
        "semantic_findings": base.get("qualifications") or [],
        "causal_findings": base.get("propagations") or [],
    }
    value = (
        "TRAJECTORY_PACKET=\n" + packet.canonical_json(built)
        + "\nFOCUSED_CANDIDATE=\n" + packet.canonical_json(candidate)
        + "\nNEUTRAL_EVIDENCE=\n" + packet.canonical_json(neutral)
    )
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"'):
        if field in value:
            raise ValueError("focused prosecution retry input firewall violation: " + field)
    return value


def _run_focused_prosecution_for_candidate(
    *, candidate: dict[str, Any], built: dict[str, Any], base: dict[str, Any],
    config: dict[str, Any], output_dir: Path, max_retries: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Run focused prosecution for a single candidate with iterative refinement.

    Returns:
        (certificates, audits, provider_meta)
    """
    candidate_id = str(candidate.get("candidate_id") or "unknown")
    cache_dir = output_dir / "focused_prosecution_cache" / candidate_id
    all_audits: list[dict[str, Any]] = []
    provider_meta: dict[str, Any] = {}
    if max_retries is None:
        max_retries = max(0, int(config.get("focused_prosecution_retries", 2)))

    # Round 1: initial focused prosecution
    user = _focused_prosecution_user(built=built, candidate=candidate, base=base)
    try:
        value, meta = provider_call(
            route=config["focused_prosecution_route"],
            system=FOCUSED_PROSECUTION_SYSTEM,
            user=user, cache_dir=cache_dir,
        )
        provider_meta["round1"] = meta
    except Exception as exc:
        provider_meta["round1_error"] = {"error_type": type(exc).__name__, "error": str(exc)[:500]}
        return [], [], provider_meta

    coverage_state = str(value.get("coverage_state") or "unresolved")
    certificates = list(value.get("candidates") or [])
    if coverage_state != "complete":
        provider_meta["coverage_error"] = {
            "declared_coverage_state": coverage_state,
        }
        return [], [], provider_meta
    if any(
        not isinstance(cert, dict)
        or str(cert.get("source_candidate_id") or "") != candidate_id
        for cert in certificates
    ):
        provider_meta["coverage_error"] = {
            "error": "focused response contains another candidate",
        }
        return [], [], provider_meta
    if not certificates:
        # Provider determined no error — this is a valid "no prosecution" result
        return [], [], provider_meta

    # Validate each certificate
    candidate_origins = {candidate_id: str(candidate.get("origin_hypothesis_event_id") or "")}
    for cert in certificates:
        audit = contract.validate_focused_prosecution(cert, built, candidate_origins)
        audit["source_candidate_id"] = candidate_id
        all_audits.append(audit)

    # A mixed valid/quarantined response leaves part of the provider's claimed
    # proof unresolved. Retry the whole candidate instead of silently dropping
    # the invalid claim and declaring complete coverage.
    passed_certs = [
        cert for cert, audit in zip(certificates, all_audits)
        if audit["decision"] == "pass"
    ]
    if passed_certs and len(passed_certs) == len(certificates):
        return passed_certs, all_audits, provider_meta
    provider_meta["all_quarantined"] = True  # round 1 produced certs, none valid

    # All quarantined — iterative refinement loop
    for retry_round in range(1, max_retries + 1):
        # Build feedback from the last audit
        last_audit = all_audits[-1] if all_audits else {}
        feedback = contract.build_quarantine_feedback(last_audit)

        retry_system = FOCUSED_PROSECUTION_RETRY_SYSTEM.format(feedback=feedback)
        retry_user = _focused_prosecution_retry_user(
            built=built, candidate=candidate, base=base, feedback=feedback,
        )
        retry_cache = output_dir / "focused_prosecution_cache" / candidate_id / f"retry_{retry_round}"

        try:
            value, meta = provider_call(
                route=config["focused_prosecution_route"],
                system=retry_system, user=retry_user, cache_dir=retry_cache,
            )
            provider_meta[f"retry_{retry_round}"] = meta
        except Exception as exc:
            provider_meta[f"retry_{retry_round}_error"] = {"error_type": type(exc).__name__, "error": str(exc)[:500]}
            break

        coverage_state = str(value.get("coverage_state") or "unresolved")
        certificates = list(value.get("candidates") or [])
        if coverage_state != "complete":
            provider_meta[f"retry_{retry_round}_coverage_error"] = {
                "declared_coverage_state": coverage_state,
            }
            break
        if any(
            not isinstance(cert, dict)
            or str(cert.get("source_candidate_id") or "") != candidate_id
            for cert in certificates
        ):
            provider_meta[f"retry_{retry_round}_coverage_error"] = {
                "error": "focused response contains another candidate",
            }
            break
        if not certificates:
            # The provider retracted after quarantine feedback; this is an
            # explicit no-error decision, not a quarantined certificate.
            provider_meta.pop("all_quarantined", None)
            break

        for cert in certificates:
            audit = contract.validate_focused_prosecution(cert, built, candidate_origins)
            audit["source_candidate_id"] = candidate_id
            all_audits.append(audit)

        passed_certs = [
            cert for cert, audit in zip(certificates, all_audits[-len(certificates):])
            if audit["decision"] == "pass"
        ]
        if passed_certs and len(passed_certs) == len(certificates):
            provider_meta.pop("all_quarantined", None)
            return passed_certs, all_audits, provider_meta
        provider_meta["all_quarantined"] = True

    # All retries exhausted: only a quarantine-only round can reach this point
    # with the flag set; any explicit no-error retraction cleared it above.
    return [], all_audits, provider_meta


def _run_focused_prosecution(
    *, built: dict[str, Any], base: dict[str, Any], config: dict[str, Any], output_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Run focused prosecution for all candidates in parallel.

    Returns:
        (merged_prosecution, all_audits, provider_metas)
    """
    candidates = _required_candidates(base)
    if not candidates:
        return {"coverage_state": "complete", "candidates": []}, [], {}

    all_certificates: list[dict[str, Any]] = []
    all_audits: list[dict[str, Any]] = []
    provider_metas: dict[str, Any] = {}

    # Run per-candidate prosecution in parallel (bounded concurrency)
    max_workers = min(len(candidates), int(config.get("max_focused_parallel", 4)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _run_focused_prosecution_for_candidate,
                candidate=candidate, built=built, base=base,
                config=config, output_dir=output_dir,
            ): candidate
            for candidate in candidates
        }
        for future in as_completed(futures):
            candidate = futures[future]
            candidate_id = str(candidate.get("candidate_id") or "unknown")
            try:
                certs, audits, meta = future.result()
                all_certificates.extend(certs)
                all_audits.extend(audits)
                provider_metas[candidate_id] = meta
            except Exception as exc:
                provider_metas[candidate_id] = {"error_type": type(exc).__name__, "error": str(exc)[:500]}

    all_certificates.sort(key=lambda cert: (
        str(cert.get("source_candidate_id") or ""),
        str(cert.get("origin_event_id") or ""),
        str(cert.get("breach_type") or ""),
        str(cert.get("impact_kind") or ""),
        str(cert.get("certificate_id") or ""),
    ))
    certificate_overflow = len(all_certificates) > 32
    # Renumber certificate IDs only after deterministic ordering.
    for idx, cert in enumerate(all_certificates, 1):
        cert["certificate_id"] = f"focused_error_{idx:03d}"

    # Determine coverage state (v3.5.1 fix: distinguish LLM-empty from LLM-crash)
    # OLD bug: prosecuted_candidates only counted candidates that produced certificates.
    # When the LLM explicitly returned "no error" (empty candidates), the candidate was
    # absent from prosecuted_candidates → coverage degraded to "unresolved" → the frozen
    # verdict tree's verified_clean branch (requires p_coverage=="complete") never fired.
    # FIX: track attempted candidates (all submitted to provider) AND errored candidates
    # (provider crashed). LLM-empty counts as attempted-success → can contribute to
    # "complete". LLM-crash counts as attempted-error → forces "incomplete" (fail-closed).
    #
    # v3.5.2 fix: a candidate whose certificates were all QUARANTINED (produced but
    # structurally invalid after retries) is neither an explicit no-error nor a crash:
    # it leaves an open structural concern. It must force "incomplete" coverage
    # (fail-closed) and remain appealable via the calibrated trigger.
    all_candidate_ids = {str(c.get("candidate_id") or "") for c in candidates}
    errored_candidates = {
        str(cid) for cid, meta in (provider_metas or {}).items()
        if meta and (any(k.endswith("_error") for k in meta) or "error_type" in (meta or {}))
    }
    quarantined_candidates = {
        str(cid) for cid, meta in (provider_metas or {}).items()
        if meta and meta.get("all_quarantined")
    }
    successful_candidates = all_candidate_ids - errored_candidates - quarantined_candidates
    if errored_candidates:
        # At least one provider call crashed → fail-closed (do not claim complete coverage)
        coverage = "incomplete"
    elif quarantined_candidates:
        # Quarantined-only candidates leave an open structural concern → fail-closed
        coverage = "incomplete"
    # FIX (D5): 集合比较应为相等（==）而非超集（>=）；此处语义是
    # “所有候选均成功”，successful 是 attempted 的子集，>= 仅在相等时
    # 成立但表达意图错误且易误导维护。
    elif certificate_overflow:
        coverage = "incomplete"
    elif successful_candidates == all_candidate_ids:
        # All candidates were submitted and the LLM returned a result (empty = no error)
        coverage = "complete"
    elif successful_candidates:
        coverage = "incomplete"
    else:
        coverage = "unresolved"

    merged = {"coverage_state": coverage, "candidates": all_certificates[:32]}
    return merged, all_audits, provider_metas


# ============================================================
# H2: Tightened Trigger Calibrator (priority 0-1 only)
# ============================================================

def _appeal_trigger_calibrator_v35(
    *, base: dict[str, Any], proof: dict[str, Any], prosecution: dict[str, Any],
    defense: dict[str, Any], built: dict[str, Any],
    focused_prosecution_metas: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """v3.5 trigger calibrator: ONLY priority 0-1 triggers pass.

    Priority 0 (strongest):
    - quarantined error certificate (focused prosecution returned certificates
      but every round was structurally invalid, v3.5.2: reported through
      ``focused_prosecution_metas``)
    - quarantined defense certificate
    - base-v2 verified_error origin

    Priority 1:
    - unresolved causal link (process_only/unresolved benchmark_causal_status)
    - non-dominating insufficient_evidence defense

    DELETED from v3.4 (priority 2-3):
    - earliest-origin conflict (was priority 2)
    - candidate coverage hole (was priority 2)
    - evidence-authority mismatch (was priority 3)

    This reduces appeal trigger rate from ~83% to ~40%.
    """
    required = _required_candidates(base)
    by_id = {str(row.get("candidate_id") or ""): row for row in required}
    event_order = {str(event["event_id"]): index for index, event in enumerate(built["events"])}
    priorities: dict[str, int] = {}

    def mark(candidate_id: Any, priority: int) -> None:
        key = str(candidate_id or "")
        if key in by_id and priority <= 1:  # v3.5: ONLY priority 0-1
            priorities[key] = min(priority, priorities.get(key, priority))

    # Priority 0: quarantined focused-prosecution certificates.  The merged
    # prosecution only contains validated certificates, so the quarantine
    # signal must come from the per-candidate provider metadata. A
    # fully-quarantined candidate still triggers an appeal: the appeal is the
    # audited re-review surface for quarantined evidence (its consensus-gate
    # fate is decided by the court, which may carry the quarantine-exempt
    # routing in a future pre-ledger restore).
    for candidate_id, meta in (focused_prosecution_metas or {}).items():
        if meta and meta.get("all_quarantined"):
            mark(candidate_id, 0)

    # Priority 0: quarantined error certificates
    errors = (prosecution or {}).get("candidates") or []
    for error, audit in zip(errors, proof.get("error_audits") or []):
        if audit.get("decision") != "pass":
            mark(error.get("source_candidate_id"), 0)
        elif error.get("benchmark_causal_status") in {"process_only", "unresolved"}:
            # process_only / unresolved causal status is structurally absent
            # from the consensus gate's initial-support set (it only counts
            # causes_incorrect_result / propagates_to_incorrect_result), so an
            # appeal certificate for this candidate is always blocked; the
            # trigger would only burn a provider call. Quarantine of the
            # INITIAL certificates is not skipped here: those appeals are the
            # audited re-review surface and stay triggerable.
            continue

    # Priority 0: quarantined defense certificates
    for item, audit in zip((defense or {}).get("candidates") or [], proof.get("defense_audits") or []):
        if audit.get("decision") != "pass":
            mark(item.get("target_candidate_id"), 0)
        elif item.get("defense_type") == "insufficient_evidence":
            mark(item.get("target_candidate_id"), 1)

    # A base-layer responsible event is diagnostic context, not by itself an
    # appeal trigger. Appeal is reserved for an open proof obligation; this
    # avoids paying for a second full court on already terminal candidates.

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
    return [by_id[candidate_id] for candidate_id in ordered[:8]]  # v3.5: max 8 (was 16)


# ============================================================
# H2: Informed Appeal User Messages
# ============================================================

def _appeal_prosecutor_user_v35(
    *, built: dict[str, Any], candidates: list[dict[str, Any]], base: dict[str, Any],
    prosecution: dict[str, Any], defense: dict[str, Any], proof: dict[str, Any],
) -> str:
    """Build appellate prosecutor input (v3.5: same structure, updated prompt)."""
    candidate_ids = {str(row.get("candidate_id") or "") for row in candidates}
    initial_errors = [
        row for row in (prosecution.get("candidates") or [])
        if str(row.get("source_candidate_id") or "") in candidate_ids
    ]
    initial_defenses = [
        row for row in (defense.get("candidates") or [])
        if str(row.get("target_candidate_id") or "") in candidate_ids
    ]
    proof_summary = {
        key: proof.get(key)
        for key in (
            "state", "responsible_event_id", "prosecution_coverage",
            "defense_coverage", "quarantined_error_certificate_ids",
            "quarantined_defense_certificate_ids",
            "surviving_error_certificate_ids", "defeated_error_certificate_ids",
            "consensus_gate_blocked_ids", "decision_authority",
        )
    }
    neutral = {
        "appeal_candidates": candidates,
        "semantic_findings": base.get("qualifications") or [],
        "causal_findings": base.get("propagations") or [],
        "case_boundary_finding": base.get("boundary_path") or {},
        "first_prosecution": {"candidates": initial_errors},
        "first_defense": {"candidates": initial_defenses},
        "first_adjudication": proof_summary,
    }
    value = "TRAJECTORY_PACKET=\n" + packet.canonical_json(built) + "\nAPPEAL_EVIDENCE=\n" + packet.canonical_json(neutral)
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"'):
        if field in value:
            raise ValueError("appeal prosecutor input firewall violation: " + field)
    return value


def _classify_appeal_prosecution_failure(
    *, response: dict[str, Any] | None, allowed_ids: set[str],
    error: Exception | None = None,
) -> str | None:
    """Classify an appeal prosecution failure into a stable category.

    Returns ``None`` when the contract is satisfied. Otherwise returns one of:
      ``missing_coverage_state`` / ``incomplete_coverage_state`` /
      ``missing_reviewed_candidate_ids`` / ``unreviewed_candidate_ids`` /
      ``foreign_candidate_id`` / ``invalid_certificate_schema`` /
      ``response_truncated`` / ``provider_exception`` / ``missing_response``.

    The category is the only case-agnostic signal fed back into the retry
    prompt; raw provider text is never echoed back (avoids prompt poison and
    keeps the retry request body deterministic w.r.t. failure class only).
    """
    if error is not None and response is None:
        return "response_truncated" if "truncat" in str(error).lower() else "provider_exception"
    if response is None:
        return "missing_response"
    coverage = str(response.get("coverage_state") or "")
    if coverage != "complete":
        return "missing_coverage_state" if not coverage else "incomplete_coverage_state"
    reviewed = response.get("reviewed_candidate_ids")
    if not isinstance(reviewed, list) or not reviewed:
        return "missing_reviewed_candidate_ids"
    reviewed_set = {str(item) for item in reviewed}
    if reviewed_set != allowed_ids:
        return "unreviewed_candidate_ids"
    returned_ids = {
        str(row.get("source_candidate_id") or "")
        for row in response.get("candidates") or []
        if isinstance(row, dict)
    }
    if not returned_ids <= allowed_ids:
        return "foreign_candidate_id"
    return None  # contract satisfied


# Stable, case-agnostic mappings from a prosecutor failure category to the
# specific missing field name and the required fix. These feed the retry
# feedback JSON so the model gets a concrete field name (not raw provider
# text) telling it exactly what to repair. ``None`` means the failure is not
# tied to a single field (e.g. truncation / provider exception).
_APPEAL_PROSECUTOR_FAILURE_FIELD: dict[str, str | None] = {
    "missing_coverage_state": "coverage_state",
    "incomplete_coverage_state": "coverage_state",
    "missing_reviewed_candidate_ids": "reviewed_candidate_ids",
    "unreviewed_candidate_ids": "reviewed_candidate_ids",
    "foreign_candidate_id": "candidates[].source_candidate_id",
    "response_truncated": None,
    "provider_exception": None,
    "missing_response": None,
}

_APPEAL_PROSECUTOR_FAILURE_ACTION: dict[str, str] = {
    "missing_coverage_state": 'set coverage_state="complete"',
    "incomplete_coverage_state": 'set coverage_state="complete"',
    "missing_reviewed_candidate_ids": (
        "add reviewed_candidate_ids listing every supplied APPEAL_CANDIDATE id"
    ),
    "unreviewed_candidate_ids": (
        "set reviewed_candidate_ids to exactly the supplied APPEAL_CANDIDATE id set"
    ),
    "foreign_candidate_id": (
        "drop any candidates[].source_candidate_id not in the supplied APPEAL_CANDIDATES"
    ),
    "response_truncated": (
        "emit the JSON object immediately without re-narrating evidence; "
        "candidates may be empty if no prosecution applies"
    ),
    "provider_exception": "re-emit the same structured JSON; do not echo the exception",
}


def _appeal_prosecutor_retry_user_v35(
    *, built: dict[str, Any], candidates: list[dict[str, Any]], base: dict[str, Any],
    prosecution: dict[str, Any], defense: dict[str, Any], proof: dict[str, Any],
    failure_category: str,
) -> str:
    """Build appellate prosecutor retry input with structured failure feedback.

    The retry request body differs from the initial call only by the appended
    APPEAL_RETRY_FEEDBACK block, which is a stable case-agnostic category. This
    guarantees a different cache key while keeping the retry prompt free of
    case-specific provider text (no prompt poison, no evaluator metadata).
    """
    base_user = _appeal_prosecutor_user_v35(
        built=built, candidates=candidates, base=base,
        prosecution=prosecution, defense=defense, proof=proof,
    )
    feedback = packet.canonical_json({
        "failure_category": failure_category,
        "missing_field": _APPEAL_PROSECUTOR_FAILURE_FIELD.get(failure_category),
        "required_action": _APPEAL_PROSECUTOR_FAILURE_ACTION.get(failure_category, (
            "set coverage_state=complete and reviewed_candidate_ids to every "
            "supplied APPEAL_CANDIDATE id; candidates may be empty if no "
            "prosecution applies"
        )),
    })
    value = base_user + "\nAPPEAL_RETRY_FEEDBACK=\n" + feedback
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"'):
        if field in value:
            raise ValueError("appeal prosecutor retry input firewall violation: " + field)
    return value


def _validate_appeal_prosecution_contract_or_retry(
    response: dict[str, Any],
    *,
    allowed_ids: set[str],
    retry_call: Any,
    providers: dict[str, Any],
) -> dict[str, Any]:
    """Validate appeal prosecution contract; on repairable failure do ONE retry.

    Contract (v3.5.3):
      - ``coverage_state`` must be ``"complete"`` (empty ``candidates`` is OK).
      - ``reviewed_candidate_ids`` must be present and equal ``allowed_ids``.
      - Any ``source_candidate_id`` in ``candidates`` must be in ``allowed_ids``.

    On a structurally repairable failure the function invokes ``retry_call``
    exactly once with the classified failure category. The retry uses a
    different request body (retry system + retry user with feedback), so the
    cache key differs from the initial call. If the retry still fails, the
    function raises ``ValueError`` and the caller's except block fail-closes
    via ``_resolve_incomplete_appeal`` — authority is never fabricated.

    ``retry_call`` must return ``(response, meta)``. ``providers`` is mutated
    to record retry audit (parent failure category, retry meta, retry outcome).
    """
    failure_category = _classify_appeal_prosecution_failure(
        response=response, allowed_ids=allowed_ids,
    )
    if failure_category is None:
        return response  # contract already satisfied

    # One bounded retry with a different request body (different cache key).
    retry_response, retry_meta = retry_call(failure_category)
    providers["appeal_prosecutor_retry"] = {
        "parent_failure_category": failure_category,
        "retry_meta": retry_meta,
    }
    retry_failure = _classify_appeal_prosecution_failure(
        response=retry_response, allowed_ids=allowed_ids,
    )
    if retry_failure is None:
        providers["appeal_prosecutor_retry"]["outcome"] = "repaired"
        return retry_response
    providers["appeal_prosecutor_retry"]["outcome"] = "failed_closed"
    providers["appeal_prosecutor_retry"]["retry_failure_category"] = retry_failure
    raise ValueError(
        "required appeal prosecution is incomplete after bounded retry: "
        f"{failure_category} -> {retry_failure}"
    )


# ============================================================
# v3.5.4 — appeal defender symmetric retry
#
# The prosecutor already had ONE bounded retry on a structurally incomplete
# response (missing coverage_state / missing reviewed_candidate_ids / foreign
# candidate id / truncation). The defender used to raise immediately on the
# same defects, so a single truncated defender response fail-closed the whole
# appeal — the asymmetry that produced the trial_5 runtime_invalid cases. The
# defender now gets the same ONE bounded retry with structured case-agnostic
# feedback, restoring symmetry. The fail-closed contract is unchanged: the
# retry only repairs the top-level structural envelope (coverage_state +
# target_candidate_id coverage); per-certificate schema validation in
# ``proof_contract.adjudicate_proof_court`` still raises on malformed
# certificates and the caller fail-closes via ``_resolve_incomplete_appeal``.
# ============================================================


def _classify_appeal_defense_failure(
    *, response: dict[str, Any] | None, allowed_ids: set[str],
    error: Exception | None = None,
) -> str | None:
    """Classify an appeal defense failure into a stable category.

    Mirrors ``_classify_appeal_prosecution_failure`` for the defender schema.
    Returns ``None`` when the contract is satisfied. Otherwise one of:
      ``missing_coverage_state`` / ``incomplete_coverage_state`` /
      ``missing_defense_candidates`` / ``unreviewed_candidate_ids`` /
      ``defense_coverage_mismatch`` / ``response_truncated`` /
      ``provider_exception`` / ``missing_response``.

    The category is the only signal fed back into the retry prompt (raw
    provider text is never echoed back).
    """
    if error is not None and response is None:
        return "response_truncated" if "truncat" in str(error).lower() else "provider_exception"
    if response is None:
        return "missing_response"
    coverage = str(response.get("coverage_state") or "")
    if coverage != "complete":
        return "missing_coverage_state" if not coverage else "incomplete_coverage_state"
    rows = response.get("candidates")
    if not isinstance(rows, list) or not rows:
        return "missing_defense_candidates"
    returned_ids = {
        str(row.get("target_candidate_id") or "")
        for row in rows
        if isinstance(row, dict)
    }
    if not returned_ids:
        return "missing_defense_candidates"
    if returned_ids != allowed_ids:
        return "defense_coverage_mismatch"
    return None  # contract satisfied


_APPEAL_DEFENDER_FAILURE_FIELD: dict[str, str | None] = {
    "missing_coverage_state": "coverage_state",
    "incomplete_coverage_state": "coverage_state",
    "missing_defense_candidates": "candidates",
    "unreviewed_candidate_ids": "candidates[].target_candidate_id",
    "defense_coverage_mismatch": "candidates[].target_candidate_id",
    "response_truncated": None,
    "provider_exception": None,
    "missing_response": None,
}

_APPEAL_DEFENDER_FAILURE_ACTION: dict[str, str] = {
    "missing_coverage_state": 'set coverage_state="complete"',
    "incomplete_coverage_state": 'set coverage_state="complete"',
    "missing_defense_candidates": (
        "emit a candidates array with one defense certificate per supplied "
        "APPEAL_CANDIDATE id; insufficient_evidence is a valid defense"
    ),
    "unreviewed_candidate_ids": (
        "ensure every supplied APPEAL_CANDIDATE id appears exactly once as a "
        "target_candidate_id"
    ),
    "defense_coverage_mismatch": (
        "set candidates[].target_candidate_id to exactly the supplied "
        "APPEAL_CANDIDATE id set (no missing, no foreign ids)"
    ),
    "response_truncated": (
        "emit the JSON object immediately without re-narrating evidence; "
        "an insufficient_evidence certificate per candidate is acceptable"
    ),
    "provider_exception": "re-emit the same structured JSON; do not echo the exception",
}


def _appeal_defender_retry_user_v35(
    *, built: dict[str, Any], candidates: list[dict[str, Any]], base: dict[str, Any],
    prosecution: dict[str, Any], defense: dict[str, Any], proof: dict[str, Any],
    appeal_prosecution: dict[str, Any] | None,
    failure_category: str,
) -> str:
    """Build appellate defender retry input with structured failure feedback.

    Mirrors ``_appeal_prosecutor_retry_user_v35``. The retry request body
    differs from the initial call only by the appended APPEAL_RETRY_FEEDBACK
    block, so the cache key changes (no stale hit) without leaking raw
    provider text.
    """
    base_user = _appeal_defender_user_v35(
        built=built, candidates=candidates, base=base,
        prosecution=prosecution, defense=defense, proof=proof,
        appeal_prosecution=appeal_prosecution,
    )
    feedback = packet.canonical_json({
        "failure_category": failure_category,
        "missing_field": _APPEAL_DEFENDER_FAILURE_FIELD.get(failure_category),
        "required_action": _APPEAL_DEFENDER_FAILURE_ACTION.get(failure_category, (
            "set coverage_state=complete and return one defense certificate "
            "per supplied APPEAL_CANDIDATE id"
        )),
    })
    value = base_user + "\nAPPEAL_RETRY_FEEDBACK=\n" + feedback
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"'):
        if field in value:
            raise ValueError("appeal defender retry input firewall violation: " + field)
    return value


def _validate_appeal_defense_contract_or_retry(
    response: dict[str, Any],
    *,
    allowed_ids: set[str],
    retry_call: Any,
    providers: dict[str, Any],
) -> dict[str, Any]:
    """Validate appeal defense contract; on repairable failure do ONE retry.

    Mirrors ``_validate_appeal_prosecution_contract_or_retry``. Contract:
      - ``coverage_state`` must be ``"complete"``.
      - ``candidates`` must be a non-empty list.
      - ``candidates[].target_candidate_id`` must equal ``allowed_ids``.

    On a structurally repairable failure the function invokes ``retry_call``
    exactly once with the classified failure category. If the retry still
    fails, the function raises ``ValueError`` and the caller's except block
    fail-closes via ``_resolve_incomplete_appeal`` — authority is never
    fabricated. The deeper per-certificate schema check stays in
    ``proof_contract.adjudicate_proof_court`` (unchanged) so a structurally
    complete but schema-invalid defense still fail-closes there.
    """
    failure_category = _classify_appeal_defense_failure(
        response=response, allowed_ids=allowed_ids,
    )
    if failure_category is None:
        return response  # contract already satisfied

    # One bounded retry with a different request body (different cache key).
    retry_response, retry_meta = retry_call(failure_category)
    providers["appeal_defender_retry"] = {
        "parent_failure_category": failure_category,
        "retry_meta": retry_meta,
    }
    retry_failure = _classify_appeal_defense_failure(
        response=retry_response, allowed_ids=allowed_ids,
    )
    if retry_failure is None:
        providers["appeal_defender_retry"]["outcome"] = "repaired"
        return retry_response
    providers["appeal_defender_retry"]["outcome"] = "failed_closed"
    providers["appeal_defender_retry"]["retry_failure_category"] = retry_failure
    raise ValueError(
        "required appeal defense is incomplete after bounded retry: "
        f"{failure_category} -> {retry_failure}"
    )


def _deep_validate_appeal_defense(
    response: dict[str, Any],
    *,
    built: dict[str, Any],
    required_candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the same deep validator the court uses on appeal defense rows.

    Returns ``(valid_rows, invalid_audits)``. Invalid audits carry the
    concrete ``failed_checks`` list, which the current top-level contract
    retry never sees. This is the diagnostic precursor to a bounded
    certificate-level repair.
    """
    candidate_origins: dict[str, str] = {}
    for row in required_candidates:
        candidate_id = str(row.get("candidate_id") or "")
        origin = str(row.get("origin_hypothesis_event_id") or "")
        if candidate_id:
            candidate_origins[candidate_id] = origin
    rows = response.get("candidates") or []
    if not isinstance(rows, list):
        return [], [{"failed_checks": ["candidates_not_list"]}]
    valid_rows: list[dict[str, Any]] = []
    invalid_audits: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            invalid_audits.append({"failed_checks": ["certificate_not_object"]})
            continue
        audit = contract.validate_defense_certificate(
            row, built, candidate_origins,
        )
        if audit.get("decision") == "pass":
            valid_rows.append(dict(row))
        else:
            invalid_audits.append({
                "target_candidate_id": str(row.get("target_candidate_id") or ""),
                "failed_checks": audit.get("failed_checks") or [],
            })
    return valid_rows, invalid_audits


def _appeal_defender_deep_repair_user_v35(
    *,
    built: dict[str, Any],
    candidates: list[dict[str, Any]],
    base: dict[str, Any],
    prosecution: dict[str, Any],
    defense: dict[str, Any],
    proof: dict[str, Any],
    appeal_prosecution: dict[str, Any] | None,
    invalid_audits: Sequence[Mapping[str, Any]],
) -> str:
    """Build a compact deep-repair prompt for invalid appeal defense certs.

    Only stable failed-check categories and the affected candidate's required
    origin/addressable refs are fed back; raw provider text is never echoed.
    The repair asks for replacement certificates ONLY for invalid candidates;
    every certificate must still pass the unmodified deep validator.
    """
    base_user = _appeal_defender_user_v35(
        built=built, candidates=candidates, base=base,
        prosecution=prosecution, defense=defense, proof=proof,
        appeal_prosecution=appeal_prosecution,
    )
    event_ids = [str(event["event_id"]) for event in built["events"]]
    repair_hints = []
    for audit in invalid_audits:
        candidate_id = str(audit.get("target_candidate_id") or "")
        candidate = next(
            (c for c in candidates if str(c.get("candidate_id") or "") == candidate_id),
            {},
        )
        origin = str(candidate.get("origin_hypothesis_event_id") or "")
        hint = {
            "target_candidate_id": candidate_id,
            "required_origin_event_id": origin,
            "failed_checks": audit.get("failed_checks") or [],
            "allowed_defense_types": contract.DEFENSE_TYPES,
            "addressable_event_ids": event_ids,
        }
        repair_hints.append(hint)
    feedback = packet.canonical_json({
        "repair_required": "one replacement defense certificate per listed "
        "candidate",
        "candidate_repairs": repair_hints,
        "hard_requirements": (
            "coverage_state must remain complete; target_candidate_id must "
            "match the supplied id; target_origin_event_id must equal the "
            "required_origin_event_id; evidence_refs must be non-empty and "
            "copied from addressable_event_ids; defense_type must be one of "
            "allowed_defense_types; rationale must be non-empty."
        ),
    })
    value = base_user + "\nAPPEAL_DEFENSE_DEEP_REPAIR=\n" + feedback
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"'):
        if field in value:
            raise ValueError("appeal defender deep repair input firewall violation: " + field)
    return value


def _validate_appeal_defense_deep_or_repair(
    response: dict[str, Any],
    *,
    built: dict[str, Any],
    candidates: list[dict[str, Any]],
    base: dict[str, Any],
    prosecution: dict[str, Any],
    defense: dict[str, Any],
    proof: dict[str, Any],
    appeal_prosecution: dict[str, Any] | None,
    required_candidates: list[dict[str, Any]],
    retry_call: Any,
    providers: dict[str, Any],
) -> dict[str, Any]:
    """Validate appeal defense at the certificate level; on invalid certs do ONE
    bounded deep repair.

    The final deep validator (``adjudicate_proof_court``) is unchanged and
    remains authoritative. This repair only gives the defender a second,
    compact, validator-driven chance to fix malformed certificates for the
    specific failed candidates. If the repair is not called or still fails,
    the response is returned unchanged and the court fail-closes as before.
    """
    valid_rows, invalid_audits = _deep_validate_appeal_defense(
        response, built=built, required_candidates=required_candidates,
    )
    if not invalid_audits:
        providers.setdefault("appeal_defender_deep", {})["status"] = "complete"
        return response
    providers["appeal_defender_deep"] = {
        "status": "repaired_attempted",
        "invalid_candidate_count": len(invalid_audits),
    }
    try:
        repair_response, repair_meta = retry_call(invalid_audits)
    except Exception as exc:
        providers["appeal_defender_deep"]["outcome"] = "repair_provider_exception"
        providers["appeal_defender_deep"]["error"] = str(exc)[:200]
        return response
    providers["appeal_defender_deep"]["repair_meta"] = repair_meta
    repair_rows, repair_audits = _deep_validate_appeal_defense(
        repair_response, built=built, required_candidates=required_candidates,
    )
    if not repair_audits:
        providers["appeal_defender_deep"]["outcome"] = "repaired"
        # The repair prompt asks for replacement certificates ONLY for the
        # invalid candidates, so returning the repair response alone would
        # silently drop the valid defenses that survived the first deep
        # validation (weakening the defense pool and risking a flipped
        # clean verdict). Merge the repaired certificates with the original
        # valid rows; on target overlap the repaired certificate wins.
        merged_rows = list(valid_rows)
        repaired_targets = {
            str(row.get("target_candidate_id") or "")
            for row in repair_rows
            if str(row.get("target_candidate_id") or "")
        }
        merged_rows = [
            row for row in merged_rows
            if str(row.get("target_candidate_id") or "") not in repaired_targets
        ]
        merged_rows.extend(repair_rows)
        merged = dict(response)
        merged["candidates"] = merged_rows
        providers["appeal_defender_deep"]["merged_valid_row_count"] = (
            len(valid_rows)
        )
        providers["appeal_defender_deep"]["merged_repair_row_count"] = (
            len(repair_rows)
        )
        return merged
    providers["appeal_defender_deep"]["outcome"] = "still_invalid"
    providers["appeal_defender_deep"]["remaining_failed_checks"] = [
        a.get("failed_checks") for a in repair_audits
    ]
    return response


def _appeal_defender_user_v35(
    *, built: dict[str, Any], candidates: list[dict[str, Any]], base: dict[str, Any],
    prosecution: dict[str, Any], defense: dict[str, Any], proof: dict[str, Any],
    appeal_prosecution: dict[str, Any] | None = None,
) -> str:
    """Build appellate defender input (v3.5: includes prosecution certificates)."""
    candidate_ids = {str(row.get("candidate_id") or "") for row in candidates}
    initial_errors = [
        row for row in (prosecution.get("candidates") or [])
        if str(row.get("source_candidate_id") or "") in candidate_ids
    ]
    initial_defenses = [
        row for row in (defense.get("candidates") or [])
        if str(row.get("target_candidate_id") or "") in candidate_ids
    ]
    proof_summary = {
        key: proof.get(key)
        for key in (
            "state", "responsible_event_id", "prosecution_coverage",
            "defense_coverage", "quarantined_error_certificate_ids",
            "quarantined_defense_certificate_ids",
            "surviving_error_certificate_ids", "defeated_error_certificate_ids",
            "consensus_gate_blocked_ids", "decision_authority",
        )
    }
    neutral = {
        "appeal_candidates": candidates,
        "semantic_findings": base.get("qualifications") or [],
        "causal_findings": base.get("propagations") or [],
        "case_boundary_finding": base.get("boundary_path") or {},
        "first_prosecution": {"candidates": initial_errors},
        "first_defense": {"candidates": initial_defenses},
        "first_adjudication": proof_summary,
        # v3.5 H2: Informed defender sees the appeal prosecution certificates
        "prosecution_certificates": appeal_prosecution,
    }
    value = "TRAJECTORY_PACKET=\n" + packet.canonical_json(built) + "\nAPPEAL_EVIDENCE=\n" + packet.canonical_json(neutral)
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"'):
        if field in value:
            raise ValueError("appeal defender input firewall violation: " + field)
    return value


# ============================================================
# H3: Counterfactual Localization
# ============================================================

def _counterfactual_localization_user(
    *, built: dict[str, Any], surviving_errors: list[dict[str, Any]],
) -> str:
    """Build the counterfactual localization input."""
    origins = [
        {
            "origin_event_id": str(e.get("origin_event_id") or ""),
            "breach_type": str(e.get("breach_type") or ""),
            "impact_kind": str(e.get("impact_kind") or ""),
            "rationale": str(e.get("rationale") or "")[:500],
        }
        for e in surviving_errors
    ]
    value = (
        "TRAJECTORY_PACKET=\n" + packet.canonical_json(built)
        + "\nSURVIVING_ORIGINS=\n" + packet.canonical_json({"origins": origins})
    )
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"'):
        if field in value:
            raise ValueError("counterfactual localization input firewall violation: " + field)
    return value


def _run_counterfactual_localization(
    *, built: dict[str, Any], surviving_errors: list[dict[str, Any]],
    config: dict[str, Any], output_dir: Path,
) -> dict[str, dict[str, Any]] | None:
    """Run pairwise counterfactual comparison for multiple surviving origins.

    Only called when there are 2+ surviving errors with DIFFERENT origins.
    Returns rankings dict or None if not applicable.
    """
    # Only run if there are multiple distinct origins
    distinct_origins = list(dict.fromkeys(
        str(e.get("origin_event_id") or "") for e in surviving_errors
    ))
    if len(distinct_origins) < 2:
        return None

    user = _counterfactual_localization_user(built=built, surviving_errors=surviving_errors)
    try:
        # H3 counterfactual localization returns {"rankings": {...}} (no
        # "candidates" field), so use the schema-relaxed provider call.
        value, _meta = json_object_provider_call(
            route=config.get("counterfactual_route", config["focused_prosecution_route"]),
            system=COUNTERFACTUAL_LOCALIZATION_SYSTEM,
            user=user, cache_dir=output_dir / "counterfactual_cache",
        )
        rankings = value.get("rankings")
        if (
            isinstance(rankings, dict)
            and set(map(str, rankings)) == set(distinct_origins)
        ):
            return rankings
    except Exception:
        pass
    return None


def _initial_error_has_local_authority(proof: dict[str, Any]) -> bool:
    """Return whether an initial positive is complete without appeal evidence."""
    surviving = proof.get("surviving_error_certificate_ids") or []
    winner = str(proof.get("winning_error_certificate_id") or "")
    return bool(
        proof.get("state") == "verified_error"
        and proof.get("decision_authority") == "authoritative"
        and proof.get("prosecution_coverage") == "complete"
        and proof.get("defense_coverage") == "complete"
        and not (proof.get("quarantined_error_certificate_ids") or [])
        and not (proof.get("quarantined_defense_certificate_ids") or [])
        and int(proof.get("resolved_candidate_count") or 0)
        == int(proof.get("required_candidate_count") or 0)
        and winner
        and winner in surviving
    )


def _surface_runtime_failure_reasons(
    *,
    proof_reasons: Sequence[Mapping[str, Any]],
    base: Mapping[str, Any],
    construct_authoritative: bool,
    construct_authority_reasons: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project base-layer and construct failures into authority failure reasons.

    v5.8 observability fix: a ``runtime_valid=False`` run must always carry a
    machine-readable cause.  Base-layer provider errors, census fallback
    failures, base certificate quarantines and non-authoritative construct
    views previously invalidated a run without leaving any top-level reason
    entry.  This projection is diagnostic only: it never changes a verdict,
    an authority scope or any fail-closed behaviour.
    """
    reasons: list[dict[str, Any]] = [
        dict(row) for row in (proof_reasons or []) if isinstance(row, Mapping)
    ]
    providers = base.get("providers") if isinstance(base, Mapping) else None
    if isinstance(providers, Mapping):
        for key in sorted(providers, key=str):
            meta = providers.get(key)
            if not str(key).endswith("_error") or not isinstance(meta, Mapping):
                continue
            reasons.append({
                "component": "base_layer",
                "code": str(key),
                "error_type": str(meta.get("error_type") or ""),
                "error": str(meta.get("error") or "")[:500],
            })
    census_audit = base.get("census_audit") if isinstance(base, Mapping) else None
    if (
        isinstance(census_audit, Mapping)
        and str(census_audit.get("status") or "") == "fallback_failed"
    ):
        reasons.append({
            "component": "base_layer",
            "code": "census_fallback_failed",
            "error_type": str(census_audit.get("error_type") or ""),
            "error": str(census_audit.get("error") or "")[:500],
        })
    candidate_path = (
        base.get("candidate_path") if isinstance(base, Mapping) else None
    )
    layer_audits = (
        candidate_path.get("layer_audits")
        if isinstance(candidate_path, Mapping) else None
    )
    quarantined: list[dict[str, Any]] = []
    if isinstance(layer_audits, Mapping):
        for candidate_id in sorted(layer_audits, key=str):
            audits = layer_audits.get(candidate_id)
            if not isinstance(audits, Mapping):
                continue
            for stage in sorted(audits, key=str):
                audit = audits.get(stage)
                if (
                    not isinstance(audit, Mapping)
                    or audit.get("decision") != "quarantine"
                ):
                    continue
                failed = list(audit.get("failed_checks") or [])
                if failed:
                    quarantined.append({
                        "candidate_id": str(candidate_id),
                        "stage": str(stage),
                        "failed_checks": [str(item) for item in failed],
                    })
    if quarantined:
        reasons.append({
            "component": "base_layer",
            "code": "certificate_quarantine",
            "quarantined": quarantined,
        })
    if not construct_authoritative:
        for row in construct_authority_reasons or []:
            if isinstance(row, Mapping):
                reasons.append(dict(row))
    if not reasons:
        reasons.append({
            "component": "runtime",
            "code": "unknown_runtime_invalidation",
        })
    return reasons


def _resolve_incomplete_appeal(
    initial_proof: dict[str, Any], exc: Exception,
) -> dict[str, Any]:
    """Preserve only a complete initial positive when appeal infrastructure fails."""
    failure = {
        "component": "appeal",
        "code": "required_appeal_incomplete",
        "error_type": type(exc).__name__,
        "error": str(exc)[:500],
    }
    if _initial_error_has_local_authority(initial_proof):
        return {
            **initial_proof,
            "authority_failure_reasons": [],
            "appeal_incomplete": True,
            "appeal_status": "incomplete_initial_error_preserved",
            "verdict_authority_scope": "initial_local_positive",
            "non_authoritative_appeal_diagnostic": failure,
        }
    return {
        **initial_proof,
        "state": "unresolved",
        "responsible_event_id": None,
        "impact_status": None,
        "winning_impact_kind": None,
        "winning_error_certificate_id": None,
        "surviving_error_certificate_ids": [],
        # An incomplete appeal means the evidence chain could not be closed;
        # that is the unresolved verdict, not a runtime failure.  The invalid
        # authority escape hatch leaked a fourth state into consumers (the
        # evolution loop skips invalid cases entirely) for what is a normal
        # evidence-insufficient outcome.
        "decision_authority": "authoritative",
        "authority_failure_reasons": [failure],
        "appeal_incomplete": True,
        "appeal_status": "incomplete_fail_closed_unresolved",
        "verdict_authority_scope": "unresolved_appeal_incomplete",
    }


# ============================================================
# Main adjudication pipeline
# ============================================================

def adjudicate_case(
    *, case_id: str, record: dict[str, Any], config: dict[str, Any],
    output_dir: Path, census_root: Path | None = None,
    policy_items: Sequence[Mapping[str, Any]] | None = None,
    policy_registry=None,
) -> dict[str, Any]:
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
            # v3.5.6: one bounded census-repair call. A structurally incomplete
            # or schema-invalid census response is repairable under the same
            # coverage contract (coverage_state="complete" + every supplied
            # candidate returned). The retry uses a different request body so
            # the cache key differs, and never weakens the fail-closed
            # construct authority: a still-incomplete retry keeps the same
            # non-authoritative outcome.
            if construct_audit.get("status") != "complete":
                # Structured repair feedback: the generic "must be complete"
                # instruction cannot repair a candidate whose dependency
                # references a nonexistent event id — the model does not know
                # WHICH candidate is invalid or WHAT the legal references
                # are.  Feed the concrete validation failures and the legal
                # event-id universe back so the repair can actually fix the
                # references (observed: BFCL/143 failed 3x with
                # construct_not_complete until the failures were surfaced).
                repair_failures = construct_audit.get("failures") or []
                legal_event_ids = [
                    str(event["event_id"]) for event in built["events"]
                ]
                repair_hint = (
                    "\nCONSTRUCT_CENSUS_REPAIR=coverage_state must be "
                    "\"complete\" and every supplied candidate must pass "
                    "validation. Validation failures to fix: "
                    + json.dumps(repair_failures, ensure_ascii=False)
                    + ". Legal event IDs for origin_hypothesis_event_id: "
                    + json.dumps(legal_event_ids, ensure_ascii=False)
                    + ". Legal trigger_event_id and dependency_event_ids "
                    "(event IDs or tool interaction IDs copied exactly from "
                    "TRAJECTORY_PACKET): "
                    + json.dumps(_addressable_refs(built), ensure_ascii=False)
                    + ". Re-emit the FULL census with the repaired candidates "
                    "(same candidate_ids). Return JSON only."
                )
                repair_user = v2._provider_user(built) + repair_hint
                repair_value, repair_provider = provider_call(
                    route=config["construct_census_route"], system=CONSTRUCT_CENSUS_SYSTEM,
                    user=repair_user, cache_dir=output_dir / "construct_census_cache" / "repair_1",
                )
                construct_provider = {"initial": construct_provider, "repair": repair_provider}
                construct_value, axis_normalized_count = _normalize_construct_value(repair_value)
                construct_census, construct_audit = validate_provider_census(repair_value, built)
                construct_audit["axis_normalized_count"] = axis_normalized_count
                construct_audit["repaired_after_incomplete"] = True
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

    # Phase 2: H1 — Focused Prosecution (per-candidate, parallel) + Defense
    appeal_prosecution = None
    appeal_defense_certs = None
    combined_prosecution = None
    appeal_candidates: list[dict[str, Any]] = []
    initial_proof = None
    focused_audits: list[dict[str, Any]] = []
    counterfactual_rankings = None
    prosecution, defense = None, None

    try:
        # Run focused prosecution and defense concurrently
        with ThreadPoolExecutor(max_workers=2) as executor:
            focused_pros_future = executor.submit(
                _run_focused_prosecution,
                built=built, base=base, config=config, output_dir=output_dir,
            )
            defense_future = executor.submit(
                provider_call, route=config["defense_route"], system=DEFENSE_SYSTEM,
                user=user, cache_dir=output_dir / "defense_cache",
            )
            focused_prosecution, focused_audits, focused_metas = focused_pros_future.result()
            defense, providers["defense"] = defense_future.result()

        providers["focused_prosecution"] = focused_metas
        prosecution = focused_prosecution  # v3.5: focused prosecution IS the initial prosecution

        # Initial adjudication with v3.5 contract. The kwargs dict is kept
        # as a literal exactly once: the appeal re-adjudication, the
        # counterfactual re-adjudication and the L2 policy re-adjudication
        # all derive from it, so no court call can silently drift onto a
        # different evidence bundle.
        initial_court_kwargs = {
            "required_candidates": _required_candidates(base),
            "prosecution": prosecution,
            "defense": defense,
            "packet": built,
            "apply_materiality_check": True,
            "apply_commitment_check": True,
            "apply_consensus_gate_flag": False,  # No consensus gate on initial
            "focused_prosecution_audits": focused_audits,
        }
        initial_proof = contract.adjudicate_proof_court(**initial_court_kwargs)
        # The base court verdict is locally authoritative: the appeal stage
        # augments it with independent verification, it does not re-validate
        # it.  Marking it here makes the complete-initial-positive
        # preservation in _resolve_incomplete_appeal reachable when the
        # appeal infrastructure fails mid-way.
        initial_proof["decision_authority"] = "authoritative"
        proof = initial_proof
        # Tracks the exact evidence bundle of whichever court call produced
        # the current proof, so the L2 policy re-adjudication replays the
        # identical inputs at the convergence point.
        final_court_kwargs = dict(initial_court_kwargs)
        # The appeal evidence bundle defaults to the base prosecution and an
        # EMPTY COMPLETE defense so the localization stage can run even when
        # the appeal stage was skipped or failed mid-way (a preserved initial
        # verdict still localizes from base evidence).  The court contract
        # raises ValueError on any non-complete appeal defense, so an
        # "incomplete" stub would crash the H3 re-adjudication below instead
        # of degrading gracefully; an empty complete defense is a legal
        # evidence bundle that contributes no certificates.
        combined_prosecution = prosecution
        appeal_defense_certs = {"coverage_state": "complete", "candidates": []}

        # H2: Tightened trigger calibrator (priority 0-1 only)
        appeal_candidates = _appeal_trigger_calibrator_v35(
            base=base, proof=initial_proof, prosecution=prosecution, defense=defense, built=built,
            focused_prosecution_metas=focused_metas,
        )

        if appeal_candidates:
            allowed_ids = {str(row.get("candidate_id") or "") for row in appeal_candidates}
            try:
                # H2: Appeal prosecution first; the informed defender needs to
                # see the appeal prosecution certificates.
                appeal_user = _appeal_prosecutor_user_v35(
                    built=built, candidates=appeal_candidates, base=base,
                    prosecution=prosecution, defense=defense, proof=initial_proof,
                )
                appeal_prosecution, providers["appeal_prosecutor"] = provider_call(
                    route=config["appeal_prosecutor_route"],
                    system=APPEAL_PROSECUTOR_SYSTEM_V35, user=appeal_user,
                    cache_dir=output_dir / "appeal_prosecutor_cache",
                )
                # v3.5.3: Validate the reviewed_candidate_ids contract. A
                # structurally repairable failure (missing coverage state,
                # missing/unreviewed ids, foreign candidate) triggers ONE
                # bounded retry with a different request body — different cache
                # key, structured case-agnostic feedback, no prompt poison.
                appeal_prosecution = _validate_appeal_prosecution_contract_or_retry(
                    appeal_prosecution,
                    allowed_ids=allowed_ids,
                    retry_call=lambda failure_category: provider_call(
                        route=config["appeal_prosecutor_route"],
                        system=APPEAL_PROSECUTOR_RETRY_SYSTEM_V35,
                        user=_appeal_prosecutor_retry_user_v35(
                            built=built, candidates=appeal_candidates, base=base,
                            prosecution=prosecution, defense=defense, proof=initial_proof,
                            failure_category=failure_category,
                        ),
                        cache_dir=output_dir / "appeal_prosecutor_cache" / "retry_1",
                    ),
                    providers=providers,
                )

                # H2: Informed defender (sees prosecution certificates)
                appeal_defender_user = _appeal_defender_user_v35(
                    built=built, candidates=appeal_candidates, base=base,
                    prosecution=prosecution, defense=defense, proof=initial_proof,
                    appeal_prosecution=appeal_prosecution,
                )
                appeal_defense_certs, providers["appeal_defender"] = provider_call(
                    route=config["appeal_defender_route"],
                    system=APPEAL_DEFENDER_SYSTEM_V35, user=appeal_defender_user,
                    cache_dir=output_dir / "appeal_defender_cache",
                )
                # v3.5.4: Validate the defender coverage contract with ONE
                # bounded retry, symmetric with the prosecutor above. A
                # structurally repairable failure (missing coverage_state,
                # missing/unreviewed target_candidate_ids, truncation)
                # triggers ONE retry with a different request body (different
                # cache key, structured case-agnostic feedback). The deeper
                # per-certificate schema check stays in adjudicate_proof_court
                # so a structurally complete but schema-invalid defense still
                # fail-closes there — the retry never weakens the contract.
                appeal_defense_certs = _validate_appeal_defense_contract_or_retry(
                    appeal_defense_certs,
                    allowed_ids=allowed_ids,
                    retry_call=lambda failure_category: provider_call(
                        route=config["appeal_defender_route"],
                        system=APPEAL_DEFENDER_RETRY_SYSTEM_V35,
                        user=_appeal_defender_retry_user_v35(
                            built=built, candidates=appeal_candidates, base=base,
                            prosecution=prosecution, defense=defense, proof=initial_proof,
                            appeal_prosecution=appeal_prosecution,
                            failure_category=failure_category,
                        ),
                        cache_dir=output_dir / "appeal_defender_cache" / "retry_1",
                    ),
                    providers=providers,
                )

                # v5.6: deep certificate-level defense validation with ONE
                # bounded compact repair. The top-level contract retry above
                # cannot see per-certificate schema failures (a structurally
                # complete defense may still contain an invalid target_origin
                # or evidence_ref). Give the defender one validator-driven
                # repair for the specific failed candidates; the final court
                # validator is unchanged and remains fail-closed.
                appeal_defense_certs = _validate_appeal_defense_deep_or_repair(
                    appeal_defense_certs,
                    built=built,
                    candidates=appeal_candidates,
                    base=base,
                    prosecution=prosecution,
                    defense=defense,
                    proof=initial_proof,
                    appeal_prosecution=appeal_prosecution,
                    required_candidates=_required_candidates(base),
                    retry_call=lambda invalid_audits: provider_call(
                        route=config["appeal_defender_route"],
                        system=APPEAL_DEFENDER_RETRY_SYSTEM_V35,
                        user=_appeal_defender_deep_repair_user_v35(
                            built=built, candidates=appeal_candidates, base=base,
                            prosecution=prosecution, defense=defense, proof=initial_proof,
                            appeal_prosecution=appeal_prosecution,
                            invalid_audits=invalid_audits,
                        ),
                        cache_dir=output_dir / "appeal_defender_cache" / "deep_repair_1",
                    ),
                    providers=providers,
                )

                # Merge prosecutions
                combined_prosecution = _merge_prosecutions(
                    prosecution, appeal_prosecution, built, allowed_ids,
                )

                # v3.5: adjudicate with consensus gate + appeal defense
                appeal_court_kwargs = {
                    **initial_court_kwargs,
                    "prosecution": combined_prosecution,
                    "appeal_defense": appeal_defense_certs,
                    "initial_prosecution": prosecution,  # H2: for consensus gate
                    "apply_consensus_gate_flag": True,   # H2: enable consensus gate
                }
                proof = contract.adjudicate_proof_court(**appeal_court_kwargs)
                final_court_kwargs = dict(appeal_court_kwargs)
            except Exception as exc:
                providers["appeal_error"] = {"error_type": type(exc).__name__, "error": str(exc)[:500]}
                proof = _resolve_incomplete_appeal(initial_proof, exc)

        # H3: Two-Phase Localization (only if multiple surviving origins)
        surviving_ids = proof.get("surviving_error_certificate_ids") or []
        counterfactual_rankings = None
        if (
            proof.get("state") == "verified_error"
            and len(surviving_ids) >= 2
            and "appeal_error" not in providers
        ):
            # Get the surviving error certificates for counterfactual comparison
            all_certs = (combined_prosecution or prosecution or {}).get("candidates") or []
            surviving_errors = [
                c for c in all_certs
                if str(c.get("certificate_id") or "") in surviving_ids
            ]
            if len(surviving_errors) >= 2:
                counterfactual_rankings = _run_counterfactual_localization(
                    built=built, surviving_errors=surviving_errors,
                    config=config, output_dir=output_dir,
                )
                if counterfactual_rankings:
                    # Re-adjudicate with counterfactual rankings. The H3
                    # re-adjudication always runs the consensus-gate court
                    # shape, even when the appeal stage itself never
                    # executed (initial proof with multiple survivors), so
                    # the gate shape is applied unconditionally here.
                    counterfactual_court_kwargs = {
                        **final_court_kwargs,
                        "prosecution": combined_prosecution or prosecution,
                        "initial_prosecution": prosecution,
                        "appeal_defense": appeal_defense_certs,
                        "apply_consensus_gate_flag": True,
                        "counterfactual_rankings": counterfactual_rankings,
                    }
                    proof = contract.adjudicate_proof_court(
                        **counterfactual_court_kwargs,
                    )
                    final_court_kwargs = counterfactual_court_kwargs

        # L2 adjudication-policy passthrough: policies act on the terminal
        # edge only, after every appeal/localization stage has settled. The
        # re-adjudication replays the exact evidence bundle that produced
        # the current proof, so the deterministic policy evaluator sees the
        # same facts the court committed to. Empty policy input leaves the
        # default path untouched.
        if policy_items:
            from ..bccse.v6_contracts import PolicyItem as _PolicyItem
            from ..bccse.vocabulary_registry import (
                L2_REGISTRY,
                POLICY_STAGE_PRE_LEDGER,
            )

            normalized_policy_items = [
                item if isinstance(item, _PolicyItem)
                else _PolicyItem.from_dict(dict(item))
                for item in policy_items
            ]
            # P9 stage routing: the registry is the single authority on which
            # surface an action executes on. Pre-ledger items act before the
            # occurrence snapshot; post-ledger items act on the terminal edge
            # only. An action the registry does not know resolves to
            # post-ledger, where the DSL whitelist rejects it fail-closed, so
            # an unknown action can never reach the pre-ledger surface.
            _stage_registry = policy_registry or L2_REGISTRY
            pre_ledger_items = [
                item for item in normalized_policy_items
                if _stage_registry.policy_stage_of(item.action)
                == POLICY_STAGE_PRE_LEDGER
            ]
            post_ledger_items = [
                item for item in normalized_policy_items
                if _stage_registry.policy_stage_of(item.action)
                != POLICY_STAGE_PRE_LEDGER
            ]
            boundary_path = base.get("boundary_path") or {}
            boundary_validation = boundary_path.get("contract_validation") or {}
            proof = contract.adjudicate_proof_court(
                **final_court_kwargs,
                policy_items=post_ledger_items,
                pre_ledger_policy_items=pre_ledger_items or None,
                policy_registry=policy_registry,
                boundary_path_state=str(boundary_path.get("state") or ""),
                # Authority is valid only when the deterministic boundary
                # path passed its own contract validation (decision=pass);
                # a quarantined or exception-fallback boundary stays
                # non-authoritative, so consumption fails closed.
                boundary_authority_valid=(
                    boundary_validation.get("decision") == "pass"
                ),
            )

        proof, construct_authoritative, construct_authority_reasons = (
            _apply_construct_authority(proof, construct_audit)
        )
        if proof.get("state") == "verified_error":
            surviving = proof.get("surviving_error_certificate_ids") or []
            proof["localization_status"] = (
                "complete" if counterfactual_rankings else
                "not_applicable" if (
                    len(surviving) < 2 or "appeal_error" in providers
                ) else
                "failed_temporal_fallback"
            )
        else:
            proof["localization_status"] = "not_applicable"
        runtime_valid = (
            _proof_runtime_valid(base_runtime_valid, proof)
            and construct_authoritative
            and (
                "appeal_error" not in providers
                or proof.get("verdict_authority_scope") in {
                    "initial_local_positive",
                    "unresolved_appeal_incomplete",
                }
            )
        )
    except Exception as exc:
        proof = {
            "state": "unresolved", "responsible_event_id": None, "impact_status": None,
            "winning_impact_kind": None, "error_type": type(exc).__name__, "error": str(exc)[:500],
            "quarantined_error_certificate_ids": [], "quarantined_defense_certificate_ids": [],
            "consensus_gate_blocked_ids": [], "consensus_gate_applied": False,
            "counterfactual_rankings_used": False,
            "required_candidate_count": 0,
            "resolved_candidate_count": 0,
            "wide_construct_state": "unresolved",
            "surviving_error_certificate_ids": [],
            "defense_type_counts": {},
            "error_audits": [],
            "defense_audits": [],
            "synthetic_defense_count": 0,
            "evidence_authority_routes": {},
            "prosecution_coverage": "unresolved",
            "defense_coverage": "unresolved",
            "winning_error_certificate_id": None,
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
    failure_reasons = proof.get("authority_failure_reasons") or []
    if not runtime_valid:
        # v5.8 observability: every invalidated run carries a machine-readable
        # cause, including base-layer and construct failures that previously
        # left the top-level reason list empty. Diagnostic only.
        failure_reasons = _surface_runtime_failure_reasons(
            proof_reasons=failure_reasons,
            base=base,
            construct_authoritative=construct_authoritative,
            construct_authority_reasons=construct_authority_reasons,
        )
    output = dict(base)
    output.update({
        "runtime_valid": runtime_valid,
        "state": proof["state"],
        "decision_authority": proof.get("decision_authority", "invalid"),
        "authority_failure_reasons": failure_reasons,
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
        # The pairwise origin rankings produced by the counterfactual
        # localization stage.  Purely additive telemetry: the court already
        # consumed them (``counterfactual_rankings_used``), and surfacing
        # them here makes the causal-link-test execution surface
        # deterministically verifiable for the D1 postcondition audit.
        "counterfactual_rankings": counterfactual_rankings,
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
                "incomplete_initial_error_preserved" if (
                    proof.get("verdict_authority_scope") == "initial_local_positive"
                ) else
                "complete" if (
                    appeal_prosecution is not None
                    and appeal_defense_certs is not None
                    and "appeal_error" not in providers
                ) else "provider_error"
            ),
            # v3.5 diagnostics
            "focused_prosecution_candidate_count": len(_required_candidates(base)),
            "focused_prosecution_certificate_count": len((prosecution or {}).get("candidates") or []),
            "focused_prosecution_audit_count": len(focused_audits),
            # v3.5.2: candidates whose focused certificates were all quarantined
            # (open structural concern → forced incomplete coverage + appealable)
            "focused_prosecution_quarantined_candidate_count": sum(
                1 for meta in (providers.get("focused_prosecution") or {}).values()
                if isinstance(meta, dict) and meta.get("all_quarantined")
            ),
            # v3.5.1-D3: distinguish LLM-empty (explicit no-error) from LLM-crash (provider error)
            "focused_prosecution_explicit_no_error_count": sum(
                1 for meta in (providers.get("focused_prosecution") or {}).values()
                if isinstance(meta, dict) and "round1" in meta
                and not any(k.endswith("_error") for k in meta)
            ),
            "focused_prosecution_provider_error_count": sum(
                1 for meta in (providers.get("focused_prosecution") or {}).values()
                if isinstance(meta, dict) and (
                    any(k.endswith("_error") for k in meta) or "error_type" in meta
                )
            ),
            "focused_chain_completeness": proof.get("focused_prosecution_chain_completeness"),
            "consensus_gate_applied": proof.get("consensus_gate_applied", False),
            "consensus_gate_blocked_count": len(proof.get("consensus_gate_blocked_ids") or []),
            "counterfactual_rankings_used": proof.get("counterfactual_rankings_used", False),
            "localization_status": proof.get("localization_status", "not_applicable"),
            "appeal_triggered": bool(appeal_candidates),
            "materiality_hits": proof.get("synthetic_defense_count") or 0,
            "evidence_authority_routes": proof.get("evidence_authority_routes") or {},
        },
    })
    return output


# ============================================================
# Evaluation and gate (preserved from v3.4 with v3.5 extensions)
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
    # v3.5 counters
    focused_certs_total = focused_audits_total = 0
    consensus_blocked_total = 0
    counterfactual_used_count = 0
    chain_completeness_scores: list[float] = []

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
        # v3.5
        focused_certs_total += int(health.get("focused_prosecution_certificate_count") or 0)
        focused_audits_total += int(health.get("focused_prosecution_audit_count") or 0)
        consensus_blocked_total += int(health.get("consensus_gate_blocked_count") or 0)
        counterfactual_used_count += int(bool(health.get("counterfactual_rankings_used")))
        completeness = health.get("focused_chain_completeness")
        if isinstance(completeness, list):
            chain_completeness_scores.extend(completeness)

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
        "materiality_defense_hits": materiality_hits_total,
        # v3.5 diagnostics
        "focused_prosecution_certificate_count": focused_certs_total,
        "focused_prosecution_audit_count": focused_audits_total,
        "consensus_gate_blocked_count": consensus_blocked_total,
        "counterfactual_localization_used_count": counterfactual_used_count,
        "mean_chain_completeness": (
            sum(chain_completeness_scores) / len(chain_completeness_scores)
            if chain_completeness_scores else 0.0
        ),
    })
    return metrics
