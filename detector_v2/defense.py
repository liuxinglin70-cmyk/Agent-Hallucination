"""Stage: Defense Verifier.

Only runs after a positive preliminary verdict (Judge or Recall Upgrade).
Its only power is a PROVABLE overturn. ``inconclusive`` and transport
failures keep the positive verdict and degrade authority.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from detector_v2.interventions import InterventionPlan, empty_plan
from detector_v2.packet import (
    evidence_refs_support_step,
    packet_features,
    render_defense_view,
    render_judge_view,
)
from detector_v2.provider import ProviderError, majority_vote, provider_call
from detector_v2.semantics import (
    DEFENSE_CONTRACT_REPAIR,
    DEFENSE_SYSTEM,
    DEFENSE_VERDICTS,
)


def _parse_defense(parsed: Any) -> tuple[dict | None, str]:
    if not isinstance(parsed, Mapping):
        return None, "defense output is not an object"
    verdict = str(parsed.get("verdict") or "")
    if verdict not in DEFENSE_VERDICTS:
        return None, f"verdict {verdict!r} outside the closed vocabulary"
    confirmed = parsed.get("confirmed_step")
    if verdict == "substantiated":
        if not isinstance(confirmed, int) or isinstance(confirmed, bool):
            return None, "confirmed_step must be an integer when substantiated"
    else:
        if confirmed is not None:
            return None, "confirmed_step must be null unless substantiated"
    refs = parsed.get("evidence_refs")
    if refs is None:
        refs = []
    if not isinstance(refs, list) or any(not isinstance(x, str) for x in refs):
        return None, "evidence_refs must be a list of event ids"
    if verdict == "refuted" and not refs:
        return None, "refuted requires at least one evidence reference"
    reason = str(parsed.get("reason") or "").strip()
    if not reason:
        return None, "reason is required"
    return {
        "verdict": verdict,
        "confirmed_step": confirmed if verdict == "substantiated" else None,
        "evidence_refs": refs,
        "reason": reason,
    }, ""


def _validate_defense_evidence(
    parsed: Mapping[str, Any], packet: Mapping[str, Any],
    positive: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Require defense references to resolve to packet event/tool ids."""
    valid_ids = {
        str(event.get("event_id") or "")
        for event in packet.get("events") or ()
        if isinstance(event, Mapping)
    }
    for event in packet.get("events") or ():
        if isinstance(event, Mapping):
            valid_ids.update(
                str(item.get("interaction_id") or "")
                for item in event.get("tool_interactions") or ()
                if isinstance(item, Mapping)
            )
    refs = [str(ref) for ref in parsed.get("evidence_refs") or ()]
    if any(ref not in valid_ids for ref in refs):
        return None, "evidence_refs contains an unknown packet event"
    if parsed.get("verdict") == "substantiated":
        step = parsed.get("confirmed_step")
        event_count = len(list(packet.get("events") or ()))
        if not isinstance(step, int) or isinstance(step, bool) or not 1 <= step <= event_count:
            return None, "confirmed_step is outside the packet event range"
        if not refs:
            return None, "substantiated requires at least one evidence reference"
        if not evidence_refs_support_step(packet, refs, step):
            return None, "evidence_refs do not support confirmed_step"
        alleged_step = (positive or {}).get("step")
        if isinstance(alleged_step, int) and step != alleged_step:
            return None, "confirmed_step must equal the alleged step"
    return dict(parsed), ""


UPGRADED_SUBSTANTIATED = "substantiated_upgraded"


def _vote_verdict(verdicts: Sequence[str], *, cast: int) -> tuple[str, bool]:
    """Collapse the per-sample verdicts into one defense verdict.

    A conviction stands when more than half of the samples uphold it. Any
    other outcome — tie, no quorum — upgrades ``substantiated`` to
    ``substantiated_upgraded``: the case stays convicted but the authority
    degrades, because the defense could not decisively confirm it.
    """
    winner, _ = majority_vote(list(verdicts), on_tie="")
    if winner == "substantiated":
        return "substantiated", False
    if winner:
        return winner, False
    upgraded = any(v == "substantiated" for v in verdicts)
    return (UPGRADED_SUBSTANTIATED if upgraded else ""), True


