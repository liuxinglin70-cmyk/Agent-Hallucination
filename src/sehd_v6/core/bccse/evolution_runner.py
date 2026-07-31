"""BCCSE v5 — Per-case online skill evolution runner (refactored).

Main orchestration loop for causal-process-reflection-driven online skill evolution.
Processes cases sequentially with CONDITION-TRIGGERED evolution:
- Not every case triggers evolution (only "informative" cases do)
- Self-supervised signals drive reflection (no gold label required)
- Structural constraints replace expensive canary re-evaluation
- Convergence detection naturally terminates evolution

Architecture:
    For each case:
        Detect (v3.5 + current Skill) →
        Trigger Check (self-supervised signals) →
        [if triggered] Causal Process Reflection (step-level attribution) →
        [if attributable] LLM Reflection (generate edit proposal) →
        Structural Constraint Gate →
        Accept/Reject
"""

from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..paca_tcl import proof_engine as v35
from ..paca_tcl.proof_evaluator import (
    DeepSeekMutationProvider,
    _json_object_provider_call,
)
from ..paca_tcl.skill_integration import integrated_v35_provider
from ..paca_tcl.basic_runner import write_json, write_jsonl
from .reflection_engine import (
    RejectedEditEntry,
    build_causal_reflection_prompt,
    build_capacity_guidance,
    build_accepted_edits_summary,
    compile_edit_proposal,
    format_detection_context_for_llm,
    format_skill_context_for_llm,
)
from .genome import (
    SkillEdit,
    SkillGenome,
    SkillOp,
    apply_edit,
    create_baseline_genome,
    validate_edit,
    semantic_overlap,
    MODULE_IDS,
    FITNESS_STATUS_WEAK,
    FITNESS_STATUS_STRONG,
)
from .fitness import (
    FitnessAssessment,
    assess_source_fitness,
    task_state_score,
)
from .safety_gate import StructuralGate, create_structural_gate
from .step_attributor import (
    AttributionSignal,
    ErrorType,
    TriggerSource,
    attribute_failure,
)

# ============================================================
# Data structures
# ============================================================

@dataclass
class EvolutionStep:
    """Record of a single evolution step."""

    case_id: str
    case_index: int
    trigger_source: str  # what triggered reflection (or "none")
    attribution: dict[str, Any] | None
    edit_proposal: dict[str, Any] | None
    mutation_applied: dict[str, Any] | None
    gate_decision: str  # "accept" | "reject" | "skip_stable" | "skip_no_edit" | "halted"
    genome_hash_before: str
    genome_hash_after: str
    detection_state: str
    timestamp: float
    fitness_check: dict[str, Any] | None = None

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
        }


@dataclass
class OnlineEvolutionResult:
    """Complete result of one online evolution run."""

    run_id: str
    champion_genome: SkillGenome
    steps: list[EvolutionStep]
    gate_summary: dict[str, Any]
    total_cases: int
    triggered_cases: int
    edits_accepted: int
    edits_rejected: int
    stable_cases: int
    use_gold: bool
    halted: bool
    halt_reason: str
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "agenthallu.bccse_v5_online_evolution.v2",
            "run_id": self.run_id,
            "champion_genome": self.champion_genome.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "gate_summary": self.gate_summary,
            "total_cases": self.total_cases,
            "triggered_cases": self.triggered_cases,
            "edits_accepted": self.edits_accepted,
            "edits_rejected": self.edits_rejected,
            "stable_cases": self.stable_cases,
            "supervision_mode": "gold_enhanced" if self.use_gold else "self_supervised",
            "use_gold": self.use_gold,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class ProvisionalEdit:
    """One reversible edit admitted to the current shadow genome."""

    case_id: str
    case_index: int
    edit: SkillEdit
    certificate: Any
    source_signal: AttributionSignal
    source_result: dict[str, Any]
    individual_assessment: FitnessAssessment
    step_index: int
    applied_rule_idx: int | None = None  # v5.3.4: rule index at apply time


# ============================================================
# Detection with skill injection
# ============================================================

def _run_detection_with_genome(
    genome: SkillGenome,
    case_id: str,
    record: dict[str, Any],
    config: dict[str, Any],
    output_dir: Path,
    census_root: Path,
    baseline_cache_root: Path | None = None,
    shared_cache_root: Path | None = None,
    provider_already_patched: bool = False,
) -> dict[str, Any]:
    """Run PACA-TCL v3.5 with global skill injection.

    v5: Global injection for ALL cases (not conditional on unresolved).
    If genome == baseline, runs plain v3.5 without injection overhead.

    v3.5.1: ``provider_already_patched`` allows batch flush to enter the
    integrated provider context once and reuse it for all shadow detections
    in parallel, avoiding repeated lock acquire/release and enabling true
    concurrency for same-genome batch evaluations.
    """
    import shutil

    baseline = create_baseline_genome()

    # Seed genome directory with baseline cache for efficiency.
    # v5.3.4b-D2: 7 cache subdirs are symlinked to a SHARED cache root keyed by
    # genome.content_hash, so individual-replay and batch-replay of the same genome+case
    # reuse the same LLM cache (eliminates CRS nondeterminism from fresh LLM calls).
    # genome_dir itself remains the per-run artifacts dir (predictions, ledger, etc.).
    genome_dir = output_dir / "genomes" / genome.content_hash
    genome_dir.mkdir(parents=True, exist_ok=True)
    cache_dirs = (
        "layered_v2", "construct_census_cache", "focused_prosecution_cache",
        "defense_cache", "appeal_prosecutor_cache", "appeal_defender_cache",
        "counterfactual_cache",
    )
    if shared_cache_root is not None:
        # Use the shared cache root passed by the caller (fixed path, not relative)
        shared_hash_root = Path(shared_cache_root) / genome.content_hash
        shared_hash_root.mkdir(parents=True, exist_ok=True)
        marker = shared_hash_root / ".baseline_cache_seeded"
        if baseline_cache_root is not None and not marker.exists():
            for name in cache_dirs:
                source = Path(baseline_cache_root) / name
                if source.is_dir():
                    shutil.copytree(source, shared_hash_root / name, dirs_exist_ok=True)
            marker.write_text("seeded\n", encoding="utf-8")
        # Create symlinks: genome_dir/<cache_name> -> shared_hash_root/<cache_name>
        for name in cache_dirs:
            link_path = genome_dir / name
            target = shared_hash_root / name
            target.mkdir(parents=True, exist_ok=True)
            if not link_path.exists() and not link_path.is_symlink():
                link_path.symlink_to(target, target_is_directory=True)
    else:
        # Fallback: original behavior (no sharing) for backward compatibility
        marker = genome_dir / ".baseline_cache_seeded"
        if baseline_cache_root is not None and not marker.exists():
            for name in cache_dirs:
                source = Path(baseline_cache_root) / name
                if source.is_dir():
                    shutil.copytree(source, genome_dir / name, dirs_exist_ok=True)
            marker.write_text("seeded\n", encoding="utf-8")

    def invoke() -> dict[str, Any]:
        if genome.content_hash == baseline.content_hash:
            return v35.adjudicate_case(
                case_id=case_id,
                record=record,
                config=config,
                output_dir=genome_dir,
                census_root=census_root,
            )
        if provider_already_patched:
            # v3.5.1: provider already patched by caller (batch flush parallel mode)
            return v35.adjudicate_case(
                case_id=case_id,
                record=record,
                config=config,
                output_dir=genome_dir,
                census_root=census_root,
            )
        with integrated_v35_provider(genome=genome, config=config):
            return v35.adjudicate_case(
                case_id=case_id,
                record=record,
                config=config,
                output_dir=genome_dir,
                census_root=census_root,
            )

    result = invoke()
    if not isinstance(result, dict):
        raise RuntimeError("detector returned a non-object result")
    state = str(result.get("state") or "")
    if state not in {"verified_error", "verified_clean", "unresolved"}:
        raise RuntimeError(f"detector returned invalid state: {state!r}")
    if result.get("runtime_valid") is False:
        raise RuntimeError("detector returned runtime_valid=false")
    return result


