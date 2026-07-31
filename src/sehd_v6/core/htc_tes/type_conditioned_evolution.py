"""HTC-TES v3 — Type-Conditioned Evolution Module.

Implements type-conditioned skill evolution where the hallucination type
classification guides which modules to evolve, what editing strategies to use,
and how to resolve evolution direction conflicts.

Core concepts:
1. Type-conditioned routing: Different hallucination types activate different
   evolution modules and editing strategies
2. Bidirectional evolution: Simultaneously learn error-detection and
   clean-detection rules for the same hallucination type
3. Experience-conditioned generation: Past editing experiences for each type
   inform the reflection engine's prompt

Architecture:
    Detection → Type Classification → Type-Conditioned Reflection
                                         ↓
                              ┌─────────────────────────────┐
                              │  Hallucination Type Strands │
                              │  ┌──────┐  ┌──────────────┐ │
                              │  │ FP   │  │  FN/u_to_err │ │
                              │  └──────┘  └──────────────┘ │
                              │  Type-specific module targets│
                              └─────────────────────────────┘
                                         ↓
                              Type-aware Fitness Assessment
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .type_classifier import HallucinationType, TypeClassificationResult


# Type to module routing table: which modules are primary targets per type
_TYPE_MODULE_ROUTING: dict[HallucinationType, tuple[str, ...]] = {
    HallucinationType.FACTUAL: (
        "evidence_obligation",
        "atomic_verifier_instruction",
        "candidate_trigger",
    ),
    HallucinationType.INSTRUCTIONAL: (
        "clean_defense_boundary",
        "abstention_rule",
        "evidence_obligation",
    ),
    HallucinationType.TOOL: (
        "candidate_to_verifier_route",
        "atomic_verifier_instruction",
        "candidate_trigger",
    ),
    HallucinationType.REASONING: (
        "causal_link_test",
        "earliest_origin_tiebreak",
        "evidence_obligation",
    ),
    HallucinationType.OMISSION: (
        "candidate_trigger",
        "candidate_to_verifier_route",
        "evidence_obligation",
    ),
    HallucinationType.ADOPTION: (
        "evidence_obligation",
        "clean_defense_boundary",
        "candidate_trigger",
    ),
    HallucinationType.CONTEXTUAL: (
        "abstention_rule",
        "clean_defense_boundary",
        "causal_link_test",
    ),
    HallucinationType.UNKNOWN: (
        "evidence_obligation",
        "abstention_rule",
        "candidate_trigger",
    ),
}

# Type to editing strategy mapping
_TYPE_EDIT_STRATEGY: dict[HallucinationType, str] = {
    HallucinationType.FACTUAL: "strict_verification",
    HallucinationType.INSTRUCTIONAL: "boundary_enforcement",
    HallucinationType.TOOL: "tool_validation",
    HallucinationType.REASONING: "causal_chain_strengthening",
    HallucinationType.OMISSION: "trigger_sensitivity",
    HallucinationType.ADOPTION: "adoption_scrutiny",
    HallucinationType.CONTEXTUAL: "context_preservation",
    HallucinationType.UNKNOWN: "generic_improvement",
}

# Evolution direction to type-specific guidance
_DIRECTION_TYPE_GUIDANCE: dict[str, dict[HallucinationType, str]] = {
    "error_to_clean": {
        HallucinationType.FACTUAL: "Add boundary: when claim has verified tool output, it is supported",
        HallucinationType.INSTRUCTIONAL: "When agent followed explicit instruction, do not flag as error",
        HallucinationType.TOOL: "When tool returned valid data and agent used it correctly, treat as clean",
        HallucinationType.REASONING: "When reasoning steps are logically sound despite unexpected result, treat as process issue",
        HallucinationType.OMISSION: "When agent attempted required action but tool blocked it, not an omission",
        HallucinationType.ADOPTION: "When adopted information was from verified tool output, it is not unsupported",
        HallucinationType.CONTEXTUAL: "When response is within context of the conversation, treat as on-topic",
        HallucinationType.UNKNOWN: "When detector cannot identify specific failure mode, treat as clean if no clear error",
    },
    "clean_to_error": {
        HallucinationType.FACTUAL: "When claim lacks any supporting evidence, create error candidate",
        HallucinationType.INSTRUCTIONAL: "When agent explicitly violated user instruction, create error candidate",
        HallucinationType.TOOL: "When agent made unsupported tool call or misused tool, create error candidate",
        HallucinationType.REASONING: "When agent's conclusion contradicts available evidence, create error candidate",
        HallucinationType.OMISSION: "When agent failed to take required action despite clear trigger, create error candidate",
        HallucinationType.ADOPTION: "When agent adopted unverified information, create error candidate",
        HallucinationType.CONTEXTUAL: "When agent's response is clearly off-topic or out-of-context, create error candidate",
        HallucinationType.UNKNOWN: "When detector identifies clear failure pattern, create error candidate",
    },
}


@dataclass
class TypeConditionedEdit:
    """A skill edit conditioned on hallucination type.

    Extends the basic SkillEdit with type-specific metadata for
    the HTC-TES v3 framework.
    """

    edit_id: str
    target_module: str
    operation: str  # add_rule / modify_rule / remove_rule
    new_content: str
    rationale: str
    source_case_id: str
    hallucination_type: HallucinationType
    evolution_direction: str  # "error_to_clean" / "clean_to_error" / "unresolved_to_clean" / "unresolved_to_error"
    edit_strategy: str  # type-conditioned editing strategy
    type_confidence: float  # confidence of the type classification
    rule_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "edit_id": self.edit_id,
            "target_module": self.target_module,
            "operation": self.operation,
            "new_content": self.new_content,
            "rationale": self.rationale,
            "source_case_id": self.source_case_id,
            "hallucination_type": self.hallucination_type.value,
            "evolution_direction": self.evolution_direction,
            "edit_strategy": self.edit_strategy,
            "type_confidence": self.type_confidence,
            "rule_index": self.rule_index,
        }


@dataclass
class TypeConditionedEditProposal:
    """A structured edit proposal conditioned on hallucination type.

    Generated by the type-conditioned reflection engine, this proposal
    includes type-specific guidance and strategy selection.
    """

    target_module: str
    operation: str
    new_content: str
    rationale: str
    hallucination_type: HallucinationType
    evolution_direction: str
    edit_strategy: str
    type_guidance: str
    source_case_id: str
    rule_index: int | None = None

    def to_edit(self) -> TypeConditionedEdit:
        """Convert proposal to a TypeConditionedEdit."""
        return TypeConditionedEdit(
            edit_id=f"tcedit_{uuid.uuid4().hex[:8]}",
            target_module=self.target_module,
            operation=self.operation,
            new_content=self.new_content,
            rationale=self.rationale,
            source_case_id=self.source_case_id,
            hallucination_type=self.hallucination_type,
            evolution_direction=self.evolution_direction,
            edit_strategy=self.edit_strategy,
            type_confidence=1.0,
            rule_index=self.rule_index,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_module": self.target_module,
            "operation": self.operation,
            "new_content": self.new_content,
            "rationale": self.rationale,
            "hallucination_type": self.hallucination_type.value,
            "evolution_direction": self.evolution_direction,
            "edit_strategy": self.edit_strategy,
            "type_guidance": self.type_guidance,
            "source_case_id": self.source_case_id,
            "rule_index": self.rule_index,
        }


@dataclass
class TypeConditionedEvolution:
    """Type-conditioned evolution engine for HTC-TES v3.

    Routes evolution decisions based on hallucination type classification,
    ensuring that different hallucination types get appropriate detection
    experience and module targeting.

    Key features:
    1. Type-conditioned module routing
    2. Bidirectional learning (error + clean simultaneously)
    3. Type-specific editing strategies
    4. Experience accumulation per hallucination type
    """

    _experience_cache: dict[str, list[TypeConditionedEdit]] = field(
        default_factory=dict
    )

    def get_target_modules(
        self,
        hallucination_type: HallucinationType,
        primary: bool = True,
    ) -> tuple[str, ...]:
        """Get target modules for a given hallucination type.

        Args:
            hallucination_type: The classified hallucination type.
            primary: If True, return primary modules; if False, secondary.

        Returns:
            Tuple of module IDs to target.
        """
        modules = _TYPE_MODULE_ROUTING.get(
            hallucination_type, _TYPE_MODULE_ROUTING[HallucinationType.UNKNOWN]
        )
        if primary:
            return modules[:1]
        return modules[1:]

    def get_edit_strategy(self, hallucination_type: HallucinationType) -> str:
        """Get the editing strategy for a given hallucination type."""
        return _TYPE_EDIT_STRATEGY.get(
            hallucination_type, "generic_improvement"
        )

    def generate_type_guidance(
        self,
        hallucination_type: HallucinationType,
        evolution_direction: str,
    ) -> str:
        """Generate type-specific guidance for the reflection engine.

        Args:
            hallucination_type: The classified hallucination type.
            evolution_direction: The direction of evolution.

        Returns:
            Guidance string to include in the reflection prompt.
        """
        direction_guidance = _DIRECTION_TYPE_GUIDANCE.get(
            evolution_direction, {}
        )
        guidance = direction_guidance.get(
            hallucination_type,
            "Focus on improving detection for this case type.",
        )
        strategy = self.get_edit_strategy(hallucination_type)
        modules = self.get_target_modules(hallucination_type)
        return (
            f"[HTC-TES v3] Hallucination type: {hallucination_type.value}\n"
            f"Edit strategy: {strategy}\n"
            f"Target modules: {', '.join(modules)}\n"
            f"Type-specific guidance: {guidance}"
        )

    def create_edit_proposal(
        self,
        signal: Mapping[str, Any],
        type_result: TypeClassificationResult,
        allowed_modules: Sequence[str],
        source_case_id: str,
        reflection_output: Mapping[str, Any],
    ) -> TypeConditionedEditProposal | None:
        """Create a type-conditioned edit proposal.

        Args:
            signal: The attribution signal dict.
            type_result: The hallucination type classification.
            allowed_modules: Modules that can be edited.
            source_case_id: The source case ID.
            reflection_output: The LLM reflection output.

        Returns:
            TypeConditionedEditProposal or None if not applicable.
        """
        operation = str(reflection_output.get("operation") or "")
        if operation == "no_edit":
            return None

        target_module = str(reflection_output.get("target_module") or "")
        if not target_module or target_module not in allowed_modules:
            target_module = str(signal.get("target_module") or "")

        new_content = str(reflection_output.get("new_content") or "")
        if not new_content:
            return None

        rationale = str(
            reflection_output.get("rationale")
            or reflection_output.get("reasoning")
            or ""
        )

        # Determine evolution direction
        state = str(signal.get("state") or "")
        gold = signal.get("gold_label")
        direction = self._infer_evolution_direction(state, gold)

        strategy = self.get_edit_strategy(type_result.primary_type)
        guidance = self.generate_type_guidance(
            type_result.primary_type, direction
        )

        return TypeConditionedEditProposal(
            target_module=target_module,
            operation=operation,
            new_content=new_content,
            rationale=rationale,
            hallucination_type=type_result.primary_type,
            evolution_direction=direction,
            edit_strategy=strategy,
            type_guidance=guidance,
            source_case_id=source_case_id,
            rule_index=reflection_output.get("rule_index"),
        )

    def _infer_evolution_direction(
        self, state: str, gold: Any
    ) -> str:
        """Infer the evolution direction from state and gold."""
        if isinstance(gold, bool):
            if gold:
                if state == "unresolved":
                    return "unresolved_to_error"
                if state == "verified_clean":
                    return "clean_to_error"
            else:
                if state == "unresolved":
                    return "unresolved_to_clean"
                if state == "verified_error":
                    return "error_to_clean"

        # Self-supervised mode: infer from state
        if state == "unresolved":
            return "unresolved_to_clean"
        if state == "verified_error":
            return "error_to_clean"
        return "clean_to_error"

    def accumulate_experience(
        self,
        edit: TypeConditionedEdit,
        success: bool,
    ) -> None:
        """Accumulate editing experience for a hallucination type.

        Args:
            edit: The edit that was applied.
            success: Whether the edit improved detection.
        """
        type_key = edit.hallucination_type.value
        if type_key not in self._experience_cache:
            self._experience_cache[type_key] = []

        entry = {
            "edit": edit,
            "success": success,
            "direction": edit.evolution_direction,
            "strategy": edit.edit_strategy,
            "target_module": edit.target_module,
        }
        self._experience_cache[type_key].append(edit)

    def get_experience_summary(
        self, hallucination_type: HallucinationType
    ) -> dict[str, Any]:
        """Get accumulated experience summary for a hallucination type."""
        type_key = hallucination_type.value
        edits = self._experience_cache.get(type_key, [])

        total = len(edits)
        if total == 0:
            return {"type": type_key, "total_edits": 0}

        by_direction: dict[str, int] = {}
        by_module: dict[str, int] = {}
        for edit in edits:
            direction = edit.evolution_direction
            by_direction[direction] = by_direction.get(direction, 0) + 1
            module = edit.target_module
            by_module[module] = by_module.get(module, 0) + 1

        return {
            "type": type_key,
            "total_edits": total,
            "by_direction": by_direction,
            "by_module": by_module,
            "top_modules": sorted(
                by_module.items(), key=lambda x: x[1], reverse=True
            )[:3],
        }


def build_type_conditioned_reflection_prompt(
    type_result: TypeClassificationResult,
    signal: Mapping[str, Any],
    allowed_modules: Sequence[str],
    experience_summary: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Build a type-conditioned reflection prompt.

    Enhances the standard BCCSE v5 reflection prompt with hallucination
    type-specific guidance and accumulated experience.

    Args:
        type_result: The hallucination type classification result.
        signal: The attribution signal.
        allowed_modules: Modules that can be edited.
        experience_summary: Optional accumulated experience for this type.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    htype = type_result.primary_type
    confidence = type_result.confidence
    modules = _TYPE_MODULE_ROUTING.get(
        htype, _TYPE_MODULE_ROUTING[HallucinationType.UNKNOWN]
    )
    strategy = _TYPE_EDIT_STRATEGY.get(htype, "generic_improvement")

    system_prompt = f"""You are the HTC-TES v3 type-conditioned reflection engine.

