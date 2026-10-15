"""Bounded diagnostic priorities, without factual or localization authority."""
import json
from typing import Any, Mapping

from detector.causal_contracts import seal, validate_seal
from detector.identity import digest
from detector.packet import packet_reference_error
from detector.next_action import build_next_action_audit
from detector.code_lineage import build_code_lineage, code_ancestor_events
from detector.semantic_trace import build_semantic_trace, semantic_ancestors
from detector.step_uncertainty import build_step_risk_profile, select_causal_candidates, risk_candidate_rows


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
    code_lineage = build_code_lineage(packet, max_edges=policy["max_graph_edges"])
    ancestors = code_ancestor_events(code_lineage, mandatory[0])
    semantic_trace = build_semantic_trace(packet)
    semantic_sources = semantic_ancestors(semantic_trace, mandatory[0])
    selected = list(dict.fromkeys([*selection["selected_event_ids"], *ancestors[:policy["top_k"]],
                                  *semantic_sources[:policy["top_k"]]]))
    selected = selected[:policy["max_projection_events"]]
    risk = risk_candidate_rows(profile)
    needles = [positive.get(key) for key in ("quote", "delivered_result")
               if isinstance(positive.get(key), str)]
    candidates = []
    for eid in selected:
        if eid not in risk:
            continue
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
        action_audit = build_next_action_audit(packet, [eid])
        if action_audit["comparisons"]:
            candidates[-1]["next_action_audit"] = action_audit
    payload = {"schema_version": RISK_CONTEXT_SCHEMA, "packet_digest": digest(packet),
               "policy_digest": digest(policy), "target_digest": digest(positive),
               "scope": "diagnostic_event_priority", "claim_equivalence": "unestablished",
               "decision_authority": "none", "localization_authority": "none",
               "score_source": "semantic_proxy", "profile_health": profile["health"],
               "code_lineage": {key: code_lineage[key] for key in (
                   "schema_version", "digest", "scope", "execution_state", "decision_authority", "localization_authority",
                   "omitted_interaction_ids")},
               "code_consumption_hints": [edge for edge in code_lineage["edges"]
                                          if edge["target_event_id"] in {*ancestors, mandatory[0]}][:8],
               "semantic_trace_digest": semantic_trace["digest"],
               "semantic_consumption_hints": [edge for edge in semantic_trace["edges"]
                                               if edge["target_event_id"] in {*semantic_sources, mandatory[0]}][:8],
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
