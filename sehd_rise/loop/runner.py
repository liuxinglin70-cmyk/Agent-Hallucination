"""SEHD-RISE loop runner: the single L1/L2 orchestration path on RepairPlan.

C6 converges the two frozen orchestrations (L1 provider-rule reflection and
L2 adjudication-policy reflection) onto one per-case pipeline:

    case queue -> detection -> causal cut -> causal episode
      -> repairability verdict -> RepairPlan
      -> planning (hypothesis search) -> execution (dispatch/edge move/blocker)
      -> evaluation (fitness + canary/contrast) -> ledger -> experience -> meta

The repairability authority (``causal/repairability.analyze``) is the single
routing decision: a REPAIRABLE episode is executed through a RepairPlan, a
NON_EVOLVABLE episode is a stable detection, an UNADDRESSABLE_FRAMEWORK_DEFECT
is counted on its own funnel line (C2) and a FRAMEWORK_DEFECT is an infra
outcome. Every case deposits exactly one §2.9 outcome into the funnel; the
convergence window only consumes complete repair attempts whose fitness
verdict found no acceptable improvement (C9), and an accepted edit carries
the full credit chain from dispatch to canary preservation (C10).

The heavy stage functions live in the sibling modules shipped in phase 9a
(state/result/ledger/persistence/probes/shadow); this module owns the
orchestration, the L1 reflection-to-shadow-batch path, the L2 policy path and
the Meta Review aggregation. The frozen ``EvolutionLoop.run`` /
``_run_l2_policy_case_impl`` / ``_initialize_state`` bodies are rewritten
here; the frozen source keeps only its own copy and is never imported by
this package.
"""
from __future__ import annotations

import hashlib
import json
import time
import traceback
import uuid
from collections import deque
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from sehd_rise.causal.cut import (
    REASON_GOLD_FALSE_NEGATIVE_CLEAN,
    REASON_GOLD_FALSE_POSITIVE,
    build_causal_cut,
)
from sehd_rise.causal.episode import (
    CausalFailureEpisode,
    build_causal_episode,
    build_runtime_observation,
    project_for_prompt,
)
from sehd_rise.causal.plan import build_repair_plan
from sehd_rise.causal.repairability import (
    RepairabilityStatus,
    analyze as analyze_repairability,
)
from sehd_rise.causal.signals import (
    classify_runtime_error,
    decision_authority_invalid,
    project_detection,
    resolve_evolution_state,
    runtime_invalid_signal,
)
from sehd_rise.detection.detector import _detector_record
from sehd_rise.detection.policy import build_adjudication_facts
from sehd_rise.evaluation.canary import (
    canary_preserved,
    evict_stable_canaries,
    fitness_gold_label,
    selected_canaries,
)
from sehd_rise.evaluation.effect import (
    _attribute_intervention_fitness,
    _baseline_surviving_error_certificate_ids,
    _classify_replay_no_effect,
    _cross_case_no_effect_duplicate,
    _enforce_expected_effect_observation,
    _expected_effect_audit,
    _no_effect_reason_for,
)
from sehd_rise.evaluation.fitness import (
    FitnessAssessment,
    assess_source_fitness,
    task_state_score,
)
from sehd_rise.evaluation.gate import create_structural_gate
from sehd_rise.execution.replay import (
    _run_conflict_gate_call,
    _run_counted_detection,
    _run_mutation_call,
)
from sehd_rise.execution.trace import _injected_applied_flags
from sehd_rise.experience.memory import ExperienceMemory
from sehd_rise.loop.ledger import (
    GateEventLedger,
    _build_gate_event_ledger,
    _record_attempt,
    _record_gate_event,
    aggregate_gate_experience,
)
from sehd_rise.loop.machine import Outcome, OutcomeFunnel
from sehd_rise.loop.persistence import (
    _PersistenceContext,
    _persist_progress,
    _restore_checkpoint_state,
    _save_evolution_results,
)
from sehd_rise.loop.probes import (
    PendingProbeCommit,
    _run_development_probe_impl,
)
from sehd_rise.loop.records import (
    attribute_failure_for_result,
    validate_evolution_params,
)
from sehd_rise.loop.result import (
    EvolutionStep,
    OnlineEvolutionResult,
    ProvisionalEdit,
    _build_evolution_result,
)
from sehd_rise.loop.shadow import (
    _evict_stable_canaries,
    _flush_shadow_batch_impl,
    _shadow_commit_eligible,
    _structured_outcome,
    outcome_class_for,
)
from sehd_rise.loop.state import RunState
from sehd_rise.meta.review import (
    FEEDBACK_POLICY_ROUTE_UNAVAILABLE,
    build_drift_snapshot,
    commit_validated_strategy_edits,
    generate_strategy_suggestions,
    project_strategy_for_prompt,
)
from sehd_rise.meta.strategy import StrategyState, create_baseline_strategy
from sehd_rise.platform.gold import _evaluator_gold
from sehd_rise.platform.jsonio import atomic_write_json
from sehd_rise.platform.routes import has_provider_credential, resolve_provider_route
from sehd_rise.platform.usage import ProviderUsageLedger
from sehd_rise.planning.candidates import (
    _allowed_l1_modules,
    _candidate_direction_signature,
    _filter_policy_candidates_by_gold,
    _generation_module_requirement,
    _normalize_candidate_from_repair_plan,
    _policy_rejection_signature,
    _prevalidate_candidate,
    _rejected_proposal_duplicate,
)
from sehd_rise.planning.feedback import (
    _build_mutation_retry_feedback,
    _build_structural_system_constraints,
    _structural_reject_category,
    _structural_rejection_retryable,
)
from sehd_rise.planning.l1 import (
    RejectedEditEntry,
    RejectedEditProposal,
    build_accepted_edits_summary,
    build_capacity_guidance,
    build_causal_reflection_prompt,
    build_failure_diagnosis_prompt,
    build_intervention_critic_prompt,
    extract_intervention_candidates,
    format_detection_context_for_llm,
    format_skill_context_for_llm,
    sanitize_diagnosis,
    select_intervention_candidate,
)
from sehd_rise.planning.l2 import (
    PolicyPlan,
    build_policy_selection_prompt,
    plan_policy_candidates,
    select_policy_candidate,
)
from sehd_rise.planning.search import (
    _abstention_ladder_rung_complete,
    _contrast_inversion,
    _select_contrast_neighbor,
    _trim_contrast_registry,
)
from sehd_rise.skills.action import compile_action_spec, sanitize_action_spec
from sehd_rise.skills.genome import (
    EXPECTED_DIRECTIONS,
    EXPECTED_SCOPES,
    RuleSpec,
    SkillEdit,
    SkillGenome,
    SkillOp,
    _detect_rule_direction,
    apply_edit,
    create_baseline_genome,
    render_rule,
    validate_expected_effect_contract,
    validate_rule_spec,
)
from sehd_rise.skills.policy import (
    FITNESS_STATUS_STRONG,
    FITNESS_STATUS_WEAK,
    PENDING_STATUS_PENDING,
    PolicyGenome,
    PolicyOp,
    apply_policy_edit,
    create_baseline_policy_genome,
    validate_policy_edit,
    validate_policy_genome_items,
)
from sehd_rise.skills.rule import sanitize_condition_spec
from sehd_rise.skills.vocabulary.contracts import (
    LAYER_DETECTOR,
    LAYER_L1,
    LAYER_L2,
    MODULE_IDS,
    ROUTE_CANDIDATE_BLOCKED_BY_PROTECTION,
    ROUTE_EXECUTED,
    ROUTE_NO_REGISTERED_RUNTIME_ACTION,
    ROUTE_RUNTIME_PRECONDITION_NOT_SATISFIED,
)
from sehd_rise.skills.vocabulary.charters import MODULE_OBSERVABLE_FIELDS
from sehd_rise.skills.vocabulary.evolution import (
    PURPOSE_REGISTRY_REFLECTION,
    REGISTRY_REQUIRED_VALIDATION,
    apply_registry_edit,
    build_registry_selection_prompt,
    compile_registration_candidate,
    plan_registry_candidates,
    promote_pending_registry_entries,
    promoted_intent_candidates,
    select_registry_candidate,
)
from sehd_rise.typing.classifier import HallucinationType, HallucinationTypeClassifier
from sehd_rise.typing.guidance import TypeGuidance
from sehd_rise.typing.slices import TypeAwareFitness

# ============================================================
# Frozen-source pure helpers
# ============================================================


def _infer_evolution_direction(state: str, gold: Any) -> str:
    """Infer the evolution direction from terminal state and gold.

    Gold-supervised: the label names the correct pole, so a disagreement
    moves toward the gold-implied state. Self-supervised: only unresolved
    cases need evolution (toward a decisive judgment); an already-decided
    state is never auto-flipped.
    """
    if isinstance(gold, bool):
        if gold:
            if state == "unresolved":
                return "unresolved_to_error"
            if state == "verified_clean":
                return "clean_to_error"
            return "no_evolution"
        if state == "unresolved":
            return "unresolved_to_clean"
        if state == "verified_error":
            return "error_to_clean"
        return "no_evolution"
    if state == "unresolved":
        return "unresolved_to_decisive"
    return "process_repair"


def _no_edit_reason_categories(reason: str) -> list[str]:
    """Reduce free-form mutator abstention text to safe learning categories."""
    lowered = str(reason or "").lower()
    categories = ["model_no_edit"]
    if "baseline" in lowered and any(
        token in lowered for token in ("cover", "already", "sufficient")
    ):
        categories.append("baseline_coverage_claimed")
    if any(
        token in lowered
        for token in ("no observable", "lacks observable", "no concrete")
    ):
        categories.append("insufficient_observable_evidence")
    if any(
        token in lowered for token in ("unsafe", "speculative", "false positive")
    ):
        categories.append("unsafe_generalization_risk")
    return categories


def _classify_mutation_failure(reason: str) -> str:
    """Reduce a mutation invalid-schema reason to a stable retry-feedback class.

    The category is the only case-agnostic signal fed back into the retry
    prompt; raw provider text is never echoed back. The deterministic
    prevalidation detail is closed-vocabulary and safe to carry.
    """
    text = str(reason or "")
    if text.startswith("all_candidates_prevalidation_rejected:"):
        detail = text[len("all_candidates_prevalidation_rejected:"):]
        return detail if detail else "mutation_invalid_schema"
    if text.startswith("single_reflection_runtime_error"):
        return "mutation_provider_exception"
    if "operation=" in text and "content_length=0" in text:
        return "mutation_empty_content"
    if "operation=" in text:
        return "mutation_invalid_operation"
    if "json" in text.lower() or "parse" in text.lower():
        return "mutation_unparseable_json"
    return "mutation_invalid_schema"


def _localization_bias_signal(
    detection_result: Mapping[str, Any] | None,
    *,
    last_audited_step: int | None = None,
) -> str:
    """Label-free telemetry for terminal-step localization bias.

    A verified_error localized by the temporal fallback onto the final
    audited step likely skipped earlier sufficient-evidence origins.
    Diagnostic only: the signal feeds routing telemetry, never the verdict.
    """
    detection_result = detection_result or {}
    if str(detection_result.get("state") or "") != "verified_error":
        return ""
    if str(
        detection_result.get("localization_status") or ""
    ) != "failed_temporal_fallback":
        return ""
    responsible_step = detection_result.get("responsible_step")
    if responsible_step is None:
        return ""
    if last_audited_step is None or str(responsible_step) != str(last_audited_step):
        return ""
    return "terminal_step_bias"


def _llm_no_conflict_gate(
    new_rule: str,
    existing_rules: Sequence[str],
    *,
    target_module: str,
    mutation_route: dict[str, Any] | None,
    budget_usage: dict[str, int] | None = None,
    provider_usage_ledger: ProviderUsageLedger | None = None,
) -> tuple[bool, str, list[int], bool]:
    """LLM no-conflict gate (T5): semantic conflict check on a new rule.

    Two rules conflict when, under the same trigger conditions, they
    prescribe contradictory verdicts or one negates the other; mere
    condition overlap or complementarity is NOT a conflict. The gate runs
    only when a live provider credential is configured; a skipped gate
    returns ``(True, reason, [], False)`` so a caller never mistakes a
    skipped gate for a verified "no conflict". When the gate runs it is
    fail-closed: any provider exception or parse failure is a conflict.
    """
    existing = [rule for rule in existing_rules if rule and rule.strip()]
    if not existing:
        return True, "no_existing_rules", [], False
    if mutation_route is None or not has_provider_credential(mutation_route):
        return True, "skipped_no_live_route", [], False
    system_prompt = (
        "你是规则冲突判定器。判断新增规则是否与目标模块已有规则存在语义冲突。"
        "冲突指：在相同触发条件下，两条规则给出相互矛盾的判定或动作，或一条规则"
        "直接否定另一条的结论。仅条件重叠或互补不构成冲突。"
    )
    rules_block = "\n".join(f"[{i}] {rule}" for i, rule in enumerate(existing))
    user_prompt = (
        f"【目标模块】{target_module}\n"
        f"【已有规则】\n{rules_block}\n\n"
        f"【新增规则】\n{new_rule}\n\n"
        "判断新增规则是否与已有规则冲突。仅返回JSON："
        '{"decision":"pass"或"conflict","reason":"简短理由",'
        '"conflicting_rule_indices":[冲突的已有规则索引]}'
    )
    try:
        result, meta = _run_conflict_gate_call(
            route=mutation_route,
            system=system_prompt,
            user=user_prompt,
            budget_usage=budget_usage,
            provider_usage_ledger=provider_usage_ledger,
        )
    except Exception:
        return False, "provider_exception", [], True
    if budget_usage is not None and isinstance(meta, Mapping) and not meta.get("cache_hit"):
        budget_usage["conflict_gate_transport_attempts"] = (
            budget_usage.get("conflict_gate_transport_attempts", 0)
            + int(meta.get("transport_attempts") or 0)
        )
        budget_usage["conflict_gate_repairs"] = (
            budget_usage.get("conflict_gate_repairs", 0)
            + int(bool(meta.get("repair_used")))
        )
    if not isinstance(result, dict):
        return False, "parse_failure", [], True
    decision = str(result.get("decision", "")).strip().lower()
    reason = str(result.get("reason", "")).strip() or "llm_conflict"
    raw_indices = result.get("conflicting_rule_indices", [])
    if not isinstance(raw_indices, list):
        raw_indices = []
    indices: list[int] = [
        int(index) for index in raw_indices
        if isinstance(index, int)
        or (isinstance(index, str) and index.strip().lstrip("-").isdigit())
    ]
    if decision != "pass":
        return False, reason, indices, True
    return True, reason, indices, True


def _run_registry_reflection_impl(
    *,
    attribution: Any,
    facts_summary: Mapping[str, Any],
    case_id: str,
    policy_genome: PolicyGenome,
    mutation_route: dict[str, Any] | None,
    run_mutation_call: Any,
    output_dir: Path,
    l2_registry_registrations: list[dict[str, Any]],
) -> None:
    """Extend the L2 vocabulary when no runtime action can repair an
    attribution. A registered entry is pending (shadow-only) on first
    registration and enters the pending validation queue, where later
    independent observations decide its promotion; the genome's registry is
    the single authority, so the new state is checkpointed with the policy
    genome."""
    if mutation_route is None:
        return
    candidates = plan_registry_candidates(
        attribution=attribution, registry=policy_genome.registry,
    )
    if not candidates:
        return
    # One bounded retry on transport/parse failure, symmetric with the
    # L1/L2 proposal budget: a single provider wobble must not silently
    # forfeit the only call that can extend the vocabulary.
    system_prompt, user_prompt = build_registry_selection_prompt(
        attribution=attribution,
        facts_summary=facts_summary,
        candidates=candidates,
    )
    parsed: Any = None
    last_exc: Exception | None = None
    for _attempt in range(2):
        try:
            parsed, _meta = run_mutation_call(
                route=mutation_route,
                purpose=PURPOSE_REGISTRY_REFLECTION,
                system=system_prompt,
                user=user_prompt,
            )
            last_exc = None
            break
        except Exception as exc:  # provider transport/parse failure — retried
            last_exc = exc
    if last_exc is not None:
        l2_registry_registrations.append({
            "outcome": "reflection_call_failed",
            "reason": f"{type(last_exc).__name__}:{str(last_exc)[:200]}",
        })
        return
    candidate, select_reason = select_registry_candidate(
        parsed, candidates, registry=policy_genome.registry,
    )
    if candidate is None:
        l2_registry_registrations.append({
            "outcome": "no_registration",
            "reason": select_reason,
        })
        return
    if select_reason:
        l2_registry_registrations.append({
            "outcome": "intent_dropped",
            "target": candidate.target,
            "reason": select_reason,
        })
    try:
        edit_kwargs = compile_registration_candidate(
            candidate, policy_genome.registry,
        )
    except ValueError as exc:
        l2_registry_registrations.append({
            "outcome": "shadow_compile_rejected",
            "target": candidate.target,
            "reason": str(exc)[:200],
        })
        return
    new_registry = apply_registry_edit(
        policy_genome.registry,
        **edit_kwargs,
        reason_codes=(attribution.reason_code,),
    )
    policy_genome.registry = new_registry
    applied_edit = new_registry.audit[-1]
    l2_registry_registrations.append({
        "outcome": "registered",
        "edit_id": applied_edit.edit_id,
        "operation": applied_edit.operation,
        "target": applied_edit.target,
    })


_CONTRAST_RESOLVED_STATES = frozenset({"verified_error", "verified_clean"})

# Highest-value learning signals: a gold-locked polarity flip (FP / FN-clean)
# names the detector's exact error pole, so these cases earn extra search
# budget. Everything else uses the configured cap unchanged. The ceiling is a
# sanity bound, not a knob: it only caps how far the adaptive bonus can go.
_GOLD_POLARITY_REASON_CODES = frozenset({
    REASON_GOLD_FALSE_POSITIVE,
    REASON_GOLD_FALSE_NEGATIVE_CLEAN,
})
_MAX_ATTEMPT_CAP = 6
_GOLD_POLARITY_ATTEMPT_BONUS = 2


def _attempt_cap(base: int, reason_code: str) -> int:
    """Search budget for one case's mutation retries.

    A gold-locked polarity flip (FP / FN-clean) names the detector's exact
    error pole — the highest-value learning signal — so it earns a bounded
    bonus over the configured cap. All other cases use the configured cap
    unchanged, so this never changes behavior for the default self-supervised
    / process-repair path.
    """
    if reason_code in _GOLD_POLARITY_REASON_CODES:
        return min(base + _GOLD_POLARITY_ATTEMPT_BONUS, _MAX_ATTEMPT_CAP)
    return base


