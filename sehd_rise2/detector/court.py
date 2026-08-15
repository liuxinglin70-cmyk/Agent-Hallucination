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

from sehd_rise2.detector.packet import (
    INDEX_EVENT_PREVIEW,
    INDEX_TAIL_EVENTS,
    render_evidence_view,
    render_full_view,
    render_index_view,
)
from sehd_rise2.detector.provider import ProviderError, provider_call

CENSUS_SYSTEM = (
    "You are the candidate census of a hallucination detector. A candidate "
    "is ANY assistant action or statement that deviates from what the "
    "trajectory evidence supports or from what the user's task requires. "
    "The official agent-hallucination taxonomy you must scan for:\n"
    "1. TOOL-USE: unnecessary tool call; missing required call; incorrect "
    "tool arguments; wrong tool for the step; parallel-call conflict; "
    "fabricated, misreported or reordered tool results; placeholder values "
    "(e.g. {{...}}) written instead of real tool output; extra details "
    "invented inside tool arguments or summaries that no tool observation "
    "contains. Tool arguments are matched EXACTLY against what the task "
    "requires: an argument that differs from the required argument even "
    "only in formatting (e.g. writing '2.0000' where the task requires "
    "'2.0', or the wrong code 'RIV' where 'RMS' is required) is a "
    "deviation candidate. When a later tool argument or assistant report "
    "RESTATES a value a tool observation produced, compare it exactly "
    "against the observation too: an observation that returned '2.0' "
    "being echoed as '2.0000' is a misreported tool result.\n"
    "2. RETRIEVAL: the answer or summary misaligns the retrieved content, "
    "the query misaligns the user intent, or facts are stated without "
    "retrieved support.\n"
    "3. REASONING: math, science, general or factual reasoning errors.\n"
    "4. PLANNING: task-decomposition errors, or facts derived from nowhere "
    "(a claimed result no tool observation ever produced).\n"
    "5. HUMAN-INTERACTION: misinterpreting user instructions, requirements "
    "or user-stated facts.\n"
    "For every assistant message AND every tool call, compare it against "
    "(a) the tool observations in the trajectory, (b) the user's earlier "
    "statements, and (c) the task the user set. Name the deviation in the "
    "contrast form: 'the agent did X instead of the required Y'.\n"
    "Also flag assistant statements or tool arguments that add specific "
    "details (numbers, names, completion claims, evaluations) that no tool "
    "observation and no user statement contains — even when the tool call "
    "itself succeeded.\n"
    "Reply with ONLY a JSON object of the exact shape:\n"
    '{"candidates": [{"candidate_id": "c1", "claim": "the agent did X '
    'instead of the required Y", "locus_event_id": "event_XXX", '
    '"evidence_refs": ["event_XXX"]}]}\n'
    "- candidate_id: short unique id c1, c2, ...\n"
    "- claim: the exact deviating action or statement, in contrast form.\n"
    "- locus_event_id: the EARLIEST event where the deviation FIRST "
    "occurred — the step where the wrong action was taken, the wrong "
    "argument was used, or the wrong statement was first made. If the "
    "same deviation surfaces again in later events, still point at the "
    "FIRST occurrence, never at a later message that only repeats or "
    "reveals it. Example: if the agent called a tool with a wrong "
    "argument at step 1 and only described the wrong result in a message "
    "at step 4, the locus is the step-1 tool call, NOT the step-4 "
    "message.\n"
    "- evidence_refs: event ids that prove the correct or required "
    "behavior (observations, user requirements).\n"
    'If nothing deviates, return {"candidates": []}. No commentary.'
)