def _atomic_write_json(path: Path, value: Any) -> None:
    """Write a restart/audit artifact without exposing a partial JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


# ============================================================
# ============================================================
# Main evolution loop
# ============================================================


_SIGNAL_SEVERITY = {
    ErrorType.CORRECT: 0,
    ErrorType.BOUNDARY_HESITATION: 1,
    ErrorType.CRS_CONTRADICTION: 2,
    ErrorType.EVIDENCE_INCOMPLETE: 2,
    ErrorType.UNRESOLVED_UNKNOWN: 3,
    ErrorType.UNRESOLVED_SHOULD_ERROR: 4,
    ErrorType.UNRESOLVED_SHOULD_CLEAN: 4,
    ErrorType.CANDIDATE_MISS: 4,
    ErrorType.FALSE_POSITIVE: 4,
    ErrorType.FALSE_NEGATIVE_CLEAN: 4,
    ErrorType.FALSE_NEGATIVE_UNRESOLVED: 4,
    ErrorType.LOCALIZATION_ERROR: 3,
    ErrorType.SIGNAL_UNAVAILABLE: 4,
}


def _signal_severity(signal: AttributionSignal) -> int:
    return _SIGNAL_SEVERITY.get(signal.error_type, 4)


def _llm_merge_rules(
    existing_rule: str,
    new_rule: str,
    *,
    mutation_route: dict[str, Any] | None,
) -> str | None:
    """Merge two semantically-overlapping rules into a single rule.

    Uses LLM-assisted merge when a mutation_route is available; falls back to
    structural merge (newline-separated concatenation) otherwise.

    Args:
        existing_rule: The existing rule text to merge into.
        new_rule: The new rule text carrying additional conditions.
        mutation_route: LLM route for reflection calls (None = structural only).

    Returns:
        Merged rule text, or None if merge failed.
    """
    # Structural fallback: concatenate conditions with a separator.
    # This preserves both rules' information without semantic analysis.
    if mutation_route is None:
        # Simple structural merge: combine unique condition clauses.
        # Split on common separators and deduplicate clauses.
        def _split_clauses(text: str) -> list[str]:
            for sep in ["；", "; ", "；", "。", ". "]:
                if sep in text:
                    return [c.strip() for c in text.split(sep) if c.strip()]
            return [text.strip()]

        existing_clauses = _split_clauses(existing_rule)
        new_clauses = _split_clauses(new_rule)
        # Add new clauses that are not near-duplicates of existing ones
        merged = list(existing_clauses)
        for nc in new_clauses:
            is_dup = any(
                semantic_overlap(nc, ec) >= 0.85 for ec in existing_clauses
            )
            if not is_dup:
                merged.append(nc)
        return "；".join(merged) if len(merged) > 1 else merged[0] if merged else None

    # LLM-assisted merge
    system_prompt = (
        "你是规则合并助手。请将以下两条语义重叠的规则合并为一条更通用的规则，"
        "保留两条规则的所有有效条件，去除冗余，保持 if-then 格式。"
    )
    user_prompt = (
        f"【已有规则】\n{existing_rule}\n\n"
        f"【新增规则】\n{new_rule}\n\n"
        "请直接输出合并后的规则文本，不要包含任何解释或标记。"
        "合并后的规则应该：\n"
        "1. 保持简洁的 if-then 行为指令格式\n"
        "2. 包含两条规则中所有不重复的条件\n"
        "3. 保留两条规则的有效动作\n"
        "4. 总长度不超过 120 字符\n"
        "请以 JSON 格式返回：{\"merged_rule\": \"合并后的规则文本\"}"
    )
    try:
        merged, _ = _json_object_provider_call(
            route=mutation_route,
            system=system_prompt,
            user=user_prompt,
            cache_dir=Path(".skill_merge_cache"),
        )
        if merged and isinstance(merged, dict):
            rule = merged.get("merged_rule", "").strip()
            if rule and len(rule) <= 120:
                return rule
    except Exception:
        pass
    # Fallback to structural merge
    return _llm_merge_rules(existing_rule, new_rule, mutation_route=None)


def _source_fitness_passed(
    before: AttributionSignal,
    after: AttributionSignal,
) -> bool:
    """Return whether a source achieved strong (fully closed) improvement.

    Kept as a compatibility helper for audits/tests. Online selection now uses
    assess_source_fitness(), which also admits measurable module-local progress
    to a reversible shadow batch.
    """
    return (
        before.should_evolve
        and not after.should_evolve
        and after.state in {"verified_error", "verified_clean"}
    )


def run_online_evolution(
    *,
    evolution_case_ids: Sequence[str],
    records: Mapping[str, dict[str, Any]],
    config: dict[str, Any],
    output_dir: Path,
    census_root: Path,
    use_gold: bool = True,
    mutation_route: dict[str, Any] | None = None,
    baseline_cache_root: Path | None = None,
    module_cooldown: int = 3,
    max_total_edits: int = 30,
    convergence_window: int = 10,
    fitness_check: bool = True,
    canary_size: int = 3,
    initial_canaries: Sequence[tuple[str, str]] = (),
    fitness_batch_size: int = 1,
    fitness_use_gold: bool = True,
    run_id: str | None = None,
) -> OnlineEvolutionResult:
    """Run per-case online evolution with condition-triggered self-supervised reflection.

    Args:
        evolution_case_ids: Cases for evolution (natural order or shuffled).
        records: Case records keyed by case_id.
        config: PACA-TCL v3.5 config.
        output_dir: Output directory.
        census_root: Census cache root.
        use_gold: Explicit opt-in to gold-enhanced attribution. Default is
            strict self-supervision.
        mutation_route: LLM route for reflection calls (None = template only).
        baseline_cache_root: Cache root for efficiency.
        module_cooldown: Cases between edits to same module.
        max_total_edits: Maximum edits per run.
        convergence_window: Consecutive no-edit cases before convergence.
        fitness_check: Require local source improvement and reversible batch
            selection before champion commit.
        canary_size: Number of matched champion-vs-shadow audit cases per batch.
        initial_canaries: Optional audit case IDs. Their cached states are never
            treated as truth.
        fitness_batch_size: Provisional edits accumulated before batch commit.
            Default 1 (incremental commit) — each edit is validated and committed
            independently, avoiding same-module edit interference in batch audit.
            Set >1 to batch multiple edits (faster but risks batch rollback).
        fitness_use_gold: Use labels only inside fitness selection. Labels never
            enter attribution or the reflection prompt unless use_gold is also
            explicitly enabled.
        run_id: Optional run identifier.

    Returns:
        OnlineEvolutionResult with champion genome and audit trail.
    """
    if not evolution_case_ids:
        raise ValueError("evolution_case_ids must not be empty")
    if len(evolution_case_ids) != len(set(evolution_case_ids)):
        raise ValueError("evolution_case_ids contains duplicates")
    missing_records = [case_id for case_id in evolution_case_ids if case_id not in records]
    if missing_records:
        raise ValueError(
            "Evolution records missing: " + ", ".join(missing_records)
        )
    if canary_size < 0 or canary_size > 10:
        raise ValueError("canary_size must be between 0 and 10")
    if fitness_batch_size < 1 or fitness_batch_size > 10:
        raise ValueError("fitness_batch_size must be between 1 and 10")
    if len(initial_canaries) > canary_size:
        raise ValueError("initial_canaries cannot exceed canary_size")
    initial_ids = [str(case_id) for case_id, _ in initial_canaries]
    if len(initial_ids) != len(set(initial_ids)):
        raise ValueError("initial_canaries contains duplicate case IDs")
    for case_id, state in initial_canaries:
        if case_id not in records:
            raise ValueError(f"Initial canary record missing: {case_id}")
        if state not in {"verified_error", "verified_clean"}:
            raise ValueError(f"Initial canary is not a stable state: {state}")
    if mutation_route is not None:
        model = str(mutation_route.get("model") or "")
        if model not in {"DeepSeek-V4-Flash", "DeepSeek-V4-Pro"}:
            raise ValueError(f"Disallowed mutation model: {model!r}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # v5.3.4b-D2: shared LLM cache root for all replays of the same genome+case
    shared_cache_root = output_dir / "shared_cache"
    shared_cache_root.mkdir(parents=True, exist_ok=True)
    start_time = time.time()

    if run_id is None:
        run_id = f"bccse_v5_{uuid.uuid4().hex[:8]}"

    # Initialize
    champion_genome = create_baseline_genome()
    current_genome = champion_genome  # reversible working/shadow genome
    gate = create_structural_gate(
        module_cooldown=module_cooldown,
        max_total_edits=max_total_edits,
        convergence_window=convergence_window,
    )
    rejected_buffer: list[RejectedEditEntry] = []
    accepted_edits_history: list[dict[str, Any]] = []  # Track accepted edits for diversity
    steps: list[EvolutionStep] = []
    provisional_edits: list[ProvisionalEdit] = []
    batch_history: list[dict[str, Any]] = []

    print(f"[{run_id}] Starting online evolution: {len(evolution_case_ids)} cases")
    print(f"  Config: use_gold={use_gold}, cooldown={module_cooldown}, "
          f"max_edits={max_total_edits}, convergence_window={convergence_window}")

    stable_cases = 0
    # Cached states are provenance only. Batch canaries are always evaluated as
    # matched champion-vs-shadow pairs.
    anchor_canaries = [(str(case_id), str(state)) for case_id, state in initial_canaries]
    stable_canaries: list[tuple[str, str]] = []
    triggered_cases = 0
    edits_accepted = 0
    edits_rejected = 0

    def persist_progress(next_case_index: int, status: str = "in_progress") -> None:
        _atomic_write_json(
            output_dir / "champion_checkpoint.json",
            champion_genome.to_dict(),
        )
        _atomic_write_json(
            output_dir / "shadow_checkpoint.json",
            current_genome.to_dict(),
        )
        _atomic_write_json(
            output_dir / "evolution_checkpoint.json",
            {
                "schema_version": "agenthallu.bccse_v5_checkpoint.v1",
                "run_id": run_id,
                "status": status,
                "next_case_index": next_case_index,
                "evolution_case_count": len(evolution_case_ids),
                "evolution_case_ids": list(evolution_case_ids),
                "genome_hash": champion_genome.content_hash,
                "champion_genome_hash": champion_genome.content_hash,
                "shadow_genome_hash": current_genome.content_hash,
                "provisional_edit_count": len(provisional_edits),
                "steps": [step.to_dict() for step in steps],
                "gate_summary": gate.summary(),
                "stable_canaries": [
                    {"case_id": value[0], "state": value[1]}
                    for value in stable_canaries
                ],
                "anchor_canaries": [
                    {"case_id": value[0], "state": value[1]}
                    for value in anchor_canaries
                ],
                "accepted_edits_history": accepted_edits_history,
                "batch_history": batch_history,
                "counters": {
                    "stable_cases": stable_cases,
                    "triggered_cases": triggered_cases,
                    "edits_accepted": edits_accepted,
                    "edits_rejected": edits_rejected,
                },
                "updated_at": time.time(),
            },
        )

    def record_step(step: EvolutionStep, *, next_case_index: int) -> None:
        steps.append(step)
        persist_progress(next_case_index)

    def fitness_canaries() -> list[tuple[str, str]]:
        if canary_size == 0:
            return []
        selected = list(anchor_canaries[:canary_size])
        if len(selected) >= canary_size:
            return selected
        for item in reversed(stable_canaries):
            if item[0] in {case_id for case_id, _ in selected}:
                continue
            selected.append(item)
            if len(selected) >= canary_size:
                break
        return selected[:canary_size]

    def fitness_gold(case_id: str) -> bool | None:
        if not fitness_use_gold:
            return None
        record = records[case_id]
        # Try gold_label (bool) first, then is_hallucination (str)
        value = record.get("gold_label")
        if isinstance(value, bool):
            return value
        value = record.get("is_hallucination")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower().strip() == "true"
        return None  # gold unavailable, degrade to self-supervised

    def flush_shadow_batch(*, next_case_index: int, force: bool = False) -> bool:
        """Commit or roll back the current reversible shadow batch."""
        nonlocal champion_genome, current_genome
        nonlocal edits_accepted, edits_rejected

        if not provisional_edits:
            return True
        if not force and len(provisional_edits) < fitness_batch_size:
            return True

        batch_id = f"batch_{len(batch_history):03d}"
        batch_root = output_dir / "fitness_batches" / batch_id
        source_audits: list[dict[str, Any]] = []
        canary_audits: list[dict[str, Any]] = []
        runtime_errors: list[str] = []

        # v3.5.1: 并行执行所有检测，分两阶段避免 provider patch 冲突
        # 阶段1: 并行执行所有 shadow 检测（current_genome，在 integrated_v35_provider 上下文内）
        # 阶段2: 并行执行所有 champion canary 检测（baseline genome，不需要 patch）
        # 保障：每阶段内检测输入完全独立，并行不改变任何单次检测的输入和输出
        canary_list = list(fitness_canaries())

        shadow_raw: dict[str, Any] = {}  # case_id -> result_dict 或 Exception

        # 阶段1: 并行执行所有 shadow 检测（source 重检 + canary shadow）
        shadow_case_count = len(provisional_edits) + len(canary_list)
        if shadow_case_count:
            with integrated_v35_provider(genome=current_genome, config=config):
                with ThreadPoolExecutor(max_workers=min(4, shadow_case_count)) as pool:
                    future_to_case: dict[Any, str] = {}
                    # source 重检
                    for item in provisional_edits:
                        future = pool.submit(
                            _run_detection_with_genome,
                            genome=current_genome,
                            case_id=item.case_id,
                            record=records[item.case_id],
                            config=config,
                            output_dir=batch_root / "sources" / item.case_id.replace("/", "_"),
                            shared_cache_root=shared_cache_root,
                            census_root=census_root,
                            baseline_cache_root=baseline_cache_root,
                            provider_already_patched=True,
                        )
                        future_to_case[future] = item.case_id
                    # canary shadow
                    for canary_id, _ in canary_list:
                        future = pool.submit(
                            _run_detection_with_genome,
                            genome=current_genome,
                            case_id=canary_id,
                            record=records[canary_id],
                            config=config,
                            output_dir=batch_root / "canaries" / "shadow" / canary_id.replace("/", "_"),
                            shared_cache_root=shared_cache_root,
                            census_root=census_root,
                            baseline_cache_root=baseline_cache_root,
                            provider_already_patched=True,
                        )
                        future_to_case[future] = canary_id
                    for future in as_completed(future_to_case):
                        case_id = future_to_case[future]
                        try:
                            shadow_raw[case_id] = future.result()
                        except Exception as exc:
                            shadow_raw[case_id] = exc

        # 阶段2: 并行执行 champion canary 检测（baseline genome，不需要 patch）
        champion_raw: dict[str, Any] = {}
        if canary_list:
            with ThreadPoolExecutor(max_workers=min(4, len(canary_list))) as pool:
                future_to_canary: dict[Any, str] = {}
                for canary_id, _ in canary_list:
                    future = pool.submit(
                        _run_detection_with_genome,
                        genome=champion_genome,
                        case_id=canary_id,
                        record=records[canary_id],
                        config=config,
                        output_dir=batch_root / "canaries" / "champion" / canary_id.replace("/", "_"),
                        shared_cache_root=shared_cache_root,
                        census_root=census_root,
                        baseline_cache_root=baseline_cache_root,
                    )
                    future_to_canary[future] = canary_id
                for future in as_completed(future_to_canary):
                    case_id = future_to_canary[future]
                    try:
                        champion_raw[case_id] = future.result()
                    except Exception as exc:
                        champion_raw[case_id] = exc

        # 处理 source 重检结果（保持原顺序，audit 数据与串行完全一致）
        for item in provisional_edits:
            shadow_result = shadow_raw.get(item.case_id)
            if isinstance(shadow_result, Exception):
                runtime_errors.append(
                    f"source:{item.case_id}:{type(shadow_result).__name__}:{shadow_result}"
                )
                continue
            try:
                shadow_signal = attribute_failure(
                    shadow_result,
                    use_gold=use_gold,
                )
                assessment = assess_source_fitness(
                    item.source_signal,
                    shadow_signal,
                    target_module=item.edit.target_module,
                    operation=item.edit.operation,
                    before_result=item.source_result,
                    after_result=shadow_result,
                    gold_label=fitness_gold(item.case_id),
                )
                source_audits.append({
                    "case_id": item.case_id,
                    "edit_id": item.edit.edit_id,
                    "assessment": assessment.to_dict(),
                    "source_after": shadow_signal.to_dict(),
                })
            except Exception as exc:
                runtime_errors.append(
                    f"source:{item.case_id}:{type(exc).__name__}:{exc}"
                )

        # 处理 canary 结果（保持原顺序）
        for canary_id, cached_state in canary_list:
            champion_result = champion_raw.get(canary_id)
            shadow_result = shadow_raw.get(canary_id)
            if isinstance(champion_result, Exception):
                runtime_errors.append(
                    f"canary:{canary_id}:champion:{type(champion_result).__name__}:{champion_result}"
                )
                continue
            if isinstance(shadow_result, Exception):
                runtime_errors.append(
                    f"canary:{canary_id}:shadow:{type(shadow_result).__name__}:{shadow_result}"
                )
                continue
            try:
                champion_signal = attribute_failure(
                    champion_result,
                    use_gold=False,
                )
                shadow_signal = attribute_failure(
                    shadow_result,
                    use_gold=False,
                )
                gold = fitness_gold(canary_id)
                task_delta: int | None = None
                if gold is not None:
                    task_delta = (
                        task_state_score(shadow_signal.state, gold)
                        - task_state_score(champion_signal.state, gold)
                    )
                canary_audits.append({
                    "case_id": canary_id,
                    "cached_state_provenance_only": cached_state,
                    "champion_state": champion_signal.state,
                    "shadow_state": shadow_signal.state,
                    "champion_should_evolve": champion_signal.should_evolve,
                    "shadow_should_evolve": shadow_signal.should_evolve,
                    "task_score_delta": task_delta,
                })
            except Exception as exc:
                runtime_errors.append(
                    f"canary:{canary_id}:{type(exc).__name__}:{exc}"
                )

        decisions = [
            value["assessment"]["decision"] for value in source_audits
        ]
        improved_count = sum(
            value in {"strong_improvement", "local_improvement"}
            for value in decisions
        )
        worse_count = sum(value == "worse" for value in decisions)
        canary_deltas = [
            value["task_score_delta"]
            for value in canary_audits
            if value["task_score_delta"] is not None
        ]
        canary_gold_passed = (
            not canary_deltas
            or (
                sum(canary_deltas) >= 0
                and all(value > -2 for value in canary_deltas)
            )
        )
        # Canary is audit-only (v5.3): no hard veto.
        # Canary audits are recorded for manual review but do not block
        # batch commit. Gold-assisted source fitness already provides
        # directional correctness checking.
        # v5.3.5 incremental commit: each edit is validated independently.
        # Edits that are worse are isolated and rejected; non-worse edits
        # (improved or no_effect) are committed. This avoids the old behavior
        # where a single worse edit rolled back the entire batch, discarding
        # per-case FIXED edits (especially FN fixes).
        # v5.3.5 modify_rule relaxation: modify_rule is no longer held to the
        # strict required_improvements threshold. As long as worse_count == 0
        # across the batch, all non-worse edits (add or modify) are committed.
        # Rationale: the per-case fitness gate already verified each edit is
        # locally useful or no_effect; batch audit only needs to confirm no
        # regression appears under joint evaluation.
        accepted_indices: list[int] = []
        rejected_indices: list[int] = []
        for i, dec in enumerate(decisions):
            if dec == "worse":
                rejected_indices.append(i)
            else:
                accepted_indices.append(i)
        # Commit policy:
        # - If any runtime_error: reject entire batch (cannot trust audits)
        # - If any source_audit missing: reject entire batch
        # - Otherwise: commit accepted_indices, reject rejected_indices
        audit_complete = len(source_audits) == len(provisional_edits)
        if runtime_errors or not audit_complete:
            passed = False
            accepted_indices = []
            rejected_indices = list(range(len(provisional_edits)))
        elif not accepted_indices:
            # All edits worse or no acceptable edits
            passed = False
        else:
            passed = True
        batch_audit = {
            "batch_id": batch_id,
            "provisional_edit_count": len(provisional_edits),
            "champion_hash_before": champion_genome.content_hash,
            "shadow_hash": current_genome.content_hash,
            "source_audits": source_audits,
            "canary_audits": canary_audits,
            "runtime_errors": runtime_errors,
            "improved_count": improved_count,
            "worse_count": worse_count,
            "accepted_indices": accepted_indices,
            "rejected_indices": rejected_indices,
            "fitness_use_gold": fitness_use_gold,
            "canary_gold_passed": canary_gold_passed,
            "passed": passed,
        }

        # v5.3.5 incremental commit: commit accepted edits, reject worse edits.
        # Champion genome gains only the accepted edits; rejected edits are
        # rolled back individually without discarding the accepted ones.
        if accepted_indices:
            # Rebuild champion genome from accepted edits only.
            # current_genome already contains all provisional edits applied;
            # we cannot partially unapply, so rebuild from champion + accepted.
            new_champion = champion_genome
            # Iterate over a copy to avoid skip-when-remove bug.
            failed_indices: list[int] = []
            for idx in list(accepted_indices):
                item = provisional_edits[idx]
                try:
                    new_champion = apply_edit(new_champion, item.edit)
                except ValueError:
                    # If re-apply fails (e.g. protected rule state changed),
                    # skip this edit and mark as rejected.
                    failed_indices.append(idx)
                    continue
                # Re-capture rule index on the new champion
                if item.edit.operation == "add_rule":
                    item.applied_rule_idx = (
                        len(new_champion.modules[item.edit.target_module].rules) - 1
                    )
            # Move failed re-applies from accepted to rejected.
            for idx in failed_indices:
                accepted_indices.remove(idx)
                rejected_indices.append(idx)
            champion_genome = new_champion
            current_genome = new_champion
            # v5.3.5: snapshot champion after each commit for auditability.
            # Snapshots are saved to <output_dir>/champion_snapshots/<hash>.json
            # and allow tracing which edit produced which genome version.
            snapshot_dir = output_dir / "champion_snapshots"
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            snapshot_path = snapshot_dir / f"{champion_genome.content_hash[:16]}.json"
            if not snapshot_path.exists():
                write_json(snapshot_path, champion_genome.to_dict())
            edits_accepted += len(accepted_indices)
            for idx in accepted_indices:
                item = provisional_edits[idx]
                gate.record_edit_accepted(
                    item.case_index,
                    item.edit.target_module,
                    item.source_signal.error_type.value,
                )
                # Set fitness_status on accepted rules (v5.3 protected modify)
                assessment_decision = (
                    source_audits[idx]["assessment"]["decision"]
                    if idx < len(source_audits) else ""
                )
                fitness_status = (
                    FITNESS_STATUS_STRONG
                    if assessment_decision == "strong_improvement"
                    else FITNESS_STATUS_WEAK
                )
                mod = champion_genome.modules[item.edit.target_module]
                rule_idx = item.applied_rule_idx
                if rule_idx is None and item.edit.rule_index is not None:
                    rule_idx = item.edit.rule_index
                if rule_idx is not None and rule_idx < len(mod.rules):
                    meta = mod.get_rule_meta(rule_idx)
                    meta["fitness_status"] = fitness_status
                    mod.set_rule_meta(rule_idx, meta)
                accepted_edits_history.append({
                    "target_module": item.edit.target_module,
                    "operation": item.edit.operation,
                    "new_content": item.edit.new_content,
                    "case_id": item.case_id,
                    "batch_id": batch_id,
                })
                step = steps[item.step_index]
                step.gate_decision = "accept_batch"
                step.mutation_applied = item.edit.to_dict()
                step.genome_hash_after = champion_genome.content_hash
                step.fitness_check = {
                    **(step.fitness_check or {}),
                    "batch": batch_audit,
                }

        if rejected_indices:
            # Roll back rejected edits individually.
            # If any accepted edits were committed, current_genome already
            # points to the new champion; rejected edits are simply not applied.
            # If no accepted edits, reset current_genome to champion.
            if not accepted_indices:
                current_genome = champion_genome
            edits_rejected += len(rejected_indices)
            reasons = ["shadow_edit_worse"]
            if runtime_errors:
                reasons.append("shadow_batch_runtime_error")
            if not canary_gold_passed:
                reasons.append("gold_assisted_canary_regression")
            for idx in rejected_indices:
                item = provisional_edits[idx]
                gate.revoke_structural_accept(item.certificate, reasons)
                rejected_buffer.append(RejectedEditEntry(
                    proposal=type(
                        "P",
                        (),
                        {
                            "target_module": item.edit.target_module,
                            "operation": item.edit.operation,
                            "content": item.edit.new_content[:80],
                        },
                    )(),
                    regressed_cases=[
                        value["case_id"]
                        for value in canary_audits
                        if (
                            value["task_score_delta"] is not None
                            and value["task_score_delta"] < 0
                        )
                    ],
                    budget_consumed=len(source_audits) + 2 * len(canary_audits),
                ))
                step = steps[item.step_index]
                step.gate_decision = "rollback_batch"
                step.mutation_applied = None
                step.genome_hash_after = champion_genome.content_hash
                step.fitness_check = {
                    **(step.fitness_check or {}),
                    "batch": batch_audit,
                }
        batch_audit["champion_hash_after"] = champion_genome.content_hash
        if accepted_indices and not rejected_indices:
            print(
                f"  [{batch_id}] COMMIT {len(accepted_indices)} edits "
                f"→ {champion_genome.content_hash[:12]}"
            )
        elif accepted_indices and rejected_indices:
            print(
                f"  [{batch_id}] PARTIAL COMMIT {len(accepted_indices)} edits, "
                f"REJECT {len(rejected_indices)} worse edits "
                f"→ {champion_genome.content_hash[:12]}"
            )
        else:
            print(
                f"  [{batch_id}] ROLLBACK {len(rejected_indices)} edits: "
                f"improved={improved_count}, worse={worse_count}, "
                f"runtime_errors={len(runtime_errors)}"
            )

        batch_history.append(batch_audit)
        provisional_edits.clear()
        persist_progress(next_case_index)
        return passed

    persist_progress(0)

    for idx, case_id in enumerate(evolution_case_ids):
        # --- Check halt ---
        if gate.is_halted:
            record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source="halted",
                attribution=None,
                edit_proposal=None,
                mutation_applied=None,
                gate_decision="halted",
                genome_hash_before=current_genome.content_hash,
                genome_hash_after=current_genome.content_hash,
                detection_state="skipped",
                timestamp=time.time(),
            ), next_case_index=idx + 1)
            continue

        genome_before = current_genome.content_hash

        # --- [1] Run detection with current skill (global injection) ---
        try:
            detection_result = _run_detection_with_genome(
                genome=current_genome,
                case_id=case_id,
                record=records[case_id],
                config=config,
                output_dir=output_dir,
                census_root=census_root,
                baseline_cache_root=baseline_cache_root,
                shared_cache_root=shared_cache_root,
            )
        except Exception as exc:
            persist_progress(idx, status="failed")
            _atomic_write_json(
                output_dir / "evolution_failure.json",
                {
                    "schema_version": "agenthallu.bccse_v5_failure.v1",
                    "run_id": run_id,
                    "case_id": case_id,
                    "case_index": idx,
                    "genome_hash": current_genome.content_hash,
                    "error": f"{type(exc).__name__}: {exc}",
                    "checkpoint": str(output_dir / "evolution_checkpoint.json"),
                },
            )
            raise
        detection_state = str(detection_result.get("state") or "")

        # --- [2] Causal process reflection (self-supervised trigger) ---
        signal = attribute_failure(detection_result, use_gold=use_gold)

        # LLM-enhanced attribution (v5.3): when confidence is low,
        # use LLM to refine root-cause module identification.
        if signal.attribution_confidence == "low" and mutation_route is not None:
            from dataclasses import replace as _replace_signal
            from .step_attributor import build_attribution_llm_prompt
            try:
                attr_system, attr_user = build_attribution_llm_prompt(signal)
                attr_parsed, _ = _json_object_provider_call(
                    route=mutation_route,
                    system=attr_system,
                    user=attr_user,
                    cache_dir=output_dir / "mutation_cache",
                )
                if isinstance(attr_parsed, dict):
                    primary = str(attr_parsed.get("primary_module", ""))
                    secondary = str(attr_parsed.get("secondary_module", ""))
                    confidence = str(attr_parsed.get("confidence", "low")).lower()
                    if (
                        primary in MODULE_IDS
                        and confidence in ("high", "medium", "low")
                        and (primary != signal.target_module or secondary != signal.secondary_module)
                    ):
                        signal = _replace_signal(
                            signal,
                            target_module=primary,
                            secondary_module=secondary if secondary in MODULE_IDS else signal.secondary_module,
                            attribution_confidence=confidence,
                        )
            except Exception:
                pass  # Fall back to rule-based attribution

        if not signal.should_evolve:
            # Stable success — no trigger
            stable_cases += 1
            stable_canaries.append((case_id, detection_state))
            gate.record_no_edit()
            record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source="none",
                attribution=signal.to_dict(),
                edit_proposal=None,
                mutation_applied=None,
                gate_decision="skip_stable",
                genome_hash_before=genome_before,
                genome_hash_after=current_genome.content_hash,
                detection_state=detection_state,
                timestamp=time.time(),
            ), next_case_index=idx + 1)
            print(f"  [{idx}] STABLE {case_id}: state={detection_state}")
            continue

        # --- Triggered! ---
        triggered_cases += 1
        gate.record_trigger(idx, signal.target_module, signal.error_type.value)
        print(f"  [{idx}] TRIGGERED {case_id}: {signal.trigger_source.value} "
              f"→ {signal.error_type.value} → {signal.target_module}")

        # --- [3] LLM Causal Process Reflection (PRIMARY PATH) ---
        # The LLM is the core engine of evolution — it reasons about WHY
        # the skill failed and GENERATES a novel edit.
        edit = None
        no_edit_reason = ""
        invalid_mutation_reason = ""

        if mutation_route is not None:
            mutation_cache = output_dir / "mutation_cache"
            mutation_cache.mkdir(parents=True, exist_ok=True)

            # Prepare rich context for LLM
            allowed_modules = tuple(dict.fromkeys(
                (signal.target_module, signal.secondary_module)
            ))
            skill_context = "\n\n".join(
                format_skill_context_for_llm(
                    current_genome.modules,
                    module,
                )
                for module in allowed_modules
            )
            detection_context = format_detection_context_for_llm(detection_result)

            # Do not seed the LLM with legacy CRS/coverage templates. The
            # reflection must derive a runtime-observable rule from the trace.
            template_hint = None

            # Build capacity guidance (when target module is full/near-full)
            capacity_guidance = "\n".join(
                value
                for value in (
                    build_capacity_guidance(current_genome.modules, module)
                    for module in allowed_modules
                )
                if value
            )

            # Build accepted edits summary (diversity guidance)
            accepted_summary = build_accepted_edits_summary(accepted_edits_history)

            # Build the primary causal reflection prompt
            system_prompt, user_prompt = build_causal_reflection_prompt(
                signal=signal,
                current_skill_text=skill_context,
                detection_context=detection_context,
                rejected_buffer=rejected_buffer,
                template_hint=template_hint,
                accepted_edits_summary=accepted_summary,
                capacity_guidance=capacity_guidance,
                allowed_target_modules=allowed_modules,
            )

            # One semantic reflection call per source. Invalid output remains an
            # auditable no-mutation event; it is never "prompted into" an edit.
            try:
                parsed, meta = _json_object_provider_call(
                    route=mutation_route,
                    system=system_prompt,
                    user=user_prompt,
                    cache_dir=mutation_cache,
                )
                target_module = str(parsed.get("target_module") or "")
                if target_module not in allowed_modules:
                    target_module = signal.target_module
                operation = str(parsed.get("operation") or "")
                rule_index = parsed.get("rule_index")
                new_content = str(parsed.get("new_content") or "")
                rationale = str(
                    parsed.get("rationale") or parsed.get("reasoning") or ""
                )

                if operation == "no_edit":
                    no_edit_reason = (
                        rationale[:300] or "LLM found no safe general edit"
                    )
                    print(f"    LLM requested NO_EDIT: {no_edit_reason}")
                elif operation == "remove_rule" and rule_index is not None:
                    edit = SkillEdit(
                        edit_id=f"edit_{uuid.uuid4().hex[:8]}",
                        target_module=target_module,
                        operation=operation,
                        rule_index=int(rule_index),
                        new_content="",
                        rationale=f"[LLM] {rationale[:200]}",
                        source_case_id=case_id,
                    )
                    print(
                        f"    LLM generated: {target_module}/{operation} "
                        f"index={rule_index}"
                    )
                elif (
                    operation in {"add_rule", "modify_rule"}
                    and new_content
                    and len(new_content) >= 10
                ):
                    edit = SkillEdit(
                        edit_id=f"edit_{uuid.uuid4().hex[:8]}",
                        target_module=target_module,
                        operation=operation,
                        rule_index=(
                            int(rule_index) if rule_index is not None else None
                        ),
                        new_content=new_content,
                        rationale=f"[LLM] {rationale[:200]}",
                        source_case_id=case_id,
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
                invalid_mutation_reason = (
                    "single_reflection_runtime_error:"
                    f"{type(exc).__name__}:{exc}"
                )

        if edit is None and no_edit_reason:
            gate.record_no_edit()
            record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source=signal.trigger_source.value,
                attribution=signal.to_dict(),
                edit_proposal={
                    "operation": "no_edit",
                    "rationale": no_edit_reason,
                },
                mutation_applied=None,
                gate_decision="skip_no_edit",
                genome_hash_before=genome_before,
                genome_hash_after=current_genome.content_hash,
                detection_state=detection_state,
                timestamp=time.time(),
            ), next_case_index=idx + 1)
            print(f"  [{idx}] SKIP_NO_EDIT {case_id}: {no_edit_reason}")
            continue

        # --- [3b] No mutation provider or invalid single response ---
        if edit is None:
            print(f"  [{idx}] SKIP_INVALID_MUTATION {case_id}")
            gate.record_no_edit()
            record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source=signal.trigger_source.value,
                attribution=signal.to_dict(),
                edit_proposal={
                    "operation": "invalid_mutation",
                    "rationale": invalid_mutation_reason
                    or "mutation provider unavailable",
                },
                mutation_applied=None,
                gate_decision="skip_invalid_mutation",
                genome_hash_before=genome_before,
                genome_hash_after=current_genome.content_hash,
                detection_state=detection_state,
                timestamp=time.time(),
            ), next_case_index=idx + 1)
            continue

        # --- [4] Structural constraint gate ---
        accepted, cert, reject_reasons = gate.evaluate(
            case_id=case_id,
            case_index=idx,
            target_module=edit.target_module,
            operation=edit.operation,
            content=edit.new_content,
            preconditions=(),
            current_genome=current_genome,
            rule_index=edit.rule_index,
            expected_module=allowed_modules,
        )

        if not accepted:
            # v5.3.5: Handle merge_required signal from tiered dedup.
            # When overlap is in [0.70, 0.95), instead of dropping the edit,
            # route to LLM-assisted merge: convert add_rule → modify_rule on
            # the existing rule, with merged content combining both rules.
            merge_reason = next(
                (r for r in reject_reasons if r.startswith("cross_module_merge_required")
                 or r.startswith("merge_required:")),
                None,
            )
            if merge_reason is not None and edit.operation == "add_rule":
                # Find the existing rule to merge with
                merge_target_module: str | None = None
                merge_rule_index: int | None = None
                for mid in MODULE_IDS:
                    mod = current_genome.modules.get(mid)
                    if not mod or not mod.active:
                        continue
                    for ri, existing_rule in enumerate(mod.rules):
                        if semantic_overlap(existing_rule, edit.new_content) >= 0.70:
                            merge_target_module = mid
                            merge_rule_index = ri
                            break
                    if merge_target_module is not None:
                        break
                if merge_target_module is not None and merge_rule_index is not None:
                    # Build merged content via LLM (or structural fallback)
                    existing_text = current_genome.modules[merge_target_module].rules[merge_rule_index]
                    merged_content = _llm_merge_rules(
                        existing_text,
                        edit.new_content,
                        mutation_route=mutation_route,
                    )
                    if merged_content is not None:
                        # Convert to modify_rule on the existing rule
                        edit = SkillEdit(
                            operation=SkillOp.MODIFY_RULE,
                            target_module=merge_target_module,
                            new_content=merged_content,
                            rule_index=merge_rule_index,
                            edit_id=edit.edit_id,
                            rationale=f"merged: {edit.rationale[:60]}",
                            source_case_id=case_id,
                            is_merge=True,
                        )
                        # Re-run structural gate on the merged edit
                        accepted, cert, reject_reasons = gate.evaluate(
                            case_id=case_id,
                            case_index=idx,
                            target_module=edit.target_module,
                            operation=edit.operation,
                            content=edit.new_content,
                            preconditions=(),
                            current_genome=current_genome,
                            rule_index=edit.rule_index,
                            expected_module={merge_target_module},
                        )
                        if accepted:
                            print(
                                f"  [{idx}] MERGE {case_id}: add_rule converted to "
                                f"modify_rule on {merge_target_module}[{merge_rule_index}]"
                            )
                        else:
                            print(
                                f"  [{idx}] MERGE_FAILED {case_id}: merged edit rejected: "
                                f"{reject_reasons}"
                            )
            if not accepted:
                edits_rejected += 1
                rejected_buffer.append(RejectedEditEntry(
                    proposal=type('P', (), {'target_module': edit.target_module, 'operation': edit.operation, 'content': edit.new_content[:80]})(),
                    regressed_cases=[],
                    budget_consumed=0,
                    reject_reasons=reject_reasons,
                ))
                record_step(EvolutionStep(
                    case_id=case_id,
                    case_index=idx,
                    trigger_source=signal.trigger_source.value,
                    attribution=signal.to_dict(),
                    edit_proposal=edit.to_dict(),
                    mutation_applied=None,
                    gate_decision="reject",
                    genome_hash_before=genome_before,
                    genome_hash_after=current_genome.content_hash,
                    detection_state=detection_state,
                    timestamp=time.time(),
                ), next_case_index=idx + 1)
                print(f"  [{idx}] REJECT {case_id}: {reject_reasons}")
                continue

        # --- [5] Apply edit ---
        try:
            candidate_genome = apply_edit(current_genome, edit)
            # v5.3.4: capture the rule index this edit produced, for batch commit
            if edit.operation == "add_rule":
                _applied_rule_idx = len(candidate_genome.modules[edit.target_module].rules) - 1
            elif edit.rule_index is not None:
                _applied_rule_idx = edit.rule_index
            else:
                _applied_rule_idx = None
        except ValueError as e:
            print(f"  [{idx}] EDIT_ERROR {case_id}: {e}")
            gate.revoke_structural_accept(cert, [f"apply_edit_failed:{e}"])
            edits_rejected += 1
            record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source=signal.trigger_source.value,
                attribution=signal.to_dict(),
                edit_proposal=edit.to_dict(),
                mutation_applied=None,
                gate_decision="reject_apply",
                genome_hash_before=genome_before,
                genome_hash_after=current_genome.content_hash,
                detection_state=detection_state,
                timestamp=time.time(),
            ), next_case_index=idx + 1)
            continue

        fitness_audit: dict[str, Any] | None = None
        if fitness_check:
            replay_root = output_dir / "fitness_replay" / f"{idx:03d}_{case_id.replace('/', '_')}"
            try:
                candidate_result = _run_detection_with_genome(
                    genome=candidate_genome,
                    case_id=case_id,
                    record=records[case_id],
                    config=config,
                    output_dir=replay_root / "source",
                    census_root=census_root,
                    baseline_cache_root=baseline_cache_root,
                    shared_cache_root=shared_cache_root,
                )
            except Exception as exc:
                reason = f"source_replay_runtime_error:{type(exc).__name__}:{exc}"
                fitness_audit = {
                    "source_before": signal.to_dict(),
                    "source_after": None,
                    "source_improved": False,
                    "source_stable_after": False,
                    "canaries_checked": 0,
                    "canary_regressions": [],
                    "runtime_error": reason,
                    "passed": False,
                }
                gate.revoke_structural_accept(cert, [reason])
                edits_rejected += 1
                record_step(EvolutionStep(
                    case_id=case_id,
                    case_index=idx,
                    trigger_source=signal.trigger_source.value,
                    attribution=signal.to_dict(),
                    edit_proposal=edit.to_dict(),
                    mutation_applied=None,
                    gate_decision="reject_fitness_runtime",
                    genome_hash_before=genome_before,
                    genome_hash_after=current_genome.content_hash,
                    detection_state=detection_state,
                    timestamp=time.time(),
                    fitness_check=fitness_audit,
                ), next_case_index=idx + 1)
                print(f"  [{idx}] REJECT_FITNESS_RUNTIME {case_id}: {reason}")
                continue
            candidate_signal = attribute_failure(
                candidate_result,
                use_gold=use_gold,
            )
            assessment = assess_source_fitness(
                signal,
                candidate_signal,
                target_module=edit.target_module,
                operation=edit.operation,
                before_result=detection_result,
                after_result=candidate_result,
                gold_label=fitness_gold(case_id),
            )
            fitness_audit = {
                "source_before": signal.to_dict(),
                "source_after": candidate_signal.to_dict(),
                "assessment": assessment.to_dict(),
                "source_stable_after": not candidate_signal.should_evolve,
                "canaries_checked": 0,
                "canary_regressions": [],
                "passed": assessment.passed,
            }
            if not assessment.passed:
                # v5.3.3: no_effect add_rule is harmless - allow into shadow.
                # The rule adds information without removing anything; batch
                # validation will confirm or deny at commit time.
                if (
                    assessment.decision == "no_effect"
                    and edit.operation == "add_rule"
                ):
                    print(
                        f"    [v5.3.3] no_effect add_rule admitted to shadow: "
                        f"{assessment.reasons[0] if assessment.reasons else ''}"
                    )
                    # Fall through to provisional path (do NOT continue)
                else:
                    reasons = [
                        f"source_{assessment.decision}:"
                        + ",".join(assessment.reasons)
                    ]
                    gate.revoke_structural_accept(cert, reasons)
                    edits_rejected += 1
                    rejected_buffer.append(RejectedEditEntry(
                        proposal=type(
                            "P",
                            (),
                            {
                                "target_module": edit.target_module,
                                "operation": edit.operation,
                                "content": edit.new_content[:80],
                            },
                        )(),
                        regressed_cases=[],
                        budget_consumed=1,
                    ))
                    record_step(EvolutionStep(
                        case_id=case_id,
                        case_index=idx,
                        trigger_source=signal.trigger_source.value,
                        attribution=signal.to_dict(),
                        edit_proposal=edit.to_dict(),
                        mutation_applied=None,
                        gate_decision="reject_fitness",
                        genome_hash_before=genome_before,
                        genome_hash_after=current_genome.content_hash,
                        detection_state=detection_state,
                        timestamp=time.time(),
                        fitness_check=fitness_audit,
                    ), next_case_index=idx + 1)
                    print(f"  [{idx}] REJECT_LOCAL_FITNESS {case_id}: {reasons}")
                    continue

        if not fitness_check:
            # Non-formal diagnostic compatibility path. Formal CLI runs require
            # fitness_check=True.
            champion_genome = candidate_genome
            current_genome = candidate_genome
            edits_accepted += 1
            gate.record_edit_accepted(
                idx,
                edit.target_module,
                signal.error_type.value,
            )
            accepted_edits_history.append({
                "target_module": edit.target_module,
                "operation": edit.operation,
                "new_content": edit.new_content,
                "case_id": case_id,
                "batch_id": "fitness_disabled",
            })
            record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source=signal.trigger_source.value,
                attribution=signal.to_dict(),
                edit_proposal=edit.to_dict(),
                mutation_applied=edit.to_dict(),
                gate_decision="accept_fitness_disabled",
                genome_hash_before=genome_before,
                genome_hash_after=current_genome.content_hash,
                detection_state=detection_state,
                timestamp=time.time(),
                fitness_check=None,
            ), next_case_index=idx + 1)
            continue

        # Admit locally useful edits to a reversible shadow genome. Champion is
        # unchanged until flush_shadow_batch commits the batch.
        current_genome = candidate_genome
        step_index = len(steps)
        provisional_edits.append(ProvisionalEdit(
            case_id=case_id,
            case_index=idx,
            edit=edit,
            certificate=cert,
            applied_rule_idx=_applied_rule_idx,
            source_signal=signal,
            source_result=detection_result,
            individual_assessment=assessment,
            step_index=step_index,
        ))
        record_step(EvolutionStep(
            case_id=case_id,
            case_index=idx,
            trigger_source=signal.trigger_source.value,
            attribution=signal.to_dict(),
            edit_proposal=edit.to_dict(),
            mutation_applied=None,
            gate_decision="provisional",
            genome_hash_before=genome_before,
            genome_hash_after=current_genome.content_hash,
            detection_state=detection_state,
            timestamp=time.time(),
            fitness_check=fitness_audit,
        ), next_case_index=idx + 1)
        print(
            f"  [{idx}] PROVISIONAL {case_id}: "
            f"{edit.target_module}/{edit.operation} "
            f"({assessment.decision}; shadow={len(provisional_edits)})"
        )
        flush_shadow_batch(next_case_index=idx + 1)

    # --- Final summary ---
    flush_shadow_batch(
        next_case_index=len(evolution_case_ids),
        force=True,
    )
    duration = time.time() - start_time
    result = OnlineEvolutionResult(
        run_id=run_id,
        champion_genome=champion_genome,
        steps=steps,
        gate_summary=gate.summary(),
        total_cases=len(evolution_case_ids),
        triggered_cases=triggered_cases,
        edits_accepted=edits_accepted,
        edits_rejected=edits_rejected,
        stable_cases=stable_cases,
        use_gold=use_gold,
        halted=gate.is_halted,
        halt_reason=gate.halt_reason,
        duration_seconds=duration,
    )

    # Save results
    write_json(output_dir / "evolution_result.json", result.to_dict())
    write_json(output_dir / "champion_genome.json", champion_genome.to_dict())
    persist_progress(len(evolution_case_ids), status="complete")

    print(f"\n[{run_id}] Complete in {duration:.1f}s:")
    print(f"  Cases: {len(evolution_case_ids)} total, {triggered_cases} triggered, "
          f"{stable_cases} stable")
    print(f"  Edits: {edits_accepted} accepted, {edits_rejected} rejected")
    print(f"  Halted: {gate.is_halted} ({gate.halt_reason})")
    print(f"  Champion: {champion_genome.content_hash[:16]}...")

    return result