@dataclass(frozen=True)
class _ReflectionSignalView:
    """ReflectionSignal view over one projected detection signal.

    ``build_causal_reflection_prompt`` reads the attribution signal through
    the closed ``ReflectionSignal`` Protocol (error_type, trigger_source,
    target_module, evidence_summary plus the flat CRS/coverage readings).
    The causal ``DetectionSignal`` carries the same facts nested (CRSSignal /
    CoverageReading) and derives the classifier words from the cut, so this
    view flattens them without re-walking the raw row.
    """

    signal: Any
    owner: str
    reason_code: str

    @property
    def error_type(self) -> str:
        # The reflection direction contract and the polarity admission pool
        # consume the classifier's closed error_type vocabulary (false_positive
        # / false_negative_clean / unresolved_should_*). The causal reason_code
        # is NOT part of that vocabulary, so falling back to it silently
        # disabled both the direction contract and the polarity filter for every
        # gold-locked FP/FN case (the reflection prompt then offered the full
        # opcode set and the admission pool never fired). Derive the polarity
        # token from the gold label + terminal state — the same reading the
        # frozen reflection engine consumed — and keep appeal breakages as a
        # process diagnostic that never maps to a polarity.
        gold = self.signal.gold_label
        if self.signal.has_gold and gold is not None:
            state = self.signal.state
            if state == "verified_error" and not gold:
                return "false_positive"
            if state == "verified_clean" and gold:
                return "false_negative_clean"
            if state == "unresolved":
                return "unresolved_should_error" if gold else "unresolved_should_clean"
        return str(self.signal.diagnostics.get("appeal_error_type") or "")

    @property
    def trigger_source(self) -> str:
        diagnostics = self.signal.diagnostics or {}
        if diagnostics.get("appeal_error_type"):
            return "appeal_failure"
        if int(diagnostics.get("focused_quarantined_candidate_count") or 0) > 0:
            return "focused_quarantine"
        if str(diagnostics.get("localization_status") or "") == (
            "failed_temporal_fallback"
        ):
            return "localization_fallback"
        return "detection_trigger"

    @property
    def target_module(self) -> str:
        return self.owner

    @property
    def evidence_summary(self) -> str:
        diagnostics = self.signal.diagnostics or {}
        parts: list[str] = []
        for key in (
            "localization_status",
            "appeal_status",
            "focused_quarantined_candidate_count",
            "appeal_error_type",
        ):
            value = diagnostics.get(key)
            if value not in (None, "", "not_applicable"):
                parts.append(f"{key}={value}")
        return " | ".join(parts)

    @property
    def crs_scores(self) -> Mapping[str, float]:
        return dict(self.signal.crs.scores)

    @property
    def crs_source(self) -> str:
        return str(self.signal.crs.source or "")

    @property
    def crs_aggregate(self) -> float:
        return float(self.signal.crs.aggregate or 0.0)

    @property
    def gold_reflection(self) -> bool:
        return bool(getattr(self.signal, "gold_reflection", False))

    @property
    def gold_label(self) -> bool | None:
        return self.signal.gold_label

    @property
    def state(self) -> str:
        return str(self.signal.state or "")

    @property
    def defense_coverage_state(self) -> str:
        return str(self.signal.defense_coverage.state or "")

    @property
    def prosecution_coverage(self) -> float | None:
        return self.signal.prosecution_coverage.score

    @property
    def prosecution_coverage_state(self) -> str:
        return str(self.signal.prosecution_coverage.state or "")

    @property
    def defense_coverage(self) -> float | None:
        return self.signal.defense_coverage.score

    @property
    def candidate_count(self) -> int:
        return int(self.signal.candidate_count or 0)


@dataclass(frozen=True)
class _L2Attribution:
    """Structural L2 attribution view consumed by the policy planner.

    The policy planner declares the shape it reads (reason_code, evidence,
    to_dict); this is the runner's concrete record built from the causal
    cut so the L2 loop never touches the classifier internals.
    """

    layer: str
    artifact: str
    reason_code: str
    evidence: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "artifact": self.artifact,
            "reason_code": self.reason_code,
            "evidence": dict(self.evidence),
        }


def _triggered(
    result: Mapping[str, Any], *, use_gold: bool, gold_label: bool | None,
) -> bool:
    """Whether a detection result is an evolvable (triggered) episode.

    The trigger is the Repairability Authority's ``REPAIRABLE`` verdict:
    project -> cut -> episode -> analyze, exactly like the canary guard.
    """
    payload = dict(result)
    if use_gold and gold_label is not None:
        payload["gold_label"] = gold_label
    signal = project_detection(payload, use_gold=use_gold)
    if signal.diagnostic_only:
        return False
    cut = build_causal_cut(signal)
    episode = build_causal_episode(
        signal=signal,
        cut=cut,
        record_key=str(payload.get("case_id") or ""),
    )
    verdict = analyze_repairability(episode)
    return verdict.status is RepairabilityStatus.REPAIRABLE


def _l2_outcome_for(
    decision: str, *, reason: str, route: str,
) -> Outcome:
    """Map an L2 policy-loop decision onto its §2.9 funnel terminal.

    The closed outcome vocabulary is the only ``gate_decision`` the loop
    writes; the frozen per-layer words (``accept_policy``,
    ``reject_policy_*``, ``no_policy``) are resolved here onto the funnel
    so a GateEvent grades the same terminal the funnel counts. The
    ``no_policy`` family splits by cause per the §2.9 taxonomy: no route /
    reflection failure -> infra, no candidate -> no hypothesis, invalid
    edit -> invalid proposal, unsatisfied runtime precondition -> routing
    failure.
    """
    if decision == "accept_policy":
        return Outcome.ACCEPTED
    if decision == "reject_policy_no_effect":
        return Outcome.SOURCE_NOT_IMPROVED
    if decision == "reject_policy_fitness":
        if "canary" in str(reason):
            return Outcome.CANARY_REGRESSION
        return Outcome.SOURCE_NOT_IMPROVED
    if decision == "reject_policy_replay_error":
        return Outcome.INFRA_FAILURE
    # no_policy family: nothing executable was proposed.
    text = str(reason or "")
    if route == ROUTE_RUNTIME_PRECONDITION_NOT_SATISFIED:
        return Outcome.ROUTING_FAILURE
    if route == ROUTE_CANDIDATE_BLOCKED_BY_PROTECTION:
        return Outcome.INVALID_PROPOSAL
    if "invalid_policy_edit" in text or "all_candidates_filtered_anti_gold" in text:
        return Outcome.INVALID_PROPOSAL
    if (
        "no_mutation_route" in text
        or "reflection_call_failed" in text
    ):
        return Outcome.INFRA_FAILURE
    return Outcome.NO_HYPOTHESIS


class _TypeEvolutionAdapter:
    """Duck-typed type evolution surface the shadow batch consumes.

    The shadow batch calls ``_infer_evolution_direction`` and
    ``generate_type_guidance``; both are pure derivations owned by the loop
    layer / typing layer, so this tiny adapter binds them without a
    type-conditioned store object.
    """

    def __init__(self) -> None:
        self._guidance = TypeGuidance()

    def _infer_evolution_direction(self, state: str, gold: Any) -> str:
        return _infer_evolution_direction(state, gold)

    def generate_type_guidance(
        self, hallucination_type: HallucinationType, evolution_direction: str,
    ) -> str:
        return self._guidance.generate_type_guidance(
            hallucination_type, evolution_direction,
        )


