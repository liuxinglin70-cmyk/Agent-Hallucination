"""The minimal proof court: census -> verification -> defense -> verdict.

Three provider stages and one deterministic adjudication:

1. ``census`` — discover the assistant's specific factual-error candidates.
2. ``verification`` — test each candidate ONLY against cited trajectory
   evidence; emit one certificate per candidate (supported / rejected).
3. ``defense`` — adversarial review of every supported certificate
   (upheld / overturned). A supported certificate that was never reviewed
   is a structural hole: the court fails closed instead of convicting.

The verdict is deterministic, closed-vocabulary aggregation of the
provider outputs — no LLM decides the terminal state. Any provider or
contract failure makes the whole run ``runtime_valid=False`` and the
verdict ``unresolved``.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from sehd_rise2.detector.packet import render_packet
from sehd_rise2.detector.provider import ProviderError, provider_call

CENSUS_SYSTEM = (
    "You are the candidate census of a hallucination detector. Given one "
    "trajectory, list the specific claims or actions by the assistant that "
    "could be factual errors: statements that contradict cited evidence, "
    "unsupported factual assertions, wrong numbers, or fabricated tool "
    "results.\n"
    "Reply with ONLY a JSON object of the exact shape:\n"
    '{"candidates": [{"candidate_id": "c1", "claim": "the exact claim text", '
    '"locus_event_id": "event_XXX", "evidence_refs": ["event_XXX"]}]}\n'
    "- candidate_id: short unique id c1, c2, ...\n"
    "- claim: quote the claim exactly.\n"
    "- locus_event_id: the event id where the claim appears.\n"
    "- evidence_refs: event ids of the evidence that should support or "
    "contradict the claim.\n"
    'If nothing is suspicious, return {"candidates": []}. No commentary.'
)

VERIFICATION_SYSTEM = (
    "You are the verification stage of a hallucination detector. For each "
    "candidate below, test the claim ONLY against the cited trajectory "
    "evidence. Do not override trajectory facts with outside knowledge, "
    "except for universally known facts (arithmetic, dates, geography, "
    "public reference facts).\n"
    "Reply with ONLY a JSON object of the exact shape:\n"
    '{"certificates": [{"candidate_id": "c1", "verdict": '
    '"supported|rejected", "rationale": "..."}]}\n'
    "- verdict=supported: the trajectory evidence shows the claim is "
    "factually wrong or unsupported by its cited evidence.\n"
    "- verdict=rejected: the claim is correct or adequately grounded.\n"
    "- verdict is EXACTLY one word: supported or rejected. Put ALL "
    "reasoning in rationale.\n"
    "One certificate per candidate. No commentary."
)

DEFENSE_SYSTEM = (
    "You are the defense stage of a hallucination detector. Challenge each "
    "supported certificate below: the certificate claims the assistant "
    "made a factual error. Find any legitimate reading under which the "
    "assistant's claim is acceptable, or the cited evidence is "
    "insufficient to convict.\n"
    "Reply with ONLY a JSON object of the exact shape:\n"
    '{"decisions": [{"candidate_id": "c1", "verdict": '
    '"upheld|overturned", "rationale": "..."}]}\n'
    "- upheld: the error stands after adversarial review.\n"
    "- overturned: the claim is defensible or the evidence cannot "
    "convict.\n"
    "- verdict is EXACTLY ONE word: upheld or overturned. Never write a "
    "sentence for verdict; put reasoning in rationale only.\n"
    "One decision per certificate. No commentary."
)


class _StageAudit:
    """Per-stage outcome record (provider + contract status)."""

    def __init__(self) -> None:
        self.census_ok = False
        self.verification_ok = False
        self.defense_ok = False
        self.defense_needed = False
        self.census_error = ""
        self.verification_error = ""
        self.defense_error = ""
        self.census_candidate_count = 0
        self.certificate_count = 0


def _census_user(packet: Mapping[str, Any]) -> str:
    return (
        "Find every candidate factual error in the assistant's trajectory.\n\n"
        + render_packet(packet)
    )


def _verification_user(
    packet: Mapping[str, Any], census: Sequence[Mapping[str, Any]],
) -> str:
    candidates = "\n".join(
        f"- {c.get('candidate_id')}: {c.get('claim')} "
        f"(locus {c.get('locus_event_id') or '?'}, "
        f"evidence {', '.join(c.get('evidence_refs') or []) or '?'})"
        for c in census
    )
    return (
        "Verify each candidate against the cited trajectory evidence.\n\n"
        "CANDIDATES:\n" + candidates + "\n\nTRAJECTORY:\n"
        + render_packet(packet)
    )


def _defense_user(
    packet: Mapping[str, Any], supported: Sequence[Mapping[str, Any]],
) -> str:
    rows = "\n".join(
        f"- {c.get('candidate_id')}: verdict=supported, "
        f"rationale={c.get('rationale')}"
        for c in supported
    )
    return (
        "Adversarially review every supported certificate below.\n\n"
        "SUPPORTED CERTIFICATES:\n" + rows + "\n\nTRAJECTORY:\n"
        + render_packet(packet)
    )


def _parse_census(
    parsed: Any, audit: _StageAudit,
) -> list[dict[str, Any]]:
    if not isinstance(parsed, dict) or not isinstance(
        parsed.get("candidates"), list
    ):
        audit.census_error = "census_contract_invalid"
        return []
    candidates: list[dict[str, Any]] = []
    for index, raw in enumerate(parsed["candidates"], start=1):
        if not isinstance(raw, dict):
            audit.census_error = "census_contract_invalid"
            return []
        candidate_id = str(raw.get("candidate_id") or f"c{index}")
        claim = str(raw.get("claim") or "")
        if not claim:
            audit.census_error = "census_contract_invalid"
            return []
        evidence_refs = [
            str(value) for value in raw.get("evidence_refs") or ()
            if str(value)
        ]
        candidates.append({
            "candidate_id": candidate_id,
            "claim": claim,
            "locus_event_id": str(raw.get("locus_event_id") or ""),
            "evidence_refs": evidence_refs,
        })
    audit.census_ok = True
    audit.census_candidate_count = len(candidates)
    return candidates


def _parse_certificates(
    parsed: Any, audit: _StageAudit,
) -> list[dict[str, Any]]:
    if not isinstance(parsed, dict) or not isinstance(
        parsed.get("certificates"), list
    ):
        audit.verification_error = "certificate_contract_invalid"
        return []
    certificates: list[dict[str, Any]] = []
    for raw in parsed["certificates"]:
        if not isinstance(raw, dict):
            audit.verification_error = "certificate_contract_invalid"
            return []
        verdict = str(raw.get("verdict") or "")
        if verdict not in ("supported", "rejected"):
            audit.verification_error = (
                f"certificate_verdict_invalid:{verdict!r}"
            )
            return []
        certificates.append({
            "candidate_id": str(raw.get("candidate_id") or ""),
            "verdict": verdict,
            "rationale": str(raw.get("rationale") or "")[:500],
        })
    audit.verification_ok = True
    audit.certificate_count = len(certificates)
    return certificates


def _parse_defense(
    parsed: Any, supported: Sequence[Mapping[str, Any]],
    audit: _StageAudit,
) -> dict[str, str]:
    if not isinstance(parsed, dict) or not isinstance(
        parsed.get("decisions"), list
    ):
        audit.defense_error = "defense_contract_invalid"
        return {}
    decisions: dict[str, str] = {}
    for raw in parsed["decisions"]:
        if not isinstance(raw, dict):
            audit.defense_error = "defense_contract_invalid"
            return {}
        verdict = str(raw.get("verdict") or "")
        if verdict not in ("upheld", "overturned"):
            audit.defense_error = f"defense_verdict_invalid:{verdict!r}"
            return {}
        decisions[str(raw.get("candidate_id") or "")] = verdict
    missing = [
        str(c.get("candidate_id"))
        for c in supported
        if str(c.get("candidate_id")) not in decisions
    ]
    if missing:
        audit.defense_error = (
            "defense_decision_missing:" + ",".join(sorted(missing))
        )
        return decisions
    audit.defense_ok = True
    return decisions


def _event_ordinal(
    packet: Mapping[str, Any], event_id: str,
) -> int:
    for event in packet.get("events") or []:
        if event.get("event_id") == event_id:
            return int(event.get("position") or 0)
    return 0


def adjudicate(
    *, packet: Mapping[str, Any], census: Sequence[Mapping[str, Any]],
    certificates: Sequence[Mapping[str, Any]], decisions: Mapping[str, str],
    audit: _StageAudit, runtime_valid: bool,
) -> dict[str, Any]:
    """Deterministic, closed-vocabulary terminal adjudication."""
    reasons: list[dict[str, Any]] = []
    if not runtime_valid:
        return _proof("unresolved", False, reasons, audit, packet)
    census_ids = {str(c.get("candidate_id")) for c in census}
    cert_ids = {str(c.get("candidate_id")) for c in certificates}
    unknown = sorted(cert_ids - census_ids)
    if unknown:
        # A certificate for a candidate the census never declared is a
        # contract violation: fail closed instead of convicting on it.
        reasons.append({
            "component": "verification",
            "code": "certificate_for_unknown_candidate",
            "error_type": ",".join(unknown),
        })
        return _proof("unresolved", True, reasons, audit, packet)
    coverage_complete = len(cert_ids) >= len(census_ids)
    supported = [c for c in certificates if c["verdict"] == "supported"]
    error_certs: list[dict[str, Any]] = []
    if supported:
        if not audit.defense_ok:
            reasons.append({
                "component": "defense",
                "code": "defense_incomplete",
                "error_type": audit.defense_error,
            })
            return _proof("unresolved", True, reasons, audit, packet)
        error_certs = [
            c for c in supported
            if decisions.get(str(c.get("candidate_id"))) == "upheld"
        ]
    # A supported certificate that survived adversarial review is the
    # error evidence. The five-element causal-chain decomposition is the
    # future evolution framework's authority, not the minimal detector's.
    complete = list(error_certs)
    if complete and coverage_complete:
        state = "verified_error"
    elif (not supported or not error_certs) and coverage_complete:
        # No certificate survived adversarial review (or none was supported
        # at all): the prosecution failed to convict.
        state = "verified_clean"
    elif not coverage_complete:
        reasons.append({
            "component": "verification",
            "code": "coverage_incomplete",
            "error_type": (
                f"certificates {len(cert_ids)} do not cover census "
                f"candidates {len(census_ids)}"
            ),
        })
        state = "unresolved"
    else:
        reasons.append({
            "component": "court",
            "code": "certificate_chain_incomplete",
            "error_type": (
                "supported certificates survived but no error "
                "certificate completed the adversarial review"
            ),
        })
        state = "unresolved"
    if complete:
        # The locus lives on the CENSUS candidate, not the certificate:
        # join back by candidate_id to locate the earliest responsible event.
        census_by_id = {
            str(c.get("candidate_id")): c for c in census
        }
        loci = [
            str(
                census_by_id.get(str(cert.get("candidate_id")), {}).get(
                    "locus_event_id"
                ) or ""
            )
            for cert in complete
        ]
        loci = [locus for locus in loci if locus]
        responsible_event_id = (
            min(loci, key=lambda eid: _event_ordinal(packet, eid))
            if loci else None
        )
    else:
        responsible_event_id = None
    prosecution_coverage = (
        "complete"
        if audit.census_candidate_count > 0
        and len(certificates) >= audit.census_candidate_count
        else "incomplete" if audit.census_candidate_count > 0 else "complete"
    )
    defense_coverage = (
        "complete"
        if (not supported or audit.defense_ok)
        else "incomplete"
    )
    proof = _proof(state, True, reasons, audit, packet)
    proof["responsible_event_id"] = responsible_event_id
    proof["error_certificate_count"] = len(error_certs)
    proof["surviving_error_certificate_count"] = len(complete)
    proof["prosecution_coverage"] = prosecution_coverage
    proof["defense_coverage"] = defense_coverage
    proof["terminal_basis"] = {
        "state": state,
        "decisive_facts": {
            "candidate_count": audit.census_candidate_count,
            "certificate_count": audit.certificate_count,
            "supported_certificate_count": len(supported),
            "error_certificate_count": len(error_certs),
            "complete_chain_certificate_count": len(complete),
        },
        "blocking_facts": [
            dict(reason) for reason in reasons
        ],
    }
    return proof


def _proof(
    state: str, runtime_valid: bool,
    reasons: Sequence[Mapping[str, Any]], audit: _StageAudit,
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "state": state,
        "decision_authority": (
            "authoritative" if runtime_valid else "invalid"
        ),
        "runtime_valid": runtime_valid,
        "responsible_event_id": None,
        "candidate_count": audit.census_candidate_count,
        "certificate_count": audit.certificate_count,
        "error_certificate_count": 0,
        "prosecution_coverage": "incomplete",
        "defense_coverage": "incomplete",
        "policy_evaluation": {"fired": [], "skipped": []},
        "authority_failure_reasons": [dict(reason) for reason in reasons],
        "terminal_basis": {
            "state": state,
            "decisive_facts": {},
            "blocking_facts": [dict(reason) for reason in reasons],
        },
        "occurrence_state": state,
        "incident_occurred": state == "verified_error",
        "occurrence_ledger": {
            "state": state,
            "occurrence_state": state,
        },
        "_packet_event_count": len(list(packet.get("events") or [])),
    }


def run_court(
    packet: Mapping[str, Any], config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run the three provider stages and the deterministic adjudication.

    Returns ``(proof, providers, stage_audit_dict)``. Provider failures are
    recorded per route and the run fails closed; they never raise out of
    the court.
    """
    audit = _StageAudit()
    providers: dict[str, Any] = {}
    runtime_valid = True
    reasons: list[dict[str, Any]] = []

    def _run_stage(
        name: str, system: str, user: str,
    ) -> Any:
        nonlocal runtime_valid
        route = dict(config[f"{name}_route"])
        route["_route_name"] = name
        try:
            parsed, meta = provider_call(
                route=route, system=system, user=user, purpose=name,
            )
        except ProviderError as exc:
            runtime_valid = False
            providers[name] = {
                "error_type": exc.error_type,
                "error": exc.message[:300],
                "attempts": exc.attempts,
            }
            reasons.append({
                "component": name,
                "code": "provider_error",
                "error_type": exc.error_type,
            })
            return None
        providers[name] = meta
        return parsed

    census_raw = _run_stage("census", CENSUS_SYSTEM, _census_user(packet))
    census = _parse_census(census_raw, audit)
    if not audit.census_ok:
        # One contract-repair pass: name the exact violation and re-ask.
        # The model re-emits the corrected object; we never reinterpret.
        repair_user = _census_user(packet) + (
            "\n\nCONTRACT_REPAIR: Your previous reply violated the census "
            "contract: " + audit.census_error + ". Return the complete "
            "corrected JSON object, starting with {."
        )
        census_raw = _run_stage("census", CENSUS_SYSTEM, repair_user)
        census = _parse_census(census_raw, audit)
    if not audit.census_ok:
        runtime_valid = False
        reasons.append({
            "component": "census",
            "code": "contract_error",
            "error_type": audit.census_error,
        })
        census = []

    if not runtime_valid:
        # The census is the evidence source for every later stage: a broken
        # census means no verification can be authoritative. Fail closed
        # without burning further provider calls.
        proof = adjudicate(
            packet=packet, census=census, certificates=[],
            decisions={}, audit=audit, runtime_valid=runtime_valid,
        )
        proof["decision_authority"] = "invalid"
        proof["state"] = "unresolved"
        proof["terminal_basis"]["state"] = "unresolved"
        proof["authority_failure_reasons"] = [dict(r) for r in reasons]
        stage_audit = {
            "census_ok": audit.census_ok,
            "verification_ok": audit.verification_ok,
            "defense_ok": audit.defense_ok,
            "defense_needed": audit.defense_needed,
            "candidate_count": audit.census_candidate_count,
            "certificate_count": audit.certificate_count,
        }
        return proof, providers, stage_audit

    verification_raw = _run_stage(
        "verification", VERIFICATION_SYSTEM,
        _verification_user(packet, census),
    )
    certificates = _parse_certificates(verification_raw, audit)
    if not audit.verification_ok:
        repair_user = _verification_user(packet, census) + (
            "\n\nCONTRACT_REPAIR: Your previous reply violated the "
            "certificate contract: " + audit.verification_error + ". "
            "Verdict must be EXACTLY supported or rejected; put all "
            "reasoning in rationale only. Return the complete corrected "
            "JSON object, starting with {."
        )
        verification_raw = _run_stage(
            "verification", VERIFICATION_SYSTEM, repair_user,
        )
        certificates = _parse_certificates(verification_raw, audit)
    if not audit.verification_ok:
        runtime_valid = False
        reasons.append({
            "component": "verification",
            "code": "contract_error",
            "error_type": audit.verification_error,
        })
        certificates = []

    supported = [c for c in certificates if c["verdict"] == "supported"]
    decisions: dict[str, str] = {}
    if supported:
        audit.defense_needed = True
        defense_raw = _run_stage(
            "defense", DEFENSE_SYSTEM, _defense_user(packet, supported),
        )
        decisions = _parse_defense(defense_raw, supported, audit)
        if not audit.defense_ok:
            repair_user = _defense_user(packet, supported) + (
                "\n\nCONTRACT_REPAIR: Your previous reply violated the "
                "defense contract: " + audit.defense_error + ". Verdict "
                "must be EXACTLY upheld or overturned; put reasoning in "
                "rationale only. Return the complete corrected JSON "
                "object, starting with {."
            )
            defense_raw = _run_stage("defense", DEFENSE_SYSTEM, repair_user)
            decisions = _parse_defense(defense_raw, supported, audit)
        if not audit.defense_ok:
            runtime_valid = False
            reasons.append({
                "component": "defense",
                "code": "contract_error",
                "error_type": audit.defense_error,
            })
    else:
        audit.defense_ok = True

    proof = adjudicate(
        packet=packet, census=census, certificates=certificates,
        decisions=decisions, audit=audit, runtime_valid=runtime_valid,
    )
    # Evidence is part of the frozen output: the evolution framework reads
    # the census, the certificates and the defense decisions for causal
    # analysis. They are never derived from anything outside the court.
    proof["census"] = [dict(c) for c in census]
    proof["certificates"] = [dict(c) for c in certificates]
    proof["defense_decisions"] = dict(decisions)
    if not runtime_valid:
        proof["decision_authority"] = "invalid"
        proof["state"] = "unresolved"
        proof["terminal_basis"]["state"] = "unresolved"
        proof["authority_failure_reasons"] = [
            dict(reason) for reason in reasons
        ] or proof["authority_failure_reasons"]
    stage_audit = {
        "census_ok": audit.census_ok,
        "verification_ok": audit.verification_ok,
        "defense_ok": audit.defense_ok,
        "defense_needed": audit.defense_needed,
        "candidate_count": audit.census_candidate_count,
        "certificate_count": audit.certificate_count,
    }
    return proof, providers, stage_audit