VERIFICATION_SYSTEM = (
    "You are the verification stage of a hallucination detector. For each "
    "candidate below, test the claimed deviation ONLY against the cited "
    "trajectory evidence. Do not override trajectory facts with outside "
    "knowledge, except for universally known facts (arithmetic, dates, "
    "geography, public reference facts).\n"
    "- TOOL-USE candidates: check the tool call's own observation and the "
    "task requirement. The question is whether the chosen tool, its "
    "arguments, and the agent's report of its output match what the "
    "trajectory actually required and produced. Tool arguments are "
    "compared EXACTLY against the task requirement: a formatting-only "
    "argument difference (e.g. '2.0000' where the task requires '2.0') "
    "IS a deviation. When a later argument or report restates a value "
    "PRODUCED BY a tool observation, the observation is the AUTHORITATIVE "
    "value: the task's precision/format instructions were already applied "
    "by the tool call, so the restatement must match the observation "
    "exactly. An observation of '2.0' echoed as '2.0000' is a deviation "
    "even when the task text mentions decimal precision. (A value the "
    "agent computed MANUALLY may follow the task's formatting "
    "instructions; a value PRODUCED BY a tool call must be restated "
    "exactly as the tool produced it.)\n"
    "- RETRIEVAL candidates: compare the agent's answer/summary against "
    "the retrieved content it cites.\n"
    "- REASONING candidates: recompute or re-derive the result from the "
    "cited numbers and facts. A difference that is only rounding or "
    "approximation — a last-digit difference, or a relative error under "
    "0.5% — is NOT a deviation: do not support a certificate for it.\n"
    "- PLANNING candidates: check whether every claimed intermediate fact "
    "is grounded in a tool observation or a user statement.\n"
    "Formatting variations (e.g. 2.0 vs 2.0000) and equivalent rephrasing "
    "are not deviations in PROSE reports. Exception: TOOL-ARGUMENT "
    "candidates use exact-match semantics — an argument that differs from "
    "the task requirement even only in formatting is a deviation; support "
    "the certificate for it.\n"
    "A certificate is supported when the cited evidence shows the "
    "deviation is real (the action contradicts the observations, or the "
    "task required something else and nothing in the evidence excuses it). "
    "It is rejected only when the evidence shows the action or statement "
    "was correct or adequate. Uncertainty is not a reason to reject: if "
    "the evidence cannot excuse the deviation, support it.\n"
    "Reply with ONLY a JSON object of the exact shape:\n"
    '{"certificates": [{"candidate_id": "c1", "verdict": '
    '"supported|rejected", "rationale": "..."}]}\n'
    "- verdict=supported: the trajectory evidence shows the deviation is "
    "real and unexcused.\n"
    "- verdict=rejected: the action or statement was correct or adequately "
    "grounded after all.\n"
    "- verdict is EXACTLY one word: supported or rejected. Put ALL "
    "reasoning in rationale.\n"
    "One certificate per candidate. No commentary."
)

RATIONALE_MAX_CHARS = 2000

