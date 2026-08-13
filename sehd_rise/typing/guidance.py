"""Type-conditioned advisory guidance derived from runtime capabilities.

Type remains a retrieval/applicability prior only.  It may rank modules that
the causal owner already admitted, but it cannot invent a repair semantic or
claim a constructive operation that the runtime lacks.
"""

from __future__ import annotations

from dataclasses import dataclass

from sehd_rise.skills.capabilities import SKILL_CAPABILITY_REGISTRY
from sehd_rise.typing.classifier import HallucinationType


_TYPE_MODULE_PRIOR: dict[HallucinationType, tuple[str, ...]] = {
    HallucinationType.FACTUAL: (
        "evidence_obligation", "atomic_verifier_instruction", "candidate_trigger",
    ),
    HallucinationType.INSTRUCTIONAL: ("evidence_obligation",),
    HallucinationType.TOOL: (
        "candidate_to_verifier_route", "atomic_verifier_instruction", "candidate_trigger",
    ),
    HallucinationType.REASONING: (
        "causal_link_test", "earliest_origin_tiebreak", "evidence_obligation",
    ),
    HallucinationType.OMISSION: (
        "candidate_trigger", "candidate_to_verifier_route", "evidence_obligation",
    ),
    HallucinationType.ADOPTION: ("evidence_obligation", "candidate_trigger"),
    HallucinationType.CONTEXTUAL: ("causal_link_test",),
    HallucinationType.UNKNOWN: ("evidence_obligation", "candidate_trigger"),
}

_TYPE_APPLICABILITY_PRIOR: dict[HallucinationType, str] = {
    HallucinationType.FACTUAL: "evidence-backed claim shape",
    HallucinationType.INSTRUCTIONAL: "explicit instruction-bound behavior",
    HallucinationType.TOOL: "tool request/result lifecycle",
    HallucinationType.REASONING: "origin-to-impact causal shape",
    HallucinationType.OMISSION: "required-action omission shape",
    HallucinationType.ADOPTION: "adopted external information shape",
    HallucinationType.CONTEXTUAL: "conversation-context dependency",
    HallucinationType.UNKNOWN: "strongest observable court-result discriminator",
}


@dataclass
class TypeGuidance:
    """Advisory type prior; the capability registry remains semantic owner."""

    def get_target_modules(
        self,
        hallucination_type: HallucinationType,
        primary: bool = True,
    ) -> tuple[str, ...]:
        modules = tuple(
            module
            for module in _TYPE_MODULE_PRIOR.get(
                hallucination_type, _TYPE_MODULE_PRIOR[HallucinationType.UNKNOWN]
            )
            if SKILL_CAPABILITY_REGISTRY.for_module(module)
        )
        return modules[:1] if primary else modules[1:]

    def get_edit_strategy(self, hallucination_type: HallucinationType) -> str:
        return "induce_applicability_for_registered_capability"

    def generate_type_guidance(
        self,
        hallucination_type: HallucinationType,
        evolution_direction: str,
    ) -> str:
        modules = self.get_target_modules(hallucination_type, primary=True)
        operations = SKILL_CAPABILITY_REGISTRY.operations_for(
            skill_layer="l1_provider_rule",
            modules=modules,
            direction=evolution_direction,
        )
        capability_text = ", ".join(operations) or "CAPABILITY_GAP"
        return (
            f"[HTC-TES v3] Hallucination type: {hallucination_type.value}\n"
            "Authority: applicability prior only; CapabilityRegistry defines semantics\n"
            f"Applicability prior: {_TYPE_APPLICABILITY_PRIOR.get(hallucination_type, _TYPE_APPLICABILITY_PRIOR[HallucinationType.UNKNOWN])}\n"
            f"Registry-compatible operations: {capability_text}"
        )


__all__ = ["TypeGuidance"]
