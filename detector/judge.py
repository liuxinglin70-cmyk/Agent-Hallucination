"""Stage 1: Outcome Judge.

Single end-to-end judgement over the full trajectory. It is the ONLY recall
source in the detector; later stages can only overturn a positive with
evidence, never invent a conviction.
"""

from __future__ import annotations

from typing import Any, Mapping

from detector.interventions import InterventionPlan, empty_plan
from detector.packet import (
    evidence_refs_support_step,
    packet_features,
    packet_reference_error,
    render_judge_view,
)
from detector.provider import (
    ProviderError,
    accumulate_usage,
    majority_vote,
    provider_call,
    raise_if_terminal,
    required_quorum,
)
from detector.runtime import parallel_ordered
from detector.semantics import (
    CONTRACT_KINDS,
    JUDGE_BASIS_VALUES,
    JUDGE_CONFIDENCE_VALUES,
    JUDGE_CONTRACT_REPAIR,
    JUDGE_SYSTEM,
)


def _parse_judge(parsed: Any, packet: Mapping[str, Any]) -> tuple[dict | None, str]:
    if not isinstance(parsed, Mapping):
        return None, "judge output is not an object"
    flag = parsed.get("is_hallucination")
    if not isinstance(flag, bool):
        return None, "is_hallucination must be a boolean"
    step_value = parsed.get("hallucination_step")
    reason = str(parsed.get("hallucination_reason") or "").strip()
    confidence = str(parsed.get("confidence") or "").strip()
    if confidence not in JUDGE_CONFIDENCE_VALUES:
        return None, f"confidence {confidence!r} outside high|medium|low"
    contracts = parsed.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        return None, "contracts must be a non-empty list"
    clean_contracts = []
    for item in contracts:
        if not isinstance(item, Mapping):
            return None, "contract entry is not an object"
        kind = str(item.get("kind") or "")
        requirement = str(item.get("requirement") or "").strip()
        if kind not in CONTRACT_KINDS:
            return None, f"contract kind {kind!r} outside the closed vocabulary"
        if not requirement:
            return None, "contract requirement is empty"
        clean_contracts.append({"kind": kind, "requirement": requirement})
    refs = parsed.get("evidence_refs")
    if refs is None:
        refs = []
    reference_error = packet_reference_error(packet, refs)
    if reference_error:
        return None, reference_error
    root_cause = parsed.get("root_cause")
    if flag:
        if not isinstance(step_value, int) or isinstance(step_value, bool):
            return None, "hallucination_step must be an integer"
        event_count = len(list(packet.get("events") or []))
        if step_value < 1 or step_value > event_count:
            return None, (
                f"hallucination_step {step_value} outside 1..{event_count}"
            )
        if not reason:
            return None, "hallucination_reason is required when true"
        if not refs:
            return None, "evidence_refs is required when true"
        if not evidence_refs_support_step(packet, refs, step_value):
            return None, "evidence_refs do not support hallucination_step"
        if not isinstance(root_cause, Mapping):
            return None, "root_cause object is required when true"
        root_step = root_cause.get("step")
        if not isinstance(root_step, int) or isinstance(root_step, bool):
            return None, "root_cause.step must be an integer"
        if root_step != step_value:
            return None, "root_cause.step must equal hallucination_step"
        if not str(root_cause.get("quote") or "").strip():
            return None, "root_cause.quote is required"
        if not str(root_cause.get("referent") or "").strip():
            return None, "root_cause.referent is required"
    else:
        if step_value is not None:
            return None, "hallucination_step must be null when false"
        if reason:
            return None, "hallucination_reason must be null when false"
        if refs:
            return None, "evidence_refs must be empty when false"
        if root_cause not in (None, {}):
            return None, "root_cause must be null when false"
        root_cause = None
    basis = parsed.get("basis")
    if basis is not None and not isinstance(basis, str):
        # Guard the type before the closed-vocabulary test: a list/dict here
        # raised ``TypeError: unhashable type`` from the ``in`` check, which
        # escaped the vote loop and aborted the whole batch instead of
        # abstaining one ballot.
        return None, f"basis {basis!r} must be a string"
    if basis not in (None, "") and basis not in JUDGE_BASIS_VALUES:
        return None, f"basis {basis!r} outside the closed vocabulary"
    root_clean = {
        "step": int(root_cause.get("step")),
        "quote": str(root_cause.get("quote") or "").strip(),
        "referent": str(root_cause.get("referent") or "").strip(),
    } if flag else None
    return {
        "is_hallucination": flag,
        "hallucination_step": step_value if flag else None,
        "hallucination_reason": reason if flag else None,
        "delivered_result": str(parsed.get("delivered_result") or "").strip(),
        "correct_result": str(parsed.get("correct_result") or "").strip(),
        "basis": str(basis) if basis else None,
        "confidence": confidence,
        "evidence_refs": refs if flag else [],
        "root_cause": root_clean,
        "contracts": clean_contracts,
    }, ""


def _cast_vote(
    *,
    packet: Mapping[str, Any],
    route: Mapping[str, Any],
    system: str,
    user: str,
) -> tuple[dict | None, dict[str, Any] | None, list[dict[str, str]]]:
    """A recoverable failed ballot abstains; terminal errors propagate."""
    warnings: list[dict[str, str]] = []

    def abstain(message: str) -> tuple:
        warnings.append({"attempt": "vote", "error": message})
        return None, None, list(warnings)

    try:
        parsed, meta = provider_call(
            route=route, system=system, user=user, purpose="judge",
        )
    except ProviderError as exc:
        raise_if_terminal(exc)
        return abstain(exc.message)
    result, error = _parse_judge(parsed, packet)
    if result is None:
        warnings.append({"attempt": "initial", "error": f"contract:{error}"})
        try:
            parsed, meta = provider_call(
                route=route, system=system,
                user=user + JUDGE_CONTRACT_REPAIR,
                purpose="judge_repair",
            )
            result, error = _parse_judge(parsed, packet)
        except ProviderError as exc:
            raise_if_terminal(exc)
            return abstain(exc.message)
    if result is None:
        warnings.append({"attempt": "final", "error": f"contract:{error}"})
    return result, meta, list(warnings)


