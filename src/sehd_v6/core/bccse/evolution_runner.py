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
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..paca_tcl import proof_engine as v35
from ..paca_tcl.skill_integration import integrated_v35_provider
from ..paca_tcl.basic_runner import (
    json_object_provider_call,
    write_json,
    write_jsonl,
)
from ...utils import ocse_packet as packet
from ...utils.gold_label import coerce_gold_label
from .reflection_engine import (
    EditProposal,
    RejectedEditEntry,
    build_causal_reflection_prompt,
    MODULE_OBSERVABLE_FIELDS,
    build_capacity_guidance,
    build_accepted_edits_summary,
    format_detection_context_for_llm,
    build_runtime_observation,
    extract_intervention_candidates,
    build_intervention_critic_prompt,
    select_intervention_candidate,
    build_failure_diagnosis_prompt,
    sanitize_diagnosis,
    format_skill_context_for_llm,
)
from .genome import (
    SkillEdit,
    SkillGenome,
    SkillOp,
    apply_edit,
    create_baseline_genome,
    validate_edit,
    _detect_rule_direction,
    MODULE_IDS,
    FITNESS_STATUS_NONE,
    FITNESS_STATUS_WEAK,
    FITNESS_STATUS_STRONG,
    OBSERVABLE_FIELDS,
    EXPECTED_DIRECTIONS,
    EXPECTED_SCOPES,
    sanitize_expected_effect_contract,
    validate_expected_effect_contract,
)
from .rule_contract import RuleSpec, render_rule, validate_rule_spec
from .action_spec import ActionSpec, compile_action_spec, sanitize_action_spec
from .fitness import (
    FitnessAssessment,
    assess_source_fitness,
    observable_process_delta,
    task_state_score,
)
from .safety_gate import GateCertificate, StructuralGate, create_structural_gate
from .failure_classifier import classify_failure
from .step_attributor import (
    AttributionSignal,
    TriggerSource,
    attribute_failure,
    runtime_invalid_signal,
)
from .signal_adapter import (
    decision_authority_invalid,
    resolve_authoritative_state,
    resolve_evolution_state,
)
from .v6_contracts import (
    LAYER_DETECTOR,
    LAYER_L1,
    LAYER_L2,
    FailureAttribution,
    build_adjudication_facts,
)
from .failure_episode import (
    UnaddressableFailure,
    build_failure_episode,
    project_provider_artifacts,
)
from .module_charter import validate_action_surface
from .policy_genome import (
    FITNESS_STATUS_STRONG,
    PolicyEdit,
    PolicyGenome,
    PolicyOp,
    _validate_item_contract,
    apply_policy_edit,
    create_baseline_policy_genome,
    validate_policy_edit,
)
from .policy_reflection import (
    build_policy_selection_prompt,
    plan_policy_candidates,
    select_policy_candidate,
)
from ..htc_tes import (
    HallucinationType,
    HallucinationTypeClassifier,
    TypeConditionedEvolution,
    TypeAwareFitness,
    classify_hallucination_type,
)
from ..provider_models import canonical_model_name
from ...provider_routes import has_provider_credential

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
            gate_decision=str(data.get("gate_decision") or "skip_stable"),
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
    # + runtime_invalid 可被外部审计对账（任务二计数不变式）。
    runtime_invalid_cases: int
    use_gold: bool
    halted: bool
    halt_reason: str
    duration_seconds: float
    evolution_audit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "agenthallu.bccse_v5_online_evolution.v3",
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


def _classify_replay_no_effect(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> str:
    """Split a no-effect source replay into a deterministic cause class.

    - ``output_changed_then_quarantined``: provider output moved but the
      court quarantined it (raw/valid counts diverged).
    - ``provider_output_changed_but_court_invariant``: provider-visible
      counts moved but the court's valid bookkeeping and terminal did not.
    - ``no_observable_change_any_layer``: nothing addressable moved, i.e.
      the rule condition never matched or the provider ignored the rule.

    Only health/court-addressable counts are compared; the semantic split
    (condition vs provider obedience) stays with the reflection loop.
    """
    def health_counts(value: Mapping[str, Any] | None) -> dict[str, int]:
        value = value or {}
        health = value.get("evidence_health")
        if not isinstance(health, Mapping):
            return {}
        out: dict[str, int] = {}
        for key in (
            "construct_raw_candidate_count",
            "construct_accepted_candidate_count",
            "required_candidate_count",
            "resolved_candidate_count",
            "quarantined_error_certificate_count",
            "quarantined_defense_certificate_count",
            "focused_prosecution_certificate_count",
            "appeal_candidate_count",
        ):
            try:
                out[key] = int(health.get(key) or 0)
            except (TypeError, ValueError):
                out[key] = 0
        return out

    def valid_counts(value: Mapping[str, Any] | None) -> dict[str, int]:
        value = value or {}
        adjudication = value.get("proof_adjudication")
        if not isinstance(adjudication, Mapping):
            return {}
        out: dict[str, int] = {}
        for key in (
            "valid_error_certificate_count",
            "valid_defense_certificate_count",
        ):
            try:
                out[key] = int(adjudication.get(key) or 0)
            except (TypeError, ValueError):
                out[key] = 0
        return out

    before = before or {}
    after = after or {}
    health_moved = health_counts(after) != health_counts(before)
    valid_moved = valid_counts(after) != valid_counts(before)
    if not health_moved and not valid_moved:
        return "no_observable_change_any_layer"
    if valid_moved:
        return "provider_output_changed_but_court_invariant"
    return "output_changed_then_quarantined"


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
        elif any("llm_conflict" in value for value in reasons):
            stage = "conflict"
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
    "rule_spec_contract",
    "content_too_short",
    "not_if_then_instruction",
    "multi_step_pseudocode",
    "condition_overload",
    "multiple_actions",
    "action_target_outside_schema",
    "cross_module_near_duplicate",
    "cross_module_merge_required",
    "merge_required",
    "diagnostic_only_condition",
    "cross_module_reference",
    "modify_bloat",
    "duplicate_content",
    "source_no_effect",
})


def _structural_rejection_retryable(reasons: Sequence[str]) -> bool:
    """Whether a fresh proposal can safely repair this structural failure."""
    categories = {str(reason).split(":", 1)[0] for reason in reasons}
    return bool(categories) and categories <= _REPAIRABLE_STRUCTURAL_REASONS


# v5.8 fix 2: structural rejection categories are translated into stable,
# case-agnostic hard constraints for the bounded retry prompt.  Only
# registered categories exist; raw reject text is never echoed (no prompt
# poison, no evaluator metadata).
_RUNTIME_RULE_CONSTRAINT_TEXT: dict[str, str] = {
    "content_too_short": (
        "rule content must be a substantive natural-language instruction"
    ),
    "not_if_then_instruction": (
        "the rule must be an observable 'if/when <condition> -> <one action>'"
        " instruction"
    ),
    "multi_step_pseudocode": (
        "no multi-step pseudocode or procedures; prescribe one observable"
        " action"
    ),
    "condition_overload": (
        "the rule condition must be ONE clause with at most one conjunction"
        " (and/or/且/并且/以及/或); the structural gate rejects conditions"
        " with two or more conjunctions, so keep one decisive observable"
        " pattern instead of a compound enumeration"
    ),
    "multiple_actions": (
        "the rule must prescribe exactly one observable action"
    ),
}
_STRUCTURAL_CATEGORY_CONSTRAINT_TEXT: dict[str, str] = {
    "duplicate_content": (
        "the content duplicates an existing rule; propose a genuinely"
        " different discriminator or use modify_rule/remove_rule"
    ),
    "cross_module_near_duplicate": (
        "the content is a near-duplicate of another module's rule; propose a"
        " genuinely distinct discriminator"
    ),
    "cross_module_merge_required": (
        "the proposal semantically overlaps an existing rule; return a"
        " distinct single-action discriminator, never a compound merge"
    ),
    "merge_required": (
        "the proposal semantically overlaps an existing rule; return a"
        " distinct single-action discriminator, never a compound merge"
    ),
    "semantic_overlap_requires_distinct_discriminator": (
        "the proposal semantically overlaps an existing rule; return a"
        " distinct single-action discriminator, never a compound merge"
    ),
    "diagnostic_only_condition": (
        "conditions must not reference internal diagnostic state, dataset"
        " labels or case identifiers"
    ),
    "cross_module_reference": (
        "the rule must not reference other modules or their rules"
    ),
    "modify_bloat": (
        "modify_rule must keep a comparable length and structure to the"
        " original rule"
    ),
    "source_no_effect": (
        "the previous edit produced no observable target change; propose an"
        " intervention with a concrete observable effect contract"
    ),
    "action_target_outside_schema": (
        "the action target must be one of the module's registered"
        " provider-schema output fields (see the valid output-field targets"
        " list); a target the provider has no schema slot for is inert"
    ),
    "case_specific_pattern": (
        "the rule must be general: no case-specific identifiers or dataset"
        " labels"
    ),
    "label_or_case_leakage": (
        "the rule must not mention dataset labels, case identifiers or"
        " evaluator fields"
    ),
}


def _structural_reject_category(reason: str) -> str:
    """Reduce one structural reject reason to a stable case-agnostic category.

    ``runtime_rule_contract:condition_overload: 3 rules already share ...``
    becomes ``runtime_rule_contract:condition_overload``; the free-text tail
    is dropped so retry feedback never carries case-specific content.
    """
    text = str(reason or "").strip()
    if not text:
        return ""
    prefix, _, detail = text.partition(":")
    prefix = prefix.strip()
    if not detail.strip():
        return prefix
    key = detail.strip().split(" ", 1)[0].strip(":").strip()
    if not key:
        return prefix
    if (
        prefix in ("runtime_rule_contract", "rule_spec_contract")
        and key in _RUNTIME_RULE_CONSTRAINT_TEXT
    ):
        return f"{prefix}:{key}"
    if key in _STRUCTURAL_CATEGORY_CONSTRAINT_TEXT:
        return f"{prefix}:{key}" if prefix else key
    return prefix


def _structural_hard_constraint(category: str) -> str | None:
    """Map a stable structural category to its retry hard-constraint text."""
    value = str(category or "")
    prefix, _, detail = value.partition(":")
    if prefix in ("runtime_rule_contract", "rule_spec_contract"):
        runtime_text = _RUNTIME_RULE_CONSTRAINT_TEXT.get(detail)
        if runtime_text:
            return runtime_text
    return (
        _STRUCTURAL_CATEGORY_CONSTRAINT_TEXT.get(value)
        or _STRUCTURAL_CATEGORY_CONSTRAINT_TEXT.get(detail)
        or _STRUCTURAL_CATEGORY_CONSTRAINT_TEXT.get(prefix)
    )


def _structural_retry_hard_constraints(
    prior_categories: Sequence[str],
) -> list[str]:
    """Collect the binding hard-constraint texts for prior categories."""
    return sorted({
        constraint
        for category in prior_categories
        if str(category).startswith("structural:")
        for constraint in (
            _structural_hard_constraint(
                str(category)[len("structural:"):],
            ),
        )
        if constraint
    })


def _build_structural_system_constraints(
    prior_categories: Sequence[str],
) -> str:
    """Render binding structural constraints for the retry SYSTEM prompt.

    v5.8 fix 2 strengthening: the dry-run showed a bounded retry that only
    carried the hard constraints in a trailing user-prompt feedback block
    still repeated the same ``condition_overload`` defect.  On a retry the
    constraints are therefore also promoted into the system prompt, where
    they hold instruction-level priority.  Deterministic and case-agnostic:
    only registered category texts exist.
    """
    constraints = _structural_retry_hard_constraints(prior_categories)
    if not constraints:
        return ""
    lines = "\n".join(f"- {constraint}" for constraint in constraints)
    return (
        "\n\n[STRUCTURAL HARD CONSTRAINTS - binding for this attempt]\n"
        "A previous proposal for this source was rejected by the structural"
        " gate. The constraints below are binding; violating any of them"
        " will reject this attempt as well:\n" + lines
    )


# Differential retry guidance per deterministic no-effect class: the next
# reflection must know WHY nothing moved, not only that nothing moved.
_NO_EFFECT_RETRY_GUIDANCE: dict[str, str] = {
    "output_changed_then_quarantined": (
        "the previous edit moved provider output but the court quarantined "
        "it: tighten the condition or fix the emitted fields, do not switch "
        "the intervention surface"
    ),
    "provider_output_changed_but_court_invariant": (
        "provider-visible counts moved but the court terminal did not: the "
        "edit acts below the terminal edge; retarget it at a field the "
        "proof court actually consumes"
    ),
    "no_observable_change_any_layer": (
        "nothing addressable moved: the condition likely never matched or "
        "the action was not injected; re-check the opcode and condition "
        "against the module's observable fields"
    ),
}


def _build_mutation_retry_feedback(
    *,
    attempt_index: int,
    prior_categories: Sequence[str],
    rejected_prior: Sequence[Mapping[str, Any]],
) -> str:
    """Render the MUTATION_RETRY_FEEDBACK block; empty string when nothing.

    v5.8 fix 2: structural rejection categories recorded with the
    ``structural:`` marker are additionally translated into explicit
    ``structural_hard_constraints`` so a bounded retry cannot repeat the same
    structural defect.  The block also changes the request body (and thus the
    provider cache key) relative to the initial attempt.  Only stable
    case-agnostic categories are used; raw provider text is never echoed.
    """
    retry_block: dict[str, Any] = {}
    if prior_categories:
        retry_block["attempt_index"] = attempt_index
        retry_block["previous_failure_categories"] = list(prior_categories)
        retry_block["required_action"] = (
            "previous mutation output was structurally "
            "invalid or had no observable target change; "
            "return a DIFFERENT valid JSON edit with a distinct "
            "trigger, a distinct output action, and if possible a "
            "different owner module, with operation in "
            "{add_rule,modify_rule,remove_rule,no_edit} and "
            "non-empty condition/action/evidence_anchor slots"
        )
        hard_constraints = _structural_retry_hard_constraints(prior_categories)
        if hard_constraints:
            retry_block["structural_hard_constraints"] = hard_constraints
    if rejected_prior:
        retry_block["rejected_source_attempts"] = [
            {
                "target_module": prior["target_module"],
                "observable_deltas": prior["observable_deltas"],
                **(
                    {
                        "no_effect_guidance": _NO_EFFECT_RETRY_GUIDANCE[
                            prior["no_effect_classification"]
                        ],
                    }
                    if str(prior.get("no_effect_classification") or "")
                    in _NO_EFFECT_RETRY_GUIDANCE else {}
                ),
            }
            for prior in rejected_prior
        ]
        retry_block.setdefault("required_action", (
            "return a DIFFERENT valid JSON edit with a distinct "
            "trigger, a distinct output action, and if possible a "
            "different owner module"
        ))
    if not retry_block:
        return ""
    return (
        "\n[MUTATION_RETRY_FEEDBACK]\n"
        + json.dumps(retry_block, ensure_ascii=False, sort_keys=True)
    )


