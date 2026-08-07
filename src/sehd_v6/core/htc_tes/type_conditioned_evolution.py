"""HTC-TES v3 — Type-Conditioned Evolution Module.

Implements type-conditioned skill evolution where the hallucination type
classification guides which modules to evolve, what editing strategies to use,
and how to resolve evolution direction conflicts.

Core concepts:
1. Type-conditioned routing: Different hallucination types activate different
   evolution modules and editing strategies
2. Bidirectional provenance: Record error/clean evolution direction on each
   edit and enforce an auditable bounded-module routing intersection
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

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .type_classifier import (
    HallucinationType,
    TypeClassificationResult,
)
from ..bccse.genome import normalize_type_confidence


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
    "unresolved_to_decisive": {
        htype: "Strengthen observable evidence construction without presuming clean or error; let the proof court decide."
        for htype in HallucinationType
    },
    "process_repair": {
        htype: "Repair the attributed evidence process without prescribing a final verdict."
        for htype in HallucinationType
    },
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
class TypeConditionedEvolution:
    """Type-conditioned evolution engine for HTC-TES v3.

    Routes evolution decisions based on hallucination type classification,
    ensuring that different hallucination types get appropriate detection
    experience and module targeting.

    Key features:
    1. Type-conditioned module routing
    2. Bidirectional typed provenance (not runtime strand isolation)
    3. Type-specific editing strategies
    4. Experience accumulation per hallucination type
    """

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

    def intersect_allowed_modules(
        self,
        hallucination_type: HallucinationType,
        attribution_modules: Sequence[str],
        secondary_type: HallucinationType | None = None,
    ) -> tuple[str, ...]:
        """Return an auditable primary/secondary typed routing intersection.

        This is provenance-aware routing, not isolated runtime strands: every
        candidate must still fall inside the attributor's bounded modules.
        """
        attributed = tuple(dict.fromkeys(str(module) for module in attribution_modules))
        allowed = set(attributed)
        typed_modules = set(
            self.get_target_modules(hallucination_type, primary=True)
            + self.get_target_modules(hallucination_type, primary=False)
        )
        if secondary_type is not None:
            typed_modules.update(
                self.get_target_modules(secondary_type, primary=True)
                + self.get_target_modules(secondary_type, primary=False)
            )
        # Causal attribution owns root-cause priority. Type classification is
        # advisory and may filter implausible modules, but must not reorder the
        # attributor's primary/secondary diagnosis. The previous typed-first
        # order turned earliest_origin_tiebreak localization failures into
        # causal_link_test mutations solely because the type was REASONING.
        primary = tuple(
            module for module in attributed
            if module in typed_modules
        )
        routed = primary
        if routed:
            return routed

        # The type classifier is advisory; the causal attributor is the
        # stronger runtime provenance boundary.  A hard empty intersection
        # made a low-confidence type mismatch silently discard an otherwise
        # auditable edit candidate (one of the five-case run's five triggers
        # was lost here).  Fall back to the already bounded attributor route,
        # preserving safety while avoiding classifier-induced dead ends.
        return tuple(dict.fromkeys(str(module) for module in attribution_modules if module))

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

    def _infer_evolution_direction(
        self, state: str, gold: Any
    ) -> str:
        """Infer the evolution direction from state and gold.

        Semantics:
        - Gold-supervised (gold is bool): the gold label tells us the
          *correct* target state. If current state disagrees with gold,
          evolution should move toward the gold-implied state.
        - Self-supervised (gold is None): only ``unresolved`` cases need
          evolution (move toward a decisive judgment). Already-decided
          states (verified_error / verified_clean) should NOT be
          auto-flipped — that was the original bug which guided every
          correct detection toward the opposite label.
        """
        if isinstance(gold, bool):
            if gold:
                # Gold says "error". Current state should move to error.
                if state == "unresolved":
                    return "unresolved_to_error"
                if state == "verified_clean":
                    return "clean_to_error"
                # verified_error already correct → no evolution needed
                return "no_evolution"
            else:
                # Gold says "clean". Current state should move to clean.
                if state == "unresolved":
                    return "unresolved_to_clean"
                if state == "verified_error":
                    return "error_to_clean"
                # verified_clean already correct → no evolution needed
                return "no_evolution"

        # Self-supervised mode has no authority to prescribe verdict polarity.
        if state == "unresolved":
            return "unresolved_to_decisive"
        return "process_repair"
