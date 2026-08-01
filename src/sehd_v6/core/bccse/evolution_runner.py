"""BCCSE v5 — Per-case online skill evolution runner (refactored).

Main orchestration loop for causal-process-reflection-driven online skill evolution.
Processes cases sequentially with CONDITION-TRIGGERED evolution:
- Not every case triggers evolution (only "informative" cases do)
- Self-supervised signals drive reflection (no gold label required)
- Structural constraints plus bounded stable-case canaries gate promotion
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
import hashlib
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..paca_tcl import proof_engine as v35
from ..paca_tcl.skill_integration import integrated_v35_provider
from ..paca_tcl.basic_runner import (
    json_object_provider_call,
    write_json,
    write_jsonl,
)
from .reflection_engine import (
    RejectedEditEntry,
    build_causal_reflection_prompt,
    build_capacity_guidance,
    build_accepted_edits_summary,
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
    FITNESS_STATUS_NONE,
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
from .signal_adapter import (
    decision_authority_invalid,
    resolve_authoritative_state,
)
from ..htc_tes import (
    HallucinationType,
    HallucinationTypeClassifier,
    TypeConditionedEvolution,
    TypeAwareFitness,
    classify_hallucination_type,
)
from ..provider_models import canonical_model_name

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
    evolution_audit: dict[str, Any] = field(default_factory=dict)

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
            "evolution_audit": self.evolution_audit,
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
    source_after_signal: AttributionSignal
    source_after_result: dict[str, Any]
    individual_assessment: FitnessAssessment
    step_index: int
    applied_rule_idx: int | None = None  # v5.3.4: rule index at apply time
    canary_audits: list[dict[str, Any]] = field(default_factory=list)


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
                try:
                    link_path.symlink_to(target, target_is_directory=True)
                except (FileExistsError, OSError):
                    # FIX (C8): Windows 上无管理员权限/未开发者模式时
                    # symlink_to 抛 OSError；降级为直接使用本地目录（不共享
                    # 缓存，但功能正确）。FileExistsError 为并发创建，安全忽略。
                    pass
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
    if decision_authority_invalid(result):
        raise RuntimeError("detector returned invalid decision authority")
    try:
        resolve_authoritative_state(result)
    except ValueError as exc:
        raise RuntimeError(f"detector returned invalid state: {exc}") from exc
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


_EVALUATOR_ONLY_FIELDS = frozenset({
    "gold_label",
    "gold_explanation",
    "is_hallucination",
    "hallucination_step",
    "hallucination_reason",
    "outer_feedback",
})


def _detector_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the detector envelope with evaluator-only labels removed."""
    return {
        key: value
        for key, value in record.items()
        if key not in _EVALUATOR_ONLY_FIELDS
    }


def _evaluator_gold(record: Mapping[str, Any]) -> bool | None:
    """Join a label only inside an evaluator; never pass it to detection."""
    value = record.get("gold_label")
    if isinstance(value, bool):
        return value
    value = record.get("is_hallucination")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.lower().strip()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


# ============================================================
# Main evolution loop
# ============================================================


