"""Read-only step signals and stable causal candidate selection."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from detector.causal_contracts import (
    RISK_PROFILE_SCHEMA, SCORER, SIGNAL_PRIORITY, arithmetic_check,
    bound_fields, field_binding, seal, validate_seal,
)
from detector.identity import artifact_digest, digest
from detector.packet import step_kind, validate_packet
from detector.predicates import run_predicate


def build_step_risk_profile(
    packet: Mapping[str, Any], *, policy: Mapping[str, Any],
    context: Mapping[str, Any] | None = None, snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validate_packet(packet)
    unsupported = policy["score_source"] == "teacher_forced_ppl"
    event_limit = policy["max_projection_events"] * policy["max_dependency_depth"]
    char_limit = policy["max_added_prompt_tokens_per_case"]
    prior_fields: dict[tuple[Any, ...], list[tuple[Any, str]]] = {}
    rows = []
    for event in packet["events"][:event_limit]:
        eid = event["event_id"]
        signals = []
        recognized = event.get("role") in {"assistant", "tool", "user", "system"}
        truncated = len(json.dumps(event, ensure_ascii=False)) > char_limit
        if not unsupported and recognized and not truncated:
            components = [(event.get("content"), eid)]
            for interaction in event.get("tool_interactions") or ():
                iid = interaction["interaction_id"]
                check = arithmetic_check(interaction)
                if check and check["status"] == "deviation":
                    signals.append({
                        "signal": "verified_deviation", "producer": check["verifier"],
                        "verification_status": "verified", "evidence_refs": [iid],
                        "certificate": check, "responsibility": "tool_observation",
                    })
                components.extend([(interaction.get("arguments"), iid),
                                   (interaction.get("observation"), iid)])
            for value, ref in components:
                fields, clipped = bound_fields(value)
                truncated |= clipped
                for field in fields:
                    binding = field_binding(field)
                    if any(old != field["value"] for old, _ in prior_fields.get(binding, ())):
                        signals.append({
                            "signal": "bound_field_conflict", "producer": "explicit_field.v1",
                            "verification_status": "hypothesis", "evidence_refs": list(dict.fromkeys([
                                *[old_ref for _, old_ref in prior_fields[binding]], ref,
                            ])), "binding": list(binding), "responsibility": "unassigned",
                        })
                    prior_fields[binding] = [(field["value"], ref)]
                    if len(signals) >= 64:
                        truncated = True
                        break
            if event.get("role") == "assistant" and event.get("content") not in (None, ""):
                signals.append({
                    "signal": "unsupported_claim", "producer": "unverified_content.v1",
                    "verification_status": "unknown", "evidence_refs": [eid],
                    "responsibility": "unassigned",
                })
        status = "unsupported" if unsupported else "truncated" if truncated else (
            "scored" if recognized and signals else "unknown")
        rows.append({
            "event_id": eid, "position": event["position"],
            "step_kind": "tool_observation" if event.get("role") == "tool" else step_kind(packet, event["position"]),
            "producer": policy["score_source"], "score_status": status,
            "risk_signals": signals,
            "priority": min((SIGNAL_PRIORITY[s["signal"]] for s in signals), default=None),
            "evidence_refs": list(dict.fromkeys(ref for signal in signals for ref in signal["evidence_refs"])),
        })
    action = run_predicate("required_action_present", packet, context)
    task_signals = [{
        "signal": "action_precondition", "producer": "required_action_present",
        "verification_status": "verified", "locus_event_id": None,
        "evidence_refs": [], "certificate": action, "scope": "task_obligation",
    }] if action["verdict"] == "violated" else []
    omitted = [event["event_id"] for event in packet["events"][event_limit:]]
    return seal({
        "schema_version": RISK_PROFILE_SCHEMA, "packet_digest": digest(packet),
        "policy_digest": digest(policy), "scorer_digest": digest({
            "definition": SCORER, "artifact": artifact_digest(policy["scorer_artifact"])
            if policy.get("scorer_artifact") is not None else None,
        }), "context_digest": digest(context), "snapshot_digest": digest(snapshot),
        "by_event": rows, "task_signals": task_signals, "omitted_event_ids": omitted,
        "health": {"state": "unsupported" if unsupported else "truncated" if omitted or any(
            row["score_status"] == "truncated" for row in rows) else "ok",
            "reason": "teacher_forced_backend_unavailable" if unsupported else None},
        "cost": {"provider_requests": 0, "input_budget_unit": "characters"},
    })


def select_causal_candidates(
    profile: Mapping[str, Any], *, mandatory_event_ids: Sequence[str], policy: Mapping[str, Any],
) -> dict[str, Any]:
    validate_seal(profile, schema=RISK_PROFILE_SCHEMA)
    if profile["policy_digest"] != digest(policy):
        raise ValueError("candidate policy differs from risk profile")
    if isinstance(mandatory_event_ids, str) or any(not isinstance(eid, str) for eid in mandatory_event_ids):
        raise ValueError("mandatory event ids must be strings")
    mandatory = list(dict.fromkeys(mandatory_event_ids))
    ranked = sorted((row for row in profile["by_event"] if row["score_status"] == "scored"),
                    key=lambda row: (row["priority"], -sum(
                        signal["verification_status"] == "verified" for signal in row["risk_signals"]),
                        row["position"]))
    top = [row["event_id"] for row in ranked[:policy["top_k"]]]
    selected = list(dict.fromkeys([*mandatory, *top]))
    capacity = policy["max_projection_events"]
    omitted = selected[capacity:]
    selected = selected[:capacity]
    known = {row["event_id"] for row in profile["by_event"]}
    unresolved = [eid for eid in mandatory if eid not in known]
    return seal({
        "schema_version": "detector.causal_candidates.v1", "profile_digest": profile["digest"],
        "packet_digest": profile["packet_digest"], "policy_digest": digest(policy),
        "top_k_event_ids": top, "mandatory_event_ids": mandatory,
        "selected_event_ids": selected, "omitted_event_ids": omitted,
        "unresolved_event_ids": unresolved,
        "state": "unknown" if omitted or unresolved else "selected",
    })
