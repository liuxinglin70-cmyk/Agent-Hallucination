"""Executable L1 intervention specifications: the compiled action slot.

Natural-language conditions remain the generalization surface, while every
action is compiled from the module/action vocabulary into an explicit provider
route, schema object, output field and postcondition. Reflection therefore
does not invent execution semantics or a separate expected-effect story.

This module owns the data model and the deterministic compiler: the ActionSpec
persisted inside rule metadata, the opcode -> postcondition/effect tables, and
the render helpers shared by the prompt and the validator. Evaluating a
postcondition against a detection result is execution evidence and lives in
the execution layer, never here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from sehd_rise.skills.vocabulary.charters import (
    MODULE_ACTION_OPCODES,
    MODULE_OPERATION_TARGETS,
    validate_action_surface,
)


MODULE_ROUTE_IDS: Mapping[str, tuple[str, ...]] = {
    "candidate_trigger": (
        "candidate_fallback_route", "construct_census_route",
    ),
    "evidence_obligation": (
        "propagation_route", "focused_prosecution_route",
        "appeal_prosecutor_route",
    ),
    "candidate_to_verifier_route": ("qualification_route",),
    "atomic_verifier_instruction": (
        "qualification_route", "focused_prosecution_route",
        "appeal_prosecutor_route",
    ),
    "causal_link_test": (
        "propagation_route", "focused_prosecution_route",
        "appeal_prosecutor_route", "boundary_route", "counterfactual_route",
    ),
    "earliest_origin_tiebreak": ("boundary_route", "counterfactual_route"),
}

MODULE_OUTPUT_OBJECT: Mapping[str, str] = {
    "candidate_trigger": "candidate",
    "evidence_obligation": "error_certificate",
    "candidate_to_verifier_route": "candidate_qualification",
    "atomic_verifier_instruction": "verification_certificate",
    "causal_link_test": "causal_certificate_or_boundary",
    "earliest_origin_tiebreak": "boundary_finding",
}

_OPERATION_POSTCONDITION: Mapping[str, str] = {
    "suppress_candidate": "object_absent",
    "keep_coverage_incomplete": "coverage_not_complete",
    "hold_candidate_qualification": "qualification_not_positive",
    # ``withhold_certificate`` semantics are fingerprint-targeted (the rule
    # removes ONE certificate matching its condition), not whole-surface (all
    # surviving certificates gone): whenever multiple certificates carry the
    # verdict and the rule withholds one, a whole-surface absence check is
    # unreachable and the verifier's compliant action would be scored as not
    # executed. ``targeted_certificate_withheld`` evaluates the rule's actual
    # effect against any prior surviving id set the postcondition caller can
    # supply, and otherwise falls back to the strict whole-surface check for
    # single-carrier cases.
    "withhold_certificate": "targeted_certificate_withheld",
    "reject_non_causal_origin": "origin_not_selected",
    "select_earliest_origin": "responsible_origin_present",
    "suppress_later_origin": "later_origin_not_selected",
}

_MODULE_EFFECT: Mapping[str, tuple[str, str]] = {
    "earliest_origin_tiebreak": ("responsible_step", "shift"),
}

_OPERATION_EFFECT: Mapping[str, tuple[str, str]] = {
    "suppress_candidate": ("candidate_count", "decrease"),
    "keep_coverage_incomplete": ("prosecution_coverage", "opened"),
    "hold_candidate_qualification": ("prosecution_certificates", "decrease"),
    # Claim-level carrier movement, not the whole-surface count: a withhold
    # removes ONE surviving claim, and positional certificate ids are
    # re-minted on every provider re-sampling, so only the content
    # fingerprint surviving set measures the rule's actual effect.
    "withhold_certificate": ("surviving_certificates", "decrease"),
    "reject_non_causal_origin": ("responsible_step", "removed"),
    "suppress_later_origin": ("responsible_step", "shift"),
}

# Operations whose runtime effect can move an over-conviction toward clean.
# Generation guidance and admission validation consume this same table so the
# model cannot be told that an operation is legal while the gate rejects it.
ERROR_TO_CLEAN_ACTIONS: Mapping[str, frozenset[str]] = {
    "evidence_obligation": frozenset({"keep_coverage_incomplete"}),
    "atomic_verifier_instruction": frozenset({"withhold_certificate"}),
    "causal_link_test": frozenset({"reject_non_causal_origin"}),
    "candidate_trigger": frozenset({"suppress_candidate"}),
    "candidate_to_verifier_route": frozenset({"hold_candidate_qualification"}),
}

CLEAN_TO_ERROR_ACTIONS: Mapping[str, frozenset[str]] = {
    "earliest_origin_tiebreak": frozenset({"select_earliest_origin"}),
}


def render_direction_action_contract(
    direction: str, modules: Sequence[str],
) -> str:
    """Render the executable opcode subset for one task direction."""
    if direction in {"error_to_clean", "unresolved_to_clean"}:
        contract = ERROR_TO_CLEAN_ACTIONS
    elif direction in {"clean_to_error", "unresolved_to_error"}:
        contract = CLEAN_TO_ERROR_ACTIONS
    else:
        return ""
    lines = []
    for module in modules:
        actions = sorted(contract.get(str(module), frozenset()))
        if actions:
            lines.append(f"- {module}: " + ", ".join(actions))
    return "\n".join(lines)


@dataclass(frozen=True)
class ActionSpec:
    module_id: str
    route_ids: tuple[str, ...]
    output_object: str
    output_field: str
    operation: str
    postcondition: str
    observable_field: str
    expected_direction: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["route_ids"] = list(self.route_ids)
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionSpec":
        return cls(
            module_id=str(value.get("module_id") or ""),
            route_ids=tuple(str(item) for item in value.get("route_ids") or ()),
            output_object=str(value.get("output_object") or ""),
            output_field=str(value.get("output_field") or ""),
            operation=str(value.get("operation") or ""),
            postcondition=str(value.get("postcondition") or ""),
            observable_field=str(value.get("observable_field") or ""),
            expected_direction=str(value.get("expected_direction") or ""),
        )

    def effect_contract(self, condition: str) -> dict[str, str]:
        return {
            "observable_field": self.observable_field,
            "expected_direction": self.expected_direction,
            "expected_scope": "all_cases",
            "trigger_condition": " ".join(str(condition).split())[:240],
        }

    def expected_effect_text(self) -> str:
        return (
            f"{self.observable_field} must {self.expected_direction} when "
            f"{self.postcondition} is enforced"
        )

    def removal_effect_contract(self, condition: str) -> dict[str, str]:
        inverse = {
            "increase": "decrease",
            "decrease": "increase",
            "opened": "closed",
            "closed": "opened",
            "removed": "added",
            "added": "removed",
            "shift": "shift",
        }
        direction = inverse.get(self.expected_direction, "shift")
        contract = self.effect_contract(condition)
        contract["expected_direction"] = direction
        return contract

    def removal_effect_text(self) -> str:
        direction = self.removal_effect_contract("removed rule condition")[
            "expected_direction"
        ]
        return (
            f"{self.observable_field} must {direction} when the intervention "
            "is removed"
        )


def compile_action_spec(module_id: str, action: str) -> ActionSpec:
    """Compile one validated opcode slot into its executable specification."""
    valid, reason = validate_action_surface(module_id, action)
    if not valid:
        raise ValueError(reason)
    operation, separator, raw_target = str(action).lower().partition(":")
    operation = operation.strip()
    output_field = raw_target.strip() if separator else ""
    postcondition = _OPERATION_POSTCONDITION.get(operation)
    if not postcondition:
        raise ValueError(f"action_spec_operation_unmapped:{operation}")
    # Effect resolution is most-specific first and must be lazy: a module
    # with no module-default effect (its opcodes all resolve at operation
    # level) would otherwise KeyError here even though its opcode maps
    # cleanly — ``dict.get`` evaluates its default argument eagerly. An
    # opcode that resolves nowhere is an instrumentation defect, reported
    # fail-closed, never guessed.
    effect = _OPERATION_EFFECT.get(operation) or _MODULE_EFFECT.get(module_id)
    if effect is None:
        raise ValueError(f"action_spec_effect_unmapped:{module_id}:{operation}")
    observable_field, direction = effect
    return ActionSpec(
        module_id=module_id,
        route_ids=MODULE_ROUTE_IDS[module_id],
        output_object=MODULE_OUTPUT_OBJECT[module_id],
        output_field=output_field,
        operation=operation,
        postcondition=postcondition,
        observable_field=observable_field,
        expected_direction=direction,
    )


def render_action_spec_contract(module_id: str) -> str:
    """Render the executable intervention choices used by reflection.

    The prompt and the runtime compiler share these operation semantics, so
    reflection selects an output intervention instead of inventing a natural-
    language effect claim disconnected from provider output.
    """
    lines: list[str] = []
    for operation, template in MODULE_ACTION_OPCODES.get(module_id, ()):
        targets = MODULE_OPERATION_TARGETS.get(module_id, {}).get(operation, ())
        if not targets:
            continue
        spec = compile_action_spec(module_id, f"{operation}: {targets[0]}")
        lines.append(
            f"- {operation}: {template}; output_object={spec.output_object}; "
            f"postcondition={spec.postcondition}; "
            f"observable_effect={spec.observable_field}:{spec.expected_direction}"
        )
        lines.append("  valid targets: " + ", ".join(targets))
    return "\n".join(lines) if lines else "(no executable actions)"


def sanitize_action_spec(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    spec = ActionSpec.from_dict(value)
    try:
        canonical = compile_action_spec(
            spec.module_id,
            f"{spec.operation}: {spec.output_field}"
            if spec.output_field else spec.operation,
        )
    except (KeyError, ValueError):
        return {}
    return canonical.to_dict() if spec == canonical else {}
