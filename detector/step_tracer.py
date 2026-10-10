"""Root-cause step tracer for positive verdicts."""

from __future__ import annotations

import json
from typing import Any, Mapping

from detector.causal_audit import validate_hearing_context
from detector.causal_contracts import numeric_literal
from detector.interventions import InterventionPlan, empty_plan
from detector.packet import (evidence_refs_support_step, event_id_for_step,
                             packet_features, packet_quote_error,
                             packet_reference_error, render_judge_view)
from detector.provider import ProviderError, provider_call, raise_if_terminal
from detector.semantics import TRACER_CONTRACT_REPAIR, TRACER_SYSTEM
from detector.runtime import AddedBudgetExceeded, current_added_budget, current_added_request_budget


def _parse_tracer(parsed: Any, packet: Mapping[str, Any]):
    if not isinstance(parsed, Mapping):
        return None, "tracer output is not an object"
    step = parsed.get("step")
    if not isinstance(step, int) or isinstance(step, bool):
        return None, "step must be an integer"
    event_count = len(list(packet.get("events") or []))
    if step < 1 or step > event_count:
        return None, f"step {step} outside 1..{event_count}"
    quote = parsed.get("quote")
    referent = str(parsed.get("referent") or "").strip()
    reason = str(parsed.get("reason") or "").strip()
    if not quote or not referent or not reason:
        return None, "quote, referent and reason are required"
    refs = parsed.get("evidence_refs")
    reference_error = packet_reference_error(packet, refs, required=True)
    if reference_error:
        return None, reference_error
    if not evidence_refs_support_step(packet, refs, step):
        return None, "evidence_refs do not support the traced step"
    quote_error = packet_quote_error(
        packet, quote, locus=event_id_for_step(packet, step), evidence_refs=refs,
    )
    if quote_error:
        return None, quote_error
    return {
        "step": step,
        "quote": quote.strip(),
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
    positive: Mapping[str, Any] | None = None,
    causal_context: Mapping[str, Any] | None = None,
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
    if positive is not None:
        target = {key: positive.get(key) for key in
                  ("source", "candidate_id", "step", "reason", "quote", "evidence_refs")}
        user = (
            "Trace ONLY this hearing's alleged deviation. Do not replace it with "
            "another unrelated error in the trajectory. The traced evidence must "
            "explain the same claim, even if its earliest cause is at another step.\n"
            "HEARING TARGET: " + json.dumps(target, ensure_ascii=False) + "\n\n" + user
        )
    context_block = ""
    if causal_context is not None:
        validate_hearing_context(causal_context, packet=packet, positive=positive,
                                 policy=config["causal_audit"])
        if (numeric_literal(delivered_result) != numeric_literal(positive["delivered_result"])
                or numeric_literal(correct_result) != numeric_literal(positive["correct_result"])):
            raise ValueError("causal context actual result inputs differ")
        context_block = (
            "\n\nCAUSAL CONTEXT: " + json.dumps(causal_context, ensure_ascii=False)
            + "\nThe certificate binds this referenced literal arithmetic result and its observed "
            "declared field copies. An observed consistent result does not prove that stale copies "
            "were corrected. The certificate has no decision or localization authority and does not "
            "prove general task materiality, semantic propagation or the first causal introduction. "
            "A verified task_certificate applies only to its stated literal arithmetic contract; "
            "verified_symbolic repair assumes the same declared copies and proves no tool/state replay. "
            "Corrected and unrelated errors cannot replace the target."
        )
    warnings: list[dict[str, str]] = []
    used_context_digest = None

    def call(repair: bool = False):
        nonlocal used_context_digest
        block = context_block
        effective_route = route
        if block:
            budget = current_added_budget()
            if budget is None:
                raise ValueError("causal context requires a case budget")
            try:
                if current_added_request_budget() is None:
                    budget.reserve_context(block)
            except AddedBudgetExceeded as exc:
                block = ""
                warnings.append({"attempt": "repair" if repair else "initial",
                                 "error": "causal_context_budget:" + exc.resource})
            else:
                # Each admission counts one context copy; repair is admitted separately.
                effective_route = {**route, "transport_retries": 0}
        used_context_digest = causal_context["digest"] if block else None
        return provider_call(route=effective_route, system=system,
                             user=user + block + (TRACER_CONTRACT_REPAIR if repair else ""),
                             purpose="step_tracer_repair" if repair else "step_tracer")

    try:
        parsed, meta = call()
    except ProviderError as exc:
        raise_if_terminal(exc)
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
    if result is None:
        warnings.append({"attempt": "initial", "error": f"contract:{error}"})
        try:
            parsed, meta = call(repair=True)
            result, error = _parse_tracer(parsed, packet)
        except ProviderError as exc:
            raise_if_terminal(exc)
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
        "hearing_target": dict(positive) if positive is not None else None,
        "causal_context_digest": used_context_digest,
        "view_meta": view_meta,
    }, {
        "stage": "step_tracer", "state": "ok",
        "attempts": int(meta.get("attempts") or 0),
        "view": "complete" if not view_meta.get("degraded") else "degraded",
        "warnings": warnings,
    }


__all__ = ["run_step_tracer"]
