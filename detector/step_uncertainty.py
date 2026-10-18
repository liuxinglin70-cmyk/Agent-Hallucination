"""Read-only step signals and stable causal candidate selection."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from detector.causal_contracts import (
    RISK_PROFILE_SCHEMA, SCORER, SIGNAL_PRIORITY, arithmetic_check,
    bound_fields, field_binding, seal, validate_seal,
)
from detector.identity import artifact_digest, digest
from detector.packet import (
    ROLE_CHANNEL_AGENT,
    ROLE_CHANNEL_OBSERVATION,
    ROLE_CHANNEL_UNKNOWN,
    role_channel,
    step_kind,
    validate_packet,
)
from detector.predicates import run_predicate
from detector.next_action import observation_failed, scan_static_action_risks


def _human_guidance_channel(tool_name: Any) -> bool:
    """Whether a tool call is the agent asking a human, under the names corpora use.

    The recorded names are free-form ("ask_human", "ask_human_via_console",
    "ask_human_for_help", ...), so an exact name match classified the Camel
    family's console ask as an ordinary observation and lost the stronger
    "this came from a human" signal.
    """
    name = str(tool_name or "").strip().casefold().replace("-", "_").replace(" ", "_")
    return "ask_human" in name or name in {"ask", "askuser", "ask_user"}


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
        channel = role_channel(event.get("role"))
        # A role that is not a string is the only unreadable case; a corpus that
        # names its agents ("Coder", "WebSurfer", "Planner", ...) is still agent
        # output and must be scored like "assistant".
        recognized = channel != ROLE_CHANNEL_UNKNOWN
        truncated = len(json.dumps(event, ensure_ascii=False)) > char_limit
        if not unsupported and recognized:
            components = [(event.get("content"), eid)]
            interactions = event.get("tool_interactions") or []
            truncated |= len(interactions) > 32
            for interaction in interactions[:32]:
                iid = interaction["interaction_id"]
                if observation_failed(interaction.get("observation")):
                    signals.append({
                        "signal": "observed_tool_failure", "producer": "observed_failure.v1",
                        "verification_status": "hypothesis", "evidence_refs": [iid],
                        "responsibility": "unassigned",
                    })
                check = arithmetic_check(interaction)
                if check and check["status"] == "deviation":
                    signals.append({
                        "signal": "verified_deviation", "producer": check["verifier"],
                        "verification_status": "verified", "evidence_refs": [iid],
                        "certificate": check, "responsibility": "tool_observation",
                    })
                for value, signal in (
                    (interaction.get("arguments"), "unverified_action"),
                    (interaction.get("observation"), "external_guidance"
                     if _human_guidance_channel(interaction.get("tool_name"))
                     else "unverified_observation"),
                ):
                    if value not in (None, "", {}):
                        signals.append({"signal": signal, "producer": "visible_channel.v1",
                                        "verification_status": "hypothesis", "evidence_refs": [iid],
                                        "responsibility": "unassigned"})
                components.extend([(interaction.get("arguments"), iid),
                                   (interaction.get("observation"), iid)])
            for value, ref in components:
                if len(json.dumps(value, ensure_ascii=False)) > char_limit:
                    truncated = True
                    continue
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
            if channel == ROLE_CHANNEL_AGENT and event.get("content") not in (None, ""):
                signals.append({
                    "signal": "unsupported_claim", "producer": "unverified_content.v1",
                    "verification_status": "unknown", "evidence_refs": [eid],
                    "responsibility": "unassigned",
                })
        status = "unsupported" if unsupported else "truncated" if truncated else (
            "scored" if recognized and signals else "unknown")
        rows.append({
            "event_id": eid, "position": event["position"],
            "step_kind": ("tool_observation" if channel == ROLE_CHANNEL_OBSERVATION
                          else step_kind(packet, event["position"])),
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
        "static_action_scan": scan_static_action_risks(packet, top_k=policy["top_k"]),
        "health": {"state": "unsupported" if unsupported else "truncated" if omitted or any(
            row["score_status"] == "truncated" for row in rows) else "ok",
            "reason": "teacher_forced_backend_unavailable" if unsupported else None},
        "cost": {"provider_requests": 0, "input_budget_unit": "characters"},
    })


def risk_candidate_rows(profile: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Combine bounded semantic rows with full-trajectory explicit failure anchors."""
    rows = {row["event_id"]: row for row in profile["by_event"]}
    if profile["health"]["state"] != "unsupported":
        for row in (profile.get("static_action_scan") or {}).get("candidate_rows") or []:
            existing = rows.get(row["event_id"])
            if existing is None or existing["priority"] is None:
                rows[row["event_id"]] = row
    return rows


def select_causal_candidates(
    profile: Mapping[str, Any], *, mandatory_event_ids: Sequence[str], policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Pick the events the causal projection may walk.

    ``state`` is load-bearing downstream: ``closure_state`` reads it (see
    ``detector.causal_graph``), and a ``"unknown"`` state degrades the causal
    context and the task certificate for the whole case. It is ``"selected"``
    only when the selection is complete -- nothing was cut by capacity and
    every mandatory event was found in the profile -- and ``"unknown"``
    otherwise, so a truncated projection is never reported as a full one.
    """
    validate_seal(profile, schema=RISK_PROFILE_SCHEMA)
    if profile["policy_digest"] != digest(policy):
        raise ValueError("candidate policy differs from risk profile")
    if isinstance(mandatory_event_ids, str) or any(not isinstance(eid, str) for eid in mandatory_event_ids):
        raise ValueError("mandatory event ids must be strings")
    mandatory = list(dict.fromkeys(mandatory_event_ids))
    selection_rows = risk_candidate_rows(profile)
    ranked = sorted((row for row in selection_rows.values()
                     if row["score_status"] in {"scored", "truncated"} and row["risk_signals"]),
                    key=lambda row: (row["priority"], -sum(
                        signal["verification_status"] == "verified" for signal in row["risk_signals"]),
                        row["position"]))
    top = [row["event_id"] for row in ranked[:policy["top_k"]]]
    selected = list(dict.fromkeys([*mandatory, *top]))
    capacity = policy["max_projection_events"]
    omitted = selected[capacity:]
    selected = selected[:capacity]
    known = set(selection_rows)
    unresolved = [eid for eid in mandatory if eid not in known]
    return seal({
        "schema_version": "detector.causal_candidates.v1", "profile_digest": profile["digest"],
        "packet_digest": profile["packet_digest"], "policy_digest": digest(policy),
        "top_k_event_ids": top, "mandatory_event_ids": mandatory,
        "selected_event_ids": selected, "omitted_event_ids": omitted,
        "unresolved_event_ids": unresolved,
        "state": "unknown" if omitted or unresolved else "selected",
    })
