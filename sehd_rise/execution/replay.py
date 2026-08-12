"""Counted, label-free detector replay and mutation-call accounting.

``_run_detection_with_genome`` runs PACA-TCL v3.5 with a genome's rules
globally injected (baseline + weak + strong items), returning the
provider's own bookkeeping as route-keyed execution traces.
``_run_counted_detection`` wraps one replay in the budget envelope (debit
before dispatch so runtime-invalid attempts are still auditable) and
records per-surface provider usage. ``_run_mutation_call`` counts
attribution/reflection calls separately from detector calls.
``_authority_failure_details`` keeps the fail-closed diagnostics on the
runtime-failure record instead of a bare exception string.

No evaluation logic lives here: the replay returns a detection result;
effect/fitness/canary/promotion reading happens in the evaluation layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from sehd_rise.skills.genome import SkillGenome, create_baseline_genome
from sehd_rise.skills.policy import PolicyGenome
from sehd_rise.detection.injection import integrated_engine_provider
from sehd_rise.detection.court.engine import adjudicate_case
from sehd_rise.detection.detector import _detector_record
from sehd_rise.detection.transport import json_object_provider_call
from sehd_rise.platform.usage import ProviderUsageLedger
from sehd_rise.platform.gold import _evaluator_gold
from sehd_rise.causal.signals import (
    decision_authority_invalid,
    missing_decision_authority,
    resolve_authoritative_state,
)
from sehd_rise.execution.trace import (
    _collect_applied_rule_trace,
    _collect_candidate_rule_trace,
    _collect_condition_feature_trace,
    _collect_condition_matched_rule_trace,
    _collect_condition_not_matched_rule_trace,
    _collect_injected_rule_trace,
    _collect_rule_effects_trace,
)


def _authority_failure_details(result: dict[str, Any]) -> dict[str, Any]:
    """Collect machine-readable failure diagnostics from a detector result.

    The returned mapping carries the same reason rows the detector surfaced
    plus per-provider error summaries, so a fail-closed case keeps its root
    cause on disk instead of a bare exception string.
    """
    authority = str(result.get("decision_authority") or "").strip().lower()
    proof = result.get("proof_adjudication")
    proof_authority = ""
    if isinstance(proof, Mapping):
        proof_authority = str(proof.get("decision_authority") or "").strip().lower()
    reasons: list[dict[str, Any]] = []
    for scope, source in (("result", result), ("proof", proof)):
        if not isinstance(source, Mapping):
            continue
        for row in source.get("authority_failure_reasons") or []:
            if isinstance(row, Mapping):
                reasons.append({"source": scope, **dict(row)})
    provider_errors: list[dict[str, Any]] = []
    providers = result.get("providers")
    if isinstance(providers, Mapping):
        for name, meta in providers.items():
            if isinstance(meta, Mapping) and meta.get("error_type"):
                provider_errors.append({
                    "name": str(name),
                    "error_type": str(meta.get("error_type") or ""),
                    "error": str(meta.get("error") or "")[:300],
                })
    return {
        "decision_authority": authority,
        "proof_decision_authority": proof_authority,
        "runtime_valid": result.get("runtime_valid"),
        "state": result.get("state"),
        "authority_failure_reasons": reasons,
        "provider_errors": provider_errors,
    }


def _run_detection_with_genome(
    genome: SkillGenome,
    case_id: str,
    record: dict[str, Any],
    config: dict[str, Any],
    output_dir: Path,
    policy_genome: PolicyGenome | None = None,
    policy_scope: str | None = "working",
) -> dict[str, Any]:
    """Run PACA-TCL v3.5 with global skill injection.

    ``policy_genome`` forwards the accepted L2 adjudication-policy items to
    the proof court. Items are closed vocabulary and evaluated
    deterministically inside the court, so the detector envelope itself is
    unchanged when no genome (or an empty one) is supplied.

    Replays use the same integrated provider boundary as source detection so
    genome-specific prompt injection remains scoped to one detector call.
    If genome == baseline, runs plain v3.5 without injection overhead.
    """
    baseline = create_baseline_genome()

    genome_dir = output_dir / "genomes" / genome.execution_hash
    genome_dir.mkdir(parents=True, exist_ok=True)

    # Evolution runs on the working scope: baseline + weak + strong items.
    # Conditional artifacts are not in the genome items (pending queue), and
    # an item whose registry dependency went pending/disabled is dropped at
    # projection time so the evaluator never sees an unexecutable policy.
    #
    # ``policy_scope=None`` is the CANDIDATE-VALIDATION channel: a candidate
    # genome under evaluation must replay with its FULL item set so a newly
    # proposed (still ``fitness_status=none``) item's effect is observable —
    # otherwise the replay could never fire the candidate and every L2
    # proposal would be silently rejected as no-effect.  Regular evolution
    # detection and deployment always use a real scope.
    if policy_genome is None or not policy_genome.items:
        policy_items = None
    elif policy_scope is None:
        policy_items = list(policy_genome.items)
    else:
        policy_items = policy_genome.items_for_scope(policy_scope)
    # The genome's own registry is the single vocabulary authority for its
    # items: an item may reference a predicate/action registered during this
    # run, and the evaluator must see that registry or the item is silently
    # skipped against the baseline vocabulary (which makes L2 evolution a
    # no-op).  Mirrors detect_record()'s policy_registry wiring.
    policy_registry = (
        policy_genome.registry if policy_genome is not None else None
    )

    compiled_rule_trace: dict[str, list[dict[str, str]]] = {}

    def invoke() -> dict[str, Any]:
        nonlocal compiled_rule_trace
        # Baseline and evolved genomes cross the same wrapper. With no delta
        # rules it leaves prompts byte-identical and only records the exact
        # pre-call condition-feature projection needed by reflection.
        with integrated_engine_provider(genome=genome, config=config) as compiled:
            if compiled is not None:
                compiled_rule_trace = compiled.rule_trace()
            return adjudicate_case(
                case_id=case_id,
                record=record,
                config=config,
                output_dir=genome_dir,
                policy_items=policy_items,
                policy_registry=policy_registry,
            )

    result = invoke()
    if not isinstance(result, dict):
        raise RuntimeError("detector returned a non-object result")
    result["condition_feature_trace"] = _collect_condition_feature_trace(result)
    if compiled_rule_trace:
        # Compiled rule trace: which evolved rules exist in this genome.
        # Audit telemetry only; it never participates in a verdict. The
        # per-route injected/applied/field-effect traces live in the result
        # too.
        result["compiled_rule_trace"] = compiled_rule_trace
        result["injected_rule_trace"] = _collect_injected_rule_trace(result)
        result["applied_rule_trace"] = _collect_applied_rule_trace(result)
        result["candidate_rule_trace"] = _collect_candidate_rule_trace(result)
        result["condition_matched_rule_trace"] = (
            _collect_condition_matched_rule_trace(result)
        )
        result["condition_not_matched_rule_trace"] = (
            _collect_condition_not_matched_rule_trace(result)
        )
        result["rule_effects_trace"] = _collect_rule_effects_trace(result)
    # Formal evolution must never consume rows that omit the explicit
    # authority field.  Compatibility evaluators may still inspect such rows,
    # but they cannot enter source fitness or champion promotion.  The
    # non-authoritative classification is the single adapter authority; the
    # runner only adds the missing-authority hard error on top of it.
    authority = str(result.get("decision_authority") or "").strip().lower()
    if missing_decision_authority(result):
        raise RuntimeError("detector returned missing decision authority")
    proof = result.get("proof_adjudication")
    proof_authority = (
        str(proof.get("decision_authority") or "").strip().lower()
        if isinstance(proof, Mapping) else ""
    )
    if decision_authority_invalid(result):
        # Fail closed with the full diagnostic on the exception: the
        # runtime-failure record persists it verbatim, so a fail-closed case
        # never needs an external replay to recover its root cause.
        details = _authority_failure_details(result)
        summaries = [
            f"{row.get('component', '?')}/{row.get('code', '?')}: "
            f"{row.get('error_type', '')} {str(row.get('error', ''))[:200]}"
            for row in details["authority_failure_reasons"]
        ]
        summaries.extend(
            f"provider[{row['name']}]: {row['error_type']} {row['error']}"
            for row in details["provider_errors"]
        )
        detail = " | ".join(summaries) or "no details"
        invalid = RuntimeError(
            "detector returned invalid decision authority: "
            f"top_level={authority or 'absent'!r}, "
            f"proof={proof_authority or 'absent'!r}; {detail}"
        )
        invalid.failure_details = details  # type: ignore[attr-defined]
        raise invalid
    try:
        resolve_authoritative_state(result)
    except ValueError as exc:
        raise RuntimeError(f"detector returned invalid state: {exc}") from exc
    return result


def _record_detector_provider_usage(
    ledger: ProviderUsageLedger,
    result: Mapping[str, Any],
    budget_kind: str,
) -> None:
    """Record the per-surface provider metas a detection result carries.

    The engine places one provider-meta dict per surface (usage, latency,
    cache fields) in ``result["providers"]``; some surfaces nest their metas
    (construct initial/repair, per-candidate focused prosecution), so walk
    one level.  Error-only dicts (no ``usage`` key) are skipped: they carry
    no billable tokens and the budget counters already account for the call.
    """
    providers = result.get("providers")
    if not isinstance(providers, Mapping):
        return
    purpose = f"detector:{budget_kind}"
    for surface, meta in providers.items():
        if isinstance(meta, Mapping) and "usage" in meta:
            ledger.record(purpose=f"{purpose}:{surface}", meta=meta)
            continue
        if isinstance(meta, Mapping):
            for sub in meta.values():
                if isinstance(sub, Mapping) and "usage" in sub:
                    ledger.record(purpose=f"{purpose}:{surface}", meta=sub)


def _run_mutation_call(
    *, budget_usage: dict[str, int], max_mutation_calls: int,
    provider_usage_ledger: ProviderUsageLedger, **kwargs: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Count attribution/reflection calls separately from detector calls."""
    if budget_usage["mutation_calls"] >= max_mutation_calls:
        budget_usage["mutation_budget_exhausted"] += 1
        raise RuntimeError("mutation_call_budget_exhausted")
    budget_usage["mutation_calls"] += 1
    try:
        parsed, meta = json_object_provider_call(**kwargs)
    except Exception:
        budget_usage["mutation_errors"] += 1
        raise
    if isinstance(meta, Mapping) and meta.get("cache_hit"):
        budget_usage["mutation_cache_hits"] += 1
    if isinstance(meta, Mapping):
        # Cache hits served zero network traffic; their recorded
        # transport attempts/repair flag belong to the original call and
        # must not be re-counted on every replay of the cached entry.
        if not meta.get("cache_hit"):
            budget_usage["mutation_transport_attempts"] += int(
                meta.get("transport_attempts") or 0
            )
            budget_usage["mutation_repairs"] += int(
                bool(meta.get("repair_used"))
            )
    provider_usage_ledger.record(
        purpose=str(kwargs.get("purpose") or "mutation"),
        meta=meta if isinstance(meta, Mapping) else None,
    )
    return parsed, meta


