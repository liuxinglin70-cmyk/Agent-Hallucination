"""Replayable local checks and optional bounded model hypotheses."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import time
from typing import Any, Callable, Mapping, Sequence

from detector.causal_contracts import (
    CAUSAL_PROJECTION_SCHEMA, RISK_PROFILE_SCHEMA, arithmetic_check, bind_arithmetic_claim,
    seal, validate_seal,
)
from detector.causal_graph import build_observed_claim_lineage, build_observed_dependencies, project_dependency_closure
from detector.identity import digest
from detector.measurement import request_execution
from detector.packet import packet_reference_error, validate_packet
from detector.provider import ProviderError, provider_call, raise_if_terminal
from detector.predicates import run_predicate
from detector.runtime import AddedBudget, AddedBudgetExceeded, added_request_scope, usage_scope, utc_now
from detector.step_uncertainty import build_step_risk_profile, select_causal_candidates

AUDIT_SCHEMA = "detector.causal_audit.v1"
CONTEXT_SCHEMA = "detector.causal_context.v3"
CAUSAL_ROLES = frozenset({"introduction", "propagation", "delivery", "corrected", "unknown"})
CLAIM_STATUSES = frozenset({"supported", "refuted", "inconclusive"})
_SYSTEM = (
    "Inspect only observed trajectory evidence for these candidates. Return JSON {\"candidates\": "
    "[{\"event_id\": string, \"role\": introduction|propagation|delivery|corrected|unknown, "
    "\"claim_status\": supported|refuted|inconclusive, \"evidence_refs\": [packet reference], "
    "\"reason\": string}]}. Your explanation is a hypothesis, not execution verification. "
    "Do not invent tool replays, external facts or state changes."
)


@dataclass(frozen=True)
class OfflineFeedbackPolicy:
    policy_id: str
    allows: Callable[[list[dict[str, Any]]], bool]

    def __post_init__(self):
        if not isinstance(self.policy_id, str) or not self.policy_id.strip() or not callable(self.allows):
            raise ValueError("offline feedback policy requires an identity and a callable")


def causal_recall_decision(
    audit: Mapping[str, Any] | None, *, judge_result: Mapping[str, Any],
    policy: Mapping[str, Any], budget: AddedBudget,
    offline_policy: OfflineFeedbackPolicy | None = None,
) -> dict[str, Any]:
    result = {"state": "inactive", "reason": "disabled", "anomalies": [],
              "policy_origin": "offline_injected" if offline_policy else "production",
              "policy_id": offline_policy.policy_id if offline_policy else None,
              "decision_authority": "none"}
    if policy["mode"] != "tracer_context" or not policy["negative_feedback_enabled"]:
        return result
    if not judge_result.get("ok") or (judge_result.get("parsed") or {}).get("is_hallucination") is not False:
        return {**result, "reason": "not_a_verified_negative"}
    if audit is None:
        return {**result, "reason": "audit_unavailable"}
    validate_seal(audit, schema=AUDIT_SCHEMA)
    if audit["policy_digest"] != digest(policy):
        raise ValueError("negative feedback policy identity differs")
    if audit["health"]["state"] != "ok":
        return {**result, "reason": "audit_unhealthy"}
    anomalies = [deepcopy(row) for row in audit["candidates"]
                 if row["claim_status"] == "supported" and row["verification_status"] == "verified"
                 and row["scope"] == "local_arithmetic_result" and row["evidence_refs"]]
    if not anomalies:
        return {**result, "reason": "no_verified_anomaly"}
    if offline_policy is None:
        return {**result, "reason": "calibration_unavailable"}
    allowed = offline_policy.allows(deepcopy(anomalies))
    try:
        budget.check()
    except AddedBudgetExceeded as exc:
        return {**result, "state": "budget_exhausted", "reason": exc.resource}
    if type(allowed) is not bool:
        raise ValueError("offline feedback policy must return a boolean")
    if not allowed:
        return {**result, "reason": "policy_refused"}
    return {**result, "state": "eligible", "reason": "verified_anomaly_allowed", "anomalies": anomalies}


def _model_hypotheses(parsed: Any, packet, selected, visible) -> list[dict[str, Any]]:
    if not isinstance(parsed, Mapping) or set(parsed) != {"candidates"}:
        raise ValueError("audit output requires candidates")
    rows = parsed["candidates"]
    if not isinstance(rows, list) or len(rows) > len(selected):
        raise ValueError("audit candidate list exceeds its input")
    hypotheses = []
    seen = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"event_id", "role", "claim_status", "evidence_refs", "reason"}:
            raise ValueError("audit candidate fields are invalid")
        if not isinstance(row["event_id"], str) or row["event_id"] not in selected or row["event_id"] in seen:
            raise ValueError("audit candidate event is unknown or repeated")
        if (not isinstance(row["role"], str) or row["role"] not in CAUSAL_ROLES
                or not isinstance(row["claim_status"], str) or row["claim_status"] not in CLAIM_STATUSES):
            raise ValueError("audit candidate enum is invalid")
        if not isinstance(row["reason"], str) or not 1 <= len(row["reason"]) <= 1000:
            raise ValueError("audit candidate reason is invalid")
        if (packet_reference_error(packet, row["evidence_refs"], event_ids=visible, required=True)
                or len(row["evidence_refs"]) > 32):
            raise ValueError("audit candidate references are invalid")
        seen.add(row["event_id"])
        hypotheses.append({**deepcopy(dict(row)), "verification_status": "hypothesis"})
    return hypotheses


def _local_candidate(eid: str, checks: list[dict[str, Any]], *, unchecked: bool = False) -> dict[str, Any]:
    deviations = [check for check in checks if check["status"] == "deviation"]
    return {
        "event_id": eid, "role": "unknown",
        "claim": "observed calculator result deviates from its literal expression"
        if checks else "candidate has a material causal deviation",
        "claim_status": "supported" if deviations else "refuted" if checks and not unchecked else "inconclusive",
        "scope": "local_arithmetic_result" if checks else "unverified_causal_claim",
        "materiality": "unknown", "evidence_refs": list(dict.fromkeys(
            ref for check in checks for ref in check["evidence_refs"])),
        "verification_status": "verified" if checks else "unknown", "certificates": checks,
        "decision_authority": "none",
        "counterfactual": {
            "kind": "local_arithmetic_correction" if deviations else "none",
            "status": "verified_local" if deviations else "not_run",
            "assumptions": ["same literal expression"] if deviations else [],
            "verifier_refs": [check["verifier"] for check in deviations],
            "state_changes_verified": False,
        },
    }


def run_causal_audit(
    *, packet: Mapping[str, Any], profile: Mapping[str, Any], projection: Mapping[str, Any],
    target: Mapping[str, Any] | None = None, snapshot: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    config: Mapping[str, Any], budget: AddedBudget,
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_packet(packet)
    validate_seal(profile, schema=RISK_PROFILE_SCHEMA)
    validate_seal(projection, schema=CAUSAL_PROJECTION_SCHEMA)
    policy = config["causal_audit"]
    if any(value != digest(packet) for value in (profile["packet_digest"], projection["packet_digest"])):
        raise ValueError("audit input packet identity differs")
    if any(value != digest(policy) for value in (
        profile["policy_digest"], projection["policy_digest"], budget.policy_digest,
    )):
        raise ValueError("audit input policy identity differs")
    if profile["snapshot_digest"] != digest(snapshot):
        raise ValueError("audit input snapshot identity differs")
    if profile["context_digest"] != digest(context):
        raise ValueError("audit input context identity differs")
    selected = list(projection["target_event_ids"])
    if packet_reference_error(packet, selected):
        raise ValueError("audit target references are invalid")
    visible = set(projection["selected_event_ids"])
    by_id = {event["event_id"]: event for event in packet["events"]}
    if any(eid not in by_id for eid in selected):
        raise ValueError("audit target must reference events")
    candidates = []
    task_checks = []
    health = {"state": "ok", "warnings": []}
    started_at = utc_now()
    started = time.perf_counter()
    with usage_scope() as observations:
        try:
            budget.begin()
            for signal in profile["task_signals"]:
                budget.check()
                certificate = run_predicate(signal["producer"], packet, context)
                task_checks.append({
                    "locus_event_id": None, "scope": "mechanical_task_obligation",
                    "claim_status": "supported" if certificate["verdict"] == "violated" else "inconclusive",
                    "certificate": certificate, "verification_status": "verified",
                    "decision_authority": "none",
                })
            for eid in selected:
                budget.check()
                checks = []
                unchecked = False
                if eid in visible:
                    interactions = by_id[eid].get("tool_interactions") or ()
                    unchecked = len(interactions) > 32
                    for interaction in interactions[:32]:
                        check = arithmetic_check(interaction)
                        if check:
                            checks.append(check)
                        elif str(interaction.get("tool_name") or "").lower() == "calculator":
                            unchecked = True
                candidates.append(_local_candidate(eid, checks, unchecked=unchecked))
            budget.check()
            hypotheses = []
            if visible.intersection(selected) and policy["counterfactual_mode"] == "model_hypothesis":
                user = json.dumps({"projection": projection, "candidates": candidates,
                                   "target": target, "snapshot": snapshot}, ensure_ascii=False)
                route = dict(config["judge_route"])
                route.update(_route_name="causal_audit", transport_retries=0)
                route["max_tokens"] = policy["max_added_output_tokens_per_case"]
                with added_request_scope(budget):
                    parsed, _ = provider_call(route=route, system=_SYSTEM, user=user,
                                              purpose="causal_audit", cache_tag=digest({
                        "packet": digest(packet), "profile": profile["digest"], "projection": projection["digest"],
                        "target": target, "snapshot": snapshot, "policy": policy,
                    }))
                budget.check()
                try:
                    hypotheses = _model_hypotheses(parsed, packet, visible.intersection(selected), visible)
                except ValueError as exc:
                    health = {"state": "contract_error", "warnings": [str(exc)]}
        except AddedBudgetExceeded as exc:
            health = {"state": "budget_exhausted", "warnings": [exc.resource]}
            hypotheses = []
        except ProviderError as exc:
            raise_if_terminal(exc)
            health = {"state": "provider_error", "warnings": [exc.as_dict()]}
            hypotheses = []
    execution = request_execution(observations, started_at=started_at, finished_at=utc_now(),
                                  elapsed_seconds=time.perf_counter() - started)
    audit = seal({
        "schema_version": AUDIT_SCHEMA, "packet_digest": digest(packet), "policy_digest": digest(policy),
        "profile_digest": profile["digest"], "projection_digest": projection["digest"],
        "snapshot_digest": digest(snapshot), "target_digest": digest(target),
        "candidates": candidates, "task_checks": task_checks, "hypotheses": hypotheses,
        "context_digest": digest(context), "scope": "observed_local_checks",
        "decision_authority": "none",
        "closure_state": projection["closure_state"], "health": health,
        "cost": execution, "budget": budget.snapshot(),
    })
    return audit, health


def context_for_hearing(
    audit: Mapping[str, Any], *, positive: Mapping[str, Any], packet: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any] | None:
    validate_seal(audit, schema=AUDIT_SCHEMA)
    if audit["packet_digest"] != digest(packet):
        raise ValueError("causal context packet identity differs")
    if audit["policy_digest"] != digest(policy):
        raise ValueError("causal context policy identity differs")
    if audit["health"]["state"] != "ok":
        return None
    refs = positive.get("evidence_refs") or []
    error = packet_reference_error(packet, refs)
    if error:
        raise ValueError(error)
    bound = bind_arithmetic_claim(packet, positive)
    if bound is None:
        return None
    binding, check = bound
    if not any(row["event_id"] == binding["event_id"] and check in row["certificates"]
               and row["verification_status"] == "verified" for row in audit["candidates"]):
        return None
    candidates = [_local_candidate(binding["event_id"], [check])]
    return seal({
        "schema_version": CONTEXT_SCHEMA, "packet_digest": audit["packet_digest"],
        "policy_digest": audit["policy_digest"],
        "audit_evidence_digest": digest({key: value for key, value in audit.items()
                                        if key not in {"digest", "cost", "budget", "hypotheses"}}),
        "target_digest": digest(positive), "candidates": candidates,
        "scope": "hearing_literal_result_and_declared_copies", "claim_equivalence": "same_local_result",
        "claim_binding": binding,
        "observed_lineage": build_observed_claim_lineage(packet, binding=binding, policy=policy),
        "closure_state": audit["closure_state"], "decision_authority": "none",
    })


def validate_hearing_context(
    context: Mapping[str, Any], *, packet: Mapping[str, Any],
    positive: Mapping[str, Any] | None, policy: Mapping[str, Any],
) -> None:
    validate_seal(context, schema=CONTEXT_SCHEMA)
    if (set(context) != {
            "schema_version", "packet_digest", "policy_digest", "audit_evidence_digest",
            "target_digest", "candidates", "scope", "claim_equivalence", "claim_binding",
            "observed_lineage", "closure_state", "decision_authority", "digest",
        } or context.get("scope") != "hearing_literal_result_and_declared_copies"
            or context.get("closure_state") not in ("complete", "unknown")):
        raise ValueError("causal context contract fields differ")
    if context["packet_digest"] != digest(packet):
        raise ValueError("causal context packet identity differs")
    if positive is None or context["target_digest"] != digest(positive):
        raise ValueError("causal context hearing identity differs")
    if context["policy_digest"] != digest(policy):
        raise ValueError("causal context policy identity differs")
    error = packet_reference_error(packet, positive.get("evidence_refs") or ())
    if error:
        raise ValueError(error)
    candidates = context.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], Mapping):
        raise ValueError("causal context requires one bound local claim")
    bound = bind_arithmetic_claim(packet, positive)
    if bound is None:
        raise ValueError("causal context local claim is not bound")
    binding, check = bound
    if (context.get("claim_binding") != binding
            or candidates != [_local_candidate(binding["event_id"], [check])]
            or context.get("claim_equivalence") != "same_local_result"
            or context.get("decision_authority") != "none"):
        raise ValueError("causal context local claim certificate differs")
    if context.get("observed_lineage") != build_observed_claim_lineage(packet, binding=binding, policy=policy):
        raise ValueError("causal context observed claim lineage differs")


def build_causal_diagnostics(
    *, packet: Mapping[str, Any], config: Mapping[str, Any], budget: AddedBudget,
    mandatory_event_ids: Sequence[str], context: Mapping[str, Any] | None,
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    policy = config["causal_audit"]
    try:
        budget.begin()
    except AddedBudgetExceeded as exc:
        return {"mode": policy["mode"], "profile": None, "candidates": None, "graph": None,
                "projection": None, "audit": None,
                "health": {"state": "budget_exhausted", "warnings": [exc.resource]},
                "budget": budget.snapshot()}
    profile = build_step_risk_profile(packet, policy=policy, context=context, snapshot=snapshot)
    candidates = select_causal_candidates(profile, mandatory_event_ids=mandatory_event_ids, policy=policy)
    graph = build_observed_dependencies(packet, policy=policy)
    projection = project_dependency_closure(packet, candidates=candidates, graph=graph, policy=policy)
    audit, health = run_causal_audit(packet=packet, profile=profile, projection=projection,
                                   snapshot=snapshot, context=context, config=config, budget=budget)
    return {"mode": policy["mode"], "profile": profile, "candidates": candidates,
            "graph": graph, "projection": projection, "audit": audit, "health": health}
