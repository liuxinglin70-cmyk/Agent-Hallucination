"""Frozen route replay for route-guidance interventions (design D2 / F1).

A route-guidance skill has no deterministic backend: its intervention is a
firewall-checked guidance block on the owner's producer route.  To attribute a
terminal or observable change to that guidance rather than to provider
re-sampling, the replay freezes every upstream provider artifact and re-runs
only the owner's producer route plus its deterministic downstream, then
recomputes the proof court (which is not a provider route and always recomputes
the terminal).

``replay_route_guidance_focused`` with an empty ``guidance_blocks`` is the
unguided control: the frozen scope and the re-run routes are identical, so a
guided-vs-control delta is causal evidence — the only variable is the guidance
text.  This is the entity the runner's D3 re-confirmation previously could not
provide (it compared two guided stochastic re-runs instead of a control).

The observable postcondition fingerprints are the drift-tolerant signatures the
attribution layer reads after a replay; each is a stable content hash, so a
provider re-minting positional ids never reads as movement.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping, Sequence

from sehd_rise.detection.court import contract
from sehd_rise.detection.court.engine import (
    BOUNDARY_SYSTEM,
    CONSTRUCT_CENSUS_SYSTEM,
    DEFENSE_SYSTEM,
    PROPAGATION_SYSTEM,
    QUALIFICATION_SYSTEM,
    _coverage,
    _deterministic_process_census,
    _guidance_suffix,
    _normalize_construct_value,
    _proof_user,
    _provider_user,
    _required_candidates,
    _run_focused_prosecution,
    _union_census,
    _validate_boundary,
)
from sehd_rise.detection.court.identity import certificate_claim_fingerprint
from sehd_rise.detection.egress import detection_call
from sehd_rise.detection.packet import build_packet
from sehd_rise.detection.transport import (
    _addressable_refs,
    _responsible_step,
    validate_provider_census,
)
from sehd_rise.platform.usage import ProviderUsageLedger
from sehd_rise.skills.capabilities import OWNER_PRODUCER_ROUTE

# Producer-route -> owner(s) whose guidance injects into that route.
_FOCUSED_PROSECUTION_OWNERS = (
    "atomic_verifier_instruction", "evidence_obligation",
)


def _stable_tokens(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def route_observable_fingerprints(result: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the RouteAddressability observable postconditions from a result.

    These are the drift-tolerant signatures the attribution layer compares
    across a baseline and a frozen replay.  A provider re-minting positional
    ids does not move any content fingerprint.
    """
    census = [
        dict(row) for row in result.get("census") or ()
        if isinstance(row, Mapping)
    ]
    census_fingerprints = tuple(
        _stable_tokens({
            "candidate_id": row.get("candidate_id"),
            "origin": row.get("origin_hypothesis_event_id"),
            "kind": row.get("candidate_kind"),
            "claim": row.get("claim_or_commitment"),
        })
        for row in census
    )

    qualifications = [
        dict(row) for row in result.get("qualifications") or ()
        if isinstance(row, Mapping)
    ]
    qualification_states = tuple(sorted(
        (
            str(row.get("candidate_id") or ""),
            str(contract.qualification_state(row) if row else "missing"),
        )
        for row in qualifications
    ))

    propagations = [
        dict(row) for row in result.get("propagations") or ()
        if isinstance(row, Mapping)
    ]
    propagation_edges = tuple(sorted(
        (
            str(row.get("candidate_id") or ""),
            str(row.get("propagation_state") or ""),
            tuple(sorted(str(item) for item in (row.get("consumption_event_ids") or []))),
        )
        for row in propagations
    ))

    cert_fingerprints: list[str] = []
    for key in ("combined_prosecution", "prosecution", "defense"):
        payload = result.get(key)
        if not isinstance(payload, Mapping):
            continue
        for cert in payload.get("candidates") or []:
            if not isinstance(cert, Mapping):
                continue
            fingerprint = certificate_claim_fingerprint(cert)
            cert_fingerprints.append(
                fingerprint or f"id:{str(cert.get('certificate_id') or '')}"
            )
    certificate_audit_fingerprints = tuple(sorted(cert_fingerprints))

    boundary = result.get("boundary_path") or {}
    return {
        "census_candidate_fingerprints": census_fingerprints,
        "candidate_qualification_states": qualification_states,
        "propagation_dependency_edges": propagation_edges,
        "certificate_audit_fingerprints": certificate_audit_fingerprints,
        "boundary_path_state": str(boundary.get("state") or ""),
    }


