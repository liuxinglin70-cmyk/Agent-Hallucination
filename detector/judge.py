"""Outcome judgment over the complete task trajectory."""

from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
from typing import Any, Mapping
from detector.action_forecast import (
    FORECAST_JUDGE_EXTENSION, action_comparisons, build_action_forecast, comparison_prompt, comparison_health, repair_comparisons,
)

from detector.interventions import InterventionPlan, empty_plan
from detector.packet import (
    evidence_refs_support_step,
    event_id_for_step,
    packet_features,
    packet_quote_error,
    packet_reference_error,
    render_judge_view,
)
from detector.stage_retry import memo_stage
from detector.evidence_view import task_source_block
from detector.provider import (
    ProviderError,
    accumulate_usage,
    merge_provider_meta,
    majority_vote,
    provider_call,
    raise_if_terminal,
    required_quorum,
)
from detector.runtime import AddedBudgetExceeded, OptionalBudget, added_budget_scope, current_added_budget, parallel_ordered
from detector.config import diagnostic_budget_reserve
from detector.semantics import (
    contract_repair_feedback,
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
        quote_error = packet_quote_error(
            packet, root_cause.get("quote"),
            locus=event_id_for_step(packet, step_value), evidence_refs=refs,
        )
        if quote_error:
            return None, f"root_cause.{quote_error}"
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
    forecast: Mapping[str, Any] | None = None,
) -> tuple[dict | None, dict[str, Any] | None, list[dict[str, str]], dict | None]:
    """A recoverable failed ballot abstains; terminal errors propagate."""
    warnings: list[dict[str, str]] = []
    meta: dict[str, Any] = {}

    def abstain(exc: ProviderError) -> tuple:
        warnings.append({"attempt": "vote", "error": exc.message})
        return None, merge_provider_meta(meta, {
            "attempts": exc.attempts, "usage_total": exc.usage_total}), list(warnings), exc.as_dict()

    try:
        parsed, meta = provider_call(
            route=route, system=system, user=user, purpose="judge",
        )
    except ProviderError as exc:
        raise_if_terminal(exc)
        return abstain(exc)
    result, error = _parse_judge(parsed, packet)
    if result is None:
        initial_meta = dict(meta)
        warnings.append({"attempt": "initial", "error": f"contract:{error}"})
        try:
            parsed, meta = provider_call(
                route=route, system=system,
                user=user + JUDGE_CONTRACT_REPAIR + contract_repair_feedback(parsed, error),
                purpose="judge_repair",
            )
            meta = merge_provider_meta(initial_meta, meta)
            result, error = _parse_judge(parsed, packet)
        except ProviderError as exc:
            raise_if_terminal(exc)
            return abstain(exc)
    if result is None:
        warnings.append({"attempt": "final", "error": f"contract:{error}"})
        return None, meta, list(warnings), {
            "error_type": "contract_error", "message": error, "raw": parsed,
        }
    if forecast is not None:
        rows = action_comparisons(parsed, forecast, packet)
        result["next_action_comparisons"] = rows
        result["next_action_comparison_health"] = comparison_health(rows)
    return result, meta, list(warnings), None


