"""SEHD-RISE loop development-probe commit and certification.

The reversible shadow batch admits edits as ``PendingProbeCommit`` anchors
so an independent development probe can veto them before they harden. The
probe replays the registered probe cases against the champion genome plus
the working policy surface, scores each against gold when available, and
rolls the whole batch back on any regression. Certification classifies the
probe outcome change per case (improved / unchanged / regressed) and the
batch as a whole (certified / neutral / regressed) so the experience record
carries a fail-closed, auditable label.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sehd_rise.causal.signals import (
    decision_authority_invalid,
    resolve_evolution_state,
)
from sehd_rise.evaluation.fitness import task_state_score
from sehd_rise.evaluation.gate import GateCertificate
from sehd_rise.loop.machine import Outcome
from sehd_rise.loop.state import RunState
from sehd_rise.meta.strategy import StrategyState, create_baseline_strategy
from sehd_rise.platform.gold import _evaluator_gold
from sehd_rise.skills.genome import SkillEdit, SkillGenome
from sehd_rise.typing.classifier import HallucinationType


@dataclass
class PendingProbeCommit:
    """Minimal pending edit state needed to resume probe rollback."""

    case_id: str
    case_index: int
    edit: SkillEdit
    certificate: GateCertificate
    step_index: int


# Development-probe certification (opt-in experience record)
PROBE_CERTIFICATION_LABELS = ("certified", "neutral", "regressed")


def _probe_case_delta(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> str:
    """Classify one probe case outcome change: improved/unchanged/regressed.

    Scored (gold) probes compare ``task_score``; label-free probes can only
    compare runtime validity, so a state flip without scores is recorded as
    unchanged rather than guessed as an improvement.
    """
    previous_score = previous.get("task_score")
    current_score = current.get("task_score")
    if isinstance(previous_score, int) and isinstance(current_score, int):
        if current_score < previous_score:
            return "regressed"
        if current_score > previous_score:
            return "improved"
        return "unchanged"
    previous_valid = previous.get("runtime_valid") is True
    current_valid = current.get("runtime_valid") is True
    if previous_valid and not current_valid:
        return "regressed"
    if not previous_valid and current_valid:
        return "improved"
    return "unchanged"


def _probe_certification(
    current_audits: Sequence[Mapping[str, Any]],
    previous_audits: Sequence[Mapping[str, Any]] | None,
) -> str:
    """Fail-closed certification of pending edits by a passing probe.

    - no prior probe baseline          -> neutral (nothing to certify against)
    - any probe case regressed         -> regressed
    - no regression, >=1 improvement   -> certified
    - all unchanged                    -> neutral

    The returned label must belong to ``PROBE_CERTIFICATION_LABELS``; the
    closed vocabulary is the schema contract for audit artifacts and the
    opt-in experience record.
    """
    label = _probe_certification_label(current_audits, previous_audits)
    if label not in PROBE_CERTIFICATION_LABELS:
        raise ValueError(
            f"probe certification produced unknown label: {label!r}"
        )
    return label


def _probe_certification_label(
    current_audits: Sequence[Mapping[str, Any]],
    previous_audits: Sequence[Mapping[str, Any]] | None,
) -> str:
    if not previous_audits:
        return "neutral"
    previous_by_case = {
        str(entry.get("case_id")): entry for entry in previous_audits
    }
    deltas: list[str] = []
    for case_audit in current_audits:
        previous = previous_by_case.get(str(case_audit.get("case_id")))
        if previous is None:
            # A case with no baseline cannot certify an improvement.
            deltas.append("unchanged")
        else:
            deltas.append(_probe_case_delta(previous, case_audit))
    if "regressed" in deltas:
        return "regressed"
    if "improved" in deltas:
        return "certified"
    return "neutral"


def _run_development_probe_impl(
    *, reason: str, state: RunState,
    budget_usage: dict[str, int], gate: Any, causal_memory: Any,
    records: Mapping[str, dict[str, Any]],
    probe_history: list[dict[str, Any]], registered_probe_ids: Sequence[str],
    run_detection: Any, steps: list[Any],
    accepted_edits_history: list[dict[str, Any]],
    pending_probe_commits: list[PendingProbeCommit],
    pending_probe_step_indexes: list[int],
    stable_canaries: dict[str, dict[str, Any]],
    probe_experience_record: bool, type_fitness: Any,
) -> tuple[bool, dict[str, Any]]:
    """Apply the independent development envelope as a veto-only gate."""
    before_budget = dict(budget_usage)
    # Convergence is restored as a whole when the probe fails, so capture
    # the probe-start snapshot for the synchronized rollback.
    probe_convergence_snapshot = gate.snapshot_convergence()
    audit: dict[str, Any] = {
        "probe_index": len(probe_history),
        "reason": reason,
        "genome_hash": state.champion_genome.content_hash,
        "case_audits": [],
        "passed": True,
        "rollback_reason": "",
        "promotion_effect": "continue_only_not_final_champion",
    }
    failures: list[str] = []
    for probe_id in registered_probe_ids:
        gold = _evaluator_gold(records[probe_id])
        try:
            result = run_detection(
                budget_kind="probe",
                genome=state.champion_genome,
                case_id=probe_id,
                # The probe must measure the detector that subsequent cases
                # actually run: champion genome plus the accepted L2 policy
                # surface (working scope).
                policy_genome=(
                    state.policy_genome
                    if state.policy_genome.items_for_scope("working")
                    else None
                ),
            )
            state_evolution = resolve_evolution_state(result)
            runtime_valid = result.get("runtime_valid") is True
            authority_invalid = decision_authority_invalid(result)
            if gold is None:
                # Unlabelled probe mode is deliberately process-only: it can
                # veto runtime/authority loss, but it cannot score a
                # clean/error polarity or provide correctness evidence.
                score = None
                passed = runtime_valid and not authority_invalid
            else:
                score = task_state_score(state_evolution, gold)
                passed = runtime_valid and not authority_invalid and score == 2
            audit["case_audits"].append({
                "case_id": probe_id,
                "state": state_evolution,
                "gold_correct": (passed if gold is not None else None),
                "task_score": score,
                "runtime_valid": runtime_valid,
                "probe_mode": (
                    "gold_correctness_veto"
                    if gold is not None
                    else "label_free_process_veto"
                ),
            })
            if not passed:
                failures.append(
                    f"{probe_id}:"
                    + ("gold_mismatch" if gold is not None else "runtime_or_authority_invalid")
                )
        except Exception as exc:
            # The attempt and provider call were debited before dispatch.
            error = f"{probe_id}:{type(exc).__name__}:{exc}"
            failures.append(error)
            audit["case_audits"].append({
                "case_id": probe_id,
                "state": "runtime_invalid",
                "gold_correct": False,
                "runtime_valid": False,
                "error": error,
            })

    audit["source_replays"] = 0
    audit["canary_cases"] = 0
    audit["probe_cases"] = (
        budget_usage["probe_cases"] - before_budget["probe_cases"]
    )
    audit["provider_calls"] = (
        budget_usage["provider_calls"] - before_budget["provider_calls"]
    )
    state.last_probe_triggered_count = state.triggered_cases

    if failures:
        state.rollback_reason = "development_probe_failed:" + "|".join(failures)
        audit["passed"] = False
        audit["rollback_reason"] = state.rollback_reason
        rolled_back = len(pending_probe_commits)
        audit["rolled_back_edits"] = rolled_back
        state.champion_genome = state.stable_genome.clone()
        state.current_genome = state.stable_genome.clone()
        state.policy_genome = state.stable_policy_genome.clone()
        state.l2_policy_accepts = state.stable_l2_policy_accepts
        causal_memory.from_dict(state.stable_memory_payload)
        # Synchronized rollback: the strategy configuration rolls back with
        # the experience memory that is its single authority, so the gate
        # profile and guidance never drift ahead of the genomes they were
        # validated against.
        persisted_meta = causal_memory.meta_review.get("meta_state") or {}
        if persisted_meta:
            try:
                state.strategy_state = StrategyState.from_dict(persisted_meta)
            except ValueError:
                state.strategy_state = create_baseline_strategy()
        stable_canaries.clear()
        stable_canaries.update(state.stable_canaries_checkpoint)
        for item in pending_probe_commits:
            gate.revoke_structural_accept(
                item.certificate,
                [state.rollback_reason],
                # Delayed batch rollback: the per-certificate accept-time
                # convergence snapshot is stale across the intervening cases,
                # so the counters are restored from the probe-time snapshot
                # below instead of being replayed per certificate.
                restore_streak=False,
            )
            step = steps[item.step_index]
            step.gate_decision = Outcome.CANARY_REGRESSION.value
            step.mutation_applied = None
            step.genome_hash_after = state.stable_genome.content_hash
            step.fitness_check = {
                **(step.fitness_check or {}),
                "development_probe": audit,
            }
        # Synchronized convergence restore: the probe is rolled back as a
        # whole, so the streak is the probe-start snapshot (never the sum of
        # per-certificate priors, which would erase or inflate the
        # intervening no-edit counting).
        gate.restore_convergence(probe_convergence_snapshot)
        state.edits_accepted -= rolled_back
        state.edits_rejected += rolled_back
        del accepted_edits_history[state.stable_history_length:]
        pending_probe_commits.clear()
        pending_probe_step_indexes.clear()
        state.last_probe_accepted_count = state.edits_accepted
    else:
        state.rollback_reason = ""
        audit["rolled_back_edits"] = 0
        state.stable_genome = state.champion_genome.clone()
        state.stable_policy_genome = state.policy_genome.clone()
        state.stable_l2_policy_accepts = state.l2_policy_accepts
        state.stable_history_length = len(accepted_edits_history)
        # Fail-closed probe certification. Compared against the previous probe
        # baseline; the first probe has no baseline and stays neutral. The
        # label is auditable from the step record and (opt-in) persisted to the
        # experience memory.
        previous_probe_audits = (
            probe_history[-1].get("case_audits") if probe_history else None
        )
        certification = _probe_certification(
            audit["case_audits"], previous_probe_audits
        )
        audit["probe_certification"] = certification
        # The probe is the independent development validation of the edits it
        # covers; the outcome validation rows let the Meta review consume
        # probe evidence (certified -> supports_accept, regressed ->
        # supports_reject) instead of running on gate events alone.
        audit["validated_edit_ids"] = [
            item.edit.edit_id for item in pending_probe_commits
        ]
        if probe_experience_record:
            probe_case_ids = [
                str(case_audit.get("case_id"))
                for case_audit in audit["case_audits"]
            ]
            # Written before the stable-memory snapshot so a certified edit's
            # record survives together with the edit itself.
            for item in pending_probe_commits:
                type_fitness.get_experience_memory().add_experience(
                    hallucination_type=HallucinationType.UNKNOWN,
                    evolution_direction="probe_certification",
                    module_targeted=item.edit.target_module,
                    strategy_used=f"probe_certification:{certification}",
                    detection_improved=certification == "certified",
                    source_case_id=item.case_id,
                    metadata={
                        "probe_certification": certification,
                        "probe_index": audit["probe_index"],
                        "probe_case_ids": probe_case_ids,
                    },
                )
        state.stable_memory_payload = causal_memory.to_dict()
        state.stable_canaries_checkpoint = dict(stable_canaries)
        for item in pending_probe_commits:
            step = steps[item.step_index]
            step.fitness_check = {
                **(step.fitness_check or {}),
                "development_probe": audit,
            }
        pending_probe_commits.clear()
        pending_probe_step_indexes.clear()
        state.last_probe_accepted_count = state.edits_accepted

    probe_history.append(audit)
    return not failures, audit