The detected hallucination type is: {htype.value} (confidence: {confidence:.2f})
Edit strategy: {strategy}
Target modules for this type: {', '.join(modules)}

Your task:
1. Generate a rule that improves detection for THIS specific hallucination type
2. Focus on the target modules listed above
3. Follow the type-specific editing strategy
4. Ensure the rule is generalizable and not case-specific

Type-specific guidance:
"""

    # Add type-specific guidance
    for direction, guidance_map in _DIRECTION_TYPE_GUIDANCE.items():
        if htype in guidance_map:
            system_prompt += f"- [{direction}]: {guidance_map[htype]}\n"

    if experience_summary and experience_summary.get("total_edits", 0) > 0:
        system_prompt += f"""
Accumulated experience for type '{htype.value}':
- Total edits applied: {experience_summary['total_edits']}
- Top modules: {', '.join(m for m, _ in experience_summary.get('top_modules', []))}

Use this experience to avoid repeating ineffective patterns and focus on
strategies that have worked for this hallucination type.
"""

    error_type = str(signal.get("error_type") or "unknown")
    target_module = str(signal.get("target_module") or "")
    state = str(signal.get("state") or "")

    user_prompt = f"""Case analysis:
- Error type: {error_type}
- Current state: {state}
- Attributed module: {target_module}
- Hallucination type: {htype.value} (confidence: {confidence:.2f})
- Allowed modules: {', '.join(allowed_modules)}
- Edit strategy: {strategy}

Generate a type-specific rule that addresses this hallucination type.
The rule should:
1. Be conditioned on the detected hallucination type '{htype.value}'
2. Use the appropriate editing strategy '{strategy}'
3. Target the most relevant module for this type
4. Be generalizable to similar cases of this hallucination type

Return exactly one JSON object:
{{"target_module": "<module>", "operation": "add_rule|modify_rule", "rule_index": null,
 "new_content": "<rule>", "rationale": "<reasoning for this type>"}}
"""

    return system_prompt, user_prompt