def _run_judge(
    *,
    packet: Mapping[str, Any],
    config: Mapping[str, Any],
    plan: InterventionPlan | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the judge; returns ``(judge_result, stage_status)``.

    Recoverable provider or contract failures return an abstention status.
    Terminal failures propagate with their classification. When the route requests
    more than one vote, independent samples decide ``is_hallucination``
    by strict majority; a tie keeps the negative verdict -- a positive verdict
    is the extraordinary claim and must carry a majority -- while the stage
    itself records ``state: "ok"`` and the tie in ``provider.vote.tie``. The
    tie is a reading about the ensemble's uncertainty, and it is the caller's
    rule that acts on it: ``Detector._judge_negative_needs_recall`` forces the
    recall net for a tied or split vote instead of accepting a 3/3-or-better
    clean verdict. ``vote_topup`` re-casts abstained ballots a bounded number
    of times so transport jitter cannot shrink the quorum between runs.
    """
    plan = plan or empty_plan()
    route = dict(config["judge_route"])
    route["_route_name"] = "judge"
    view_text, view_meta = render_judge_view(packet)
    system = JUDGE_SYSTEM + plan.prompt_block(packet_features(packet), hook="judge.system")
    user = (
        "Judge the trajectory below. Do not use any information that is not "
        "in the question or the trajectory.\n\n" + view_text + task_source_block(packet)
    )
    forecast = build_action_forecast(packet, config=config)
    forecast_block = comparison_prompt(forecast, packet)
    base_user = user
    base_system = system
    user += forecast_block
    if forecast_block:
        system += FORECAST_JUDGE_EXTENSION
    view_state = "complete" if not view_meta.get("degraded") else "degraded"

    def evaluate_votes(active_user, active_forecast, active_system):
        votes = max(1, int(route.get("votes", 1) or 1))
        topup_budget = max(0, int(route.get("vote_topup", 0) or 0))
        ballots: list[dict[str, Any]] = []
        sample_meta: dict[str, Any] = {}
        ballots_usage: dict[str, Any] = {}
        total_attempts = 0
        warnings: list[dict[str, str]] = []
        failures: list[dict[str, Any]] = []
        topups_used = 0

        # Independent ballots are exactly that, so they are cast concurrently: the
        # vote count is a majority requirement, not a serial protocol. Order is
        # preserved (see ``parallel_ordered``) so the returned sample does not
        # depend on which ballot happened to finish first.
        def cast_at(_index: int) -> tuple:
            return _cast_vote(
                packet=packet, route=route, system=active_system, user=active_user,
                **({"forecast": active_forecast} if active_forecast is not None else {}),
            )

        def absorb(result: tuple) -> None:
            nonlocal total_attempts, sample_meta
            parsed, meta, vote_warnings, failure = result
            total_attempts += int((meta or {}).get("attempts") or 0)
            accumulate_usage(ballots_usage, (meta or {}).get("usage_total") or (meta or {}).get("usage") or {})
            if not sample_meta and meta:
                sample_meta = dict(meta)
            if ballots_usage:
                sample_meta["usage_total"] = dict(ballots_usage)
            warnings.extend(vote_warnings)
            if failure is not None:
                failures.append(failure)
            if parsed is not None:
                ballots.append(parsed)
                sample_meta = dict(meta or {})

        parallel = max(1, int((route.get("vote_concurrency") or votes) or 1))
        outcomes = parallel_ordered(cast_at, votes, workers=parallel)
        for outcome in outcomes:
            absorb(outcome)
        while len(ballots) < votes and topup_budget > 0:
            topup_budget -= 1
            topups_used += 1
            absorb(cast_at(0))
        if not ballots:
            failure_types = {failure["error_type"] for failure in failures}
            error_type = next(iter(failure_types)) if len(failure_types) == 1 else "no_quorum"
            return {
                "ok": False,
                "parsed": None,
                "error": "every judge vote failed",
                "error_type": error_type,
                "vote_failures": failures,
                "provider": {**sample_meta, "usage_total": ballots_usage},
            }, {
                "stage": "judge", "state": error_type,
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
                "error_type": "no_quorum",
                "vote_failures": failures,
                "provider": {**sample_meta, "usage_total": ballots_usage},
            }, {
                "stage": "judge", "state": "no_quorum",
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
            if not winner:
                # Recall may be skipped only if every supporting negative is certain.
                # Keep raw ballots intact so the ensemble remains auditable.
                result = deepcopy(result)
                confidence_rank = {"low": 0, "medium": 1, "high": 2}
                result["confidence"] = min(
                    (ballot["confidence"] for ballot in ballots
                     if ballot["is_hallucination"] is False),
                    key=confidence_rank.__getitem__,
                )
        if topups_used:
            vote_meta["topups"] = topups_used
        sample_meta["vote"] = vote_meta
        if ballots_usage:
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

    def evaluate():
        used = bool(forecast_block)
        budget = current_added_budget()
        if used and budget is not None:
            try:
                # 修复 P0-1: forecast_block 在所有投票间复用（通过 memo_stage 缓存）
                # 预算预检应该只预留 1× 成本，而非 votes× 成本
                # 参考: next-action-prediction-audit-report.md 第5.2节
                OptionalBudget(budget, diagnostic_budget_reserve(config.get("causal_audit") or {})).reserve_context(
                    forecast_block + FORECAST_JUDGE_EXTENSION)
            except AddedBudgetExceeded:
                used = False
        result, status = evaluate_votes(user if used else base_user, forecast if used else None,
                                        system if used else base_system)
        if forecast is not None:
            result["action_forecast_used"] = used
            repair = {"state": "not_requested"}
            if used and result.get("ok"):
                # One repair per case, not one per ballot: the comparison is a
                # property of the recorded next action and the shared forecast,
                # so re-asking it three times only triples the cost.  A repaired
                # row replaces only the rows that failed structural validation in
                # each ballot; a ballot's own valid comparison is never
                # overwritten, so the ensemble's divergence count is preserved.
                policy = config.get("causal_audit") or {}
                votes = result.get("raw")
                votes = votes if isinstance(votes, list) else [votes]
                invalid: dict[str, dict[str, Any]] = {}
                for ballot in votes:
                    if not isinstance(ballot, Mapping):
                        continue
                    for row in ballot.get("next_action_comparisons") or []:
                        if row.get("validation_state") != "valid":
                            invalid.setdefault(row["constraint_id"], row)
                representative = list(invalid.values()) or (result["parsed"].get("next_action_comparisons") or [])
                repaired, repair = repair_comparisons(
                    representative, forecast=forecast, packet=packet, route=route, policy=policy)
                by_id = {row["constraint_id"]: row for row in repaired
                         if row["validation_state"] == "valid"}
                merged = []
                for ballot in votes:
                    if not isinstance(ballot, Mapping):
                        merged.append(ballot)
                        continue
                    rows = [by_id.get(row.get("constraint_id"), row)
                            if row.get("validation_state") != "valid" else row
                            for row in ballot.get("next_action_comparisons") or []]
                    merged.append({**ballot, "next_action_comparisons": rows,
                                   "next_action_comparison_health": {
                                       **comparison_health(rows), "repair": repair}})
                result["raw"] = merged
                result["parsed"] = {**result["parsed"],
                                    "next_action_comparisons": merged[0]["next_action_comparisons"]
                                    if merged and isinstance(merged[0], Mapping) else representative,
                                    "next_action_comparison_health": {
                                        **comparison_health(
                                            merged[0]["next_action_comparisons"]
                                            if merged and isinstance(merged[0], Mapping) else representative),
                                        "repair": repair}}
        return result, status

    result, status = memo_stage('judge', {"packet": packet, "route": route, "system": system, "user": user, "view_meta": view_meta}, evaluate)
    if forecast is not None:
        result = {**result, "action_forecast": forecast}
    return result, status


def run_judge(*, packet: Mapping[str, Any], config: Mapping[str, Any],
              plan: InterventionPlan | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """运行 judge 阶段并直接构建 action_candidates。

    修复 P0-1: 在 judge 投票完成后立即构建 action_candidates，
    使用已验证的 ballot 数据，避免重复验证导致的不一致。
    """
    policy = config.get("causal_audit")
    with added_budget_scope(policy) if policy is not None else nullcontext():
        result, status = _run_judge(packet=packet, config=config, plan=plan)

    # 修复 P0-1: 在投票完成后立即构建 action_candidates
    # 此时 result["raw"] 包含所有 ballot 的原始数据（含 next_action_comparisons）
    # 确保使用的是同一份已验证的数据，避免重复调用 action_comparisons 导致不一致
    if result.get("ok") and result.get("action_forecast") is not None:
        try:
            from detector.action_evidence import collect_action_candidates_from_ballots
            # 传入已验证的 ballots，而不是让 collect 函数重新提取和验证
            result["action_candidates"] = collect_action_candidates_from_ballots(
                packet, result, policy or {})
        except ValueError as exc:
            # 修复JUDGE-001: ValueError通常表示契约/身份检查失败，这是系统错误
            # 应该记录并诊断，但不应该silently设置为空列表
            result["action_candidates"] = []
            status["warnings"] = status.get("warnings", [])
            status["warnings"].append({
                "component": "action_candidates",
                "error": str(exc),
                "severity": "contract_error"  # 清晰标记为契约错误
            })
        except Exception as exc:
            # 其他异常（如网络、超时等）也要标记为degraded
            result["action_candidates"] = []
            status["warnings"] = status.get("warnings", [])
            status["warnings"].append({
                "component": "action_candidates",
                "error": str(exc),
                "severity": "degraded"
            })
    else:
        result["action_candidates"] = []

    return result, status


__all__ = ["run_judge"]
