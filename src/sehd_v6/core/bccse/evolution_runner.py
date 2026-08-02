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
from collections import deque
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
    OBSERVABLE_FIELDS,
    EXPECTED_DIRECTIONS,
    EXPECTED_SCOPES,
    sanitize_expected_effect_contract,
)
from .fitness import (
    FitnessAssessment,
    assess_source_fitness,
    observable_process_delta,
    task_state_score,
)
from .safety_gate import StructuralGate, create_structural_gate
from .step_attributor import (
    AttributionSignal,
    ErrorType,
    TriggerSource,
    attribute_failure,
    runtime_invalid_signal,
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
    attempt_index: int = 0

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
            gate_decision=str(data.get("gate_decision") or "skip_stable"),
            genome_hash_before=str(data.get("genome_hash_before") or ""),
            genome_hash_after=str(data.get("genome_hash_after") or ""),
            detection_state=str(data.get("detection_state") or ""),
            timestamp=float(data.get("timestamp") or 0.0),
            fitness_check=dict(data.get("fitness_check") or {})
            if data.get("fitness_check") is not None
            else None,
            attempt_index=int(data.get("attempt_index") or 0),
        )


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
    # runtime_invalid 是已处理但检测运行时失败的 case（diagnostic_only 遥测，
    # 不计入 triggered/stable）。暴露在顶层使 processed = triggered + stable
    # + runtime_invalid 可被外部审计对账（任务二计数不变式）。
    runtime_invalid_cases: int
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
            "runtime_invalid_cases": self.runtime_invalid_cases,
            "supervision_mode": "gold_enhanced" if self.use_gold else "self_supervised",
            "use_gold": self.use_gold,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "duration_seconds": self.duration_seconds,
            "evolution_audit": self.evolution_audit,
        }


