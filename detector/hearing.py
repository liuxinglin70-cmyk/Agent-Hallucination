"""Independent hearing targets and Tracer/Defense execution."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping, Sequence

from detector.causal_audit import context_for_hearing
from detector.risk_context import build_risk_context
from detector.defense import run_defense
from detector.interventions import InterventionPlan
from detector.replay import match_frozen_hearing
from detector.step_tracer import run_step_tracer
from detector.packet import packet_features
from detector.action_evidence import validate_action_evidence
from detector.runtime import parallel_ordered


_TARGET_DEFAULTS = {
    "source": "", "candidate_id": None, "step": None, "reason": "",
    "quote": "", "referent": "", "evidence_refs": [], "evidence_citations": [],
    "delivered_result": "", "correct_result": "", "contracts": [], "confidence": "",
    "basis": "unestablished",
    "action_evidence": [],
}


def hearing_target(positive: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy({key: positive.get(key, default) for key, default in _TARGET_DEFAULTS.items()})


def judge_target(parsed: Mapping[str, Any]) -> dict[str, Any]:
    root = parsed.get("root_cause") or {}
    return hearing_target({
        "source": "judge", "step": parsed.get("hallucination_step"),
        "reason": parsed.get("hallucination_reason") or "",
        "delivered_result": parsed.get("delivered_result") or "",
        "correct_result": parsed.get("correct_result") or "",
        "evidence_refs": parsed.get("evidence_refs") or [],
        "contracts": parsed.get("contracts") or [], "confidence": parsed.get("confidence") or "",
        "basis": parsed.get("basis") or "unestablished",
        "quote": root.get("quote") or "", "referent": root.get("referent") or "",
    })


def recall_target(candidate: Mapping[str, Any], contracts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    # Task obligations may cross this boundary; Judge answer/verdict anchors may not.
    return hearing_target({
        "source": "recall_upgrade", "candidate_id": candidate.get("candidate_id"),
        "step": candidate.get("corrected_step"), "reason": candidate.get("claim") or "",
        "evidence_refs": candidate.get("evidence_refs") or [],
        "evidence_citations": candidate.get("evidence_citations") or [],
        "quote": candidate.get("quote") or "", "contracts": list(contracts),
        "action_evidence": candidate.get("action_evidence") or [],
    })


def run_hearing(
    *, packet: Mapping[str, Any], positive: Mapping[str, Any],
    config: Mapping[str, Any], plan: InterventionPlan,
    frozen: Mapping[str, Any] | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    diagnostics_factory: Callable[[], Mapping[str, Any] | None] | None = None,
    snapshot: Mapping[str, Any] | None = None,
    on_defense: Callable[[Mapping[str, Any], Mapping[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    for witness in positive.get("action_evidence") or []:
        validate_action_evidence(packet, witness)
    entry = match_frozen_hearing(frozen, hearing_target(positive))
    def prepare_context():
        prepared = diagnostics_factory() if diagnostics_factory is not None else diagnostics
        context = None
        if prepared is not None and prepared.get("audit") is not None:
            context = context_for_hearing(prepared["audit"], positive=positive, packet=packet,
                                          policy=config.get("causal_audit") or {})
            if context is None and prepared["audit"]["health"]["state"] == "ok":
                context = build_risk_context(packet, positive=positive, policy=config.get("causal_audit") or {})
        return context

    # Frozen reuse must be decided before touching the intervention trace.
    frozen_tracer = entry is not None and "tracer_result" in entry
    causal_context = prepare_context() if frozen_tracer else None
    reuse_tracer = (frozen_tracer and entry["tracer_result"].get("causal_context_digest")
                   == (causal_context["digest"] if causal_context is not None else None))
    # Freeze the Tracer hook before Defense writes its pre-check facts.
    tracer_prompt = "" if reuse_tracer else plan.prompt_block(packet_features(packet), hook="tracer.system")

    def stage(index: int):
        if index == 0:
            if reuse_tracer:
                return deepcopy(entry["tracer_result"]), deepcopy(entry["tracer_status"])
            context = causal_context if frozen_tracer else prepare_context()
            return run_step_tracer(
                packet=packet, delivered_result=str(positive.get("delivered_result") or ""),
                correct_result=str(positive.get("correct_result") or ""), config=config,
                plan=plan, positive=positive, intervention_prompt=tracer_prompt,
                **({"causal_context": context} if context is not None else {}),
            )
        if entry is not None and "defense_result" in entry:
            defended = deepcopy(entry["defense_result"]), deepcopy(entry["defense_status"])
        else:
            defended = run_defense(packet=packet, positive=positive, config=config, plan=plan,
                                   **({"snapshot": snapshot} if snapshot is not None else {}))
        if on_defense is not None:
            on_defense(*defended)
        return defended

    traced, defended = parallel_ordered(stage, 2, workers=2)
    tracer_result, tracer_status = traced
    defense_result, defense_status = defended
    return tracer_result, tracer_status, defense_result, defense_status
