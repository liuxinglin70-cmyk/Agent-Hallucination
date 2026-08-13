"""Type-conditioned advisory guidance (C5: demoted from routing authority).

Plan §2.6 demotes HTC-TES to advisory: type information serves retrieval
priors, reflection priors and direction suggestions only. This module emits
those suggestions; it does NOT filter the causal owner's module set and does
NOT arbitrate the evolution direction. The causal layer (``cut.owner`` /
``episode.failure_owner``) and the loop layer own routing and direction
respectively; a second typing-layer routing truth-source is abolished.

What is preserved verbatim from the earlier module:
- the type -> target-module table (retrieval prior: which modules a type
  historically implicates),
- the type -> editing-strategy table (reflection prior),
- the direction x type -> guidance text table (direction suggestion),
and the three pure accessors that surface them.

What was deleted (C5, plan §2.6):
- module route filtering (intersecting / reordering the attributor's module
  set by type) -- a second module-routing truth-source competing with the
  causal owner;
- evolution-direction conflict arbitration (prescribing the direction from
  state + gold) -- a second direction truth-source competing with the loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sehd_rise.typing.classifier import HallucinationType


# Type to module routing table: which modules are primary targets per type.
# Advisory retrieval prior only; it never filters the causal owner's modules.
_TYPE_MODULE_ROUTING: dict[HallucinationType, tuple[str, ...]] = {
    HallucinationType.FACTUAL: (
        "evidence_obligation",
        "atomic_verifier_instruction",
        "candidate_trigger",
    ),
    HallucinationType.INSTRUCTIONAL: (
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
        "candidate_trigger",
    ),
    HallucinationType.CONTEXTUAL: (
        "causal_link_test",
    ),
    HallucinationType.UNKNOWN: (
        "evidence_obligation",
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
    "unresolved_to_error": {
        HallucinationType.FACTUAL: "When the unresolved case shows an unsupported claim, complete the evidence chain toward error",
        HallucinationType.INSTRUCTIONAL: "When the unresolved case shows an instruction breach, complete the evidence chain toward error",
        HallucinationType.TOOL: "When the unresolved case shows a tool misuse, complete the evidence chain toward error",
        HallucinationType.REASONING: "When the unresolved case shows a broken inference, complete the causal chain toward error",
        HallucinationType.OMISSION: "When the unresolved case shows a consequential omission, complete the evidence toward error",
        HallucinationType.ADOPTION: "When the unresolved case shows unverified adoption, complete the evidence toward error",
        HallucinationType.CONTEXTUAL: "When the unresolved case shows a context breach, complete the evidence toward error",
        HallucinationType.UNKNOWN: "When the unresolved case carries a clear failure pattern, complete the evidence toward error",
    },
    "unresolved_to_clean": {
        HallucinationType.FACTUAL: "When the unresolved claim is actually supported, complete the defense toward clean",
        HallucinationType.INSTRUCTIONAL: "When the unresolved behavior actually followed instructions, complete the defense toward clean",
        HallucinationType.TOOL: "When the unresolved tool use was actually valid, complete the defense toward clean",
        HallucinationType.REASONING: "When the unresolved reasoning is actually sound, complete the defense toward clean",
        HallucinationType.OMISSION: "When the unresolved step was actually not required, complete the defense toward clean",
        HallucinationType.ADOPTION: "When the unresolved adoption was actually verified, complete the defense toward clean",
        HallucinationType.CONTEXTUAL: "When the unresolved response is actually in context, complete the defense toward clean",
        HallucinationType.UNKNOWN: "When the unresolved case actually shows no defect, complete the defense toward clean",
    },
    "no_evolution": {
        htype: "Detection is already aligned; only tighten process evidence if it is demonstrably weak."
        for htype in HallucinationType
    },
}


@dataclass
class TypeGuidance:
    """Advisory type-conditioned priors and direction suggestions (C5).

    Surfaces the type -> module retrieval prior, the type -> strategy
    reflection prior, and the direction x type -> guidance suggestion. It
    produces suggestions only; it never trims ``episode.failure_owner`` or
    ``cut.owner`` and never prescribes the evolution direction.
    """

    def get_target_modules(
        self,
        hallucination_type: HallucinationType,
        primary: bool = True,
    ) -> tuple[str, ...]:
        """Get the module retrieval prior for a given hallucination type.

        Advisory: the caller (causal/loop owner) decides which modules are
        actually in scope; this never filters that set.
        """
        modules = _TYPE_MODULE_ROUTING.get(
            hallucination_type, _TYPE_MODULE_ROUTING[HallucinationType.UNKNOWN]
        )
        if primary:
            return modules[:1]
        return modules[1:]

    def get_edit_strategy(self, hallucination_type: HallucinationType) -> str:
        """Get the advisory editing strategy for a given hallucination type."""
        return _TYPE_EDIT_STRATEGY.get(
            hallucination_type, "generic_improvement"
        )

    def generate_type_guidance(
        self,
        hallucination_type: HallucinationType,
        evolution_direction: str,
    ) -> str:
        """Generate the advisory type-specific guidance for the reflection
        engine. The direction itself is owned by the loop layer; this only
        surfaces the suggestion text keyed by it.
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


__all__ = ["TypeGuidance"]
