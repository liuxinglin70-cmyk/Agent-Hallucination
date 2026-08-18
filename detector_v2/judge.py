"""Stage 1: Outcome Judge.

Single end-to-end judgement over the full trajectory. It is the ONLY recall
source in the detector; later stages can only overturn a positive with
evidence, never invent a conviction.
"""

from __future__ import annotations

from typing import Any, Mapping

from detector_v2.artifacts import DeployPlan, empty_plan
from detector_v2.packet import (
    packet_features,
    render_judge_view,
)
from detector_v2.provider import ProviderError, provider_call
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


def run_judge(
    *,
    packet: Mapping[str, Any],
    config: Mapping[str, Any],
    plan: DeployPlan | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the judge; returns ``(judge_result, stage_status)``.

    ``judge_result`` always carries ``ok`` and ``parsed``. A provider or
    contract failure is returned as status, never raised: only the judge
    failure can make the whole case unresolved.
    """
    plan = plan or empty_plan()
    route = dict(config["judge_route"])
    route["_route_name"] = "judge"
    view_text, view_meta = render_judge_view(packet)
    system = JUDGE_SYSTEM + plan.prompt_block(
        packet_features(packet), stage="judge", hook="judge.system",
    )
    user = (
        "Judge the trajectory below. Do not use any information that is not "
        "in the question or the trajectory.\n\n" + view_text
    )
    warnings: list[dict[str, str]] = []
    meta: dict[str, Any] = {}
    try:
        parsed, meta = provider_call(
            route=route, system=system, user=user, purpose="judge",
        )
    except ProviderError as exc:
        return {
            "ok": False,
            "parsed": None,
            "error": exc.message,
            "error_type": exc.error_type,
        }, {
            "stage": "judge",
            "state": "provider_error",
            "attempts": exc.attempts,
            "view": "complete" if not view_meta.get("degraded") else "degraded",
            "warnings": warnings,
        }
    result, error = _parse_judge(parsed, packet)
    if result is None:
        warnings.append({
            "attempt": "initial", "error": f"contract:{error}",
        })
        try:
            parsed, meta = provider_call(
                route=route,
                system=system,
                user=user + JUDGE_CONTRACT_REPAIR,
                purpose="judge_repair",
            )
            result, error = _parse_judge(parsed, packet)
        except ProviderError as exc:
            return {
                "ok": False,
                "parsed": None,
                "error": exc.message,
                "error_type": exc.error_type,
            }, {
                "stage": "judge",
                "state": "provider_error",
                "attempts": exc.attempts,
                "view": "complete" if not view_meta.get("degraded") else "degraded",
                "warnings": warnings,
            }
    if result is None:
        return {
            "ok": False,
            "parsed": None,
            "error": error,
            "error_type": "contract_error",
        }, {
            "stage": "judge",
            "state": "contract_error",
            "attempts": int(meta.get("attempts") or 0),
            "view": "complete" if not view_meta.get("degraded") else "degraded",
            "warnings": warnings,
        }
    return {
        "ok": True,
        "parsed": result,
        "raw": parsed,
        "provider": meta,
        "view_meta": view_meta,
    }, {
        "stage": "judge",
        "state": "ok",
        "attempts": int(meta.get("attempts") or 0),
        "view": "complete" if not view_meta.get("degraded") else "degraded",
        "warnings": warnings,
    }


__all__ = ["run_judge"]