DEFENSE_SYSTEM = (
    "You are the defense stage of a hallucination detector. Challenge each "
    "supported certificate below: the certificate claims the assistant "
    "deviated from the required behavior. Find any legitimate reading "
    "under which the assistant's action or statement is correct, or the "
    "cited evidence is insufficient to convict.\n"
    "Do not excuse a deviation by pointing to a later step that happened "
    "to work, unless that later step actually corrects the error. Do not "
    "excuse an action merely because the user did not complain.\n"
    "Overturn certificates whose only basis is a trivial rounding, "
    "approximation or formatting difference in a REPORTED number "
    "(e.g. 1116.5 vs 1115.2, 33.44 vs 33.42): such micro-differences are "
    "normal numerical presentation, not hallucinations. Do NOT overturn a "
    "TOOL-ARGUMENT deviation for formatting alone: tool arguments are "
    "exact-match interfaces, so an argument that differs from the task "
    "requirement even only in formatting (e.g. '2.0000' where '2.0' is "
    "required) is a real deviation. Do not excuse a misrestated tool "
    "observation by pointing to the task's precision/format phrasing: the "
    "tool call already applied the task's transformation, so its "
    "observation is the authoritative value.\n"
    "Reply with ONLY a JSON object of the exact shape:\n"
    '{"decisions": [{"candidate_id": "c1", "verdict": '
    '"upheld|overturned", "rationale": "..."}]}\n'
    "- upheld: the deviation stands after adversarial review.\n"
    "- overturned: the action is defensible or the evidence cannot "
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


class _StageAudit:
    """Per-stage outcome record (provider + contract + evidence status)."""

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
        self.verification_batches = 0
        self.defense_batches = 0
        # Evidence-view telemetry: whether each stage's provider view was
        # truncated. Verification/defense views are the CONVICTION basis:
        # a truncated view there fails the run closed. A truncated census
        # view only degrades discovery (recall), so it is recorded, not
        # fatal.
        self.census_view_meta: dict[str, Any] = {}
        self.verification_view_meta: dict[str, Any] = {}
        self.defense_view_meta: dict[str, Any] = {}

    @staticmethod
    def _coverage(meta: Mapping[str, Any]) -> str:
        return "reduced" if meta.get("packet_truncated") else "complete"


def _referenced_event_ids(
    candidates: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Union of every candidate's locus and evidence refs (order-stable)."""
    ids: set[str] = set()
    for candidate in candidates:
        locus = str(candidate.get("locus_event_id") or "")
        if locus:
            ids.add(locus)
        for ref in candidate.get("evidence_refs") or ():
            if str(ref):
                ids.add(str(ref))
    return sorted(ids)


def _census_user(
    packet: Mapping[str, Any], audit: _StageAudit,
) -> str:
    text, meta = render_index_view(packet)
    audit.census_view_meta = meta
    return (
        "Find every candidate factual error in the assistant's trajectory.\n\n"
        + text
    )


def _verification_user(
    packet: Mapping[str, Any], census: Sequence[Mapping[str, Any]],
    audit: _StageAudit, subset: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    batch = list(census if subset is None else subset)
    candidates = "\n".join(
        f"- {c.get('candidate_id')}: {c.get('claim')} "
        f"(locus {c.get('locus_event_id') or '?'}, "
        f"evidence {', '.join(c.get('evidence_refs') or []) or '?'})"
        for c in batch
    )
    text, meta = render_evidence_view(
        packet, _referenced_event_ids(batch),
    )
    audit.verification_view_meta = meta
    return (
        "Verify each candidate against the cited trajectory evidence.\n\n"
        "CANDIDATES:\n" + candidates + "\n\nTRAJECTORY EVIDENCE:\n"
        + text
    )


def _defense_user(
    packet: Mapping[str, Any], supported: Sequence[Mapping[str, Any]],
    census: Sequence[Mapping[str, Any]], audit: _StageAudit,
    subset: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    batch = list(supported if subset is None else subset)
    rows = "\n".join(
        f"- {c.get('candidate_id')}: verdict=supported, "
        f"rationale={c.get('rationale')}"
        for c in batch
    )
    census_by_id = {
        str(c.get("candidate_id")): c for c in census
    }
    cited: list[str] = []
    for certificate in batch:
        candidate = census_by_id.get(
            str(certificate.get("candidate_id")), {},
        )
        cited.append(candidate)
    text, meta = render_evidence_view(
        packet, _referenced_event_ids(cited),
    )
    audit.defense_view_meta = meta
    return (
        "Adversarially review every supported certificate below.\n\n"
        "SUPPORTED CERTIFICATES:\n" + rows + "\n\nTRAJECTORY EVIDENCE:\n"
        + text
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
            "rationale": str(raw.get("rationale") or "")[:RATIONALE_MAX_CHARS],
        })
    audit.verification_ok = True
    audit.certificate_count = len(certificates)
    return certificates


def _parse_defense(
    parsed: Any, supported: Sequence[Mapping[str, Any]],
    audit: _StageAudit,
) -> dict[str, dict[str, str]]:
    if not isinstance(parsed, dict) or not isinstance(
        parsed.get("decisions"), list
    ):
        audit.defense_error = "defense_contract_invalid"
        return {}
    decisions: dict[str, dict[str, str]] = {}
    for raw in parsed["decisions"]:
        if not isinstance(raw, dict):
            audit.defense_error = "defense_contract_invalid"
            return {}
        verdict = str(raw.get("verdict") or "")
        if verdict not in ("upheld", "overturned"):
            audit.defense_error = f"defense_verdict_invalid:{verdict!r}"
            return {}
        decisions[str(raw.get("candidate_id") or "")] = {
            "verdict": verdict,
            "rationale": str(raw.get("rationale") or "")[:RATIONALE_MAX_CHARS],
        }
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
    """Position of an event id; interaction ids resolve to their parent."""
    if not event_id:
        return 0
    for event in packet.get("events") or []:
        if event.get("event_id") == event_id:
            return int(event.get("position") or 0)
    parent = str(event_id).rsplit("_tool_", 1)[0]
    if parent != event_id:
        for event in packet.get("events") or []:
            if event.get("event_id") == parent:
                return int(event.get("position") or 0)
    return 0


_NUMERIC = __import__("re").compile(r"-?[0-9]+(?:\.[0-9]+)?")


def _numeric_literals(text: Any) -> dict[str, str]:
    """ASCII numeric string form -> float value for every numeric literal."""
    out: dict[str, str] = {}
    for match in _NUMERIC.finditer(str(text or "")):
        form = match.group(0)
        try:
            value = float(form)
        except ValueError:
            continue
        out.setdefault(form, value)
    return out


def _observation_restatement_check(
    packet: Mapping[str, Any], census: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministic census augment: misrestated tool observations.

    BFCL/025 class: a tool observation produces a value (mpf('2.0')) and a
    LATER tool argument restates it with a numerically equal but
    differently formatted literal ('2.0000'). The benchmark's tool-argument
    convention condemns that restatement; the LLM census catches it only
    intermittently, so the court adds a deterministic candidate whenever
    the pattern matches. The candidate still travels the full
    verification/defense chain: the verdict is never taken here.

    Precision guards (each eliminates a noise class observed in the data):
    - only ASCII numeric literals (Unicode digits in data tables are not
      agent restatements),
    - the restatement must be a tool ARGUMENT in a strictly later
      (event, interaction) position than the observation — prose reports
      are not covered by the exact-match convention,
    - both forms must carry a decimal point, so pure leading-zero
      variants ('01' vs '1') from tabular content never fire.
    """
    events = list(packet.get("events") or [])
    produced: dict[float, list[tuple[str, tuple[int, int]]]] = {}
    for event in events:
        epos = int(event.get("position") or 0)
        for iindex, interaction in enumerate(
            event.get("tool_interactions") or [],
        ):
            obs = interaction.get("observation")
            for form, value in _numeric_literals(obs).items():
                produced.setdefault(value, []).append(
                    (form, (epos, iindex)),
                )
    existing = " ".join(str(c.get("claim") or "") for c in census)
    added: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    index = len(census)
    for event in events:
        epos = int(event.get("position") or 0)
        for iindex, interaction in enumerate(
            event.get("tool_interactions") or [],
        ):
            args = interaction.get("arguments")
            if args in (None, ""):
                continue
            locus_id = (
                interaction.get("interaction_id") or event["event_id"]
            )
            for form, value in _numeric_literals(args).items():
                if "." not in form:
                    continue
                for produced_form, (opos, oindex) in produced.get(value, []):
                    if "." not in produced_form:
                        continue
                    if (opos, oindex) >= (epos, iindex):
                        continue
                    if produced_form == form:
                        continue
                    if (produced_form, form) in seen_pairs:
                        continue
                    if form in existing and produced_form in existing:
                        continue
                    seen_pairs.add((produced_form, form))
                    index += 1
                    added.append({
                        "candidate_id": f"c{index}",
                        "claim": (
                            f"the agent restated the tool-observed value "
                            f"'{produced_form}' (from an earlier tool "
                            f"observation) as '{form}' — numerically equal "
                            f"but differently formatted, without the task "
                            f"instructing that reformatting"
                        ),
                        "locus_event_id": locus_id,
                        "evidence_refs": [
                            f"event_{opos:03d}_tool_{oindex + 1:02d}",
                        ],
                    })
    return added


def _partition_by_evidence(
    packet: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]],
) -> list[list[Mapping[str, Any]]]:
    """Greedy batches of candidates whose cited events fit one evidence view.

    Each batch is built so that ``render_evidence_view`` for the batch's
    cited events fits the budget (not ``packet_truncated``). A candidate
    whose cited events cannot fit even alone becomes its own single-item
    batch: the court fails closed on that batch upstream, preserving the
    fail-closed discipline at the irreducible level instead of refusing
    the whole case. The partition is deterministic — same census, same
    batches, no randomness.
    """
    if len(candidates) <= 1:
        return [list(candidates)]
    batches: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    for candidate in candidates:
        trial = current + [candidate]
        _text, meta = render_evidence_view(
            packet, _referenced_event_ids(trial),
        )
        if meta.get("packet_truncated") and current:
            batches.append(current)
            current = [candidate]
        else:
            current = trial
    if current:
        batches.append(current)
    return batches


def adjudicate(
    *, packet: Mapping[str, Any], census: Sequence[Mapping[str, Any]],
    certificates: Sequence[Mapping[str, Any]],
    decisions: Mapping[str, Mapping[str, str]],
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
            if (decisions.get(str(c.get("candidate_id"))) or {}).get(
                "verdict"
            ) == "upheld"
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
        # The deviation origin is the locus each census candidate itself
        # declared: the step where the wrong action/statement FIRST
        # occurred. Evidence refs are the PROOF anchors (observations,
        # user requirements) — they precede the deviation by construction,
        # so folding them into the minimum systematically biases the
        # responsible step earlier than the deviation (e.g. the user
        # question at event_001 would win almost every case). The
        # responsible event is therefore the EARLIEST locus among the
        # upheld certificates; evidence refs are consulted only as a
        # fallback when no upheld certificate carries a locus.
        census_by_id = {
            str(c.get("candidate_id")): c for c in census
        }
        loci: list[str] = []
        fallback_refs: set[str] = set()
        for cert in complete:
            candidate = census_by_id.get(
                str(cert.get("candidate_id")), {}
            )
            locus = str(candidate.get("locus_event_id") or "")
            if locus:
                loci.append(locus)
            for ref in candidate.get("evidence_refs") or ():
                if str(ref):
                    fallback_refs.add(str(ref))
        cited_events = loci if loci else sorted(fallback_refs)
        ordered = [
            (event_id, _event_ordinal(packet, event_id))
            for event_id in cited_events
        ]
        ordered = [
            (event_id, position)
            for event_id, position in ordered
            if position > 0
        ]
        responsible_event_id = (
            min(ordered, key=lambda pair: pair[1])[0] if ordered else None
        )
        if responsible_event_id and "_tool_" in responsible_event_id:
            responsible_event_id = responsible_event_id.rsplit(
                "_tool_", 1,
            )[0]
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

    def _audit_dict() -> dict[str, Any]:
        return {
            "census_ok": audit.census_ok,
            "verification_ok": audit.verification_ok,
            "defense_ok": audit.defense_ok,
            "defense_needed": audit.defense_needed,
            "candidate_count": audit.census_candidate_count,
            "certificate_count": audit.certificate_count,
            "verification_batches": audit.verification_batches,
            "defense_batches": audit.defense_batches,
            "census_second_pass": bool(
                audit.census_view_meta.get("second_pass")
            ),
            "census_deterministic_augment": int(
                audit.census_view_meta.get("deterministic_augment") or 0
            ),
            "evidence_coverage": {
                "census": _StageAudit._coverage(audit.census_view_meta),
                "verification": _StageAudit._coverage(
                    audit.verification_view_meta,
                ),
                "defense": _StageAudit._coverage(audit.defense_view_meta),
            },
        }

    def _run_stage(
        name: str, system: str, user: str, *, route_name: str | None = None,
    ) -> Any:
        nonlocal runtime_valid
        route_key = route_name or name
        route = dict(config[f"{route_key}_route"])
        route["_route_name"] = name
        try:
            parsed, meta = provider_call(
                route=route, system=system, user=user, purpose=name,
            )
        except ProviderError as exc:
            runtime_valid = False
            # Provider errors accumulate under a dedicated key: a later
            # successful call must never overwrite the failure evidence.
            err_key = f"{name}_errors"
            providers.setdefault(err_key, [])
            providers[err_key].append({
                "error_type": exc.error_type,
                "error": exc.message[:300],
                "attempts": exc.attempts,
            })
            reasons.append({
                "component": name,
                "code": "provider_error",
                "error_type": exc.error_type,
                "error": exc.message[:300],
            })
            return None
        providers[name] = meta
        return parsed

    census_raw = _run_stage("census", CENSUS_SYSTEM, _census_user(packet, audit))
    census = _parse_census(census_raw, audit)
    for _repair_attempt in range(2):
        if audit.census_ok:
            break
        # Contract-repair pass: name the exact violation and re-ask. The
        # model re-emits the corrected object; we never reinterpret.
        repair_user = _census_user(packet, audit) + (
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

    if audit.census_ok:
        # Deterministic recall augment: misrestated tool observations
        # (BFCL/025 class) are proposed mechanically so the verification
        # and defense stages can judge them instead of the case silently
        # clearing on a census miss.
        augment = _observation_restatement_check(packet, census)
        if augment:
            census = list(census) + augment
            audit.census_candidate_count = len(census)
            audit.census_view_meta["deterministic_augment"] = len(augment)

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
        return proof, providers, _audit_dict()

    if not census:
        # A well-formed EMPTY census normally IS the clean verdict — but
        # only if the census could actually SEE the trajectory. The index
        # view renders the middle of long trajectories as 300-char
        # previews, so a hallucination buried in long middle content can
        # be invisible to the first pass. When any non-tail event carried
        # more content than the index preview could show, run ONE
        # second census pass over a bounded full-detail render of the
        # whole trajectory (a different lens; truncation there is a
        # recall degradation, not a conviction basis). A second pass that
        # fails provider-wise or contract-wise never damages the clean
        # verdict of the first pass: it is ignored.
        tail_ids = {
            str(event.get("event_id"))
            for event in list(packet.get("events") or [])[-INDEX_TAIL_EVENTS:]
        }
        middle_undetailed = [
            event for event in packet.get("events") or []
            if str(event.get("event_id")) not in tail_ids
            and event.get("content") not in (None, "")
            and len(str(event.get("content"))) > INDEX_EVENT_PREVIEW
        ]
        if middle_undetailed:
            audit.census_view_meta["second_pass"] = True
            second_text, _second_meta = render_full_view(packet)
            route = dict(config["census_route"])
            route["_route_name"] = "census_pass2"
            try:
                second_parsed, second_meta = provider_call(
                    route=route, system=CENSUS_SYSTEM, purpose="census_pass2",
                    user=(
                        "Find every candidate factual error in the "
                        "assistant's trajectory. The full trajectory follows "
                        "(long events may be truncated for space).\n\n"
                        + second_text
                    ),
                )
            except ProviderError as exc:
                providers["census_pass2"] = {
                    "error_type": exc.error_type,
                    "error": exc.message[:300],
                    "attempts": exc.attempts,
                }
            else:
                providers["census_pass2"] = second_meta
                second_census = _parse_census(second_parsed, audit)
                if audit.census_ok and second_census:
                    census = second_census
                else:
                    # The first pass stands: restore its well-formed
                    # empty verdict (the second pass is advisory).
                    audit.census_ok = True
                    audit.census_error = ""
                    audit.census_candidate_count = 0

    if not census:
        # Nothing to verify: the verification/defense stages are skipped
        # entirely (a later stage can never invent candidates the census
        # did not declare).
        audit.verification_ok = True
        audit.defense_ok = True
        proof = adjudicate(
            packet=packet, census=census, certificates=[],
            decisions={}, audit=audit, runtime_valid=runtime_valid,
        )
        proof["census"] = []
        proof["certificates"] = []
        proof["defense_decisions"] = {}
        return proof, providers, _audit_dict()

    verification_batches = _partition_by_evidence(packet, census)
    audit.verification_batches = len(verification_batches)
    certificates: list[dict[str, Any]] = []
    verification_truncated = False
    for batch in verification_batches:
        _text, meta = render_evidence_view(
            packet, _referenced_event_ids(batch),
        )
        if meta.get("packet_truncated"):
            # Only a single-candidate batch can reach this point (the
            # partition already splits anything larger): the cited events
            # cannot fit even alone. Fail closed on an irreducible view.
            verification_truncated = True
        verification_raw = _run_stage(
            "verification", VERIFICATION_SYSTEM,
            _verification_user(packet, census, audit, subset=batch),
        )
        # Reset per-batch parse state: a previous batch's success must not
        # suppress this batch's contract-repair loop.
        audit.verification_ok = False
        audit.verification_error = ""
        batch_certs = _parse_certificates(verification_raw, audit)
        for _repair_attempt in range(2):
            if audit.verification_ok:
                break
            repair_user = _verification_user(
                packet, census, audit, subset=batch,
            ) + (
                "\n\nCONTRACT_REPAIR: Your previous reply violated the "
                "certificate contract: " + audit.verification_error + ". "
                "Verdict must be EXACTLY supported or rejected; put all "
                "reasoning in rationale only. Return the complete corrected "
                "JSON object, starting with {."
            )
            verification_raw = _run_stage(
                "verification", VERIFICATION_SYSTEM, repair_user,
            )
            batch_certs = _parse_certificates(verification_raw, audit)
        if not audit.verification_ok:
            runtime_valid = False
            reasons.append({
                "component": "verification",
                "code": "contract_error",
                "error_type": audit.verification_error,
            })
            certificates = []
            break
        certificates.extend(batch_certs)
        if not runtime_valid:
            # A provider error already doomed this case: stop burning
            # calls on the remaining batches.
            break
    audit.certificate_count = len(certificates)
    audit.verification_view_meta = {
        "batches": len(verification_batches),
        "packet_truncated": verification_truncated,
    }
    if verification_truncated:
        # Some cited event could not be rendered in full even after
        # batching: the conviction basis is incomplete — fail closed.
        runtime_valid = False
        reasons.append({
            "component": "verification",
            "code": "evidence_view_incomplete",
            "error_type": "cited events exceeded the evidence view budget",
        })

    supported = [c for c in certificates if c["verdict"] == "supported"]
    decisions: dict[str, dict[str, str]] = {}
    defense_batches = 0
    if supported and runtime_valid:
        audit.defense_needed = True
        defense_batches = _partition_by_evidence(packet, supported)
        audit.defense_batches = len(defense_batches)
        defense_truncated = False
        for batch in defense_batches:
            _text, meta = render_evidence_view(
                packet, _referenced_event_ids(batch),
            )
            if meta.get("packet_truncated"):
                defense_truncated = True
            defense_raw = _run_stage(
                "defense", DEFENSE_SYSTEM,
                _defense_user(packet, supported, census, audit, subset=batch),
            )
            # Reset per-batch parse state (see verification batching).
            audit.defense_ok = False
            audit.defense_error = ""
            batch_decisions = _parse_defense(defense_raw, batch, audit)
            for _repair_attempt in range(2):
                if audit.defense_ok:
                    break
                repair_user = _defense_user(
                    packet, supported, census, audit, subset=batch,
                ) + (
                    "\n\nCONTRACT_REPAIR: Your previous reply violated the "
                    "defense contract: " + audit.defense_error + ". Verdict "
                    "must be EXACTLY upheld or overturned; put reasoning in "
                    "rationale only. Return the complete corrected JSON "
                    "object, starting with {."
                )
                defense_raw = _run_stage(
                    "defense", DEFENSE_SYSTEM, repair_user,
                )
                batch_decisions = _parse_defense(defense_raw, batch, audit)
            if not audit.defense_ok:
                runtime_valid = False
                reasons.append({
                    "component": "defense",
                    "code": "contract_error",
                    "error_type": audit.defense_error,
                })
                break
            decisions.update(batch_decisions)
            if not runtime_valid:
                # Provider error: stop burning calls on remaining batches.
                break
        missing = [
            str(cert.get("candidate_id")) for cert in supported
            if str(cert.get("candidate_id")) not in decisions
        ]
        if missing and runtime_valid:
            runtime_valid = False
            reasons.append({
                "component": "defense",
                "code": "defense_incomplete",
                "error_type": "decisions missing: " + ",".join(sorted(missing)),
            })
        audit.defense_view_meta = {
            "batches": len(defense_batches),
            "packet_truncated": defense_truncated,
        }
        if defense_truncated:
            # Same fail-closed rule for the adversarial review stage.
            runtime_valid = False
            reasons.append({
                "component": "defense",
                "code": "evidence_view_incomplete",
                "error_type": (
                    "cited events exceeded the evidence view budget"
                ),
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
    return proof, providers, _audit_dict()