class EvolutionLoop:
    """Per-case online evolution loop on the single RepairPlan pipeline.

    Encapsulates the run state and every stage of the C6-converged path:
    state initialization (with checkpoint resume), the case work queue, the
    L1 reflection / structural-gate / shadow-batch chain and the L2 policy
    chain, and the final Meta Review aggregation. The stage functions in the
    sibling phase-9a modules read and write ``self.state.<field>`` through
    ``RunState``; this class owns the orchestration and the run-scoped
    collections (budget, canaries, audits) that the stage functions consume.
    """

    def __init__(self, *,
    evolution_case_ids: Sequence[str],
    records: Mapping[str, dict[str, Any]],
    config: dict[str, Any],
    output_dir: Path,
    use_gold: bool = False,
    mutation_route: dict[str, Any] | None = None,
    module_cooldown: int = 0,
    max_total_edits: int = 200,
    convergence_window: int = 20,
    fitness_check: bool = True,
    fitness_batch_size: int = 1,
    fitness_use_gold: bool = False,
    experience_memory_path: Path | None = None,
    canary_limit: int = 2,
    canary_case_ids: Sequence[str] = (),
    validation_probe_every: int = 0,
    probe_ids: Sequence[str] = (),
    probe_limit: int = 2,
    validation_probe_trigger_threshold: int = 0,
    max_mutation_attempts_per_case: int = 2,
    initial_genome: SkillGenome | None = None,
    initial_policy_genome: PolicyGenome | None = None,
    pro_mutation_route: dict[str, Any] | None = None,
    run_id: str | None = None,
    resume_from: Path | None = None,
    gold_reflection: bool = False,
    probe_experience_record: bool = False,
    reflection_candidate_count: int = 1,
    ) -> None:
        self.evolution_case_ids = list(evolution_case_ids)
        self.records = records
        self.config = config
        self.output_dir = Path(output_dir)
        self.use_gold = use_gold
        self.mutation_route = mutation_route
        self.module_cooldown = module_cooldown
        self.max_total_edits = max_total_edits
        self.convergence_window = convergence_window
        self.fitness_check = fitness_check
        self.fitness_batch_size = fitness_batch_size
        self.fitness_use_gold = fitness_use_gold
        self.experience_memory_path = experience_memory_path
        self.canary_limit = canary_limit
        self.canary_case_ids = tuple(canary_case_ids)
        self.validation_probe_every = validation_probe_every
        self.probe_ids = tuple(probe_ids)
        self.probe_limit = probe_limit
        self.validation_probe_trigger_threshold = validation_probe_trigger_threshold
        self.max_mutation_attempts_per_case = max_mutation_attempts_per_case
        self.initial_genome = initial_genome
        self.initial_policy_genome = initial_policy_genome
        self.pro_mutation_route = pro_mutation_route
        self.run_id = run_id
        self.resume_from = Path(resume_from) if resume_from is not None else None
        self.gold_reflection = gold_reflection
        self.probe_experience_record = probe_experience_record
        self.reflection_candidate_count = reflection_candidate_count
        # The §2.9 funnel is the single terminal-count authority (C8/C2):
        # every case deposits exactly one outcome here and the final result
        # derives its counters from this funnel, never from the per-step
        # words.
        self.funnel = OutcomeFunnel()

    def _attempt_cap_for(self, reason_code: str) -> int:
        """Search budget for one case's mutation retries (delegates to the
        pure module-level ``_attempt_cap`` so the logic stays testable)."""
        return _attempt_cap(self.max_mutation_attempts_per_case, reason_code)

    def _initialize_state(self) -> None:
        """Validate parameters and build all run state.

        Parameter validation, state initialization, HTC-TES setup,
        checkpoint resume, canary seeding and the initial progress persist.
        """
        validate_evolution_params(
            evolution_case_ids=self.evolution_case_ids,
            records=self.records,
            reflection_candidate_count=self.reflection_candidate_count,
            fitness_batch_size=self.fitness_batch_size,
            canary_limit=self.canary_limit,
            validation_probe_every=self.validation_probe_every,
            validation_probe_trigger_threshold=self.validation_probe_trigger_threshold,
            max_mutation_attempts_per_case=self.max_mutation_attempts_per_case,
            probe_limit=self.probe_limit,
            probe_experience_record=self.probe_experience_record,
            probe_ids=self.probe_ids,
            canary_case_ids=self.canary_case_ids,
            use_gold=self.use_gold,
            fitness_use_gold=self.fitness_use_gold,
        )
        self.registered_probe_ids = tuple(self.probe_ids[:self.probe_limit])

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.start_time = time.time()

        self.champion_genome = (
            self.initial_genome.clone() if self.initial_genome is not None
            else create_baseline_genome()
        )
        self.current_genome = self.champion_genome
        self.gate = create_structural_gate(
            module_cooldown=self.module_cooldown,
            max_total_edits=self.max_total_edits,
            convergence_window=self.convergence_window,
        )
        self.rejected_buffer: list[RejectedEditEntry] = []
        self.accepted_edits_history: list[dict[str, Any]] = []
        self.steps: list[EvolutionStep] = []
        self.provisional_edits: list[ProvisionalEdit] = []
        self.batch_history: list[dict[str, Any]] = []
        self.probe_history: list[dict[str, Any]] = []
        self.budget_usage = {
            "source_replays": 0,
            "canary_cases": 0,
            "contrast_replays": 0,
            "policy_replays": 0,
            "probe_cases": 0,
            "provider_calls": 0,
            "detector_calls": 0,
            "mutation_calls": 0,
            "mutation_errors": 0,
            "mutation_cache_hits": 0,
            "mutation_transport_attempts": 0,
            "mutation_repairs": 0,
            "mutation_retry_attempts": 0,
            "conflict_gate_calls": 0,
            "conflict_gate_transport_attempts": 0,
            "conflict_gate_repairs": 0,
            "contrast_replay_errors": 0,
            "policy_replay_errors": 0,
        }
        self.provider_usage_ledger = ProviderUsageLedger()
        self.mutation_retry_feedback: dict[str, list[str]] = {}
        self.localization_bias_signals: list[dict[str, str]] = []
        self.non_l1_failure_attributions: list[dict[str, Any]] = []
        self.policy_genome = (
            self.initial_policy_genome.clone()
            if self.initial_policy_genome is not None
            else create_baseline_policy_genome()
        )
        self.l2_registry_registrations: list[dict[str, Any]] = []
        self.l2_policy_audit: list[dict[str, Any]] = []
        self.rejected_policy_summaries: list[str] = []
        self.abstention_ladder_used: set[str] = set()
        self.abstention_ladder_advances: list[dict[str, str]] = []
        self.contrast_replay_registry: dict[str, list[dict[str, Any]]] = {}
        self.rejected_source_proposals: dict[str, list[dict[str, Any]]] = {}
        self.pending_probe_step_indexes: list[int] = []
        self.pending_probe_commits: list[PendingProbeCommit] = []
        self.active_attempt_index = 0
        self.state = RunState(
            champion_genome=self.champion_genome,
            current_genome=self.champion_genome,
            stable_genome=self.champion_genome.clone(),
            policy_genome=self.policy_genome,
            stable_policy_genome=self.policy_genome.clone(),
            l2_policy_accepts=0,
            stable_l2_policy_accepts=0,
            strategy_state=create_baseline_strategy(),
            edits_accepted=0,
            edits_rejected=0,
            rollback_reason="",
            last_probe_accepted_count=0,
            last_probe_triggered_count=0,
            stable_history_length=0,
            stable_memory_payload={},
            stable_canaries_checkpoint={},
            active_episode_payload=None,
            triggered_cases=0,
            stable_cases=0,
        )

        # HTC-TES: the type-conditioned components. The combined causal
        # memory composes the type statistics with the structural
        # failure-mechanism index; both persist through the same artifact.
        self.type_classifier = HallucinationTypeClassifier()
        self.type_evolution = _TypeEvolutionAdapter()
        self.type_fitness = TypeAwareFitness()
        self.causal_memory = ExperienceMemory()
        self.gate_event_ledger = GateEventLedger()
        if self.experience_memory_path is not None:
            memory_path = Path(self.experience_memory_path)
            if not memory_path.is_file():
                raise FileNotFoundError(
                    f"Experience memory not found: {memory_path}"
                )
            with memory_path.open(encoding="utf-8") as handle:
                memory_payload = json.load(handle)
            self.causal_memory.from_dict(memory_payload)
            persisted_meta = self.causal_memory.meta_review.get("meta_state") or {}
            if persisted_meta:
                try:
                    self.state.strategy_state = StrategyState.from_dict(
                        persisted_meta
                    )
                except ValueError:
                    self.state.strategy_state = create_baseline_strategy()
        self.state.stable_memory_payload = self.causal_memory.to_dict()
        self.state.active_episode_payload = None

        self.stable_canaries: dict[str, dict[str, Any]] = {}
        self.state.stable_canaries_checkpoint = {}
        self.baseline_canary_audit: list[dict[str, Any]] = []
        self.runtime_invalid_cases = 0
        self.runtime_error_categories: dict[str, int] = {}

        # ---- optional resume from a persisted checkpoint ----
        self.resume_next_case_index = 0
        if self.resume_from is not None:
            resumed = _restore_checkpoint_state(
                resume_from=self.resume_from,
                evolution_case_ids=self.evolution_case_ids,
                records=self.records, config=self.config,
                gate=self.gate, budget_usage=self.budget_usage,
                stable_canaries=self.stable_canaries,
                stable_canaries_checkpoint=self.state.stable_canaries_checkpoint,
                rejected_buffer=self.rejected_buffer,
                champion_genome=self.state.champion_genome,
                current_genome=self.state.current_genome,
                stable_genome=self.state.stable_genome,
                policy_genome=self.state.policy_genome,
                stable_policy_genome=self.state.stable_policy_genome,
                causal_memory=self.causal_memory,
            )
            self.steps = resumed["steps"]
            self.accepted_edits_history = resumed["accepted_edits_history"]
            self.batch_history = resumed["batch_history"]
            self.probe_history = resumed["probe_history"]
            self.baseline_canary_audit = resumed["baseline_canary_audit"]
            self.state.rollback_reason = resumed["rollback_reason"]
            self.pending_probe_step_indexes = resumed["pending_probe_step_indexes"]
            self.pending_probe_commits = resumed["pending_probe_commits"]
            self.state.stable_cases = resumed["stable_cases"]
            self.state.triggered_cases = resumed["triggered_cases"]
            self.state.edits_accepted = resumed["edits_accepted"]
            self.state.edits_rejected = resumed["edits_rejected"]
            self.state.stable_memory_payload = resumed["stable_memory_payload"]
            self.state.stable_history_length = resumed["stable_history_length"]
            self.state.last_probe_accepted_count = resumed["last_probe_accepted_count"]
            self.state.last_probe_triggered_count = resumed["last_probe_triggered_count"]
            self.active_attempt_index = resumed["active_attempt_index"]
            self.mutation_retry_feedback = resumed["mutation_retry_feedback"]
            self.rejected_source_proposals = resumed["rejected_source_proposals"]
            self.runtime_invalid_cases = resumed["runtime_invalid_cases"]
            self.runtime_error_categories = resumed["runtime_error_categories"]
            self.state.policy_genome = resumed["policy_genome"]
            self.state.l2_policy_accepts = resumed["l2_policy_accepts"]
            self.gate_event_ledger = resumed["gate_event_ledger"]
            self.state.stable_policy_genome = resumed["stable_policy_genome"]
            self.state.stable_l2_policy_accepts = resumed["stable_l2_policy_accepts"]
            self.abstention_ladder_used = resumed["abstention_ladder_used"]
            self.contrast_replay_registry = resumed["contrast_replay_registry"]
            self.rejected_policy_summaries = resumed["rejected_policy_summaries"]
            self.localization_bias_signals = resumed["localization_bias_signals"]
            self.non_l1_failure_attributions = resumed["non_l1_failure_attributions"]
            self.l2_policy_audit = resumed["l2_policy_audit"]
            self.resume_next_case_index = resumed["resume_next_case_index"]
            persisted_meta = self.causal_memory.meta_review.get("meta_state") or {}
            if persisted_meta:
                try:
                    self.state.strategy_state = StrategyState.from_dict(
                        persisted_meta
                    )
                except ValueError:
                    self.state.strategy_state = create_baseline_strategy()
            if self.run_id is None:
                self.run_id = resumed["run_id"] or None
            print(
                f"[resume] restored checkpoint from {self.resume_from}: "
                f"next_case_index={self.resume_next_case_index}, "
                f"steps={len(self.steps)}, "
                f"edits_accepted={self.state.edits_accepted}, "
                f"edits_rejected={self.state.edits_rejected}"
            )

        # The funnel is a pure projection of the terminal steps (C8): after
        # a checkpoint restore it must be rebuilt from the restored steps so
        # the resumed run's funnel totals match a continuous run's. Interim
        # markers (provisional / retry_* / halted) are not §2.9 terminals
        # and never enter the funnel.
        for step in self.steps:
            try:
                outcome = Outcome(str(step.gate_decision or ""))
            except ValueError:
                continue
            if outcome in Outcome:
                self.funnel.record(outcome)

        if self.run_id is None:
            self.run_id = f"bccse_v5_{uuid.uuid4().hex[:8]}"

        print(
            f"[{self.run_id}] Starting online evolution: "
            f"{len(self.evolution_case_ids)} cases"
        )
        print(
            f"  Config: use_gold={self.use_gold}, "
            f"cooldown={self.module_cooldown}, "
            f"max_edits={self.max_total_edits}, "
            f"convergence_window={self.convergence_window}"
        )
        print("  HTC-TES v3: type-conditioned evolution enabled")

        # Seed independent label-free anchors before processing the
        # evolution sample. Without this explicit pool, an all-triggered
        # sample has no stable case available for the non-regression gate.
        self.registered_canary_ids = tuple(
            self.canary_case_ids[:self.canary_limit]
        )
        for canary_id in self.registered_canary_ids:
            audit: dict[str, Any] = {
                "case_id": canary_id, "admitted": False,
            }
            try:
                canary_result = self.run_detection(
                    budget_kind="canary",
                    genome=self.state.champion_genome,
                    case_id=canary_id,
                    policy_genome=(
                        self.state.policy_genome
                        if self.state.policy_genome.items_for_scope("working")
                        else None
                    ),
                )
                canary_signal = attribute_failure_for_result(
                    canary_result, canary_id, records=self.records,
                    use_gold=self.use_gold, gold_reflection=self.gold_reflection,
                )
                audit.update({
                    "state": resolve_evolution_state(canary_result),
                    "should_evolve": _triggered(
                        canary_result, use_gold=self.use_gold,
                        gold_label=(
                            _evaluator_gold(records[canary_id])
                            if self.use_gold else None
                        ),
                    ),
                    "attribution": canary_signal.to_dict(),
                })
                if not _triggered(
                    canary_result, use_gold=self.use_gold,
                    gold_label=(
                        _evaluator_gold(records[canary_id])
                        if self.use_gold else None
                    ),
                ):
                    self.stable_canaries[canary_id] = canary_result
                    audit["admitted"] = True
            except Exception as exc:
                audit.update({
                    "state": "runtime_invalid",
                    "error": f"{type(exc).__name__}:{exc}",
                })
            self.baseline_canary_audit.append(audit)
        if self.resume_from is None or not self.state.stable_canaries_checkpoint:
            self.state.stable_canaries_checkpoint = dict(self.stable_canaries)
        if (
            self.registered_canary_ids
            and not self.stable_canaries
            and not self.fitness_use_gold
        ):
            raise RuntimeError(
                "No registered canary remained stable under the initial "
                "genome; label-free evolution cannot distinguish directional "
                "evidence from self-confirmation"
            )

        self.persist_progress(self.resume_next_case_index)

    def run_detection(self, *,
        budget_kind: str, genome: SkillGenome, case_id: str,
        policy_genome: PolicyGenome | None = None,
        policy_scope: str | None = "working",
    ) -> dict[str, Any]:
        """Run one detector call in a counted, label-free envelope."""
        return _run_counted_detection(
            budget_kind=budget_kind, genome=genome, case_id=case_id,
            budget_usage=self.budget_usage,
            records=self.records, config=self.config,
            provider_usage_ledger=self.provider_usage_ledger,
            policy_genome=policy_genome, policy_scope=policy_scope,
        )

    def run_mutation_call(self, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        """Count attribution/reflection calls separately from detector calls."""
        return _run_mutation_call(
            budget_usage=self.budget_usage,
            provider_usage_ledger=self.provider_usage_ledger, **kwargs,
        )

    def persist_progress(self, next_case_index: int, status: str = "in_progress") -> None:
        _persist_progress(_PersistenceContext(
            output_dir=self.output_dir, run_id=self.run_id,
            evolution_case_ids=self.evolution_case_ids, records=self.records,
            config=self.config, gate=self.gate,
            champion_genome=self.state.champion_genome,
            current_genome=self.state.current_genome,
            stable_genome=self.state.stable_genome,
            policy_genome=self.state.policy_genome,
            stable_policy_genome=self.state.stable_policy_genome,
            causal_memory=self.causal_memory,
            gate_event_ledger=self.gate_event_ledger,
            steps=self.steps, provisional_edits=self.provisional_edits,
            accepted_edits_history=self.accepted_edits_history,
            batch_history=self.batch_history, probe_history=self.probe_history,
            baseline_canary_audit=self.baseline_canary_audit,
            registered_canary_ids=self.registered_canary_ids,
            budget_usage=self.budget_usage,
            rollback_reason=self.state.rollback_reason,
            stable_canaries=self.stable_canaries,
            stable_canaries_checkpoint=self.state.stable_canaries_checkpoint,
            stable_memory_payload=self.state.stable_memory_payload,
            stable_history_length=self.state.stable_history_length,
            pending_probe_step_indexes=self.pending_probe_step_indexes,
            pending_probe_commits=self.pending_probe_commits,
            active_attempt_index=self.active_attempt_index,
            mutation_retry_feedback=self.mutation_retry_feedback,
            rejected_source_proposals=self.rejected_source_proposals,
            rejected_buffer=self.rejected_buffer,
            runtime_invalid_cases=self.runtime_invalid_cases,
            runtime_error_categories=self.runtime_error_categories,
            last_probe_accepted_count=self.state.last_probe_accepted_count,
            last_probe_triggered_count=self.state.last_probe_triggered_count,
            stable_cases=self.state.stable_cases,
            triggered_cases=self.state.triggered_cases,
            edits_accepted=self.state.edits_accepted,
            edits_rejected=self.state.edits_rejected,
            l2_policy_accepts=self.state.l2_policy_accepts,
            l2_policy_audit=self.l2_policy_audit,
            stable_l2_policy_accepts=self.state.stable_l2_policy_accepts,
            abstention_ladder_used=self.abstention_ladder_used,
            contrast_replay_registry=self.contrast_replay_registry,
            rejected_policy_summaries=self.rejected_policy_summaries,
            localization_bias_signals=self.localization_bias_signals,
            non_l1_failure_attributions=self.non_l1_failure_attributions,
        ), next_case_index, status)

    def record_step(self, step: EvolutionStep, *, next_case_index: int) -> None:
        step.attempt_index = self.active_attempt_index
        step.episode = self.state.active_episode_payload
        self.steps.append(step)
        _record_gate_event(
            step, gate_event_ledger=self.gate_event_ledger, steps=self.steps,
        )
        self.persist_progress(next_case_index)

    def record_attempt(self, *,
        decision: str, operation: str = "", target_module: str = "",
        action_spec: Mapping[str, Any] | None = None,
        structural_result: str = "accepted", compiled: bool = False,
        injected: bool = False, applied: bool = False,
        observable_delta: Sequence[str] = (),
        downstream_court_delta: Sequence[str] = (),
        no_effect_reason: str = "", proposal_content: str = "",
    ) -> None:
        """Append one execution outcome to the current episode."""
        self.state.active_episode_payload = _record_attempt(
            active_episode_payload=self.state.active_episode_payload,
            causal_memory=self.causal_memory, decision=decision,
            operation=operation, target_module=target_module,
            action_spec=action_spec, structural_result=structural_result,
            compiled=compiled, injected=injected, applied=applied,
            observable_delta=observable_delta,
            downstream_court_delta=downstream_court_delta,
            no_effect_reason=no_effect_reason,
            proposal_content=proposal_content,
        )

    def _run_registry_reflection(self, *,
        attribution: Any, facts_summary: Mapping[str, Any], case_id: str,
    ) -> None:
        _run_registry_reflection_impl(
            attribution=attribution, facts_summary=facts_summary,
            case_id=case_id, policy_genome=self.state.policy_genome,
            mutation_route=self.mutation_route,
            run_mutation_call=self.run_mutation_call,
            output_dir=self.output_dir,
            l2_registry_registrations=self.l2_registry_registrations,
        )

    def run_development_probe(self, *, reason: str) -> tuple[bool, dict[str, Any]]:
        """Apply the independent development envelope as a veto-only gate."""
        return _run_development_probe_impl(
            reason=reason, state=self.state, budget_usage=self.budget_usage,
            gate=self.gate, causal_memory=self.causal_memory,
            records=self.records,
            probe_history=self.probe_history,
            registered_probe_ids=self.registered_probe_ids,
            run_detection=self.run_detection, steps=self.steps,
            accepted_edits_history=self.accepted_edits_history,
            pending_probe_commits=self.pending_probe_commits,
            pending_probe_step_indexes=self.pending_probe_step_indexes,
            stable_canaries=self.stable_canaries,
            probe_experience_record=self.probe_experience_record,
            type_fitness=self.type_fitness,
        )

    def flush_shadow_batch(self, *, next_case_index: int, force: bool = False) -> bool:
        """Commit or roll back the current reversible shadow batch."""
        return _flush_shadow_batch_impl(
            next_case_index=next_case_index, force=force, state=self.state,
            provisional_edits=self.provisional_edits,
            fitness_batch_size=self.fitness_batch_size,
            batch_history=self.batch_history,
            fitness_use_gold=self.fitness_use_gold,
            steps=self.steps, gate=self.gate, budget_usage=self.budget_usage,
            accepted_edits_history=self.accepted_edits_history,
            pending_probe_commits=self.pending_probe_commits,
            pending_probe_step_indexes=self.pending_probe_step_indexes,
            stable_canaries=self.stable_canaries, canary_limit=self.canary_limit,
            rejected_buffer=self.rejected_buffer, output_dir=self.output_dir,
            type_classifier=self.type_classifier,
            type_evolution=self.type_evolution,
            type_fitness=self.type_fitness,
            registered_probe_ids=self.registered_probe_ids,
            validation_probe_every=self.validation_probe_every,
            validation_probe_trigger_threshold=self.validation_probe_trigger_threshold,
            run_development_probe=self.run_development_probe,
            record_attempt=self.record_attempt,
            persist_progress=self.persist_progress,
        )

    def run(self) -> None:
        """Drain the source work queue over the single RepairPlan pipeline.

        Each case runs detection, then routes through the repairability
        authority: NON_EVOLVABLE cases are stable detections, an
        UNADDRESSABLE_FRAMEWORK_DEFECT is counted on its own funnel line
        (C2), a FRAMEWORK_DEFECT is an infra outcome, and a REPAIRABLE
        episode dispatches to the L1 or L2 executor on its RepairPlan.
        Every case deposits exactly one outcome into the funnel; the
        convergence window only consumes fitness-layer rejections of
        complete repair attempts (C9).
        """
        # The checkpoint's active_attempt_index belongs to the last
        # processed iteration, which is only meaningful for a retry
        # continuation. A fresh case after resume must start at attempt 0:
        # carrying the stale index would silently consume an attempt and
        # inflate mutation_retry_attempts.
        tail_is_retry_for_next = (
            self.steps
            and self.resume_next_case_index == self.steps[-1].case_index
            and self.steps[-1].gate_decision.startswith("retry_")
        )
        resume_attempt_index = (
            self.active_attempt_index if tail_is_retry_for_next else 0
        )
        self.source_work_queue = deque(
            (
                index, case_id,
                resume_attempt_index
                if index == self.resume_next_case_index else 0,
            )
            for index, case_id in enumerate(self.evolution_case_ids)
            if index >= self.resume_next_case_index
        )
        if self.resume_next_case_index > 0:
            print(
                f"[resume] skipping first {self.resume_next_case_index} "
                f"case(s); {len(self.source_work_queue)} case(s) remaining "
                f"in work queue"
            )
        while self.source_work_queue:
            idx, case_id, self.active_attempt_index = self.source_work_queue.popleft()
            prior_episode_payload = None
            if self.active_attempt_index > 0:
                for prior_step in reversed(self.steps):
                    if (
                        prior_step.case_id == case_id
                        and isinstance(prior_step.episode, Mapping)
                        and "episode_id" in prior_step.episode
                    ):
                        prior_episode_payload = dict(prior_step.episode)
                        break
            self.state.active_episode_payload = prior_episode_payload
            if self.active_attempt_index > 0:
                self.budget_usage["mutation_retry_attempts"] += 1
            if self.gate.is_halted or self.gate.converged:
                self.record_step(EvolutionStep(
                    case_id=case_id,
                    case_index=idx,
                    trigger_source="halted",
                    attribution=None,
                    edit_proposal=None,
                    mutation_applied=None,
                    gate_decision="halted",
                    genome_hash_before=self.state.current_genome.content_hash,
                    genome_hash_after=self.state.current_genome.content_hash,
                    detection_state="skipped",
                    timestamp=time.time(),
                ), next_case_index=idx + 1)
                continue

            genome_before = self.state.current_genome.content_hash

            # --- [1] Detection with the current working genome ---
            try:
                detection_result = self.run_detection(
                    budget_kind="case",
                    genome=self.state.current_genome,
                    case_id=case_id,
                    policy_genome=(
                        self.state.policy_genome
                        if self.state.policy_genome.items_for_scope("working")
                        else None
                    ),
                )
            except Exception as exc:
                self._record_runtime_invalid(
                    idx, case_id, genome_before, exc, "detection_runtime",
                )
                continue
            detection_state = resolve_evolution_state(detection_result)

            # --- [2] Attribution signal (gold join under the explicit
            # use_gold opt-in) ---
            signal = attribute_failure_for_result(
                detection_result, case_id, records=self.records,
                use_gold=self.use_gold, gold_reflection=self.gold_reflection,
            )
            if signal.diagnostic_only:
                # A diagnostic-only signal (runtime invalid inside the
                # projection) is recorded as infra telemetry, never
                # evolution evidence.
                self._record_runtime_invalid_signal(
                    idx, case_id, genome_before, detection_result, signal,
                )
                continue

            # --- [3] Deterministic causal cut + episode + repairability
            # routing (C6 single authority) ---
            cut = build_causal_cut(signal)
            type_result = self.type_classifier.classify(
                detection_result, signal,
            )
            evolution_direction = _infer_evolution_direction(
                signal.state, signal.gold_label,
            )
            # The cut is the L2 routing authority: when it lands on the
            # adjudication surface, the classifier row is recorded so the L2
            # path can open independently of the step trigger (a stable
            # source whose L2 attribution opened independently must still be
            # able to repair its adjudication surface).
            l2_attribution: dict[str, Any] | None = None
            if cut.skill_layer == LAYER_L2:
                l2_attribution = {
                    "case_id": case_id,
                    "layer": LAYER_L2,
                    "artifact": cut.owner,
                    "reason_code": cut.reason_code,
                    "evidence": dict(cut.evidence or {}),
                }
                if not any(
                    str(entry.get("case_id")) == case_id
                    and entry.get("layer") == LAYER_L2
                    for entry in self.non_l1_failure_attributions
                ):
                    self.non_l1_failure_attributions.append(dict(l2_attribution))
            episode_payload: dict[str, Any] | None = None
            if cut.actionable:
                try:
                    episode = build_causal_episode(
                        signal=signal,
                        cut=cut,
                        record_key=case_id,
                        evolution_direction=evolution_direction,
                        allowed_modules=_allowed_l1_modules(
                            signal,
                            cut.owner,
                            (
                                cut.evidence.get("artifact_frontier", ())
                                if isinstance(cut.evidence, Mapping) else ()
                            ),
                            cut.secondary_owner,
                        ),
                    )
                    episode_payload = episode.to_dict()
                    self.causal_memory.upsert_observation(episode)
                except Exception as exc:
                    episode_payload = {"unaddressable": str(exc)}
            self.state.active_episode_payload = episode_payload
            if episode_payload is not None and "episode_id" in episode_payload:
                episode = CausalFailureEpisode.from_dict(episode_payload)
                verdict = analyze_repairability(
                    episode,
                    allowed_modules=(
                        _allowed_l1_modules(
                            signal, cut.owner,
                            (
                                cut.evidence.get("artifact_frontier", ())
                                if isinstance(cut.evidence, Mapping) else ()
                            ),
                            cut.secondary_owner,
                        )
                        if cut.skill_layer == LAYER_L1 else ()
                    ),
                    evolution_direction=evolution_direction,
                )
            else:
                verdict = None
                episode = None

            # --- [3a] Repairability terminal routing (C2/C8/C9) ---
            # The repairability verdict is the only trigger authority:
            # ``should_evolve`` was absorbed into ``analyze`` (Phase 4), so
            # a case is evolvable iff its episode is REPAIRABLE.
            if verdict is None or episode is None:
                if cut.actionable:
                    # The cut declared an evolvable surface but no episode
                    # compiled (unaddressable payload): a framework-invariant
                    # situation, counted on its own C2 line — never a
                    # stable/no-edit case.
                    if self.active_attempt_index == 0:
                        self.state.triggered_cases += 1
                    self.funnel.record(Outcome.UNADDRESSABLE)
                    self.record_step(EvolutionStep(
                        case_id=case_id,
                        case_index=idx,
                        trigger_source="unaddressable",
                        attribution=signal.to_dict(),
                        edit_proposal={
                            "operation": "no_edit",
                            "reason": "no_addressable_failure_episode",
                            "causal_cut": cut.to_dict(),
                        },
                        mutation_applied=None,
                        gate_decision=Outcome.UNADDRESSABLE.value,
                        genome_hash_before=genome_before,
                        genome_hash_after=self.state.current_genome.content_hash,
                        detection_state=detection_state,
                        timestamp=time.time(),
                        fitness_check={"causal_cut": cut.to_dict()},
                    ), next_case_index=idx + 1)
                    print(
                        f"  [{idx}] UNADDRESSABLE {case_id}: "
                        f"{cut.skill_layer}/{cut.reason_code}"
                    )
                    continue
                # The cut did not land on an evolvable surface: a stable
                # detection.
                self._record_stable_case(
                    idx, case_id, detection_state, genome_before, signal,
                    type_result, cut, detection_result,
                )
                continue
            status = verdict.status
            if status is RepairabilityStatus.NON_EVOLVABLE:
                self._record_stable_case(
                    idx, case_id, detection_state, genome_before, signal,
                    type_result, cut, detection_result,
                )
                continue
            if status is RepairabilityStatus.FRAMEWORK_DEFECT:
                # §2.5 detector-boundary defect: always a framework defect,
                # never a Skill. Infra telemetry, not convergence evidence.
                self.funnel.record(Outcome.INFRA_FAILURE)
                self.record_step(EvolutionStep(
                    case_id=case_id,
                    case_index=idx,
                    trigger_source="framework_defect",
                    attribution=signal.to_dict(),
                    edit_proposal=None,
                    mutation_applied=None,
                    gate_decision=Outcome.INFRA_FAILURE.value,
                    genome_hash_before=genome_before,
                    genome_hash_after=self.state.current_genome.content_hash,
                    detection_state=detection_state,
                    timestamp=time.time(),
                    fitness_check={
                        "repairability": verdict.to_dict(),
                        "diagnostic_only": True,
                        "claim_scope": "diagnostic_only_not_evolution_evidence",
                    },
                ), next_case_index=idx + 1)
                print(
                    f"  [{idx}] FRAMEWORK_DEFECT {case_id}: {verdict.reason}"
                )
                continue
            if status is RepairabilityStatus.UNADDRESSABLE_FRAMEWORK_DEFECT:
                # C2: a framework-invariant violation is counted on its own
                # funnel line and never enters the no-edit / convergence
                # statistics.
                if self.active_attempt_index == 0:
                    self.state.triggered_cases += 1
                self.funnel.record(Outcome.UNADDRESSABLE)
                self.record_step(EvolutionStep(
                    case_id=case_id,
                    case_index=idx,
                    trigger_source="unaddressable",
                    attribution=signal.to_dict(),
                    edit_proposal={
                        "operation": "no_edit",
                        "reason": verdict.reason,
                    },
                    mutation_applied=None,
                    gate_decision=Outcome.UNADDRESSABLE.value,
                    genome_hash_before=genome_before,
                    genome_hash_after=self.state.current_genome.content_hash,
                    detection_state=detection_state,
                    timestamp=time.time(),
                    fitness_check={
                        "repairability": verdict.to_dict(),
                    },
                ), next_case_index=idx + 1)
                print(
                    f"  [{idx}] UNADDRESSABLE_FRAMEWORK_DEFECT {case_id}: "
                    f"{verdict.reason}"
                )
                continue

            # --- [4] REPAIRABLE: dispatch by skill layer (C6) ---
            if self.active_attempt_index == 0:
                self.state.triggered_cases += 1
            print(
                f"  [{idx}] TRIGGERED {case_id}: "
                f"{cut.reason_code} -> {cut.skill_layer}/{cut.owner} "
                f"(attempt {self.active_attempt_index + 1}/"
                f"{self._attempt_cap_for(cut.reason_code)})"
            )
            if episode.skill_layer == LAYER_L2:
                if l2_attribution is None:
                    # L2 episode without a classifier attribution row: the
                    # L2 surface owns the repair but the audit row is
                    # missing, so the case ends as unaddressable rather
                    # than fabricating an L1 owner.
                    self.funnel.record(Outcome.UNADDRESSABLE)
                    self.record_step(EvolutionStep(
                        case_id=case_id,
                        case_index=idx,
                        trigger_source="l2_policy_reflection",
                        attribution=signal.to_dict(),
                        edit_proposal=None,
                        mutation_applied=None,
                        gate_decision=Outcome.UNADDRESSABLE.value,
                        genome_hash_before=genome_before,
                        genome_hash_after=self.state.current_genome.content_hash,
                        detection_state=detection_state,
                        timestamp=time.time(),
                    ), next_case_index=idx + 1)
                    continue
                self._run_l2_policy_case(
                    idx=idx, case_id=case_id,
                    detection_result=detection_result,
                    detection_state=detection_state,
                    genome_before=genome_before,
                    l2_attribution=l2_attribution,
                    stable_source=False,
                )
                continue

            # L1 provider-rule surface.
            view = _ReflectionSignalView(signal, cut.owner, cut.reason_code)
            self._run_l1_policy_case(
                idx=idx, case_id=case_id, detection_result=detection_result,
                detection_state=detection_state, genome_before=genome_before,
                signal=signal, view=view, cut=cut, episode=episode,
                type_result=type_result, evolution_direction=evolution_direction,
            )

    def _record_runtime_invalid(
        self, idx: int, case_id: str, genome_before: str,
        exc: Exception, stage: str,
    ) -> None:
        """Record a detector crash as diagnostic-only infra telemetry.

        A crashed detector run cannot be attributed to a skill module (no
        verdict, no coverage, no CRS), so it never triggers evolution and
        never advances the convergence window (C9: infra failure is not a
        complete repair attempt).
        """
        runtime_error = f"{type(exc).__name__}: {exc}"
        invalid_signal = runtime_invalid_signal(error=runtime_error)
        failure_details = getattr(exc, "failure_details", None)
        atomic_write_json(
            self.output_dir / "runtime_failures" / f"{idx:04d}.json",
            {
                "schema_version": "agenthallu.bccse_v5_failure.v2",
                "run_id": self.run_id,
                "case_id": case_id,
                "case_index": idx,
                "genome_hash": self.state.current_genome.content_hash,
                "error": runtime_error,
                "error_category": invalid_signal.runtime_error_category,
                "failure_details": (
                    failure_details if isinstance(failure_details, Mapping)
                    else None
                ),
                "action": "case_skipped",
                "claim_scope": "diagnostic_only_not_evolution_evidence",
            },
        )
        self.runtime_invalid_cases += 1
        self.runtime_error_categories[invalid_signal.runtime_error_category] = (
            self.runtime_error_categories.get(
                invalid_signal.runtime_error_category, 0
            ) + 1
        )
        self.type_fitness.get_experience_memory().add_experience(
            hallucination_type=HallucinationType.UNKNOWN,
            evolution_direction="runtime_invalid",
            module_targeted="",
            strategy_used="runtime_invalid",
            detection_improved=False,
            source_case_id=case_id,
            metadata=_structured_outcome(
                outcome="runtime_invalid",
                stage=stage,
                operation="none",
                expected_effect_status="not_replayed",
                reason_categories=[invalid_signal.runtime_error_category],
                promotable=False,
                diagnostic_only=True,
            ),
            feature_description="",
            type_confidence=0.0,
        )
        self.funnel.record(Outcome.INFRA_FAILURE)
        self.record_step(EvolutionStep(
            case_id=case_id,
            case_index=idx,
            trigger_source="runtime_invalid",
            attribution=invalid_signal.to_dict(),
            edit_proposal=None,
            mutation_applied=None,
            gate_decision=Outcome.INFRA_FAILURE.value,
            genome_hash_before=genome_before,
            genome_hash_after=self.state.current_genome.content_hash,
            detection_state="runtime_invalid",
            timestamp=time.time(),
            fitness_check={
                "runtime_error": runtime_error,
                "runtime_error_category": (
                    invalid_signal.runtime_error_category
                ),
                "diagnostic_only": True,
                "claim_scope": "diagnostic_only_not_evolution_evidence",
                "passed": False,
            },
        ), next_case_index=idx + 1)
        print(f"  [{idx}] RUNTIME_INVALID {case_id}: {runtime_error}")

    def _record_runtime_invalid_signal(
        self, idx: int, case_id: str, genome_before: str,
        detection_result: Mapping[str, Any], signal: Any,
    ) -> None:
        runtime_error = str(signal.runtime_error_category or "diagnostic_only")
        self.runtime_invalid_cases += 1
        self.runtime_error_categories[runtime_error] = (
            self.runtime_error_categories.get(runtime_error, 0) + 1
        )
        self.funnel.record(Outcome.INFRA_FAILURE)
        self.record_step(EvolutionStep(
            case_id=case_id,
            case_index=idx,
            trigger_source="runtime_invalid",
            attribution=signal.to_dict(),
            edit_proposal=None,
            mutation_applied=None,
            gate_decision=Outcome.INFRA_FAILURE.value,
            genome_hash_before=genome_before,
            genome_hash_after=self.state.current_genome.content_hash,
            detection_state="runtime_invalid",
            timestamp=time.time(),
            fitness_check={
                "runtime_error": runtime_error,
                "diagnostic_only": True,
                "claim_scope": "diagnostic_only_not_evolution_evidence",
                "passed": False,
            },
        ), next_case_index=idx + 1)
        print(f"  [{idx}] RUNTIME_INVALID {case_id}: {runtime_error}")

    def _record_stable_case(
        self, idx: int, case_id: str, detection_state: str,
        genome_before: str, signal: Any, type_result: Any, cut: Any,
        detection_result: Mapping[str, Any],
    ) -> None:
        """Record a stable (non-triggered) detection as NON_EVOLVABLE."""
        if self.active_attempt_index == 0:
            self.state.stable_cases += 1
        self.stable_canaries[case_id] = detection_result
        evict_stable_canaries(self.stable_canaries, self.canary_limit)
        if not self.pending_probe_commits:
            self.state.stable_canaries_checkpoint = dict(self.stable_canaries)
        self.contrast_replay_registry.setdefault(
            type_result.primary_type.value, []
        ).append({
            "case_id": case_id,
            "state": detection_state,
            "stable": True,
            "owner": cut.owner,
        })
        _trim_contrast_registry(self.contrast_replay_registry)
        self.funnel.record(Outcome.NON_EVOLVABLE)
        self.record_step(EvolutionStep(
            case_id=case_id,
            case_index=idx,
            trigger_source="none",
            attribution=signal.to_dict(),
            edit_proposal=None,
            mutation_applied=None,
            gate_decision=Outcome.NON_EVOLVABLE.value,
            genome_hash_before=genome_before,
            genome_hash_after=self.state.current_genome.content_hash,
            detection_state=detection_state,
            timestamp=time.time(),
        ), next_case_index=idx + 1)
        print(f"  [{idx}] STABLE {case_id}: state={detection_state}")

    def _last_audited_step(self, case_id: str) -> int | None:
        """The trajectory's final step in the detector's step coordinate."""
        from sehd_rise.loop.persistence import _last_audited_step
        return _last_audited_step(self.records[case_id])

    def _run_l2_policy_case(
        self, *,
        idx: int, case_id: str,
        detection_result: Mapping[str, Any],
        detection_state: str,
        genome_before: str,
        l2_attribution: Mapping[str, Any],
        stable_source: bool = False,
    ) -> bool:
        """Close the L2 proposal loop for one adjudication-layer failure.

        One reflection call proposes a single closed-vocabulary policy edit
        (add_item / modify_item / remove_item); the candidate genome must
        then show an observable effect in a source replay and, whenever the
        terminal state moves, move it toward the gold polarity under the
        same discipline as L1 edits. Rejected candidates are only ever
        replayed as copies, so the working genome rolls back inherently.

        Returns True when the policy edit was accepted. Rejected and
        unavailable L2 repairs remain L2 outcomes: they do not authorize a
        synthetic provider-rule episode because no L1 artifact defect has
        been established.
        """
        accepted_outcome = False
        policy_canary_audit: list[dict[str, Any]] = []
        policy_canary_regressions: list[str] = []
        policy_canary_results: dict[str, dict[str, Any]] = {}
        promotion_canaries = (
            {
                canary_id: self.stable_canaries[canary_id]
                for canary_id in self.registered_canary_ids
                if canary_id in self.stable_canaries
            }
            if self.registered_canary_ids else self.stable_canaries
        )

        attribution = _L2Attribution(
            layer=str(l2_attribution.get("layer") or LAYER_L2),
            artifact=str(l2_attribution.get("artifact") or ""),
            reason_code=str(l2_attribution.get("reason_code") or ""),
            evidence=dict(l2_attribution.get("evidence") or {}),
        )
        adjudication = detection_result.get("proof_adjudication") or {}
        if not isinstance(adjudication, Mapping):
            adjudication = {}
        boundary_path = detection_result.get("boundary_path")
        if not isinstance(boundary_path, Mapping):
            boundary_path = {}
        boundary_path_state = str(boundary_path.get("state") or "")
        boundary_validation = boundary_path.get("contract_validation")
        boundary_authority_valid = (
            isinstance(boundary_validation, Mapping)
            and boundary_validation.get("decision") == "pass"
        )
        facts = build_adjudication_facts(
            dict(adjudication),
            boundary_path_state=boundary_path_state,
            boundary_authority_valid=boundary_authority_valid,
        )
        facts_summary = asdict(facts)
        plan = plan_policy_candidates(
            attribution=attribution,
            genome=self.state.policy_genome,
        )
        # P3B: pending registry entries accumulate independent observations
        # on every no-registered-action L2 case; an entry that clears the
        # evidence threshold and stays applicable is promoted to working
        # (enabled) here, before the promoted-intent lookup reads the queue.
        if (
            plan.route_result == ROUTE_NO_REGISTERED_RUNTIME_ACTION
            and any(
                entry.required_validation == REGISTRY_REQUIRED_VALIDATION
                and entry.status == PENDING_STATUS_PENDING
                for entry in self.state.policy_genome.pending_queue
            )
        ):
            for promotion_row in promote_pending_registry_entries(
                genome=self.state.policy_genome,
                facts=facts,
                case_id=case_id,
                reason_code=plan.route_result,
            ):
                if promotion_row["outcome"] in ("promoted", "rejected"):
                    self.l2_registry_registrations.append(promotion_row)
        proposal_edit: Any = None
        candidate_genome: PolicyGenome | None = None
        route_result = ""
        feedback_code = ""
        policy_exc: Exception | None = None

        def finish(
            decision: str,
            *,
            reason: str = "",
            replay: Mapping[str, Any] | None = None,
            route: str = "",
            feedback: str = "",
            replay_error_detail: str = "",
            replay_error_traceback: str = "",
        ) -> None:
            nonlocal accepted_outcome
            nonlocal route_result, feedback_code, policy_exc
            accepted = decision == "accept_policy"
            route_result = route or (
                ROUTE_EXECUTED if proposal_edit is not None else route_result
            )
            feedback_code = feedback or feedback_code
            accepted_outcome = accepted
            outcome = _l2_outcome_for(
                decision, reason=reason, route=route,
            )
            replay_error = decision == "reject_policy_replay_error"
            if accepted:
                self.state.l2_policy_accepts += 1
                self.gate.reset_convergence()
            else:
                # C9: only complete repair attempts (executed + measured +
                # no acceptable improvement) advance the convergence streak.
                # A fruitless policy reflection with no executed plan and a
                # replay that raised before any verdict are not convergence
                # evidence. A stable-source L2 attribution must not push
                # the streak either: the case was not a trigger.
                if (
                    not stable_source
                    and not replay_error
                    and outcome
                    in (Outcome.SOURCE_NOT_IMPROVED, Outcome.CANARY_REGRESSION)
                ):
                    self.gate.record_no_edit()
                if replay_error:
                    self.budget_usage["policy_replay_errors"] += 1
                if not replay_error:
                    self.rejected_policy_summaries.append(
                        _policy_rejection_signature(
                            decision, reason, proposal_edit,
                        )
                    )
                    del self.rejected_policy_summaries[:-20]
            audit_entry: dict[str, Any] = {
                "case_id": case_id,
                "decision": decision,
                "outcome": outcome.value,
                "reason": reason,
                "route_result": route_result,
                "feedback_code": feedback_code,
                "attribution": attribution.to_dict(),
                "facts": facts_summary,
                "proposal": (
                    proposal_edit.to_dict() if proposal_edit else None
                ),
                "policy_genome_hash": self.state.policy_genome.content_hash,
                "candidate_policy_genome_hash": (
                    candidate_genome.content_hash
                    if candidate_genome is not None else ""
                ),
                "configured_canary_count": len(self.registered_canary_ids),
                "stable_registered_canary_count": len(promotion_canaries),
                "canary_cases": len(policy_canary_audit),
                "canary_audit": list(policy_canary_audit),
                "canary_regressions": list(policy_canary_regressions),
            }
            if replay_error_detail:
                audit_entry["replay_error_detail"] = replay_error_detail
            if replay_error_traceback:
                audit_entry["replay_error_traceback"] = replay_error_traceback
            if isinstance(replay, Mapping):
                replay_adjudication = replay.get("proof_adjudication") or {}
                if not isinstance(replay_adjudication, Mapping):
                    replay_adjudication = {}
                audit_entry["policy_evaluation"] = dict(
                    replay_adjudication.get("policy_evaluation") or {}
                )
                audit_entry["replay_state"] = resolve_evolution_state(replay)
            self.l2_policy_audit.append(audit_entry)
            if replay_error_detail:
                atomic_write_json(
                    self.output_dir / "runtime_failures"
                    / f"{idx:04d}_l2_policy_replay.json",
                    {
                        "schema_version": "agenthallu.bccse_l2_replay_failure.v1",
                        "case_id": case_id,
                        "case_index": idx,
                        "genome_hash": self.state.current_genome.content_hash,
                        "policy_edit_id": (
                            str(proposal_edit.edit_id)
                            if proposal_edit else ""
                        ),
                        "policy_action": (
                            str(proposal_edit.new_item.action)
                            if proposal_edit and proposal_edit.new_item else ""
                        ),
                        "error": replay_error_detail,
                        "error_category": classify_runtime_error(
                            replay_error_detail
                        ),
                        "traceback": replay_error_traceback,
                        "decision": decision,
                        "reason": reason,
                        "claim_scope": (
                            "diagnostic_only_not_evolution_evidence"
                        ),
                    },
                )
            replay_state_value = str(
                audit_entry.get("replay_state") or detection_state
            )
            state_improved = accepted and replay_state_value != detection_state
            if state_improved:
                # A gold-verified terminal-state change is a real, committed
                # improvement: the accept path already confirmed the gold
                # task score moved toward the label and no canary regressed.
                self.state.edits_accepted += 1
            policy_operation = (
                str(proposal_edit.operation) if proposal_edit else "no_proposal"
            )
            policy_action = (
                str(proposal_edit.new_item.action)
                if proposal_edit and proposal_edit.new_item else "none"
            )
            self.record_attempt(
                decision=decision,
                operation=f"policy:{policy_operation}:{policy_action}",
                target_module=attribution.artifact,
                action_spec=(
                    {
                        "policy_item_id": str(
                            proposal_edit.new_item.item_id
                        ),
                        "action": policy_action,
                    }
                    if proposal_edit and proposal_edit.new_item
                    else {}
                ),
                structural_result=(
                    "no_proposal"
                    if outcome is Outcome.NO_HYPOTHESIS
                    else (
                        "accepted"
                        if accepted or decision.startswith("reject")
                        else "rejected"
                    )
                ),
                compiled=bool(proposal_edit),
                injected=isinstance(replay, Mapping),
                applied=bool(
                    isinstance(replay, Mapping)
                    and isinstance(replay.get("proof_adjudication"), Mapping)
                    and any(
                        isinstance(row, Mapping)
                        and str(row.get("item_id") or "") == (
                            str(proposal_edit.new_item.item_id)
                            if proposal_edit is not None
                            and proposal_edit.new_item is not None
                            else ""
                        )
                        for row in (
                            (
                                replay["proof_adjudication"]
                                .get("policy_evaluation") or {}
                            ).get("fired") or ()
                        )
                    )
                ),
                observable_delta=(
                    (f"state:{detection_state}->{replay_state_value}",)
                    if replay_state_value != detection_state else ()
                ),
                no_effect_reason=(
                    "expected_delta_observed"
                    if state_improved
                    else (
                        str(reason or "")[:80]
                        if not accepted
                        else (
                            "provider_output_changed_but_downstream_invariant"
                        )
                    )
                ),
            )
            self.type_fitness.get_experience_memory().add_experience(
                hallucination_type=HallucinationType.UNKNOWN,
                evolution_direction=(
                    f"{detection_state}_to_{replay_state_value}"
                    if replay_state_value != detection_state
                    else "l2_process_repair"
                ),
                module_targeted=attribution.artifact,
                strategy_used=f"policy:{policy_operation}:{policy_action}",
                detection_improved=state_improved,
                source_case_id=case_id,
                metadata=_structured_outcome(
                    outcome=(
                        "improved" if state_improved
                        else ("directional" if accepted else "rejected")
                    ),
                    stage="l2_policy_replay",
                    operation=policy_operation,
                    expected_effect_status=(
                        "terminal_state_changed" if state_improved
                        else (
                            "coverage_effect_only" if accepted
                            else "not_committed"
                        )
                    ),
                    reason_categories=[reason or decision],
                    decision=decision,
                    layer=LAYER_L2,
                    artifact=attribution.artifact,
                    reason_code=attribution.reason_code,
                    promotable=(
                        state_improved
                        and fitness_gold_label(
                            case_id, records=self.records,
                            fitness_use_gold=self.fitness_use_gold,
                        ) is not None
                    ),
                    diagnostic_only=False,
                ),
                feature_description=(
                    f"{attribution.artifact}:{attribution.reason_code}"
                ),
                type_confidence=0.0,
            )
            self.record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source="l2_policy_reflection",
                attribution=dict(l2_attribution),
                edit_proposal=(
                    proposal_edit.to_dict() if proposal_edit else None
                ),
                mutation_applied=(
                    proposal_edit.to_dict() if accepted else None
                ),
                gate_decision=outcome.value,
                genome_hash_before=genome_before,
                genome_hash_after=self.state.current_genome.content_hash,
                detection_state=detection_state,
                timestamp=time.time(),
                fitness_check={"l2_policy": audit_entry},
            ), next_case_index=idx + 1)
            print(
                f"  [{idx}] L2_POLICY {case_id}: {decision}"
                + (f" ({reason})" if reason else "")
            )

        if self.mutation_route is None:
            finish("no_policy", reason="no_mutation_route")
            return False

        planned_candidates = plan.candidates
        if not planned_candidates:
            if plan.route_result == ROUTE_NO_REGISTERED_RUNTIME_ACTION:
                promoted = promoted_intent_candidates(
                    self.state.policy_genome, facts
                )
                if promoted:
                    plan = PolicyPlan(
                        candidates=promoted,
                        route_result=ROUTE_EXECUTED,
                        feedback_code="",
                    )
                    planned_candidates = promoted
                else:
                    self._run_registry_reflection(
                        attribution=attribution,
                        facts_summary=facts_summary,
                        case_id=case_id,
                    )
            if not planned_candidates:
                finish(
                    "no_policy",
                    reason=plan.route_result,
                    route=plan.route_result,
                    feedback=plan.feedback_code,
                )
                return accepted_outcome
        # The selection prompt never sees gold, so polarity filtering must
        # run deterministically here, before the prompt is built.
        planned_candidates = _filter_policy_candidates_by_gold(
            planned_candidates,
            fitness_gold_label(
                case_id, records=self.records,
                fitness_use_gold=self.fitness_use_gold,
            ),
        )
        if not planned_candidates:
            finish(
                "no_policy",
                reason="all_candidates_filtered_anti_gold",
                route=plan.route_result,
            )
            return accepted_outcome
        meta_guidance = (
            project_strategy_for_prompt(self.state.strategy_state)
            if (
                self.state.strategy_state
                and any(
                    self.state.strategy_state.enabled_in(domain)
                    for domain in (
                        "search_prior",
                        "operation_preference",
                        "mechanism_ranking",
                        "retry_strategy",
                        "candidate_diversity",
                    )
                )
            )
            else None
        )
        system_prompt, user_prompt = build_policy_selection_prompt(
            attribution=attribution,
            facts_summary=facts_summary,
            candidates=planned_candidates,
            meta_guidance=meta_guidance,
        )
        # One bounded retry on transport/parse failure, symmetric with the
        # L1 proposal budget: a single provider wobble must not silently
        # forfeit the case's only L2 repair attempt.
        parsed: Any = None
        reflection_exc: Exception | None = None
        for _attempt in range(2):
            try:
                parsed, _meta = self.run_mutation_call(
                    route=self.mutation_route,
                    purpose="policy_reflection",
                    system=system_prompt,
                    user=user_prompt,
                )
                reflection_exc = None
                break
            except Exception as exc:  # provider transport/parse failure
                reflection_exc = exc
        if reflection_exc is not None:
            finish(
                "no_policy",
                reason=(
                    f"reflection_call_failed:"
                    f"{type(reflection_exc).__name__}: "
                    f"{str(reflection_exc)[:300]}"
                ),
            )
            return False
        proposed_edit, parse_reason = select_policy_candidate(
            parsed,
            planned_candidates,
            registry=self.state.policy_genome.registry,
        )
        if proposed_edit is None:
            finish(
                "no_policy", reason=parse_reason or "no_proposal",
            )
            return False
        if proposed_edit.new_item is not None:
            proposed_edit.new_item = replace(
                proposed_edit.new_item,
                item_id=f"policy_{uuid.uuid4().hex[:8]}",
                source_case_id=case_id,
            )
        proposed_edit.source_case_id = case_id
        proposal_edit = proposed_edit
        valid, validate_reason = validate_policy_edit(
            proposal_edit, self.state.policy_genome,
        )
        if not valid:
            blocked_by_protection = validate_reason.startswith(
                ("protected_item", "immutable_baseline_item")
            )
            finish(
                "no_policy",
                reason=f"invalid_policy_edit:{validate_reason}",
                route=(
                    ROUTE_CANDIDATE_BLOCKED_BY_PROTECTION
                    if blocked_by_protection else ""
                ),
                feedback=(
                    FEEDBACK_POLICY_ROUTE_UNAVAILABLE
                    if blocked_by_protection else ""
                ),
            )
            return False
        candidate_genome = apply_policy_edit(
            self.state.policy_genome, proposal_edit,
        )
        try:
            replay_result = self.run_detection(
                budget_kind="policy",
                genome=self.state.current_genome,
                case_id=case_id,
                policy_genome=candidate_genome,
                # Candidate validation must observe the full candidate item
                # set: a newly proposed item is still fitness_status=none and
                # is excluded from the working scope, so a scoped replay
                # could never fire it and every proposal would die as
                # no-effect.
                policy_scope=None,
            )
        except Exception as exc:
            replay_error_detail = f"{type(exc).__name__}: {exc}"
            finish(
                "reject_policy_replay_error",
                reason=(
                    f"replay_runtime_invalid:"
                    f"{classify_runtime_error(replay_error_detail)}"
                ),
                replay_error_detail=replay_error_detail,
                replay_error_traceback=traceback.format_exc(),
            )
            return False
        replay_state = resolve_evolution_state(replay_result)
        replay_adjudication = replay_result.get("proof_adjudication") or {}
        if not isinstance(replay_adjudication, Mapping):
            replay_adjudication = {}
        evaluation = replay_adjudication.get("policy_evaluation") or {}
        if not isinstance(evaluation, Mapping):
            evaluation = {}
        fired = evaluation.get("fired") or []
        skipped_rows = evaluation.get("skipped") or []
        proposal_item_id = (
            str(proposal_edit.new_item.item_id)
            if proposal_edit.new_item is not None else ""
        )
        proposal_skipped_reason = next(
            (
                str(row.get("reason") or "")
                for row in skipped_rows
                if isinstance(row, Mapping)
                and str(row.get("item_id") or "") == proposal_item_id
            ),
            "",
        )
        if (
            not fired
            and proposal_skipped_reason == ROUTE_RUNTIME_PRECONDITION_NOT_SATISFIED
        ):
            finish(
                "reject_policy_no_effect",
                reason=f"policy_skipped:{proposal_skipped_reason}",
                replay=replay_result,
                route=ROUTE_RUNTIME_PRECONDITION_NOT_SATISFIED,
                feedback=FEEDBACK_POLICY_ROUTE_UNAVAILABLE,
            )
            return False
        state_changed = replay_state != detection_state
        coverage_changed = (
            evaluation.get("effective_prosecution_coverage")
            != facts.prosecution_coverage
            or evaluation.get("effective_defense_coverage")
            != facts.defense_coverage
        )
        operation = str(proposal_edit.operation)
        if operation == PolicyOp.REMOVE_ITEM.value:
            source_evaluation = (
                detection_result.get("proof_adjudication") or {}
            ).get("policy_evaluation")
            source_evaluation = (
                source_evaluation
                if isinstance(source_evaluation, Mapping) else {}
            )
            original_fired_ids = {
                str(row.get("item_id") or "")
                for row in (source_evaluation.get("fired") or [])
                if isinstance(row, Mapping)
            }
            removed_item = self.state.policy_genome.items[
                proposal_edit.item_index
            ]
            effect_ok = (
                removed_item.item_id in original_fired_ids
                and (state_changed or coverage_changed)
            )
            no_effect_reason = "removed_item_did_not_move_the_terminal_edge"
        else:
            proposal_fired = any(
                isinstance(row, Mapping)
                and str(row.get("item_id") or "") == proposal_item_id
                for row in fired
            )
            effect_ok = proposal_fired and (state_changed or coverage_changed)
            no_effect_reason = (
                "policy_did_not_move_the_terminal_edge"
                if proposal_fired else "proposed_item_did_not_fire"
            )
        if not effect_ok:
            finish(
                "reject_policy_no_effect",
                reason=no_effect_reason,
                replay=replay_result,
            )
            return False
        gold_label = fitness_gold_label(
            case_id, records=self.records,
            fitness_use_gold=self.fitness_use_gold,
        )
        if state_changed:
            if gold_label is None:
                finish(
                    "reject_policy_fitness",
                    reason="state_change_without_gold_polarity",
                    replay=replay_result,
                )
                return False
            try:
                score_before = task_state_score(detection_state, gold_label)
                score_after = task_state_score(replay_state, gold_label)
            except ValueError:
                finish(
                    "reject_policy_fitness",
                    reason="unscorable_state",
                    replay=replay_result,
                )
                return False
            if score_after <= score_before:
                finish(
                    "reject_policy_fitness",
                    reason="state_change_against_gold_polarity",
                    replay=replay_result,
                )
                return False
        if (
            self.registered_canary_ids
            and not promotion_canaries
            and not self.fitness_use_gold
        ):
            finish(
                "reject_policy_fitness",
                reason="independent_canary_unavailable",
                replay=replay_result,
            )
            return False
        for canary_id, parent_canary in list(
            promotion_canaries.items()
        )[:self.canary_limit]:
            try:
                child_canary = self.run_detection(
                    budget_kind="canary",
                    genome=self.state.current_genome,
                    case_id=canary_id,
                    policy_genome=candidate_genome,
                    policy_scope=None,
                )
                preserved, audit = canary_preserved(
                    parent_canary, child_canary,
                    use_gold=self.use_gold,
                    gold_label=_evaluator_gold(self.records[canary_id]),
                )
                policy_canary_audit.append({"case_id": canary_id, **audit})
                if preserved:
                    policy_canary_results[canary_id] = child_canary
                else:
                    policy_canary_regressions.append(canary_id)
            except Exception as exc:
                policy_canary_regressions.append(canary_id)
                policy_canary_audit.append({
                    "case_id": canary_id,
                    "preserved": False,
                    "error": f"{type(exc).__name__}:{exc}",
                })
        if policy_canary_regressions:
            finish(
                "reject_policy_fitness",
                reason=(
                    "stable_canary_regression:"
                    + ",".join(policy_canary_regressions)
                ),
                replay=replay_result,
            )
            return False
        if operation != PolicyOp.REMOVE_ITEM.value:
            accepted_index = (
                proposal_edit.item_index
                if operation == PolicyOp.MODIFY_ITEM.value
                else len(candidate_genome.items) - 1
            )
            accepted_meta = candidate_genome.get_item_meta(accepted_index)
            if state_changed:
                accepted_meta["fitness_status"] = FITNESS_STATUS_STRONG
            elif (
                accepted_meta.get("fitness_status") != FITNESS_STATUS_STRONG
            ):
                accepted_meta["fitness_status"] = FITNESS_STATUS_WEAK
            candidate_genome.set_item_meta(accepted_index, accepted_meta)
        self.state.policy_genome = candidate_genome
        self.stable_canaries.update(policy_canary_results)
        finish("accept_policy", replay=replay_result)
        return accepted_outcome

    def _run_l1_policy_case(
        self, *,
        idx: int, case_id: str,
        detection_result: Mapping[str, Any],
        detection_state: str,
        genome_before: str,
        signal: Any, view: Any, cut: Any, episode: CausalFailureEpisode,
        type_result: Any, evolution_direction: str,
    ) -> None:
        """Run the L1 provider-rule repair path for one REPAIRABLE episode.

        Reflection (hypothesis search) -> deterministic pre-validation ->
        structural gate -> optional LLM no-conflict gate -> source replay
        (execution evidence) -> fitness + canary + contrast (credit chain,
        C10) -> provisional shadow admit or terminal rejection. Every
        terminal deposits exactly one §2.9 outcome; only fitness-layer
        rejections of executed plans advance the convergence window (C9).
        """
        print(
            f"    [ATTR] owner hypothesis: {cut.owner} via {cut.reason_code}"
        )
        bias_signal = _localization_bias_signal(
            detection_result,
            last_audited_step=self._last_audited_step(case_id),
        )
        if bias_signal:
            self.localization_bias_signals.append({
                "case_id": case_id,
                "signal": bias_signal,
                "responsible_step": str(
                    detection_result.get("responsible_step")
                ),
            })
        type_allowed_modules = _allowed_l1_modules(
            signal,
            cut.owner,
            (
                cut.evidence.get("artifact_frontier", ())
                if isinstance(cut.evidence, Mapping) else ()
            ),
            cut.secondary_owner,
        )
        type_guidance = self.type_evolution.generate_type_guidance(
            type_result.primary_type, evolution_direction,
        )
        print(
            f"    [HTC-TES] type={type_result.primary_type.value} "
            f"confidence={type_result.confidence:.2f}"
        )
        if detection_state in _CONTRAST_RESOLVED_STATES:
            self.contrast_replay_registry.setdefault(
                type_result.primary_type.value, []
            ).append({
                "case_id": case_id,
                "state": detection_state,
                "stable": False,
                "artifact": cut.owner or view.target_module,
                "reason": cut.reason_code or "",
            })
            _trim_contrast_registry(self.contrast_replay_registry)

        # --- [3] LLM causal reflection (hypothesis search) ---
        edit = None
        pending_rule_slots: dict[str, Any] | None = None
        pending_condition_spec: dict[str, Any] = {}
        pending_repair_edge: dict[str, Any] = {}
        no_edit_reason = ""
        invalid_mutation_reason = ""
        reflection_audit: dict[str, Any] = {
            "requested_candidates": self.reflection_candidate_count,
            "generated_candidates": 0,
            "critic": None,
            "proposed_target_module": None,
            "validated_target_module": None,
        }
        allowed_modules = type_allowed_modules

        if self.mutation_route is not None:
            skill_context = "\n\n".join(
                format_skill_context_for_llm(
                    self.state.current_genome.modules, module,
                )
                for module in allowed_modules
            )
            detection_context = format_detection_context_for_llm(
                detection_result,
                trajectory_record=_detector_record(self.records[case_id]),
            )
            template_hint = None
            capacity_guidance = "\n".join(
                value
                for value in (
                    build_capacity_guidance(
                        self.state.current_genome.modules, module,
                    )
                    for module in allowed_modules
                )
                if value
            )
            accepted_summary = build_accepted_edits_summary(
                self.accepted_edits_history
            )
            meta_guidance = (
                project_strategy_for_prompt(self.state.strategy_state)
                if (
                    self.state.strategy_state
                    and any(
                        self.state.strategy_state.enabled_in(domain)
                        for domain in (
                            "search_prior",
                            "operation_preference",
                            "mechanism_ranking",
                            "retry_strategy",
                            "candidate_diversity",
                        )
                    )
                )
                else None
            )
            repair_frontier_dicts = tuple(
                edge.to_dict() for edge in episode.causal_slice.repair_frontier
            )
            history_refs = self.causal_memory.retrieve(
                episode=episode,
                exclude_episode_id=episode.episode_id,
            )
            episode_projection = project_for_prompt(
                episode, historical_refs=history_refs,
            )
            runtime_observation = build_runtime_observation(
                signal, allowed_modules=allowed_modules,
            )
            system_prompt, user_prompt = build_causal_reflection_prompt(
                signal=view,
                current_skill_text=skill_context,
                detection_context=detection_context,
                rejected_buffer=self.rejected_buffer,
                template_hint=template_hint,
                accepted_edits_summary=accepted_summary,
                capacity_guidance=capacity_guidance,
                allowed_target_modules=allowed_modules,
                episode_projection=episode_projection,
                candidate_count=1,
                meta_guidance=meta_guidance,
                episode=episode,
                experience_memory=self.causal_memory,
                runtime_observation=runtime_observation,
            )
            experience_summary = (
                self.type_fitness.get_experience_memory().get_reflection_summary(
                    type_result.primary_type, evolution_direction,
                )
            )
            user_prompt += f"\n\n{type_guidance}"
            if experience_summary.get("total", 0) > 0:
                user_prompt += (
                    "\n[Prior type-conditioned replay evidence]\n"
                    + json.dumps(
                        experience_summary, ensure_ascii=False, sort_keys=True,
                    )
                    + "\nUse successful patterns as hypotheses, avoid repeated "
                    "failed strategies, and do not treat this memory as verdict "
                    "authority."
                )
            if self.active_attempt_index > 0:
                system_prompt += _build_structural_system_constraints(
                    self.mutation_retry_feedback.get(case_id, []),
                )
                user_prompt += _build_mutation_retry_feedback(
                    attempt_index=self.active_attempt_index,
                    prior_categories=self.mutation_retry_feedback.get(
                        case_id, []
                    ),
                    rejected_prior=self.rejected_source_proposals.get(
                        case_id, []
                    ),
                )

            try:
                candidates: list[dict[str, Any]] = []
                no_edit_reasons: list[str] = []
                primary_cache_hits: list[bool] = []
                mutation_provider_unavailable = False
                round_reject_categories: list[str] = []
                round_direction_signatures: list[str] = []
                generated_count = 0
                for generation_index in range(self.reflection_candidate_count):
                    required_module = _generation_module_requirement(
                        allowed_modules,
                        generation_index,
                        tuple(
                            str(candidate.get("target_module") or "")
                            for candidate in candidates
                        ),
                    )
                    generation_user = (
                        user_prompt
                        + f"\n<independent_generation index=\"{generation_index}\">"
                        "Produce a distinct intervention direction; do not repeat "
                        "a previous direction. The index is orchestration "
                        "metadata, not rule content.</independent_generation>"
                    )
                    if required_module:
                        generation_user += (
                            "\n[OWNER COVERAGE CONTRACT]\n"
                            "This independent sample must use target_module="
                            f"{required_module}. Produce no candidate for another "
                            "module; return no_edit if this owner has no "
                            "executable intervention."
                        )
                    if round_direction_signatures:
                        generation_user += (
                            "\n[VALID DIRECTIONS ALREADY SAMPLED]\n- "
                            + "\n- ".join(round_direction_signatures)
                            + "\nUse a different module/action surface or "
                            "return no_edit."
                        )
                    if round_reject_categories:
                        generation_user += (
                            "\n[PREVALIDATION FEEDBACK for this round]\n"
                            "Earlier candidates in this round were rejected by "
                            "the deterministic pre-validation with:\n- "
                            + "\n- ".join(dict.fromkeys(round_reject_categories))
                            + "\nDo not repeat these defects."
                        )
                    generated, meta = self.run_mutation_call(
                        route=self.mutation_route,
                        purpose="mutation",
                        system=system_prompt,
                        user=generation_user,
                    )
                    primary_cache_hits.append(bool(
                        isinstance(meta, Mapping) and meta.get("cache_hit")
                    ))
                    generated_candidates, generated_no_edit = (
                        extract_intervention_candidates(generated, limit=1)
                    )
                    generated_count += len(generated_candidates)
                    generated_candidates = [
                        _normalize_candidate_from_repair_plan(
                            candidate, repair_frontier_dicts,
                        )
                        for candidate in generated_candidates
                    ]
                    for candidate in generated_candidates:
                        prevalidate_reason = _prevalidate_candidate(
                            candidate,
                            allowed_modules,
                            error_type=getattr(view, "error_type", None),
                            failure_edge_field=(
                                episode.causal_slice.failure_edge.field
                                if (
                                    episode.causal_slice.failure_edge is not None
                                )
                                else ""
                            ),
                            repair_frontier=repair_frontier_dicts,
                            runtime_condition_features=(
                                runtime_observation.get(
                                    "runtime_condition_features", {}
                                )
                                if isinstance(runtime_observation, Mapping)
                                else {}
                            ),
                        )
                        if prevalidate_reason is None:
                            candidates.append(candidate)
                            round_direction_signatures.append(
                                _candidate_direction_signature(candidate)
                            )
                        else:
                            round_reject_categories.append(prevalidate_reason)
                    if generated_no_edit:
                        no_edit_reasons.append(generated_no_edit)
                candidates, _ = extract_intervention_candidates(
                    {"candidates": candidates},
                    limit=self.reflection_candidate_count,
                )
                reflection_audit["primary_cache_hit"] = any(primary_cache_hits)
                reflection_audit["primary_cache_hits"] = primary_cache_hits
                reflection_audit["generated_candidates"] = generated_count
                prevalidation_rejects = [
                    {"target_module": "", "reason": reason}
                    for reason in dict.fromkeys(round_reject_categories)
                ]
                reflection_audit["prevalidated_candidates"] = len(candidates)
                reflection_audit["prevalidation_rejects"] = prevalidation_rejects
                if not candidates and prevalidation_rejects:
                    invalid_mutation_reason = (
                        "all_candidates_prevalidation_rejected:"
                        + prevalidation_rejects[0]["reason"]
                    )
                no_edit_reason = ""
                if len(candidates) > 1:
                    critic_system, critic_user = (
                        build_intervention_critic_prompt(
                            candidates=candidates,
                            runtime_observation=runtime_observation,
                            current_skill_text=skill_context,
                            allowed_target_modules=allowed_modules,
                        )
                    )
                    critic_parsed, _critic_meta = self.run_mutation_call(
                        route=self.mutation_route,
                        purpose="reflection_critic",
                        system=critic_system,
                        user=critic_user,
                    )
                    reflection_audit["critic_cache_hit"] = bool(
                        isinstance(_critic_meta, Mapping)
                        and _critic_meta.get("cache_hit")
                    )
                    parsed, critic_audit = select_intervention_candidate(
                        candidates, critic_parsed,
                    )
                    reflection_audit["critic"] = critic_audit
                    if parsed is None:
                        if critic_audit.get("decision") == "no_edit":
                            no_edit_reason = str(
                                critic_audit.get("reason")
                                or "critic found no safe candidate"
                            )
                        else:
                            invalid_mutation_reason = (
                                "critic_rejected_candidates:"
                                + str(
                                    critic_audit.get("reason") or "no_selection"
                                )
                            )
                        parsed = {}
                elif not candidates and not mutation_provider_unavailable and not invalid_mutation_reason:
                    parsed = {}
                    diagnosis_routes = [self.mutation_route]
                    if self.pro_mutation_route is not None:
                        diagnosis_routes.append(self.pro_mutation_route)
                    for diag_index, diag_route in enumerate(diagnosis_routes):
                        diag_purpose = (
                            "reflection_diagnosis"
                            if diag_route is self.mutation_route
                            else "reflection_pro"
                        )
                        diagnosis: dict[str, Any] = {}
                        try:
                            diag_system, diag_user = build_failure_diagnosis_prompt(
                                signal=view,
                                runtime_observation=runtime_observation,
                                detection_context=detection_context,
                                current_skill_text=skill_context,
                                allowed_target_modules=allowed_modules,
                            )
                            diag_parsed, _diag_meta = self.run_mutation_call(
                                route=diag_route,
                                purpose=diag_purpose,
                                system=diag_system,
                                user=diag_user,
                            )
                            diagnosis = sanitize_diagnosis(
                                diag_parsed, allowed_modules,
                                runtime_observation=runtime_observation,
                            )
                        except Exception as exc:
                            diagnosis = {
                                "owner_executable": False,
                                "owner_module": None,
                                "failure_field": "",
                                "observed_gap": "",
                                "reasoning": (
                                    f"diagnosis_unavailable:{type(exc).__name__}"
                                ),
                            }
                        reflection_audit["diagnosis"] = diagnosis
                        reflection_audit["diagnosis_route"] = diag_purpose
                        if not diagnosis.get("owner_executable"):
                            continue
                        targeted_system, targeted_user = build_causal_reflection_prompt(
                            signal=view,
                            current_skill_text=skill_context,
                            detection_context=detection_context,
                            rejected_buffer=self.rejected_buffer,
                            template_hint=template_hint,
                            accepted_edits_summary=accepted_summary,
                            capacity_guidance=capacity_guidance,
                            allowed_target_modules=allowed_modules,
                            episode_projection=episode_projection,
                            candidate_count=1,
                            targeted_diagnosis=diagnosis,
                            episode=episode,
                            experience_memory=self.causal_memory,
                            runtime_observation=runtime_observation,
                        )
                        if self.active_attempt_index > 0:
                            targeted_system += _build_structural_system_constraints(
                                self.mutation_retry_feedback.get(case_id, []),
                            )
                        targeted_ok = False
                        try:
                            targeted_parsed, _target_meta = self.run_mutation_call(
                                route=diag_route,
                                purpose=(
                                    "mutation"
                                    if diag_purpose == "reflection_diagnosis"
                                    else "reflection_pro"
                                ),
                                system=targeted_system,
                                user=targeted_user,
                            )
                            targeted_candidates, targeted_no_edit = (
                                extract_intervention_candidates(
                                    targeted_parsed, limit=1,
                                )
                            )
                            reflection_audit["targeted_retry"] = True
                            reflection_audit["targeted_cache_hit"] = bool(
                                isinstance(_target_meta, Mapping)
                                and _target_meta.get("cache_hit")
                            )
                            if targeted_candidates:
                                parsed = targeted_candidates[0]
                                reflection_audit["generated_candidates"] = 1
                                targeted_ok = True
                            elif targeted_no_edit:
                                no_edit_reason = targeted_no_edit
                            else:
                                no_edit_reason = (
                                    no_edit_reasons[0]
                                    if no_edit_reasons
                                    else "LLM found no safe general edit"
                                )
                        except Exception as exc:
                            reflection_audit["targeted_retry"] = True
                            no_edit_reason = (
                                no_edit_reasons[0]
                                if no_edit_reasons
                                else (
                                    f"targeted_generation_unavailable:"
                                    f"{type(exc).__name__}"
                                )
                            )
                        if targeted_ok:
                            no_edit_reason = ""
                            break
                    if not parsed and not no_edit_reason:
                        no_edit_reason = (
                            no_edit_reasons[0]
                            if no_edit_reasons
                            else "LLM found no safe general edit"
                        )
                else:
                    parsed = candidates[0] if candidates else {}
                    reflection_audit["selected_index"] = 0
                if parsed:
                    parsed = _normalize_candidate_from_repair_plan(
                        parsed, repair_frontier_dicts,
                    )
                final_prevalidation_reason = (
                    _prevalidate_candidate(
                        parsed,
                        allowed_modules,
                        error_type=getattr(view, "error_type", None),
                        failure_edge_field=(
                            episode.causal_slice.failure_edge.field
                            if (
                                episode.causal_slice.failure_edge is not None
                            )
                            else ""
                        ),
                        repair_frontier=repair_frontier_dicts,
                        runtime_condition_features=(
                            runtime_observation.get(
                                "runtime_condition_features", {}
                            )
                            if isinstance(runtime_observation, Mapping)
                            else {}
                        ),
                    )
                    if parsed else None
                )
                if final_prevalidation_reason is not None:
                    invalid_mutation_reason = (
                        "selected_candidate_prevalidation_rejected:"
                        + final_prevalidation_reason
                    )
                    reflection_audit["selected_prevalidation_reject"] = (
                        final_prevalidation_reason
                    )
                    parsed = {}
                raw_target_module = str(parsed.get("target_module") or "")
                reflection_audit["proposed_target_module"] = (
                    raw_target_module or None
                )
                if raw_target_module and raw_target_module not in allowed_modules:
                    invalid_mutation_reason = (
                        "candidate_target_outside_allowed_modules:"
                        + raw_target_module
                    )
                    parsed = {}
                    reflection_audit["validated_target_module"] = None
                target_module = raw_target_module
                if target_module not in allowed_modules:
                    target_module = allowed_modules[0]
                elif target_module:
                    reflection_audit["validated_target_module"] = target_module
                operation = str(parsed.get("operation") or "")
                rule_index = parsed.get("rule_index")
                pending_condition_spec = sanitize_condition_spec(
                    parsed.get("condition_spec")
                )
                selected_repair_edge_id = str(
                    parsed.get("repair_edge_id") or ""
                )
                if selected_repair_edge_id:
                    pending_repair_edge = next(
                        (
                            edge.to_dict()
                            for edge in episode.causal_slice.repair_frontier
                            if edge.edge_id == selected_repair_edge_id
                        ),
                        {},
                    )
                new_content = ""
                if operation in {"add_rule", "modify_rule"}:
                    if not all(
                        key in parsed
                        for key in ("condition", "action", "evidence_anchor")
                    ):
                        invalid_mutation_reason = "rule_spec:missing_slots"
                    else:
                        condition = str(parsed.get("condition") or "")
                        action = str(parsed.get("action") or "")
                        evidence_anchor = str(
                            parsed.get("evidence_anchor") or ""
                        )
                        new_content = render_rule(RuleSpec(
                            condition=condition,
                            action=action,
                            evidence_anchor=evidence_anchor,
                            module_id=target_module,
                            direction="process",
                        ))
                        spec = RuleSpec(
                            condition=condition,
                            action=action,
                            evidence_anchor=evidence_anchor,
                            module_id=target_module,
                            direction=_detect_rule_direction(new_content),
                        )
                        spec_ok, spec_reason = validate_rule_spec(spec)
                        if not spec_ok:
                            new_content = ""
                            invalid_mutation_reason = f"rule_spec:{spec_reason}"
                        else:
                            pending_rule_slots = {
                                "condition": condition,
                                "action": action,
                                "evidence_anchor": evidence_anchor,
                            }
                rationale = str(
                    parsed.get("rationale")
                    or parsed.get("reasoning") or ""
                )
                feature_description = str(
                    parsed.get("feature_description") or ""
                )[:500]
                expected_effect = str(
                    parsed.get("expected_effect") or ""
                )[:240]
                expected_effect_contract = {}
                pending_action_spec: dict[str, Any] = {}
                if pending_rule_slots:
                    try:
                        compiled_action = compile_action_spec(
                            target_module, pending_rule_slots["action"],
                        )
                    except (KeyError, ValueError) as exc:
                        invalid_mutation_reason = f"action_spec:{exc}"
                    else:
                        pending_action_spec = compiled_action.to_dict()
                        expected_effect = (
                            compiled_action.expected_effect_text()
                        )
                        expected_effect_contract = (
                            compiled_action.effect_contract(
                                pending_rule_slots["condition"]
                            )
                        )
                elif operation == "remove_rule" and rule_index is not None:
                    try:
                        removed_rule_index = int(rule_index)
                        removed_meta = (
                            self.state.current_genome.modules[target_module]
                            .get_rule_meta(removed_rule_index)
                        )
                    except (KeyError, TypeError, ValueError):
                        removed_meta = {}
                    removed_action_payload = sanitize_action_spec(
                        removed_meta.get("action_spec")
                    )
                    if removed_action_payload:
                        from sehd_rise.skills.action import ActionSpec
                        removed_action = ActionSpec.from_dict(
                            removed_action_payload
                        )
                        removed_slots = removed_meta.get("rule_slots")
                        removed_condition = (
                            str(removed_slots.get("condition") or "")
                            if isinstance(removed_slots, Mapping) else ""
                        )
                        expected_effect = removed_action.removal_effect_text()
                        expected_effect_contract = (
                            removed_action.removal_effect_contract(
                                removed_condition
                                or "removed rule condition"
                            )
                        )
                    else:
                        invalid_mutation_reason = (
                            "action_spec:removed_rule_has_no_executable_spec"
                        )
                else:
                    raw_contract = parsed.get("expected_effect_contract")
                    if isinstance(raw_contract, dict):
                        expected_effect_contract = raw_contract
                diagnosis = parsed.get("diagnosis")
                diagnosis_present = isinstance(diagnosis, Mapping)
                diagnosis_valid = (
                    not diagnosis_present
                    or (
                        bool(
                            str(diagnosis.get("failed_output") or "").strip()
                        )
                        and str(diagnosis.get("owner_module") or "")
                        == target_module
                        and bool(
                            str(
                                diagnosis.get("existing_rule_gap") or ""
                            ).strip()
                        )
                    )
                )
                if diagnosis_present and not diagnosis_valid:
                    reflection_audit["causal_diagnosis_dropped"] = True
                    diagnosis = None
                    diagnosis_present = False
                elif diagnosis_present:
                    reflection_audit["causal_diagnosis"] = dict(diagnosis)
                contract_field = str(
                    expected_effect_contract.get("observable_field") or ""
                )
                contract_present = bool(expected_effect_contract)
                contract_shape_valid, contract_shape_reason = (
                    validate_expected_effect_contract(
                        expected_effect_contract
                    )
                )
                contract_valid = (
                    not contract_present
                    or (
                        contract_shape_valid
                        and contract_field
                        in MODULE_OBSERVABLE_FIELDS.get(target_module, ())
                        and str(
                            expected_effect_contract.get(
                                "expected_direction"
                            ) or ""
                        ) in EXPECTED_DIRECTIONS
                        and str(
                            expected_effect_contract.get("expected_scope") or ""
                        ) in EXPECTED_SCOPES
                        and bool(
                            str(
                                expected_effect_contract.get(
                                    "trigger_condition"
                                ) or ""
                            ).strip()
                        )
                    )
                )

                if no_edit_reason:
                    print(f"    LLM requested NO_EDIT: {no_edit_reason}")
                elif invalid_mutation_reason:
                    pass
                elif operation == "no_edit":
                    no_edit_reason = (
                        rationale[:300]
                        or "LLM found no safe general edit"
                    )
                    print(f"    LLM requested NO_EDIT: {no_edit_reason}")
                elif operation == "remove_rule" and rule_index is None:
                    invalid_mutation_reason = "remove_rule_missing_rule_index"
                elif operation in {"add_rule", "modify_rule", "remove_rule"} and (
                    not expected_effect or not contract_present
                ):
                    invalid_mutation_reason = (
                        "invalid_intervention_contract:missing"
                    )
                elif contract_present and not contract_valid:
                    invalid_mutation_reason = (
                        "invalid_intervention_contract:"
                        f"module={target_module},field={contract_field or 'missing'}"
                        + (
                            f",reason={contract_shape_reason}"
                            if contract_shape_reason else ""
                        )
                    )
                elif (
                    self.active_attempt_index > 0
                    and _rejected_proposal_duplicate(
                        self.rejected_source_proposals.get(case_id, []),
                        target_module=target_module,
                        new_content=new_content,
                    )
                ):
                    invalid_mutation_reason = (
                        "retry_duplicate_proposal:"
                        f"module={target_module}"
                    )
                elif _cross_case_no_effect_duplicate(
                    target_module=target_module,
                    new_content=new_content,
                    entries=self.causal_memory.entries(),
                    exclude_episode_id=(
                        self.state.active_episode_payload.get("episode_id", "")
                        if self.state.active_episode_payload is not None else ""
                    ),
                ):
                    invalid_mutation_reason = (
                        "cross_case_no_effect_duplicate:"
                        f"module={target_module}"
                    )
                elif operation == "remove_rule" and rule_index is not None:
                    edit = SkillEdit(
                        edit_id=f"edit_{uuid.uuid4().hex[:8]}",
                        target_module=target_module,
                        operation=operation,
                        rule_index=int(rule_index),
                        new_content="",
                        rationale=f"[LLM] {rationale[:200]}",
                        source_case_id=case_id,
                        feature_description=feature_description,
                        hallucination_type=type_result.primary_type.value,
                        secondary_hallucination_type=(
                            type_result.secondary_type.value
                            if type_result.secondary_type is not None else ""
                        ),
                        evolution_direction=evolution_direction,
                        type_confidence=type_result.confidence,
                        routing_allowed_modules=tuple(allowed_modules),
                        expected_effect=expected_effect,
                        expected_effect_contract=expected_effect_contract,
                        condition_spec=pending_condition_spec,
                        repair_edge=pending_repair_edge,
                    )
                    print(
                        f"    LLM generated: {target_module}/{operation} "
                        f"index={rule_index}"
                    )
                elif operation in {"add_rule", "modify_rule"} and new_content:
                    edit = SkillEdit(
                        edit_id=f"edit_{uuid.uuid4().hex[:8]}",
                        target_module=target_module,
                        operation=operation,
                        rule_index=(
                            int(rule_index)
                            if rule_index is not None else None
                        ),
                        new_content=new_content,
                        rationale=f"[LLM] {rationale[:200]}",
                        source_case_id=case_id,
                        feature_description=feature_description,
                        hallucination_type=type_result.primary_type.value,
                        secondary_hallucination_type=(
                            type_result.secondary_type.value
                            if type_result.secondary_type is not None else ""
                        ),
                        evolution_direction=evolution_direction,
                        type_confidence=type_result.confidence,
                        routing_allowed_modules=tuple(allowed_modules),
                        expected_effect=expected_effect,
                        expected_effect_contract=expected_effect_contract,
                        rule_slots=pending_rule_slots or {},
                        action_spec=pending_action_spec,
                        condition_spec=pending_condition_spec,
                        repair_edge=pending_repair_edge,
                    )
                    print(
                        f"    LLM generated: {target_module}/{operation}: "
                        f"{new_content[:60]}..."
                    )
                else:
                    invalid_mutation_reason = (
                        "invalid_single_reflection_output:"
                        f"operation={operation!r},content_length={len(new_content)}"
                    )
            except Exception as exc:
                error_text = str(exc).lower()
                if (
                    "missing provider credential" in error_text
                    or "no valid api-key environment" in error_text
                ):
                    mutation_provider_unavailable = True
                    no_edit_reason = "mutation_provider_credential_unavailable"
                    parsed = {}
                else:
                    invalid_mutation_reason = (
                        "single_reflection_runtime_error:"
                        f"{type(exc).__name__}:{exc}"
                    )
                    parsed = {}

        if self.mutation_route is None:
            no_edit_reason = "mutation_route_unavailable"

        if edit is None and no_edit_reason:
            self.type_fitness.get_experience_memory().add_experience(
                hallucination_type=type_result.primary_type,
                evolution_direction=evolution_direction,
                module_targeted=allowed_modules[0],
                strategy_used="model_no_edit",
                detection_improved=False,
                source_case_id=case_id,
                metadata=_structured_outcome(
                    outcome="not_attempted",
                    stage="mutation",
                    operation="no_edit",
                    expected_effect_status="not_proposed",
                    reason_categories=_no_edit_reason_categories(
                        no_edit_reason
                    ),
                    decision="no_edit",
                    layer="l1_provider_rule",
                    artifact=(
                        cut.owner if cut.owner else allowed_modules[0]
                    ),
                    reason_code=cut.reason_code,
                ),
                feature_description="",
                type_confidence=type_result.confidence,
            )
            self.record_attempt(
                decision="no_edit",
                structural_result="no_proposal",
                no_effect_reason="",
            )
            self.funnel.record(Outcome.NO_HYPOTHESIS)
            self.record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source=view.trigger_source,
                attribution=signal.to_dict(),
                edit_proposal={
                    "operation": "no_edit",
                    "rationale": no_edit_reason,
                    "reflection": reflection_audit,
                },
                mutation_applied=None,
                gate_decision=Outcome.NO_HYPOTHESIS.value,
                genome_hash_before=genome_before,
                genome_hash_after=self.state.current_genome.content_hash,
                detection_state=detection_state,
                timestamp=time.time(),
            ), next_case_index=idx + 1)
            print(f"  [{idx}] NO_HYPOTHESIS {case_id}: {no_edit_reason}")
            return

        # --- [3b] Invalid mutation or provider unavailable ---
        if edit is None:
            print(f"  [{idx}] INVALID_PROPOSAL {case_id}")
            retry_scheduled = (
                self.mutation_route is not None
                and self.active_attempt_index + 1
                < self._attempt_cap_for(cut.reason_code)
            )
            if retry_scheduled:
                failure_category = _classify_mutation_failure(
                    invalid_mutation_reason
                    or "mutation provider unavailable",
                )
                self.mutation_retry_feedback.setdefault(case_id, []).append(
                    failure_category
                )
                self.source_work_queue.appendleft(
                    (idx, case_id, self.active_attempt_index + 1)
                )
            self.record_attempt(
                decision=(
                    "retry_invalid_mutation"
                    if retry_scheduled else "invalid_mutation_terminal"
                ),
                structural_result="invalid_mutation",
            )
            self.record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source=view.trigger_source,
                attribution=signal.to_dict(),
                edit_proposal={
                    "operation": "invalid_mutation",
                    "rationale": invalid_mutation_reason
                    or "mutation provider unavailable",
                },
                mutation_applied=None,
                gate_decision=(
                    "retry_invalid_mutation"
                    if retry_scheduled else Outcome.INVALID_PROPOSAL.value
                ),
                genome_hash_before=genome_before,
                genome_hash_after=self.state.current_genome.content_hash,
                detection_state=detection_state,
                timestamp=time.time(),
                fitness_check={
                    "retry_scheduled": retry_scheduled,
                    "mutation_error": invalid_mutation_reason
                    or "mutation provider unavailable",
                    "reflection": reflection_audit,
                    "retry_failure_category": (
                        self.mutation_retry_feedback.get(case_id, [None])[-1]
                        if retry_scheduled else None
                    ),
                },
            ), next_case_index=(idx if retry_scheduled else idx + 1))
            if not retry_scheduled:
                self.funnel.record(Outcome.INVALID_PROPOSAL)
            return

        # --- [4] Structural constraint gate ---
        accepted, cert, reject_reasons = self.gate.evaluate(
            case_id=case_id,
            case_index=idx,
            target_module=edit.target_module,
            operation=edit.operation,
            content=edit.new_content,
            preconditions=(),
            current_genome=self.state.current_genome,
            rule_index=edit.rule_index,
            expected_module=allowed_modules,
            rule_slots=pending_rule_slots,
        )

        if not accepted:
            retry_scheduled = False
            merge_reason = next(
                (
                    r for r in reject_reasons
                    if r.startswith("cross_module_merge_required")
                    or r.startswith("merge_required:")
                ),
                None,
            )
            if merge_reason is not None and edit.operation == "add_rule":
                feedback = self.mutation_retry_feedback.setdefault(
                    case_id, []
                )
                marker = (
                    "structural:"
                    "semantic_overlap_requires_distinct_discriminator"
                )
                if marker not in feedback:
                    feedback.append(marker)
                retry_scheduled = (
                    self.mutation_route is not None
                    and self.active_attempt_index + 1
                    < self._attempt_cap_for(cut.reason_code)
                )
            if not accepted:
                if retry_scheduled:
                    self.gate.defer_structural_reject(cert)
                    self.source_work_queue.appendleft(
                        (idx, case_id, self.active_attempt_index + 1)
                    )
                    self.record_attempt(
                        decision="retry_structural",
                        operation=(
                            edit.operation.value
                            if isinstance(edit.operation, SkillOp)
                            else str(edit.operation)
                        ),
                        target_module=edit.target_module,
                        action_spec=edit.action_spec or pending_action_spec,
                        structural_result="rejected",
                        compiled=bool(edit.action_spec or pending_action_spec),
                        proposal_content=edit.new_content,
                    )
                    self.record_step(EvolutionStep(
                        case_id=case_id,
                        case_index=idx,
                        trigger_source=view.trigger_source,
                        attribution=signal.to_dict(),
                        edit_proposal=edit.to_dict(),
                        mutation_applied=None,
                        gate_decision="retry_structural",
                        genome_hash_before=genome_before,
                        genome_hash_after=self.state.current_genome.content_hash,
                        detection_state=detection_state,
                        timestamp=time.time(),
                        fitness_check={
                            "retry_scheduled": True,
                            "reflection": reflection_audit,
                            "reject_reasons": list(reject_reasons),
                            "retry_reason": "semantic_overlap; "
                            + ", ".join(
                                str(value).split(":", 1)[0]
                                for value in reject_reasons
                            ),
                        },
                    ), next_case_index=idx)
                    self.state.edits_rejected += 1
                    print(f"  [{idx}] NOVELTY_RETRY {case_id}: {reject_reasons}")
                    return
                self.state.edits_rejected += 1
                rejected_entry = RejectedEditEntry(
                    proposal=RejectedEditProposal(
                        target_module=str(edit.target_module),
                        operation=(
                            edit.operation.value
                            if isinstance(edit.operation, SkillOp)
                            else str(edit.operation)
                        ),
                        content=str(edit.new_content)[:80],
                    ),
                    regressed_cases=[],
                    budget_consumed=0,
                    reject_reasons=reject_reasons,
                )
                self.rejected_buffer.append(rejected_entry)
                self.type_fitness.get_experience_memory().add_experience(
                    hallucination_type=type_result.primary_type,
                    evolution_direction=evolution_direction,
                    module_targeted=edit.target_module,
                    strategy_used=(
                        "operation:"
                        + (
                            edit.operation.value
                            if isinstance(edit.operation, SkillOp)
                            else str(edit.operation)
                        )
                    ),
                    detection_improved=False,
                    source_case_id=case_id,
                    metadata=_structured_outcome(
                        outcome="rejected",
                        stage="structural_gate",
                        operation=(
                            edit.operation.value
                            if isinstance(edit.operation, SkillOp)
                            else str(edit.operation)
                        ),
                        expected_effect_status="not_replayed",
                        reason_categories=[
                            str(value).split(":", 1)[0]
                            for value in reject_reasons
                        ],
                        decision="structural_reject",
                        layer="l1_provider_rule",
                        artifact=edit.target_module,
                        reason_code=(
                            str(reject_reasons[0]).split(":", 1)[0]
                            if reject_reasons else ""
                        ),
                    ),
                    feature_description=edit.feature_description,
                    type_confidence=type_result.confidence,
                )
                retry_categories = [
                    _structural_reject_category(reason)
                    for reason in reject_reasons
                ]
                retry_categories = [c for c in retry_categories if c]
                prior_feedback = self.mutation_retry_feedback.get(case_id, [])
                repeated_category = any(
                    f"structural:{category}" in prior_feedback
                    for category in retry_categories
                )
                retry_scheduled = (
                    self.mutation_route is not None
                    and self.active_attempt_index + 1
                    < self._attempt_cap_for(cut.reason_code)
                    and _structural_rejection_retryable(reject_reasons)
                    and not repeated_category
                )
                if retry_scheduled:
                    feedback = self.mutation_retry_feedback.setdefault(
                        case_id, []
                    )
                    for category in retry_categories:
                        marker = f"structural:{category}"
                        if marker not in feedback:
                            feedback.append(marker)
                    self.gate.defer_structural_reject(cert)
                    self.source_work_queue.appendleft(
                        (idx, case_id, self.active_attempt_index + 1)
                    )
                self.record_attempt(
                    decision=(
                        "retry_structural"
                        if retry_scheduled else "reject"
                    ),
                    operation=(
                        edit.operation.value
                        if isinstance(edit.operation, SkillOp)
                        else str(edit.operation)
                    ),
                    target_module=edit.target_module,
                    action_spec=edit.action_spec or pending_action_spec,
                    structural_result="rejected",
                    compiled=bool(edit.action_spec or pending_action_spec),
                    proposal_content=edit.new_content,
                )
                self.record_step(EvolutionStep(
                    case_id=case_id,
                    case_index=idx,
                    trigger_source=view.trigger_source,
                    attribution=signal.to_dict(),
                    edit_proposal=edit.to_dict(),
                    mutation_applied=None,
                    gate_decision=(
                        "retry_structural"
                        if retry_scheduled else Outcome.INVALID_PROPOSAL.value
                    ),
                    genome_hash_before=genome_before,
                    genome_hash_after=self.state.current_genome.content_hash,
                    detection_state=detection_state,
                    timestamp=time.time(),
                    fitness_check={
                        "retry_scheduled": retry_scheduled,
                        "reflection": reflection_audit,
                        "reject_reasons": list(reject_reasons),
                        "retry_reason": rejected_entry.feedback_summary(),
                    },
                ), next_case_index=(idx if retry_scheduled else idx + 1))
                print(f"  [{idx}] REJECT {case_id}: {reject_reasons}")
                if not retry_scheduled:
                    self.funnel.record(Outcome.INVALID_PROPOSAL)
                return

        # --- [4b] LLM no-conflict gate (T5, advisory) ---
        conflict_gate_passed = False
        conflict_gate_ran = False
        _conflict_flag: dict[str, Any] = {"passed": False, "ran": False}
        if edit.operation in (SkillOp.ADD_RULE, SkillOp.MODIFY_RULE):
            _existing_rules = list(
                self.state.current_genome.modules[edit.target_module].rules
            )
            if (
                edit.operation == SkillOp.MODIFY_RULE
                and edit.rule_index is not None
            ):
                _existing_rules = [
                    rule for i, rule in enumerate(_existing_rules)
                    if i != edit.rule_index
                ]
            (
                _cg_passed, _cg_reason, _cg_indices, _cg_ran,
            ) = _llm_no_conflict_gate(
                edit.new_content,
                _existing_rules,
                target_module=edit.target_module,
                mutation_route=self.mutation_route,
                budget_usage=self.budget_usage,
                provider_usage_ledger=self.provider_usage_ledger,
            )
            conflict_gate_ran = _cg_ran
            _conflict_flag = {
                "passed": _cg_ran and _cg_passed,
                "ran": _cg_ran,
                **(
                    {
                        "reason": _cg_reason,
                        "conflicting_rule_indices": _cg_indices,
                    }
                    if _cg_ran and not _cg_passed else {}
                ),
            }
            if _cg_ran and _cg_passed:
                conflict_gate_passed = True
            elif _cg_ran and not _cg_passed:
                print(
                    f"  [{idx}] CONFLICT_FLAGGED {case_id}: "
                    f"{_cg_reason} indices={_cg_indices}"
                )
        reflection_audit["llm_conflict_gate"] = _conflict_flag

        # --- [5] Apply edit ---
        try:
            candidate_genome = apply_edit(self.state.current_genome, edit)
            if edit.operation == "add_rule":
                _applied_rule_idx = (
                    len(
                        candidate_genome.modules[edit.target_module].rules
                    ) - 1
                )
            elif edit.rule_index is not None:
                _applied_rule_idx = edit.rule_index
            else:
                _applied_rule_idx = None
        except ValueError as e:
            print(f"  [{idx}] EDIT_ERROR {case_id}: {e}")
            self.gate.revoke_structural_accept(cert, [f"apply_edit_failed:{e}"])
            self.state.edits_rejected += 1
            self.record_attempt(
                decision="reject_apply",
                operation=(
                    edit.operation.value
                    if isinstance(edit.operation, SkillOp)
                    else str(edit.operation)
                ),
                target_module=edit.target_module,
                action_spec=edit.action_spec or pending_action_spec,
                structural_result="accepted",
                compiled=bool(edit.action_spec or pending_action_spec),
                proposal_content=edit.new_content,
            )
            self.record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source=view.trigger_source,
                attribution=signal.to_dict(),
                edit_proposal=edit.to_dict(),
                mutation_applied=None,
                gate_decision=Outcome.ACTION_NOT_DISPATCHED.value,
                genome_hash_before=genome_before,
                genome_hash_after=self.state.current_genome.content_hash,
                detection_state=detection_state,
                timestamp=time.time(),
                fitness_check={"reflection": reflection_audit},
            ), next_case_index=idx + 1)
            self.funnel.record(Outcome.ACTION_NOT_DISPATCHED)
            return

        fitness_audit: dict[str, Any] | None = None
        # Source replay is mandatory for every edit. ``fitness_check``
        # remains an interface-compatible flag controlling formal promotion
        # semantics, not permission to skip the replay envelope.
        try:
            candidate_result = self.run_detection(
                budget_kind="source",
                genome=candidate_genome,
                case_id=case_id,
                policy_genome=(
                    self.state.policy_genome
                    if self.state.policy_genome.items_for_scope("working")
                    else None
                ),
            )
        except Exception as exc:
            reason = (
                f"source_replay_runtime_error:{type(exc).__name__}:{exc}"
            )
            fitness_audit = {
                "reflection": reflection_audit,
                "source_before": signal.to_dict(),
                "source_after": None,
                "source_improved": False,
                "source_stable_after": False,
                "commit_validation": (
                    "single_source_replay_plus_sampled_canaries"
                ),
                "runtime_error": reason,
                "passed": False,
            }
            self.gate.revoke_structural_accept(cert, [reason])
            self.state.edits_rejected += 1
            self.record_attempt(
                decision="reject_fitness_runtime",
                operation=(
                    edit.operation.value
                    if isinstance(edit.operation, SkillOp)
                    else str(edit.operation)
                ),
                target_module=edit.target_module,
                action_spec=edit.action_spec or pending_action_spec,
                structural_result="accepted",
                compiled=bool(edit.action_spec or pending_action_spec),
                no_effect_reason="unresolved_no_effect",
                proposal_content=edit.new_content,
            )
            self.record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source=view.trigger_source,
                attribution=signal.to_dict(),
                edit_proposal=edit.to_dict(),
                mutation_applied=None,
                gate_decision=Outcome.PROVIDER_NOT_COMPLIED.value,
                genome_hash_before=genome_before,
                genome_hash_after=self.state.current_genome.content_hash,
                detection_state=detection_state,
                timestamp=time.time(),
                fitness_check=fitness_audit,
            ), next_case_index=idx + 1)
            self.funnel.record(Outcome.PROVIDER_NOT_COMPLIED)
            print(f"  [{idx}] REJECT_FITNESS_RUNTIME {case_id}: {reason}")
            return
        candidate_signal = attribute_failure_for_result(
            candidate_result, case_id, records=self.records,
            use_gold=self.use_gold, gold_reflection=self.gold_reflection,
        )
        # C10: the source failure was triggered by construction (this path is
        # only reachable for a REPAIRABLE episode); the replay's trigger state
        # decides whether the edit closed the failure. Threading both into
        # fitness/effect re-activates the "source failure closed" and
        # "trigger:closed/opened" channels that the loop otherwise left dead.
        source_gold_label = (
            _evaluator_gold(self.records[case_id]) if self.use_gold else None
        )
        after_trigger = _triggered(
            candidate_result, use_gold=self.use_gold,
            gold_label=source_gold_label,
        )
        assessment = assess_source_fitness(
            signal,
            candidate_signal,
            target_module=edit.target_module,
            before_result=detection_result,
            after_result=candidate_result,
            before_trigger=True,
            after_trigger=after_trigger,
            gold_label=fitness_gold_label(
                case_id, records=self.records,
                fitness_use_gold=self.fitness_use_gold,
            ),
        )
        assessment, causal_execution_audit = _attribute_intervention_fitness(
            assessment,
            edit=edit,
            result=candidate_result,
            baseline_surviving_ids=_baseline_surviving_error_certificate_ids(
                detection_result
            ),
            baseline_result=detection_result,
        )

        canary_audit: list[dict[str, Any]] = []
        canary_regressions: list[str] = []
        promotion_canaries = (
            {
                canary_id: self.stable_canaries[canary_id]
                for canary_id in self.registered_canary_ids
                if canary_id in self.stable_canaries
            }
            if self.registered_canary_ids else self.stable_canaries
        )
        if (
            (assessment.passed or assessment.evidence_only)
            and self.registered_canary_ids
            and not promotion_canaries
            and not self.fitness_use_gold
        ):
            assessment = FitnessAssessment(
                "no_effect",
                ("independent_canary_unavailable",),
                assessment.task_score_before,
                assessment.task_score_after,
                assessment.local_score_before,
                assessment.local_score_after,
            )
        if (assessment.passed or assessment.evidence_only) and promotion_canaries:
            for canary_id, parent_canary in selected_canaries(
                edit, stable_canaries=promotion_canaries,
                canary_limit=self.canary_limit,
                genome_content_hash=self.state.current_genome.content_hash,
            ):
                try:
                    child_canary = self.run_detection(
                        budget_kind="canary",
                        genome=candidate_genome,
                        case_id=canary_id,
                        policy_genome=(
                            self.state.policy_genome
                            if self.state.policy_genome.items_for_scope(
                                "working"
                            )
                            else None
                        ),
                    )
                    preserved, audit = canary_preserved(
                        parent_canary, child_canary,
                        use_gold=self.use_gold,
                        gold_label=(
                            _evaluator_gold(self.records[canary_id])
                            if self.use_gold else None
                        ),
                    )
                    canary_audit.append({"case_id": canary_id, **audit})
                    if not preserved:
                        canary_regressions.append(canary_id)
                except Exception as exc:
                    canary_regressions.append(canary_id)
                    canary_audit.append({
                        "case_id": canary_id,
                        "preserved": False,
                        "error": f"{type(exc).__name__}:{exc}",
                    })
        fitness_audit = {
            "reflection": reflection_audit,
            "source_before": signal.to_dict(),
            "source_after": candidate_signal.to_dict(),
            "assessment": assessment.to_dict(),
            "causal_execution": causal_execution_audit,
            "source_stable_after": not _triggered(
                        candidate_result, use_gold=self.use_gold,
                        gold_label=(
                            _evaluator_gold(self.records[case_id])
                            if self.use_gold else None
                        ),
                    ),
            "compiled_rule_trace": (
                candidate_result.get("compiled_rule_trace")
                if isinstance(candidate_result, Mapping) else None
            ),
            "injected_rule_trace": (
                candidate_result.get("injected_rule_trace")
                if isinstance(candidate_result, Mapping) else None
            ),
            "applied_rule_trace": (
                candidate_result.get("applied_rule_trace")
                if isinstance(candidate_result, Mapping) else None
            ),
            "rule_effects_trace": (
                candidate_result.get("rule_effects_trace")
                if isinstance(candidate_result, Mapping) else None
            ),
            "no_effect_classification": (
                _classify_replay_no_effect(
                    detection_result, candidate_result,
                )
                if assessment.decision == "no_effect" else None
            ),
            "commit_validation": (
                "single_source_replay_plus_sampled_canaries"
            ),
            "source_replays": 1,
            "configured_canary_count": len(self.registered_canary_ids),
            "stable_registered_canary_count": len(promotion_canaries),
            "canary_cases": len(canary_audit),
            "probe_cases": 0,
            "provider_calls": 1 + len(canary_audit),
            "rollback_reason": "",
            "passed": assessment.passed,
            "canary_audit": canary_audit,
            "canary_regressions": canary_regressions,
        }
        if canary_regressions:
            assessment = FitnessAssessment(
                "worse",
                ("stable_canary_regression:" + ",".join(canary_regressions),),
                assessment.task_score_before,
                assessment.task_score_after,
                assessment.local_score_before,
                assessment.local_score_after,
            )
            fitness_audit["assessment"] = assessment.to_dict()
            fitness_audit["passed"] = False
        effect_audit = _expected_effect_audit(
            edit,
            assessment,
            before=signal,
            after=candidate_signal,
            before_result=detection_result,
            after_result=candidate_result,
            before_trigger=True,
            after_trigger=after_trigger,
        )
        fitness_audit["expected_effect_contract"] = effect_audit
        decision_before_effect_gate = assessment.decision
        assessment = _enforce_expected_effect_observation(
            assessment,
            effect_audit,
            str(causal_execution_audit.get("status") or ""),
        )
        fitness_audit["assessment"] = assessment.to_dict()
        fitness_audit["passed"] = assessment.passed
        if assessment.decision != decision_before_effect_gate:
            fitness_audit["no_effect_classification"] = (
                "expected_effect_not_observed"
            )
        shadow_admissible = _shadow_commit_eligible(
            assessment.decision,
            fitness_use_gold=self.fitness_use_gold,
            canary_audits=canary_audit,
        )
        if shadow_admissible:
            if assessment.decision == "directional_evidence":
                fitness_audit["directional_evidence_canary_preserved"] = True
            fitness_audit["shadow_admissible"] = True
        # Contrastive replay acceptance (overfitting guard).
        contrast_audit: dict[str, Any] | None = None
        if shadow_admissible and not canary_regressions:
            neighbor = _select_contrast_neighbor(
                self.contrast_replay_registry.get(
                    type_result.primary_type.value, ()
                ),
                case_id=case_id,
            )
            if neighbor is not None:
                neighbor_id = str(neighbor.get("case_id") or "")
                neighbor_before_state = str(neighbor.get("state") or "")
                if not neighbor_id or not neighbor_before_state:
                    neighbor = None
            if neighbor is not None:
                try:
                    neighbor_result = self.run_detection(
                        budget_kind="contrast",
                        genome=candidate_genome,
                        case_id=neighbor_id,
                        policy_genome=(
                            self.state.policy_genome
                            if self.state.policy_genome.items_for_scope(
                                "working"
                            )
                            else None
                        ),
                    )
                    neighbor_after_state = str(
                        neighbor_result.get("state") or ""
                    )
                    inverted = _contrast_inversion(
                        before_state=neighbor_before_state,
                        after_state=neighbor_after_state,
                        gold_label=fitness_gold_label(
                            neighbor_id, records=self.records,
                            fitness_use_gold=self.fitness_use_gold,
                        ),
                    )
                    contrast_audit = {
                        "neighbor_case_id": neighbor_id,
                        "state_before": neighbor_before_state,
                        "state_after": neighbor_after_state,
                        "inverted": inverted,
                    }
                except Exception as exc:
                    inverted = False
                    self.budget_usage["contrast_replay_errors"] += 1
                    contrast_audit = {
                        "neighbor_case_id": neighbor_id,
                        "state_before": neighbor_before_state,
                        "state_after": None,
                        "inverted": False,
                        "runtime_error": f"{type(exc).__name__}:{exc}",
                    }
                fitness_audit["contrast_replay"] = contrast_audit
                fitness_audit["provider_calls"] += 1
                fitness_audit["contrast_replays"] = 1
                if inverted:
                    assessment = FitnessAssessment(
                        "worse",
                        (f"contrast_neighbor_inversion:{neighbor_id}",),
                        assessment.task_score_before,
                        assessment.task_score_after,
                        assessment.local_score_before,
                        assessment.local_score_after,
                    )
                    fitness_audit["assessment"] = assessment.to_dict()
                    fitness_audit["passed"] = False
                    fitness_audit["shadow_admissible"] = False
                    shadow_admissible = False
                    print(
                        f"  [{idx}] CONTRAST_INVERSION {case_id}: "
                        f"neighbour {neighbor_id} flipped "
                        f"{neighbor_before_state}->{neighbor_after_state}"
                    )
        fitness_audit["llm_conflict_gate"] = (
            dict(reflection_audit["llm_conflict_gate"])
            if isinstance(
                reflection_audit.get("llm_conflict_gate"), Mapping
            )
            else {
                "passed": conflict_gate_passed,
                "ran": conflict_gate_ran,
            }
        )
        if not shadow_admissible:
            typed_assessment = self.type_fitness.assess_edit(
                source_case_id=case_id,
                hallucination_type=type_result.primary_type,
                evolution_direction=evolution_direction,
                detection_before=detection_result,
                detection_after=candidate_result,
                type_result=type_result,
                target_module=edit.target_module,
                override_success=False,
                feature_description=edit.feature_description,
                attempt_metadata=_structured_outcome(
                    outcome=outcome_class_for(assessment.decision),
                    stage="source_replay",
                    operation=(
                        edit.operation.value
                        if isinstance(edit.operation, SkillOp)
                        else str(edit.operation)
                    ),
                    expected_effect_status=effect_audit["status"],
                    reason_categories=[
                        str(value).split(":", 1)[0]
                        for value in assessment.reasons
                    ],
                    observable_deltas=effect_audit["observable_deltas"],
                    promotable=False,
                    decision=assessment.decision,
                    layer="l1_provider_rule",
                    artifact=edit.target_module,
                    reason_code=(
                        str(assessment.reasons[0]).split(":", 1)[0]
                        if assessment.reasons else ""
                    ),
                ),
            )
            fitness_audit["htc_tes_typed_fitness"] = (
                typed_assessment.to_dict()
            )
            if assessment.decision != "no_effect":
                # C9: a complete repair attempt (executed + measured + no
                # acceptable improvement) is convergence evidence.
                self.gate.record_no_edit()
                reasons = [
                    f"source_{assessment.decision}:"
                    + ",".join(assessment.reasons)
                ]
                self.gate.revoke_structural_accept(cert, reasons)
                self.state.edits_rejected += 1
                injected_flag, applied_flag = _injected_applied_flags(
                    edit, candidate_result
                )
                state_after = str(getattr(candidate_signal, "state", ""))
                self.record_attempt(
                    decision="reject_fitness",
                    operation=(
                        edit.operation.value
                        if isinstance(edit.operation, SkillOp)
                        else str(edit.operation)
                    ),
                    target_module=edit.target_module,
                    action_spec=edit.action_spec or pending_action_spec,
                    structural_result="accepted",
                    compiled=bool(edit.action_spec or pending_action_spec),
                    injected=injected_flag,
                    applied=applied_flag,
                    observable_delta=effect_audit["observable_deltas"],
                    downstream_court_delta=(
                        (
                            f"state:{signal.state}->{state_after}",
                        )
                        if state_after and state_after != signal.state
                        else ()
                    ),
                    proposal_content=edit.new_content,
                )
                self.rejected_buffer.append(RejectedEditEntry(
                    proposal=RejectedEditProposal(
                        target_module=str(edit.target_module),
                        operation=(
                            edit.operation.value
                            if isinstance(edit.operation, SkillOp)
                            else str(edit.operation)
                        ),
                        content=str(edit.new_content)[:80],
                    ),
                    regressed_cases=[],
                    budget_consumed=1,
                    reject_reasons=reasons,
                    rejection_stage=(
                        "canary_regression"
                        if canary_regressions else "source_fitness"
                    ),
                    observed_effect=effect_audit["status"],
                    observable_deltas=tuple(
                        effect_audit["observable_deltas"]
                    ),
                ))
                outcome = (
                    Outcome.CANARY_REGRESSION
                    if canary_regressions
                    else Outcome.SOURCE_NOT_IMPROVED
                )
                self.record_step(EvolutionStep(
                    case_id=case_id,
                    case_index=idx,
                    trigger_source=view.trigger_source,
                    attribution=signal.to_dict(),
                    edit_proposal=edit.to_dict(),
                    mutation_applied=None,
                    gate_decision=outcome.value,
                    genome_hash_before=genome_before,
                    genome_hash_after=self.state.current_genome.content_hash,
                    detection_state=detection_state,
                    timestamp=time.time(),
                    fitness_check=fitness_audit,
                ), next_case_index=idx + 1)
                print(f"  [{idx}] REJECT_LOCAL_FITNESS {case_id}: {reasons}")
                self.funnel.record(outcome)
                return
            # no_effect: retry when budget allows, else terminal.
            reasons = ["source_no_effect:" + ",".join(assessment.reasons)]
            self.gate.revoke_structural_accept(cert, reasons)
            self.state.edits_rejected += 1
            rejected_entry = RejectedEditEntry(
                proposal=RejectedEditProposal(
                    target_module=str(edit.target_module),
                    operation=(
                        edit.operation.value
                        if isinstance(edit.operation, SkillOp)
                        else str(edit.operation)
                    ),
                    content=str(edit.new_content)[:80],
                ),
                regressed_cases=[],
                budget_consumed=1,
                reject_reasons=reasons,
                rejection_stage="source_fitness",
                observed_effect=effect_audit["status"],
                observable_deltas=tuple(
                    effect_audit["observable_deltas"]
                ),
            )
            self.rejected_buffer.append(rejected_entry)
            self.rejected_source_proposals.setdefault(case_id, []).append({
                "target_module": edit.target_module,
                "operation": (
                    edit.operation.value
                    if isinstance(edit.operation, SkillOp)
                    else str(edit.operation)
                ),
                "action_operation": str(
                    (edit.action_spec or {}).get("operation") or ""
                ),
                "content": edit.new_content,
                "content_hash": hashlib.sha256(
                    edit.new_content.encode("utf-8")
                ).hexdigest()[:12],
                "observable_deltas": list(
                    effect_audit["observable_deltas"]
                ),
                "no_effect_classification": fitness_audit.get(
                    "no_effect_classification"
                ),
            })
            retry_scheduled = (
                self.mutation_route is not None
                and self.active_attempt_index + 1
                < self._attempt_cap_for(cut.reason_code)
            )
            if retry_scheduled:
                self.source_work_queue.appendleft(
                    (idx, case_id, self.active_attempt_index + 1)
                )
            else:
                self.gate.record_no_edit()
            injected_flag, applied_flag = _injected_applied_flags(
                edit, candidate_result
            )
            classification = str(
                fitness_audit.get("no_effect_classification") or ""
            )
            self.record_attempt(
                decision=(
                    "retry_fitness_no_effect"
                    if retry_scheduled
                    else "reject_fitness_no_effect"
                ),
                operation=(
                    edit.operation.value
                    if isinstance(edit.operation, SkillOp)
                    else str(edit.operation)
                ),
                target_module=edit.target_module,
                action_spec=edit.action_spec or pending_action_spec,
                structural_result="accepted",
                compiled=bool(edit.action_spec or pending_action_spec),
                injected=injected_flag,
                applied=applied_flag,
                observable_delta=effect_audit["observable_deltas"],
                no_effect_reason=_no_effect_reason_for(
                    classification,
                    injected_flag,
                    applied_flag,
                    str(causal_execution_audit.get("status") or ""),
                ),
                proposal_content=edit.new_content,
            )
            fitness_audit["retry_scheduled"] = retry_scheduled
            fitness_audit["retry_reason"] = rejected_entry.feedback_summary()
            self.record_step(EvolutionStep(
                case_id=case_id, case_index=idx,
                trigger_source=view.trigger_source,
                attribution=signal.to_dict(), edit_proposal=edit.to_dict(),
                mutation_applied=None,
                gate_decision=(
                    "retry_fitness_no_effect"
                    if retry_scheduled else Outcome.SOURCE_NOT_IMPROVED.value
                ),
                genome_hash_before=genome_before,
                genome_hash_after=self.state.current_genome.content_hash,
                detection_state=detection_state, timestamp=time.time(),
                fitness_check=fitness_audit,
            ), next_case_index=(idx if retry_scheduled else idx + 1))
            print(f"  [{idx}] REJECT_LOCAL_FITNESS {case_id}: {reasons}")
            if not retry_scheduled:
                self.funnel.record(Outcome.SOURCE_NOT_IMPROVED)
            return

        if not self.fitness_check:
            # Non-formal diagnostic compatibility path. Formal CLI runs
            # require fitness_check=True.
            if not self.fitness_use_gold:
                self.gate.revoke_structural_accept(
                    cert,
                    ["label_free_candidate_is_telemetry_only"],
                )
                self.state.edits_rejected += 1
                self.state.current_genome = self.state.champion_genome
                self.record_step(EvolutionStep(
                    case_id=case_id,
                    case_index=idx,
                    trigger_source=view.trigger_source,
                    attribution=signal.to_dict(),
                    edit_proposal=edit.to_dict(),
                    mutation_applied=None,
                    gate_decision="provisional_no_gold_telemetry",
                    genome_hash_before=genome_before,
                    genome_hash_after=self.state.champion_genome.content_hash,
                    detection_state=detection_state,
                    timestamp=time.time(),
                    fitness_check={
                        "reflection": reflection_audit,
                        "passed": False,
                        "promotion_eligible": False,
                        "claim_scope": "label_free_provisional_telemetry",
                        "rollback_reason": "no_independent_correctness_authority",
                    },
                ), next_case_index=idx + 1)
                self.funnel.record(Outcome.SOURCE_NOT_IMPROVED)
                return
            self.state.champion_genome = candidate_genome
            self.state.current_genome = candidate_genome
            self.state.edits_accepted += 1
            self.accepted_edits_history.append({
                "target_module": edit.target_module,
                "operation": edit.operation,
                "new_content": edit.new_content,
                "case_id": case_id,
                "batch_id": "fitness_disabled",
            })
            self.record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source=view.trigger_source,
                attribution=signal.to_dict(),
                edit_proposal=edit.to_dict(),
                mutation_applied=edit.to_dict(),
                gate_decision=Outcome.ACCEPTED.value,
                genome_hash_before=genome_before,
                genome_hash_after=self.state.current_genome.content_hash,
                detection_state=detection_state,
                timestamp=time.time(),
                fitness_check=None,
            ), next_case_index=idx + 1)
            self.funnel.record(Outcome.ACCEPTED)
            return

        # Admit locally useful edits to a reversible shadow genome. Champion
        # is unchanged until flush_shadow_batch commits the batch.
        self.state.current_genome = candidate_genome
        step_index = len(self.steps)
        self.provisional_edits.append(ProvisionalEdit(
            case_id=case_id,
            case_index=idx,
            edit=edit,
            certificate=cert,
            applied_rule_idx=_applied_rule_idx,
            source_signal=signal,
            source_result=detection_result,
            source_after_signal=candidate_signal,
            source_after_result=candidate_result,
            individual_assessment=assessment,
            step_index=step_index,
            canary_audits=canary_audit,
            conflict_gate_passed=conflict_gate_passed,
        ))
        self.record_step(EvolutionStep(
            case_id=case_id,
            case_index=idx,
            trigger_source=view.trigger_source,
            attribution=signal.to_dict(),
            edit_proposal=edit.to_dict(),
            mutation_applied=None,
            gate_decision="provisional",
            genome_hash_before=genome_before,
            genome_hash_after=self.state.current_genome.content_hash,
            detection_state=detection_state,
            timestamp=time.time(),
            fitness_check=fitness_audit,
        ), next_case_index=idx + 1)
        print(
            f"  [{idx}] PROVISIONAL {case_id}: "
            f"{edit.target_module}/{edit.operation} "
            f"({assessment.decision}; shadow={len(self.provisional_edits)})"
        )
        self.flush_shadow_batch(next_case_index=idx + 1)

        # v5.8 mechanism 3: rung 1 of the procedural abstention ladder.
        if _abstention_ladder_rung_complete(
            target_module=edit.target_module,
            before_state=str(signal.state),
            after_state=str(candidate_signal.state),
            gold_label=signal.gold_label,
            ladder_used=case_id in self.abstention_ladder_used,
            assessment_passed=assessment.passed,
        ):
            self.abstention_ladder_used.add(case_id)
            self.abstention_ladder_advances.append({
                "case_id": case_id,
                "rung": "abstention_demotion_clean_to_unresolved",
            })
            self.source_work_queue.append((idx, case_id, 0))
            print(
                f"  [{idx}] [LADDER] {case_id}: rung 1 complete "
                f"(verified_clean->unresolved); source re-queued for the "
                f"evidence_obligation rung"
            )

    def _finalize(self) -> OnlineEvolutionResult:
        """Final summary, Meta aggregation and result save."""
        self.flush_shadow_batch(
            next_case_index=len(self.evolution_case_ids),
            force=True,
        )
        if self.pending_probe_commits and self.registered_probe_ids:
            self.run_development_probe(reason="final_pending_candidate")
            self.persist_progress(len(self.evolution_case_ids))
        duration = time.time() - self.start_time
        final_gate_summary = self.gate.summary()

        # --- Meta Review aggregation (Phase 8 cross-case strategy learner) ---
        # Deterministic, prompt-safe and never touching the hard gate:
        # aggregate the audited gate events into GateExperience rows, derive
        # the verified outcome signals from the mechanism memory, enumerate
        # shadow StrategyEdit candidates, build a reporting-only drift
        # snapshot, and persist the strategy configuration back into the
        # single meta_review authority.
        gate_event_ledger = _build_gate_event_ledger(self.steps)
        aggregated_experiences = [
            aggregate_gate_experience(event, sequence=event.sequence)
            for event in gate_event_ledger.events
        ]
        gate_experiences = [
            experience.to_dict()
            for experience in aggregated_experiences
        ]
        verified_outcomes = list(self.causal_memory.entries())
        meta_candidates = generate_strategy_suggestions(
            aggregated_experiences,
            verified_outcomes,
            self.state.strategy_state,
        )
        drift_snapshot = build_drift_snapshot(
            aggregated_experiences,
            verified_outcomes,
            registry_size=(
                len(self.state.policy_genome.registry.all_predicate_names())
                + len(
                    self.state.policy_genome.registry.all_action_instance_names()
                )
            ),
        )
        committed_meta_state, meta_commit_audit = (
            commit_validated_strategy_edits(
                self.state.strategy_state,
                meta_candidates,
                aggregated_experiences,
                verified_outcomes,
            )
        )
        meta_edits_committed = [
            row for row in meta_commit_audit if row.get("committed")
        ]
        self.causal_memory.meta_review["gate_experiences"] = gate_experiences
        self.causal_memory.meta_review["meta_state"] = (
            committed_meta_state.to_dict()
        )
        self.causal_memory.meta_review["meta_commit_audit"] = meta_commit_audit
        self.causal_memory.meta_review["drift_snapshots"] = [
            drift_snapshot.to_dict()
        ]
        meta_review_summary = {
            "gate_experience_count": len(gate_experiences),
            "shadow_candidate_count": len(meta_candidates),
            "shadow_candidates": [
                edit.to_dict() for edit in meta_candidates
            ],
            "committed_edit_count": len(meta_edits_committed),
            "commit_audit": meta_commit_audit,
            "meta_state_hash": committed_meta_state.content_hash,
            "meta_state_hash_before": self.state.strategy_state.content_hash,
            "meta_generation": committed_meta_state.generation,
            "drift": drift_snapshot.to_dict(),
        }
        if meta_edits_committed:
            print(
                f"  [META] committed {len(meta_edits_committed)} meta "
                f"edit(s); generation "
                f"{self.state.strategy_state.generation} -> "
                f"{committed_meta_state.generation}"
            )
        self.state.strategy_state = committed_meta_state

        result = _build_evolution_result(
            run_id=self.run_id,
            champion_genome=self.state.champion_genome,
            policy_genome=self.state.policy_genome,
            steps=self.steps,
            final_gate_summary=final_gate_summary,
            evolution_case_ids=self.evolution_case_ids,
            triggered_cases=self.state.triggered_cases,
            funnel=self.funnel,
            use_gold=self.use_gold,
            gate=self.gate,
            duration=duration,
            budget_usage=self.budget_usage,
            localization_bias_signals=self.localization_bias_signals,
            non_l1_failure_attributions=self.non_l1_failure_attributions,
            l2_policy_audit=self.l2_policy_audit,
            l2_policy_accepts=self.state.l2_policy_accepts,
            l2_registry_registrations=self.l2_registry_registrations,
            abstention_ladder_advances=self.abstention_ladder_advances,
            rollback_reason=self.state.rollback_reason,
            probe_history=self.probe_history,
            validation_probe_every=self.validation_probe_every,
            validation_probe_trigger_threshold=(
                self.validation_probe_trigger_threshold
            ),
            registered_probe_ids=self.registered_probe_ids,
            probe_limit=self.probe_limit,
            canary_limit=self.canary_limit,
            max_mutation_attempts_per_case=self.max_mutation_attempts_per_case,
            registered_canary_ids=self.registered_canary_ids,
            baseline_canary_audit=self.baseline_canary_audit,
            runtime_error_categories=self.runtime_error_categories,
            meta_review_summary=meta_review_summary,
            provider_usage_ledger=self.provider_usage_ledger,
        )

        _save_evolution_results(
            output_dir=self.output_dir, result=result,
            champion_genome=self.state.champion_genome,
            policy_genome=self.state.policy_genome,
            causal_memory=self.causal_memory,
            provider_usage_ledger=self.provider_usage_ledger,
            evolution_case_ids=self.evolution_case_ids,
            run_id=self.run_id,
            duration=duration,
            triggered_cases=self.state.triggered_cases,
            stable_cases=self.state.stable_cases,
            edits_accepted=self.state.edits_accepted,
            edits_rejected=self.state.edits_rejected,
            gate=self.gate,
            registered_canary_ids=self.registered_canary_ids,
            baseline_canary_audit=self.baseline_canary_audit,
            type_coverage=(
                self.type_fitness.get_experience_memory().get_type_coverage()
            ),
            persist_progress=self.persist_progress,
        )
        return result


