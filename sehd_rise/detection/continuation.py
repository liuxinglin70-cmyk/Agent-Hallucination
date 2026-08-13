"""Constructive recall harness over an already-computed proof-court artifact.

The harness binds one deterministic ClaimAnchor, materializes one neutral
candidate, and runs only the frozen downstream qualification, propagation,
focused verification, defense, and proof-court recomputation.  It never reruns
candidate census and never lets a Skill author evidence, a certificate, or a
verdict.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from sehd_rise.detection.court import contract
from sehd_rise.detection.court.engine import (
    DEFENSE_SYSTEM,
    PROPAGATION_SYSTEM,
    QUALIFICATION_SYSTEM,
    _coverage,
    _proof_user,
    _provider_user,
    _run_focused_prosecution_for_candidate,
)
from sehd_rise.detection.egress import detection_call
from sehd_rise.detection.packet import build_packet
from sehd_rise.detection.transport import _responsible_step
from sehd_rise.platform.usage import ProviderUsageLedger
from sehd_rise.skills.anchors import ClaimAnchor, project_claim_anchors
from sehd_rise.skills.action import ActionSpec, sanitize_action_spec
from sehd_rise.skills.binding import BINDING_BOUND, match_condition_bindings


CONSTRUCTION_COMPLETED = "CONSTRUCTION_COMPLETED"
CONSTRUCTION_NO_CANDIDATE = "CONSTRUCTION_NO_CANDIDATE"
CONSTRUCTION_ANCHOR_MISMATCH = "CONSTRUCTION_ANCHOR_MISMATCH"
CONSTRUCTED_CANDIDATE_DUPLICATE = "CONSTRUCTED_CANDIDATE_DUPLICATE"
CONSTRUCTED_CANDIDATE_NOT_QUALIFIED = "CONSTRUCTED_CANDIDATE_NOT_QUALIFIED"
CONSTRUCTED_CANDIDATE_NOT_VERIFIED = "CONSTRUCTED_CANDIDATE_NOT_VERIFIED"
CONSTRUCTED_CANDIDATE_NO_ERROR_CERTIFICATE = (
    "CONSTRUCTED_CANDIDATE_NO_ERROR_CERTIFICATE"
)
EXECUTED_CONSTRUCTIVE_OPERATIONS = frozenset({"construct_candidate"})


@dataclass(frozen=True)
class ConstructiveExecutionProof:
    source_anchor_id: str
    constructor_invoked: bool
    constructed_candidate_id: str | None
    candidate_anchor_id: str | None
    qualification_result: str
    verifier_invoked: bool
    verifier_result: str
    certificate_ids_created: tuple[str, ...]
    terminal_before: str
    terminal_after: str
    causal_edge_changed: bool
    status: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["certificate_ids_created"] = list(self.certificate_ids_created)
        return payload


def _stable_id(prefix: str, value: Any) -> str:
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return prefix + hashlib.sha256(data).hexdigest()[:16]


def _candidate_from_anchor(anchor: ClaimAnchor) -> dict[str, Any]:
    """Frozen neutral constructor: copy observable span facts, no verdict."""
    return {
        "candidate_id": _stable_id("skill_candidate_", anchor.anchor_id),
        "claim_anchor_id": anchor.anchor_id,
        "origin_hypothesis_event_id": anchor.source_event_id,
        "trigger_event_id": anchor.source_event_id,
        "axes": ["P" if anchor.observable_features.get("anchor_depends_on_tool") else "F"],
        "candidate_kind": "claim_anchor_review",
        "claim_or_commitment": anchor.span_text,
        "dependency_event_ids": list(anchor.upstream_ref_ids),
        "module_tag": (
            "Tool-Use" if anchor.observable_features.get("anchor_depends_on_tool")
            else "Factuality"
        ),
        "observation": "deterministic claim anchor selected for frozen verification",
        "is_decision_point": True,
    }


def _existing_candidates(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row) for row in result.get("census") or ()
        if isinstance(row, Mapping)
    ]


def _duplicate(candidate: Mapping[str, Any], existing: Sequence[Mapping[str, Any]]) -> bool:
    anchor_id = str(candidate.get("claim_anchor_id") or "")
    origin = str(candidate.get("origin_hypothesis_event_id") or "")
    claim = " ".join(str(candidate.get("claim_or_commitment") or "").split())
    return any(
        str(row.get("claim_anchor_id") or "") == anchor_id
        or (
            str(row.get("origin_hypothesis_event_id") or "") == origin
            and " ".join(str(row.get("claim_or_commitment") or "").split()) == claim
        )
        for row in existing
    )


def _proof(
    *, anchor_id: str, before: str, after: str | None = None,
    candidate: Mapping[str, Any] | None = None, status: str,
    qualification: str = "not_run", verifier_invoked: bool = False,
    verifier_result: str = "not_run", certificate_ids: Sequence[str] = (),
) -> ConstructiveExecutionProof:
    candidate_id = str((candidate or {}).get("candidate_id") or "") or None
    candidate_anchor = str((candidate or {}).get("claim_anchor_id") or "") or None
    final = after if after is not None else before
    return ConstructiveExecutionProof(
        source_anchor_id=anchor_id,
        constructor_invoked=candidate is not None,
        constructed_candidate_id=candidate_id,
        candidate_anchor_id=candidate_anchor,
        qualification_result=qualification,
        verifier_invoked=verifier_invoked,
        verifier_result=verifier_result,
        certificate_ids_created=tuple(str(item) for item in certificate_ids),
        terminal_before=before,
        terminal_after=final,
        causal_edge_changed=bool(before != final and final == "verified_error"),
        status=status,
    )


def resume_court_from_candidate(
    baseline_result: Mapping[str, Any], *, candidate: Mapping[str, Any],
    record: Mapping[str, Any], config: Mapping[str, Any],
    provider_usage_ledger: ProviderUsageLedger | None = None,
) -> tuple[dict[str, Any], ConstructiveExecutionProof]:
    """Run only the frozen downstream path for one constructed candidate."""
    result = copy.deepcopy(dict(baseline_result))
    before = str(result.get("state") or "")
    candidate = dict(candidate)
    anchor_id = str(candidate.get("claim_anchor_id") or "")
    if not candidate.get("candidate_id"):
        return result, _proof(
            anchor_id=anchor_id, before=before, candidate=candidate,
            status=CONSTRUCTION_NO_CANDIDATE,
        )
    if not anchor_id:
        return result, _proof(
            anchor_id=anchor_id, before=before, candidate=candidate,
            status=CONSTRUCTION_ANCHOR_MISMATCH,
        )
    existing = _existing_candidates(result)
    if _duplicate(candidate, existing):
        return result, _proof(
            anchor_id=anchor_id, before=before, candidate=candidate,
            status=CONSTRUCTED_CANDIDATE_DUPLICATE,
        )

    built = build_packet(dict(record))
    candidate_id = str(candidate["candidate_id"])
    result["census"] = [*existing, candidate]
    result["skill_constructed_candidates"] = [
        *[
            dict(row) for row in result.get("skill_constructed_candidates") or ()
            if isinstance(row, Mapping)
        ],
        candidate,
    ]

    q_value, q_meta = detection_call(
        route=dict(config["qualification_route"]),
        system=QUALIFICATION_SYSTEM,
        user=_provider_user(built, census=[candidate]),
        purpose="detector:qualification",
        ledger=provider_usage_ledger,
    )
    q_coverage, qualifications = _coverage(q_value, {candidate_id})
    qualification = next((
        dict(row) for row in qualifications
        if str(row.get("candidate_id") or "") == candidate_id
    ), {})
    q_audit = contract.validate_qualification(qualification, built)
    q_state = contract.qualification_state(qualification) if qualification else "missing"
    result["qualifications"] = [
        *[
            dict(row) for row in result.get("qualifications") or ()
            if isinstance(row, Mapping)
        ],
        *([qualification] if qualification else []),
    ]
    providers = dict(result.get("providers") or {})
    providers.setdefault("constructive_continuation", {})[candidate_id] = {
        "qualification": q_meta,
    }
    if q_coverage != "complete" or q_audit.get("decision") != "pass" or q_state != "qualified":
        result["providers"] = providers
        return result, _proof(
            anchor_id=anchor_id, before=before, candidate=candidate,
            qualification=q_state,
            status=CONSTRUCTED_CANDIDATE_NOT_QUALIFIED,
        )

    p_value, p_meta = detection_call(
        route=dict(config["propagation_route"]),
        system=PROPAGATION_SYSTEM,
        user=_provider_user(built, census=[candidate], qualification=[qualification]),
        purpose="detector:propagation",
        ledger=provider_usage_ledger,
    )
    p_coverage, propagations = _coverage(p_value, {candidate_id})
    propagation = next((
        dict(row) for row in propagations
        if str(row.get("candidate_id") or "") == candidate_id
    ), {})
    p_audit = contract.validate_propagation(
        propagation,
        str(candidate.get("origin_hypothesis_event_id") or ""),
        built,
    )
    result["propagations"] = [
        *[
            dict(row) for row in result.get("propagations") or ()
            if isinstance(row, Mapping)
        ],
        *([propagation] if propagation else []),
    ]
    providers["constructive_continuation"][candidate_id]["propagation"] = p_meta
    if p_coverage != "complete" or p_audit.get("decision") != "pass":
        result["providers"] = providers
        return result, _proof(
            anchor_id=anchor_id, before=before, candidate=candidate,
            qualification=q_state, verifier_invoked=True,
            verifier_result="propagation_invalid",
            status=CONSTRUCTED_CANDIDATE_NOT_VERIFIED,
        )

    certs, audits, verifier_meta = _run_focused_prosecution_for_candidate(
        candidate=candidate, built=built, base=result, config=dict(config),
        provider_usage_ledger=provider_usage_ledger,
    )
    providers["constructive_continuation"][candidate_id]["focused_prosecution"] = verifier_meta
    if str(verifier_meta.get("terminal_status") or "") not in {"complete", "no_error"}:
        result["providers"] = providers
        return result, _proof(
            anchor_id=anchor_id, before=before, candidate=candidate,
            qualification=q_state, verifier_invoked=True,
            verifier_result=str(verifier_meta.get("terminal_status") or "invalid"),
            status=CONSTRUCTED_CANDIDATE_NOT_VERIFIED,
        )
    if not certs:
        result["providers"] = providers
        return result, _proof(
            anchor_id=anchor_id, before=before, candidate=candidate,
            qualification=q_state, verifier_invoked=True,
            verifier_result="verified_non_error", certificate_ids=(),
            status=CONSTRUCTED_CANDIDATE_NO_ERROR_CERTIFICATE,
        )

    defense_value, defense_meta = detection_call(
        route=dict(config["defense_route"]), system=DEFENSE_SYSTEM,
        user=_proof_user(built, result),
        purpose="detector:defense", ledger=provider_usage_ledger,
    )
    providers["constructive_continuation"][candidate_id]["defense"] = defense_meta
    existing_prosecution = result.get("combined_prosecution") or result.get("prosecution") or {}
    existing_errors = [
        dict(row) for row in existing_prosecution.get("candidates") or ()
        if isinstance(row, Mapping)
    ] if isinstance(existing_prosecution, Mapping) else []
    existing_defense = result.get("defense") or {}
    existing_defenses = [
        dict(row) for row in existing_defense.get("candidates") or ()
        if isinstance(row, Mapping)
    ] if isinstance(existing_defense, Mapping) else []
    new_defenses = [
        dict(row) for row in defense_value.get("candidates") or ()
        if isinstance(row, Mapping)
    ] if isinstance(defense_value, Mapping) else []
    prosecution = {"coverage_state": "complete", "candidates": [*existing_errors, *certs]}
    defense = {"coverage_state": "complete", "candidates": [*existing_defenses, *new_defenses]}
    proof = contract.adjudicate_proof_court(
        required_candidates=[*existing, candidate],
        prosecution=prosecution,
        defense=defense,
        packet=built,
        apply_materiality_check=True,
        apply_commitment_check=True,
        apply_consensus_gate_flag=False,
        focused_prosecution_audits=[
            *[
                dict(row) for row in (result.get("proof_adjudication") or {}).get("error_audits") or ()
                if isinstance(row, Mapping)
            ],
            *audits,
        ],
    )
    proof["decision_authority"] = "authoritative"
    occurrence_event_id = proof.get("occurrence_responsible_event_id")
    result.update({
        "state": proof["state"],
        "decision_authority": "authoritative",
        "incident_state": {
            "verified_error": "verified_hallucination",
            "verified_clean": "verified_non_hallucination",
            "unresolved": "unresolved",
        }[proof["state"]],
        "responsible_event_id": proof.get("responsible_event_id"),
        "responsible_step": _responsible_step(
            proof.get("responsible_event_id"), built,
        ),
        "occurrence_state": proof.get("occurrence_state", "unresolved"),
        "incident_occurred": proof.get("incident_occurred"),
        "occurrence_responsible_event_id": occurrence_event_id,
        "occurrence_responsible_step": _responsible_step(
            occurrence_event_id, built,
        ),
        "occurrence_impact_status": proof.get("occurrence_impact_status"),
        "occurrence_impact_kind": proof.get("occurrence_impact_kind"),
        "occurrence_recovery_state": proof.get("occurrence_recovery_state"),
        "occurrence_ledger": proof.get("occurrence_ledger"),
        "impact_status": proof.get("impact_status"),
        "impact_kind": proof.get("winning_impact_kind"),
        "wide_construct_state": proof.get("wide_construct_state"),
        "proof_adjudication": proof,
        "prosecution": prosecution,
        "combined_prosecution": prosecution,
        "defense": defense,
        "providers": providers,
    })
    certificate_ids = tuple(str(cert.get("certificate_id") or "") for cert in certs)
    return result, _proof(
        anchor_id=anchor_id, before=before, after=str(proof.get("state") or before),
        candidate=candidate, qualification=q_state, verifier_invoked=True,
        verifier_result="error_certificate_created",
        certificate_ids=certificate_ids, status=CONSTRUCTION_COMPLETED,
    )


def execute_constructive_rules(
    result: Mapping[str, Any],
    rules: Sequence[tuple[str, str, Mapping[str, Any] | None, Mapping[str, Any] | None, str]],
    *, record: Mapping[str, Any], config: Mapping[str, Any],
    provider_usage_ledger: ProviderUsageLedger | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ground and execute constructive rules in deterministic genome order."""
    current = copy.deepcopy(dict(result))
    fired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    bound: list[dict[str, Any]] = []
    proofs: list[dict[str, Any]] = []
    anchors = {anchor.anchor_id: anchor for anchor in project_claim_anchors(record)}
    for module_id, rule_id, condition_spec, action_value, binding_entity_id in rules:
        spec_value = sanitize_action_spec(action_value)
        if not spec_value:
            continue
        spec = ActionSpec.from_dict(spec_value)
        if spec.operation != "construct_candidate":
            continue
        match = match_condition_bindings(
            condition_spec, current, domain=spec.input_binding_domain,
            cardinality=spec.cardinality, record=record,
            preferred_entity_id=str(binding_entity_id or ""),
        )
        row = {
            "module_id": module_id, "rule_id": rule_id,
            "operation": spec.operation, "postcondition": spec.postcondition,
            "matched": bool(match.bindings), "binding_status": match.status,
            "binding_proof": match.to_dict(),
        }
        if not match.bound:
            row["fired"] = False
            row["detail"] = match.reason
            skipped.append(row)
            continue
        bound.append(dict(row))
        binding = match.bindings[0]
        anchor = anchors.get(binding.entity_id)
        if anchor is None:
            proof = _proof(
                anchor_id=binding.entity_id,
                before=str(current.get("state") or ""),
                status=CONSTRUCTION_ANCHOR_MISMATCH,
            )
        else:
            candidate = _candidate_from_anchor(anchor)
            current, proof = resume_court_from_candidate(
                current, candidate=candidate, record=record, config=config,
                provider_usage_ledger=provider_usage_ledger,
            )
        proof_dict = proof.to_dict()
        proofs.append(proof_dict)
        row.update({
            "fired": proof.constructor_invoked and proof.status not in {
                CONSTRUCTION_NO_CANDIDATE, CONSTRUCTION_ANCHOR_MISMATCH,
                CONSTRUCTED_CANDIDATE_DUPLICATE,
            },
            "construction_status": proof.status,
            "constructive_execution_proof": proof_dict,
            "detail": proof.status.lower(),
        })
        (fired if row["fired"] else skipped).append(row)
    return current, {
        "fired": fired, "bound": bound, "skipped": skipped,
        "constructive_execution_proofs": proofs,
        "terminal_state": str(current.get("state") or ""),
    }


__all__ = [
    "CONSTRUCTED_CANDIDATE_DUPLICATE",
    "CONSTRUCTED_CANDIDATE_NO_ERROR_CERTIFICATE",
    "CONSTRUCTED_CANDIDATE_NOT_QUALIFIED",
    "CONSTRUCTED_CANDIDATE_NOT_VERIFIED",
    "CONSTRUCTION_ANCHOR_MISMATCH", "CONSTRUCTION_COMPLETED",
    "CONSTRUCTION_NO_CANDIDATE", "ConstructiveExecutionProof",
    "EXECUTED_CONSTRUCTIVE_OPERATIONS",
    "execute_constructive_rules", "resume_court_from_candidate",
]