def _run_conflict_gate_call(
    *, route: dict[str, Any], system: str, user: str,
    budget_usage: dict[str, int] | None = None,
    mutation_call_limit: int | None = None,
    provider_usage_ledger: ProviderUsageLedger | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Provider call for the LLM no-conflict gate (T5).

    The structural gate owns its own budget bookkeeping so a conflict-gate call
    cannot exhaust the mutation budget (and vice versa): a skipped gate leaves
    mutation capacity intact, and a mutation burst cannot block the safety gate.
    Returns ``(result, meta)``; the caller fail-closes on any provider
    exception by treating it as a conflict.
    """
    if budget_usage is not None:
        if (
            mutation_call_limit is not None
            and budget_usage["mutation_calls"] >= mutation_call_limit
        ):
            budget_usage["mutation_budget_exhausted"] = (
                budget_usage.get("mutation_budget_exhausted", 0) + 1
            )
            raise RuntimeError("conflict_gate_budget_exhausted")
        budget_usage["mutation_calls"] += 1
    result, meta = json_object_provider_call(
        route=route,
        system=system,
        user=user,
        purpose="mutation",
    )
    if provider_usage_ledger is not None:
        provider_usage_ledger.record(
            purpose="conflict_gate",
            meta=meta if isinstance(meta, Mapping) else None,
        )
    return result, meta


def _run_counted_detection(
    *, budget_kind: str, genome: SkillGenome, case_id: str,
    output_path: Path, budget_usage: dict[str, int],
    records: Mapping[str, dict[str, Any]], config: dict[str, Any],
    provider_usage_ledger: ProviderUsageLedger,
    policy_genome: PolicyGenome | None = None,
    policy_scope: str | None = "working",
) -> dict[str, Any]:
    """Run one detector call in a counted, label-free envelope."""
    budget_usage["provider_calls"] += 1
    budget_usage["detector_calls"] += 1
    budget_field = {
        "source": "source_replays",
        "canary": "canary_cases",
        "contrast": "contrast_replays",
        "policy": "policy_replays",
        "probe": "probe_cases",
    }.get(budget_kind)
    if budget_field is not None:
        # Debit before dispatch so runtime-invalid attempts are auditable.
        budget_usage[budget_field] += 1
    replay_kwargs: dict[str, Any] = {}
    if policy_genome is not None:
        replay_kwargs["policy_genome"] = policy_genome
        replay_kwargs["policy_scope"] = policy_scope
    result = _run_detection_with_genome(
        genome=genome,
        case_id=case_id,
        record=_detector_record(records[case_id]),
        config=config,
        output_dir=output_path,
        **replay_kwargs,
    )
    # The engine surfaces one provider-meta dict per surface in the result's
    # ``providers`` map (with usage / latency / cache fields); walk it and
    # record every leaf into the same per-logical-call ledger the mutation
    # path uses.
    _record_detector_provider_usage(
        provider_usage_ledger, result, budget_kind,
    )
    return result


__all__ = [
    "_authority_failure_details",
    "_run_conflict_gate_call",
    "_run_detection_with_genome",
    "_record_detector_provider_usage",
    "_run_mutation_call",
    "_run_counted_detection",
]
