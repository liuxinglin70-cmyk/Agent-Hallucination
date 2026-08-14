"""Candidate-genome replay surface — the detector side of the evolution contract.

Extracted from the old ``sehd_rise.execution.replay`` envelope and re-exposed
as a public, budget-free API. The future ``sehd_rise2.evolution`` framework
replays candidate genomes through these functions; call accounting and
fitness judgment stay on the evolution side (they were deliberately not
migrated).

Semantics preserved from the original:

- ``policy_scope``: "working" replays baseline+weak+strong policy items;
  ``None`` replays the FULL item set — candidate validation must observe a
  newly proposed (still ``fitness_status=none``) item or every proposal dies
  as no-effect.
- ``base_result``: the prior court result that gates route-guidance skills.
  Their applicability predicate names a court-result feature that only
  exists AFTER the court runs, so a guidance can only be gated against a
  prior result. Without a prior result the replay runs one guidance-free
  pass first; an unconditional injection would leak an accepted guidance
  onto every case.
- Fail-closed: missing/invalid decision authority raises; the caller (the
  evolution framework) records the diagnostic and never treats it as
  evidence.
"""

from __future__ import annotations

from typing import Any, Mapping

from sehd_rise2.detector.authority import (
    decision_authority_invalid,
    missing_decision_authority,
    resolve_authoritative_state,
)
from sehd_rise2.detector.court.engine import adjudicate_case
from sehd_rise2.detector.pipeline.continuation import execute_constructive_rules
from sehd_rise2.detector.pipeline.core import _detector_record
from sehd_rise2.detector.pipeline.injection import (
    compile_genome_rules,
    compile_route_guidance,
    select_route_guidance,
)
from sehd_rise2.detector.pipeline.packet import build_packet
from sehd_rise2.detector.pipeline.transport import _responsible_step
from sehd_rise2.detector.runtime.usage import ProviderUsageLedger
from sehd_rise2.detector.skills.capabilities import (
    L1_CONSTRUCTIVE_HARNESS_EXECUTOR,
    SKILL_CAPABILITY_REGISTRY,
)
from sehd_rise2.detector.skills.genome import SkillGenome, create_baseline_genome
from sehd_rise2.detector.skills.l1_execution import execute_l1_rules
from sehd_rise2.detector.skills.policy import PolicyGenome
from sehd_rise2.detector.skills.rule import project_result_condition_features


def authority_failure_details(result: Mapping[str, Any]) -> dict[str, Any]:
    """Collect machine-readable fail-closed diagnostics from a detector result.

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


def run_replay(
    *,
    record: Mapping[str, Any],
    config: Mapping[str, Any],
    case_id: str = "case",
    genome: SkillGenome,
    policy_genome: PolicyGenome | None = None,
    policy_scope: str | None = "working",
    base_result: Mapping[str, Any] | None = None,
    provider_usage_ledger: ProviderUsageLedger | None = None,
) -> dict[str, Any]:
    """Run PACA-TCL v3.5 with one candidate genome (label-free).

    The L1 genome's rules run as deterministic post-court transforms; the L2
    policy items are evaluated inside the proof court. The record is
    sanitized through the detector firewall, so gold fields can never reach
    the provider surface from this entry either.
    """
    baseline = create_baseline_genome()
    clean_record = _detector_record(record)

    # Evolution runs on the working scope: baseline + weak + strong items.
    # ``policy_scope=None`` is the CANDIDATE-VALIDATION channel (full item
    # set); a regular scope is used by evolution detection and deployment.
    if policy_genome is None or not policy_genome.items:
        policy_items = None
    elif policy_scope is None:
        policy_items = list(policy_genome.items)
    else:
        policy_items = policy_genome.items_for_scope(policy_scope)
    # The genome's own registry is the single vocabulary authority for its
    # items, so the evaluator must see the same registry the item was
    # registered against (mirrors detect_record's policy_registry wiring).
    policy_registry = (
        policy_genome.registry if policy_genome is not None else None
    )

    route_guidance: dict[str, tuple[str, ...]] | None = None
    compiled_guidance = compile_route_guidance(genome, baseline=baseline)
    if compiled_guidance:
        from sehd_rise2.detector.skills.capabilities import OWNER_PRODUCER_ROUTE

        prior = dict(base_result) if base_result is not None else None
        if prior is None:
            # Two-phase gate: run one guidance-free pass to obtain a prior
            # court result, then decide which guidance (if any) to inject.
            prior = adjudicate_case(
                case_id=case_id, record=clean_record, config=config,
                policy_items=policy_items, policy_registry=policy_registry,
                provider_usage_ledger=provider_usage_ledger,
                route_guidance=None,
            )
            if not isinstance(prior, dict):
                raise RuntimeError("detector returned a non-object result")
        selected = select_route_guidance(
            compiled_guidance, result=prior, record=clean_record,
        )
        if selected:
            route_guidance = {
                OWNER_PRODUCER_ROUTE[owner]: blocks
                for owner, blocks in selected.items()
                if owner in OWNER_PRODUCER_ROUTE
            }

    result = adjudicate_case(
        case_id=case_id,
        record=clean_record,
        config=config,
        policy_items=policy_items,
        policy_registry=policy_registry,
        provider_usage_ledger=provider_usage_ledger,
        route_guidance=route_guidance,
    )
    if not isinstance(result, dict):
        raise RuntimeError("detector returned a non-object result")

    # The result-level condition snapshot feeds reflection and prevalidation
    # on the evolution side.
    result["condition_feature_trace"] = project_result_condition_features(
        result, clean_record,
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
                result, constructive, record=clean_record, config=config,
                provider_usage_ledger=provider_usage_ledger,
            )
        result, transform_execution = execute_l1_rules(
            result, transforms, record=clean_record,
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
                responsible, build_packet(clean_record),
            )

    # Formal evolution must never consume rows that omit the explicit
    # authority field or fail its authority contract.
    authority = str(result.get("decision_authority") or "").strip().lower()
    if missing_decision_authority(result):
        raise RuntimeError("detector returned missing decision authority")
    proof = result.get("proof_adjudication")
    proof_authority = (
        str(proof.get("decision_authority") or "").strip().lower()
        if isinstance(proof, Mapping) else ""
    )
    if decision_authority_invalid(result):
        details = authority_failure_details(result)
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


def run_constructive_replay(
    *,
    baseline_result: Mapping[str, Any],
    baseline_genome: SkillGenome,
    candidate_genome: SkillGenome,
    record: Mapping[str, Any],
    config: Mapping[str, Any],
    provider_usage_ledger: ProviderUsageLedger | None = None,
) -> dict[str, Any]:
    """Replay only newly-added constructive rules on the frozen source artifact.

    A source replay that never resamples the full detector: it applies the
    candidate's constructive delta to the baseline court result.
    """
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
    clean_record = _detector_record(record)
    result, execution = execute_constructive_rules(
        dict(baseline_result), constructive, record=clean_record, config=config,
        provider_usage_ledger=provider_usage_ledger,
        max_matches_per_rule=int(
            config.get("constructive_max_matches_per_rule", 8)
        ),
    )
    result["condition_feature_trace"] = project_result_condition_features(
        result, clean_record,
    )
    result["l1_execution"] = execution
    if missing_decision_authority(result) or decision_authority_invalid(result):
        raise RuntimeError(
            "constructive continuation returned invalid decision authority"
        )
    resolve_authoritative_state(result)
    return result


__all__ = [
    "authority_failure_details",
    "run_constructive_replay",
    "run_replay",
]