def run_judge(
    *,
    packet: Mapping[str, Any],
    config: Mapping[str, Any],
    plan: InterventionPlan | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the judge; returns ``(judge_result, stage_status)``.

    Recoverable provider or contract failures return an abstention status.
    Terminal failures propagate with their classification. When the route requests
    more than one vote, independent samples decide ``is_hallucination``
    by strict majority; a tie or no quorum keeps the negative verdict and
    the stage degrades, because a positive verdict is the extraordinary
    claim. ``vote_topup`` re-casts abstained ballots a bounded number of
    times so transport jitter cannot shrink the quorum between runs.
    """
    plan = plan or empty_plan()
    route = dict(config["judge_route"])
    route["_route_name"] = "judge"
    view_text, view_meta = render_judge_view(packet)
    system = JUDGE_SYSTEM + plan.prompt_block(packet_features(packet), hook="judge.system")
    user = (
        "Judge the trajectory below. Do not use any information that is not "
        "in the question or the trajectory.\n\n" + view_text
    )
    view_state = "complete" if not view_meta.get("degraded") else "degraded"

    votes = max(1, int(route.get("votes", 1) or 1))
    topup_budget = max(0, int(route.get("vote_topup", 0) or 0))
    ballots: list[dict[str, Any]] = []
    sample_meta: dict[str, Any] = {}
    # ``sample_meta`` follows the last ballot, so its usage is one ballot's.
    # The total is what the stage actually cost and is the only honest input
    # for sizing a run (see ``accumulate_usage``).
    ballots_usage: dict[str, Any] = {}
    total_attempts = 0
    warnings: list[dict[str, str]] = []
    topups_used = 0

    # Independent ballots are exactly that, so they are cast concurrently: the
    # vote count is a majority requirement, not a serial protocol. Order is
    # preserved (see ``parallel_ordered``) so the returned sample does not
    # depend on which ballot happened to finish first.
    def cast_at(_index: int) -> tuple[Any, Any, list]:
        return _cast_vote(
            packet=packet, route=route, system=system, user=user,
        )

    def absorb(result: tuple[Any, Any, list]) -> None:
        nonlocal total_attempts, sample_meta
        parsed, meta, vote_warnings = result
        total_attempts += int((meta or {}).get("attempts") or 0)
        warnings.extend(vote_warnings)
        if parsed is not None:
            ballots.append(parsed)
            sample_meta = dict(meta or {})
            accumulate_usage(ballots_usage, (meta or {}).get("usage") or {})

    parallel = max(1, int((route.get("vote_concurrency") or votes) or 1))
    outcomes = parallel_ordered(cast_at, votes, workers=parallel)
    for outcome in outcomes:
        absorb(outcome)
    while len(ballots) < votes and topup_budget > 0:
        topup_budget -= 1
        topups_used += 1
        absorb(cast_at(0))
    if not ballots:
        return {
            "ok": False,
            "parsed": None,
            "error": "every judge vote failed",
            "error_type": "provider_error",
        }, {
            "stage": "judge", "state": "provider_error",
            "attempts": total_attempts, "view": view_state,
            "warnings": warnings,
        }

    quorum = required_quorum(votes)
    if len(ballots) < quorum:
        # Fewer valid ballots than a majority of the configured votes: the
        # stage could not decide honestly. One surviving ballot must never
        # stand in for the whole vote (see ``required_quorum``). This is a
        # judge failure, so the case resolves to unresolved/invalid rather
        # than a silently degraded verdict.
        return {
            "ok": False,
            "parsed": None,
            "error": (
                f"judge quorum not reached: {len(ballots)} valid ballot(s) "
                f"of {votes} (need {quorum})"
            ),
            "error_type": "provider_error",
        }, {
            "stage": "judge", "state": "provider_error",
            "attempts": total_attempts, "view": view_state,
            "warnings": warnings,
        }

    if votes == 1:
        result = ballots[0]
        vote_meta: dict[str, Any] = {"votes": 1}
    else:
        winner, cast = majority_vote(
            [bool(ballot.get("is_hallucination")) for ballot in ballots],
            on_tie=False,
        )
        positives = sum(
            1 for ballot in ballots if ballot.get("is_hallucination")
        )
        # A positive verdict needs a strict majority; an undecided vote
        # keeps the negative verdict, the less extraordinary claim.
        vote_meta = {
            "votes": votes, "cast": cast, "positives": positives,
            "winner": winner,
            "tie": not (winner or positives * 2 < cast),
        }
        result = next(
            (
                ballot for ballot in ballots
                if bool(ballot.get("is_hallucination")) == winner
            ),
            ballots[0],
        )
    if topups_used:
        vote_meta["topups"] = topups_used
    sample_meta["vote"] = vote_meta
    if ballots_usage:
        # Keep the last ballot's own usage under ``usage`` (existing readers and
        # the truncation message depend on it) and add the stage total beside
        # it, so the undercount is corrected without changing the old field.
        sample_meta["usage_total"] = ballots_usage
    return {
        "ok": True,
        "parsed": result,
        "raw": ballots if votes > 1 else ballots[0],
        "provider": sample_meta,
        "view_meta": view_meta,
    }, {
        "stage": "judge",
        "state": "ok",
        "attempts": total_attempts,
        "view": view_state,
        "warnings": warnings,
    }


__all__ = ["run_judge"]