def _llm_merge_rules(
    existing_rule: str,
    new_rule: str,
    *,
    mutation_route: dict[str, Any] | None,
    cache_dir: Path | None = None,
) -> str | None:
    """Merge two semantically-overlapping rules into a single rule.

    Uses LLM-assisted merge when a mutation_route is available; falls back to
    structural merge (newline-separated concatenation) otherwise.

    Args:
        existing_rule: The existing rule text to merge into.
        new_rule: The new rule text carrying additional conditions.
        mutation_route: LLM route for reflection calls (None = structural only).
        cache_dir: Cache directory for LLM merge calls. Defaults to a
            relative ``.skill_merge_cache`` only as a last resort; callers
            should pass ``output_dir / "skill_merge_cache"``.

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
        merged, _ = json_object_provider_call(
            route=mutation_route,
            purpose="mutation",
            system=system_prompt,
            user=user_prompt,
            cache_dir=cache_dir if cache_dir is not None else Path(".skill_merge_cache"),
        )
        if merged and isinstance(merged, dict):
            rule = merged.get("merged_rule", "").strip()
            if rule and len(rule) <= 120:
                return rule
    except Exception:
        pass
    # Fallback to structural merge
    return _llm_merge_rules(existing_rule, new_rule, mutation_route=None)


def run_online_evolution(
    *,
    evolution_case_ids: Sequence[str],
    records: Mapping[str, dict[str, Any]],
    config: dict[str, Any],
    output_dir: Path,
    census_root: Path,
    use_gold: bool = False,
    mutation_route: dict[str, Any] | None = None,
    baseline_cache_root: Path | None = None,
    module_cooldown: int = 0,
    max_total_edits: int = 200,
    convergence_window: int = 20,
    fitness_check: bool = True,
    fitness_batch_size: int = 1,
    fitness_use_gold: bool = False,
    experience_memory_path: Path | None = None,
    canary_limit: int = 2,
    validation_probe_every: int = 0,
    probe_ids: Sequence[str] = (),
    probe_limit: int = 2,
    validation_probe_trigger_threshold: int = 0,
    initial_genome: SkillGenome | None = None,
    run_id: str | None = None,
) -> OnlineEvolutionResult:
    """Run per-case online evolution with condition-triggered self-supervised reflection.

    Batch commit requires per-case source replay and preservation of a rolling
    set of previously stable development cases. The separately registered
    gold probe is opened only by its budgeted gate; the final holdout remains
    unopened during evolution and is the authoritative cross-set check.
    Self-supervised signals may advance only an unprotected provisional working
    candidate after independent canary non-regression. Protected promotion and
    accuracy claims require explicit evaluation authority outside this run.

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
        fitness_batch_size: Provisional edits accumulated before batch commit.
            Default 1 (incremental commit) — each edit is validated and committed
            independently, avoiding same-module edit interference in batch audit.
            Set >1 to batch multiple edits (faster but risks batch rollback).
        fitness_use_gold: Use labels only inside fitness selection. Labels never
            enter attribution or the reflection prompt unless use_gold is also
            explicitly enabled.
        experience_memory_path: Optional prior HTC-TES memory artifact. Loaded
            before evolution so confirmed observations can guide reflection.
        canary_limit: Maximum sampled stable evolution cases replayed for
            local non-regression on each edit. Default 2.
        validation_probe_every: Run the independent gold development probe
            after each N accepted edits. Zero disables this trigger.
        probe_ids: Pre-registered development-probe case IDs. These are never
            inferred from validation, test, or outer partitions.
        probe_limit: Maximum pre-registered probe cases evaluated per probe.
        validation_probe_trigger_threshold: Also run the development probe
            after this many newly triggered source cases. Zero disables it.
        initial_genome: Optional audited champion to continue evolving. When
            omitted, evolution starts from the frozen baseline genome.
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
    if probe_limit < 0 or probe_limit > 20:
        raise ValueError("probe_limit must be between 0 and 20")
    if len(probe_ids) != len(set(probe_ids)):
        raise ValueError("probe_ids contains duplicates")
    overlapping_probe_ids = sorted(set(probe_ids).intersection(evolution_case_ids))
    if overlapping_probe_ids:
        raise ValueError(
            "probe_ids must be disjoint from evolution_case_ids: "
            + ", ".join(overlapping_probe_ids)
        )
    missing_probe_records = [case_id for case_id in probe_ids if case_id not in records]
    if missing_probe_records:
        raise ValueError("Probe records missing: " + ", ".join(missing_probe_records))
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
    if mutation_route is not None:
        mutation_route = dict(mutation_route)
        model = str(mutation_route.get("model") or "")
        mutation_route["model"] = canonical_model_name(model)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # v5.3.4b-D2: shared LLM cache root for all replays of the same genome+case
    shared_cache_root = output_dir / "shared_cache"
    shared_cache_root.mkdir(parents=True, exist_ok=True)
    start_time = time.time()

    if run_id is None:
        run_id = f"bccse_v5_{uuid.uuid4().hex[:8]}"

    # Initialize
    champion_genome = (
        initial_genome.clone() if initial_genome is not None
        else create_baseline_genome()
    )
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
    probe_history: list[dict[str, Any]] = []
    budget_usage = {
        "source_replays": 0,
        "canary_cases": 0,
        "probe_cases": 0,
        "provider_calls": 0,
    }
    rollback_reason = ""
    stable_genome = champion_genome.clone()
    pending_probe_step_indexes: list[int] = []
    pending_probe_commits: list[ProvisionalEdit] = []
    stable_history_length = 0
    last_probe_accepted_count = 0
    last_probe_triggered_count = 0

    # HTC-TES v3: type-conditioned evolution components
    type_classifier = HallucinationTypeClassifier()
    type_evolution = TypeConditionedEvolution()
    type_fitness = TypeAwareFitness()
    if experience_memory_path is not None:
        memory_path = Path(experience_memory_path)
        if not memory_path.is_file():
            raise FileNotFoundError(f"Experience memory not found: {memory_path}")
        with memory_path.open(encoding="utf-8") as handle:
            memory_payload = json.load(handle)
        type_fitness.get_experience_memory().from_dict(memory_payload)
    stable_memory_payload = type_fitness.get_experience_memory().to_dict()

    # A rolling set of label-free, previously stable cases protects decisive
    # behavior from catastrophic forgetting. These are evolution cases only;
    # validation and outer partitions remain unopened.
    stable_canaries: dict[str, dict[str, Any]] = {}
    stable_canaries_checkpoint: dict[str, dict[str, Any]] = {}

    def canary_preserved(
        parent_result: Mapping[str, Any],
        child_result: Mapping[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Require both verdict and process stability on a replay anchor."""
        try:
            parent_state = resolve_authoritative_state(parent_result)
            child_state = resolve_authoritative_state(child_result)
        except ValueError as exc:
            return False, {
                "before_state": None,
                "after_state": None,
                "preserved": False,
                "error": f"canary_state:{exc}",
            }
        try:
            parent_signal = attribute_failure(parent_result, use_gold=False)
            child_signal = attribute_failure(child_result, use_gold=False)
            parent_triggered = parent_signal.should_evolve
            child_triggered = child_signal.should_evolve
        except Exception as exc:
            return False, {
                "before_state": parent_state,
                "after_state": child_state,
                "preserved": False,
                "error": f"canary_attribution:{type(exc).__name__}:{exc}",
            }
        preserved = (
            parent_state == child_state
            and bool(child_result.get("runtime_valid"))
            and not parent_triggered
            and not child_triggered
        )
        return preserved, {
            "before_state": parent_state,
            "after_state": child_state,
            "before_should_evolve": parent_triggered,
            "after_should_evolve": child_triggered,
            "runtime_valid": bool(child_result.get("runtime_valid")),
            "preserved": preserved,
        }

    print(f"[{run_id}] Starting online evolution: {len(evolution_case_ids)} cases")
    print(f"  Config: use_gold={use_gold}, cooldown={module_cooldown}, "
          f"max_edits={max_total_edits}, convergence_window={convergence_window}")
    print(f"  HTC-TES v3: type-conditioned evolution enabled")

    stable_cases = 0
    triggered_cases = 0
    edits_accepted = 0
    edits_rejected = 0

    def run_detection(
        *,
        budget_kind: str,
        genome: SkillGenome,
        case_id: str,
        output_path: Path,
    ) -> dict[str, Any]:
        """Run one detector call in a counted, label-free envelope."""
        budget_usage["provider_calls"] += 1
        budget_field = {
            "source": "source_replays",
            "canary": "canary_cases",
            "probe": "probe_cases",
        }.get(budget_kind)
        if budget_field is not None:
            # Debit before dispatch so runtime-invalid attempts are auditable.
            budget_usage[budget_field] += 1
        return _run_detection_with_genome(
            genome=genome,
            case_id=case_id,
            record=_detector_record(records[case_id]),
            config=config,
            output_dir=output_path,
            census_root=census_root,
            baseline_cache_root=baseline_cache_root,
            shared_cache_root=shared_cache_root,
        )

    def attribution_for(
        result: Mapping[str, Any],
        case_id: str,
    ) -> AttributionSignal:
        """Join gold to attribution only under the explicit use_gold opt-in."""
        payload = dict(result)
        if use_gold:
            gold = _evaluator_gold(records[case_id])
            if gold is not None:
                payload["gold_label"] = gold
        return attribute_failure(payload, use_gold=use_gold)

    def selected_canaries(edit: SkillEdit) -> list[tuple[str, dict[str, Any]]]:
        """Deterministically sample a small risk-adaptive canary set."""
        if not stable_canaries or canary_limit == 0:
            return []
        requested = canary_limit
        if edit.operation == SkillOp.ADD_RULE:
            requested = min(requested, 1)
        ranked = sorted(
            stable_canaries.items(),
            key=lambda item: hashlib.sha256(
                f"{current_genome.content_hash}:{edit.edit_id}:{item[0]}".encode(
                    "utf-8"
                )
            ).hexdigest(),
        )
        return ranked[:requested]

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
                "schema_version": "agenthallu.bccse_v5_checkpoint.v2",
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
                "accepted_edits_history": accepted_edits_history,
                "batch_history": batch_history,
                "probe_history": probe_history,
                "budget_usage": dict(budget_usage),
                "rollback_reason": rollback_reason,
                "stable_genome_hash": stable_genome.content_hash,
                "pending_probe_step_indexes": list(pending_probe_step_indexes),
                "counters": {
                    "stable_cases": stable_cases,
                    "triggered_cases": triggered_cases,
                    "edits_accepted": edits_accepted,
                    "edits_rejected": edits_rejected,
                },
                "updated_at": time.time(),
            },
        )
        _atomic_write_json(
            output_dir / "htc_tes_experience_memory.json",
            type_fitness.get_experience_memory().to_dict(),
        )

    def record_step(step: EvolutionStep, *, next_case_index: int) -> None:
        steps.append(step)
        persist_progress(next_case_index)

    def fitness_gold(case_id: str) -> bool | None:
        if not fitness_use_gold:
            return None
        return _evaluator_gold(records[case_id])

    def run_development_probe(*, reason: str) -> tuple[bool, dict[str, Any]]:
        """Apply the independent development envelope as a veto-only gate."""
        nonlocal champion_genome, current_genome, stable_genome
        nonlocal edits_accepted, edits_rejected, rollback_reason
        nonlocal last_probe_accepted_count, last_probe_triggered_count
        nonlocal stable_history_length
        nonlocal stable_memory_payload, stable_canaries_checkpoint

        before_budget = dict(budget_usage)
        audit: dict[str, Any] = {
            "probe_index": len(probe_history),
            "reason": reason,
            "genome_hash": champion_genome.content_hash,
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
                    genome=champion_genome,
                    case_id=probe_id,
                    output_path=(
                        output_dir
                        / "development_probes"
                        / f"probe_{len(probe_history):03d}"
                        / probe_id.replace("/", "_")
                    ),
                )
                state = resolve_authoritative_state(result)
                runtime_valid = result.get("runtime_valid") is True
                authority_invalid = decision_authority_invalid(result)
                if gold is None:
                    # Unlabelled probe mode is deliberately process-only: it
                    # can veto runtime/authority loss, but it cannot score a
                    # clean/error polarity or provide correctness evidence.
                    score = None
                    passed = runtime_valid and not authority_invalid
                else:
                    score = task_state_score(state, gold)
                    passed = runtime_valid and not authority_invalid and score == 2
                audit["case_audits"].append({
                    "case_id": probe_id,
                    "state": state,
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
        last_probe_triggered_count = triggered_cases

        if failures:
            rollback_reason = "development_probe_failed:" + "|".join(failures)
            audit["passed"] = False
            audit["rollback_reason"] = rollback_reason
            rolled_back = len(pending_probe_commits)
            audit["rolled_back_edits"] = rolled_back
            champion_genome = stable_genome.clone()
            current_genome = stable_genome.clone()
            type_fitness.get_experience_memory().from_dict(stable_memory_payload)
            stable_canaries.clear()
            stable_canaries.update(stable_canaries_checkpoint)
            for item in pending_probe_commits:
                gate.revoke_structural_accept(item.certificate, [rollback_reason])
                gate.record_no_edit()
                step = steps[item.step_index]
                step.gate_decision = "rollback_probe"
                step.mutation_applied = None
                step.genome_hash_after = stable_genome.content_hash
                step.fitness_check = {
                    **(step.fitness_check or {}),
                    "development_probe": audit,
                }
            edits_accepted -= rolled_back
            edits_rejected += rolled_back
            del accepted_edits_history[stable_history_length:]
            pending_probe_commits.clear()
            pending_probe_step_indexes.clear()
            last_probe_accepted_count = edits_accepted
        else:
            rollback_reason = ""
            audit["rolled_back_edits"] = 0
            stable_genome = champion_genome.clone()
            stable_history_length = len(accepted_edits_history)
            stable_memory_payload = type_fitness.get_experience_memory().to_dict()
            stable_canaries_checkpoint = dict(stable_canaries)
            for item in pending_probe_commits:
                step = steps[item.step_index]
                step.fitness_check = {
                    **(step.fitness_check or {}),
                    "development_probe": audit,
                }
            pending_probe_commits.clear()
            pending_probe_step_indexes.clear()
            last_probe_accepted_count = edits_accepted

        probe_history.append(audit)
        return not failures, audit

    def flush_shadow_batch(*, next_case_index: int, force: bool = False) -> bool:
        """Commit or roll back the current reversible shadow batch."""
        nonlocal champion_genome, current_genome
        nonlocal edits_accepted, edits_rejected

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
        # v5.3.5 incremental commit: each edit is validated independently.
        # Edits that are worse are isolated and rejected; non-worse edits
        # (improved or no_effect) are committed. This avoids the old behavior
        # where a single worse edit rolled back the entire batch, discarding
        # per-case FIXED edits (especially FN fixes).
        # v5.3.5 modify_rule relaxation: modify_rule is no longer held to the
        # strict required_improvements threshold. As long as worse_count == 0
        # across the batch, all non-worse edits (add or modify) are committed.
        # Rationale: only an explicit source improvement is promotable. A
        # no-effect edit is retained as telemetry, not silently committed.
        accepted_indices: list[int] = []
        rejected_indices: list[int] = []
        for i, dec in enumerate(decisions):
            # Label-free directional evidence may advance the reversible
            # working genome only when an independent sampled canary proves
            # process non-regression. It remains explicitly provisional and
            # cannot acquire protected fitness status or an accuracy claim.
            label_free_provisional = (
                not fitness_use_gold
                and dec == "directional_evidence"
                and bool(provisional_edits[i].canary_audits)
                and all(
                    bool(audit.get("preserved"))
                    for audit in provisional_edits[i].canary_audits
                )
            )
            if dec in {"strong_improvement", "local_improvement"} or label_free_provisional:
                accepted_indices.append(i)
            else:
                rejected_indices.append(i)
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
            "provider_calls": len(source_audits) + len(batch_canary_audits),
            "rollback_reason": "",
            "promotion_eligible": bool(fitness_use_gold),
            "claim_scope": (
                "gold_development_candidate"
                if fitness_use_gold
                else "label_free_provisional_telemetry"
            ),
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
            edits_accepted += len(accepted_indices)
            for idx in accepted_indices:
                item = provisional_edits[idx]
                # Set fitness_status on accepted rules (v5.3 protected modify)
                assessment_decision = (
                    source_audits[idx]["assessment"]["decision"]
                    if idx < len(source_audits) else ""
                )
                if not fitness_use_gold:
                    fitness_status = FITNESS_STATUS_NONE
                else:
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
                    meta["promotion_status"] = (
                        "provisional_label_free"
                        if not fitness_use_gold
                        else "gold_development_candidate"
                    )
                    mod.set_rule_meta(rule_idx, meta)
                accepted_edits_history.append({
                    "edit_id": item.edit.edit_id,
                    "target_module": item.edit.target_module,
                    "operation": item.edit.operation,
                    "new_content": item.edit.new_content,
                    "case_id": item.case_id,
                    "batch_id": batch_id,
                    "promotion_status": (
                        "provisional_label_free"
                        if not fitness_use_gold
                        else "gold_development_candidate"
                    ),
                })
                pending_probe_commits.append(item)
                pending_probe_step_indexes.append(item.step_index)
                # HTC-TES v3: Record type-conditioned experience
                # FIX: pass gold_label (was None) and target_module (was missing)
                # to ensure experience memory records correct direction and module.
                _type_result = type_classifier.classify(
                    item.source_result, item.source_signal
                )
                _direction = type_evolution._infer_evolution_direction(
                    item.source_signal.state, item.source_signal.gold_label,
                )
                # FIX (C1): 使用BCCSE的assessment决策作为成功标志，并传入
                # 真正的检测结果dict（shadow_result），而非AttributionSignal
                # 的序列化结果，避免HTC-TES内部误判before/after状态。
                _htc_success = assessment_decision in {
                    "strong_improvement", "local_improvement",
                }
                _shadow_result = source_audits[idx].get("shadow_result", {})
                type_fitness.assess_edit(
                    source_case_id=item.case_id,
                    hallucination_type=_type_result.primary_type,
                    evolution_direction=_direction,
                    detection_before=item.source_result,
                    detection_after=_shadow_result,
                    type_result=_type_result,
                    target_module=item.edit.target_module,
                    override_success=_htc_success,
                    feature_description=item.edit.feature_description,
                )
                # A successfully repaired source becomes a future regression
                # anchor, so later edits cannot silently forget the fix.
                if (
                    resolve_authoritative_state(_shadow_result)
                    in {"verified_error", "verified_clean"}
                    and not bool(source_audits[idx]["source_after"].get("should_evolve"))
                ):
                    stable_canaries[item.case_id] = _shadow_result
                    while len(stable_canaries) > max(8, canary_limit * 4):
                        stable_canaries.pop(next(iter(stable_canaries)))
                step = steps[item.step_index]
                step.gate_decision = "accept_batch"
                step.mutation_applied = item.edit.to_dict()
                step.genome_hash_after = champion_genome.content_hash
                step.fitness_check = {
                    **(step.fitness_check or {}),
                    "batch": batch_audit,
                }

            # Snapshot only after fitness metadata and experience have been
            # attached, so artifacts with the same hash have identical audit
            # semantics.
            snapshot_dir = output_dir / "champion_snapshots"
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            snapshot_path = snapshot_dir / f"{champion_genome.content_hash[:16]}.json"
            write_json(snapshot_path, champion_genome.to_dict())

        if not accepted_indices:
            passed = False
            batch_audit["passed"] = False

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
            for idx in rejected_indices:
                item = provisional_edits[idx]
                # Preserve negative learning signal as well. A batch-level
                # canary failure or a shadow replay rejection is valuable
                # evidence for avoiding the same strategy later.
                if idx < len(source_audits):
                    rejected_result = source_audits[idx].get("shadow_result")
                    if isinstance(rejected_result, Mapping):
                        try:
                            rejected_type = type_classifier.classify(
                                item.source_result, item.source_signal,
                            )
                            type_fitness.assess_edit(
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
                            )
                        except Exception as exc:
                            runtime_errors.append(
                                f"experience:{item.case_id}:{type(exc).__name__}:{exc}"
                            )
                gate.revoke_structural_accept(item.certificate, reasons)
                # One processed case produced no effective committed edit.
                # Structural acceptance had reset convergence, so restore the
                # case-level no-edit signal exactly once after replay rejects.
                gate.record_no_edit()
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
                    regressed_cases=[],
                    budget_consumed=len(source_audits),
                ))
                step = steps[item.step_index]
                step.gate_decision = "rollback_batch"
                step.mutation_applied = None
                step.genome_hash_after = champion_genome.content_hash
                step.fitness_check = {
                    **(step.fitness_check or {}),
                    "batch": batch_audit,
                }

        probe_due_reason = ""
        if accepted_indices and registered_probe_ids:
            if (
                validation_probe_every > 0
                and edits_accepted - last_probe_accepted_count
                >= validation_probe_every
            ):
                probe_due_reason = "accepted_edit_cadence"
            elif (
                validation_probe_trigger_threshold > 0
                and triggered_cases - last_probe_triggered_count
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

    persist_progress(0)

    for idx, case_id in enumerate(evolution_case_ids):
        # --- Check halt (including convergence) ---
        if gate.is_halted or getattr(gate, "_converged", False):
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
            detection_result = run_detection(
                budget_kind="case",
                genome=current_genome,
                case_id=case_id,
                output_path=output_dir,
            )
        except Exception as exc:
            _atomic_write_json(
                output_dir / "runtime_failures" / f"{idx:04d}.json",
                {
                    "schema_version": "agenthallu.bccse_v5_failure.v1",
                    "run_id": run_id,
                    "case_id": case_id,
                    "case_index": idx,
                    "genome_hash": current_genome.content_hash,
                    "error": f"{type(exc).__name__}: {exc}",
                    "action": "case_skipped",
                },
            )
            gate.record_no_edit()
            record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source="runtime_invalid",
                attribution=None,
                edit_proposal=None,
                mutation_applied=None,
                gate_decision="skip_runtime_invalid",
                genome_hash_before=genome_before,
                genome_hash_after=current_genome.content_hash,
                detection_state="runtime_invalid",
                timestamp=time.time(),
                fitness_check={
                    "runtime_error": f"{type(exc).__name__}: {exc}",
                    "passed": False,
                },
            ), next_case_index=idx + 1)
            print(f"  [{idx}] RUNTIME_INVALID {case_id}: {type(exc).__name__}: {exc}")
            continue
        detection_state = resolve_authoritative_state(detection_result)

        # --- [2] Causal process reflection (self-supervised trigger) ---
        signal = attribution_for(detection_result, case_id)

        # LLM-enhanced attribution (v5.3): when confidence is low,
        # use LLM to refine root-cause module identification.
        if signal.attribution_confidence == "low" and mutation_route is not None:
            from dataclasses import replace as _replace_signal
            from .step_attributor import build_attribution_llm_prompt
            try:
                attr_system, attr_user = build_attribution_llm_prompt(signal)
                attr_parsed, _ = json_object_provider_call(
                    route=mutation_route,
                    purpose="mutation",
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
            stable_canaries[case_id] = detection_result
            while len(stable_canaries) > max(8, canary_limit * 4):
                stable_canaries.pop(next(iter(stable_canaries)))
            if not pending_probe_commits:
                stable_canaries_checkpoint = dict(stable_canaries)
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
        print(f"  [{idx}] TRIGGERED {case_id}: {signal.trigger_source.value} "
              f"→ {signal.error_type.value} → {signal.target_module}")

        # --- HTC-TES v3: Classify hallucination type ---
        type_result = type_classifier.classify(detection_result, signal)
        evolution_direction = type_evolution._infer_evolution_direction(
            signal.state, signal.gold_label,
        )
        type_allowed_modules = type_evolution.intersect_allowed_modules(
            type_result.primary_type,
            (signal.target_module, signal.secondary_module),
            secondary_type=type_result.secondary_type,
        )
        if not type_allowed_modules:
            gate.record_no_edit()
            record_step(EvolutionStep(
                case_id=case_id, case_index=idx,
                trigger_source=signal.trigger_source.value,
                attribution=signal.to_dict(), edit_proposal={
                    "operation": "no_edit",
                    "reason": "type_attribution_route_intersection_empty",
                    "hallucination_type": type_result.primary_type.value,
                }, mutation_applied=None, gate_decision="skip_route_conflict",
                genome_hash_before=genome_before,
                genome_hash_after=current_genome.content_hash,
                detection_state=detection_state, timestamp=time.time(),
            ), next_case_index=idx + 1)
            continue
        type_guidance = type_evolution.generate_type_guidance(
            type_result.primary_type,
            evolution_direction,
        )
        print(f"    [HTC-TES] type={type_result.primary_type.value} "
              f"confidence={type_result.confidence:.2f}")

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
            allowed_modules = type_allowed_modules
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

            # HTC-TES v3: Append type-conditioned guidance to reflection prompt
            experience_memory = type_fitness.get_experience_memory()
            experience_summary = experience_memory.get_reflection_summary(
                type_result.primary_type,
                evolution_direction,
            )
            user_prompt += f"\n\n{type_guidance}"
            if experience_summary.get("total", 0) > 0:
                user_prompt += (
                    "\n[Prior type-conditioned replay evidence]\n"
                    + json.dumps(experience_summary, ensure_ascii=False, sort_keys=True)
                    + "\nUse successful patterns as hypotheses, avoid repeated failed "
                    "strategies, and do not treat this memory as verdict authority."
                )

            # One semantic reflection call per source. Invalid output remains an
            # auditable no-mutation event; it is never "prompted into" an edit.
            try:
                parsed, meta = json_object_provider_call(
                    route=mutation_route,
                    purpose="mutation",
                    system=system_prompt,
                    user=user_prompt,
                    cache_dir=mutation_cache,
                )
                target_module = str(parsed.get("target_module") or "")
                if target_module not in allowed_modules:
                    target_module = allowed_modules[0]
                operation = str(parsed.get("operation") or "")
                rule_index = parsed.get("rule_index")
                new_content = str(parsed.get("new_content") or "")
                rationale = str(
                    parsed.get("rationale") or parsed.get("reasoning") or ""
                )
                feature_description = str(parsed.get("feature_description") or "")[:500]

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
                        feature_description=feature_description,
                        hallucination_type=type_result.primary_type.value,
                        secondary_hallucination_type=(
                            type_result.secondary_type.value
                            if type_result.secondary_type is not None else ""
                        ),
                        evolution_direction=evolution_direction,
                        type_confidence=type_result.confidence,
                        routing_allowed_modules=tuple(allowed_modules),
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
                        feature_description=feature_description,
                        hallucination_type=type_result.primary_type.value,
                        secondary_hallucination_type=(
                            type_result.secondary_type.value
                            if type_result.secondary_type is not None else ""
                        ),
                        evolution_direction=evolution_direction,
                        type_confidence=type_result.confidence,
                        routing_allowed_modules=tuple(allowed_modules),
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
                if (
                    merge_target_module is not None
                    and merge_rule_index is not None
                    and merge_target_module in allowed_modules
                ):
                    # Build merged content via LLM (or structural fallback)
                    existing_text = current_genome.modules[merge_target_module].rules[merge_rule_index]
                    merged_content = _llm_merge_rules(
                        existing_text,
                        edit.new_content,
                        mutation_route=mutation_route,
                        # FIX (B3): 缓存目录放入 output_dir，避免相对路径
                        # 依赖当前工作目录。
                        cache_dir=output_dir / "skill_merge_cache",
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
                            feature_description=edit.feature_description,
                            hallucination_type=edit.hallucination_type,
                            secondary_hallucination_type=(
                                edit.secondary_hallucination_type
                            ),
                            evolution_direction=edit.evolution_direction,
                            type_confidence=edit.type_confidence,
                            routing_allowed_modules=edit.routing_allowed_modules,
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
            gate.record_no_edit()
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
        # Source replay is mandatory for every edit. ``fitness_check`` remains
        # an interface-compatible flag controlling formal promotion semantics,
        # not permission to skip the replay envelope.
        if True:
            replay_root = output_dir / "fitness_replay" / f"{idx:03d}_{case_id.replace('/', '_')}"
            try:
                candidate_result = run_detection(
                    budget_kind="source",
                    genome=candidate_genome,
                    case_id=case_id,
                    output_path=replay_root / "source",
                )
            except Exception as exc:
                reason = f"source_replay_runtime_error:{type(exc).__name__}:{exc}"
                fitness_audit = {
                    "source_before": signal.to_dict(),
                    "source_after": None,
                    "source_improved": False,
                    "source_stable_after": False,
                    "commit_validation": "single_source_replay_plus_sampled_canaries",
                    "runtime_error": reason,
                    "passed": False,
                }
                gate.revoke_structural_accept(cert, [reason])
                gate.record_no_edit()
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
            candidate_signal = attribution_for(candidate_result, case_id)
            assessment = assess_source_fitness(
                signal,
                candidate_signal,
                target_module=edit.target_module,
                operation=edit.operation,
                before_result=detection_result,
                after_result=candidate_result,
                gold_label=fitness_gold(case_id),
            )

            canary_audit: list[dict[str, Any]] = []
            canary_regressions: list[str] = []
            if (assessment.passed or assessment.evidence_only) and stable_canaries:
                canary_root = replay_root / "canaries"
                for canary_id, parent_canary in selected_canaries(edit):
                    try:
                        child_canary = run_detection(
                            budget_kind="canary",
                            genome=candidate_genome,
                            case_id=canary_id,
                            output_path=canary_root / canary_id.replace("/", "_"),
                        )
                        preserved, audit = canary_preserved(
                            parent_canary, child_canary,
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
                "source_before": signal.to_dict(),
                "source_after": candidate_signal.to_dict(),
                "assessment": assessment.to_dict(),
                "source_stable_after": not candidate_signal.should_evolve,
                "commit_validation": "single_source_replay_plus_sampled_canaries",
                "source_replays": 1,
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
            directional_canary_support = (
                assessment.evidence_only
                and bool(canary_audit)
                and not canary_regressions
                and all(bool(audit.get("preserved")) for audit in canary_audit)
            )
            if directional_canary_support:
                fitness_audit["directional_evidence_canary_preserved"] = True
                fitness_audit["shadow_admissible"] = True
            if not assessment.passed and not directional_canary_support:
                type_fitness.assess_edit(
                    source_case_id=case_id,
                    hallucination_type=type_result.primary_type,
                    evolution_direction=type_evolution._infer_evolution_direction(
                        signal.state, signal.gold_label,
                    ),
                    detection_before=detection_result,
                    detection_after=candidate_result,
                    type_result=type_result,
                    target_module=edit.target_module,
                    override_success=False,
                    feature_description=edit.feature_description,
                )
                persist_progress(idx + 1)
                if assessment.decision != "no_effect":
                    reasons = [
                        f"source_{assessment.decision}:"
                        + ",".join(assessment.reasons)
                    ]
                    gate.revoke_structural_accept(cert, reasons)
                    gate.record_no_edit()
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
                reasons = ["source_no_effect:" + ",".join(assessment.reasons)]
                gate.revoke_structural_accept(cert, reasons)
                gate.record_no_edit()
                edits_rejected += 1
                record_step(EvolutionStep(
                    case_id=case_id, case_index=idx,
                    trigger_source=signal.trigger_source.value,
                    attribution=signal.to_dict(), edit_proposal=edit.to_dict(),
                    mutation_applied=None, gate_decision="reject_fitness_no_effect",
                    genome_hash_before=genome_before,
                    genome_hash_after=current_genome.content_hash,
                    detection_state=detection_state, timestamp=time.time(),
                    fitness_check=fitness_audit,
                ), next_case_index=idx + 1)
                print(f"  [{idx}] REJECT_LOCAL_FITNESS {case_id}: {reasons}")
                continue

        if not fitness_check:
            # Non-formal diagnostic compatibility path. Formal CLI runs require
            # fitness_check=True.
            if not fitness_use_gold:
                gate.revoke_structural_accept(
                    cert,
                    ["label_free_candidate_is_telemetry_only"],
                )
                gate.record_no_edit()
                edits_rejected += 1
                current_genome = champion_genome
                record_step(EvolutionStep(
                    case_id=case_id,
                    case_index=idx,
                    trigger_source=signal.trigger_source.value,
                    attribution=signal.to_dict(),
                    edit_proposal=edit.to_dict(),
                    mutation_applied=None,
                    gate_decision="provisional_no_gold_telemetry",
                    genome_hash_before=genome_before,
                    genome_hash_after=champion_genome.content_hash,
                    detection_state=detection_state,
                    timestamp=time.time(),
                    fitness_check={
                        "passed": False,
                        "promotion_eligible": False,
                        "claim_scope": "label_free_provisional_telemetry",
                        "rollback_reason": "no_independent_correctness_authority",
                    },
                ), next_case_index=idx + 1)
                continue
            champion_genome = candidate_genome
            current_genome = candidate_genome
            edits_accepted += 1
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
            source_after_signal=candidate_signal,
            source_after_result=candidate_result,
            individual_assessment=assessment,
            step_index=step_index,
            canary_audits=canary_audit,
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
    if pending_probe_commits and registered_probe_ids:
        run_development_probe(reason="final_pending_candidate")
        persist_progress(len(evolution_case_ids))
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
        evolution_audit={
            **budget_usage,
            "rollback_reason": rollback_reason,
            "probe_history": probe_history,
            "validation_probe_every": validation_probe_every,
            "validation_probe_trigger_threshold": (
                validation_probe_trigger_threshold
            ),
            "probe_ids": list(registered_probe_ids),
            "probe_limit": probe_limit,
            "canary_limit": canary_limit,
            "final_champion_claim": False,
            "result_scope": "development_candidate_not_final_holdout_champion",
            "label_free_accuracy_claim": False,
            "no_gold_promotion_allowed": False,
        },
    )

    # Save results
    write_json(output_dir / "evolution_result.json", result.to_dict())
    write_json(output_dir / "champion_genome.json", champion_genome.to_dict())
    # HTC-TES v3: Save type-conditioned experience summary
    type_coverage = type_fitness.get_experience_memory().get_type_coverage()
    write_json(output_dir / "htc_tes_coverage.json", type_coverage)
    write_json(
        output_dir / "htc_tes_experience_memory.json",
        type_fitness.get_experience_memory().to_dict(),
    )
    persist_progress(len(evolution_case_ids), status="complete")

    print(f"\n[{run_id}] Complete in {duration:.1f}s:")
    print(f"  Cases: {len(evolution_case_ids)} total, {triggered_cases} triggered, "
          f"{stable_cases} stable")
    print(f"  Edits: {edits_accepted} accepted, {edits_rejected} rejected")
    print(f"  Halted: {gate.is_halted} ({gate.halt_reason})")
    print(f"  Champion: {champion_genome.content_hash[:16]}...")
    # HTC-TES v3: Print type coverage summary
    print(f"  HTC-TES: {type_coverage['types_with_experience']}/"
          f"{type_coverage['total_types']} types covered "
          f"(effective: {type_coverage['effective_coverage']:.1%})")

    return result
