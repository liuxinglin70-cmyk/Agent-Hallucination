"""SEHD-RISE loop reversible shadow-batch commit and rollback.

The reversible working genome (``current_genome``) admits edits
provisionally; ``_flush_shadow_batch_impl`` commits the eligible subset to
the champion genome and rolls the rest back individually, so a single
regressing edit no longer discards sibling fixes. ``_shadow_commit_eligible``
is the single admission predicate shared by the main loop and the flush so
an edit is never admitted only to be rolled back. The structured-outcome
and stable-canary helpers live here because the shadow batch is their only
consumer; the closed outcome/stage vocabularies are defined once in this
module.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from sehd_rise.causal.signals import resolve_evolution_state
from sehd_rise.evaluation.effect import (
    _classify_replay_no_effect,
    _expected_effect_audit,
    _experience_intervention_trace,
    _no_effect_reason_for,
)
from sehd_rise.execution.trace import _injected_applied_flags
from sehd_rise.loop.machine import Outcome
from sehd_rise.loop.probes import PendingProbeCommit
from sehd_rise.loop.result import ProvisionalEdit
from sehd_rise.loop.state import RunState
from sehd_rise.platform.jsonio import atomic_write_json
from sehd_rise.planning.l1 import RejectedEditEntry, RejectedEditProposal
from sehd_rise.skills.action import sanitize_action_spec
from sehd_rise.skills.genome import SkillOp, apply_edit
from sehd_rise.skills.policy import (
    FITNESS_STATUS_NONE,
    FITNESS_STATUS_STRONG,
    FITNESS_STATUS_WEAK,
)


def _evict_stable_canaries(
    stable_canaries: dict[str, dict[str, Any]], canary_limit: int,
) -> None:
    """Bound the stable-canary pool with FIFO eviction.

    The bound is deliberate, not a retention strategy: selection re-ranks the
    surviving anchors by (genome, edit) hash at selection time, so the
    eviction order cannot bias which anchors a regression check sees.
    """
    while len(stable_canaries) > max(8, canary_limit * 4):
        stable_canaries.pop(next(iter(stable_canaries)))


# The stage at which an attempt stopped. Free-form strings previously made
# experience memory unqueryable; the vocabulary below is closed so that
# reflection can aggregate over it safely.
OUTCOME_STAGES = frozenset({
    "mutation",            # the mutator declined or returned an invalid edit
    "structural_gate",     # the structural constraint gate rejected the edit
    "source_replay",       # the edit was replayed on its own source case
    "batch_commit",        # the edit was evaluated in the shadow batch
    "detection_runtime",   # the detector never produced a verdict
    "l2_policy_replay",    # an adjudication-policy candidate was replayed
})

# Whether the attempt produced usable evidence, and of what strength.
OUTCOME_CLASSES = frozenset({
    "improved",             # promotable improvement (gold-assisted)
    "directional",          # process moved, correctness unproven
    "no_effect",            # measurably nothing changed
    "regressed",            # measurably worse
    "rejected",             # blocked before any replay evidence existed
    "not_attempted",        # no edit was proposed
    "runtime_invalid",      # execution failed; observation only
})

# Maps a FitnessAssessment.decision onto the closed outcome vocabulary.
_ASSESSMENT_OUTCOME_CLASS = {
    "strong_improvement": "improved",
    "local_improvement": "improved",
    "directional_evidence": "directional",
    "no_effect": "no_effect",
    "worse": "regressed",
}

# Outcomes that may be cited as evidence that an edit actually helped.
_PROMOTABLE_OUTCOME_CLASSES = frozenset({"improved"})


def outcome_class_for(decision: str) -> str:
    """Map a fitness decision onto the closed outcomes vocabulary."""
    return _ASSESSMENT_OUTCOME_CLASS.get(str(decision), "rejected")


def _structured_outcome(
    *,
    outcome: str,
    stage: str,
    operation: str,
    expected_effect_status: str,
    reason_categories: Sequence[str] = (),
    observable_deltas: Sequence[str] = (),
    promotable: bool | None = None,
    diagnostic_only: bool = False,
    decision: str = "",
    layer: str = "",
    artifact: str = "",
    reason_code: str = "",
    action_spec: Mapping[str, Any] | None = None,
    intervention_trace: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the structured outcome payload stored in experience memory.

    Every field is drawn from a closed vocabulary or is already
    category-reduced, so the payload can be aggregated, filtered, and safely
    re-entered into a reflection prompt. ``layer`` / ``artifact`` /
    ``reason_code`` carry the deterministic failure attribution so
    experiences are retrievable by failure signature, not only by
    hallucination type.
    """
    outcome_class = outcome if outcome in OUTCOME_CLASSES else "rejected"
    if promotable is None:
        promotable = outcome_class in _PROMOTABLE_OUTCOME_CLASSES
    payload = {
        "outcome": outcome_class,
        "outcome_stage": stage if stage in OUTCOME_STAGES else "mutation",
        "outcome_decision": str(decision or outcome_class),
        "operation": operation,
        "reason_categories": [str(value) for value in reason_categories][:5],
        "observable_deltas": [str(value) for value in observable_deltas][:8],
        "expected_effect_status": expected_effect_status,
        # Deterministic attribution signature: the artifact-first identity
        # of this experience for retrieval and deduplication.
        "layer": layer,
        "artifact": artifact,
        "reason_code": reason_code,
        # Explicit claim scope: label-free/diagnostic evidence must never be
        # read back as proof that an edit improved detection.
        "promotable_evidence": bool(promotable) and not diagnostic_only,
        "diagnostic_only": bool(diagnostic_only),
        "schema_version": "agenthallu.bccse_experience_outcome.v2",
    }
    canonical_action_spec = sanitize_action_spec(action_spec)
    if canonical_action_spec:
        payload["action_spec"] = canonical_action_spec
    if intervention_trace:
        applied_rule_ids = sorted({
            str(rule_id)
            for rule_id in intervention_trace.get("applied_rule_ids", ())
            if str(rule_id)
        })
        output_fields = sorted({
            str(field)
            for field in intervention_trace.get("output_fields", ())
            if str(field)
        })
        routes = sorted({
            str(route)
            for route in intervention_trace.get("routes", ())
            if str(route)
        })
        payload["intervention_trace"] = {
            "applied_rule_ids": applied_rule_ids,
            "output_fields": output_fields,
            "routes": routes,
        }
    return payload