def run_online_evolution(
    *,
    evolution_case_ids: Sequence[str],
    records: Mapping[str, dict[str, Any]],
    config: dict[str, Any],
    output_dir: Path,
    use_gold: bool = False,
    mutation_route: dict[str, Any] | None = None,
    module_cooldown: int = 0,
    max_total_edits: int = 200,
    convergence_window: int = 20,
    fitness_check: bool = True,
    fitness_batch_size: int = 1,
    fitness_use_gold: bool = False,
    experience_memory_path: Path | None = None,
    canary_limit: int = 2,
    canary_case_ids: Sequence[str] = (),
    validation_probe_every: int = 0,
    probe_ids: Sequence[str] = (),
    probe_limit: int = 2,
    validation_probe_trigger_threshold: int = 0,
    max_mutation_attempts_per_case: int = 2,
    initial_genome: SkillGenome | None = None,
    initial_policy_genome: PolicyGenome | None = None,
    pro_mutation_route: dict[str, Any] | None = None,
    run_id: str | None = None,
    resume_from: Path | None = None,
    gold_reflection: bool = False,
    probe_experience_record: bool = False,
    reflection_candidate_count: int = 1,
) -> OnlineEvolutionResult:
    """Run per-case online evolution over the single RepairPlan pipeline.

    Batch commit requires per-case source replay and preservation of a
    rolling set of previously stable development cases. The separately
    registered gold probe is opened only by its budgeted gate; the final
    holdout remains unopened during evolution and is the authoritative
    cross-set check. Self-supervised signals may advance only an
    unprotected provisional working candidate after independent canary
    non-regression. Protected promotion and accuracy claims require
    explicit evaluation authority outside this run.
    """
    loop = EvolutionLoop(
        evolution_case_ids=evolution_case_ids,
        records=records,
        config=config,
        output_dir=output_dir,
        use_gold=use_gold,
        mutation_route=mutation_route,
        module_cooldown=module_cooldown,
        max_total_edits=max_total_edits,
        convergence_window=convergence_window,
        fitness_check=fitness_check,
        fitness_batch_size=fitness_batch_size,
        fitness_use_gold=fitness_use_gold,
        experience_memory_path=experience_memory_path,
        canary_limit=canary_limit,
        canary_case_ids=canary_case_ids,
        validation_probe_every=validation_probe_every,
        probe_ids=probe_ids,
        probe_limit=probe_limit,
        validation_probe_trigger_threshold=validation_probe_trigger_threshold,
        max_mutation_attempts_per_case=max_mutation_attempts_per_case,
        initial_genome=initial_genome,
        initial_policy_genome=initial_policy_genome,
        pro_mutation_route=pro_mutation_route,
        run_id=run_id,
        resume_from=resume_from,
        gold_reflection=gold_reflection,
        probe_experience_record=probe_experience_record,
        reflection_candidate_count=reflection_candidate_count,
    )
    loop._initialize_state()
    loop.run()
    return loop._finalize()


