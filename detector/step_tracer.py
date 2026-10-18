"""Root-cause step tracer for positive verdicts."""

from __future__ import annotations
from copy import deepcopy

import json
from typing import Any, Mapping

from detector.causal_audit import validate_hearing_context
from detector.causal_contracts import numeric_literal
from detector.risk_context import RISK_CONTEXT_SCHEMA
from detector.interventions import InterventionPlan, empty_plan
from detector.packet import (evidence_refs_support_step, event_id_for_step,
                             packet_features, packet_quote_error,
                             packet_reference_error, render_judge_view)
from detector.stage_retry import memo_stage
from detector.evidence_view import expand_view, referent_source_block
from detector.provider import ProviderError, merge_provider_meta, provider_call, provider_fallback_scope, raise_if_terminal
from detector.semantics import TRACER_CONTRACT_REPAIR, TRACER_SYSTEM, contract_repair_feedback
from detector.runtime import AddedBudgetExceeded, current_added_budget, current_added_request_budget
from detector.action_evidence import action_evidence_block


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
    intervention_prompt: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = plan or empty_plan()
    route = dict(config["judge_route"])
    route["_route_name"] = "step_tracer"
    view_text, view_meta = render_judge_view(packet)
    base_view_text = view_text
    if positive is not None:
        view_text, view_meta = expand_view(packet, view_text, view_meta, positive=positive, include_ancestors=True)
    system = TRACER_SYSTEM + (
        intervention_prompt if intervention_prompt is not None
        else plan.prompt_block(packet_features(packet), hook="tracer.system")
    )
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
    base_user = user.replace(view_text, base_view_text, 1)
    if positive is not None:
        user += referent_source_block(packet, positive)
    context_block = ""
    if causal_context is not None:
        validate_hearing_context(causal_context, packet=packet, positive=positive,
                                 policy=config.get("causal_audit") or {})
        diagnostic = causal_context.get("schema_version") == RISK_CONTEXT_SCHEMA
        matches = ((delivered_result == str(positive.get("delivered_result") or "")
                    and correct_result == str(positive.get("correct_result") or "")) if diagnostic else
                   (numeric_literal(delivered_result) == numeric_literal(positive["delivered_result"])
                    and numeric_literal(correct_result) == numeric_literal(positive["correct_result"])))
        if not matches:
            raise ValueError("causal context actual result inputs differ")
        context_block = (
            "\n\nRISK CONTEXT: " + json.dumps(causal_context, ensure_ascii=False)
            + "\nThese are bounded diagnostic priorities and excerpts, not verified errors or causal "
            "certificates. Inspect the provided trajectory and lossless requested expansions before deciding; "
            "unrequested channels can remain excerpted and gaps do not prove absence. Follow the same alleged "
            "error backwards through tool observations/human guidance and assistant actions; distinguish "
            "first origin from later adoption and delivery. High priority does not prove materiality, "
            "claim equivalence or the earliest cause. Missing roles remain unknown."
            " Single-next-event action audits derive legal boundaries from the prefix and compare "
            "only the recorded t+1 event. Divergence is a tracing priority, never a verdict. "
            "Verify that a failed action and any later success claim concern this hearing's "
            "same task obligation; inspect retry, available fallback, permission and correction "
            "before attributing responsibility. Follow renamed variables and wrapped copies "
            "through their actual source values; do not stop at an intermediate duplicate."
        ) if diagnostic else (
            "\n\nCAUSAL CONTEXT: " + json.dumps(causal_context, ensure_ascii=False)
            + "\nThe certificate binds this referenced literal arithmetic result and its observed "
            "declared field copies. An observed consistent result does not prove that stale copies "
            "were corrected. The certificate has no decision or localization authority and does not "
            "prove general task materiality, semantic propagation or the first causal introduction. "
            "A verified task_certificate applies only to its stated literal arithmetic contract; "
            "verified_symbolic repair assumes the same declared copies and proves no tool/state replay. "
            "Corrected and unrelated errors cannot replace the target."
        )
    def evaluate():
        warnings: list[dict[str, str]] = []
        used_context_digest = None

        meta: dict[str, Any] = {}
        action_block, action_admission = action_evidence_block(
            packet, (positive or {}).get("action_evidence") or [], config.get("causal_audit"), copies=3)

        def call(repair_feedback: str = ""):
            nonlocal used_context_digest, meta
            repair = bool(repair_feedback)
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
            try:
                repair_block = TRACER_CONTRACT_REPAIR + repair_feedback if repair else ""
                def fallback_text():
                    nonlocal used_context_digest
                    fallback_block = block
                    if fallback_block and current_added_request_budget() is None:
                        try:
                            current_added_budget().reserve_context(fallback_block)
                        except AddedBudgetExceeded as exc:
                            fallback_block = ""
                            used_context_digest = None
                            warnings.append({"attempt": "view_fallback", "error": "causal_context_budget:" + exc.resource})
                    return base_user + fallback_block + action_block + repair_block
                with provider_fallback_scope(fallback_text):
                    parsed, latest = provider_call(route=effective_route, system=system,
                                                  user=user + block + action_block + repair_block,
                                                  purpose="step_tracer_repair" if repair else "step_tracer")
            except ProviderError as exc:
                meta = merge_provider_meta(meta, {"attempts": exc.attempts})
                raise
            meta = merge_provider_meta(meta, latest)
            if latest.get("evidence_view_fallback") and "evidence_expansion" in view_meta:
                view_meta["evidence_expansion"]["actual_view"] = "base_projection"
            return parsed, meta

        try:
            parsed, meta = call()
        except ProviderError as exc:
            raise_if_terminal(exc)
            return {
                "ok": False, "parsed": None, "error": exc.message,
                "error_type": exc.error_type,
                "provider": meta,
            }, {
                "stage": "step_tracer", "state": "provider_error",
                "attempts": int(meta.get("attempts") or 0),
                "view": "complete" if not view_meta.get("degraded") else "degraded",
                "warnings": [],
            }
        result, error = _parse_tracer(parsed, packet)
        if result is None:
            warnings.append({"attempt": "initial", "error": f"contract:{error}"})
            try:
                parsed, meta = call(contract_repair_feedback(parsed, error))
                result, error = _parse_tracer(parsed, packet)
            except ProviderError as exc:
                raise_if_terminal(exc)
                return {
                    "ok": False, "parsed": None, "error": exc.message,
                    "error_type": exc.error_type,
                    "provider": meta,
                }, {
                    "stage": "step_tracer", "state": "provider_error",
                    "attempts": int(meta.get("attempts") or 0),
                    "view": "complete" if not view_meta.get("degraded") else "degraded",
                    "warnings": warnings,
                }
        if result is None:
            return {
                "ok": False, "parsed": None, "error": error,
                "error_type": "contract_error",
                "provider": meta,
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
            "causal_context": deepcopy(causal_context),
            "action_evidence_admission": action_admission,
            "view_meta": view_meta,
        }, {
            "stage": "step_tracer", "state": "ok",
            "attempts": int(meta.get("attempts") or 0),
            "view": "complete" if not view_meta.get("degraded") else "degraded",
            "warnings": warnings,
        }

    return memo_stage('step_tracer', {"packet": packet, "route": route, "system": system, "user": user, "positive": positive, "context": causal_context, "view_meta": view_meta}, evaluate)


__all__ = ["run_step_tracer"]