def _shadow_commit_eligible(
    decision: str,
    *,
    fitness_use_gold: bool,
    canary_audits: Sequence[Mapping[str, Any]],
) -> bool:
    """Single admission/commit predicate for the reversible shadow batch.

    Label-free directional evidence may advance the reversible working
    genome only when an independent sampled canary proves process
    non-regression. It remains explicitly provisional: the commit never
    grants it protected fitness status (protection is reserved for
    gold-assisted edits) and the run result keeps final_champion_claim=False
    until an external authority scores it. Independent canaries protect
    known behavior; they do not establish that a newly decisive verdict is
    correct. The main loop and flush must share this predicate, or an edit
    is admitted only to be rolled back — wasting the replay and counting a
    no-edit against the convergence window.
    """
    if decision in {"strong_improvement", "local_improvement"}:
        return True
    if fitness_use_gold:
        return decision == "directional_evidence"
    return (
        decision == "directional_evidence"
        and bool(canary_audits)
        and all(
            bool(audit.get("preserved")) for audit in canary_audits
        )
    )


def _flush_shadow_batch_impl(
    *, next_case_index: int, force: bool = False,
    state: RunState, provisional_edits: list[ProvisionalEdit],
    fitness_batch_size: int, batch_history: list[dict[str, Any]],
    fitness_use_gold: bool, steps: list[Any], gate: Any,
    budget_usage: dict[str, int], accepted_edits_history: list[dict[str, Any]],
    pending_probe_commits: list[PendingProbeCommit],
    pending_probe_step_indexes: list[int],
    stable_canaries: dict[str, dict[str, Any]], canary_limit: int,
    rejected_buffer: list[RejectedEditEntry], output_dir: Path,
    type_classifier: Any, type_evolution: Any, type_fitness: Any,
    registered_probe_ids: Sequence[str], validation_probe_every: int,
    validation_probe_trigger_threshold: int,
    run_development_probe: Any, record_attempt: Any,
    persist_progress: Any,
) -> bool:
    """Commit or roll back the current reversible shadow batch."""

    if not provisional_edits:
        return True
    if not force and len(provisional_edits) < fitness_batch_size:
        return True

    batch_id = f"batch_{len(batch_history):03d}"
    source_audits = [
        {
            "case_id": item.case_id,
            "edit_id": item.edit.edit_id,
            "assessment": item.individual_assessment.to_dict(),
            "source_after": item.source_after_signal.to_dict(),
            "shadow_result": item.source_after_result,
        }
        for item in provisional_edits
    ]
    batch_canary_audits = [
        audit
        for item in provisional_edits
        for audit in item.canary_audits
    ]
    runtime_errors: list[str] = []

    decisions = [
        value["assessment"]["decision"] for value in source_audits
    ]
    improved_count = sum(
        value in {"strong_improvement", "local_improvement"}
        for value in decisions
    )
    worse_count = sum(value == "worse" for value in decisions)
    # Incremental commit: each edit is validated independently. Edits that are
    # worse are isolated and rejected; non-worse edits (improved or
    # no_effect) are committed. This avoids rolling back the entire batch on
    # a single worse edit, which would discard per-case FIXED edits
    # (especially FN fixes).
    # modify_rule relaxation: modify_rule is no longer held to the strict
    # required_improvements threshold. As long as worse_count == 0 across the
    # batch, all non-worse edits (add or modify) are committed.
    # Rationale: only an explicit source improvement is promotable. A
    # no-effect edit is retained as telemetry, not silently committed.
    accepted_indices: list[int] = []
    rejected_indices: list[int] = []
    for i, dec in enumerate(decisions):
        if _shadow_commit_eligible(
            dec,
            fitness_use_gold=fitness_use_gold,
            canary_audits=provisional_edits[i].canary_audits,
        ):
            accepted_indices.append(i)
        else:
            rejected_indices.append(i)
    # Commit policy:
    # - If any source_audit missing: reject entire batch
    # - Otherwise: commit accepted_indices, reject rejected_indices.
    # Runtime errors discovered during the commit pass (typed-fitness /
    # experience telemetry) are appended to runtime_errors below and surface
    # in the batch audit and rejected-entry rejection_stage; they cannot
    # influence this pre-commit decision because they do not exist yet.
    if not accepted_indices:
        passed = False
    else:
        passed = True
    batch_audit = {
        "batch_id": batch_id,
        "provisional_edit_count": len(provisional_edits),
        "champion_hash_before": state.champion_genome.content_hash,
        "shadow_hash": state.current_genome.content_hash,
        "source_audits": source_audits,
        "stable_canary_audits": batch_canary_audits,
        "runtime_errors": runtime_errors,
        "improved_count": improved_count,
        "worse_count": worse_count,
        "accepted_indices": accepted_indices,
        "rejected_indices": rejected_indices,
        "fitness_use_gold": fitness_use_gold,
        "commit_validation": "single_source_replay_plus_sampled_canaries",
        "source_replays": len(source_audits),
        "canary_cases": len(batch_canary_audits),
        "probe_cases": 0,
        "provider_calls": (
            len(source_audits)
            + len(batch_canary_audits)
            + sum(
                int(
                    bool(
                        (steps[item.step_index].fitness_check or {}).get(
                            "contrast_replays"
                        )
                    )
                )
                for item in provisional_edits
            )
        ),
        "rollback_reason": "",
        "promotion_eligible": bool(fitness_use_gold),
        "claim_scope": (
            "gold_development_candidate"
            if fitness_use_gold
            else "label_free_provisional_telemetry"
        ),
        "passed": passed,
    }

    # Incremental commit: commit accepted edits, reject worse edits. The
    # champion genome gains only the accepted edits; rejected edits are
    # rolled back individually without discarding the accepted ones.
    if accepted_indices:
        # Rebuild champion genome from accepted edits only. current_genome
        # already contains all provisional edits applied; partial unapply is
        # not supported, so rebuild from champion + accepted.
        new_champion = state.champion_genome
        # Iterate over a copy to avoid the remove-during-iteration hazard.
        failed_indices: list[int] = []
        for idx in list(accepted_indices):
            item = provisional_edits[idx]
            try:
                new_champion = apply_edit(new_champion, item.edit)
            except ValueError:
                # Re-apply failed (a protected rule state changed), so this
                # edit is rejected instead of committed.
                failed_indices.append(idx)
                continue
            # Re-capture rule index on the new champion.
            if item.edit.operation == "add_rule":
                item.applied_rule_idx = (
                    len(new_champion.modules[item.edit.target_module].rules) - 1
                )
        # Move failed re-applies from accepted to rejected.
        for idx in failed_indices:
            accepted_indices.remove(idx)
            rejected_indices.append(idx)
        state.champion_genome = new_champion
        state.current_genome = new_champion
        state.edits_accepted += len(accepted_indices)
        for idx in accepted_indices:
            item = provisional_edits[idx]
            assessment_decision = (
                source_audits[idx]["assessment"]["decision"]
                if idx < len(source_audits) else ""
            )
            # Fitness protection is earned only by gold-assisted replay
            # evidence. A label-free provisional accept commits to the
            # development champion as telemetry but stays unprotected
            # (fitness_status=none): without an external correctness
            # authority it must remain modifiable and removable by later
            # evolution, and the promotion_status marker alone records
            # whether a live conflict gate vouched for it.
            _is_provisional_accept = (
                not fitness_use_gold
                and assessment_decision == "directional_evidence"
                and bool(item.conflict_gate_passed)
            )
            if not fitness_use_gold:
                fitness_status = FITNESS_STATUS_NONE
            else:
                fitness_status = (
                    FITNESS_STATUS_STRONG
                    if assessment_decision == "strong_improvement"
                    else FITNESS_STATUS_WEAK
                )
            _promotion_status = (
                "provisional_accept"
                if _is_provisional_accept
                else (
                    "provisional_label_free"
                    if not fitness_use_gold
                    else "gold_development_candidate"
                )
            )
            mod = state.champion_genome.modules[item.edit.target_module]
            rule_idx = item.applied_rule_idx
            if rule_idx is None and item.edit.rule_index is not None:
                rule_idx = item.edit.rule_index
            if rule_idx is not None and rule_idx < len(mod.rules):
                meta = mod.get_rule_meta(rule_idx)
                meta["fitness_status"] = fitness_status
                meta["promotion_status"] = _promotion_status
                mod.set_rule_meta(rule_idx, meta)
            accepted_edits_history.append({
                "edit_id": item.edit.edit_id,
                "target_module": item.edit.target_module,
                "operation": item.edit.operation,
                "new_content": item.edit.new_content,
                "case_id": item.case_id,
                "batch_id": batch_id,
                "promotion_status": _promotion_status,
            })
            pending_probe_commits.append(PendingProbeCommit(
                case_id=item.case_id,
                case_index=item.case_index,
                edit=item.edit,
                certificate=item.certificate,
                step_index=item.step_index,
            ))
            pending_probe_step_indexes.append(item.step_index)
            # Record the committed type-conditioned experience from the
            # authoritative source result and its shadow replay.
            _type_result = type_classifier.classify(
                item.source_result, item.source_signal
            )
            _direction = type_evolution._infer_evolution_direction(
                item.source_signal.state, item.source_signal.gold_label,
            )
            _htc_success = assessment_decision in {
                "strong_improvement", "local_improvement",
            }
            # A label-free source replay cannot self-certify correctness,
            # even when the conflict gate agrees with the proposal.
            _override_success = _htc_success
            _shadow_result = source_audits[idx].get("shadow_result", {})
            if not isinstance(_shadow_result, Mapping):
                # A committed edit must have a shadow replay result; a
                # corrupt audit row must never crash the whole run after
                # the champion was already advanced.
                _shadow_result = {}
            _effect_audit = _expected_effect_audit(
                item.edit,
                item.individual_assessment,
                before=item.source_signal,
                after=item.source_after_signal,
                before_result=item.source_result,
                after_result=_shadow_result,
            )
            try:
                typed_assessment = type_fitness.assess_edit(
                    source_case_id=item.case_id,
                    hallucination_type=_type_result.primary_type,
                    evolution_direction=_direction,
                    detection_before=item.source_result,
                    detection_after=_shadow_result,
                    type_result=_type_result,
                    target_module=item.edit.target_module,
                    override_success=_override_success,
                    feature_description=item.edit.feature_description,
                    attempt_metadata=_structured_outcome(
                        outcome=outcome_class_for(
                            item.individual_assessment.decision
                        ),
                        stage="batch_commit",
                        operation=(
                            item.edit.operation.value
                            if isinstance(item.edit.operation, SkillOp)
                            else str(item.edit.operation)
                        ),
                        expected_effect_status=_effect_audit["status"],
                        reason_categories=[
                            str(value).split(":", 1)[0]
                            for value in item.individual_assessment.reasons
                        ],
                        observable_deltas=_effect_audit["observable_deltas"],
                        # Only independent gold-assisted fitness may claim
                        # that the edited detector improved correctness.
                        promotable=(_htc_success and fitness_use_gold),
                        diagnostic_only=not fitness_use_gold,
                        decision=item.individual_assessment.decision,
                        layer="l1_provider_rule",
                        artifact=item.edit.target_module,
                        reason_code=(
                            str(item.individual_assessment.reasons[0]).split(":", 1)[0]
                            if item.individual_assessment.reasons else ""
                        ),
                        action_spec=item.edit.action_spec,
                        intervention_trace=_experience_intervention_trace(
                            _shadow_result
                        ),
                    ),
                )
            except Exception as exc:
                # The typed-fitness assessment is audit telemetry only; a
                # malformed shadow result must not abort the run after the
                # commit already happened (the rejected path already
                # tolerates this via runtime_errors).
                runtime_errors.append(
                    f"typed_fitness:{item.case_id}:{type(exc).__name__}:{exc}"
                )
                typed_assessment = None
            # Persist the typed-fitness assessment in the batch audit trail:
            # it was previously computed and silently discarded, so its
            # metrics (global fitness, DACR, decisive accuracy) were
            # unobservable in evolution results.
            if typed_assessment is not None:
                batch_audit.setdefault("typed_fitness_assessments", []).append(
                    typed_assessment.to_dict()
                )
            # A successfully repaired source becomes a future regression
            # anchor, so later edits cannot silently forget the fix.
            _source_after = source_audits[idx].get("source_after")
            if (
                resolve_evolution_state(_shadow_result)
                in {"verified_error", "verified_clean"}
                and isinstance(_source_after, Mapping)
                and not bool(_source_after.get("should_evolve"))
            ):
                stable_canaries[item.case_id] = _shadow_result
                _evict_stable_canaries(stable_canaries, canary_limit)
            step = steps[item.step_index]
            step.gate_decision = Outcome.ACCEPTED.value
            step.mutation_applied = item.edit.to_dict()
            step.genome_hash_after = state.champion_genome.content_hash
            step.fitness_check = {
                **(step.fitness_check or {}),
                "batch": batch_audit,
            }
            injected_flag, applied_flag = _injected_applied_flags(
                item.edit, _shadow_result,
            )
            state_after = str(
                getattr(item.source_after_signal, "state", "")
            )
            accepted_deltas = tuple(
                _expected_effect_audit(
                    item.edit,
                    item.individual_assessment,
                    before=item.source_signal,
                    after=item.source_after_signal,
                    before_result=item.source_result,
                    after_result=_shadow_result,
                )["observable_deltas"]
            )
            record_attempt(
                decision="accept_batch",
                operation=(
                    item.edit.operation.value
                    if isinstance(item.edit.operation, SkillOp)
                    else str(item.edit.operation)
                ),
                target_module=item.edit.target_module,
                action_spec=item.edit.action_spec,
                structural_result="accepted",
                compiled=bool(item.edit.action_spec),
                injected=injected_flag,
                applied=applied_flag,
                observable_delta=accepted_deltas,
                downstream_court_delta=(
                    (
                        f"state:{item.source_signal.state}->{state_after}",
                    )
                    if state_after and state_after != item.source_signal.state
                    else ()
                ),
                no_effect_reason=(
                    "expected_delta_observed" if accepted_deltas else ""
                ),
                proposal_content=item.edit.new_content,
            )
            step.episode = state.active_episode_payload

        # Snapshot only after fitness metadata and experience have been
        # attached, so artifacts with the same hash have identical audit
        # semantics.
        snapshot_dir = output_dir / "champion_snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = snapshot_dir / f"{state.champion_genome.content_hash[:16]}.json"
        atomic_write_json(snapshot_path, state.champion_genome.to_dict())

    if not accepted_indices:
        passed = False
        batch_audit["passed"] = False

    if rejected_indices:
        # Roll back rejected edits individually. If accepted edits were
        # committed, current_genome already points to the new champion and
        # rejected edits are simply not applied; if no accepted edits,
        # reset current_genome to champion.
        if not accepted_indices:
            state.current_genome = state.champion_genome
        state.edits_rejected += len(rejected_indices)
        for idx in rejected_indices:
            item = provisional_edits[idx]
            item_assessment = item.individual_assessment
            reasons = [
                f"source_{item_assessment.decision}:"
                + ",".join(item_assessment.reasons)
            ]
            if runtime_errors:
                reasons.append("shadow_batch_runtime_error")
            # Preserve negative learning signal as well. A batch-level canary
            # failure or a shadow replay rejection is valuable evidence for
            # avoiding the same strategy later.
            if idx < len(source_audits):
                rejected_result = source_audits[idx].get("shadow_result")
                if isinstance(rejected_result, Mapping):
                    try:
                        rejected_effect = _expected_effect_audit(
                            item.edit,
                            item_assessment,
                            before=item.source_signal,
                            after=item.source_after_signal,
                            before_result=item.source_result,
                            after_result=rejected_result,
                        )
                        rejected_type = type_classifier.classify(
                            item.source_result, item.source_signal,
                        )
                        rejected_assessment = type_fitness.assess_edit(
                            source_case_id=item.case_id,
                            hallucination_type=rejected_type.primary_type,
                            evolution_direction=type_evolution._infer_evolution_direction(
                                item.source_signal.state,
                                item.source_signal.gold_label,
                            ),
                            detection_before=item.source_result,
                            detection_after=rejected_result,
                            type_result=rejected_type,
                            target_module=item.edit.target_module,
                            override_success=False,
                            feature_description=item.edit.feature_description,
                            attempt_metadata=_structured_outcome(
                                outcome=outcome_class_for(
                                    item_assessment.decision
                                ),
                                stage="batch_commit",
                                operation=(
                                    item.edit.operation.value
                                    if isinstance(item.edit.operation, SkillOp)
                                    else str(item.edit.operation)
                                ),
                                expected_effect_status=rejected_effect[
                                    "status"
                                ],
                                reason_categories=[
                                    str(value).split(":", 1)[0]
                                    for value in item_assessment.reasons
                                ],
                                observable_deltas=rejected_effect[
                                    "observable_deltas"
                                ],
                                promotable=False,
                                decision=item_assessment.decision,
                                layer="l1_provider_rule",
                                artifact=item.edit.target_module,
                                reason_code=(
                                    str(item_assessment.reasons[0]).split(":", 1)[0]
                                    if item_assessment.reasons else ""
                                ),
                            ),
                        )
                        batch_audit.setdefault(
                            "rejected_typed_fitness_assessments", []
                        ).append(rejected_assessment.to_dict())
                    except Exception as exc:
                        runtime_errors.append(
                            f"experience:{item.case_id}:{type(exc).__name__}:{exc}"
                        )
            gate.revoke_structural_accept(item.certificate, reasons)
            # One processed case produced no effective committed edit.
            # Structural acceptance had reset convergence, so restore the
            # case-level no-edit signal exactly once after replay rejects.
            gate.record_no_edit()
            batch_effect = _expected_effect_audit(
                item.edit,
                item_assessment,
                before=item.source_signal,
                after=item.source_after_signal,
                before_result=item.source_result,
                after_result=item.source_after_result,
            )
            rejected_buffer.append(RejectedEditEntry(
                proposal=RejectedEditProposal(
                    target_module=str(item.edit.target_module),
                    operation=(
                        item.edit.operation.value
                        if isinstance(item.edit.operation, SkillOp)
                        else str(item.edit.operation)
                    ),
                    content=str(item.edit.new_content)[:80],
                ),
                regressed_cases=[],
                budget_consumed=len(source_audits),
                reject_reasons=reasons,
                rejection_stage=(
                    "batch_runtime" if runtime_errors else "source_fitness"
                ),
                observed_effect=batch_effect["status"],
                observable_deltas=tuple(
                    batch_effect["observable_deltas"]
                ),
            ))
            step = steps[item.step_index]
            step.gate_decision = Outcome.SOURCE_NOT_IMPROVED.value
            step.mutation_applied = None
            step.genome_hash_after = state.champion_genome.content_hash
            step.fitness_check = {
                **(step.fitness_check or {}),
                "batch": batch_audit,
            }
            rejected_flags = _injected_applied_flags(
                item.edit, item.source_after_result,
            )
            record_attempt(
                decision="rollback_batch",
                operation=(
                    item.edit.operation.value
                    if isinstance(item.edit.operation, SkillOp)
                    else str(item.edit.operation)
                ),
                target_module=item.edit.target_module,
                action_spec=item.edit.action_spec,
                structural_result="accepted",
                compiled=bool(item.edit.action_spec),
                injected=rejected_flags[0],
                applied=rejected_flags[1],
                observable_delta=tuple(
                    batch_effect["observable_deltas"]
                ),
                no_effect_reason=(
                    _no_effect_reason_for(
                        _classify_replay_no_effect(
                            item.source_result, item.source_after_result,
                        ),
                        rejected_flags[0],
                        rejected_flags[1],
                    )
                    if item.individual_assessment.decision == "no_effect"
                    else ""
                ),
                proposal_content=item.edit.new_content,
            )
            step.episode = state.active_episode_payload

    probe_due_reason = ""
    if accepted_indices and registered_probe_ids:
        if (
            validation_probe_every > 0
            and state.edits_accepted - state.last_probe_accepted_count
            >= validation_probe_every
        ):
            probe_due_reason = "accepted_edit_cadence"
        elif (
            validation_probe_trigger_threshold > 0
            and state.triggered_cases - state.last_probe_triggered_count
            >= validation_probe_trigger_threshold
        ):
            probe_due_reason = "triggered_case_threshold"
    if probe_due_reason:
        probe_passed, probe_audit = run_development_probe(
            reason=probe_due_reason,
        )
        batch_audit["probe_cases"] = probe_audit["probe_cases"]
        batch_audit["provider_calls"] += probe_audit["provider_calls"]
        batch_audit["development_probe"] = probe_audit
        if not probe_passed:
            passed = False
            batch_audit["passed"] = False
            batch_audit["rollback_reason"] = probe_audit["rollback_reason"]
            batch_audit["probe_vetoed_indices"] = list(accepted_indices)
            accepted_indices = []
    batch_audit["champion_hash_after"] = state.champion_genome.content_hash
    if accepted_indices and not rejected_indices:
        print(
            f"  [{batch_id}] COMMIT {len(accepted_indices)} edits "
            f"→ {state.champion_genome.content_hash[:12]}"
        )
    elif accepted_indices and rejected_indices:
        print(
            f"  [{batch_id}] PARTIAL COMMIT {len(accepted_indices)} edits, "
            f"REJECT {len(rejected_indices)} worse edits "
            f"→ {state.champion_genome.content_hash[:12]}"
        )
    else:
        rollback_count = int(
            (batch_audit.get("development_probe") or {}).get(
                "rolled_back_edits", len(rejected_indices),
            )
        )
        print(
            f"  [{batch_id}] ROLLBACK {rollback_count} edits: "
            f"improved={improved_count}, worse={worse_count}, "
            f"runtime_errors={len(runtime_errors)}"
        )

    batch_history.append(batch_audit)
    provisional_edits.clear()
    persist_progress(next_case_index)
    return passed
