"""Read-only action hypotheses routed through the existing content and materiality gates."""

from copy import deepcopy
import json
from typing import Any, Mapping

from detector.action_context import action_channels, forecast_prefix
from detector.action_forecast import FORECAST_SCHEMA, _constraints, action_comparisons, forecast_checks, prefix_view
from detector.causal_contracts import arithmetic_check, bound_fields, numeric_literal, seal, validate_seal
from detector.config import diagnostic_budget_reserve
from detector.identity import digest
from detector.packet import packet_quote_error, packet_reference_error
from detector.provider import required_quorum
from detector.runtime import AddedBudgetExceeded, OptionalBudget, current_added_budget


ACTION_EVIDENCE_SCHEMA = "detector.action_candidate_evidence.v1"


def _consumption(packet, source, target, interaction):
    check = arithmetic_check(interaction)
    if check is None or check["status"] != "deviation":
        return None
    fields, clipped = bound_fields(target.get("content"))
    matches = [field for field in fields
               if field["source_ref"] == interaction["interaction_id"] and field["entity"] == "result"
               and field["attribute"] == "value" and field["unit"] is None and field["time"] is None
               and numeric_literal(field["value"]) == numeric_literal(check["observed"])]
    if clipped or len(matches) != 1 or target.get("role") not in {None, "assistant"}:
        return None
    return {"certificate": check, "field": matches[0], "origin_event_id": source["event_id"],
            "adoption_event_id": target["event_id"], "delivery_event_id": None}


def validate_action_evidence(packet: Mapping[str, Any], evidence: Mapping[str, Any]) -> None:
    validate_seal(evidence, schema=ACTION_EVIDENCE_SCHEMA)
    allowed = {"schema_version", "packet_digest", "source_event_id", "next_event_id", "producer",
               "constraint_id", "constraint", "votes", "witness", "quote", "evidence_refs",
               "verification_status", "decision_authority", "localization_authority", "digest"}
    if (set(evidence) - allowed or evidence["packet_digest"] != digest(packet)
            or evidence.get("decision_authority") != "none" or evidence.get("localization_authority") != "none"
            or evidence.get("verification_status") != "hypothesis"):
        raise ValueError("action evidence identity or authority differs")
    events = {event["event_id"]: event for event in packet["events"]}
    source, target = events.get(evidence["source_event_id"]), events.get(evidence["next_event_id"])
    if source is None or target is None or target["position"] != source["position"] + 1:
        raise ValueError("action evidence event order differs")
    if packet_reference_error(packet, evidence["evidence_refs"], required=True):
        raise ValueError("invalid action evidence references")
    if evidence["producer"] == "model_comparison":
        prefix, binding = forecast_prefix(packet, source["position"])
        if binding["state"] == "unknown":
            raise ValueError("action evidence task turn is unaligned")
        _constraints({"constraints": [evidence["constraint"]]}, prefix, prefix_view(packet, source["position"]))
        votes = evidence["votes"]
        if (any(type(votes.get(key)) is not int for key in ("requested", "cast", "valid", "divergent"))
                or not 1 <= votes["requested"] or not 0 < votes["divergent"] <= votes["valid"] <= votes["cast"] <= votes["requested"]
                or votes["valid"] < required_quorum(votes["requested"])):
            raise ValueError("action evidence comparison quorum differs")
        quote = evidence["quote"]
        constraint_ref = evidence["constraint"]["evidence_ref"]
        if (not isinstance(quote, str) or not quote.strip()
                or not any(quote in text for _, _, text in action_channels(target))
                or (constraint_ref != "question" and constraint_ref not in evidence["evidence_refs"])
                or packet_quote_error(packet, quote, locus=target["event_id"], evidence_refs=evidence["evidence_refs"])):
            raise ValueError("action evidence action quote differs")
    elif evidence["producer"] == "literal_arithmetic":
        witnesses = [_consumption(packet, source, target, interaction) for interaction in source["tool_interactions"]]
        if evidence.get("witness") is None or evidence["witness"] not in witnesses:
            raise ValueError("action arithmetic witness differs")
        if not {target["event_id"], evidence["witness"]["certificate"]["evidence_refs"][0]} <= set(evidence["evidence_refs"]):
            raise ValueError("action arithmetic witness references differ")
        if packet_quote_error(packet, evidence["quote"], locus=source["event_id"], evidence_refs=evidence["evidence_refs"]):
            raise ValueError("action evidence source quote differs")
    else:
        raise ValueError("unsupported action evidence producer")


