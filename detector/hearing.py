"""Independent hearing targets and Tracer/Defense execution."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from detector.causal_audit import context_for_hearing
from detector.defense import run_defense
from detector.interventions import InterventionPlan
from detector.replay import match_frozen_hearing
from detector.step_tracer import run_step_tracer


_TARGET_DEFAULTS = {
    "source": "", "candidate_id": None, "step": None, "reason": "",
    "quote": "", "referent": "", "evidence_refs": [], "evidence_citations": [],
    "delivered_result": "", "correct_result": "", "contracts": [], "confidence": "",
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
    })


def run_hearing(
    *, packet: Mapping[str, Any], positive: Mapping[str, Any],
    config: Mapping[str, Any], plan: InterventionPlan,
    frozen: Mapping[str, Any] | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    snapshot: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    entry = match_frozen_hearing(frozen, hearing_target(positive))
    causal_context = None
    if diagnostics is not None and diagnostics.get("audit") is not None:
        causal_context = context_for_hearing(diagnostics["audit"], positive=positive, packet=packet)
    context_digest = causal_context["digest"] if causal_context is not None else None
    if (entry is not None and "tracer_result" in entry
            and entry["tracer_result"].get("causal_context_digest") == context_digest):
        tracer_result = deepcopy(entry["tracer_result"])
        tracer_status = deepcopy(entry["tracer_status"])
    else:
        tracer_result, tracer_status = run_step_tracer(
            packet=packet, delivered_result=str(positive.get("delivered_result") or ""),
            correct_result=str(positive.get("correct_result") or ""), config=config,
            plan=plan, positive=positive,
            **({"causal_context": causal_context} if causal_context is not None else {}),
        )
    if entry is not None and "defense_result" in entry:
        defense_result = deepcopy(entry["defense_result"])
        defense_status = deepcopy(entry["defense_status"])
    else:
        defense_result, defense_status = run_defense(
            packet=packet, positive=positive, config=config, plan=plan,
            **({"snapshot": snapshot} if snapshot is not None else {}),
        )
    return tracer_result, tracer_status, defense_result, defense_status