# Gold polarity constraint for L1 owner hypotheses. A false negative (gold
# says error, detector verdict clean) may only be owned by modules that can
# tighten error detection; a false positive (gold says clean, detector
# verdict error) may only be owned by modules that can relax or bound the
# error claim. Structural invariant: failure attribution never crosses the
# gold polarity side.
_FN_POLARITY_MODULES = frozenset({
    "candidate_trigger", "evidence_obligation", "candidate_to_verifier_route",
    "atomic_verifier_instruction", "causal_link_test", "abstention_rule",
    "clean_defense_boundary",
})
_FP_POLARITY_MODULES = frozenset({
    "clean_defense_boundary", "abstention_rule", "causal_link_test",
    "earliest_origin_tiebreak", "evidence_obligation",
})


def _policy_rejection_signature(
    decision: str,
    reason: str,
    proposal: PolicyEdit | None,
) -> str:
    """Render one L2 rejection with the proposal's closed-vocabulary
    semantic signature, so the next policy reflection can recognise and
    avoid the same ineffective edit instead of only the decision label."""
    signature = f"{decision} ({reason})"
    if proposal is None:
        return signature
    if proposal.new_item is not None:
        item = proposal.new_item
        params = ",".join(
            f"{key}={value}"
            for key, value in sorted(item.params.items())
        )
        signature += (
            f" proposal={str(proposal.operation)}:"
            f"{item.precondition}->{item.action}({params})"
        )
    else:
        signature += f" proposal={str(proposal.operation)}"
    return signature[:500]


def _prevalidate_candidate(
    candidate: Mapping[str, Any],
    allowed_modules: Sequence[str],
) -> str | None:
    """Return the reject reason for a candidate that cannot pass the
    admission gate, or None when it is structurally eligible.

    Runs before the critic so ranking only ever sees executable candidates
    (ownership, operation and slot shape mirror the post-critic gate), and
    every discarded candidate keeps its reject reason in the audit trail.
    """
    target_module = str(candidate.get("target_module") or "")
    operation = str(candidate.get("operation") or "")
    if operation == "no_edit":
        # A no_edit candidate is a legitimate retreat, not an invalid
        # operation: it flows to the upper no_edit branch (SKIP_NO_EDIT)
        # instead of being rejected as an unowned operation.
        return None
    if target_module not in allowed_modules:
        return f"target_module_outside_allowed:{target_module or 'missing'}"
    if operation not in {"add_rule", "modify_rule", "remove_rule"}:
        return f"unknown_operation:{operation or 'missing'}"
    if operation in {"add_rule", "modify_rule"} and not all(
        str(candidate.get(key) or "").strip()
        for key in ("condition", "action", "evidence_anchor")
    ):
        return "rule_spec:missing_slots"
    if operation in {"add_rule", "modify_rule"}:
        action_ok, action_reason = validate_action_surface(
            target_module, str(candidate.get("action") or ""),
        )
        if not action_ok:
            return f"rule_spec:{action_reason}"
    contract = candidate.get("expected_effect_contract")
    if isinstance(contract, Mapping) and contract:
        shape_ok, shape_reason = validate_expected_effect_contract(contract)
        if not shape_ok:
            return f"invalid_intervention_contract:{shape_reason}"
        contract_field = str(contract.get("observable_field") or "")
        if contract_field and contract_field not in MODULE_OBSERVABLE_FIELDS.get(
            target_module, ()
        ):
            return f"invalid_intervention_contract:field_not_owned:{contract_field}"
        if (
            str(contract.get("expected_direction") or "") not in EXPECTED_DIRECTIONS
            or str(contract.get("expected_scope") or "") not in EXPECTED_SCOPES
            or not str(contract.get("trigger_condition") or "").strip()
        ):
            return "invalid_intervention_contract:field_shape_mismatch"
    return None


def _attribute_failure(
    *,
    signal: Any,
    detection_result: Mapping[str, Any] | None,
    primary_type: Any,
    case_id: str,
    non_l1_failure_attributions: list[dict[str, Any]],
) -> tuple[str, str, FailureAttribution]:
    """Attribute one triggered failure to an evolvable layer and artifact.

    Delegates to the deterministic failure classifier and translates the
    attribution into an L1 owner hypothesis for the bounded attribution
    route. L1 owners must pass the gold polarity gate. Non-L1 failures
    (adjudication-policy layer, detector-layer anomalies) have no L1 owner
    — guessing an L1 module for a failure outside the L1 editable surface
    ends in no-edit steps or no-effect replays — and are recorded in
    ``non_l1_failure_attributions`` as audit telemetry instead. Returns
    ``(owner, reason_code, attribution)``; empty owner when nothing is
    actionable at L1.
    """
    detection_result = detection_result or {}
    adjudication = detection_result.get("proof_adjudication") or {}
    if not isinstance(adjudication, Mapping):
        adjudication = {}
    boundary_path = detection_result.get("boundary_path")
    boundary_path_state = (
        str(boundary_path.get("state") or "")
        if isinstance(boundary_path, Mapping) else ""
    )
    gold_label = getattr(signal, "gold_label", None)
    state = str(
        getattr(signal, "state", "") or detection_result.get("state") or ""
    )
    fn_side = bool(gold_label) and state == "verified_clean"
    fp_side = gold_label is False and state == "verified_error"
    provider_artifacts = project_provider_artifacts(
        detection_result=detection_result,
        signal=signal,
        primary_type=primary_type,
    )
    attribution = classify_failure({
        "adjudication": dict(adjudication),
        "boundary_path_state": boundary_path_state,
        "provider_artifacts": provider_artifacts,
    })
    if attribution.layer == LAYER_L1:
        owner = attribution.artifact
        # Polarity gate: a hypothesis that crosses the gold side would
        # evolve the detector against the ground truth, so it is dropped.
        # Unresolved terminals carry no polarity and are accepted as-is.
        if fn_side:
            pool: frozenset[str] | None = _FN_POLARITY_MODULES
        elif fp_side:
            pool = _FP_POLARITY_MODULES
        elif state == "unresolved":
            pool = None
        else:
            pool = frozenset()
        if pool is not None and owner not in pool:
            return "", attribution.reason_code, attribution
        return owner, attribution.reason_code, attribution
    if attribution.layer in (LAYER_L2, LAYER_DETECTOR):
        non_l1_failure_attributions.append({
            "case_id": case_id,
            **attribution.to_dict(),
        })
        print(
            f"    [non-L1] failure attribution: {attribution.layer} "
            f"{attribution.artifact} ({attribution.reason_code})"
        )
    return "", attribution.reason_code, attribution


def _localization_bias_signal(
    detection_result: Mapping[str, Any] | None,
) -> str:
    """Label-free telemetry for terminal-step localization bias.

    v5.8 fix 4: when a verified_error was localized by the temporal fallback
    and the responsible step is the last audited step, earlier
    sufficient-evidence origins were likely skipped (the observed dry-run
    case localized step 12 while the planted error was step 1). Diagnostic
    only: the signal feeds routing telemetry, never the verdict.
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
    return "terminal_step_bias"


# ---------------------------------------------------------------------------
# v5.8 mechanism 3: procedural abstention ladder for FN recovery.
# ---------------------------------------------------------------------------


def _abstention_ladder_rung_complete(
    *,
    target_module: str,
    before_state: str,
    after_state: str,
    gold_label: bool | None,
    ladder_used: bool,
    assessment_passed: bool,
) -> bool:
    """Whether rung 1 of the abstention ladder completed on this edit.

    An FN source (gold hallucination) whose verdict was a confident but
    thin-evidence ``verified_clean`` becomes recoverable through two
    sequential, individually gated edits instead of one impossible edit.
    Rung 1 completes when an accepted ``abstention_rule`` edit demotes the
    clean verdict to ``unresolved``; the re-queued source then fires
    ``unresolved_should_error`` and routes ``evidence_obligation`` (rung 2).
    Every rung still passes the same structural gate, source replay and
    canary checks; no rung may be merged or credited together.  The ladder
    fires at most once per source (``ladder_used``).
    """
    return (
        target_module == "abstention_rule"
        and before_state == "verified_clean"
        and after_state == "unresolved"
        and gold_label is True
        and assessment_passed
        and not ladder_used
    )


# ---------------------------------------------------------------------------
# v5.8 mechanism 2: contrastive replay acceptance.
# ---------------------------------------------------------------------------

_CONTRAST_RESOLVED_STATES = frozenset({"verified_error", "verified_clean"})

# Per-type cap for the contrast registry: rows are persisted in every
# checkpoint and searched linearly per source, so an unbounded registry
# would grow without limit on long runs (mirrors the stable-canary cap).
_CONTRAST_REGISTRY_MAX_ROWS = 40


def _trim_contrast_registry(registry: dict[str, list[dict[str, Any]]]) -> None:
    """Keep the most recent rows per type bucket, dropping the oldest."""
    for key in list(registry):
        bucket = registry[key]
        if len(bucket) > _CONTRAST_REGISTRY_MAX_ROWS:
            registry[key] = bucket[-_CONTRAST_REGISTRY_MAX_ROWS:]


def _select_contrast_neighbor(
    entries: Sequence[Mapping[str, Any]],
    *,
    case_id: str,
) -> Mapping[str, Any] | None:
    """Pick the nearest stable same-type neighbour for a contrast replay.

    ``entries`` are same-type registry rows in processing order; nearest is
    the most recently registered row with a resolved verdict that is not the
    triggering case itself.  Deterministic and replay-cheap: neighbours are
    drawn only from cases the current run already detected.
    """
    for entry in reversed(list(entries or [])):
        if str(entry.get("case_id") or "") == case_id:
            continue
        if str(entry.get("state") or "") in _CONTRAST_RESOLVED_STATES:
            return entry
    return None


def _select_comparison_cases(
    registry: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    case_id: str,
    owner: str,
    reason: str,
    primary_type: Any,
) -> dict[str, Mapping[str, Any]]:
    """Pick one supporting case and one stable counter-case for
    contrast-condition generation (v6 phase 3).

    Positive: an earlier triggered case with the same attributed owner and
    failure reason. Negative: an earlier stable case in the same type
    bucket (a situation where the action must NOT fire). Stable cases are
    often classified UNKNOWN (no failure shape), so the unknown bucket is
    merged into the negative search. Rows are case-agnostic summaries, so
    the reflection prompt never repeats a case identity or a raw
    trajectory.
    """
    primary_key = str(getattr(primary_type, "value", primary_type) or "")
    positive_bucket = list(registry.get(primary_key, ()))
    negative_keys = [primary_key]
    if primary_key != "unknown":
        negative_keys.append("unknown")
    negative_rows = []
    for key in negative_keys:
        negative_rows.extend(registry.get(key, ()))
    positive: Mapping[str, Any] | None = None
    negative: Mapping[str, Any] | None = None
    negative_any: Mapping[str, Any] | None = None
    # Positive matching degrades: same artifact+reason, then same artifact,
    # then any non-stable row in the bucket.
    for row in reversed(positive_bucket):
        if str(row.get("case_id") or "") == case_id:
            continue
        if row.get("stable"):
            continue
        row_artifact = str(row.get("artifact") or "")
        if positive is None and row_artifact == owner and str(
            row.get("reason") or ""
        ) == reason:
            positive = row
        elif positive is None and row_artifact == owner:
            positive = row
        elif positive is None:
            positive = row
        if positive is not None:
            break
    # Negative prefers a stable case sharing the owner (same situation where
    # the action must NOT fire), falling back to any stable row.
    for row in reversed(negative_rows):
        if str(row.get("case_id") or "") == case_id:
            continue
        if row.get("stable"):
            if str(row.get("owner") or "") == owner:
                negative = row
                break
            if negative_any is None:
                negative_any = row
    if negative is None:
        negative = negative_any
    return {
        "positive": positive or {},
        "negative": negative or {},
    }


def _contrast_inversion(
    *,
    before_state: str,
    after_state: str,
    gold_label: bool | None,
) -> bool:
    """Whether a neighbour replay flip is a contrast inversion.

    A polarity flip (verified_error <-> verified_clean) is always an
    inversion.  With a gold label any task-score regression (including a
    resolved verdict demoting towards the wrong side) is an inversion.
    Without gold, transitions through ``unresolved`` are telemetry only:
    the self-supervised run has no correctness authority to call them.
    """
    if before_state == after_state:
        return False
    if (
        before_state in _CONTRAST_RESOLVED_STATES
        and after_state in _CONTRAST_RESOLVED_STATES
    ):
        return True
    if gold_label is not None:
        try:
            return task_state_score(
                after_state, gold_label,
            ) < task_state_score(before_state, gold_label)
        except ValueError:
            return False
    return False


def _rejected_proposal_duplicate(
    rejected: Sequence[Mapping[str, Any]],
    *,
    target_module: str,
    new_content: str,
) -> bool:
    """Return True when a retry repeats an already-rejected source proposal.

    A duplicate is defined by the same owner module AND a near-identical rule
    (same content hash or high token overlap). This is the enforcement arm of
    the source-scoped diversity contract: the reflection prompt says "do not
    repeat", and this gate makes that a hard rejection before any replay.
    """
    if not rejected or not new_content:
        return False
    try:
        from .genome import semantic_overlap
    except Exception:
        semantic_overlap = None
    new_hash = hashlib.sha256(new_content.encode("utf-8")).hexdigest()[:12]
    for prior in rejected:
        if str(prior.get("target_module") or "") != target_module:
            continue
        prior_hash = str(prior.get("content_hash") or "")
        if prior_hash and prior_hash == new_hash:
            return True
        prior_content = str(prior.get("content") or "")
        if semantic_overlap is not None and prior_content and new_content:
            try:
                if semantic_overlap(prior_content, new_content) >= 0.85:
                    return True
            except Exception:
                pass
    return False


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
    layer: str = "",
    artifact: str = "",
    reason_code: str = "",
    action_spec: Mapping[str, Any] | None = None,
    intervention_trace: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the structured outcome payload stored in experience memory.

    Replaces the previous free-form ``{"outcome": "<some string>"}``. Every
    field is drawn from a closed vocabulary or is already category-reduced,
    so the payload can be aggregated, filtered, and safely re-entered into a
    reflection prompt. ``layer`` / ``artifact`` / ``reason_code`` carry the
    deterministic failure attribution so experiences are retrievable by
    failure signature, not only by hallucination type.
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


def _experience_intervention_trace(
    result: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Reduce provider self-reports to a reusable execution trajectory."""
    applied_ids: list[str] = []
    output_fields: list[str] = []
    routes: list[str] = []
    applied = result.get("applied_rule_trace")
    if isinstance(applied, Mapping):
        for route, rule_ids in applied.items():
            if isinstance(rule_ids, Sequence) and not isinstance(rule_ids, str):
                routes.append(str(route))
                applied_ids.extend(str(rule_id) for rule_id in rule_ids)
    effects = result.get("rule_effects_trace")
    if isinstance(effects, Mapping):
        for route, entries in effects.items():
            if not isinstance(entries, Sequence) or isinstance(entries, str):
                continue
            routes.append(str(route))
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                applied_ids.append(str(entry.get("rule_id") or ""))
                fields = entry.get("output_fields")
                if isinstance(fields, Sequence) and not isinstance(fields, str):
                    output_fields.extend(str(field) for field in fields)
    return {
        "applied_rule_ids": applied_ids,
        "output_fields": output_fields,
        "routes": routes,
    }


