"""Stage: Defense Verifier.

Only runs after a positive preliminary verdict (Judge or Recall Upgrade).
Its only power is a PROVABLE overturn. ``inconclusive`` and transport
failures keep the positive verdict and degrade authority.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from detector.stage_retry import memo_stage
from detector.provider import provider_fallback_scope
from detector.evidence_view import expand_view, referent_source_block, task_source_block

from detector.evidence import (
    citation_error,
    render_evidence_block,
    snapshot_digest,
)
from detector.interventions import InterventionPlan, empty_plan
from detector.packet import (
    evidence_refs_support_step,
    packet_features,
    packet_reference_error,
    render_defense_view,
    render_judge_view,
)
from detector.provider import (
    ProviderError,
    accumulate_usage,
    majority_vote,
    merge_provider_meta,
    provider_call,
    raise_if_terminal,
    required_quorum,
)
from detector.runtime import parallel_ordered


from detector.semantics import (
    contract_repair_feedback,
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
    if verdict == "refuted" and not refs:
        return None, "refuted requires at least one evidence reference"
    cited = parsed.get("evidence_citations")
    if cited is None:
        cited = []
    if not isinstance(cited, list) or any(
        not isinstance(x, str) for x in cited
    ):
        return None, "evidence_citations must be a list of snapshot ids"
    reason = str(parsed.get("reason") or "").strip()
    if not reason:
        return None, "reason is required"
    return {
        "verdict": verdict,
        "confirmed_step": confirmed if verdict == "substantiated" else None,
        "evidence_refs": refs,
        "evidence_citations": [str(x) for x in cited if str(x)],
        "conflict_note": str(parsed.get("conflict_note") or "").strip(),
        "reason": reason,
    }, ""


def _validate_defense_evidence(
    parsed: Mapping[str, Any], packet: Mapping[str, Any],
    positive: Mapping[str, Any] | None = None,
    snapshot: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Require defense references to resolve to packet event/tool ids."""
    refs = parsed.get("evidence_refs")
    reference_error = packet_reference_error(packet, refs)
    if reference_error:
        return None, reference_error
    citation_error_message = citation_error(
        snapshot, parsed.get("evidence_citations") or [],
        where="defense",
        require_settled=parsed.get("verdict") == "substantiated",
    )
    if citation_error_message:
        return None, citation_error_message
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