def collect_action_candidates_from_ballots(packet, judge_result, policy):
    """从已验证的投票中收集行动候选项。

    修复 P0-1: 使用 Judge 阶段已经验证过的 ballot 数据，避免重复调用
    action_comparisons() 导致的不一致问题。

    关键改进：
    1. 直接使用 ballot 中已有的 next_action_comparisons（已验证过）
    2. 不再重新调用 action_comparisons() 进行二次验证
    3. 确保数据流一致性：投票时验证 → 直接使用验证结果

    修复P0-1b: 增加检查确保next_action_comparisons实际存在
    - 如果forecast存在但forecast_used=False，ballots中不会有comparisons
    - 需要检查至少一个ballot中有有效的comparisons，否则返回[]
    """
    forecast = judge_result.get("action_forecast")
    if (policy.get("next_action_mode") != "judge_context"
            or not judge_result.get("ok")
            or not forecast):
        return []

    # 修复P0-1b: 检查forecast_used标志，确保forecast确实被用于judge
    # forecast_used=False意味着forecast_block未被包含在judge提示中
    # 此时ballots中的next_action_comparisons将为空/不存在
    if not judge_result.get("action_forecast_used", True):
        # forecast存在但未被使用在judge阶段，无法收集有效的candidates
        return []
    validate_seal(forecast, schema=FORECAST_SCHEMA)
    if forecast["packet_digest"] != digest(packet) or forecast["policy_digest"] != digest(policy):
        raise ValueError("action forecast identity differs")
    ballots = judge_result.get("raw") or []
    ballots = ballots if isinstance(ballots, list) else [ballots]
    candidates = []

    def append(check, producer, quote, refs, claim, *, votes=None, witness=None):
        evidence = seal({"schema_version": ACTION_EVIDENCE_SCHEMA, "packet_digest": digest(packet),
                         "source_event_id": check["source_event_id"], "next_event_id": check["next_event_id"],
                         "constraint_id": check["constraint_id"], "producer": producer,
                         "constraint": check.get("constraint"), "votes": votes, "witness": witness,
                         "quote": quote, "evidence_refs": list(dict.fromkeys(refs)),
                         "verification_status": "hypothesis", "decision_authority": "none", "localization_authority": "none"})
        validate_action_evidence(packet, evidence)
        candidates.append({"candidate_id": "fa_" + evidence["digest"][:16], "claim": claim,
                           "locus_event_id": check["source_event_id"] if witness else check["next_event_id"],
                           "evidence_refs": evidence["evidence_refs"], "quote": quote, "evidence_citations": [],
                           "action_evidence": [evidence]})

    checks = forecast_checks(forecast)
    for check in checks:
        if check["producer"] != "model_hypothesis":
            continue
        # 修复 P0-1: 直接使用 ballot 中已验证的 next_action_comparisons
        # 不再重新调用 action_comparisons()，避免双重验证和结果不一致
        valid = []
        for ballot in ballots:
            if not isinstance(ballot, Mapping):
                continue
            # 直接从 ballot 中提取已验证为 valid 的比较
            supplied = [row for row in ballot.get("next_action_comparisons", [])
                        if row.get("constraint_id") == check["constraint_id"]
                        and row.get("validation_state") == "valid"]
            # 不再重新验证，直接使用
            valid.extend(supplied)

        divergent = [row for row in valid if row["state"] == "divergent"]
        requested = int(((judge_result.get("provider") or {}).get("vote") or {}).get("votes", len(ballots)))

        # A candidate reopens the standard recall net, so it needs the same
        # strict majority the ensemble itself uses: one dissenting ballot among
        # three is not evidence enough to pay for another hearing, while a
        # majority divergence is a reproducible signal the Judge prompt did not
        # absorb.
        if valid and len(valid) >= required_quorum(max(1, requested)) and len(divergent) * 2 > len(ballots):
            selected = divergent[0]
            ref = check["constraint"]["evidence_ref"]
            append(check, "model_comparison", selected["quote"],
                   [*selected["evidence_refs"], *([ref] if ref != "question" else [])],
                   "Verify whether the recorded next action materially violates this prefix constraint (hypothesis): "
                   + check["constraint"]["requirement"],
                   votes={"requested": max(1, requested), "cast": len(ballots), "valid": len(valid), "divergent": len(divergent)})

    seen = set()
    events_list = list(packet["events"])
    # The literal-arithmetic witness is a property of adjacent recorded
    # events, not of any forecast target.  Scanning the packet directly keeps
    # it available no matter which step the model forecast happened to cover;
    # tying the scan to forecast checks silently dropped the witness whenever
    # the forecast targeted the risky event itself.
    for source, target in zip(events_list, events_list[1:]):
        for interaction in source["tool_interactions"]:
            witness = _consumption(packet, source, target, interaction)
            if witness is None or digest(witness) in seen:
                continue
            seen.add(digest(witness))
            cert = witness["certificate"]
            check = {"source_event_id": source["event_id"], "next_event_id": target["event_id"],
                     "constraint_id": "a_" + digest(witness)[:20], "producer": "literal_arithmetic"}
            append(check, "literal_arithmetic", str(interaction["observation"]),
                   [interaction["interaction_id"], target["event_id"]],
                   f"The calculator returned {cert['observed']} for {cert['expression']} instead of {cert['expected']}; "
                   "the next event explicitly copies that result. Independently verify whether it affects the delivered task.", witness=witness)
    return candidates