def _expected_effect_audit(
    edit: SkillEdit,
    assessment: FitnessAssessment,
    *,
    before: AttributionSignal | None = None,
    after: AttributionSignal | None = None,
    before_result: Mapping[str, Any] | None = None,
    after_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare the mutator's counterfactual prediction with source replay.

    When ``edit.expected_effect_contract`` is populated (v5.4), the audit
    is driven by the structured contract: the predicted observable_field
    and direction are matched token-by-token against the
    ``observable_process_delta`` output. The legacy free-text
    ``expected_effect`` substring matching is retained only as a fallback
    for edits produced before the contract was introduced.
    """
    expected = " ".join(str(edit.expected_effect or "").split())[:240]
    lowered_expected = expected.lower()
    effect_markers: dict[str, tuple[str, ...]] = {
        "candidate_trigger": ("candidate", "候选"),
        "evidence_obligation": (
            "prosecution", "evidence", "certificate", "causal field",
            "控方", "证据", "证书", "因果字段",
        ),
        "candidate_to_verifier_route": (
            "prosecution", "certificate", "route", "控方", "证书", "路由",
        ),
        "atomic_verifier_instruction": (
            "certificate", "quarantine", "violation", "actor", "boundary",
            "证书", "隔离", "违规", "责任", "边界",
        ),
        "clean_defense_boundary": ("defense", "boundary", "辩护", "边界"),
        "causal_link_test": (
            "causal", "propagation", "impact", "recovery", "因果", "传播", "影响", "恢复",
        ),
        "earliest_origin_tiebreak": (
            "responsible", "origin", "earliest", "责任", "根源", "最早",
        ),
        "abstention_rule": (
            "state", "unresolved", "authoritative", "verdict", "状态", "弃权", "判定",
        ),
    }
    markers = effect_markers.get(edit.target_module, ())
    target_aligned = (
        None
        if not expected
        else any(marker in lowered_expected for marker in markers)
    )
    observable_deltas = (
        observable_process_delta(
            before,
            after,
            target_module=edit.target_module,
            before_result=before_result,
            after_result=after_result,
        )
        if before is not None and after is not None
        else ()
    )
    observable_delta_tokens = list(observable_deltas)

    # ---- v5.4 structured contract path ----
    contract = sanitize_expected_effect_contract(edit.expected_effect_contract)
    contract_status: str | None = None
    contract_field_observed = False
    contract_direction_observed = False
    if contract:
        cfield = str(contract.get("observable_field") or "")
        cdir = str(contract.get("expected_direction") or "")
        # Map contract field -> the prefixes that observable_process_delta
        # emits for that field. This is the source-level provenance link.
        field_prefixes: dict[str, tuple[str, ...]] = {
            "candidate_count": ("candidate_count:",),
            "prosecution_coverage": ("prosecution_coverage:",),
            "defense_coverage": ("defense_coverage:",),
            "prosecution_certificates": ("prosecution_certificates:",),
            "defense_certificates": ("defense_certificates:",),
            "quarantined_certificates": ("quarantined_certificates:",),
            "causal_field_coverage": ("causal_field_coverage:",),
            "responsible_step": ("responsible_step:",),
            "localization_status": ("localization_status:",),
            "target_local_score": ("target_local_score:",),
            "state": ("state:",),
            "trigger": ("trigger:",),
        }
        direction_tokens: dict[str, tuple[str, ...]] = {
            "increase": ("increased", "added", "opened"),
            "decrease": ("decreased", "removed", "closed"),
            "added": ("added", "opened"),
            "removed": ("removed", "closed"),
            "shift": ("shift", "->"),
            "opened": ("opened",),
            "closed": ("closed",),
        }
        prefixes = field_prefixes.get(cfield, ())
        dir_tokens = direction_tokens.get(cdir, ())
        for token in observable_delta_tokens:
            lowered_token = token.lower()
            if prefixes and any(token.startswith(p) for p in prefixes):
                contract_field_observed = True
                if dir_tokens and any(d in lowered_token for d in dir_tokens):
                    contract_direction_observed = True
        if not prefixes:
            # Unknown field: cannot validate; treat as unspecified.
            contract_status = "unspecified"
        elif assessment.worsened:
            contract_status = "contradicted"
        elif not observable_delta_tokens:
            contract_status = "not_observed"
        elif contract_field_observed and contract_direction_observed:
            contract_status = "observed"
        elif contract_field_observed:
            contract_status = "field_observed_direction_mismatch"
        else:
            contract_status = "not_observed"

    # ---- legacy free-text path (fallback) ----
    if not expected:
        legacy_status = "unspecified"
    elif target_aligned is False:
        legacy_status = "misaligned"
    elif assessment.worsened:
        legacy_status = "contradicted"
    elif assessment.decision == "no_effect":
        legacy_status = "not_observed"
    else:
        legacy_status = "observed"

    status = contract_status if contract else legacy_status
    return {
        "expected": expected,
        "expected_effect_contract": contract,
        "status": status,
        "contract_status": contract_status,
        "legacy_status": legacy_status,
        "contract_field_observed": contract_field_observed,
        "contract_direction_observed": contract_direction_observed,
        "target_aligned": target_aligned,
        "observed_decision": assessment.decision,
        "observed_reasons": list(assessment.reasons),
        "observable_deltas": observable_delta_tokens,
        "target_output_changed": bool(observable_delta_tokens),
        "local_score_before": assessment.local_score_before,
        "local_score_after": assessment.local_score_after,
    }


def _build_rejection_summary(
    gate_summary: Mapping[str, Any],
    steps: Sequence[EvolutionStep],
) -> dict[str, Any]:
    """Separate legality, evidence, regression and runtime rejection causes."""
    reason_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    for certificate in gate_summary.get("certificates") or ():
        if certificate.get("decision") != "reject":
            continue
        reasons = [str(value) for value in certificate.get("reject_reasons") or ()]
        if any("runtime" in value or "apply_edit_failed" in value for value in reasons):
            stage = "runtime"
        elif any("no_effect" in value for value in reasons):
            stage = "evidence_no_effect"
        elif any("directional_evidence" in value for value in reasons):
            stage = "evidence_unverified"
        elif any("worse" in value or "regression" in value for value in reasons):
            stage = "regression"
        else:
            stage = "structural"
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        for reason in reasons:
            key = reason.split(":", 1)[0]
            reason_counts[key] = reason_counts.get(key, 0) + 1
    decision_counts: dict[str, int] = {}
    for step in steps:
        decision_counts[step.gate_decision] = decision_counts.get(step.gate_decision, 0) + 1
    return {
        "by_stage": stage_counts,
        "by_reason": reason_counts,
        "by_decision": decision_counts,
    }


_REPAIRABLE_STRUCTURAL_REASONS = frozenset({
    "runtime_rule_contract",
    "content_too_short",
    "not_if_then_instruction",
    "multi_step_pseudocode",
    "condition_overload",
    "multiple_actions",
    "cross_module_near_duplicate",
    "cross_module_merge_required",
    "merge_required",
    "diagnostic_only_condition",
    "cross_module_reference",
    "modify_bloat",
    "duplicate_content",
})


def _structural_rejection_retryable(reasons: Sequence[str]) -> bool:
    """Whether a fresh proposal can safely repair this structural failure."""
    categories = {str(reason).split(":", 1)[0] for reason in reasons}
    return bool(categories) and categories <= _REPAIRABLE_STRUCTURAL_REASONS


def _no_edit_reason_categories(reason: str) -> list[str]:
    """Reduce free-form mutator abstention text to safe learning categories."""
    lowered = str(reason or "").lower()
    categories = ["model_no_edit"]
    if "baseline" in lowered and any(
        token in lowered for token in ("cover", "already", "sufficient")
    ):
        categories.append("baseline_coverage_claimed")
    if any(token in lowered for token in ("no observable", "lacks observable", "no concrete")):
        categories.append("insufficient_observable_evidence")
    if any(token in lowered for token in ("unsafe", "speculative", "false positive")):
        categories.append("unsafe_generalization_risk")
    return categories


def _classify_mutation_failure(reason: str) -> str:
    """Reduce a mutation invalid-schema reason to a stable retry-feedback category.

    The category is the only case-agnostic signal fed back into the retry
    prompt; raw provider text (which may embed case ids, rule content or
    evaluator metadata fragments) is never echoed back. This keeps the retry
    request body deterministic w.r.t. failure class only, and ensures the
    cache key differs from the initial attempt.
    """
    text = str(reason or "")
    if text.startswith("single_reflection_runtime_error"):
        return "mutation_provider_exception"
    if "operation=" in text and "content_length=0" in text:
        return "mutation_empty_content"
    if "operation=" in text:
        # Non-empty content but unknown/garbage operation
        return "mutation_invalid_operation"
    if "json" in text.lower() or "parse" in text.lower():
        return "mutation_unparseable_json"
    return "mutation_invalid_schema"


# ============================================================
# Structured experience outcome (v5.5)
# ============================================================

# The stage at which an attempt stopped. Free-form strings previously made
# experience memory unqueryable ("no_edit" / "structural_reject" were the
# only values that ever reached it, and they encoded stage and result in one
# opaque token). The vocabulary below is closed so that reflection can
# aggregate over it safely.
OUTCOME_STAGES = frozenset({
    "mutation",            # the mutator declined or returned an invalid edit
    "structural_gate",     # the structural constraint gate rejected the edit
    "source_replay",       # the edit was replayed on its own source case
    "batch_commit",        # the edit was evaluated in the shadow batch
    "detection_runtime",   # the detector never produced a verdict
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
    """Map a fitness decision onto the closed outcome vocabulary."""
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
) -> dict[str, Any]:
    """Build the structured outcome payload stored in experience memory.

    Replaces the previous free-form ``{"outcome": "<some string>"}``. Every
    field is drawn from a closed vocabulary or is already category-reduced,
    so the payload can be aggregated, filtered, and safely re-entered into a
    reflection prompt.
    """
    outcome_class = outcome if outcome in OUTCOME_CLASSES else "rejected"
    if promotable is None:
        promotable = outcome_class in _PROMOTABLE_OUTCOME_CLASSES
    return {
        "outcome": outcome_class,
        "outcome_stage": stage if stage in OUTCOME_STAGES else "mutation",
        "outcome_decision": str(decision or outcome_class),
        "operation": operation,
        "reason_categories": [str(value) for value in reason_categories][:5],
        "observable_deltas": [str(value) for value in observable_deltas][:8],
        "expected_effect_status": expected_effect_status,
        # Explicit claim scope: label-free/diagnostic evidence must never be
        # read back as proof that an edit improved detection.
        "promotable_evidence": bool(promotable) and not diagnostic_only,
        "diagnostic_only": bool(diagnostic_only),
        "schema_version": "agenthallu.bccse_experience_outcome.v1",
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
    census_root: Path | None = None,
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
    # Formal evolution must never consume legacy rows that omit the explicit
    # authority field.  Compatibility evaluators may still inspect such rows,
    # but they cannot enter source fitness or champion promotion.
    if not str(result.get("decision_authority") or "").strip():
        raise RuntimeError("detector returned missing decision authority")
    if decision_authority_invalid(result):
        # 附加详细错误信息便于排查（appeal_error / proof_court_error 等）
        reasons = result.get("authority_failure_reasons") or []
        providers_data = result.get("providers") or {}
        error_details = []
        for r in reasons:
            if isinstance(r, dict):
                error_details.append(
                    f"{r.get('component', '?')}/{r.get('code', '?')}: "
                    f"{r.get('error_type', '')} {str(r.get('error', ''))[:200]}"
                )
        for k, v in providers_data.items():
            if "error" in str(k).lower() or (isinstance(v, dict) and v.get("error_type")):
                if isinstance(v, dict):
                    error_details.append(
                        f"provider[{k}]: {v.get('error_type', '')} {str(v.get('error', ''))[:200]}"
                    )
        detail = " | ".join(error_details) if error_details else "no details"
        raise RuntimeError(f"detector returned invalid decision authority: {detail}")
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


# ============================================================
# Public checkpoint inspection / resume API (v5.4)
# ============================================================
# `persist_progress` already writes `evolution_checkpoint.json`,
# `champion_checkpoint.json`, `shadow_checkpoint.json` and the HTC-TES
# experience memory atomically after every step. The functions below
# expose the previously-internal recovery path as a public contract so
# that interrupted runs can be resumed without re-running already
# processed cases (and without losing the audit trail).

_CHECKPOINT_SCHEMA_VERSION = "agenthallu.bccse_v5_checkpoint.v2"
_CHECKPOINT_REQUIRED_FILES: tuple[str, ...] = (
    "evolution_checkpoint.json",
    "champion_checkpoint.json",
    "shadow_checkpoint.json",
)


def load_evolution_checkpoint(output_dir: Path) -> dict[str, Any]:
    """Load and validate a persisted evolution checkpoint.

    This is the PUBLIC read side of the recovery contract. It returns a
    typed dict carrying the resumable state: champion/shadow genomes, the
    next-case index, accumulated steps, accepted-edits history, budget
    usage, stable canaries, and the path to the persisted HTC-TES
    experience memory. Returns ``{}`` when no checkpoint exists.

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
    if schema != _CHECKPOINT_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported checkpoint schema_version: {schema!r} "
            f"(expected {_CHECKPOINT_SCHEMA_VERSION!r})"
        )
    for filename in _CHECKPOINT_REQUIRED_FILES:
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
    champion_path = output_dir / "champion_checkpoint.json"
    shadow_path = output_dir / "shadow_checkpoint.json"
    with champion_path.open(encoding="utf-8") as handle:
        champion_payload = json.load(handle)
    with shadow_path.open(encoding="utf-8") as handle:
        shadow_payload = json.load(handle)
    experience_path = output_dir / "htc_tes_experience_memory.json"
    return {
        "schema_version": schema,
        "run_id": str(checkpoint.get("run_id") or ""),
        "status": str(checkpoint.get("status") or "in_progress"),
        "next_case_index": next_case_index,
        "evolution_case_ids": evolution_case_ids,
        "champion_genome_payload": champion_payload,
        "shadow_genome_payload": shadow_payload,
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
        "counters": dict(checkpoint.get("counters") or {}),
        "updated_at": float(checkpoint.get("updated_at") or 0.0),
        "checkpoint_dir": str(output_dir),
    }


