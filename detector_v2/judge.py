"""Stage 1: Outcome Judge.

Single end-to-end judgement over the full trajectory. It is the ONLY recall
source in the detector; later stages can only overturn a positive with
evidence, never invent a conviction.
"""

from __future__ import annotations

from typing import Any, Mapping

from detector_v2.interventions import InterventionPlan, empty_plan
from detector_v2.packet import (
    evidence_refs_support_step,
    packet_features,
    render_judge_view,
)
from detector_v2.provider import ProviderError, majority_vote, provider_call
from detector_v2.semantics import (
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
    if not isinstance(refs, list) or any(
        not isinstance(x, str) for x in refs
    ):
        return None, "evidence_refs must be a list of event ids"
    valid_refs = {
        str(event.get("event_id") or "")
        for event in packet.get("events") or ()
        if isinstance(event, Mapping)
    }
    for event in packet.get("events") or ():
        if isinstance(event, Mapping):
            valid_refs.update(
                str(item.get("interaction_id") or "")
                for item in event.get("tool_interactions") or ()
                if isinstance(item, Mapping)
            )
    if any(ref not in valid_refs for ref in refs):
        return None, "evidence_refs contains an unknown packet event"
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
    view_state: str,
) -> tuple[dict | None, dict[str, Any] | None, bool, list[dict[str, str]]]:
    """One judge ballot; a ``None`` verdict means this ballot abstains.

    Returns ``(parsed_or_None, provider_meta_or_None, fatal, warnings)``
    where ``fatal`` marks a failure that makes the whole judge stage fail
    (a missing credential repeats for every vote). Ordinary contract
    violations and transport failures are repaired once and otherwise
    abstain — the remaining ballots still decide.
    """
    warnings: list[dict[str, str]] = []

    def abstain(message: str) -> tuple:
        warnings.append({"attempt": "vote", "error": message})
        return None, None, False, list(warnings)

    def fatal_error(message: str, attempts: int, error_type: str) -> tuple:
        warnings.append({"attempt": "vote", "error": message})
        return None, None, True, list(warnings)

    try:
        parsed, meta = provider_call(
            route=route, system=system, user=user, purpose="judge",
        )
    except ProviderError as exc:
        if exc.error_type == "missing_credential":
            return fatal_error(exc.message, exc.attempts, "provider_error")
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
            if exc.error_type == "missing_credential":
                return fatal_error(exc.message, exc.attempts, "provider_error")
            return abstain(exc.message)
    if result is None:
        warnings.append({"attempt": "final", "error": f"contract:{error}"})
    return result, meta, False, list(warnings)


def run_judge(
    *,
    packet: Mapping[str, Any],
    config: Mapping[str, Any],
    plan: InterventionPlan | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the judge; returns ``(judge_result, stage_status)``.

    ``judge_result`` always carries ``ok`` and ``parsed``. A provider or
    contract failure is returned as status, never raised: only the judge
    failure can make the whole case unresolved. When the route requests
    more than one vote, independent samples decide ``is_hallucination``
    by strict majority; a tie or no quorum keeps the negative verdict and
    the stage degrades, because a positive verdict is the extraordinary
    claim.
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
    ballots: list[dict[str, Any]] = []
    sample_meta: dict[str, Any] = {}
    total_attempts = 0
    warnings: list[dict[str, str]] = []
    fatal_error = ""
    for _ in range(votes):
        parsed, meta, fatal, vote_warnings = _cast_vote(
            packet=packet, route=route, system=system, user=user,
            view_state=view_state,
        )
        total_attempts += int((meta or {}).get("attempts") or 0)
        warnings.extend(vote_warnings)
        if fatal:
            fatal_error = vote_warnings[-1]["error"] if vote_warnings else "judge vote failed"
            break
        if parsed is not None:
            ballots.append(parsed)
            sample_meta = dict(meta or {})
    if fatal_error:
        return {
            "ok": False,
            "parsed": None,
            "error": fatal_error,
            "error_type": "provider_error",
        }, {
            "stage": "judge", "state": "provider_error",
            "attempts": total_attempts, "view": view_state,
            "warnings": warnings,
        }
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
    sample_meta["vote"] = vote_meta
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