def _construct_census_with_guidance(
    built: dict[str, Any], config: Mapping[str, Any],
    guidance: Mapping[str, tuple[str, ...]],
    provider_usage_ledger: ProviderUsageLedger | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Re-run the construct census route with guidance, mirroring the engine."""
    try:
        construct_value, construct_provider = detection_call(
            route=config["construct_census_route"],
            system=CONSTRUCT_CENSUS_SYSTEM + _guidance_suffix(
                "construct_census_route", guidance,
            ),
            user=_provider_user(built),
            purpose="detector:construct_census", ledger=provider_usage_ledger,
        )
        construct_value, axis_normalized_count = _normalize_construct_value(
            construct_value,
        )
        construct_census, construct_audit = validate_provider_census(
            construct_value, built,
        )
        construct_audit["axis_normalized_count"] = axis_normalized_count
        if construct_audit.get("status") != "complete":
            repair_hint = (
                "\nCONSTRUCT_CENSUS_REPAIR=coverage_state must be \"complete\" "
                "and every supplied candidate must pass validation. "
                "Validation failures to fix: "
                + json.dumps(construct_audit.get("failures") or [], ensure_ascii=False)
                + ". Legal event IDs for origin_hypothesis_event_id: "
                + json.dumps([str(e["event_id"]) for e in built["events"]], ensure_ascii=False)
                + ". Legal trigger_event_id and dependency_event_ids: "
                + json.dumps(_addressable_refs(built), ensure_ascii=False)
                + ". Re-emit the FULL census with repaired candidates."
            )
            repair_value, repair_provider = detection_call(
                route=config["construct_census_route"],
                system=CONSTRUCT_CENSUS_SYSTEM + _guidance_suffix(
                    "construct_census_route", guidance,
                ),
                user=_provider_user(built) + repair_hint,
                purpose="detector:construct_census", ledger=provider_usage_ledger,
            )
            construct_provider = {
                "initial": construct_provider, "repair": repair_provider,
            }
            construct_value, axis_normalized_count = _normalize_construct_value(
                repair_value,
            )
            construct_census, construct_audit = validate_provider_census(
                repair_value, built,
            )
            construct_audit["axis_normalized_count"] = axis_normalized_count
            construct_audit["repaired_after_incomplete"] = True
    except Exception as exc:
        construct_census = []
        construct_audit = {
            "status": "provider_error", "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }
        construct_provider = {
            "error_type": type(exc).__name__, "error": str(exc)[:500],
        }
    return construct_census, construct_audit, construct_provider


def _recompute_proof_and_assemble(
    result: dict[str, Any], built: dict[str, Any],
    prosecution: Mapping[str, Any], defense: Mapping[str, Any],
    focused_audits: Sequence[Mapping[str, Any]],
    providers: dict[str, Any],
    base_runtime_valid: bool,
    construct_authoritative: bool,
) -> dict[str, Any]:
    """Recompute the deterministic proof court and re-flatten the result."""
    proof = contract.adjudicate_proof_court(
        required_candidates=_required_candidates(result),
        prosecution=prosecution,
        defense=defense,
        packet=built,
        apply_materiality_check=True,
        apply_commitment_check=True,
        apply_consensus_gate_flag=False,
        focused_prosecution_audits=[
            dict(row) for row in focused_audits if isinstance(row, Mapping)
        ],
    )
    proof["decision_authority"] = "authoritative"
    if proof.get("state") == "verified_error":
        surviving = proof.get("surviving_error_certificate_ids") or []
        proof["localization_status"] = (
            "not_applicable" if len(surviving) < 2 else "failed_temporal_fallback"
        )
    else:
        proof["localization_status"] = "not_applicable"
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
        "occurrence_responsible_event_id": proof.get(
            "occurrence_responsible_event_id",
        ),
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
        "proof_adjudication": proof,
        "prosecution": dict(prosecution),
        "combined_prosecution": dict(prosecution),
        "defense": dict(defense),
        "providers": providers,
        "runtime_valid": base_runtime_valid and construct_authoritative and (
            proof.get("state") in {"verified_error", "verified_clean", "unresolved"}
        ),
    })
    return result


def replay_route_guidance_focused(
    baseline_result: Mapping[str, Any],
    *,
    owner: str,
    guidance_blocks: Sequence[str],
    record: Mapping[str, Any],
    config: Mapping[str, Any],
    provider_usage_ledger: ProviderUsageLedger | None = None,
) -> dict[str, Any]:
    """Frozen route replay focused on one owner's producer route.

    ``guidance_blocks`` is the firewall-checked guidance text to inject into the
    owner's producer route.  An empty sequence is the unguided control: the same
    frozen scope and re-run routes with no injected guidance, so a guided-vs-
    control delta is attributable to the guidance alone.

    The returned result carries ``route_guidance_injection`` (which blocks were
    injected, keyed by content fingerprint) and ``route_observable_fingerprints``
    for the attribution layer.
    """
    producer_route = OWNER_PRODUCER_ROUTE.get(owner, "")
    if not producer_route:
        raise ValueError(f"route-guidance owner has no producer route: {owner}")

    result = copy.deepcopy(dict(baseline_result))
    built = build_packet(dict(record))
    providers = dict(result.get("providers") or {})
    base_runtime_valid = bool(result.get("runtime_valid"))
    blocks = tuple(str(block) for block in guidance_blocks if str(block).strip())
    guidance: dict[str, tuple[str, ...]] = (
        {producer_route: blocks} if blocks else {}
    )
    injection: dict[str, Any] = {
        "owner": owner,
        "producer_route": producer_route,
        "injected": bool(blocks),
        "blocks": [
            {
                "fingerprint": _stable_tokens(block),
                "preview": block[:120],
            }
            for block in blocks
        ],
    }

    focused_audits: list[dict[str, Any]] = [
        dict(row) for row in (
            (result.get("proof_adjudication") or {}).get("error_audits") or ()
        )
        if isinstance(row, Mapping)
    ]
    prosecution = result.get("prosecution") or {"coverage_state": "complete", "candidates": []}
    defense = result.get("defense") or {"coverage_state": "complete", "candidates": []}
    construct_authoritative = True

    if owner in _FOCUSED_PROSECUTION_OWNERS:
        # Re-run focused prosecution (with guidance) and defense (downstream).
        prosecution, focused_audits, focused_metas = _run_focused_prosecution(
            built=built, base=result, config=dict(config),
            provider_usage_ledger=provider_usage_ledger,
            route_guidance=guidance,
        )
        providers["focused_prosecution"] = focused_metas
        defense_value, defense_meta = detection_call(
            route=dict(config["defense_route"]), system=DEFENSE_SYSTEM,
            user=_proof_user(built, result),
            purpose="detector:defense", ledger=provider_usage_ledger,
        )
        providers["defense"] = defense_meta
        defense = defense_value if isinstance(defense_value, Mapping) else {}

    elif owner == "candidate_trigger":
        # Re-run construct census (with guidance), recompute the union census,
        # then re-run focused prosecution + defense.
        construct_census, construct_audit, construct_provider = (
            _construct_census_with_guidance(
                built, dict(config), guidance, provider_usage_ledger,
            )
        )
        providers["construct_census"] = construct_provider
        construct_authoritative = (
            str(construct_audit.get("status") or "") == "complete"
            and str(construct_audit.get("coverage_state") or "") == "complete"
        )
        result["construct_census"] = construct_census
        result["construct_census_audit"] = construct_audit
        result["census"] = _union_census(
            list(result.get("paca_census") or []),
            construct_census,
            _deterministic_process_census(built),
        )
        prosecution, focused_audits, focused_metas = _run_focused_prosecution(
            built=built, base=result, config=dict(config),
            provider_usage_ledger=provider_usage_ledger, route_guidance=None,
        )
        providers["focused_prosecution"] = focused_metas
        defense_value, defense_meta = detection_call(
            route=dict(config["defense_route"]), system=DEFENSE_SYSTEM,
            user=_proof_user(built, result),
            purpose="detector:defense", ledger=provider_usage_ledger,
        )
        providers["defense"] = defense_meta
        defense = defense_value if isinstance(defense_value, Mapping) else {}

    elif owner == "earliest_origin_tiebreak":
        # Re-run the boundary route (with guidance); prosecution/defense frozen.
        boundary_value, boundary_meta = detection_call(
            route=dict(config["boundary_route"]),
            system=BOUNDARY_SYSTEM + _guidance_suffix("boundary_route", guidance),
            user=_provider_user(built),
            purpose="detector:boundary", ledger=provider_usage_ledger,
        )
        providers["boundary"] = boundary_meta
        result["boundary_path"] = _validate_boundary(boundary_value, built)

    elif owner == "candidate_to_verifier_route":
        # Re-run qualification (with guidance) then propagation (downstream).
        census = [dict(row) for row in result.get("paca_census") or ()]
        q_value, q_meta = detection_call(
            route=dict(config["qualification_route"]),
            system=QUALIFICATION_SYSTEM + _guidance_suffix(
                "qualification_route", guidance,
            ),
            user=_provider_user(built, census=census),
            purpose="detector:qualification", ledger=provider_usage_ledger,
        )
        providers["qualification"] = q_meta
        _q_coverage, qualifications = _coverage(
            q_value, {str(row.get("candidate_id") or "") for row in census},
        )
        result["qualifications"] = qualifications
        p_value, p_meta = detection_call(
            route=dict(config["propagation_route"]),
            system=PROPAGATION_SYSTEM,
            user=_provider_user(built, census=census, qualification=qualifications),
            purpose="detector:propagation", ledger=provider_usage_ledger,
        )
        providers["propagation"] = p_meta
        _p_coverage, propagations = _coverage(
            p_value, {str(row.get("candidate_id") or "") for row in census},
        )
        result["propagations"] = propagations

    elif owner == "causal_link_test":
        # Re-run propagation (with guidance); qualification frozen.
        census = [dict(row) for row in result.get("paca_census") or ()]
        qualifications = [
            dict(row) for row in result.get("qualifications") or ()
        ]
        p_value, p_meta = detection_call(
            route=dict(config["propagation_route"]),
            system=PROPAGATION_SYSTEM + _guidance_suffix(
                "propagation_route", guidance,
            ),
            user=_provider_user(built, census=census, qualification=qualifications),
            purpose="detector:propagation", ledger=provider_usage_ledger,
        )
        providers["propagation"] = p_meta
        _p_coverage, propagations = _coverage(
            p_value, {str(row.get("candidate_id") or "") for row in census},
        )
        result["propagations"] = propagations
    else:
        raise ValueError(f"unhandled route-guidance owner: {owner}")

    result = _recompute_proof_and_assemble(
        result, built, prosecution, defense, focused_audits, providers,
        base_runtime_valid=base_runtime_valid,
        construct_authoritative=construct_authoritative,
    )
    result["route_guidance_injection"] = injection
    result["route_observable_fingerprints"] = route_observable_fingerprints(result)
    return result


__all__ = [
    "replay_route_guidance_focused",
    "route_observable_fingerprints",
]