def _vote_verdict(verdicts: Sequence[str]) -> tuple[str, bool]:
    """Collapse the per-sample verdicts into one defense verdict.

    A conviction stands when more than half of the cast ballots uphold it.
    Any other outcome — tie, no strict majority — upgrades
    ``substantiated`` to ``substantiated_upgraded``, and every other
    undecided split collapses to ``inconclusive``: the case stays convicted
    but the authority degrades, because the defense could not decisively
    condemn or clear it.

    ``inconclusive`` is the deliberate fallback. Picking an arbitrary ballot
    here would let sampling order decide between an overturn (``refuted``)
    and a degraded conviction (``inconclusive``) for the same vote multiset,
    and it would let a non-majority ``refuted`` overturn a conviction that
    the contract says may only fall to a *provable* refutation.
    """
    winner, _ = majority_vote(list(verdicts), on_tie="")
    if winner == "substantiated":
        return "substantiated", False
    if winner:
        return winner, False
    if any(v == "substantiated" for v in verdicts):
        return UPGRADED_SUBSTANTIATED, True
    return "inconclusive", True


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
    snapshot: Mapping[str, Any] | None = None,
    fallback_user: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Sample one defense ballot; a ``None`` verdict abstains."""
    cache_tag = (
        snapshot_digest(snapshot) if snapshot is not None else ""
    )
    try:
        with provider_fallback_scope(fallback_user):
            parsed, meta = provider_call(route=route, system=system, user=user, purpose="defense", cache_tag=cache_tag)
    except ProviderError as exc:
        raise_if_terminal(exc)
        return {"ok": False, "parsed": None, "error": exc.message,
                "error_type": exc.error_type, "failure": exc.as_dict(),
                "provider": {"attempts": exc.attempts, "usage_total": exc.usage_total}}, {
            "stage": "defense", "state": exc.error_type,
            "attempts": exc.attempts, "view": "unknown", "warnings": [],
        }
    result, error = _parse_defense(parsed)
    if result is not None:
        result, error = _validate_defense_evidence(
            result, packet, positive, snapshot,
        )
    warnings: list[dict[str, str]] = []
    if meta.get("evidence_view_fallback"):
        warnings.append({"attempt": "initial", "error": "evidence_expansion:provider_context_limit"})
    if result is None:
        initial_meta = dict(meta)
        warnings.append({"attempt": "initial", "error": f"contract:{error}"})
        try:
            feedback = DEFENSE_CONTRACT_REPAIR + contract_repair_feedback(parsed, error)
            with provider_fallback_scope(fallback_user + feedback if fallback_user is not None else None):
                parsed, meta = provider_call(route=route, system=system, user=user + feedback,
                                             purpose="defense_repair", cache_tag=cache_tag)
            meta = merge_provider_meta(initial_meta, meta)
            result, error = _parse_defense(parsed)
            if result is not None:
                result, error = _validate_defense_evidence(
                    result, packet, positive, snapshot,
                )
        except ProviderError as exc:
            raise_if_terminal(exc)
            return {"ok": False, "parsed": None, "error": exc.message,
                    "error_type": exc.error_type, "failure": exc.as_dict(),
                    "provider": merge_provider_meta(initial_meta, {
                        "attempts": exc.attempts, "usage_total": exc.usage_total})}, {
                "stage": "defense", "state": exc.error_type,
                "attempts": int(initial_meta.get("attempts") or 0) + exc.attempts, "view": "unknown", "warnings": warnings,
            }
    if result is None:
        return {"ok": False, "parsed": None, "error": error,
                "error_type": "contract_error", "warnings": warnings, "provider": meta, "raw": parsed}, {
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
    snapshot: Mapping[str, Any] | None = None,
    fallback_user: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the requested number of defense votes and collapse them.

    A quorum is required before verdict aggregation. Failed ballots remain
    runtime failures; valid but inconclusive ballots remain semantic evidence.
    ``vote_topup`` re-casts abstained ballots a bounded number of times.
    The returned ``parsed`` is the first ballot whose verdict matches the
    collapsed verdict, or the first substantiated ballot when upgraded.
    """
    votes = max(1, int(route.get("votes", 1) or 1))
    topup_budget = max(0, int(route.get("vote_topup", 0) or 0))
    ballots: list[dict[str, Any]] = []
    sample_meta: dict[str, Any] = {}
    # See ``accumulate_usage``: the sample follows the last ballot, so only the
    # total reflects what the whole vote cost.
    ballots_usage: dict[str, Any] = {}
    total_attempts = 0
    warnings: list[dict[str, str]] = []
    failures: list[dict[str, Any]] = []
    topups_used = 0

    def cast_at(_index: int) -> tuple[dict[str, Any], dict[str, Any]]:
        return _call_defense(
            packet=packet, route=route, system=system, user=user,
            positive=positive, snapshot=snapshot,
            **({"fallback_user": fallback_user} if fallback_user is not None else {}),
        )

    def absorb(outcome: tuple[dict[str, Any], dict[str, Any]]) -> None:
        nonlocal total_attempts, sample_meta
        result, status = outcome
        total_attempts += int(status.get("attempts") or 0)
        warnings.extend(status.get("warnings") or [])
        ballot_meta = dict(result.get("provider") or {})
        accumulate_usage(ballots_usage, ballot_meta.get("usage_total") or ballot_meta.get("usage") or {})
        if ballot_meta:
            sample_meta = ballot_meta
        if ballots_usage:
            sample_meta["usage_total"] = dict(ballots_usage)
        if result.get("ok") and isinstance(result.get("parsed"), Mapping):
            ballots.append(dict(result["parsed"]))
        else:
            failures.append(result.get("failure") or {
                "message": str(result.get("error") or "defense vote failed"),
                "error_type": str(
                    result.get("error_type") or "provider_error"
                ),
                **({"raw": result["raw"]} if "raw" in result else {}),
            })

    # Independent ballots, cast concurrently; index order is preserved so the
    # returned sample does not depend on completion order (see
    # ``parallel_ordered``). Defense was the second serial vote in every
    # convicted case, so this halves the serial round trips a case waits on.
    parallel = max(1, int((route.get("vote_concurrency") or votes) or 1))
    for outcome in parallel_ordered(cast_at, votes, workers=parallel):
        if outcome is not None:
            absorb(outcome)
    while len(ballots) < votes and topup_budget > 0:
        topup_budget -= 1
        topups_used += 1
        absorb(cast_at(0))
    quorum = required_quorum(votes)
    if len(ballots) < quorum:
        failure_types = {failure["error_type"] for failure in failures}
        error_type = (next(iter(failure_types))
                      if not ballots and len(failure_types) == 1 else "no_quorum")
        reason = (
            f"defense quorum not reached: {len(ballots)} valid ballot(s) of "
            f"{votes} (need {quorum})"
        )
        sample_meta["vote"] = {
            "votes": votes, "cast": len(ballots),
            "verdicts": [str(b.get("verdict") or "") for b in ballots],
            "collapsed": "inconclusive", "tie": True, "topups": topups_used,
            "quorum": quorum, "quorum_met": False,
        }
        return {
            "ok": False, "parsed": None, "error": reason, "error_type": error_type,
            "raw": ballots, "vote_failures": failures, "provider": sample_meta,
        }, {
            "stage": "defense", "state": error_type,
            "attempts": total_attempts, "view": "unknown",
            "warnings": warnings,
        }
    verdicts = [str(ballot.get("verdict") or "") for ballot in ballots]
    collapsed, tied = _vote_verdict(verdicts)
    if collapsed == UPGRADED_SUBSTANTIATED:
        anchor = next(
            (b for b in ballots if b.get("verdict") == "substantiated"),
            ballots[0],
        )
        # The adjudication contract (adjudication.py) states: "A defense
        # that only half-convicts (substantiated_upgraded) has no confirmed
        # step to disagree with: its contract forces the field to null."
        # Leaking the anchor's confirmed_step causes false localization
        # conflicts and degrades authority on rows that should be clean.
        parsed = {
            **anchor,
            "verdict": UPGRADED_SUBSTANTIATED,
            "confirmed_step": None,
        }
    elif collapsed == "inconclusive":
        # No strict majority and no substantiated ballot: synthesize the
        # degraded, non-overturning verdict instead of adopting an arbitrary
        # ballot (the previous behaviour made the outcome order-dependent).
        reason = next(
            (str(b.get("reason") or "") for b in ballots if b.get("reason")),
            "",
        )
        parsed = {
            "verdict": "inconclusive",
            "confirmed_step": None,
            "evidence_refs": [],
            "reason": reason or "defense vote had no strict majority",
        }
    else:
        parsed = next(
            (b for b in ballots if b.get("verdict") == collapsed),
            ballots[0],
        )
    sample_meta["vote"] = {
        "votes": votes, "cast": len(verdicts),
        "verdicts": verdicts, "collapsed": collapsed, "tie": tied,
        "topups": topups_used, "quorum": quorum, "quorum_met": True,
    }
    if ballots_usage:
        sample_meta["usage_total"] = ballots_usage
    return {"ok": True, "parsed": parsed, "raw": ballots,
            "provider": sample_meta, "vote_failures": failures}, {
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
    snapshot: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Review one positive preliminary verdict.

    Terminal failures (a fatal ``ProviderError``) propagate so the caller can
    classify the row; recoverable transport errors are absorbed into an
    abstaining ballot. The name means "this stage does not decide by raising
    through its own logic" -- it is not a promise that no exception can leave
    it (compare ``detector.judge``, which states the same rule).
    """
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
        record=False,
    )
    force_full_view = "require_full_view_defense" in pre_actions
    system = DEFENSE_SYSTEM + plan.prompt_block(packet_features(packet), hook="defense.system")
    # The external-evidence snapshot must reach the defense as well: a
    # stage that decided on stale trajectory evidence alone would convict
    # or clear on facts the upgrade stage already had.
    snapshot_block = (
        "\n\n" + render_evidence_block(snapshot) if snapshot is not None else ""
    )
    allegation = (
        "POSITIVE VERDICT:\n"
        f"alleged_step={facts['step']}\n"
        f"reason={facts['reason']}\n"
        f"delivered_result={facts['delivered_result']}\n"
        f"correct_result={facts['correct_result']}\n"
        + evidence_block
    )

    def user_for(view_text: str, *, auxiliary: bool = True) -> str:
        return (
            "Review the positive verdict below using only the shown "
            "trajectory evidence.\n\n" + allegation + "\n" + view_text
            + snapshot_block + (referent_source_block(packet, positive, snapshot) + task_source_block(packet)
                                if auxiliary else "")
        )

    def vote(view_text: str, fallback_text: str | None = None):
        user = user_for(view_text)
        return memo_stage("defense", {"packet": packet, "route": route, "system": system,
                                     "user": user, "positive": positive, "snapshot": snapshot,
                                     "fallback_text": fallback_text},
                          lambda: _vote_defense(packet=packet, route=route, system=system,
                                               user=user, positive=positive, snapshot=snapshot,
                                               fallback_user=user_for(fallback_text if fallback_text is not None else view_text, auxiliary=False)))

    if force_full_view:
        view_text, view_meta = render_judge_view(packet)
    else:
        view_text, view_meta = render_defense_view(
            packet, facts["step"], evidence_refs=facts["evidence_refs"],
        )
    base_text = view_text
    view_text, view_meta = expand_view(packet, view_text, view_meta, positive=positive)
    result, status = vote(view_text, base_text if view_text != base_text else None)
    if ("evidence_expansion" in view_meta
            and any(row.get("error") == "evidence_expansion:provider_context_limit" for row in status.get("warnings", []))):
        view_meta["evidence_expansion"]["actual_view"] = "base_projection_in_one_or_more_ballots"
    status["view"] = (
        "complete"
        if not view_meta.get("packet_truncated") and not view_meta.get("degraded")
        else "degraded"
    )
    if result.get("ok"):
        verdict = (result.get("parsed") or {}).get("verdict")
        if verdict == "inconclusive" and view_meta.get("omitted_event_ids"):
            full_text, full_meta = render_judge_view(packet)
            base_full_text = full_text
            full_text, full_meta = expand_view(packet, full_text, full_meta, positive=positive)
            result2, status2 = vote(full_text, base_full_text if full_text != base_full_text else None)
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
