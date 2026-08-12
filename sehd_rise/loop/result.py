"""SEHD-RISE loop step and run records.

Per-case evolution steps, the complete run result and the shadow
provisional-edit record. Every case terminal is classified through the
§2.9 funnel (``machine.Outcome``): ``EvolutionStep.gate_decision`` carries
the outcome the case produced at that step, and ``_build_evolution_result``
derives the run counters from the run funnel instead of a flat skip
vocabulary (C8).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sehd_rise.causal.signals import DetectionSignal
from sehd_rise.evaluation.fitness import FitnessAssessment
from sehd_rise.loop.machine import Outcome, OutcomeFunnel
from sehd_rise.planning.feedback import _build_rejection_summary
from sehd_rise.platform.usage import ProviderUsageLedger
from sehd_rise.skills.genome import SkillEdit, SkillGenome
from sehd_rise.skills.policy import PolicyGenome


@dataclass
class EvolutionStep:
    """Record of one evolution step for a single case.

    ``gate_decision`` holds the §2.9 outcome for terminal steps and an
    interim marker (for example ``provisional``) for steps that keep the
    case walking; the closed outcome vocabulary lives in ``machine.Outcome``.
    """

    case_id: str
    case_index: int
    trigger_source: str  # what triggered reflection (or "none")
    attribution: dict[str, Any] | None
    edit_proposal: dict[str, Any] | None
    mutation_applied: dict[str, Any] | None
    gate_decision: str  # a machine.Outcome value, or an interim marker
    genome_hash_before: str
    genome_hash_after: str
    detection_state: str
    timestamp: float
    fitness_check: dict[str, Any] | None = None
    attempt_index: int = 0
    episode: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_index": self.case_index,
            "trigger_source": self.trigger_source,
            "attribution": self.attribution,
            "edit_proposal": self.edit_proposal,
            "mutation_applied": self.mutation_applied,
            "gate_decision": self.gate_decision,
            "genome_hash_before": self.genome_hash_before,
            "genome_hash_after": self.genome_hash_after,
            "detection_state": self.detection_state,
            "timestamp": self.timestamp,
            "fitness_check": self.fitness_check,
            "attempt_index": self.attempt_index,
            "episode": self.episode,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvolutionStep":
        """Reconstruct an EvolutionStep from a persisted dict."""
        return cls(
            case_id=str(data.get("case_id") or ""),
            case_index=int(data.get("case_index") or 0),
            trigger_source=str(data.get("trigger_source") or "none"),
            attribution=dict(data.get("attribution") or {})
            if data.get("attribution") is not None
            else None,
            edit_proposal=dict(data.get("edit_proposal") or {})
            if data.get("edit_proposal") is not None
            else None,
            mutation_applied=dict(data.get("mutation_applied") or {})
            if data.get("mutation_applied") is not None
            else None,
            gate_decision=str(data.get("gate_decision") or ""),
            genome_hash_before=str(data.get("genome_hash_before") or ""),
            genome_hash_after=str(data.get("genome_hash_after") or ""),
            detection_state=str(data.get("detection_state") or ""),
            timestamp=float(data.get("timestamp") or 0.0),
            fitness_check=dict(data.get("fitness_check") or {})
            if data.get("fitness_check") is not None
            else None,
            attempt_index=int(data.get("attempt_index") or 0),
            episode=dict(data.get("episode") or {})
            if data.get("episode") is not None
            else None,
        )


@dataclass
class OnlineEvolutionResult:
    """Complete result of one online evolution run."""

    run_id: str
    champion_genome: SkillGenome
    policy_genome: PolicyGenome
    steps: list[EvolutionStep]
    gate_summary: dict[str, Any]
    total_cases: int
    triggered_cases: int
    edits_accepted: int
    edits_rejected: int
    stable_cases: int
    # runtime_invalid 是已处理但检测运行时失败的 case（diagnostic_only 遥测，
    # 不计入 triggered/stable）。暴露在顶层使 processed = triggered + stable
    # + runtime_invalid 可被外部审计对账。
    runtime_invalid_cases: int
    use_gold: bool
    halted: bool
    halt_reason: str
    duration_seconds: float
    evolution_audit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "sehd_rise.loop.online_evolution.v1",
            "run_id": self.run_id,
            "champion_genome": self.champion_genome.to_dict(),
            "policy_genome": self.policy_genome.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "gate_summary": self.gate_summary,
            "total_cases": self.total_cases,
            "triggered_cases": self.triggered_cases,
            "edits_accepted": self.edits_accepted,
            "edits_rejected": self.edits_rejected,
            "stable_cases": self.stable_cases,
            "runtime_invalid_cases": self.runtime_invalid_cases,
            "supervision_mode": "gold_enhanced" if self.use_gold else "self_supervised",
            "use_gold": self.use_gold,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "duration_seconds": self.duration_seconds,
            "evolution_audit": self.evolution_audit,
        }


@dataclass
class ProvisionalEdit:
    """One reversible edit admitted to the current shadow genome."""

    case_id: str
    case_index: int
    edit: SkillEdit
    certificate: Any
    source_signal: DetectionSignal
    source_result: dict[str, Any]
    source_after_signal: DetectionSignal
    source_after_result: dict[str, Any]
    individual_assessment: FitnessAssessment
    step_index: int
    applied_rule_idx: int | None = None  # rule index at apply time
    canary_audits: list[dict[str, Any]] = field(default_factory=list)
    # True only when the LLM no-conflict gate was actually consulted AND
    # returned "pass"; a skipped gate stays False so the provisional path
    # cannot fire without an explicit conflict check.
    conflict_gate_passed: bool = False


def _build_evolution_result(
    *,
    run_id: str,
    champion_genome: SkillGenome,
    policy_genome: PolicyGenome,
    steps: list[EvolutionStep],
    final_gate_summary: dict[str, Any],
    evolution_case_ids: Sequence[str],
    triggered_cases: int,
    funnel: OutcomeFunnel,
    use_gold: bool,
    gate: Any,
    duration: float,
    budget_usage: dict[str, int],
    localization_bias_signals: list[dict[str, str]],
    non_l1_failure_attributions: list[dict[str, Any]],
    l2_policy_audit: list[dict[str, Any]],
    l2_policy_accepts: int,
    l2_registry_registrations: list[dict[str, Any]],
    abstention_ladder_advances: list[dict[str, str]],
    rollback_reason: str,
    probe_history: list[dict[str, Any]],
    validation_probe_every: int,
    validation_probe_trigger_threshold: int,
    registered_probe_ids: tuple[str, ...],
    probe_limit: int,
    canary_limit: int,
    max_mutation_attempts_per_case: int,
    registered_canary_ids: Sequence[str],
    baseline_canary_audit: list[dict[str, Any]],
    runtime_error_categories: dict[str, int],
    meta_review_summary: dict[str, Any],
    provider_usage_ledger: ProviderUsageLedger,
) -> OnlineEvolutionResult:
    """Assemble the final run result from run state and the outcome funnel.

    Counts derive from ``funnel`` so every number in the result traces to a
    §2.9 layer terminal: accepted and rejected edits come from the fitness
    layer, unaddressable stays on its own line (C2), and the audit embeds
    the funnel JSON (C8).
    """
    return OnlineEvolutionResult(
        run_id=run_id,
        champion_genome=champion_genome,
        policy_genome=policy_genome,
        steps=steps,
        gate_summary=final_gate_summary,
        total_cases=len(evolution_case_ids),
        triggered_cases=triggered_cases,
        edits_accepted=funnel.accepted(),
        edits_rejected=(
            funnel.count(Outcome.SOURCE_NOT_IMPROVED)
            + funnel.count(Outcome.CANARY_REGRESSION)
            + funnel.count(Outcome.TRANSFER_REGRESSION)
        ),
        stable_cases=funnel.count(Outcome.NON_EVOLVABLE),
        runtime_invalid_cases=funnel.count(Outcome.INFRA_FAILURE),
        use_gold=use_gold,
        # A convergence-terminated run stopped early just like an edit-cap
        # halt; the result must say so instead of silently reporting
        # halted=False with an empty reason.
        halted=gate.is_halted or gate.converged,
        halt_reason=(
            gate.halt_reason
            if gate.is_halted
            else ("converged" if gate.converged else "")
        ),
        duration_seconds=duration,
        evolution_audit={
            **budget_usage,
            "total_provider_calls": (
                budget_usage["detector_calls"] + budget_usage["mutation_calls"]
            ),
            "outcome_funnel": funnel.to_dict(),
            "localization_bias_signals": localization_bias_signals,
            "non_l1_failure_attributions": non_l1_failure_attributions,
            "l2_policy_audit": l2_policy_audit,
            "l2_policy_accepts": l2_policy_accepts,
            "l2_registry_registrations": l2_registry_registrations,
            "policy_genome": policy_genome.to_dict(),
            "abstention_ladder_advances": abstention_ladder_advances,
            "rollback_reason": rollback_reason,
            "probe_history": probe_history,
            "validation_probe_every": validation_probe_every,
            "validation_probe_trigger_threshold": (
                validation_probe_trigger_threshold
            ),
            "probe_ids": list(registered_probe_ids),
            "probe_limit": probe_limit,
            "canary_limit": canary_limit,
            "max_mutation_attempts_per_case": max_mutation_attempts_per_case,
            "canary_case_ids": list(registered_canary_ids),
            "baseline_canary_audit": baseline_canary_audit,
            "rejection_summary": _build_rejection_summary(
                final_gate_summary, steps,
            ),
            "final_champion_claim": False,
            "result_scope": "development_candidate_not_final_holdout_champion",
            "label_free_accuracy_claim": False,
            "no_gold_promotion_allowed": False,
            "runtime_error_categories": dict(runtime_error_categories),
            "runtime_invalid_claim_scope": (
                "diagnostic_only_not_evolution_evidence"
            ),
            "meta_review": meta_review_summary,
            "provider_usage_totals": provider_usage_ledger.totals(),
        },
    )