def _positive_facts(positive: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "step": int(positive.get("step") or 0),
        "reason": str(positive.get("reason") or ""),
        "delivered_result": str(positive.get("delivered_result") or ""),
        "correct_result": str(positive.get("correct_result") or ""),
        "evidence_refs": [
            str(ref) for ref in positive.get("evidence_refs") or ()
            if str(ref)
        ],
    }


def _call_defense(
    *,
    packet: Mapping[str, Any],
    route: Mapping[str, Any],
    system: str,
    user: str,
    positive: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Sample one defense ballot; a ``None`` verdict abstains."""
    try:
        parsed, meta = provider_call(
            route=route, system=system, user=user, purpose="defense",
        )
    except ProviderError as exc:
        return {"ok": False, "parsed": None, "error": exc.message,
                "error_type": exc.error_type}, {
            "stage": "defense", "state": "provider_error",
            "attempts": exc.attempts, "view": "unknown", "warnings": [],
        }
    result, error = _parse_defense(parsed)
    if result is not None:
        result, error = _validate_defense_evidence(result, packet, positive)
    warnings: list[dict[str, str]] = []
    if result is None:
        warnings.append({"attempt": "initial", "error": f"contract:{error}"})
        try:
            parsed, meta = provider_call(
                route=route, system=system,
                user=user + DEFENSE_CONTRACT_REPAIR,
                purpose="defense_repair",
            )
            result, error = _parse_defense(parsed)
            if result is not None:
                result, error = _validate_defense_evidence(
                    result, packet, positive,
                )
        except ProviderError as exc:
            return {"ok": False, "parsed": None, "error": exc.message,
                    "error_type": exc.error_type}, {
                "stage": "defense", "state": "provider_error",
                "attempts": exc.attempts, "view": "unknown", "warnings": warnings,
            }
    if result is None:
        return {"ok": False, "parsed": None, "error": error,
                "error_type": "contract_error", "warnings": warnings}, {
            "stage": "defense", "state": "contract_error",
            "attempts": int(meta.get("attempts") or 0), "view": "unknown",
            "warnings": warnings,
        }
    return {"ok": True, "parsed": result, "raw": parsed,
            "provider": meta, "warnings": warnings}, {
        "stage": "defense", "state": "ok",
        "attempts": int(meta.get("attempts") or 0), "view": "unknown",
        "warnings": warnings,
    }


def _vote_defense(
    *,
    packet: Mapping[str, Any],
    route: Mapping[str, Any],
    system: str,
    user: str,
    positive: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the requested number of defense votes and collapse them.

    The verdict is decided by strict majority over the cast ballots; ties
    and no-quorum outcomes keep the conviction but upgrade the verdict to
    ``substantiated_upgraded`` (a degraded confirmation, never an
    overturn). The returned ``parsed`` is the first ballot whose verdict
    matches the collapsed verdict, or the first substantiated ballot when
    upgraded.
    """
    votes = max(1, int(route.get("votes", 1) or 1))
    ballots: list[dict[str, Any]] = []
    sample_meta: dict[str, Any] = {}
    total_attempts = 0
    warnings: list[dict[str, str]] = []
    last_error = {"error": "every defense vote failed",
                  "error_type": "provider_error"}
    for _ in range(votes):
        result, status = _call_defense(
            packet=packet, route=route, system=system, user=user,
            positive=positive,
        )
        total_attempts += int(status.get("attempts") or 0)
        warnings.extend(status.get("warnings") or [])
        if result.get("ok") and isinstance(result.get("parsed"), Mapping):
            ballots.append(dict(result["parsed"]))
            sample_meta = dict(result.get("provider") or {})
        else:
            last_error = {
                "error": str(result.get("error") or "defense vote failed"),
                "error_type": str(
                    result.get("error_type") or "provider_error"
                ),
            }
    if not ballots:
        return {
            "ok": False, "parsed": None,
            "error": last_error["error"],
            "error_type": last_error["error_type"],
            "provider": sample_meta,
        }, {
            "stage": "defense", "state": last_error["error_type"],
            "attempts": total_attempts, "view": "unknown",
            "warnings": warnings,
        }
    verdicts = [str(ballot.get("verdict") or "") for ballot in ballots]
    collapsed, tied = _vote_verdict(verdicts, cast=len(verdicts))
    if collapsed == UPGRADED_SUBSTANTIATED:
        anchor = next(
            (b for b in ballots if b.get("verdict") == "substantiated"),
            ballots[0],
        )
        parsed = {**anchor, "verdict": UPGRADED_SUBSTANTIATED}
    else:
        parsed = next(
            (b for b in ballots if b.get("verdict") == collapsed),
            ballots[0],
        )
    sample_meta["vote"] = {
        "votes": votes, "cast": len(verdicts),
        "verdicts": verdicts, "collapsed": collapsed, "tie": tied,
    }
    return {"ok": True, "parsed": parsed, "raw": ballots,
            "provider": sample_meta}, {
        "stage": "defense", "state": "ok",
        "attempts": total_attempts, "view": "unknown",
        "warnings": warnings,
    }


def run_defense(
    *,
    packet: Mapping[str, Any],
    positive: Mapping[str, Any],
    config: Mapping[str, Any],
    plan: InterventionPlan | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Review one positive preliminary verdict. Never raises."""
    if not positive:
        return {"ok": True, "parsed": None}, {
            "stage": "defense", "state": "not_needed", "attempts": 0,
            "view": "not_rendered", "warnings": [],
        }
    plan = plan or empty_plan()
    route = dict(config["defense_route"])
    route["_route_name"] = "defense"
    facts = _positive_facts(positive)
    evidence = plan.evidence_for(
        "defense.pre",
        packet,
        {
            "positive": positive,
            "evidence_refs": facts["evidence_refs"],
            "quote": str(positive.get("quote") or ""),
            "contracts": list(positive.get("contracts") or []),
            "positive_claim": str(positive.get("reason") or ""),
        },
    )
    evidence_block = ""
    if evidence:
        lines = [
            "\nMECHANICAL PRE-CHECKS (facts from the trajectory/contract, "
            "not a verdict):"
        ]
        for item in evidence:
            lines.append(
                f"- {item['predicate']}: {item['verdict']} ({item['detail']})"
            )
        evidence_block = "\n".join(lines)
    pre_actions = plan.guard_actions(
        packet,
        {
            "positive_source": str(positive.get("source") or "none"),
            "judge_confidence": str(positive.get("confidence") or ""),
            "defense_view_degraded": "false",
        },
    )
    force_full_view = "require_full_view_defense" in pre_actions
    system = DEFENSE_SYSTEM + plan.prompt_block(packet_features(packet), hook="defense.system")
    allegation = (
        "POSITIVE VERDICT:\n"
        f"alleged_step={facts['step']}\n"
        f"reason={facts['reason']}\n"
        f"delivered_result={facts['delivered_result']}\n"
        f"correct_result={facts['correct_result']}\n"
        + evidence_block
    )

    def user_for(view_text: str) -> str:
        return (
            "Review the positive verdict below using only the shown "
            "trajectory evidence.\n\n" + allegation + "\n" + view_text
        )

    if force_full_view:
        view_text, view_meta = render_judge_view(packet)
    else:
        view_text, view_meta = render_defense_view(
            packet, facts["step"], evidence_refs=facts["evidence_refs"],
        )
    result, status = _vote_defense(
        packet=packet, route=route, system=system, user=user_for(view_text),
        positive=positive,
    )
    status["view"] = (
        "complete"
        if not view_meta.get("packet_truncated") and not view_meta.get("degraded")
        else "degraded"
    )
    if result.get("ok"):
        verdict = (result.get("parsed") or {}).get("verdict")
        if verdict == "inconclusive" and view_meta.get("omitted_event_ids"):
            full_text, full_meta = render_judge_view(packet)
            result2, status2 = _vote_defense(
                packet=packet, route=route, system=system,
                user=user_for(full_text), positive=positive,
            )
            status2["view"] = (
                "complete" if not full_meta.get("degraded") else "degraded"
            )
            status2["escalated_from"] = "evidence_window"
            if result2.get("ok"):
                return result2, status2
            status["escalation_failed"] = status2["state"]
    result["view_meta"] = view_meta
    return result, status


__all__ = ["run_defense"]