def _attribute_intervention_fitness(
    assessment: FitnessAssessment,
    *,
    edit: SkillEdit,
    result: Mapping[str, Any],
) -> tuple[FitnessAssessment, dict[str, Any]]:
    """Bind a replay improvement to the compiled intervention that ran.

    Aggregate detector movement is not causal evidence by itself because a
    provider replay may vary even when it ignores the evolved instruction.
    New ActionSpec-backed rules therefore earn fitness only when their stable
    rule id is injected, reported as applied, and bound to the ActionSpec's
    output field. Older uninstrumented detector adapters remain readable.
    """
    action_spec = sanitize_action_spec(edit.action_spec)
    compiled = result.get("compiled_rule_trace")
    operation = (
        edit.operation.value
        if isinstance(edit.operation, SkillOp)
        else str(edit.operation)
    )
    if (
        not action_spec
        or operation == SkillOp.REMOVE_RULE.value
        or not isinstance(compiled, Mapping)
    ):
        return assessment, {"status": "not_instrumented"}

    target_entries = compiled.get(edit.target_module)
    rule_ids = {
        str(entry.get("rule_id") or "")
        for entry in target_entries or ()
        if isinstance(entry, Mapping) and str(entry.get("rule_id") or "")
    }
    trace = _experience_intervention_trace(result)
    applied_ids = set(trace["applied_rule_ids"])
    expected_field = str(action_spec.get("output_field") or "")
    effect_ids: set[str] = set()
    effects = result.get("rule_effects_trace")
    if isinstance(effects, Mapping):
        for entries in effects.values():
            if not isinstance(entries, Sequence) or isinstance(entries, str):
                continue
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                fields = entry.get("output_fields")
                if (
                    isinstance(fields, Sequence)
                    and not isinstance(fields, str)
                    and expected_field in {str(field) for field in fields}
                ):
                    effect_ids.add(str(entry.get("rule_id") or ""))
    executed_ids = sorted(rule_ids & applied_ids & effect_ids)
    audit = {
        "status": "executed" if executed_ids else "not_executed",
        "compiled_rule_ids": sorted(rule_ids),
        "applied_rule_ids": sorted(rule_ids & applied_ids),
        "effect_rule_ids": sorted(rule_ids & effect_ids),
        "expected_output_field": expected_field,
        "executed_rule_ids": executed_ids,
    }
    if executed_ids or not (assessment.passed or assessment.evidence_only):
        return assessment, audit
    return FitnessAssessment(
        "no_effect",
        ("intervention_not_executed",),
        assessment.task_score_before,
        assessment.task_score_after,
        assessment.local_score_before,
        assessment.local_score_after,
    ), audit


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
    # T5/T6: True only when the LLM no-conflict gate was actually consulted
    # AND returned "pass". A skipped gate (no live route) stays False, so the
    # T6 provisional path cannot fire without an explicit conflict check.
    conflict_gate_passed: bool = False


@dataclass
class PendingProbeCommit:
    """Minimal pending edit state needed to resume probe rollback."""

    case_id: str
    case_index: int
    edit: SkillEdit
    certificate: GateCertificate
    step_index: int


# ============================================================
# Development-probe certification (opt-in experience record)
# ============================================================

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


# ============================================================
# Detection with skill injection
# ============================================================