def _restore_state_from_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    champion_genome: SkillGenome,
    current_genome: SkillGenome,
    stable_genome: SkillGenome,
    type_fitness: Any,
    evolution_case_ids: Sequence[str],
) -> dict[str, Any]:
    """Restore in-memory evolution state from a loaded checkpoint.

    Mutates the supplied genomes/experience-memory in place and returns a
    dict of the remaining scalar/collection state that the caller must
    bind to the matching local variables before entering the work queue.
    """
    champion_payload = dict(checkpoint.get("champion_genome_payload") or {})
    shadow_payload = dict(checkpoint.get("shadow_genome_payload") or {})
    if champion_payload:
        restored_champion = SkillGenome.from_dict(champion_payload)
        champion_genome.modules = restored_champion.modules
        champion_genome.edits_applied = restored_champion.edits_applied
        champion_genome.parent_hash = restored_champion.parent_hash
        champion_genome.generation = restored_champion.generation
        champion_genome.metadata = restored_champion.metadata
    if shadow_payload:
        restored_shadow = SkillGenome.from_dict(shadow_payload)
        current_genome.modules = restored_shadow.modules
        current_genome.edits_applied = restored_shadow.edits_applied
        current_genome.parent_hash = restored_shadow.parent_hash
        current_genome.generation = restored_shadow.generation
        current_genome.metadata = restored_shadow.metadata
        stable_genome.modules = restored_shadow.modules
        stable_genome.edits_applied = restored_shadow.edits_applied
        stable_genome.parent_hash = restored_shadow.parent_hash
        stable_genome.generation = restored_shadow.generation
        stable_genome.metadata = restored_shadow.metadata

    experience_path_str = checkpoint.get("experience_memory_path")
    if experience_path_str:
        experience_path = Path(experience_path_str)
        if experience_path.is_file():
            with experience_path.open(encoding="utf-8") as handle:
                memory_payload = json.load(handle)
            type_fitness.get_experience_memory().from_dict(memory_payload)

    return {
        "steps": list(checkpoint.get("steps") or []),
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
        "counters": dict(checkpoint.get("counters") or {}),
        "next_case_index": int(checkpoint.get("next_case_index") or 0),
    }


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
    budget_usage: dict[str, int] | None = None,
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
        if budget_usage is not None:
            budget_usage["mutation_calls"] += 1
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
        if budget_usage is not None:
            budget_usage["mutation_errors"] += 1
        pass
    # Fallback to structural merge
    return _llm_merge_rules(existing_rule, new_rule, mutation_route=None)


