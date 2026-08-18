"""Deterministic terminal adjudication for detector_v2.1.

Closed vocabulary only. The preliminary positive may come from the Judge or
from Recall Scanner + Causal Upgrade; Defense can only overturn it with a
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


from detector_v2.packet import delivered_result as packet_delivered_result


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
    candidate = (upgrade_result.get("upgraded_candidate") or {}) if isinstance(upgrade_result, Mapping) else {}
    if not candidate:
        return None
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


def adjudicate(
    *,
    packet: Mapping[str, Any],
    judge_result: Mapping[str, Any],
    judge_status: Mapping[str, Any],
    recall_result: Mapping[str, Any] | None,
    recall_status: Mapping[str, Any] | None,
    upgrade_result: Mapping[str, Any] | None,
    upgrade_status: Mapping[str, Any] | None,
    tracer_result: Mapping[str, Any] | None,
    tracer_status: Mapping[str, Any] | None,
    defense_result: Mapping[str, Any] | None,
    defense_status: Mapping[str, Any] | None,
    guard_actions: Sequence[str] | None = None,
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

    judge_parsed = judge_result.get("parsed") or {}
    judge_positive = _positive_from_judge(judge_result)
    recall_ok = (
        bool(recall_result and recall_result.get("ok"))
        and bool(upgrade_result and upgrade_result.get("ok"))
    )
    upgrade_positive = _positive_from_upgrade(upgrade_result) if recall_ok else None

    if not judge_positive and not upgrade_positive:
        if "convict_mechanical_violation" in guards and recall_ok:
            # A decision_guard may promote a mechanical contract violation
            # into a conviction even with no preliminary positive. The guard
            # trigger vocabulary (predicate verdicts written by evidence_check
            # rows plus the stage facts) decides when this is safe; the
            # localization is the delivered-answer anchor.
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
                        "mechanical_anchor_event": anchor_event,
                        "judge": "false",
                        "recall_upgrade": "none",
                    },
                    "blocking_facts": [dict(reason) for reason in reasons],
                },
                "authority_failure_reasons": reasons,
            }
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

    traced_step = None
    traced_refs = list(positive.get("evidence_refs") or [])
    if tracer_result and tracer_result.get("ok"):
        traced = tracer_result.get("parsed") or {}
        traced_step = int(traced.get("step") or 0)
        traced_refs = list(traced.get("evidence_refs") or [])
    working_positive = _clean_positive(positive, traced_step)
    working_positive["evidence_refs"] = traced_refs or working_positive.get("evidence_refs", [])

    defense_parsed = (defense_result or {}).get("parsed")
    defense_parsed = defense_parsed if isinstance(defense_parsed, Mapping) else {}

    if defense_state == "ok" and defense_parsed.get("verdict") == "refuted":
        reasons.append({
            "component": "defense",
            "code": "refuted",
            "error_type": str(defense_parsed.get("reason") or ""),
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
                    "defense_reason": defense_parsed.get("reason") or "",
                },
                "blocking_facts": [dict(reason) for reason in reasons],
            },
            "authority_failure_reasons": reasons,
        }

    degraded = False
    if defense_state == "ok" and defense_parsed.get("verdict") == "substantiated":
        confirmed = defense_parsed.get("confirmed_step")
        # The step tracer is the dedicated localization authority; the
        # defense-confirmed step is only a fallback when tracing failed.
        if traced_step is None and isinstance(confirmed, int):
            event_count = len(list(packet.get("events") or []))
            if 1 <= confirmed <= event_count:
                working_positive = _clean_positive(working_positive, confirmed)
    elif defense_state == "ok" and defense_parsed.get("verdict") == "inconclusive":
        degraded = True
        reasons.append({
            "component": "defense",
            "code": "inconclusive",
            "error_type": str(defense_parsed.get("reason") or ""),
        })
    else:
        degraded = True
        reasons.append({
            "component": "defense",
            "code": str(defense_state or "unknown"),
            "error_type": str((defense_result or {}).get("error_type") or ""),
            "error": str((defense_result or {}).get("error") or ""),
        })

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
                "positive_source": positive["source"],
                "judge_step": judge_positive.get("step") if judge_positive else None,
                "traced_step": traced_step,
                "defense": defense_parsed.get("verdict"),
                "confirmed_step": defense_parsed.get("confirmed_step"),
                "delivered_result": positive.get("delivered_result") or "",
                "correct_result": positive.get("correct_result") or "",
                "judge_basis": judge_parsed.get("basis"),
                "authority": authority,
                "guard_actions": sorted(guards),
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
