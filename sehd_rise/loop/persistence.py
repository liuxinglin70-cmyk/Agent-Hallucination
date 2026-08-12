"""SEHD-RISE loop checkpoint persistence and resume.

The persistence contract writes the champion, shadow and experience-memory
artifacts atomically after every step and exposes the recovery path so an
interrupted run resumes without re-running processed cases or losing the
audit trail. The schema version is single-sourced here and checked on load;
a checkpoint whose schema does not match is refused, so a resume only ever
starts from a shape this code defines.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from sehd_rise.loop.ledger import (
    GateEventLedger,
    _build_gate_event_ledger,
    gate_event_id,
)
from sehd_rise.loop.probes import PendingProbeCommit
from sehd_rise.loop.result import EvolutionStep, OnlineEvolutionResult
from sehd_rise.platform.jsonio import (
    atomic_write_json,
    payload_hash,
    stable_hash,
)
from sehd_rise.platform.usage import ProviderUsageLedger
from sehd_rise.planning.l1 import EditProposal, RejectedEditEntry
from sehd_rise.skills.genome import SkillEdit, SkillGenome, SkillOp
from sehd_rise.skills.policy import PolicyGenome, _validate_item_contract

CHECKPOINT_SCHEMA_VERSION = "agenthallu.bccse_v5_checkpoint.v5"
CHECKPOINT_REQUIRED_FILES: tuple[str, ...] = (
    "evolution_checkpoint.json",
    "champion_checkpoint.json",
    "shadow_checkpoint.json",
)


def load_evolution_checkpoint(output_dir: Path) -> dict[str, Any]:
    """Load and validate a persisted evolution checkpoint.

    The PUBLIC read side of the recovery contract. Returns a typed dict
    carrying the resumable state: champion/shadow genomes, the next-case
    index, accumulated steps, accepted-edits history, budget usage, stable
    canaries, and the path to the persisted experience memory. Returns
    ``{}`` when no checkpoint exists.

    The returned dict is intended for inspection and for passing to
    ``run_online_evolution(..., resume_from=output_dir)``.
    """
    output_dir = Path(output_dir)
    checkpoint_path = output_dir / "evolution_checkpoint.json"
    if not checkpoint_path.is_file():
        return {}
    with checkpoint_path.open(encoding="utf-8") as handle:
        checkpoint = json.load(handle)
    if not isinstance(checkpoint, dict):
        raise ValueError("evolution_checkpoint.json must contain an object")
    schema = str(checkpoint.get("schema_version") or "")
    if schema != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported checkpoint schema_version: {schema!r} "
            f"(expected {CHECKPOINT_SCHEMA_VERSION!r}); the checkpoint "
            "must be re-run from a clean start"
        )
    for filename in CHECKPOINT_REQUIRED_FILES:
        path = output_dir / filename
        if not path.is_file():
            raise FileNotFoundError(
                f"checkpoint artifact missing: {path}"
            )
    next_case_index = int(checkpoint.get("next_case_index") or 0)
    evolution_case_ids = list(checkpoint.get("evolution_case_ids") or [])
    if next_case_index < 0 or next_case_index > len(evolution_case_ids):
        raise ValueError(
            f"checkpoint next_case_index out of range: {next_case_index} "
            f"for {len(evolution_case_ids)} evolution_case_ids"
        )
    input_content_hash = str(checkpoint.get("input_content_hash") or "")
    config_hash = str(checkpoint.get("config_hash") or "")
    if not input_content_hash or not config_hash:
        raise ValueError(
            "checkpoint is missing run identity (input_content_hash / "
            "config_hash); refusing to resume an unidentifiable run"
        )
    champion_path = output_dir / "champion_checkpoint.json"
    shadow_path = output_dir / "shadow_checkpoint.json"
    with champion_path.open(encoding="utf-8") as handle:
        champion_payload = json.load(handle)
    with shadow_path.open(encoding="utf-8") as handle:
        shadow_payload = json.load(handle)
    if not isinstance(champion_payload, dict) or not isinstance(shadow_payload, dict):
        raise ValueError("genome checkpoint artifacts must contain objects")
    champion_checkpoint_genome = SkillGenome.from_dict(champion_payload)
    shadow_checkpoint_genome = SkillGenome.from_dict(shadow_payload)
    expected_champion_hash = str(checkpoint.get("champion_genome_hash") or "")
    expected_shadow_hash = str(checkpoint.get("shadow_genome_hash") or "")
    if (
        expected_champion_hash
        and champion_checkpoint_genome.content_hash != expected_champion_hash
    ):
        raise ValueError("champion checkpoint hash does not match its content")
    if (
        expected_shadow_hash
        and shadow_checkpoint_genome.content_hash != expected_shadow_hash
    ):
        raise ValueError("shadow checkpoint hash does not match its content")
    # The stable rollback anchors are validated against their content exactly
    # like the champion/shadow genomes; a mismatch refuses the resume instead
    # of silently resuming from a drifted anchor.
    stable_payload = dict(checkpoint.get("stable_genome_payload") or {})
    expected_stable_hash = str(checkpoint.get("stable_genome_hash") or "")
    if (
        expected_stable_hash
        and stable_payload
        and SkillGenome.from_dict(stable_payload).content_hash
        != expected_stable_hash
    ):
        raise ValueError(
            "stable genome checkpoint hash does not match its content"
        )
    stable_policy_payload = dict(
        checkpoint.get("stable_policy_genome_payload") or {}
    )
    expected_stable_policy_hash = str(
        checkpoint.get("stable_policy_genome_hash") or ""
    )
    if (
        expected_stable_policy_hash
        and stable_policy_payload
        and PolicyGenome.from_dict(stable_policy_payload).content_hash
        != expected_stable_policy_hash
    ):
        raise ValueError(
            "stable policy genome checkpoint hash does not match its content"
        )
    stable_memory_payload = dict(checkpoint.get("stable_memory_payload") or {})
    expected_stable_memory_hash = str(
        checkpoint.get("stable_memory_hash") or ""
    )
    if (
        expected_stable_memory_hash
        and stable_memory_payload
        and payload_hash(stable_memory_payload) != expected_stable_memory_hash
    ):
        raise ValueError(
            "stable memory checkpoint hash does not match its content"
        )
    experience_path = output_dir / "htc_tes_experience_memory.json"
    return {
        "schema_version": schema,
        "run_id": str(checkpoint.get("run_id") or ""),
        "status": str(checkpoint.get("status") or "in_progress"),
        "next_case_index": next_case_index,
        "evolution_case_ids": evolution_case_ids,
        "input_content_hash": input_content_hash,
        "config_hash": config_hash,
        "champion_genome_hash": str(checkpoint.get("champion_genome_hash") or ""),
        "shadow_genome_hash": str(checkpoint.get("shadow_genome_hash") or ""),
        "provisional_edit_count": int(checkpoint.get("provisional_edit_count") or 0),
        "champion_genome_payload": champion_payload,
        "shadow_genome_payload": shadow_payload,
        "gate_summary": dict(checkpoint.get("gate_summary") or {}),
        "experience_memory_path": (
            str(experience_path) if experience_path.is_file() else None
        ),
        "steps": list(checkpoint.get("steps") or []),
        "accepted_edits_history": list(
            checkpoint.get("accepted_edits_history") or []
        ),
        "batch_history": list(checkpoint.get("batch_history") or []),
        "probe_history": list(checkpoint.get("probe_history") or []),
        "baseline_canary_audit": list(
            checkpoint.get("baseline_canary_audit") or []
        ),
        "canary_case_ids": list(checkpoint.get("canary_case_ids") or []),
        "budget_usage": dict(checkpoint.get("budget_usage") or {}),
        "rollback_reason": str(checkpoint.get("rollback_reason") or ""),
        "pending_probe_step_indexes": list(
            checkpoint.get("pending_probe_step_indexes") or []
        ),
        "pending_probe_commits": list(
            checkpoint.get("pending_probe_commits") or []
        ),
        "stable_genome_payload": dict(
            checkpoint.get("stable_genome_payload") or {}
        ),
        "stable_genome_hash": str(checkpoint.get("stable_genome_hash") or ""),
        "stable_policy_genome_hash": str(
            checkpoint.get("stable_policy_genome_hash") or ""
        ),
        "stable_memory_hash": str(checkpoint.get("stable_memory_hash") or ""),
        "stable_canaries": dict(checkpoint.get("stable_canaries") or {}),
        "stable_canaries_checkpoint": dict(
            checkpoint.get("stable_canaries_checkpoint") or {}
        ),
        "stable_memory_payload": dict(
            checkpoint.get("stable_memory_payload") or {}
        ),
        "stable_history_length": int(
            checkpoint.get("stable_history_length") or 0
        ),
        "last_probe_accepted_count": int(
            checkpoint.get("last_probe_accepted_count") or 0
        ),
        "last_probe_triggered_count": int(
            checkpoint.get("last_probe_triggered_count") or 0
        ),
        "active_attempt_index": int(
            checkpoint.get("active_attempt_index") or 0
        ),
        "mutation_retry_feedback": dict(
            checkpoint.get("mutation_retry_feedback") or {}
        ),
        "rejected_source_proposals": dict(
            checkpoint.get("rejected_source_proposals") or {}
        ),
        "runtime_invalid_cases": int(
            checkpoint.get("runtime_invalid_cases") or 0
        ),
        "runtime_error_categories": dict(
            checkpoint.get("runtime_error_categories") or {}
        ),
        "rejected_buffer": list(checkpoint.get("rejected_buffer") or []),
        "counters": dict(checkpoint.get("counters") or {}),
        "policy_genome": dict(checkpoint.get("policy_genome") or {}),
        "l2_policy_accepts": int(checkpoint.get("l2_policy_accepts") or 0),
        "l2_policy_audit": list(checkpoint.get("l2_policy_audit") or []),
        "gate_events": list(checkpoint.get("gate_events") or []),
        "stable_policy_genome_payload": dict(
            checkpoint.get("stable_policy_genome_payload") or {}
        ),
        "stable_l2_policy_accepts": int(
            checkpoint.get("stable_l2_policy_accepts") or 0
        ),
        "abstention_ladder_used": list(
            checkpoint.get("abstention_ladder_used") or []
        ),
        "contrast_registry": dict(checkpoint.get("contrast_registry") or {}),
        "rejected_policy_summaries": list(
            checkpoint.get("rejected_policy_summaries") or []
        ),
        "localization_bias_signals": list(
            checkpoint.get("localization_bias_signals") or []
        ),
        "non_l1_failure_attributions": list(
            checkpoint.get("non_l1_failure_attributions") or []
        ),
        "updated_at": float(checkpoint.get("updated_at") or 0.0),
        "checkpoint_dir": str(output_dir),
    }


def _restore_state_from_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    champion_genome: SkillGenome,
    current_genome: SkillGenome,
    stable_genome: SkillGenome,
    experience_memory: Any,
    evolution_case_ids: Sequence[str],
) -> dict[str, Any]:
    """Restore in-memory evolution state from a loaded checkpoint.

    Mutates the supplied genomes/experience-memory in place and returns a
    dict of the remaining scalar/collection state that the caller must bind
    to the matching local variables before entering the work queue.
    """
    champion_payload = dict(checkpoint.get("champion_genome_payload") or {})
    if champion_payload:
        restored_champion = SkillGenome.from_dict(champion_payload)
        champion_genome.modules = restored_champion.modules
        champion_genome.edits_applied = restored_champion.edits_applied
        champion_genome.parent_hash = restored_champion.parent_hash
        champion_genome.generation = restored_champion.generation
        champion_genome.metadata = restored_champion.metadata
    # An uncommitted shadow genome is never restored. The reversible working
    # genome and the rollback anchor both resume from the last committed
    # champion; resurrecting an uncommitted shadow would silently advance a
    # genome whose source/canary evidence was never persisted.
    current_genome.modules = champion_genome.modules
    current_genome.edits_applied = champion_genome.edits_applied
    current_genome.parent_hash = champion_genome.parent_hash
    current_genome.generation = champion_genome.generation
    current_genome.metadata = champion_genome.metadata
    stable_payload = dict(checkpoint.get("stable_genome_payload") or {})
    restored_stable = (
        SkillGenome.from_dict(stable_payload)
        if stable_payload else champion_genome.clone()
    )
    stable_genome.modules = restored_stable.modules
    stable_genome.edits_applied = restored_stable.edits_applied
    stable_genome.parent_hash = restored_stable.parent_hash
    stable_genome.generation = restored_stable.generation
    stable_genome.metadata = restored_stable.metadata

    experience_path_str = checkpoint.get("experience_memory_path")
    if experience_path_str:
        experience_path = Path(experience_path_str)
        if experience_path.is_file():
            with experience_path.open(encoding="utf-8") as handle:
                memory_payload = json.load(handle)
            experience_memory.from_dict(memory_payload)

    return {
        "steps": list(checkpoint.get("steps") or []),
        "gate_summary": dict(checkpoint.get("gate_summary") or {}),
        "accepted_edits_history": list(
            checkpoint.get("accepted_edits_history") or []
        ),
        "batch_history": list(checkpoint.get("batch_history") or []),
        "probe_history": list(checkpoint.get("probe_history") or []),
        "baseline_canary_audit": list(
            checkpoint.get("baseline_canary_audit") or []
        ),
        "budget_usage": dict(checkpoint.get("budget_usage") or {}),
        "rollback_reason": str(checkpoint.get("rollback_reason") or ""),
        "pending_probe_step_indexes": list(
            checkpoint.get("pending_probe_step_indexes") or []
        ),
        "pending_probe_commits": list(
            checkpoint.get("pending_probe_commits") or []
        ),
        "stable_canaries": dict(checkpoint.get("stable_canaries") or {}),
        "stable_canaries_checkpoint": dict(
            checkpoint.get("stable_canaries_checkpoint") or {}
        ),
        "stable_memory_payload": dict(
            checkpoint.get("stable_memory_payload") or {}
        ),
        "stable_history_length": int(
            checkpoint.get("stable_history_length") or 0
        ),
        "last_probe_accepted_count": int(
            checkpoint.get("last_probe_accepted_count") or 0
        ),
        "last_probe_triggered_count": int(
            checkpoint.get("last_probe_triggered_count") or 0
        ),
        "active_attempt_index": int(
            checkpoint.get("active_attempt_index") or 0
        ),
        "mutation_retry_feedback": dict(
            checkpoint.get("mutation_retry_feedback") or {}
        ),
        "rejected_source_proposals": dict(
            checkpoint.get("rejected_source_proposals") or {}
        ),
        "runtime_invalid_cases": int(
            checkpoint.get("runtime_invalid_cases") or 0
        ),
        "runtime_error_categories": dict(
            checkpoint.get("runtime_error_categories") or {}
        ),
        "rejected_buffer": list(checkpoint.get("rejected_buffer") or []),
        "counters": dict(checkpoint.get("counters") or {}),
        "next_case_index": int(checkpoint.get("next_case_index") or 0),
    }


def _last_audited_step(record: Mapping[str, Any]) -> int | None:
    """The trajectory's final step in the detector's step coordinate.

    Matches ``_responsible_step`` semantics: the per-event ``step`` field
    when present (converted to int), otherwise the event position. Returns
    ``None`` when the history is absent or no numeric step is recoverable,
    so callers fail soft on malformed records.
    """
    history = record.get("history")
    if not isinstance(history, list) or not history:
        return None
    last = history[-1]
    if not isinstance(last, dict):
        return None
    value = last.get("step")
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    return len(history)


def _restore_checkpoint_state(
    *, resume_from: Path,
    evolution_case_ids: Sequence[str],
    records: Mapping[str, dict[str, Any]],
    config: dict[str, Any],
    gate, budget_usage: dict[str, int],
    stable_canaries: dict[str, dict[str, Any]],
    stable_canaries_checkpoint: dict[str, dict[str, Any]],
    rejected_buffer: list[RejectedEditEntry],
    champion_genome: SkillGenome, current_genome: SkillGenome,
    stable_genome: SkillGenome,
    policy_genome: PolicyGenome, stable_policy_genome: PolicyGenome,
    causal_memory: Any,
) -> dict[str, Any]:
    """Validate and apply a persisted evolution checkpoint.

    Resuming rebinds ~25 pieces of run state — scalar counters, per-case
    retry ledgers, the policy genome pair, and the halted-step rewind. The
    mutable containers (gate summary, budget, canary registries, rejected
    buffer) are updated in place; everything the caller must rebind to a
    local is returned in one dict so the work-queue entry point reads as a
    single state-transfer edge.
    """
    checkpoint = load_evolution_checkpoint(Path(resume_from))
    if not checkpoint:
        raise FileNotFoundError(
            f"resume_from set but no evolution_checkpoint.json found in "
            f"{resume_from}"
        )
    checkpoint_case_ids = list(checkpoint.get("evolution_case_ids") or [])
    if checkpoint_case_ids != list(evolution_case_ids):
        raise ValueError(
            "resume_from checkpoint evolution_case_ids do not match the "
            "supplied evolution_case_ids; cannot resume safely"
        )
    checkpoint_input_hash = str(checkpoint.get("input_content_hash") or "")
    checkpoint_config_hash = str(checkpoint.get("config_hash") or "")
    current_input_hash = stable_hash(
        {case_id: records[case_id] for case_id in evolution_case_ids}
    )
    current_config_hash = stable_hash(config)
    if checkpoint_input_hash != current_input_hash:
        raise ValueError(
            "resume_from checkpoint input_content_hash does not match the "
            "supplied records; cannot resume safely"
        )
    if checkpoint_config_hash != current_config_hash:
        raise ValueError(
            "resume_from checkpoint config_hash does not match the "
            "supplied detector config; cannot resume safely"
        )
    restored = _restore_state_from_checkpoint(
        checkpoint,
        champion_genome=champion_genome,
        current_genome=current_genome,
        stable_genome=stable_genome,
        experience_memory=causal_memory,
        evolution_case_ids=evolution_case_ids,
    )
    steps = [
        EvolutionStep.from_dict(step_payload)
        for step_payload in (restored.get("steps") or [])
        if isinstance(step_payload, Mapping)
    ]
    for key, value in dict(restored.get("budget_usage") or {}).items():
        if key in budget_usage and isinstance(value, (int, float)):
            budget_usage[key] = value
    gate.restore_summary(restored.get("gate_summary") or {})
    stable_canaries.update(restored.get("stable_canaries") or {})
    stable_canaries_checkpoint.update(
        restored.get("stable_canaries_checkpoint") or {}
    )
    restored_counters = dict(restored.get("counters") or {})
    restored_stable_memory = restored.get("stable_memory_payload") or {}
    stable_memory_payload = (
        dict(restored_stable_memory) if restored_stable_memory else {}
    )
    active_attempt_index = int(restored.get("active_attempt_index") or 0)
    pending_probe_step_indexes = list(
        restored.get("pending_probe_step_indexes") or []
    )
    pending_probe_commits: list[PendingProbeCommit] = []
    for pending in restored.get("pending_probe_commits") or ():
        if not isinstance(pending, Mapping):
            continue
        try:
            certificate_index = int(pending.get("certificate_index"))
            certificate = gate.certificates[certificate_index]
            pending_probe_commits.append(PendingProbeCommit(
                case_id=str(pending.get("case_id") or ""),
                case_index=int(pending.get("case_index") or 0),
                edit=SkillEdit.from_dict(dict(pending["edit"])),
                certificate=certificate,
                step_index=int(pending.get("step_index") or 0),
            ))
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            # A corrupt pending-probe anchor must not silently drop the
            # rollback bookkeeping: the champion already carries those edits,
            # so a later probe failure could never roll them back.
            raise ValueError(
                "checkpoint pending_probe_commits entry is corrupt; "
                "cannot resume safely"
            ) from exc
    for entry in restored.get("rejected_buffer") or ():
        if not isinstance(entry, Mapping):
            continue
        proposal_data = entry.get("proposal") or {}
        if not isinstance(proposal_data, Mapping):
            continue
        proposal = EditProposal(
            target_module=str(proposal_data.get("target_module") or ""),
            operation=str(proposal_data.get("operation") or ""),
            target_clause_id="",
            content=str(proposal_data.get("content") or ""),
            preconditions=(),
            rationale="",
            reflection_type="",
            source_case_id="",
        )
        rejected_buffer.append(RejectedEditEntry(
            proposal=proposal,
            regressed_cases=[
                str(value) for value in (entry.get("regressed_cases") or ())
            ],
            budget_consumed=int(entry.get("budget_consumed") or 0),
            reject_reasons=[
                str(value) for value in (entry.get("reject_reasons") or ())
            ],
            rejection_stage=str(entry.get("rejection_stage") or "structural_gate"),
            observed_effect=str(entry.get("observed_effect") or ""),
            observable_deltas=tuple(
                str(value) for value in (entry.get("observable_deltas") or ())
            ),
        ))
    restored_policy_payload = checkpoint.get("policy_genome")
    if (
        isinstance(restored_policy_payload, Mapping)
        and "items" in restored_policy_payload
    ):
        policy_genome = PolicyGenome.from_dict(dict(restored_policy_payload))
        # Restored policy items re-enter the deterministic evaluator on the
        # next detection, so a corrupt or vocabulary-drifted payload fails
        # closed here instead of silently skewing adjudication. An evolved
        # registry is restored with the genome and is the vocabulary
        # authority for its own items (mirrors validate_policy_genome_items).
        persisted_policy_hash = str(
            restored_policy_payload.get("content_hash") or ""
        )
        if (
            persisted_policy_hash
            and persisted_policy_hash != policy_genome.content_hash
        ):
            raise ValueError(
                "checkpoint policy genome hash does not match its content"
            )
        for restored_item in policy_genome.items:
            item_ok, item_reason = _validate_item_contract(
                restored_item, policy_genome.registry
            )
            if not item_ok:
                raise ValueError(
                    "checkpoint policy item "
                    f"{restored_item.item_id!r} failed the closed "
                    f"vocabulary contract: {item_reason}"
                )
    l2_policy_accepts = int(checkpoint.get("l2_policy_accepts") or 0)
    gate_event_ledger = GateEventLedger.from_list(
        checkpoint.get("gate_events") or ()
    )
    # The probe rollback anchor resumes from the last probe-stable policy
    # snapshot; checkpoints predating the snapshot field treat the restored
    # policy genome itself as the stable point.
    restored_stable_policy_payload = checkpoint.get(
        "stable_policy_genome_payload"
    )
    if (
        isinstance(restored_stable_policy_payload, Mapping)
        and restored_stable_policy_payload.get("items") is not None
    ):
        stable_policy_genome = PolicyGenome.from_dict(
            dict(restored_stable_policy_payload)
        )
        stable_l2_policy_accepts = int(
            checkpoint.get("stable_l2_policy_accepts") or 0
        )
    else:
        stable_policy_genome = policy_genome.clone()
        stable_l2_policy_accepts = l2_policy_accepts
    l2_policy_audit = [
        dict(entry) for entry in (checkpoint.get("l2_policy_audit") or ())
        if isinstance(entry, Mapping)
    ]
    resume_next_case_index = int(restored.get("next_case_index") or 0)
    # Cases skipped by a halt/convergence were persisted as placeholder
    # steps with next_case_index advanced past them, which made a resume
    # treat them as processed and drop them forever. Rewind over any
    # trailing unprocessed placeholder steps so the queue re-examines them
    # under the restored champion genome.
    rewound = 0
    while steps and (
        steps[-1].gate_decision == "halted"
        or steps[-1].detection_state == "skipped"
    ):
        skipped_step = steps.pop()
        rewound += 1
        resume_next_case_index = min(
            resume_next_case_index, skipped_step.case_index,
        )
        # The popped placeholder leaves a placeholder event in the
        # append-only ledger; the re-run records a real decision under a new
        # observation id, so the phantom event must be purged with the step
        # or the audit ledger claims a halt that never happened.
        gate_event_ledger.drop_event(gate_event_id(
            attempt_id="",
            episode_id="",
            decision_kind="",
            observation_id=(
                f"{skipped_step.case_id}:{skipped_step.case_index}:"
                f"{len(steps) + 1}"
            ),
        ))
    if rewound:
        gate.reset_convergence()
        print(
            f"[resume] rewinding {rewound} halted/skipped placeholder "
            f"step(s); work queue restarts at case index "
            f"{resume_next_case_index}"
        )
    # A step still marked provisional at checkpoint time lost its shadow
    # edit above (uncommitted shadows are never restored), so finalize the
    # audit record instead of leaving it open forever.
    for step in steps:
        if step.gate_decision == "provisional":
            step.gate_decision = "rollback_resume"
            step.mutation_applied = None
    if (
        steps
        and resume_next_case_index == steps[-1].case_index
        and steps[-1].gate_decision.startswith("retry_")
    ):
        active_attempt_index = max(
            active_attempt_index,
            steps[-1].attempt_index + 1,
        )
    if not stable_memory_payload:
        stable_memory_payload = causal_memory.to_dict()
    provisional_count = int(checkpoint.get("provisional_edit_count") or 0)
    shadow_hash = str(checkpoint.get("shadow_genome_hash") or "")
    champion_hash = str(checkpoint.get("champion_genome_hash") or "")
    if provisional_count > 0 or (shadow_hash and shadow_hash != champion_hash):
        print(
            f"[resume] checkpoint held {provisional_count} uncommitted "
            f"shadow edit(s); reverting to the last committed champion "
            f"(shadow evidence was not persisted)"
        )
    return {
        "steps": steps,
        "accepted_edits_history": list(restored["accepted_edits_history"]),
        "batch_history": list(restored["batch_history"]),
        "probe_history": list(restored["probe_history"]),
        "baseline_canary_audit": list(restored["baseline_canary_audit"]),
        "rollback_reason": str(restored.get("rollback_reason") or ""),
        "pending_probe_step_indexes": pending_probe_step_indexes,
        "pending_probe_commits": pending_probe_commits,
        "stable_cases": int(restored_counters.get("stable_cases") or 0),
        "triggered_cases": int(restored_counters.get("triggered_cases") or 0),
        "edits_accepted": int(restored_counters.get("edits_accepted") or 0),
        "edits_rejected": int(restored_counters.get("edits_rejected") or 0),
        "stable_memory_payload": stable_memory_payload,
        "stable_history_length": int(restored.get("stable_history_length") or 0),
        "last_probe_accepted_count": int(
            restored.get("last_probe_accepted_count") or 0
        ),
        "last_probe_triggered_count": int(
            restored.get("last_probe_triggered_count") or 0
        ),
        "active_attempt_index": active_attempt_index,
        "mutation_retry_feedback": {
            str(case_id): [str(value) for value in values]
            for case_id, values in (
                restored.get("mutation_retry_feedback") or {}
            ).items()
            if isinstance(values, list)
        },
        "rejected_source_proposals": {
            str(case_id): [dict(value) for value in values]
            for case_id, values in (
                restored.get("rejected_source_proposals") or {}
            ).items()
            if isinstance(values, list)
        },
        "runtime_invalid_cases": int(restored.get("runtime_invalid_cases") or 0),
        "runtime_error_categories": {
            str(category): int(count)
            for category, count in (
                restored.get("runtime_error_categories") or {}
            ).items()
        },
        "policy_genome": policy_genome,
        "l2_policy_accepts": l2_policy_accepts,
        "gate_event_ledger": gate_event_ledger,
        "stable_policy_genome": stable_policy_genome,
        "stable_l2_policy_accepts": stable_l2_policy_accepts,
        "abstention_ladder_used": {
            str(value)
            for value in (checkpoint.get("abstention_ladder_used") or ())
        },
        "contrast_replay_registry": {
            str(hallucination_type): [
                dict(row)
                for row in (rows or ())
                if isinstance(row, Mapping)
            ]
            for hallucination_type, rows in (
                checkpoint.get("contrast_registry") or {}
            ).items()
        },
        "rejected_policy_summaries": [
            str(value)
            for value in (checkpoint.get("rejected_policy_summaries") or ())
        ][-20:],
        "localization_bias_signals": [
            dict(value)
            for value in (checkpoint.get("localization_bias_signals") or ())
            if isinstance(value, Mapping)
        ],
        "non_l1_failure_attributions": [
            dict(value)
            for value in (checkpoint.get("non_l1_failure_attributions") or ())
            if isinstance(value, Mapping)
        ],
        "l2_policy_audit": l2_policy_audit,
        "resume_next_case_index": resume_next_case_index,
        "run_id": str(checkpoint.get("run_id") or ""),
    }


@dataclass
class _PersistenceContext:
    """Run state that ``_persist_progress`` snapshots into the checkpoints."""

    output_dir: Path
    run_id: str
    evolution_case_ids: Sequence[str]
    records: Mapping[str, dict[str, Any]]
    config: dict[str, Any]
    gate: Any
    champion_genome: SkillGenome
    current_genome: SkillGenome
    stable_genome: SkillGenome
    policy_genome: PolicyGenome
    stable_policy_genome: PolicyGenome
    causal_memory: Any
    gate_event_ledger: GateEventLedger
    steps: list[Any]
    provisional_edits: list[Any]
    accepted_edits_history: list[dict[str, Any]]
    batch_history: list[dict[str, Any]]
    probe_history: list[dict[str, Any]]
    baseline_canary_audit: list[dict[str, Any]]
    registered_canary_ids: tuple[str, ...]
    budget_usage: dict[str, int]
    rollback_reason: str
    stable_canaries: dict[str, dict[str, Any]]
    stable_canaries_checkpoint: dict[str, dict[str, Any]]
    stable_memory_payload: dict[str, Any]
    stable_history_length: int
    pending_probe_step_indexes: list[int]
    pending_probe_commits: list[PendingProbeCommit]
    active_attempt_index: int
    mutation_retry_feedback: dict[str, list[str]]
    rejected_source_proposals: dict[str, list[dict[str, Any]]]
    rejected_buffer: list[RejectedEditEntry]
    runtime_invalid_cases: int
    runtime_error_categories: dict[str, int]
    last_probe_accepted_count: int
    last_probe_triggered_count: int
    stable_cases: int
    triggered_cases: int
    edits_accepted: int
    edits_rejected: int
    l2_policy_accepts: int
    l2_policy_audit: list[dict[str, Any]]
    stable_l2_policy_accepts: int
    abstention_ladder_used: set[str]
    contrast_replay_registry: dict[str, list[dict[str, Any]]]
    rejected_policy_summaries: list[str]
    localization_bias_signals: list[dict[str, Any]]
    non_l1_failure_attributions: list[dict[str, Any]]


def _persist_progress(
    ctx: _PersistenceContext, next_case_index: int, status: str = "in_progress",
) -> None:
    pending_probe_payloads = []
    for item in ctx.pending_probe_commits:
        certificate_index = next(
            (
                index for index, certificate in enumerate(ctx.gate.certificates)
                if certificate is item.certificate
            ),
            None,
        )
        if certificate_index is None:
            continue
        pending_probe_payloads.append({
            "case_id": item.case_id,
            "case_index": item.case_index,
            "edit": item.edit.to_dict(),
            "certificate_index": certificate_index,
            "step_index": item.step_index,
        })
    rejected_buffer_payloads = []
    for entry in ctx.rejected_buffer:
        proposal = entry.proposal
        rejected_buffer_payloads.append({
            "proposal": {
                "target_module": str(proposal.target_module),
                "operation": (
                    proposal.operation.value
                    if isinstance(proposal.operation, SkillOp)
                    else str(proposal.operation)
                ),
                "content": str(proposal.content),
            },
            "regressed_cases": list(entry.regressed_cases),
            "budget_consumed": entry.budget_consumed,
            "reject_reasons": list(entry.reject_reasons),
            "rejection_stage": entry.rejection_stage,
            "observed_effect": entry.observed_effect,
            "observable_deltas": list(entry.observable_deltas),
        })
    atomic_write_json(
        ctx.output_dir / "champion_checkpoint.json",
        ctx.champion_genome.to_dict(),
    )
    atomic_write_json(
        ctx.output_dir / "shadow_checkpoint.json",
        ctx.current_genome.to_dict(),
    )
    atomic_write_json(
        ctx.output_dir / "evolution_checkpoint.json",
        {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "run_id": ctx.run_id,
            "status": status,
            "next_case_index": next_case_index,
            "evolution_case_count": len(ctx.evolution_case_ids),
            "evolution_case_ids": list(ctx.evolution_case_ids),
            "input_content_hash": stable_hash(
                {case_id: ctx.records[case_id] for case_id in ctx.evolution_case_ids}
            ),
            "config_hash": stable_hash(ctx.config),
            "genome_hash": ctx.champion_genome.content_hash,
            "champion_genome_hash": ctx.champion_genome.content_hash,
            "shadow_genome_hash": ctx.current_genome.content_hash,
            "provisional_edit_count": len(ctx.provisional_edits),
            "steps": [step.to_dict() for step in ctx.steps],
            "gate_summary": ctx.gate.summary(),
            "accepted_edits_history": ctx.accepted_edits_history,
            "batch_history": ctx.batch_history,
            "probe_history": ctx.probe_history,
            "baseline_canary_audit": ctx.baseline_canary_audit,
            "canary_case_ids": list(ctx.registered_canary_ids),
            "budget_usage": dict(ctx.budget_usage),
            "rollback_reason": ctx.rollback_reason,
            "stable_genome_hash": ctx.stable_genome.content_hash,
            "stable_genome_payload": ctx.stable_genome.to_dict(),
            "stable_canaries": dict(ctx.stable_canaries),
            "stable_canaries_checkpoint": dict(ctx.stable_canaries_checkpoint),
            "stable_memory_payload": ctx.stable_memory_payload,
            "stable_history_length": ctx.stable_history_length,
            "pending_probe_step_indexes": list(ctx.pending_probe_step_indexes),
            "pending_probe_commits": pending_probe_payloads,
            "active_attempt_index": ctx.active_attempt_index,
            "mutation_retry_feedback": ctx.mutation_retry_feedback,
            "rejected_source_proposals": ctx.rejected_source_proposals,
            "rejected_buffer": rejected_buffer_payloads,
            "runtime_invalid_cases": ctx.runtime_invalid_cases,
            "runtime_error_categories": ctx.runtime_error_categories,
            "last_probe_accepted_count": ctx.last_probe_accepted_count,
            "last_probe_triggered_count": ctx.last_probe_triggered_count,
            "counters": {
                "stable_cases": ctx.stable_cases,
                "triggered_cases": ctx.triggered_cases,
                "edits_accepted": ctx.edits_accepted,
                "edits_rejected": ctx.edits_rejected,
                "runtime_invalid_cases": ctx.runtime_invalid_cases,
            },
            "policy_genome": ctx.policy_genome.to_dict(),
            "l2_policy_accepts": ctx.l2_policy_accepts,
            "gate_events": _build_gate_event_ledger(ctx.steps).to_list(),
            "l2_policy_audit": list(ctx.l2_policy_audit),
            # Probe rollback anchors and the in-memory evolution context that
            # a resume must reproduce exactly (behavior invariants like the
            # once-per-source abstention ladder, plus the audit telemetry of
            # the processed prefix).
            "stable_policy_genome_payload": ctx.stable_policy_genome.to_dict(),
            "stable_policy_genome_hash": ctx.stable_policy_genome.content_hash,
            "stable_memory_hash": payload_hash(ctx.stable_memory_payload),
            "stable_l2_policy_accepts": ctx.stable_l2_policy_accepts,
            "abstention_ladder_used": sorted(ctx.abstention_ladder_used),
            "contrast_registry": {
                str(hallucination_type): list(rows)
                for hallucination_type, rows in ctx.contrast_replay_registry.items()
            },
            "rejected_policy_summaries": list(ctx.rejected_policy_summaries),
            "localization_bias_signals": list(ctx.localization_bias_signals),
            "non_l1_failure_attributions": list(ctx.non_l1_failure_attributions),
            "updated_at": time.time(),
        },
    )
    atomic_write_json(
        ctx.output_dir / "htc_tes_experience_memory.json",
        ctx.causal_memory.to_dict(),
    )


def _save_evolution_results(
    *, output_dir: Path, result: OnlineEvolutionResult,
    champion_genome: SkillGenome, policy_genome: PolicyGenome,
    causal_memory: Any,
    provider_usage_ledger: ProviderUsageLedger,
    evolution_case_ids: Sequence[str], run_id: str,
    duration: float, triggered_cases: int, stable_cases: int,
    edits_accepted: int, edits_rejected: int, gate: Any,
    registered_canary_ids: Sequence[str],
    baseline_canary_audit: list[dict[str, Any]],
    type_coverage: dict[str, Any],
    persist_progress: Any,
) -> None:
    """Persist evolution results and print the completion summary.

    The run tail writes six JSON artifacts and prints a summary. Extracting
    it keeps the main loop readable and makes the persistence contract
    testable.
    """
    atomic_write_json(output_dir / "evolution_result.json", result.to_dict())
    atomic_write_json(output_dir / "champion_genome.json", champion_genome.to_dict())
    atomic_write_json(output_dir / "policy_genome.json", policy_genome.to_dict())
    atomic_write_json(output_dir / "htc_tes_coverage.json", type_coverage)
    atomic_write_json(
        output_dir / "htc_tes_experience_memory.json",
        causal_memory.to_dict(),
    )
    atomic_write_json(
        output_dir / "provider_usage_ledger.json",
        provider_usage_ledger.to_dict(),
    )
    persist_progress(len(evolution_case_ids), status="complete")

    print(f"\n[{run_id}] Complete in {duration:.1f}s:")
    print(f"  Cases: {len(evolution_case_ids)} total, {triggered_cases} triggered, "
          f"{stable_cases} stable")
    print(f"  Edits: {edits_accepted} accepted, {edits_rejected} rejected")
    if registered_canary_ids:
        print(
            f"  Baseline canaries: "
            f"{sum(bool(item.get('admitted')) for item in baseline_canary_audit)}/"
            f"{len(registered_canary_ids)} admitted"
        )
    print(f"  Halted: {gate.is_halted} ({gate.halt_reason})")
    print(f"  Champion: {champion_genome.content_hash[:16]}...")
    print(f"  HTC-TES: {type_coverage['types_with_experience']}/"
          f"{type_coverage['total_types']} types covered "
          f"(effective: {type_coverage['effective_coverage']:.1%})")