def _collect_rule_trace(
    result: Mapping[str, Any],
    meta_key: str,
) -> dict[str, list[str]]:
    """Aggregate which evolved rule ids each provider route reported.

    The skill-aware provider wrapper records rule ids in every call's meta
    (``bccse_injected_rule_ids`` for what it injected, ``bccse_applied_rule_ids``
    for what the provider self-reported applying); this walks the detection
    result's provider bookkeeping and returns route -> rule ids (order-
    preserving, deduplicated). Distinct from the compiled trace: a route that
    never ran contributes nothing, so a no-effect replay can distinguish
    "rule not injected" from "injected but ignored".
    """
    providers = result.get("providers")
    if not isinstance(providers, Mapping):
        return {}
    trace: dict[str, list[str]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            rule_ids = node.get(meta_key)
            if isinstance(rule_ids, list) and rule_ids:
                route_key = str(node.get("bccse_route") or "unknown_route")
                bucket = trace.setdefault(route_key, [])
                for rule_id in rule_ids:
                    if str(rule_id) not in bucket:
                        bucket.append(str(rule_id))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(providers)
    return trace


def _collect_injected_rule_trace(
    result: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Which evolved rule ids each provider route actually received."""
    return _collect_rule_trace(result, "bccse_injected_rule_ids")


def _collect_applied_rule_trace(
    result: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Which evolved rule ids each provider self-reported applying."""
    return _collect_rule_trace(result, "bccse_applied_rule_ids")


def _collect_rule_effects_trace(
    result: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Field-level binding: which output fields each applied rule affected,
    per provider route, from the provider's ``rule_effects`` self-report."""
    providers = result.get("providers")
    if not isinstance(providers, Mapping):
        return {}
    trace: dict[str, list[dict[str, Any]]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            effects = node.get("bccse_rule_effects")
            if isinstance(effects, list) and effects:
                route_key = str(node.get("bccse_route") or "unknown_route")
                bucket = trace.setdefault(route_key, [])
                for effect in effects:
                    if not isinstance(effect, Mapping):
                        continue
                    rule_id = str(effect.get("rule_id") or "").strip()
                    if not rule_id:
                        continue
                    fields = effect.get("output_fields")
                    field_list = (
                        [str(field) for field in fields]
                        if isinstance(fields, list) else []
                    )
                    entry = {"rule_id": rule_id, "output_fields": field_list}
                    if entry not in bucket:
                        bucket.append(entry)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(providers)
    return trace


def _run_detection_with_genome(
    genome: SkillGenome,
    case_id: str,
    record: dict[str, Any],
    config: dict[str, Any],
    output_dir: Path,
    census_root: Path | None = None,
    baseline_cache_root: Path | None = None,
    shared_cache_root: Path | None = None,
    policy_genome: PolicyGenome | None = None,
) -> dict[str, Any]:
    """Run PACA-TCL v3.5 with global skill injection.

    v6: ``policy_genome`` forwards the accepted L2 adjudication-policy
    items to the proof court. Items are closed vocabulary and evaluated
    deterministically inside the court, so the detector envelope itself is
    unchanged when no genome (or an empty one) is supplied.

    v5: Global injection for ALL cases (not conditional on unresolved).
    If genome == baseline, runs plain v3.5 without injection overhead.

    Replays use the same integrated provider boundary as source detection so
    genome-specific prompt injection remains scoped to one detector call.
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

    policy_items = (
        list(policy_genome.items)
        if policy_genome is not None and policy_genome.items
        else None
    )

    compiled_rule_trace: dict[str, list[dict[str, str]]] = {}

    def invoke() -> dict[str, Any]:
        nonlocal compiled_rule_trace
        if genome.content_hash == baseline.content_hash:
            return v35.adjudicate_case(
                case_id=case_id,
                record=record,
                config=config,
                output_dir=genome_dir,
                census_root=census_root,
                policy_items=policy_items,
            )
        with integrated_v35_provider(genome=genome, config=config) as compiled:
            if compiled is not None:
                compiled_rule_trace = compiled.rule_trace()
            return v35.adjudicate_case(
                case_id=case_id,
                record=record,
                config=config,
                output_dir=genome_dir,
                census_root=census_root,
                policy_items=policy_items,
            )

    result = invoke()
    if not isinstance(result, dict):
        raise RuntimeError("detector returned a non-object result")
    if compiled_rule_trace:
        # Compiled rule trace: which evolved rules exist in this genome.
        # Audit telemetry only; it never participates in a verdict. The
        # per-route injected/applied/field-effect traces live in the result
        # too.
        result["compiled_rule_trace"] = compiled_rule_trace
        result["injected_rule_trace"] = _collect_injected_rule_trace(result)
        result["applied_rule_trace"] = _collect_applied_rule_trace(result)
        result["rule_effects_trace"] = _collect_rule_effects_trace(result)
    # Formal evolution must never consume legacy rows that omit the explicit
    # authority field.  Compatibility evaluators may still inspect such rows,
    # but they cannot enter source fitness or champion promotion.
    authority = str(result.get("decision_authority") or "").strip().lower()
    if not authority:
        raise RuntimeError("detector returned missing decision authority")
    proof = result.get("proof_adjudication")
    proof_authority = (
        str(proof.get("decision_authority") or "").strip().lower()
        if isinstance(proof, Mapping) else ""
    )
    if authority != "authoritative" or (
        proof_authority and proof_authority != "authoritative"
    ) or decision_authority_invalid(result):
        # Fail closed with the full diagnostic on the exception: the
        # runtime-failure record persists it verbatim, so a fail-closed case
        # never needs an external replay to recover its root cause.
        details = _authority_failure_details(result)
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


def _authority_failure_details(result: dict[str, Any]) -> dict[str, Any]:
    """Collect machine-readable failure diagnostics from a detector result.

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

_CHECKPOINT_SCHEMA_VERSION = "agenthallu.bccse_v5_checkpoint.v4"
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
            f"(expected {_CHECKPOINT_SCHEMA_VERSION!r}); v3 checkpoints "
            "must be re-run — the v4 L2 policy surface and artifact-first "
            "experience signatures were not persisted before this version"
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
    type_fitness: Any,
    evolution_case_ids: Sequence[str],
) -> dict[str, Any]:
    """Restore in-memory evolution state from a loaded checkpoint.

    Mutates the supplied genomes/experience-memory in place and returns a
    dict of the remaining scalar/collection state that the caller must
    bind to the matching local variables before entering the work queue.
    """
    champion_payload = dict(checkpoint.get("champion_genome_payload") or {})
    if champion_payload:
        restored_champion = SkillGenome.from_dict(champion_payload)
        champion_genome.modules = restored_champion.modules
        champion_genome.edits_applied = restored_champion.edits_applied
        champion_genome.parent_hash = restored_champion.parent_hash
        champion_genome.generation = restored_champion.generation
        champion_genome.metadata = restored_champion.metadata
    # An uncommitted shadow genome is never restored.  The reversible working
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
            type_fitness.get_experience_memory().from_dict(memory_payload)

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


# Single source of truth: the OCSE packet firewall owns the evaluator-only
# field list so both detector boundaries strip the identical label set.
_EVALUATOR_ONLY_FIELDS = packet.EVALUATOR_ONLY_FIELDS


def _detector_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the detector envelope with evaluator-only labels removed."""
    return {
        key: value
        for key, value in record.items()
        if key not in _EVALUATOR_ONLY_FIELDS
    }


def _evaluator_gold(record: Mapping[str, Any]) -> bool | None:
    """Join a label only inside an evaluator; never pass it to detection.

    Both label keys accept booleans and the string token forms handled by
    ``coerce_gold_label`` (dataset dumps are inconsistent about types);
    unrecognizable values stay ``None`` so the opt-in gold paths remain
    fail-closed.
    """
    value = coerce_gold_label(record.get("gold_label"))
    if value is not None:
        return value
    return coerce_gold_label(record.get("is_hallucination"))


# ============================================================
# Main evolution loop
# ============================================================


def _llm_no_conflict_gate(
    new_rule: str,
    existing_rules: Sequence[str],
    *,
    target_module: str,
    mutation_route: dict[str, Any] | None,
    cache_dir: Path | None = None,
    budget_usage: dict[str, int] | None = None,
    mutation_call_limit: int | None = None,
) -> tuple[bool, str, list[int], bool]:
    """LLM no-conflict gate (T5).

    Checks whether ``new_rule`` semantically conflicts with the target
    module's existing rules. Two rules conflict when, under the same trigger
    conditions, they prescribe contradictory verdicts/actions or one directly
    negates the other. Mere condition overlap or complementarity is NOT a
    conflict.

    The gate reuses the mutation route (AutoDL, thinking disabled). It runs
    only when a live provider credential is configured, so offline/test runs
    without a credential skip it (structural dedup still provides the baseline
    safety). When the gate runs it is fail-closed: any provider exception or
    parse failure is treated as a conflict, because a safety gate must never
    default to "pass" on uncertainty.

    Returns ``(passed, reason, conflicting_rule_indices, ran)``. ``ran`` is
    True only when the LLM was actually consulted; a skipped gate returns
    ``passed=True`` with ``ran=False`` so callers can distinguish a verified
    "no conflict" from a gate that could not run.
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
    rules_block = "\n".join(
        f"[{i}] {rule}" for i, rule in enumerate(existing)
    )
    user_prompt = (
        f"【目标模块】{target_module}\n"
        f"【已有规则】\n{rules_block}\n\n"
        f"【新增规则】\n{new_rule}\n\n"
        "判断新增规则是否与已有规则冲突。仅返回JSON："
        '{"decision":"pass"或"conflict","reason":"简短理由",'
        '"conflicting_rule_indices":[冲突的已有规则索引]}'
    )
    try:
        if budget_usage is not None:
            if (
                mutation_call_limit is not None
                and budget_usage["mutation_calls"] >= mutation_call_limit
            ):
                budget_usage["mutation_budget_exhausted"] = (
                    budget_usage.get("mutation_budget_exhausted", 0) + 1
                )
                return False, "conflict_gate_budget_exhausted", [], True
            budget_usage["mutation_calls"] += 1
        result, _meta = json_object_provider_call(
            route=mutation_route,
            purpose="mutation",
            system=system_prompt,
            user=user_prompt,
            cache_dir=(
                cache_dir if cache_dir is not None
                else Path(".conflict_gate_cache")
            ),
        )
    except Exception:
        if budget_usage is not None:
            budget_usage["mutation_errors"] += 1
        return False, "provider_exception", [], True
    if budget_usage is not None and isinstance(_meta, Mapping) and _meta.get("cache_hit"):
        budget_usage["mutation_cache_hits"] = (
            budget_usage.get("mutation_cache_hits", 0) + 1
        )
    if budget_usage is not None and isinstance(_meta, Mapping):
        # A cache hit served zero network traffic: the cached record carries
        # the ORIGINAL transport attempts/repair flag, so accumulating them
        # here would inflate the counters once per replay of the same entry.
        if not _meta.get("cache_hit"):
            budget_usage["mutation_transport_attempts"] = (
                budget_usage.get("mutation_transport_attempts", 0)
                + int(_meta.get("transport_attempts") or 0)
            )
            budget_usage["mutation_repairs"] = (
                budget_usage.get("mutation_repairs", 0)
                + int(bool(_meta.get("repair_used")))
            )
    if not isinstance(result, dict):
        return False, "parse_failure", [], True
    decision = str(result.get("decision", "")).strip().lower()
    reason = str(result.get("reason", "")).strip() or "llm_conflict"
    raw_indices = result.get("conflicting_rule_indices", [])
    if not isinstance(raw_indices, list):
        raw_indices = []
    indices: list[int] = []
    for value in raw_indices:
        try:
            indices.append(int(value))
        except (TypeError, ValueError):
            continue
    if decision == "pass":
        return True, reason, [], True
    return False, reason, indices, True


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
    initial_policy_genome: PolicyGenome | None = None,
    pro_mutation_route: dict[str, Any] | None = None,
    run_id: str | None = None,
    resume_from: Path | None = None,
    gold_reflection: bool = False,
    probe_experience_record: bool = False,
    reflection_candidate_count: int = 1,
    max_mutation_calls: int = 100,
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
        initial_policy_genome: Optional audited L2 adjudication-policy genome
            to continue evolving. When omitted, evolution starts from the
            empty baseline policy set. Ignored when resuming (the
            checkpoint's policy genome takes precedence), symmetric with
            ``initial_genome``.
        pro_mutation_route: Optional deeper-diagnosis route used only when a
            source exhausts its bounded Flash attempts with a categorical
            no-edit outcome (no candidates, or the diagnosis stage could not
            produce an executable owner). One Pro attempt is then allowed on
            the same source; when omitted no escalation happens.
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
        probe_experience_record: Opt-in (default False). When True, each
            passing development probe writes a certification record
            (certified / neutral / regressed) for its pending edits into the
            HTC-TES experience memory. Metadata-only: the memory schema and
            the default (off) behavior are unchanged.

    Returns:
        OnlineEvolutionResult with champion genome and audit trail.
    """
    if not evolution_case_ids:
        raise ValueError("evolution_case_ids must not be empty")
    if reflection_candidate_count < 1 or reflection_candidate_count > 5:
        raise ValueError("reflection_candidate_count must be between 1 and 5")
    if max_mutation_calls < 1:
        raise ValueError("max_mutation_calls must be at least 1")
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
        "contrast_replays": 0,
        "policy_replays": 0,
        "probe_cases": 0,
        # ``provider_calls`` is retained as the historical detector-call
        # counter. Explicit fields avoid conflating detector replays with
        # reflection/mutation calls in audit output.
        "provider_calls": 0,
        "detector_calls": 0,
        "mutation_calls": 0,
        "mutation_errors": 0,
        "mutation_cache_hits": 0,
        "mutation_transport_attempts": 0,
        "mutation_repairs": 0,
        "mutation_retry_attempts": 0,
        "mutation_budget_exhausted": 0,
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
    # v5.8 fix 4: label-free localization-bias telemetry collected during the
    # run; audit output only, never feeds verdicts or fitness.
    localization_bias_signals: list[dict[str, str]] = []
    # Deterministic non-L1 failure attributions (adjudication-policy layer
    # and detector-layer anomalies) collected during the run; audit
    # telemetry only, never feeds verdicts or fitness.
    non_l1_failure_attributions: list[dict[str, Any]] = []
    # L2 adjudication-policy surface: closed-vocabulary policy items evolve
    # in parallel with the skill genome. Acceptance is gated by a source
    # replay that must show an observable adjudication effect, then by the
    # same gold polarity discipline used for L1 edits; a rejected candidate
    # is only ever replayed as a copy, so rollback is inherent.
    policy_genome = (
        initial_policy_genome.clone()
        if initial_policy_genome is not None
        else create_baseline_policy_genome()
    )
    l2_policy_accepts = 0
    # Probe rollback anchors for the L2 surface, symmetric with stable_genome:
    # a failed development probe restores the policy genome accepted before
    # the last passing probe, so L1 and L2 roll back under one authority.
    stable_policy_genome = policy_genome.clone()
    stable_l2_policy_accepts = 0
    l2_policy_audit: list[dict[str, Any]] = []
    rejected_policy_summaries: list[str] = []
    # v5.8 mechanism 3: procedural abstention ladder state.  A source may use
    # the ladder at most once; advances are audit telemetry only.
    abstention_ladder_used: set[str] = set()
    abstention_ladder_advances: list[dict[str, str]] = []
    # v5.8 mechanism 2: same-type neighbour registry for contrastive replay
    # acceptance.  Rows are registered when a triggered case is classified;
    # neighbours are replayed only when an edit would otherwise be accepted.
    contrast_registry: dict[str, list[dict[str, Any]]] = {}
    # v5.6: source-scoped rejected-proposal tracking. Retry must explore a
    # genuinely different intervention, not paraphrase the rejected owner. The
    # canonical signature is (case, owner module, operation, content hash) and
    # ``observable_deltas`` records what the rejected edit actually moved.
    rejected_source_proposals: dict[str, list[dict[str, Any]]] = {}
    rollback_reason = ""
    stable_genome = champion_genome.clone()
    pending_probe_step_indexes: list[int] = []
    pending_probe_commits: list[PendingProbeCommit] = []
    stable_history_length = 0
    last_probe_accepted_count = 0
    last_probe_triggered_count = 0
    active_attempt_index = 0

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
    # The causal episode payload attached to every step of the current
    # case; reset per iteration so a stale episode never leaks to the next
    # case's audit trail.
    active_episode_payload: dict[str, Any] | None = None

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
        checkpoint_input_hash = str(checkpoint.get("input_content_hash") or "")
        checkpoint_config_hash = str(checkpoint.get("config_hash") or "")
        current_input_hash = packet.stable_hash(
            {case_id: records[case_id] for case_id in evolution_case_ids}
        )
        current_config_hash = packet.stable_hash(config)
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
        gate.restore_summary(restored.get("gate_summary") or {})
        stable_canaries.update(restored.get("stable_canaries") or {})
        stable_canaries_checkpoint.update(
            restored.get("stable_canaries_checkpoint") or {}
        )
        restored_stable_memory = restored.get("stable_memory_payload") or {}
        if restored_stable_memory:
            stable_memory_payload = dict(restored_stable_memory)
        stable_history_length = int(restored.get("stable_history_length") or 0)
        last_probe_accepted_count = int(
            restored.get("last_probe_accepted_count") or 0
        )
        last_probe_triggered_count = int(
            restored.get("last_probe_triggered_count") or 0
        )
        active_attempt_index = int(restored.get("active_attempt_index") or 0)
        mutation_retry_feedback = {
            str(case_id): [str(value) for value in values]
            for case_id, values in (
                restored.get("mutation_retry_feedback") or {}
            ).items()
            if isinstance(values, list)
        }
        rejected_source_proposals = {
            str(case_id): [dict(value) for value in values]
            for case_id, values in (
                restored.get("rejected_source_proposals") or {}
            ).items()
            if isinstance(values, list)
        }
        runtime_invalid_cases = int(
            restored.get("runtime_invalid_cases") or 0
        )
        runtime_error_categories = {
            str(category): int(count)
            for category, count in (
                restored.get("runtime_error_categories") or {}
            ).items()
        }
        pending_probe_commits = []
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
            except (KeyError, TypeError, ValueError, IndexError):
                pending_probe_commits = []
                pending_probe_step_indexes = []
                break
        restored_rejected_buffer = restored.get("rejected_buffer") or ()
        for entry in restored_rejected_buffer:
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
            and restored_policy_payload.get("items")
        ):
            policy_genome = PolicyGenome.from_dict(dict(restored_policy_payload))
            # Restored policy items re-enter the deterministic evaluator on
            # the next detection, so a corrupt or vocabulary-drifted payload
            # fails closed here instead of silently skewing adjudication.
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
                item_ok, item_reason = _validate_item_contract(restored_item)
                if not item_ok:
                    raise ValueError(
                        "checkpoint policy item "
                        f"{restored_item.item_id!r} failed the closed "
                        f"vocabulary contract: {item_reason}"
                    )
        l2_policy_accepts = int(checkpoint.get("l2_policy_accepts") or 0)
        # The probe rollback anchor resumes from the last probe-stable policy
        # snapshot; checkpoints predating the snapshot field treat the
        # restored policy genome itself as the stable point.
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
        abstention_ladder_used = {
            str(value)
            for value in (checkpoint.get("abstention_ladder_used") or ())
        }
        restored_contrast_registry = checkpoint.get("contrast_registry") or {}
        contrast_registry = {
            str(hallucination_type): [
                dict(row)
                for row in (rows or ())
                if isinstance(row, Mapping)
            ]
            for hallucination_type, rows in restored_contrast_registry.items()
        }
        rejected_policy_summaries = [
            str(value)
            for value in (checkpoint.get("rejected_policy_summaries") or ())
        ][-20:]
        localization_bias_signals = [
            dict(value)
            for value in (checkpoint.get("localization_bias_signals") or ())
            if isinstance(value, Mapping)
        ]
        non_l1_failure_attributions = [
            dict(value)
            for value in (
                checkpoint.get("non_l1_failure_attributions") or ()
            )
            if isinstance(value, Mapping)
        ]
        restored_policy_audit = checkpoint.get("l2_policy_audit")
        if isinstance(restored_policy_audit, list):
            l2_policy_audit = [
                dict(entry) for entry in restored_policy_audit
                if isinstance(entry, Mapping)
            ]
        resume_next_case_index = int(restored.get("next_case_index") or 0)
        # v5.9b: cases skipped by a halt/convergence were persisted as
        # "halted" placeholder steps with next_case_index advanced past
        # them, which made a resume treat them as processed and drop them
        # forever. Rewind over any trailing unprocessed placeholder steps so
        # the queue re-examines them under the restored champion genome.
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
        if rewound:
            gate.reset_convergence()
            print(
                f"[resume] rewinding {rewound} halted/skipped placeholder "
                f"step(s); work queue restarts at case index "
                f"{resume_next_case_index}"
            )
        # A step still marked provisional at checkpoint time lost its shadow
        # edit above (uncommitted shadows are never restored), so finalize
        # the audit record instead of leaving it open forever.
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
            stable_memory_payload = type_fitness.get_experience_memory().to_dict()
        if run_id is None:
            run_id = str(checkpoint.get("run_id") or run_id or "")
        provisional_count = int(checkpoint.get("provisional_edit_count") or 0)
        shadow_hash = str(checkpoint.get("shadow_genome_hash") or "")
        champion_hash = str(checkpoint.get("champion_genome_hash") or "")
        if provisional_count > 0 or (shadow_hash and shadow_hash != champion_hash):
            print(
                f"[resume] checkpoint held {provisional_count} uncommitted "
                f"shadow edit(s); reverting to the last committed champion "
                f"(shadow evidence was not persisted)"
            )
        print(
            f"[resume] restored checkpoint from {resume_from}: "
            f"next_case_index={resume_next_case_index}, "
            f"steps={len(steps)}, edits_accepted={edits_accepted}, "
            f"edits_rejected={edits_rejected}"
        )

    if run_id is None:
        run_id = f"bccse_v5_{uuid.uuid4().hex[:8]}"

    def canary_preserved(
        parent_result: Mapping[str, Any],
        child_result: Mapping[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Require both verdict and process stability on a replay anchor."""
        try:
            parent_state = resolve_evolution_state(parent_result)
            child_state = resolve_evolution_state(child_result)
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
        policy_genome: PolicyGenome | None = None,
    ) -> dict[str, Any]:
        """Run one detector call in a counted, label-free envelope."""
        budget_usage["provider_calls"] += 1
        budget_usage["detector_calls"] += 1
        budget_field = {
            "source": "source_replays",
            "canary": "canary_cases",
            "contrast": "contrast_replays",
            "policy": "policy_replays",
            "probe": "probe_cases",
        }.get(budget_kind)
        if budget_field is not None:
            # Debit before dispatch so runtime-invalid attempts are auditable.
            budget_usage[budget_field] += 1
        replay_kwargs: dict[str, Any] = {}
        if policy_genome is not None:
            replay_kwargs["policy_genome"] = policy_genome
        return _run_detection_with_genome(
            genome=genome,
            case_id=case_id,
            record=_detector_record(records[case_id]),
            config=config,
            output_dir=output_path,
            census_root=census_root,
            baseline_cache_root=baseline_cache_root,
            shared_cache_root=shared_cache_root,
            **replay_kwargs,
        )

    def run_mutation_call(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        """Count attribution/reflection calls separately from detector calls."""
        if budget_usage["mutation_calls"] >= max_mutation_calls:
            budget_usage["mutation_budget_exhausted"] += 1
            raise RuntimeError("mutation_call_budget_exhausted")
        budget_usage["mutation_calls"] += 1
        try:
            parsed, meta = json_object_provider_call(**kwargs)
        except Exception:
            budget_usage["mutation_errors"] += 1
            raise
        if isinstance(meta, Mapping) and meta.get("cache_hit"):
            budget_usage["mutation_cache_hits"] += 1
        if isinstance(meta, Mapping):
            # Cache hits served zero network traffic; their recorded
            # transport attempts/repair flag belong to the original call and
            # must not be re-counted on every replay of the cached entry.
            if not meta.get("cache_hit"):
                budget_usage["mutation_transport_attempts"] += int(
                    meta.get("transport_attempts") or 0
                )
                budget_usage["mutation_repairs"] += int(
                    bool(meta.get("repair_used"))
                )
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
        return attribute_failure(
            payload, use_gold=use_gold, gold_reflection=gold_reflection,
        )

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
                "state": resolve_evolution_state(canary_result),
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
    if resume_from is None or not stable_canaries_checkpoint:
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
        pending_probe_payloads = []
        for item in pending_probe_commits:
            certificate_index = next(
                (
                    index for index, certificate in enumerate(gate.certificates)
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
        for entry in rejected_buffer:
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
                "schema_version": "agenthallu.bccse_v5_checkpoint.v4",
                "run_id": run_id,
                "status": status,
                "next_case_index": next_case_index,
                "evolution_case_count": len(evolution_case_ids),
                "evolution_case_ids": list(evolution_case_ids),
                "input_content_hash": packet.stable_hash(
                    {case_id: records[case_id] for case_id in evolution_case_ids}
                ),
                "config_hash": packet.stable_hash(config),
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
                "stable_genome_payload": stable_genome.to_dict(),
                "stable_canaries": dict(stable_canaries),
                "stable_canaries_checkpoint": dict(stable_canaries_checkpoint),
                "stable_memory_payload": stable_memory_payload,
                "stable_history_length": stable_history_length,
                "pending_probe_step_indexes": list(pending_probe_step_indexes),
                "pending_probe_commits": pending_probe_payloads,
                "active_attempt_index": active_attempt_index,
                "mutation_retry_feedback": mutation_retry_feedback,
                "rejected_source_proposals": rejected_source_proposals,
                "rejected_buffer": rejected_buffer_payloads,
                "runtime_invalid_cases": runtime_invalid_cases,
                "runtime_error_categories": runtime_error_categories,
                "last_probe_accepted_count": last_probe_accepted_count,
                "last_probe_triggered_count": last_probe_triggered_count,
                "counters": {
                    "stable_cases": stable_cases,
                    "triggered_cases": triggered_cases,
                    "edits_accepted": edits_accepted,
                    "edits_rejected": edits_rejected,
                    "runtime_invalid_cases": runtime_invalid_cases,
                },
                "policy_genome": policy_genome.to_dict(),
                "l2_policy_accepts": l2_policy_accepts,
                "l2_policy_audit": list(l2_policy_audit),
                # Probe rollback anchors and the in-memory evolution context
                # that a resume must reproduce exactly (behavior invariants
                # like the once-per-source abstention ladder, plus the
                # audit telemetry of the processed prefix).
                "stable_policy_genome_payload": stable_policy_genome.to_dict(),
                "stable_l2_policy_accepts": stable_l2_policy_accepts,
                "abstention_ladder_used": sorted(abstention_ladder_used),
                "contrast_registry": {
                    str(hallucination_type): list(rows)
                    for hallucination_type, rows in contrast_registry.items()
                },
                "rejected_policy_summaries": list(rejected_policy_summaries),
                "localization_bias_signals": list(localization_bias_signals),
                "non_l1_failure_attributions": list(non_l1_failure_attributions),
                "updated_at": time.time(),
            },
        )
        _atomic_write_json(
            output_dir / "htc_tes_experience_memory.json",
            type_fitness.get_experience_memory().to_dict(),
        )

    def record_step(step: EvolutionStep, *, next_case_index: int) -> None:
        step.attempt_index = active_attempt_index
        step.episode = active_episode_payload
        steps.append(step)
        persist_progress(next_case_index)

    def fitness_gold(case_id: str) -> bool | None:
        if not fitness_use_gold:
            return None
        return _evaluator_gold(records[case_id])

    def run_l2_policy_case(
        *,
        idx: int,
        case_id: str,
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

        ``stable_source`` marks a case the step attributor considered stable
        (its L2 attribution opened independently): a fruitless L2 proposal
        on such a case is audited but never advances the convergence streak,
        preserving the v5.9b invariant that stable cases cannot halt a run.

        Returns True when the policy edit was accepted.  A rejected L2
        proposal on a genuinely triggered case means the adjudication layer
        could not repair the failure; the caller then falls through to the
        L1 skill path so a skill-level intervention still gets a chance
        (policy rules cannot fix a broken detection, they only reclassify
        its adjudication).
        """
        nonlocal policy_genome, l2_policy_accepts
        accepted_outcome = False

        attribution = FailureAttribution(
            layer=str(l2_attribution.get("layer") or LAYER_L2),
            artifact=str(l2_attribution.get("artifact") or ""),
            reason_code=str(l2_attribution.get("reason_code") or ""),
            evidence=dict(l2_attribution.get("evidence") or {}),
        )
        adjudication = detection_result.get("proof_adjudication") or {}
        if not isinstance(adjudication, Mapping):
            adjudication = {}
        boundary_path = detection_result.get("boundary_path")
        boundary_path_state = (
            str(boundary_path.get("state") or "")
            if isinstance(boundary_path, Mapping) else ""
        )
        facts = build_adjudication_facts(
            dict(adjudication), boundary_path_state=boundary_path_state,
        )
        facts_summary = asdict(facts)
        proposal_edit: PolicyEdit | None = None

        def finish(
            decision: str,
            *,
            reason: str = "",
            replay: Mapping[str, Any] | None = None,
        ) -> None:
            nonlocal policy_genome, l2_policy_accepts, accepted_outcome
            accepted = decision == "accept_policy"
            accepted_outcome = accepted
            if accepted:
                l2_policy_accepts += 1
                gate._consecutive_no_edit = 0
                gate._converged = False
            else:
                # A fruitless or vetoed policy reflection advances the
                # convergence streak exactly like a fruitless L1 reflection.
                # A stable-source L2 attribution must not push the streak:
                # the case was not a trigger and v5.9b forbids stable cases
                # from halting a run.
                if not stable_source:
                    gate.record_no_edit()
                # The rejection carries the proposal's closed-vocabulary
                # semantic signature so the next policy reflection can
                # recognise and avoid the same ineffective edit.
                rejected_policy_summaries.append(
                    _policy_rejection_signature(
                        decision, reason, proposal_edit,
                    )
                )
                del rejected_policy_summaries[:-20]
            audit_entry: dict[str, Any] = {
                "case_id": case_id,
                "decision": decision,
                "reason": reason,
                "attribution": attribution.to_dict(),
                "facts": facts_summary,
                "proposal": proposal_edit.to_dict() if proposal_edit else None,
                "policy_genome_hash": policy_genome.content_hash,
            }
            if isinstance(replay, Mapping):
                replay_adjudication = replay.get("proof_adjudication") or {}
                if not isinstance(replay_adjudication, Mapping):
                    replay_adjudication = {}
                audit_entry["policy_evaluation"] = dict(
                    replay_adjudication.get("policy_evaluation") or {}
                )
                audit_entry["replay_state"] = resolve_evolution_state(replay)
            l2_policy_audit.append(audit_entry)
            replay_state_value = str(
                audit_entry.get("replay_state") or detection_state
            )
            state_improved = accepted and replay_state_value != detection_state
            policy_operation = (
                str(proposal_edit.operation) if proposal_edit else "no_proposal"
            )
            policy_action = (
                str(proposal_edit.new_item.action)
                if proposal_edit and proposal_edit.new_item else "none"
            )
            type_fitness.get_experience_memory().add_experience(
                hallucination_type=HallucinationType.UNKNOWN,
                evolution_direction=(
                    f"{detection_state}_to_{replay_state_value}"
                    if replay_state_value != detection_state
                    else "l2_process_repair"
                ),
                module_targeted=attribution.artifact,
                strategy_used=(
                    f"policy:{policy_operation}:{policy_action}"
                ),
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
                    promotable=(state_improved and fitness_gold(case_id) is not None),
                    diagnostic_only=False,
                ),
                feature_description=(
                    f"{attribution.artifact}:{attribution.reason_code}"
                ),
                type_confidence=0.0,
            )
            record_step(EvolutionStep(
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
                gate_decision=decision,
                genome_hash_before=genome_before,
                genome_hash_after=current_genome.content_hash,
                detection_state=detection_state,
                timestamp=time.time(),
                fitness_check={"l2_policy": audit_entry},
            ), next_case_index=idx + 1)
            print(
                f"  [{idx}] L2_POLICY {case_id}: {decision}"
                + (f" ({reason})" if reason else "")
            )

        if mutation_route is None:
            finish("skip_no_policy", reason="no_mutation_route")
            return

        planned_candidates = plan_policy_candidates(
            attribution=attribution,
            genome=policy_genome,
        )
        if not planned_candidates:
            finish(
                "skip_no_policy",
                reason="no_executable_policy_for_attribution",
            )
            return accepted_outcome
        system_prompt, user_prompt = build_policy_selection_prompt(
            attribution=attribution,
            facts_summary=facts_summary,
            candidates=planned_candidates,
        )
        try:
            parsed, _meta = run_mutation_call(
                route=mutation_route,
                purpose="policy_reflection",
                system=system_prompt,
                user=user_prompt,
                cache_dir=output_dir / "mutation_cache" / "policy_reflection",
            )
        except Exception as exc:
            finish(
                "skip_no_policy",
                reason=f"reflection_call_failed:{type(exc).__name__}: "
                f"{str(exc)[:300]}",
            )
            return
        proposed_edit, parse_reason = select_policy_candidate(
            parsed, planned_candidates,
        )
        if proposed_edit is None:
            finish("skip_no_policy", reason=parse_reason or "no_proposal")
            return
        if proposed_edit.new_item is not None:
            proposed_edit.new_item = replace(
                proposed_edit.new_item,
                item_id=f"policy_{uuid.uuid4().hex[:8]}",
                source_case_id=case_id,
            )
        proposed_edit.source_case_id = case_id
        proposal_edit = proposed_edit
        valid, validate_reason = validate_policy_edit(
            proposal_edit, policy_genome,
        )
        if not valid:
            finish(
                "skip_no_policy",
                reason=f"invalid_policy_edit:{validate_reason}",
            )
            return
        candidate_genome = apply_policy_edit(policy_genome, proposal_edit)
        try:
            replay_result = run_detection(
                budget_kind="policy",
                genome=current_genome,
                case_id=case_id,
                output_path=(
                    output_dir / "policy_replays"
                    / f"{idx:04d}_{case_id.replace('/', '_')}"
                ),
                policy_genome=candidate_genome,
            )
        except Exception as exc:
            finish(
                "reject_policy_fitness",
                reason=f"replay_runtime_invalid:{type(exc).__name__}",
            )
            return
        replay_state = resolve_evolution_state(replay_result)
        replay_adjudication = replay_result.get("proof_adjudication") or {}
        if not isinstance(replay_adjudication, Mapping):
            replay_adjudication = {}
        evaluation = replay_adjudication.get("policy_evaluation") or {}
        if not isinstance(evaluation, Mapping):
            evaluation = {}
        fired = evaluation.get("fired") or []
        state_changed = replay_state != detection_state
        coverage_changed = (
            evaluation.get("effective_prosecution_coverage")
            != facts.prosecution_coverage
            or evaluation.get("effective_defense_coverage")
            != facts.defense_coverage
        )
        operation = str(proposal_edit.operation)
        if operation == PolicyOp.REMOVE_ITEM.value:
            # A removal is justified only by evidence: the targeted item must
            # have fired on the source detection, and removing it must move
            # the terminal edge or the effective coverage observably.
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
            removed_item = policy_genome.items[proposal_edit.item_index]
            effect_ok = (
                removed_item.item_id in original_fired_ids
                and (state_changed or coverage_changed)
            )
            no_effect_reason = "removed_item_did_not_move_the_terminal_edge"
        else:
            effect_ok = bool(fired) and (state_changed or coverage_changed)
            no_effect_reason = "policy_did_not_move_the_terminal_edge"
        if not effect_ok:
            finish(
                "reject_policy_no_effect",
                reason=no_effect_reason,
                replay=replay_result,
            )
            return
        gold_label = fitness_gold(case_id)
        if state_changed:
            if gold_label is None:
                finish(
                    "reject_policy_fitness",
                    reason="state_change_without_gold_polarity",
                    replay=replay_result,
                )
                return
            try:
                score_before = task_state_score(detection_state, gold_label)
                score_after = task_state_score(replay_state, gold_label)
            except ValueError:
                finish(
                    "reject_policy_fitness",
                    reason="unscorable_state",
                    replay=replay_result,
                )
                return
            if score_after <= score_before:
                finish(
                    "reject_policy_fitness",
                    reason="state_change_against_gold_polarity",
                    replay=replay_result,
                )
                return
        if (
            state_changed
            and operation != PolicyOp.REMOVE_ITEM.value
        ):
            # A gold-verified terminal move is the strongest evidence an
            # item can earn in this loop; lock it against later
            # modify/remove so a subsequent reflection cannot erode a
            # verified win. Coverage-only accepts stay correctable.
            protected_index = (
                proposal_edit.item_index
                if operation == PolicyOp.MODIFY_ITEM.value
                else len(candidate_genome.items) - 1
            )
            protected_meta = candidate_genome.get_item_meta(protected_index)
            protected_meta["fitness_status"] = FITNESS_STATUS_STRONG
            candidate_genome.set_item_meta(protected_index, protected_meta)
        policy_genome = candidate_genome
        finish("accept_policy", replay=replay_result)
        return accepted_outcome

    def run_development_probe(*, reason: str) -> tuple[bool, dict[str, Any]]:
        """Apply the independent development envelope as a veto-only gate."""
        nonlocal champion_genome, current_genome, stable_genome
        nonlocal policy_genome, l2_policy_accepts
        nonlocal stable_policy_genome, stable_l2_policy_accepts
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
                    # The probe must measure the detector that subsequent
                    # cases actually run: champion genome plus the accepted
                    # L2 policy surface.
                    policy_genome=policy_genome if policy_genome.items else None,
                )
                state = resolve_evolution_state(result)
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
            policy_genome = stable_policy_genome.clone()
            l2_policy_accepts = stable_l2_policy_accepts
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
            stable_policy_genome = policy_genome.clone()
            stable_l2_policy_accepts = l2_policy_accepts
            stable_history_length = len(accepted_edits_history)
            # v5.8: fail-closed probe certification. Compared against the
            # previous probe baseline; the first probe has no baseline and
            # stays neutral. The label is auditable from the step record and
            # (opt-in) persisted to the experience memory.
            previous_probe_audits = (
                probe_history[-1].get("case_audits") if probe_history else None
            )
            certification = _probe_certification(
                audit["case_audits"], previous_probe_audits
            )
            audit["probe_certification"] = certification
            if probe_experience_record:
                probe_case_ids = [
                    str(case_audit.get("case_id"))
                    for case_audit in audit["case_audits"]
                ]
                # Written before the stable-memory snapshot so a certified
                # edit's record survives together with the edit itself.
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
            # process non-regression. It remains explicitly provisional: the
            # commit never grants it protected fitness status (protection is
            # reserved for gold-assisted edits) and the run result keeps
            # final_champion_claim=False until an external authority scores
            # it. Independent canaries protect known behavior; they do not
            # establish that a newly decisive verdict is correct.
            label_free_provisional = (
                not fitness_use_gold
                and dec == "directional_evidence"
                and bool(provisional_edits[i].canary_audits)
                and all(
                    bool(audit.get("preserved"))
                    for audit in provisional_edits[i].canary_audits
                )
            )
            if (
                dec in {"strong_improvement", "local_improvement"}
                or label_free_provisional
            ):
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
                mod = champion_genome.modules[item.edit.target_module]
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
                _provisional_accept_edit = False
                _override_success = _htc_success
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
                        diagnostic_only=(
                            not fitness_use_gold
                            and not _provisional_accept_edit
                        ),
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
                    resolve_evolution_state(_shadow_result)
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

    resume_attempt_index = active_attempt_index if resume_from is not None else 0
    source_work_queue = deque(
        (
            index,
            case_id,
            resume_attempt_index if index == resume_next_case_index else 0,
        )
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
        active_episode_payload = None
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
                policy_genome=policy_genome if policy_genome.items else None,
            )
        except Exception as exc:
            runtime_error = f"{type(exc).__name__}: {exc}"
            # Diagnostic-only telemetry. A crashed detector run cannot be
            # attributed to a skill module (no verdict, no coverage, no CRS),
            # so this signal never triggers evolution. It is recorded so the
            # run audit and the type-conditioned memory can still show which
            # genome failed to execute, and why, in bounded categories.
            invalid_signal = runtime_invalid_signal(error=runtime_error)
            failure_details = getattr(exc, "failure_details", None)
            _atomic_write_json(
                output_dir / "runtime_failures" / f"{idx:04d}.json",
                {
                    "schema_version": "agenthallu.bccse_v5_failure.v2",
                    "run_id": run_id,
                    "case_id": case_id,
                    "case_index": idx,
                    "genome_hash": current_genome.content_hash,
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
            # v5.9b: a runtime-invalid detection carries no information about
            # reflection productivity; it must not advance the no-edit
            # convergence streak (infra failures are not fruitless reflection).
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
        detection_state = resolve_evolution_state(detection_result)

        # --- [2] Causal process reflection (self-supervised trigger) ---
        signal = attribution_for(detection_result, case_id)

        # LLM-enhanced attribution (v5.3): when confidence is low,
        # use LLM to refine root-cause module identification.
        if signal.attribution_confidence == "low" and mutation_route is not None:
            from dataclasses import replace as _replace_signal
            from .step_attributor import build_attribution_llm_prompt
            attribution_route_bound = {
                signal.target_module,
                signal.secondary_module,
            }
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
                        and primary in attribution_route_bound
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

        # --- Deterministic failure attribution (artifact-first) ---
        # The classifier runs on every detection, not only after the
        # self-supervised trigger: an adjudication-layer (L2) failure is
        # owned by the L2 policy surface regardless of what the step
        # attributor decides, so the L2 loop opens independently here.
        type_result = type_classifier.classify(detection_result, signal)
        evolution_direction = type_evolution._infer_evolution_direction(
            signal.state, signal.gold_label,
        )
        attribution_owner, attribution_reason, classifier_attribution = (
            _attribute_failure(
                signal=signal,
                detection_result=detection_result,
                primary_type=type_result.primary_type,
                case_id=case_id,
                non_l1_failure_attributions=non_l1_failure_attributions,
            )
        )
        # The causal episode is the audit identity of this triggered case.
        # It is built once from the authoritative attribution and travels
        # with every step of this case; the reflection proposal is not yet
        # influenced by it (Phase 1 wiring only).
        episode_payload: dict[str, Any] | None = None
        if signal.should_evolve and classifier_attribution.layer == LAYER_L1:
            route = (
                attribution_owner,
                *(
                    module for module in (signal.target_module,)
                    if module != attribution_owner
                ),
            )
            if signal.secondary_module and signal.secondary_module not in route:
                route = (*route, signal.secondary_module)
            try:
                episode_payload = build_failure_episode(
                    detection_result=detection_result,
                    signal=signal,
                    attribution=classifier_attribution,
                    evolution_direction=evolution_direction,
                    record_key=case_id,
                    allowed_modules=route,
                ).to_dict()
            except UnaddressableFailure as exc:
                episode_payload = {"unaddressable": str(exc)}
        if episode_payload is not None:
            active_episode_payload = episode_payload
        l2_attribution = next(
            (
                entry for entry in reversed(non_l1_failure_attributions)
                if str(entry.get("case_id")) == case_id
                and entry.get("layer") == LAYER_L2
            ),
            None,
        )
        if l2_attribution is not None:
            # Adjudication-layer failures are owned by the L2 policy
            # surface; guessing an L1 module for them only produces
            # no-edit steps and no-effect replays. The deterministic
            # classifier is the routing authority for this layer. A case
            # the step attributor considered stable is counted as stable
            # (its L2 attribution opened independently) and its fruitless
            # proposals never advance the convergence streak.
            l2_stable_source = not signal.should_evolve
            if active_attempt_index == 0:
                if l2_stable_source:
                    stable_cases += 1
                else:
                    triggered_cases += 1
            l2_episode_payload: dict[str, Any] | None = None
            try:
                l2_episode_payload = build_failure_episode(
                    detection_result=detection_result,
                    signal=signal,
                    attribution=FailureAttribution(
                        layer=str(l2_attribution.get("layer") or LAYER_L2),
                        artifact=str(l2_attribution.get("artifact") or ""),
                        reason_code=str(l2_attribution.get("reason_code") or ""),
                        evidence=dict(l2_attribution.get("evidence") or {}),
                    ),
                    evolution_direction=evolution_direction,
                    record_key=case_id,
                    allowed_modules=(),
                ).to_dict()
            except UnaddressableFailure as exc:
                l2_episode_payload = {"unaddressable": str(exc)}
            active_episode_payload = l2_episode_payload
            if l2_stable_source:
                # A stable-source L2 case skips the regular stable branch, so
                # register its counter-case anchor here to keep the contrast
                # pool complete.
                contrast_registry.setdefault(
                    type_result.primary_type.value, []
                ).append({
                    "case_id": case_id,
                    "state": detection_state,
                    "stable": True,
                    "owner": signal.target_module,
                })
                _trim_contrast_registry(contrast_registry)
            print(f"  [{idx}] L2_TRIGGERED {case_id}: "
                  f"{l2_attribution.get('artifact')} "
                  f"({l2_attribution.get('reason_code')})")
            if budget_usage["mutation_calls"] >= max_mutation_calls:
                if not l2_stable_source:
                    gate.record_no_edit()
                record_step(EvolutionStep(
                    case_id=case_id,
                    case_index=idx,
                    trigger_source="l2_policy_reflection",
                    attribution=dict(l2_attribution),
                    edit_proposal={
                        "operation": "no_edit",
                        "reason": "mutation_call_budget_exhausted",
                    },
                    mutation_applied=None,
                    gate_decision="skip_mutation_budget_exhausted",
                    genome_hash_before=genome_before,
                    genome_hash_after=current_genome.content_hash,
                    detection_state=detection_state,
                    timestamp=time.time(),
                ), next_case_index=idx + 1)
                print(f"  [{idx}] SKIP_MUTATION_BUDGET_EXHAUSTED {case_id}")
                continue
            # A rejected L2 proposal on a genuinely triggered case falls
            # through to the L1 skill path: the adjudication layer could not
            # repair the failure, and policy rules only reclassify
            # adjudication — they cannot fix a broken detection.  Without
            # this fall-through an unresolved or mislabeled case with an L2
            # attribution would never receive a skill-level intervention
            # (observed: openmanus/091 stuck on reject_policy_no_effect with
            # zero L1 attempts).  Stable-source L2 cases keep skipping L1:
            # they were not triggers and must not spawn fruitless reflection.
            l2_accepted = run_l2_policy_case(
                idx=idx,
                case_id=case_id,
                detection_result=detection_result,
                detection_state=detection_state,
                genome_before=genome_before,
                l2_attribution=l2_attribution,
                stable_source=l2_stable_source,
            )
            if l2_accepted or l2_stable_source:
                continue

        if not signal.should_evolve:
            # Stable success — no trigger
            if active_attempt_index == 0:
                stable_cases += 1
            stable_canaries[case_id] = detection_result
            while len(stable_canaries) > max(8, canary_limit * 4):
                stable_canaries.pop(next(iter(stable_canaries)))
            if not pending_probe_commits:
                stable_canaries_checkpoint = dict(stable_canaries)
            # Register the stable case as a counter-case anchor: the same
            # type bucket later serves contrast-condition generation as the
            # situation where the proposed action must NOT fire.
            contrast_registry.setdefault(
                type_result.primary_type.value, []
            ).append({
                "case_id": case_id,
                "state": detection_state,
                "stable": True,
                "owner": signal.target_module,
            })
            _trim_contrast_registry(contrast_registry)
            # v5.9b: a stable case is a detection success, not a fruitless
            # reflection. Feeding it into the no-edit streak conflated
            # "nothing to evolve here" with "reflection is unproductive"
            # and halted a live run after a short mixed stream (observed:
            # 5-case window consumed by 3 no-edits + 1 conflict reject + 1
            # stable case, skipping the remaining queue). Convergence is
            # defined over triggered cases only.
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

        # Deterministic short-circuit: once the mutation budget is exhausted
        # no reflection-family call can run again this run, so spending the
        # full reflection stack (type classification, attribution LLM,
        # candidate generation, conflict gate) only to fail closed at every
        # call site is pure waste. The detection above still feeds the
        # stable-canary pool and the audit trail.
        if budget_usage["mutation_calls"] >= max_mutation_calls:
            gate.record_no_edit()
            record_step(EvolutionStep(
                case_id=case_id,
                case_index=idx,
                trigger_source=signal.trigger_source.value,
                attribution=signal.to_dict(),
                edit_proposal={
                    "operation": "no_edit",
                    "reason": "mutation_call_budget_exhausted",
                },
                mutation_applied=None,
                gate_decision="skip_mutation_budget_exhausted",
                genome_hash_before=genome_before,
                genome_hash_after=current_genome.content_hash,
                detection_state=detection_state,
                timestamp=time.time(),
            ), next_case_index=idx + 1)
            print(f"  [{idx}] SKIP_MUTATION_BUDGET_EXHAUSTED {case_id}")
            continue

        # Deterministic classifier authority: an L1 attribution carries
        # concrete provider-artifact evidence (missing/malformed candidate
        # or certificate), so its owner leads the route and outranks the
        # process-heuristic primary; the heuristic owner stays as a bounded
        # fallback rather than a replacement.
        if attribution_owner:
            attribution_route = (
                attribution_owner,
                *(
                    module for module in (signal.target_module,)
                    if module != attribution_owner
                ),
            )
            print(
                f"    [ATTR] owner hypothesis: {attribution_owner} via "
                f"{attribution_reason}"
            )
        else:
            attribution_route = (signal.target_module,)
        if (
            signal.secondary_module
            and signal.secondary_module not in attribution_route
        ):
            attribution_route += (signal.secondary_module,)
        bias_signal = _localization_bias_signal(detection_result)
        if bias_signal:
            localization_bias_signals.append({
                "case_id": case_id,
                "signal": bias_signal,
                "responsible_step": str(
                    detection_result.get("responsible_step")
                ),
            })
        type_allowed_modules = type_evolution.intersect_allowed_modules(
            type_result.primary_type,
            attribution_route,
            secondary_type=type_result.secondary_type,
        )
        # The classifier's artifact owner leads the allowed route when one
        # exists (label-free provider-artifact evidence); otherwise the
        # heuristic attributor's primary keeps root-cause priority. Type
        # classification may bound the route but cannot promote a module over
        # the diagnosed artifact. Repeated here as an execution-boundary
        # invariant so future classifier refactors cannot silently reorder it.
        _lead_owner = attribution_owner or signal.target_module
        if _lead_owner in type_allowed_modules:
            type_allowed_modules = (
                _lead_owner,
                *(
                    module for module in type_allowed_modules
                    if module != _lead_owner
                ),
            )
        # The attribution owner is derived from label-free runtime evidence
        # and is already polarity-bounded. A partial typed intersection can
        # silently drop it, which reintroduces the exact NO_EDIT deadlock
        # the attribution exists to break. Keep it reachable as a fallback so
        # reflection always has an executable owner.
        if (
            attribution_owner
            and attribution_owner not in type_allowed_modules
        ):
            type_allowed_modules = (*type_allowed_modules, attribution_owner)
        # v5.6 owner escalation: on a retry, move owners that already produced
        # source_no_effect to the end of the allowed-module order. The model is
        # then nudged toward a different attributed owner; the hard duplicate
        # gate above rejects an actual paraphrase regardless of order.
        if active_attempt_index > 0:
            exhausted_owners = {
                str(prior.get("target_module") or "")
                for prior in rejected_source_proposals.get(case_id, [])
            }
            if exhausted_owners:
                type_allowed_modules = (
                    *(
                        module for module in type_allowed_modules
                        if module not in exhausted_owners
                    ),
                    *(
                        module for module in type_allowed_modules
                        if module in exhausted_owners
                    ),
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

        # v5.8 mechanism 2: register this triggered case as a candidate
        # same-type neighbour for later contrastive replays.  Only resolved
        # verdicts are stable enough to serve as contrast anchors; the
        # registry is replay-side bookkeeping and never feeds the verdict.
        if detection_state in _CONTRAST_RESOLVED_STATES:
            contrast_registry.setdefault(
                type_result.primary_type.value, []
            ).append({
                "case_id": case_id,
                "state": detection_state,
                "stable": False,
                "artifact": attribution_owner or signal.target_module,
                "reason": attribution_reason or "",
            })
            _trim_contrast_registry(contrast_registry)

        # --- [3] LLM Causal Process Reflection (PRIMARY PATH) ---
        # The LLM is the core engine of evolution — it reasons about WHY
        # the skill failed and GENERATES a novel edit.
        edit = None
        # Typed rule slots of the current candidate (add/modify only). SkillEdit
        # keeps only the rendered text, so the slots travel alongside the edit
        # and hand the structural gate a slot-contract check instead of a
        # free-text shape guess.
        pending_rule_slots: dict[str, Any] | None = None
        no_edit_reason = ""
        invalid_mutation_reason = ""
        reflection_audit: dict[str, Any] = {
            "requested_candidates": reflection_candidate_count,
            "generated_candidates": 0,
            "critic": None,
            "proposed_target_module": None,
            "validated_target_module": None,
        }
        allowed_modules = type_allowed_modules

        if mutation_route is not None:
            mutation_cache = output_dir / "mutation_cache"
            mutation_cache.mkdir(parents=True, exist_ok=True)

            # Prepare rich context for LLM
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
            runtime_observation = build_runtime_observation(
                detection_result, signal, allowed_modules=allowed_modules,
            )
            comparison_cases = _select_comparison_cases(
                contrast_registry,
                case_id=case_id,
                owner=attribution_owner or signal.target_module,
                reason=attribution_reason or "",
                primary_type=type_result.primary_type,
            )
            system_prompt, user_prompt = build_causal_reflection_prompt(
                signal=signal,
                current_skill_text=skill_context,
                detection_context=detection_context,
                rejected_buffer=rejected_buffer,
                template_hint=template_hint,
                accepted_edits_summary=accepted_summary,
                capacity_guidance=capacity_guidance,
                allowed_target_modules=allowed_modules,
                runtime_observation=runtime_observation,
                comparison_cases=comparison_cases,
                # Each candidate is generated by a separate request. This
                # makes the cache key and reasoning sample independent rather
                # than asking one response to expand itself into variants.
                candidate_count=1,
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
                # v5.8 fix 2 strengthening: binding structural constraints are
                # promoted into the system prompt (instruction-level priority),
                # not only echoed in the trailing user-prompt feedback block.
                system_prompt += _build_structural_system_constraints(
                    mutation_retry_feedback.get(case_id, []),
                )
                user_prompt += _build_mutation_retry_feedback(
                    attempt_index=active_attempt_index,
                    prior_categories=mutation_retry_feedback.get(case_id, []),
                    rejected_prior=rejected_source_proposals.get(case_id, []),
                )

            # One semantic reflection call per source. Invalid output remains an
            # auditable no-mutation event; it is never "prompted into" an edit.
            try:
                candidates: list[dict[str, Any]] = []
                no_edit_reasons: list[str] = []
                primary_cache_hits: list[bool] = []
                mutation_provider_unavailable = False
                round_reject_categories: list[str] = []
                generated_count = 0
                for generation_index in range(reflection_candidate_count):
                    generation_user = (
                        user_prompt
                        + f"\n<independent_generation index=\"{generation_index}\">"
                        "Produce a distinct intervention direction; do not repeat "
                        "a previous direction. The index is orchestration metadata, "
                        "not rule content.</independent_generation>"
                    )
                    if round_reject_categories:
                        # Carry the deterministic reject categories of earlier
                        # indices into the next generation so the same defect
                        # is not repeated within one multi-candidate round.
                        generation_user += (
                            "\n[PREVALIDATION FEEDBACK for this round]\n"
                            "Earlier candidates in this round were rejected by "
                            "the deterministic pre-validation with:\n- "
                            + "\n- ".join(dict.fromkeys(round_reject_categories))
                            + "\nDo not repeat these defects."
                        )
                    generated, meta = run_mutation_call(
                        route=mutation_route,
                        purpose="mutation",
                        system=system_prompt,
                        user=generation_user,
                        cache_dir=mutation_cache / f"candidate_{generation_index}",
                    )
                    primary_cache_hits.append(bool(
                        isinstance(meta, Mapping) and meta.get("cache_hit")
                    ))
                    generated_candidates, generated_no_edit = extract_intervention_candidates(
                        generated, limit=1,
                    )
                    generated_count += len(generated_candidates)
                    for candidate in generated_candidates:
                        prevalidate_reason = _prevalidate_candidate(
                            candidate, allowed_modules,
                        )
                        if prevalidate_reason is None:
                            candidates.append(candidate)
                        else:
                            round_reject_categories.append(prevalidate_reason)
                    if generated_no_edit:
                        no_edit_reasons.append(generated_no_edit)
                candidates, _ = extract_intervention_candidates(
                    {"candidates": candidates}, limit=reflection_candidate_count,
                )
                reflection_audit["primary_cache_hit"] = any(primary_cache_hits)
                reflection_audit["primary_cache_hits"] = primary_cache_hits
                reflection_audit["generated_candidates"] = generated_count
                # The deterministic pre-validation ran inside the generation
                # loop; its rejections are the audit trail for this round.
                prevalidation_rejects = [
                    {"target_module": "", "reason": reason}
                    for reason in dict.fromkeys(round_reject_categories)
                ]
                reflection_audit["prevalidated_candidates"] = len(candidates)
                reflection_audit["prevalidation_rejects"] = prevalidation_rejects
                if not candidates and prevalidation_rejects:
                    # Every generated candidate failed deterministic
                    # pre-validation (wrong owner, missing slots, bad
                    # contract). That is an admission verdict, not a
                    # "nothing executable was proposed" case: the source
                    # retries with structured feedback instead of burning
                    # the diagnosis stage on already-known rejections.
                    invalid_mutation_reason = (
                        "all_candidates_prevalidation_rejected:"
                        + prevalidation_rejects[0]["reason"]
                    )
                no_edit_reason = ""
                if len(candidates) > 1:
                    critic_system, critic_user = build_intervention_critic_prompt(
                        candidates=candidates,
                        runtime_observation=runtime_observation,
                        current_skill_text=skill_context,
                        allowed_target_modules=allowed_modules,
                    )
                    critic_parsed, _critic_meta = run_mutation_call(
                        route=mutation_route,
                        purpose="reflection_critic",
                        system=critic_system,
                        user=critic_user,
                        cache_dir=mutation_cache / "critic",
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
                                critic_audit.get("reason") or "critic found no safe candidate"
                            )
                        else:
                            invalid_mutation_reason = (
                                "critic_rejected_candidates:"
                                + str(critic_audit.get("reason") or "no_selection")
                            )
                        parsed = {}
                elif not candidates and not mutation_provider_unavailable and not invalid_mutation_reason:
                    # v5.9 structured collaboration: when the overloaded
                    # primary pass retreats to no_edit, split the work into a
                    # diagnosis-only stage and a targeted generation stage.
                    # The diagnosis locates one failing output and an
                    # executable owner; the generation pass is then bound to
                    # implement it instead of re-deriving the diagnosis.
                    # v6 phase 3: when the bounded Flash attempt cannot
                    # produce an executable owner, one Pro attempt is allowed
                    # on the same categorical outcome (no candidates).
                    parsed = {}
                    diagnosis_routes = [mutation_route]
                    if pro_mutation_route is not None:
                        diagnosis_routes.append(pro_mutation_route)
                    for diag_index, diag_route in enumerate(diagnosis_routes):
                        diag_purpose = (
                            "reflection_diagnosis"
                            if diag_route is mutation_route
                            else "reflection_pro"
                        )
                        diagnosis: dict[str, Any] = {}
                        try:
                            diag_system, diag_user = build_failure_diagnosis_prompt(
                                signal=signal,
                                runtime_observation=runtime_observation,
                                detection_context=detection_context,
                                current_skill_text=skill_context,
                                allowed_target_modules=allowed_modules,
                            )
                            diag_parsed, _diag_meta = run_mutation_call(
                                route=diag_route,
                                purpose=diag_purpose,
                                system=diag_system,
                                user=diag_user,
                                cache_dir=mutation_cache / (
                                    "diagnosis"
                                    if diag_purpose == "reflection_diagnosis"
                                    else "diagnosis_pro"
                                ),
                            )
                            diagnosis = sanitize_diagnosis(
                                diag_parsed, allowed_modules,
                                runtime_observation=runtime_observation,
                            )
                        except Exception as exc:
                            diagnosis = {
                                "owner_executable": False, "owner_module": None,
                                "failure_field": "",
                                "observed_gap": "",
                                "reasoning": f"diagnosis_unavailable:{type(exc).__name__}",
                            }
                        reflection_audit["diagnosis"] = diagnosis
                        reflection_audit["diagnosis_route"] = diag_purpose
                        if not diagnosis.get("owner_executable"):
                            continue
                        targeted_system, targeted_user = build_causal_reflection_prompt(
                            signal=signal,
                            current_skill_text=skill_context,
                            detection_context=detection_context,
                            rejected_buffer=rejected_buffer,
                            template_hint=template_hint,
                            accepted_edits_summary=accepted_summary,
                            capacity_guidance=capacity_guidance,
                            allowed_target_modules=allowed_modules,
                            runtime_observation=runtime_observation,
                            candidate_count=1,
                            targeted_diagnosis=diagnosis,
                        )
                        if active_attempt_index > 0:
                            targeted_system += _build_structural_system_constraints(
                                mutation_retry_feedback.get(case_id, []),
                            )
                        targeted_ok = False
                        try:
                            targeted_parsed, _target_meta = run_mutation_call(
                                route=diag_route,
                                purpose=(
                                    "mutation"
                                    if diag_purpose == "reflection_diagnosis"
                                    else "reflection_pro"
                                ),
                                system=targeted_system,
                                user=targeted_user,
                                cache_dir=mutation_cache / (
                                    "targeted"
                                    if diag_purpose == "reflection_diagnosis"
                                    else "targeted_pro"
                                ),
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
                                else f"targeted_generation_unavailable:{type(exc).__name__}"
                            )
                        if targeted_ok:
                            # The successful candidate supersedes any no_edit
                            # recorded by an earlier diagnosis route; a stale
                            # reason would otherwise discard the parsed edit.
                            no_edit_reason = ""
                            break
                    if not parsed and not no_edit_reason:
                        # Every diagnosis route failed to produce an
                        # executable owner (or the route call failed closed):
                        # record the categorical outcome like the legacy
                        # single-route path did.
                        no_edit_reason = (
                            no_edit_reasons[0]
                            if no_edit_reasons
                            else "LLM found no safe general edit"
                        )
                else:
                    parsed = candidates[0] if candidates else {}
                    reflection_audit["selected_index"] = 0
                raw_target_module = str(parsed.get("target_module") or "")
                reflection_audit["proposed_target_module"] = raw_target_module or None
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
                # add/modify rules are slot contracts: the LLM supplies typed
                # slots and the rule text is their deterministic render, so a
                # shape or authority violation fails here as an invalid
                # mutation instead of being re-guessed from free text.
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
                        evidence_anchor = str(parsed.get("evidence_anchor") or "")
                        # Render before direction detection: direction is a
                        # property of the text the provider will actually see.
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
                    parsed.get("rationale") or parsed.get("reasoning") or ""
                )
                feature_description = str(parsed.get("feature_description") or "")[:500]
                expected_effect = str(parsed.get("expected_effect") or "")[:240]
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
                        expected_effect = compiled_action.expected_effect_text()
                        expected_effect_contract = compiled_action.effect_contract(
                            pending_rule_slots["condition"]
                        )
                elif operation == "remove_rule" and rule_index is not None:
                    try:
                        removed_rule_index = int(rule_index)
                        removed_meta = champion.modules[
                            target_module
                        ].get_rule_meta(removed_rule_index)
                    except (KeyError, TypeError, ValueError):
                        removed_meta = {}
                    removed_action_payload = sanitize_action_spec(
                        removed_meta.get("action_spec")
                    )
                    if removed_action_payload:
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
                                removed_condition or "removed rule condition"
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
                        bool(str(diagnosis.get("failed_output") or "").strip())
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
                    # diagnosis is optional audit metadata; an incomplete
                    # object is dropped from the audit, never an admission
                    # veto (the prompt says so and the parser must match).
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
                    validate_expected_effect_contract(expected_effect_contract)
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
                        rationale[:300] or "LLM found no safe general edit"
                    )
                    print(f"    LLM requested NO_EDIT: {no_edit_reason}")
                elif operation in {"add_rule", "modify_rule", "remove_rule"} and (
                    not expected_effect or not contract_present
                ):
                    invalid_mutation_reason = "invalid_intervention_contract:missing"
                elif contract_present and not contract_valid:
                    invalid_mutation_reason = (
                        "invalid_intervention_contract:"
                        f"module={target_module},field={contract_field or 'missing'}"
                        + (
                            f",reason={contract_shape_reason}"
                            if contract_shape_reason else ""
                        )
                    )
                elif active_attempt_index > 0 and _rejected_proposal_duplicate(
                    rejected_source_proposals.get(case_id, []),
                    target_module=target_module,
                    new_content=new_content,
                ):
                    # v5.6: a retry that repeats an already-rejected source
                    # proposal (same owner + near-identical rule) is discarded
                    # before replay. This stops the same-owner paraphrase loop
                    # that repeated Camel/Octotools edits three times without
                    # spending more detector calls.
                    invalid_mutation_reason = (
                        "retry_duplicate_proposal:"
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
                        rule_slots=pending_rule_slots or {},
                        action_spec=pending_action_spec,
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

        if mutation_route is None:
            no_edit_reason = "mutation_route_unavailable"

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
                    layer="l1_provider_rule",
                    artifact=(
                        attribution_owner
                        if attribution_owner else allowed_modules[0]
                    ),
                    reason_code=attribution_reason,
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
                    "reflection": reflection_audit,
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
                    "reflection": reflection_audit,
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
            rule_slots=pending_rule_slots,
        )

        if not accepted:
            repairable_certificate = cert
            retry_scheduled = False
            # v5.6: semantic overlap is generation feedback, not permission to
            # synthesize a compound rule. Automatic cross-module merge changed
            # module ownership, modified protected rules and routinely combined
            # two actions into an inert instruction. A fresh reflection can
            # propose a genuinely distinct discriminator; the original edit is
            # never rewritten into a different operation behind the mutator.
            merge_reason = next(
                (r for r in reject_reasons if r.startswith("cross_module_merge_required")
                 or r.startswith("merge_required:")),
                None,
            )
            if merge_reason is not None and edit.operation == "add_rule":
                mutation_retry_feedback.setdefault(case_id, []).append(
                    "semantic_overlap_requires_distinct_discriminator"
                )
                retry_scheduled = (
                    mutation_route is not None
                    and active_attempt_index + 1 < max_mutation_attempts_per_case
                )
            if not accepted:
                if retry_scheduled:
                    # Record semantic-overlap feedback and regenerate a
                    # distinct single-action intervention from the same parent.
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
                        gate_decision="retry_structural",
                        genome_hash_before=genome_before,
                        genome_hash_after=current_genome.content_hash,
                        detection_state=detection_state,
                        timestamp=time.time(),
                        fitness_check={
                            "retry_scheduled": True,
                            "reflection": reflection_audit,
                            "retry_reason": "semantic_overlap; "
                            + ", ".join(
                                str(value).split(":", 1)[0]
                                for value in reject_reasons
                            ),
                        },
                    ), next_case_index=idx)
                    edits_rejected += 1
                    print(f"  [{idx}] NOVELTY_RETRY {case_id}: {reject_reasons}")
                    continue
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
                # v5.8 fix 2: a structural defect category that already
                # consumed one retry enters cooldown; the bounded retry budget
                # is never spent twice on the same defect class.
                prior_feedback = mutation_retry_feedback.get(case_id, [])
                repeated_category = any(
                    f"structural:{category}" in prior_feedback
                    for category in retry_categories
                )
                retry_scheduled = (
                    mutation_route is not None
                    and active_attempt_index + 1 < max_mutation_attempts_per_case
                    and _structural_rejection_retryable(reject_reasons)
                    and not repeated_category
                )
                if retry_scheduled:
                    # v5.8 fix 2: record stable structural categories so the
                    # retry prompt carries explicit hard constraints (and the
                    # provider cache key differs from the rejected attempt).
                    feedback = mutation_retry_feedback.setdefault(case_id, [])
                    for category in retry_categories:
                        marker = f"structural:{category}"
                        if marker not in feedback:
                            feedback.append(marker)
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
                        "reflection": reflection_audit,
                        "retry_reason": rejected_entry.feedback_summary(),
                    },
                ), next_case_index=(idx if retry_scheduled else idx + 1))
                print(f"  [{idx}] REJECT {case_id}: {reject_reasons}")
                continue

        # --- [4b] LLM no-conflict gate (T5) ---
        # Runs only after structural dedup passes (Jaccard < 0.70 against
        # every existing rule). Structural dedup failures
        # (merge_required / near_duplicate) never reach this gate. The gate
        # is advisory: a flagged conflict is recorded and blocks the T6
        # self-supervised provisional path (``conflict_gate_passed`` stays
        # False), but the edit still goes through source replay so the
        # evidence-based guards — gold task score, canary regression,
        # module-local fitness — decide admission.  The LLM's own definition
        # ("complementarity is NOT a conflict") is routinely violated for
        # ADD refinements of a general rule, so a hard veto before replay
        # discarded 4/17 gated proposals in evolve_50 without any evidence.
        # ``conflict_gate_passed`` is True only when the LLM was actually
        # consulted and returned "pass"; a skipped gate (no live route)
        # stays False so the T6 provisional path cannot fire.
        conflict_gate_passed = False
        conflict_gate_ran = False
        _conflict_flag: dict[str, Any] = {
            "passed": False, "ran": False,
        }
        if edit.operation in (SkillOp.ADD_RULE, SkillOp.MODIFY_RULE):
            _existing_rules = list(
                current_genome.modules[edit.target_module].rules
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
                mutation_route=mutation_route,
                cache_dir=output_dir / "conflict_gate_cache",
                budget_usage=budget_usage,
                mutation_call_limit=max_mutation_calls,
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
                fitness_check={"reflection": reflection_audit},
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
                    policy_genome=policy_genome if policy_genome.items else None,
                )
            except Exception as exc:
                reason = f"source_replay_runtime_error:{type(exc).__name__}:{exc}"
                fitness_audit = {
                    "reflection": reflection_audit,
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
            assessment, causal_execution_audit = _attribute_intervention_fitness(
                assessment,
                edit=edit,
                result=candidate_result,
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
                            policy_genome=policy_genome if policy_genome.items else None,
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
                "reflection": reflection_audit,
                "source_before": signal.to_dict(),
                "source_after": candidate_signal.to_dict(),
                "assessment": assessment.to_dict(),
                "causal_execution": causal_execution_audit,
                "source_stable_after": not candidate_signal.should_evolve,
                # Rule traces for no-effect attribution: ``compiled_rule_trace``
                # lists the evolved rules present in this genome;
                # ``injected_rule_trace`` lists which rules each provider route
                # received; ``applied_rule_trace`` lists which ones the provider
                # self-reported applying; ``rule_effects_trace`` binds each
                # applied rule to the output fields it affected — so a
                # no-effect replay can distinguish a rule that was never
                # injected from one the provider ignored.
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
            # T6: self-supervised provisional acceptance path. A directional
            # source replay (trigger closed, NOT worse, NOT no_effect) may be
            # admitted when the LLM no-conflict gate was actually consulted
            # and returned "pass". Canary regression has already rewritten
            # the assessment to "worse" above, so this can never fire on a
            # canary regression. ``no_effect`` and ``worse`` decisions are
            # excluded by the strict equality check on "directional_evidence".
            provisional_accept_eligible = (
                not fitness_use_gold
                and assessment.decision == "directional_evidence"
                and conflict_gate_passed
            )
            # v5.8 mechanism 2: contrastive replay acceptance.  When the edit
            # would otherwise be admitted, replay it once on the nearest
            # stable same-type neighbour; a contrast inversion (polarity
            # flip, or a gold-informed task-score regression) vetoes the
            # admission.  A neighbour replay runtime error is fail-open but
            # audited: the neighbour gate is an overfitting guard, not a
            # verdict authority.  Costs at most one detector call per
            # otherwise-accepted edit.
            contrast_audit: dict[str, Any] | None = None
            if (
                (assessment.passed or provisional_accept_eligible)
                and not canary_regressions
            ):
                neighbor = _select_contrast_neighbor(
                    contrast_registry.get(
                        type_result.primary_type.value, ()
                    ),
                    case_id=case_id,
                )
                if neighbor is not None:
                    neighbor_id = str(neighbor["case_id"])
                    neighbor_before_state = str(neighbor["state"])
                    try:
                        neighbor_result = run_detection(
                            budget_kind="contrast",
                            genome=candidate_genome,
                            case_id=neighbor_id,
                            output_path=(
                                replay_root / "contrast"
                                / neighbor_id.replace("/", "_")
                            ),
                            policy_genome=policy_genome if policy_genome.items else None,
                        )
                        neighbor_after_state = str(
                            neighbor_result.get("state") or ""
                        )
                        inverted = _contrast_inversion(
                            before_state=neighbor_before_state,
                            after_state=neighbor_after_state,
                            gold_label=fitness_gold(neighbor_id),
                        )
                        contrast_audit = {
                            "neighbor_case_id": neighbor_id,
                            "state_before": neighbor_before_state,
                            "state_after": neighbor_after_state,
                            "inverted": inverted,
                        }
                    except Exception as exc:
                        inverted = False
                        contrast_audit = {
                            "neighbor_case_id": neighbor_id,
                            "state_before": neighbor_before_state,
                            "state_after": None,
                            "inverted": False,
                            "runtime_error": f"{type(exc).__name__}:{exc}",
                        }
                    fitness_audit["contrast_replay"] = contrast_audit
                    # The neighbour replay is a real detector call owned by
                    # this fitness envelope; keep the per-edit call accounting
                    # consistent with the global budget counters.
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
                        provisional_accept_eligible = False
                        directional_canary_support = False
                        print(
                            f"  [{idx}] CONTRAST_INVERSION {case_id}: "
                            f"neighbour {neighbor_id} flipped "
                            f"{neighbor_before_state}->"
                            f"{neighbor_after_state}"
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
            if provisional_accept_eligible:
                fitness_audit["provisional_accept"] = True
                fitness_audit["shadow_admissible"] = True
            if (
                not assessment.passed
                and not directional_canary_support
                and not provisional_accept_eligible
            ):
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
                rejected_source_proposals.setdefault(case_id, []).append({
                    "target_module": edit.target_module,
                    "operation": (
                        edit.operation.value
                        if isinstance(edit.operation, SkillOp)
                        else str(edit.operation)
                    ),
                    # Store the full rule text: the retry-deduplication gate
                    # compares bigram overlap against this content, and a
                    # truncated prefix systematically underestimates overlap
                    # for long rules (letting paraphrase retries escape).
                    # This structure is in-memory only (never checkpointed),
                    # so storing full text carries no compatibility cost.
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
                        "reflection": reflection_audit,
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
            conflict_gate_passed=conflict_gate_passed,
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

        # v5.8 mechanism 3: rung 1 of the procedural abstention ladder.  An
        # accepted abstention_rule edit that demoted a confident-but-thin
        # clean verdict to unresolved re-queues the SAME source exactly once:
        # the next detection fires unresolved_should_error and routes
        # evidence_obligation (rung 2).  Each rung already passed the same
        # structural gate, source replay and canary checks individually.
        if _abstention_ladder_rung_complete(
            target_module=edit.target_module,
            before_state=str(signal.state),
            after_state=str(candidate_signal.state),
            gold_label=signal.gold_label,
            ladder_used=case_id in abstention_ladder_used,
            assessment_passed=assessment.passed,
        ):
            abstention_ladder_used.add(case_id)
            abstention_ladder_advances.append({
                "case_id": case_id,
                "rung": "abstention_demotion_clean_to_unresolved",
            })
            source_work_queue.append((idx, case_id, 0))
            print(
                f"  [{idx}] [LADDER] {case_id}: rung 1 complete "
                f"(verified_clean->unresolved); source re-queued for the "
                f"evidence_obligation rung"
            )

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
        policy_genome=policy_genome,
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
            "localization_bias_signals": localization_bias_signals,
            "non_l1_failure_attributions": non_l1_failure_attributions,
            "l2_policy_audit": l2_policy_audit,
            "l2_policy_accepts": l2_policy_accepts,
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
    write_json(output_dir / "policy_genome.json", policy_genome.to_dict())
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
