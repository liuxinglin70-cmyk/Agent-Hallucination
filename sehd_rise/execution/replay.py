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

from typing import Any, Mapping

from sehd_rise.skills.genome import SkillGenome, create_baseline_genome
from sehd_rise.skills.policy import PolicyGenome
from sehd_rise.skills.l1_execution import execute_l1_rules
from sehd_rise.skills.capabilities import (
    L1_CONSTRUCTIVE_HARNESS_EXECUTOR,
    SKILL_CAPABILITY_REGISTRY,
)
from sehd_rise.detection.continuation import execute_constructive_rules
from sehd_rise.skills.rule import project_result_condition_features
from sehd_rise.detection.injection import (
    compile_genome_rules,
    compile_route_guidance,
    select_route_guidance,
)
from sehd_rise.detection.court.engine import adjudicate_case
from sehd_rise.detection.detector import _detector_record
from sehd_rise.detection.egress import conflict_gate_call, l1_call
from sehd_rise.detection.packet import build_packet
from sehd_rise.detection.transport import _responsible_step
from sehd_rise.platform.usage import ProviderUsageLedger
from sehd_rise.platform.gold import _evaluator_gold
from sehd_rise.causal.signals import (
    decision_authority_invalid,
    missing_decision_authority,
    resolve_authoritative_state,
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
    policy_genome: PolicyGenome | None = None,
    policy_scope: str | None = "working",
    provider_usage_ledger: ProviderUsageLedger | None = None,
    base_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run PACA-TCL v3.5 with deterministic L1 rule post-processing (plan B).

    ``policy_genome`` forwards the accepted L2 adjudication-policy items to
    the proof court; items are closed vocabulary and evaluated deterministically
    inside the court. The L1 genome's delta rules are NOT injected into provider
    prompts: the frozen detector runs once, then ``execute_l1_rules`` applies
    each rule's compiled transform to the court result. If ``genome`` ==
    baseline (no delta rules), the result is the plain court result unchanged.

    Route-guidance skills are the D2 two-phase counterpart: their applicability
    predicate names a court-result feature that only exists AFTER the court
    runs, so a guidance can only be gated against a prior result. When
    ``base_result`` is supplied (source/canary/contrast replay), each guidance's
    ``condition_spec`` is evaluated against that prior court result and only the
    matching entries are injected into the target provider route. Without a
    prior result (the initial base detection) no guidance is injected, because
    there is nothing to evaluate its predicate against — an unconditional
    injection would leak an accepted guidance onto every case.
    """
    baseline = create_baseline_genome()

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

    route_guidance: dict[str, tuple[str, ...]] | None = None
    compiled_guidance = compile_route_guidance(genome, baseline=baseline)
    if compiled_guidance:
        from sehd_rise.skills.capabilities import OWNER_PRODUCER_ROUTE
        prior = base_result
        if prior is None:
            # Two-phase gate: the guidance's applicability predicate names a
            # court-result feature, so it can only be evaluated against an
            # already-computed court result. Run one guidance-free pass to
            # obtain that prior, then decide which guidance (if any) to inject.
            prior = adjudicate_case(
                case_id=case_id, record=record, config=config,
                policy_items=policy_items, policy_registry=policy_registry,
                provider_usage_ledger=provider_usage_ledger, route_guidance=None,
            )
            if not isinstance(prior, dict):
                raise RuntimeError("detector returned a non-object result")
        selected = select_route_guidance(
            compiled_guidance, result=prior, record=record,
        )
        if selected:
            route_guidance = {
                OWNER_PRODUCER_ROUTE[owner]: blocks
                for owner, blocks in selected.items()
                if owner in OWNER_PRODUCER_ROUTE
            }

    result = adjudicate_case(
        case_id=case_id,
        record=record,
        config=config,
        policy_items=policy_items,
        policy_registry=policy_registry,
        provider_usage_ledger=provider_usage_ledger,
        route_guidance=route_guidance,
    )
    if not isinstance(result, dict):
        raise RuntimeError("detector returned a non-object result")

    # The result-level condition snapshot feeds reflection and prevalidation.
    result["condition_feature_trace"] = project_result_condition_features(
        result, record,
    )

    rules = compile_genome_rules(genome, baseline=baseline)
    if rules:
        constructive = [
            rule for rule in rules
            if SKILL_CAPABILITY_REGISTRY.require(
                str((rule[3] or {}).get("operation") or "")
            ).executor == L1_CONSTRUCTIVE_HARNESS_EXECUTOR
        ]
        transforms = [rule for rule in rules if rule not in constructive]
        constructive_execution = {"fired": [], "bound": [], "skipped": []}
        if constructive:
            result, constructive_execution = execute_constructive_rules(
                result, constructive, record=record, config=config,
                provider_usage_ledger=provider_usage_ledger,
            )
        result, transform_execution = execute_l1_rules(
            result, transforms, record=record,
        )
        l1_execution = {
            key: list(constructive_execution.get(key, []))
            + list(transform_execution.get(key, []))
            for key in ("fired", "bound", "skipped")
        }
        l1_execution["constructive_execution_proofs"] = (
            constructive_execution.get("constructive_execution_proofs", [])
        )
        l1_execution["terminal_state"] = result.get("state")
        result["l1_execution"] = l1_execution
        # Re-project the responsible_step terminal projection: the executor
        # updates responsible_event_id but not its source-step projection.
        proof = result.get("proof_adjudication")
        responsible = (
            proof.get("responsible_event_id") if isinstance(proof, Mapping) else None
        )
        if responsible is not None:
            result["responsible_step"] = _responsible_step(
                responsible, build_packet(record),
            )

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


def _run_constructive_continuation_with_genome(
    *, baseline_result: Mapping[str, Any], baseline_genome: SkillGenome,
    candidate_genome: SkillGenome, record: dict[str, Any], config: dict[str, Any],
    provider_usage_ledger: ProviderUsageLedger | None = None,
) -> dict[str, Any]:
    """Replay only newly-added constructive rules on the frozen source artifact."""
    baseline_rules = compile_genome_rules(baseline_genome)
    candidate_rules = compile_genome_rules(candidate_genome)

    def _signature(rule: tuple[Any, ...]) -> tuple[str, str, str]:
        return (
            str(rule[0]), str(rule[1]),
            str((rule[3] or {}).get("operation") or ""),
        )

    existing = {_signature(rule) for rule in baseline_rules}
    delta = [rule for rule in candidate_rules if _signature(rule) not in existing]
    constructive = [
        rule for rule in delta
        if SKILL_CAPABILITY_REGISTRY.require(
            str((rule[3] or {}).get("operation") or "")
        ).executor == L1_CONSTRUCTIVE_HARNESS_EXECUTOR
    ]
    if not constructive:
        raise RuntimeError("constructive continuation has no new constructive rule")
    result, execution = execute_constructive_rules(
        baseline_result, constructive, record=record, config=config,
        provider_usage_ledger=provider_usage_ledger,
        max_matches_per_rule=int(
            config.get("constructive_max_matches_per_rule", 8)
        ),
    )
    result["condition_feature_trace"] = project_result_condition_features(
        result, record,
    )
    result["l1_execution"] = execution
    if missing_decision_authority(result) or decision_authority_invalid(result):
        raise RuntimeError("constructive continuation returned invalid decision authority")
    resolve_authoritative_state(result)
    return result


def _run_mutation_call(
    *, budget_usage: dict[str, int],
    provider_usage_ledger: ProviderUsageLedger, **kwargs: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Count attribution/reflection calls separately from detector calls."""
    budget_usage["mutation_calls"] += 1
    try:
        parsed, meta = l1_call(
            route=kwargs["route"],
            system=kwargs["system"],
            user=kwargs["user"],
            purpose=str(kwargs.get("purpose") or "mutation"),
            ledger=provider_usage_ledger,
        )
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
    return parsed, meta


def _run_conflict_gate_call(
    *, route: dict[str, Any], system: str, user: str,
    budget_usage: dict[str, int] | None = None,
    provider_usage_ledger: ProviderUsageLedger | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Provider call for the LLM no-conflict gate (T5).

    Returns ``(result, meta)``; the caller fail-closes on any provider
    exception by treating it as a conflict. When ``budget_usage`` is supplied
    the call is counted as a conflict-gate call for run telemetry.
    """
    if budget_usage is not None:
        budget_usage["conflict_gate_calls"] += 1
    return conflict_gate_call(
        route=route, system=system, user=user,
        ledger=provider_usage_ledger,
    )


def _run_counted_detection(
    *, budget_kind: str, genome: SkillGenome, case_id: str,
    budget_usage: dict[str, int],
    records: Mapping[str, dict[str, Any]], config: dict[str, Any],
    provider_usage_ledger: ProviderUsageLedger,
    policy_genome: PolicyGenome | None = None,
    policy_scope: str | None = "working",
    base_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one detector call in a counted, label-free envelope.

    ``base_result`` is the prior court result used to gate route-guidance
    skills: only a guidance whose condition matches the prior result is
    injected into the replay. Without it (initial base detection) no guidance
    is injected — an unconditional injection would leak an accepted guidance
    onto every case.
    """
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
    if base_result is not None:
        replay_kwargs["base_result"] = base_result
    result = _run_detection_with_genome(
        genome=genome,
        case_id=case_id,
        record=_detector_record(records[case_id]),
        config=config,
        provider_usage_ledger=provider_usage_ledger,
        **replay_kwargs,
    )
    return result


def _run_counted_constructive_continuation(
    *, baseline_result: Mapping[str, Any], baseline_genome: SkillGenome,
    candidate_genome: SkillGenome, case_id: str,
    budget_usage: dict[str, int], records: Mapping[str, dict[str, Any]],
    config: dict[str, Any], provider_usage_ledger: ProviderUsageLedger,
) -> dict[str, Any]:
    """Budgeted source replay that never resamples the full detector."""
    budget_usage["provider_calls"] += 1
    budget_usage["source_replays"] += 1
    budget_usage["constructive_continuations"] = (
        budget_usage.get("constructive_continuations", 0) + 1
    )
    return _run_constructive_continuation_with_genome(
        baseline_result=baseline_result,
        baseline_genome=baseline_genome,
        candidate_genome=candidate_genome,
        record=_detector_record(records[case_id]),
        config=config,
        provider_usage_ledger=provider_usage_ledger,
    )


__all__ = [
    "_authority_failure_details",
    "_run_conflict_gate_call",
    "_run_constructive_continuation_with_genome",
    "_run_counted_constructive_continuation",
    "_run_detection_with_genome",
    "_run_mutation_call",
    "_run_counted_detection",
]
