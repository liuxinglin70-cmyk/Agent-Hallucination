"""SEHD-RISE loop case-record preparation and parameter validation.

Per-case record preparation (join gold under the explicit ``use_gold``
opt-in, then project through the §2.11 single projection authority) and
the fail-fast validation of run parameters. The case-record helpers that
shipped in the frozen source under other owners already live in their
owning layers: ``detection/detector`` (detector record), ``platform/gold``
(evaluator gold), ``execution/replay`` (authority failure details) — this
module keeps only the two functions listed in plan Phase 9 §1.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from sehd_rise.causal.signals import DetectionSignal, project_detection
from sehd_rise.platform.gold import _evaluator_gold


def attribute_failure_for_result(
    result: Mapping[str, Any],
    case_id: str,
    *,
    records: Mapping[str, dict[str, Any]],
    use_gold: bool,
    gold_reflection: bool,
) -> DetectionSignal:
    """Join gold to attribution only under the explicit use_gold opt-in."""
    payload = dict(result)
    if use_gold:
        gold = _evaluator_gold(records[case_id])
        if gold is not None:
            payload["gold_label"] = gold
            # ``gold_steps`` is the authoritative localization-correctness
            # input (``responsible_step_correct`` in fitness); it was
            # stripped by the detector firewall like every evaluator field,
            # so it is re-joined here under the same explicit opt-in.  A
            # gold error case without gold_steps stays
            # responsible_step_correct=False in fitness (documented
            # behaviour: unverified localization is not evidence of a
            # defect, but also not evidence of correctness).
            payload["gold_steps"] = records[case_id].get("gold_steps")
    signal = project_detection(payload, use_gold=use_gold)
    # ``gold_reflection`` gates the gold-diagnostic section of the L1
    # reflection prompt (planning/l1 reads it off the signal). The signal
    # is frozen by design, so the opt-in rides as a dynamic flag — the same
    # idiom episode.py uses to stamp derived edge ids.
    if gold_reflection and signal.has_gold:
        object.__setattr__(signal, "gold_reflection", True)
    return signal


def validate_evolution_params(
    *,
    evolution_case_ids: Sequence[str],
    records: Mapping[str, dict[str, Any]],
    reflection_candidate_count: int,
    fitness_batch_size: int,
    canary_limit: int,
    validation_probe_every: int,
    validation_probe_trigger_threshold: int,
    max_mutation_attempts_per_case: int,
    probe_limit: int,
    probe_experience_record: bool,
    probe_ids: Sequence[str],
    canary_case_ids: Sequence[str],
    use_gold: bool,
    fitness_use_gold: bool,
) -> None:
    """Fail-fast validation of online-evolution run parameters."""
    if not evolution_case_ids:
        raise ValueError("evolution_case_ids must not be empty")
    if reflection_candidate_count < 1 or reflection_candidate_count > 5:
        raise ValueError("reflection_candidate_count must be between 1 and 5")
    if len(evolution_case_ids) != len(set(evolution_case_ids)):
        raise ValueError("evolution_case_ids contains duplicates")
    missing_records = [
        case_id for case_id in evolution_case_ids if case_id not in records
    ]
    if missing_records:
        raise ValueError(
            "Evolution records missing: " + ", ".join(missing_records)
        )
    if fitness_batch_size != 1:
        raise ValueError(
            "fitness_batch_size must be 1: multi-edit shadow batches cannot "
            "attribute a combined replay outcome to individual edits safely"
        )
    if canary_limit < 0 or canary_limit > 20:
        raise ValueError("canary_limit must be between 0 and 20")
    if validation_probe_every < 0:
        raise ValueError("validation_probe_every must be non-negative")
    if validation_probe_trigger_threshold < 0:
        raise ValueError(
            "validation_probe_trigger_threshold must be non-negative"
        )
    if max_mutation_attempts_per_case < 1 or max_mutation_attempts_per_case > 3:
        raise ValueError(
            "max_mutation_attempts_per_case must be between 1 and 3"
        )
    if probe_limit < 0 or probe_limit > 20:
        raise ValueError("probe_limit must be between 0 and 20")
    if not isinstance(probe_experience_record, bool):
        raise ValueError("probe_experience_record must be a bool")
    if len(probe_ids) != len(set(probe_ids)):
        raise ValueError("probe_ids contains duplicates")
    if len(canary_case_ids) != len(set(canary_case_ids)):
        raise ValueError("canary_case_ids contains duplicates")
    overlapping_canary_ids = sorted(
        set(canary_case_ids).intersection(evolution_case_ids)
    )
    if overlapping_canary_ids:
        raise ValueError(
            "canary_case_ids must be disjoint from evolution_case_ids: "
            + ", ".join(overlapping_canary_ids)
        )
    overlapping_canary_probe_ids = sorted(
        set(canary_case_ids).intersection(probe_ids)
    )
    if overlapping_canary_probe_ids:
        raise ValueError(
            "canary_case_ids must be disjoint from probe_ids: "
            + ", ".join(overlapping_canary_probe_ids)
        )
    overlapping_probe_ids = sorted(
        set(probe_ids).intersection(evolution_case_ids)
    )
    if overlapping_probe_ids:
        raise ValueError(
            "probe_ids must be disjoint from evolution_case_ids: "
            + ", ".join(overlapping_probe_ids)
        )
    missing_probe_records = [
        case_id for case_id in probe_ids if case_id not in records
    ]
    if missing_probe_records:
        raise ValueError(
            "Probe records missing: " + ", ".join(missing_probe_records)
        )
    missing_canary_records = [
        case_id for case_id in canary_case_ids if case_id not in records
    ]
    if missing_canary_records:
        raise ValueError(
            "Canary records missing: " + ", ".join(missing_canary_records)
        )
    registered_probe_ids = tuple(probe_ids[:probe_limit])
    missing_probe_gold = [
        case_id
        for case_id in registered_probe_ids
        if _evaluator_gold(records[case_id]) is None
    ]
    if missing_probe_gold and (use_gold or fitness_use_gold):
        raise ValueError(
            "Development probes require evaluator gold labels: "
            + ", ".join(missing_probe_gold)
        )