# 保留旧函数作为兼容层，但标记为deprecated
def collect_action_candidates(packet, judge_result, policy):
    """已弃用：使用 collect_action_candidates_from_ballots 代替。

    该函数存在双重验证问题，保留仅为向后兼容。
    """
    return collect_action_candidates_from_ballots(packet, judge_result, policy)


def linked_action_evidence(packet, candidates, positive):
    refs = set(positive.get("evidence_refs") or [])
    linked = []
    for candidate in candidates:
        if refs.intersection(candidate["evidence_refs"]):
            for evidence in candidate.get("action_evidence", []):
                validate_action_evidence(packet, evidence)
                if evidence not in linked:
                    linked.append(deepcopy(evidence))
    return linked


def action_evidence_block(packet, evidence, policy, *, copies=1):
    if not evidence:
        return "", {"state": "not_needed"}
    for row in evidence:
        validate_action_evidence(packet, row)
    block = ("\n\nACTION EVIDENCE HYPOTHESES (no verdict or localization authority):\n"
             + json.dumps(evidence, ensure_ascii=False)
             + "\nVerify the same alleged error against the task, actual channels, later repair and final delivery. "
             "Origin, adoption and delivery may differ. Constraints and model memory are not independent facts; "
             "missing corroboration alone is not falsehood. A local arithmetic witness proves only its exact scope.")
    parent = current_added_budget()
    if parent is None:
        return "", {"state": "not_admitted", "resource": "budget_unavailable"}
    if policy is None:
        return "", {"state": "not_admitted", "resource": "policy_unavailable"}
    try:
        OptionalBudget(parent, diagnostic_budget_reserve(policy)).reserve_context(block * copies)
        return block, {"state": "ok", "digest": digest(evidence)}
    except AddedBudgetExceeded as exc:
        return "", {"state": "not_admitted", "resource": exc.resource}
