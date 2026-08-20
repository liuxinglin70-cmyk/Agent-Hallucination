"""Deterministic terminal adjudication for detector_v2.1.

Closed vocabulary only. Preliminary positives arrive as a hearing queue
(the judge first, then every recall-upgrade candidate from earliest
corrected step); defense substantiates or refutes each hearing, and the
first substantiated hearing convicts. Defense can only overturn with a
proven ``refuted`` verdict.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

STATE_ERROR = "verified_error"
STATE_CLEAN = "verified_clean"
STATE_UNRESOLVED = "unresolved"

AUTHORITY_AUTHORITATIVE = "authoritative"
AUTHORITY_DEGRADED_JUDGE_ONLY = "degraded_judge_only"
AUTHORITY_INVALID = "invalid"

CONVICTING_VERDICTS = frozenset({"substantiated", "substantiated_upgraded"})


from detector_v2.packet import (
    delivered_result as packet_delivered_result,
    evidence_refs_support_step,
)
from detector_v2.predicates import PREDICATE_CONVICTION_VERDICTS


def _event_id_for_step(packet: Mapping[str, Any], step: int | None) -> str | None:
    if step is None:
        return None
    for event in packet.get("events") or []:
        if int(event.get("position") or 0) == step:
            return str(event.get("event_id") or "")
    return None


def _mechanical_anchor(packet: Mapping[str, Any]) -> tuple[str | None, int | None]:
    """The delivered-answer anchor where a contract violation manifests."""
    anchor_id = str(packet_delivered_result(packet).get("last_prose_event_id") or "")
    if not anchor_id:
        return None, None
    for event in packet.get("events") or []:
        if str(event.get("event_id") or "") == anchor_id:
            position = event.get("position")
            return anchor_id, int(position) if str(position).isdigit() else None
    return anchor_id, None


def _positive_from_judge(judge_result: Mapping[str, Any]) -> dict[str, Any] | None:
    parsed = (judge_result.get("parsed") or {}) if isinstance(judge_result, Mapping) else {}
    if not parsed.get("is_hallucination"):
        return None
    root = parsed.get("root_cause") or {}
    return {
        "source": "judge",
        "step": int(parsed.get("hallucination_step") or 0),
        "reason": str(parsed.get("hallucination_reason") or ""),
        "delivered_result": str(parsed.get("delivered_result") or ""),
        "correct_result": str(parsed.get("correct_result") or ""),
        "evidence_refs": list(parsed.get("evidence_refs") or []),
        "quote": str(root.get("quote") or ""),
        "referent": str(root.get("referent") or ""),
        "confidence": str(parsed.get("confidence") or ""),
    }


def _positive_from_upgrade(upgrade_result: Mapping[str, Any]) -> dict[str, Any] | None:
    candidates = (
        upgrade_result.get("upgraded_candidates")
        or (
            [upgrade_result.get("upgraded_candidate")]
            if upgrade_result.get("upgraded_candidate") else []
        )
    ) if isinstance(upgrade_result, Mapping) else []
    candidates = [c for c in candidates if c]
    candidates.sort(key=lambda c: int(c.get("corrected_step") or 0))
    if not candidates:
        return None
    candidate = candidates[0]
    return {
        "source": "recall_upgrade",
        "step": int(candidate.get("corrected_step") or 0),
        "reason": str(candidate.get("claim") or ""),
        "delivered_result": "",
        "correct_result": "",
        "evidence_refs": list(candidate.get("evidence_refs") or []),
        "quote": str(candidate.get("quote") or ""),
        "referent": "",
    }


def _clean_positive(positive: Mapping[str, Any], step: int | None) -> dict[str, Any]:
    clean = dict(positive)
    if step is not None:
        clean["step"] = int(step)
    return clean


def _hearing_verdict(
    hearing: Mapping[str, Any], packet: Mapping[str, Any] | None = None,
) -> str:
    parsed = (hearing.get("defense_result") or {}).get("parsed")
    parsed = parsed if isinstance(parsed, Mapping) else {}
    verdict = str(parsed.get("verdict") or "none")
    if verdict not in CONVICTING_VERDICTS or packet is None:
        return verdict
    alleged_step = hearing.get("step")
    confirmed_step = parsed.get("confirmed_step")
    refs = parsed.get("evidence_refs") or []
    if (
        not isinstance(alleged_step, int)
        or confirmed_step != alleged_step
        or not evidence_refs_support_step(packet, refs, alleged_step)
    ):
        return "invalid"
    return verdict


def _convicted_hearing(
    hearings: Sequence[Mapping[str, Any]], packet: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    return next(
        (
            h for h in hearings
            if _hearing_verdict(h, packet) in CONVICTING_VERDICTS
        ),
        None,
    )


def _convicted_candidate_step(
    upgrade_result: Mapping[str, Any] | None,
    convicted: Mapping[str, Any],
) -> int | None:
    """The upgraded candidate the convicted hearing heard, by id."""
    if not isinstance(upgrade_result, Mapping):
        return None
    candidates = list(upgrade_result.get("upgraded_candidates") or [])
    if not candidates and upgrade_result.get("upgraded_candidate"):
        candidates = [upgrade_result["upgraded_candidate"]]
    candidate = next(
        (
            c for c in candidates
            if c and str(c.get("candidate_id") or "")
            == str(convicted.get("candidate_id") or "")
        ),
        None,
    )
    if candidate is None:
        return None
    step = candidate.get("corrected_step")
    return int(step) if isinstance(step, int) else None


def adjudicate(
    *,
    packet: Mapping[str, Any],
    judge_result: Mapping[str, Any],
    judge_status: Mapping[str, Any],
    recall_result: Mapping[str, Any] | None = None,
    recall_status: Mapping[str, Any] | None = None,
    upgrade_result: Mapping[str, Any] | None = None,
    upgrade_status: Mapping[str, Any] | None = None,
    tracer_result: Mapping[str, Any] | None = None,
    tracer_status: Mapping[str, Any] | None = None,
    defense_result: Mapping[str, Any] | None = None,
    defense_status: Mapping[str, Any] | None = None,
    guard_actions: Sequence[str] | None = None,
    mechanical_evidence: Sequence[Mapping[str, Any]] | None = None,
    hearings: Sequence[Mapping[str, Any]] | None = None,
    hearing_facts: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    guards = set(guard_actions or ())
    if len(guards) > 1:
        # Conflicting guards: all are disabled and the baseline path applies.
        guards = set()
    reasons: list[dict[str, str]] = []
    if judge_status.get("state") != "ok" or not judge_result.get("ok"):
        reasons.append({
            "component": "judge",
            "code": str(judge_status.get("state") or "unknown"),
            "error_type": str(judge_result.get("error_type") or ""),
            "error": str(judge_result.get("error") or ""),
        })
        return {
            "state": STATE_UNRESOLVED,
            "decision_authority": AUTHORITY_INVALID,
            "runtime_valid": False,
            "responsible_event_id": None,
            "responsible_step": None,
            "incident_occurred": None,
            "terminal_basis": {
                "state": STATE_UNRESOLVED,
                "decisive_facts": {},
                "blocking_facts": [dict(reason) for reason in reasons],
            },
            "authority_failure_reasons": reasons,
        }

    valid_mechanical_evidence = any(
        str(item.get("action") or "") == "convict_mechanical_violation"
        and bool(str(item.get("intervention_id") or ""))
        and bool(str(item.get("evidence_intervention_id") or ""))
        and bool(str(item.get("failure_family") or ""))
        and str(item.get("verdict") or "")
        in PREDICATE_CONVICTION_VERDICTS.get(
            str(item.get("predicate") or ""), frozenset(),
        )
        for item in mechanical_evidence or ()
    )
    if "convict_mechanical_violation" in guards and valid_mechanical_evidence:
        anchor_event, anchor_step = _mechanical_anchor(packet)
        return {
            "state": STATE_ERROR,
            "decision_authority": AUTHORITY_AUTHORITATIVE,
            "runtime_valid": True,
            "responsible_event_id": anchor_event,
            "responsible_step": anchor_step,
            "incident_occurred": True,
            "terminal_basis": {
                "state": STATE_ERROR,
                "decisive_facts": {
                    "positive_source": "mechanical_violation",
                    "guard_action": "convict_mechanical_violation",
                },
                "blocking_facts": [dict(reason) for reason in reasons],
            },
            "authority_failure_reasons": reasons,
        }

    judge_parsed = judge_result.get("parsed") or {}
    judge_positive = _positive_from_judge(judge_result)
    recall_ok = (
        bool(recall_result and recall_result.get("ok"))
        and bool(upgrade_result and upgrade_result.get("ok"))
    )
    upgrade_positive = _positive_from_upgrade(upgrade_result) if recall_ok else None

    if not judge_positive and not upgrade_positive:
        degraded = not recall_ok
        if degraded:
            reasons.append({
                "component": "recall",
                "code": "recall_unavailable",
                "error_type": (
                    str(recall_result.get("error_type") or "") if recall_result else ""
                ),
                "error": str(recall_result.get("error") or "") if recall_result else "",
            })
        authority = (
            AUTHORITY_DEGRADED_JUDGE_ONLY if degraded
            else AUTHORITY_AUTHORITATIVE
        )
        return {
            "state": STATE_CLEAN,
            "decision_authority": authority,
            "runtime_valid": True,
            "responsible_event_id": None,
            "responsible_step": None,
            "incident_occurred": False,
            "terminal_basis": {
                "state": STATE_CLEAN,
                "decisive_facts": {
                    "judge": "false",
                    "recall_upgrade": "none",
                    "delivered_result": judge_parsed.get("delivered_result") or "",
                    "correct_result": judge_parsed.get("correct_result") or "",
                },
                "blocking_facts": [dict(reason) for reason in reasons],
            },
            "authority_failure_reasons": reasons,
        }

    positive = judge_positive if judge_positive else upgrade_positive
    if upgrade_positive and not judge_positive:
        # Information asymmetry: validation stages for a recall upgrade must
        # not receive the judge's correct_result / is_hallucination anchors.
        positive = _clean_positive(positive, positive.get("step"))
        positive["delivered_result"] = ""
        positive["correct_result"] = ""

    defense_state = str(defense_status.get("state")) if defense_status else "not_run"
    if "withhold_conviction" in guards and defense_state == "ok":
        reasons.append({
            "component": "adjudicate.guard",
            "code": "withhold_conviction",
            "error_type": "guard action withheld the preliminary conviction",
        })
        return {
            "state": STATE_CLEAN,
            "decision_authority": AUTHORITY_AUTHORITATIVE,
            "runtime_valid": True,
            "responsible_event_id": None,
            "responsible_step": None,
            "incident_occurred": False,
            "terminal_basis": {
                "state": STATE_CLEAN,
                "decisive_facts": {
                    "positive_source": positive["source"],
                    "guard_action": "withhold_conviction",
                    "guard_policy": "baseline_overridden_by_deployed_guard",
                },
                "blocking_facts": [dict(reason) for reason in reasons],
            },
            "authority_failure_reasons": reasons,
        }

    # The hearing queue is the conviction flow: the judge first, then
    # every recall-upgrade candidate from earliest corrected step.
    hearing_queue: list[Mapping[str, Any]] = list(hearings or [])
    defense_parsed = (defense_result or {}).get("parsed")
    defense_parsed = defense_parsed if isinstance(defense_parsed, Mapping) else {}

    if hearing_queue and all(
        _hearing_verdict(h) == "refuted" for h in hearing_queue
    ):
        for h in hearing_queue:
            reasons.append({
                "component": "defense",
                "code": "refuted",
                "error_type": str(
                    ((h.get("defense_result") or {}).get("parsed") or {}).get(
                        "reason"
                    ) or ""
                ),
            })
        if "restore_judge_conviction" in guards and (
            judge_positive or positive.get("source") == "recall_upgrade"
        ):
            # The guard restores the preliminary conviction that defense
            # refuted. The trigger vocabulary decides when this is safe;
            # recall-upgrade positives are the FN family caused by
            # over-refuting required-action violations.
            step = positive.get("step")
            responsible_event_id = _event_id_for_step(packet, step)
            return {
                "state": STATE_ERROR,
                "decision_authority": AUTHORITY_AUTHORITATIVE,
                "runtime_valid": True,
                "responsible_event_id": responsible_event_id,
                "responsible_step": step if responsible_event_id else None,
                "incident_occurred": True,
                "terminal_basis": {
                    "state": STATE_ERROR,
                    "decisive_facts": {
                        "positive_source": positive["source"],
                        "defense": "refuted",
                        "defense_reason": defense_parsed.get("reason") or "",
                        "guard_action": "restore_judge_conviction",
                    },
                    "blocking_facts": [dict(reason) for reason in reasons],
                },
                "authority_failure_reasons": reasons,
            }
        last_reason = reasons[-1] if reasons else {}
        return {
            "state": STATE_CLEAN,
            "decision_authority": AUTHORITY_AUTHORITATIVE,
            "runtime_valid": True,
            "responsible_event_id": None,
            "responsible_step": None,
            "incident_occurred": False,
            "terminal_basis": {
                "state": STATE_CLEAN,
                "decisive_facts": {
                    "positive_source": positive["source"],
                    "defense": "refuted",
                    "defense_reason": last_reason.get("error_type") or "",
                    "hearings": [
                        {
                            "source": h.get("source"),
                            "candidate_id": h.get("candidate_id"),
                            "verdict": _hearing_verdict(h),
                        }
                        for h in hearing_queue
                    ],
                },
                "blocking_facts": [dict(reason) for reason in reasons],
            },
            "authority_failure_reasons": reasons,
        }

    convicted = _convicted_hearing(hearing_queue, packet)
    if convicted is None:
        # No hearing substantiated and not all refuted: the defense failed
        # to settle, the preliminary verdict survives with degraded
        # authority.
        degraded = True
        if defense_state != "ok":
            reasons.append({
                "component": "defense",
                "code": str(defense_state or "unknown"),
                "error_type": str((defense_result or {}).get("error_type") or ""),
                "error": str((defense_result or {}).get("error") or ""),
            })
        else:
            reasons.append({
                "component": "defense",
                "code": "inconclusive",
                "error_type": str(defense_parsed.get("reason") or ""),
            })
        step = positive.get("step")
        responsible_event_id = _event_id_for_step(packet, step)
        return {
            "state": STATE_ERROR,
            "decision_authority": AUTHORITY_DEGRADED_JUDGE_ONLY,
            "runtime_valid": True,
            "responsible_event_id": responsible_event_id,
            "responsible_step": step if responsible_event_id else None,
            "incident_occurred": True,
            "terminal_basis": {
                "state": STATE_ERROR,
                "decisive_facts": {
                    "positive_source": positive["source"],
                    "defense": "unsettled",
                    "judge_basis": judge_parsed.get("basis"),
                    "hearings": [
                        {
                            "source": h.get("source"),
                            "candidate_id": h.get("candidate_id"),
                            "verdict": _hearing_verdict(h),
                        }
                        for h in hearing_queue
                    ],
                },
                "blocking_facts": [dict(reason) for reason in reasons],
            },
            "authority_failure_reasons": reasons,
        }

    working_positive = dict(positive)
    convicted_parsed = (
        (convicted.get("defense_result") or {}).get("parsed") or {}
    )
    convicted_tracer = (
        (convicted.get("tracer_result") or {}).get("parsed") or {}
    )
    working_positive["source"] = str(convicted.get("source") or positive["source"])
    if convicted.get("source") == "recall_upgrade":
        working_positive["step"] = _convicted_candidate_step(
            upgrade_result, convicted,
        ) or int(working_positive.get("step") or 0)
    traced_step = None
    traced_refs = list(
        convicted_tracer.get("evidence_refs")
        or working_positive.get("evidence_refs")
        or []
    )
    if convicted_tracer.get("step") is not None:
        traced_step = int(convicted_tracer.get("step"))
        working_positive = _clean_positive(working_positive, traced_step)
    working_positive["evidence_refs"] = traced_refs

    verdict = str(convicted_parsed.get("verdict") or "")
    degraded = verdict == "substantiated_upgraded"
    if traced_step is None and not degraded:
        confirmed = convicted_parsed.get("confirmed_step")
        # The step tracer is the dedicated localization authority; the
        # defense-confirmed step is only a fallback when tracing failed.
        if isinstance(confirmed, int):
            event_count = len(list(packet.get("events") or []))
            if 1 <= confirmed <= event_count:
                working_positive = _clean_positive(working_positive, confirmed)

    step = working_positive.get("step")
    responsible_event_id = _event_id_for_step(packet, step)
    authority = AUTHORITY_DEGRADED_JUDGE_ONLY if degraded else AUTHORITY_AUTHORITATIVE
    return {
        "state": STATE_ERROR,
        "decision_authority": authority,
        "runtime_valid": True,
        "responsible_event_id": responsible_event_id,
        "responsible_step": step if responsible_event_id else None,
        "incident_occurred": True,
        "terminal_basis": {
            "state": STATE_ERROR,
            "decisive_facts": {
                "positive_source": working_positive["source"],
                "judge_step": judge_positive.get("step") if judge_positive else None,
                "traced_step": traced_step,
                "defense": verdict,
                "confirmed_step": convicted_parsed.get("confirmed_step"),
                "delivered_result": working_positive.get("delivered_result") or "",
                "correct_result": working_positive.get("correct_result") or "",
                "judge_basis": judge_parsed.get("basis"),
                "authority": authority,
                "guard_actions": sorted(guards),
                "hearings": [
                    {
                        "source": h.get("source"),
                        "candidate_id": h.get("candidate_id"),
                        "verdict": _hearing_verdict(h),
                    }
                    for h in hearing_queue
                ],
            },
            "blocking_facts": [dict(reason) for reason in reasons],
        },
        "authority_failure_reasons": reasons,
    }


__all__ = [
    "AUTHORITY_AUTHORITATIVE",
    "AUTHORITY_DEGRADED_JUDGE_ONLY",
    "AUTHORITY_INVALID",
    "STATE_CLEAN",
    "STATE_ERROR",
    "STATE_UNRESOLVED",
    "adjudicate",
]