def run_online_evolution(
    *,
    evolution_case_ids: Sequence[str],
    records: Mapping[str, dict[str, Any]],
    config: dict[str, Any],
    output_dir: Path,
    census_root: Path | None = None,
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
    canary_case_ids: Sequence[str] = (),
    validation_probe_every: int = 0,
    probe_ids: Sequence[str] = (),
    probe_limit: int = 2,
    validation_probe_trigger_threshold: int = 0,
    max_mutation_attempts_per_case: int = 2,
    initial_genome: SkillGenome | None = None,
    run_id: str | None = None,
    resume_from: Path | None = None,
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
        canary_case_ids: Optional pre-registered label-free baseline canaries.
            These are evaluated once before evolution so an all-triggered
            evolution sample can still have independent non-regression anchors.
            They must be disjoint from evolution and probe cases.
        validation_probe_every: Run the independent gold development probe
            after each N accepted edits. Zero disables this trigger.
        probe_ids: Pre-registered development-probe case IDs. These are never
            inferred from validation, test, or outer partitions.
        probe_limit: Maximum pre-registered probe cases evaluated per probe.
        validation_probe_trigger_threshold: Also run the development probe
            after this many newly triggered source cases. Zero disables it.
        max_mutation_attempts_per_case: Bounded same-source search budget.
            A structurally repairable or source-no-effect first proposal may
            be regenerated once using its actual rejection feedback. Each
            candidate is still replayed independently from the same parent.
        initial_genome: Optional audited champion to continue evolving. When
            omitted, evolution starts from the frozen baseline genome.
        run_id: Optional run identifier.
        resume_from: Optional directory containing a previously persisted
            checkpoint (``evolution_checkpoint.json`` + champion/shadow
            genome checkpoints). When set, the run restores champion /
            shadow genomes, accumulated steps, accepted-edits history,
            budget usage, stable canaries and the HTC-TES experience
            memory, then resumes the work queue from ``next_case_index``.
            The supplied ``evolution_case_ids`` MUST match the checkpoint's
            ``evolution_case_ids`` (otherwise a ``ValueError`` is raised),
            so that case ordering is unambiguous. ``initial_genome`` is
            ignored when resuming.

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
    if max_mutation_attempts_per_case < 1 or max_mutation_attempts_per_case > 3:
        raise ValueError("max_mutation_attempts_per_case must be between 1 and 3")
    if probe_limit < 0 or probe_limit > 20:
        raise ValueError("probe_limit must be between 0 and 20")
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
    overlapping_probe_ids = sorted(set(probe_ids).intersection(evolution_case_ids))
    if overlapping_probe_ids:
        raise ValueError(
            "probe_ids must be disjoint from evolution_case_ids: "
            + ", ".join(overlapping_probe_ids)
        )
    missing_probe_records = [case_id for case_id in probe_ids if case_id not in records]
    if missing_probe_records:
        raise ValueError("Probe records missing: " + ", ".join(missing_probe_records))
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
        # ``provider_calls`` is retained as the historical detector-call
        # counter. Explicit fields avoid conflating detector replays with
        # reflection/mutation calls in audit output.
        "provider_calls": 0,
        "detector_calls": 0,
        "mutation_calls": 0,
        "mutation_errors": 0,
        "mutation_cache_hits": 0,
        "mutation_retry_attempts": 0,
    }
    # v3.5.3: per-case stable failure categories for mutation retry feedback.
    # When a mutation returns an invalid schema, the case is requeued with
    # attempt_index+1. On the retry, the stored categories are appended to the
    # reflection prompt as MUTATION_RETRY_FEEDBACK. This (a) changes the
    # request body so the provider cache key differs (otherwise the retry
    # would hit the same cache entry and return the same invalid response),
    # and (b) gives the LLM structured, case-agnostic feedback. Raw provider
    # text is never echoed back (no prompt poison, no evaluator metadata).
    mutation_retry_feedback: dict[str, list[str]] = {}
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
    baseline_canary_audit: list[dict[str, Any]] = []
    # Diagnostic-only counters: runtime failures are observations, never
    # evolution evidence, so they are tracked separately from triggered cases.
    stable_cases = 0
    triggered_cases = 0
    edits_accepted = 0
    edits_rejected = 0
    runtime_invalid_cases = 0
    runtime_error_categories: dict[str, int] = {}

    # ---- v5.4: optional resume from a persisted checkpoint ----
    resume_next_case_index = 0
    if resume_from is not None:
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
        restored = _restore_state_from_checkpoint(
            checkpoint,
            champion_genome=champion_genome,
            current_genome=current_genome,
            stable_genome=stable_genome,
            type_fitness=type_fitness,
            evolution_case_ids=evolution_case_ids,
        )
        steps = [
            EvolutionStep.from_dict(step_payload)
            for step_payload in (restored.get("steps") or [])
            if isinstance(step_payload, Mapping)
        ]  # type: ignore[assignment]
        accepted_edits_history = list(restored["accepted_edits_history"])  # type: ignore[assignment]
        batch_history = list(restored["batch_history"])  # type: ignore[assignment]
        probe_history = list(restored["probe_history"])  # type: ignore[assignment]
        baseline_canary_audit = list(restored["baseline_canary_audit"])  # type: ignore[assignment]
        restored_budget = dict(restored.get("budget_usage") or {})
        for key, value in restored_budget.items():
            if key in budget_usage and isinstance(value, (int, float)):
                budget_usage[key] = value
        rollback_reason = str(restored.get("rollback_reason") or "")
        pending_probe_step_indexes = list(
            restored.get("pending_probe_step_indexes") or []
        )
        restored_counters = dict(restored.get("counters") or {})
        stable_cases = int(restored_counters.get("stable_cases") or 0)
        triggered_cases = int(restored_counters.get("triggered_cases") or 0)
        edits_accepted = int(restored_counters.get("edits_accepted") or 0)
        edits_rejected = int(restored_counters.get("edits_rejected") or 0)
        resume_next_case_index = int(restored.get("next_case_index") or 0)
        stable_memory_payload = type_fitness.get_experience_memory().to_dict()
        if run_id is None:
            run_id = str(checkpoint.get("run_id") or run_id or "")
        print(
            f"[resume] restored checkpoint from {resume_from}: "
            f"next_case_index={resume_next_case_index}, "
            f"steps={len(steps)}, edits_accepted={edits_accepted}, "
            f"edits_rejected={edits_rejected}"
        )

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


    def run_detection(
        *,
        budget_kind: str,
        genome: SkillGenome,
        case_id: str,
        output_path: Path,
    ) -> dict[str, Any]:
        """Run one detector call in a counted, label-free envelope."""
        budget_usage["provider_calls"] += 1
        budget_usage["detector_calls"] += 1
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

    def run_mutation_call(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        """Count attribution/reflection calls separately from detector calls."""
        budget_usage["mutation_calls"] += 1
        try:
            parsed, meta = json_object_provider_call(**kwargs)
        except Exception:
            budget_usage["mutation_errors"] += 1
            raise
        if isinstance(meta, Mapping) and meta.get("cache_hit"):
            budget_usage["mutation_cache_hits"] += 1
        return parsed, meta

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

    # Seed independent label-free anchors before processing the evolution
    # sample. Without this explicit pool, an all-triggered sample has no
    # stable case available for the non-regression gate.
    registered_canary_ids = tuple(canary_case_ids[:canary_limit])
    for canary_id in registered_canary_ids:
        audit: dict[str, Any] = {"case_id": canary_id, "admitted": False}
        try:
            canary_result = run_detection(
                budget_kind="canary",
                genome=champion_genome,
                case_id=canary_id,
                output_path=(
                    output_dir / "baseline_canaries" / canary_id.replace("/", "_")
                ),
            )
            canary_signal = attribution_for(canary_result, canary_id)
            audit.update({
                "state": resolve_authoritative_state(canary_result),
                "should_evolve": canary_signal.should_evolve,
                "attribution": canary_signal.to_dict(),
            })
            if not canary_signal.should_evolve:
                stable_canaries[canary_id] = canary_result
                audit["admitted"] = True
        except Exception as exc:
            audit.update({
                "state": "runtime_invalid",
                "error": f"{type(exc).__name__}:{exc}",
            })
        baseline_canary_audit.append(audit)
    stable_canaries_checkpoint = dict(stable_canaries)
    if registered_canary_ids and not stable_canaries and not fitness_use_gold:
        raise RuntimeError(
            "No registered canary remained stable under the initial genome; "
            "label-free evolution cannot distinguish directional evidence "
            "from self-confirmation"
        )

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
                "baseline_canary_audit": baseline_canary_audit,
                "canary_case_ids": list(registered_canary_ids),
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

    active_attempt_index = 0

    def record_step(step: EvolutionStep, *, next_case_index: int) -> None:
        step.attempt_index = active_attempt_index
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
                _effect_audit = _expected_effect_audit(
                    item.edit,
                    item.individual_assessment,
                    before=item.source_signal,
                    after=item.source_after_signal,
                    before_result=item.source_result,
                    after_result=_shadow_result,
                )
                typed_assessment = type_fitness.assess_edit(
                    source_case_id=item.case_id,
                    hallucination_type=_type_result.primary_type,
                    evolution_direction=_direction,
                    detection_before=item.source_result,
                    detection_after=_shadow_result,
                    type_result=_type_result,
                    target_module=item.edit.target_module,
                    override_success=_htc_success,
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
                        # Only a gold-assisted batch commit may claim the
                        # edit actually improved detection.
                        promotable=_htc_success and fitness_use_gold,
                        diagnostic_only=not fitness_use_gold,
                        decision=item.individual_assessment.decision,
                    ),
                )
                # Persist the typed-fitness assessment in the batch audit
                # trail: it was previously computed and silently discarded,
                # so its metrics (global fitness, DACR, decisive accuracy)
                # were unobservable in evolution results.
                batch_audit.setdefault("typed_fitness_assessments", []).append(
                    typed_assessment.to_dict()
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
            for idx in rejected_indices:
                item = provisional_edits[idx]
                item_assessment = item.individual_assessment
                reasons = [
                    f"source_{item_assessment.decision}:"
                    + ",".join(item_assessment.reasons)
                ]
                if runtime_errors:
                    reasons.append("shadow_batch_runtime_error")
                # Preserve negative learning signal as well. A batch-level
                # canary failure or a shadow replay rejection is valuable
                # evidence for avoiding the same strategy later.
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

    persist_progress(resume_next_case_index)

    source_work_queue = deque(
        (index, case_id, 0)
        for index, case_id in enumerate(evolution_case_ids)
        if index >= resume_next_case_index
    )
    if resume_next_case_index > 0:
        print(
            f"[resume] skipping first {resume_next_case_index} case(s); "
            f"{len(source_work_queue)} case(s) remaining in work queue"
        )
    while source_work_queue:
        idx, case_id, active_attempt_index = source_work_queue.popleft()
        if active_attempt_index > 0:
            budget_usage["mutation_retry_attempts"] += 1
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
            runtime_error = f"{type(exc).__name__}: {exc}"
            # Diagnostic-only telemetry. A crashed detector run cannot be
            # attributed to a skill module (no verdict, no coverage, no CRS),
            # so this signal never triggers evolution. It is recorded so the
            # run audit and the type-conditioned memory can still show which
            # genome failed to execute, and why, in bounded categories.
            invalid_signal = runtime_invalid_signal(error=runtime_error)
            _atomic_write_json(
                output_dir / "runtime_failures" / f"{idx:04d}.json",
                {
                    "schema_version": "agenthallu.bccse_v5_failure.v1",
                    "run_id": run_id,
                    "case_id": case_id,
                    "case_index": idx,
                    "genome_hash": current_genome.content_hash,
                    "error": runtime_error,
                    "error_category": invalid_signal.runtime_error_category,
                    "action": "case_skipped",
                    "claim_scope": "diagnostic_only_not_evolution_evidence",
                },
            )
            runtime_invalid_cases += 1
            runtime_error_categories[invalid_signal.runtime_error_category] = (
                runtime_error_categories.get(
                    invalid_signal.runtime_error_category, 0
                ) + 1
            )
            # Record the lapse as an explicitly non-improving, diagnostic-only
            # experience so a repeatedly crashing strategy is visible to later
            # reflection without ever counting as a detection outcome.
            type_fitness.get_experience_memory().add_experience(
                hallucination_type=HallucinationType.UNKNOWN,
                evolution_direction="runtime_invalid",
                module_targeted="",
                strategy_used="runtime_invalid",
                detection_improved=False,
                source_case_id=case_id,
                metadata=_structured_outcome(
                    outcome="runtime_invalid",
                    stage="detection_runtime",
                    operation="none",
                    expected_effect_status="not_replayed",
                    reason_categories=[invalid_signal.runtime_error_category],
                    promotable=False,
                    diagnostic_only=True,
                ),
                feature_description="",
                type_confidence=0.0,
            )
            gate.record_no_edit()
            record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source="runtime_invalid",
                attribution=invalid_signal.to_dict(),
                edit_proposal=None,
                mutation_applied=None,
                gate_decision="skip_runtime_invalid",
                genome_hash_before=genome_before,
                genome_hash_after=current_genome.content_hash,
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
                attr_parsed, _ = run_mutation_call(
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
            if active_attempt_index == 0:
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
        if active_attempt_index == 0:
            triggered_cases += 1
        print(f"  [{idx}] TRIGGERED {case_id}: {signal.trigger_source.value} "
              f"→ {signal.error_type.value} → {signal.target_module} "
              f"(attempt {active_attempt_index + 1}/"
              f"{max_mutation_attempts_per_case})")

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
            detection_context = format_detection_context_for_llm(
                detection_result,
                trajectory_record=_detector_record(records[case_id]),
            )

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

            # v3.5.3: On a mutation retry, append structured failure feedback so
            # the request body (and thus the provider cache key) differs from
            # the initial attempt. Only stable case-agnostic categories are
            # fed back; raw provider text is never echoed (no prompt poison).
            if active_attempt_index > 0:
                prior_categories = mutation_retry_feedback.get(case_id, [])
                if prior_categories:
                    user_prompt += (
                        "\n[MUTATION_RETRY_FEEDBACK]\n"
                        + json.dumps(
                            {
                                "attempt_index": active_attempt_index,
                                "previous_failure_categories": prior_categories,
                                "required_action": (
                                    "previous mutation output was structurally "
                                    "invalid; return a valid JSON edit with "
                                    "operation in {add_rule,modify_rule,remove_rule,"
                                    "no_edit} and non-empty new_content"
                                ),
                            },
                            ensure_ascii=False, sort_keys=True,
                        )
                    )

            # One semantic reflection call per source. Invalid output remains an
            # auditable no-mutation event; it is never "prompted into" an edit.
            try:
                parsed, meta = run_mutation_call(
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
                expected_effect = str(parsed.get("expected_effect") or "")[:240]
                expected_effect_contract_raw = parsed.get("expected_effect_contract")
                if not isinstance(expected_effect_contract_raw, dict):
                    expected_effect_contract_raw = None
                expected_effect_contract = (
                    expected_effect_contract_raw
                    if expected_effect_contract_raw is not None
                    else {}
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
                        expected_effect=expected_effect,
                        expected_effect_contract=expected_effect_contract,
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
            type_fitness.get_experience_memory().add_experience(
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
                ),
                feature_description="",
                type_confidence=type_result.confidence,
            )
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
            retry_scheduled = (
                mutation_route is not None
                and active_attempt_index + 1 < max_mutation_attempts_per_case
            )
            if retry_scheduled:
                # v3.5.3: record a stable failure category so the retry prompt
                # can carry structured feedback (and a different cache key).
                failure_category = _classify_mutation_failure(
                    invalid_mutation_reason or "mutation provider unavailable",
                )
                mutation_retry_feedback.setdefault(case_id, []).append(
                    failure_category
                )
                source_work_queue.appendleft(
                    (idx, case_id, active_attempt_index + 1)
                )
            else:
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
                gate_decision=(
                    "retry_invalid_mutation"
                    if retry_scheduled else "skip_invalid_mutation"
                ),
                genome_hash_before=genome_before,
                genome_hash_after=current_genome.content_hash,
                detection_state=detection_state,
                timestamp=time.time(),
                fitness_check={
                    "retry_scheduled": retry_scheduled,
                    "mutation_error": invalid_mutation_reason
                    or "mutation provider unavailable",
                    "retry_failure_category": (
                        mutation_retry_feedback.get(case_id, [None])[-1]
                        if retry_scheduled else None
                    ),
                },
            ), next_case_index=(idx if retry_scheduled else idx + 1))
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
            repairable_certificate = cert
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
                        budget_usage=budget_usage,
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
                            expected_effect=edit.expected_effect,
                        )
                        # Re-run structural gate on the merged edit
                        gate.defer_structural_reject(repairable_certificate)
                        gate.mark_repaired(
                            repairable_certificate,
                            "semantic_merge_attempt",
                        )
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
                rejected_entry = RejectedEditEntry(
                    proposal=type('P', (), {'target_module': edit.target_module, 'operation': edit.operation, 'content': edit.new_content[:80]})(),
                    regressed_cases=[],
                    budget_consumed=0,
                    reject_reasons=reject_reasons,
                )
                rejected_buffer.append(rejected_entry)
                type_fitness.get_experience_memory().add_experience(
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
                    ),
                    feature_description=edit.feature_description,
                    type_confidence=type_result.confidence,
                )
                retry_scheduled = (
                    mutation_route is not None
                    and active_attempt_index + 1 < max_mutation_attempts_per_case
                    and _structural_rejection_retryable(reject_reasons)
                )
                if retry_scheduled:
                    gate.defer_structural_reject(cert)
                    source_work_queue.appendleft(
                        (idx, case_id, active_attempt_index + 1)
                    )
                record_step(EvolutionStep(
                    case_id=case_id,
                    case_index=idx,
                    trigger_source=signal.trigger_source.value,
                    attribution=signal.to_dict(),
                    edit_proposal=edit.to_dict(),
                    mutation_applied=None,
                    gate_decision=(
                        "retry_structural" if retry_scheduled else "reject"
                    ),
                    genome_hash_before=genome_before,
                    genome_hash_after=current_genome.content_hash,
                    detection_state=detection_state,
                    timestamp=time.time(),
                    fitness_check={
                        "retry_scheduled": retry_scheduled,
                        "retry_reason": rejected_entry.feedback_summary(),
                    },
                ), next_case_index=(idx if retry_scheduled else idx + 1))
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
            effect_audit = _expected_effect_audit(
                edit,
                assessment,
                before=signal,
                after=candidate_signal,
                before_result=detection_result,
                after_result=candidate_result,
            )
            fitness_audit["expected_effect_contract"] = effect_audit
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
                typed_assessment = type_fitness.assess_edit(
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
                        # A source replay is generated by the same detector
                        # being edited, so it is never promotable on its own.
                        promotable=False,
                        decision=assessment.decision,
                    ),
                )
                fitness_audit["htc_tes_typed_fitness"] = (
                    typed_assessment.to_dict()
                )
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
                edits_rejected += 1
                rejected_entry = RejectedEditEntry(
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
                    reject_reasons=reasons,
                    rejection_stage="source_fitness",
                    observed_effect=effect_audit["status"],
                    observable_deltas=tuple(
                        effect_audit["observable_deltas"]
                    ),
                )
                rejected_buffer.append(rejected_entry)
                retry_scheduled = (
                    mutation_route is not None
                    and active_attempt_index + 1 < max_mutation_attempts_per_case
                )
                if retry_scheduled:
                    source_work_queue.appendleft(
                        (idx, case_id, active_attempt_index + 1)
                    )
                else:
                    gate.record_no_edit()
                fitness_audit["retry_scheduled"] = retry_scheduled
                fitness_audit["retry_reason"] = rejected_entry.feedback_summary()
                record_step(EvolutionStep(
                    case_id=case_id, case_index=idx,
                    trigger_source=signal.trigger_source.value,
                    attribution=signal.to_dict(), edit_proposal=edit.to_dict(),
                    mutation_applied=None,
                    gate_decision=(
                        "retry_fitness_no_effect"
                        if retry_scheduled else "reject_fitness_no_effect"
                    ),
                    genome_hash_before=genome_before,
                    genome_hash_after=current_genome.content_hash,
                    detection_state=detection_state, timestamp=time.time(),
                    fitness_check=fitness_audit,
                ), next_case_index=(idx if retry_scheduled else idx + 1))
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
    final_gate_summary = gate.summary()
    result = OnlineEvolutionResult(
        run_id=run_id,
        champion_genome=champion_genome,
        steps=steps,
        gate_summary=final_gate_summary,
        total_cases=len(evolution_case_ids),
        triggered_cases=triggered_cases,
        edits_accepted=edits_accepted,
        edits_rejected=edits_rejected,
        stable_cases=stable_cases,
        runtime_invalid_cases=runtime_invalid_cases,
        use_gold=use_gold,
        halted=gate.is_halted,
        halt_reason=gate.halt_reason,
        duration_seconds=duration,
        evolution_audit={
            **budget_usage,
            "total_provider_calls": (
                budget_usage["detector_calls"] + budget_usage["mutation_calls"]
            ),
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
            "runtime_invalid_cases": runtime_invalid_cases,
            "runtime_error_categories": dict(runtime_error_categories),
            "runtime_invalid_claim_scope": (
                "diagnostic_only_not_evolution_evidence"
            ),
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
    if registered_canary_ids:
        print(
            f"  Baseline canaries: "
            f"{sum(bool(item.get('admitted')) for item in baseline_canary_audit)}/"
            f"{len(registered_canary_ids)} admitted"
        )
    print(f"  Halted: {gate.is_halted} ({gate.halt_reason})")
    print(f"  Champion: {champion_genome.content_hash[:16]}...")
    # HTC-TES v3: Print type coverage summary
    print(f"  HTC-TES: {type_coverage['types_with_experience']}/"
          f"{type_coverage['total_types']} types covered "
          f"(effective: {type_coverage['effective_coverage']:.1%})")

    return result
