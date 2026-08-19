"""Root-cause step tracer for positive verdicts."""

from __future__ import annotations

from typing import Any, Mapping

from detector_v2.interventions import InterventionPlan, empty_plan
from detector_v2.packet import packet_features, render_judge_view
from detector_v2.provider import ProviderError, provider_call
from detector_v2.semantics import TRACER_CONTRACT_REPAIR, TRACER_SYSTEM


def _parse_tracer(parsed: Any, packet: Mapping[str, Any]):
    if not isinstance(parsed, Mapping):
        return None, "tracer output is not an object"
    step = parsed.get("step")
    if not isinstance(step, int) or isinstance(step, bool):
        return None, "step must be an integer"
    event_count = len(list(packet.get("events") or []))
    if step < 1 or step > event_count:
        return None, f"step {step} outside 1..{event_count}"
    quote = str(parsed.get("quote") or "").strip()
    referent = str(parsed.get("referent") or "").strip()
    reason = str(parsed.get("reason") or "").strip()
    if not quote or not referent or not reason:
        return None, "quote, referent and reason are required"
    refs_raw = parsed.get("evidence_refs")
    if not isinstance(refs_raw, list) or any(
        not isinstance(item, str) for item in refs_raw
    ):
        return None, "evidence_refs must be a list of strings"
    refs = [str(x) for x in refs_raw if str(x)]
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
    if not refs or any(ref not in valid_refs for ref in refs):
        return None, "evidence_refs must resolve to packet events"
    step_event_id = next(
        (
            str(event.get("event_id") or "")
            for event in packet.get("events") or ()
            if isinstance(event, Mapping) and event.get("position") == step
        ),
        "",
    )
    if not step_event_id or not any(
        ref == step_event_id or ref.startswith(step_event_id + "_tool_")
        for ref in refs
    ):
        return None, "evidence_refs do not support the traced step"
    return {
        "step": step,
        "quote": quote,
        "referent": referent,
        "evidence_refs": refs,
        "reason": reason,
    }, ""


def run_step_tracer(
    *,
    packet: Mapping[str, Any],
    delivered_result: str,
    correct_result: str,
    config: Mapping[str, Any],
    plan: InterventionPlan | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = plan or empty_plan()
    route = dict(config["judge_route"])
    route["_route_name"] = "step_tracer"
    view_text, view_meta = render_judge_view(packet)
    system = TRACER_SYSTEM + plan.prompt_block(packet_features(packet), hook="tracer.system")
    user = (
        "Find the earliest step that introduced the error.\n\n"
        f"DELIVERED RESULT: {delivered_result}\n"
        f"CORRECT RESULT: {correct_result}\n\nTRAJECTORY:\n" + view_text
    )
    try:
        parsed, meta = provider_call(
            route=route, system=system, user=user, purpose="step_tracer",
        )
    except ProviderError as exc:
        return {
            "ok": False, "parsed": None, "error": exc.message,
            "error_type": exc.error_type,
        }, {
            "stage": "step_tracer", "state": "provider_error",
            "attempts": exc.attempts,
            "view": "complete" if not view_meta.get("degraded") else "degraded",
            "warnings": [],
        }
    result, error = _parse_tracer(parsed, packet)
    warnings: list[dict[str, str]] = []
    if result is None:
        warnings.append({"attempt": "initial", "error": f"contract:{error}"})
        try:
            parsed, meta = provider_call(
                route=route, system=system,
                user=user + TRACER_CONTRACT_REPAIR,
                purpose="step_tracer_repair",
            )
            result, error = _parse_tracer(parsed, packet)
        except ProviderError as exc:
            return {
                "ok": False, "parsed": None, "error": exc.message,
                "error_type": exc.error_type,
            }, {
                "stage": "step_tracer", "state": "provider_error",
                "attempts": exc.attempts,
                "view": "complete" if not view_meta.get("degraded") else "degraded",
                "warnings": warnings,
            }
    if result is None:
        return {
            "ok": False, "parsed": None, "error": error,
            "error_type": "contract_error",
        }, {
            "stage": "step_tracer", "state": "contract_error",
            "attempts": int(meta.get("attempts") or 0),
            "view": "complete" if not view_meta.get("degraded") else "degraded",
            "warnings": warnings,
        }
    return {
        "ok": True, "parsed": result, "provider": meta,
        "view_meta": view_meta,
    }, {
        "stage": "step_tracer", "state": "ok",
        "attempts": int(meta.get("attempts") or 0),
        "view": "complete" if not view_meta.get("degraded") else "degraded",
        "warnings": warnings,
    }


__all__ = ["run_step_tracer"]
