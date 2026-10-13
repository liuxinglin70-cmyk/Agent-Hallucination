"""Bounded diagnostic priorities, without factual or localization authority."""
import json
from typing import Any, Mapping

from detector.causal_contracts import seal, validate_seal
from detector.identity import digest
from detector.packet import packet_reference_error
from detector.step_uncertainty import build_step_risk_profile, select_causal_candidates


RISK_CONTEXT_SCHEMA = "detector.risk_context.v1"


def _excerpt(value: Any, needles: list[str]) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= 480:
        return text
    for needle in needles:
        if needle and needle in text:
            start = max(0, text.index(needle) - 100)
            return text[start:start + 480]
    return text[:300] + "\n[excerpt omitted]\n" + text[-150:]


def build_risk_context(
    packet: Mapping[str, Any], *, positive: Mapping[str, Any], policy: Mapping[str, Any],
) -> dict[str, Any] | None:
    if policy["mode"] != "tracer_context" or policy["score_source"] != "semantic_proxy":
        return None
    if packet_reference_error(packet, positive.get("evidence_refs") or [], required=True):
        return None
    events = {e["event_id"]: e for e in packet["events"]}
    mandatory = [eid for eid, e in events.items() if e["position"] == positive.get("step")]
    if not mandatory:
        return None
    profile = build_step_risk_profile(packet, policy=policy)
    selection = select_causal_candidates(profile, mandatory_event_ids=mandatory, policy=policy)
    if selection["unresolved_event_ids"]:
        return None
    risk = {r["event_id"]: r for r in profile["by_event"]}
    needles = [positive.get(key) for key in ("quote", "delivered_result")
               if isinstance(positive.get(key), str)]
    candidates = []
    for eid in selection["selected_event_ids"]:
        event = events[eid]
        channels = []
        if event.get("content") not in (None, ""):
            channels.append({"kind": "content", "reference": eid,
                             "excerpt": _excerpt(event["content"], needles)})
        for interaction in (event.get("tool_interactions") or [])[:2]:
            for key in ("arguments", "observation"):
                if interaction.get(key) not in (None, "", {}):
                    channels.append({"kind": key, "reference": interaction["interaction_id"],
                                     "excerpt": _excerpt(interaction[key], needles)})
        candidates.append({"event_id": eid, "position": event["position"],
                           "source_step_id": event["source_step_id"], "source_role": event.get("role"),
                           "score_status": risk[eid]["score_status"],
                           "priority": risk[eid]["priority"],
                           "signals": [{k: s[k] for k in ("signal", "verification_status", "evidence_refs")}
                                       for s in risk[eid]["risk_signals"][:8]],
                           "channels": channels,
                           "excerpted": True})
    payload = {"schema_version": RISK_CONTEXT_SCHEMA, "packet_digest": digest(packet),
               "policy_digest": digest(policy), "target_digest": digest(positive),
               "scope": "diagnostic_event_priority", "claim_equivalence": "unestablished",
               "decision_authority": "none", "localization_authority": "none",
               "score_source": "semantic_proxy", "profile_health": profile["health"],
               "candidates": candidates, "omitted_event_ids": list(dict.fromkeys([
                   *profile["omitted_event_ids"], *selection["omitted_event_ids"],
               ]))}
    limit = min(6000, policy["max_added_prompt_tokens_per_case"] // 2)
    while len(json.dumps(seal(payload), ensure_ascii=False).encode("utf-8")) > limit:
        if len(candidates) <= 1:
            return None
        removed = candidates.pop()
        payload["omitted_event_ids"].append(removed["event_id"])
    return seal(payload)


def validate_risk_context(
    context: Mapping[str, Any], *, packet: Mapping[str, Any],
    positive: Mapping[str, Any] | None, policy: Mapping[str, Any],
) -> None:
    validate_seal(context, schema=RISK_CONTEXT_SCHEMA)
    if positive is None or context.get("target_digest") != digest(positive):
        raise ValueError("risk context hearing identity differs")
    if context != build_risk_context(packet, positive=positive, policy=policy):
        raise ValueError("risk context differs from observed packet and policy")
