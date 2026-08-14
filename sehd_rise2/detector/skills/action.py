"""Compile registry capabilities into executable L1 ActionSpecs.

Natural-language conditions remain the generalization surface.  Operation,
target, executor semantics, postcondition, and expected effect all come from
``SkillCapabilityRegistry``; this module owns only the persisted ActionSpec
shape and its compiler/view helpers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from sehd_rise2.detector.skills.capabilities import SKILL_CAPABILITY_REGISTRY
from sehd_rise2.detector.skills.vocabulary.charters import validate_action_surface


MODULE_ROUTE_IDS: Mapping[str, tuple[str, ...]] = {
    module: capability.route_ids
    for capability in SKILL_CAPABILITY_REGISTRY.for_layer("l1_provider_rule")
    for module in (capability.module,)
}

MODULE_OUTPUT_OBJECT: Mapping[str, str] = {
    module: capability.output_object
    for capability in SKILL_CAPABILITY_REGISTRY.for_layer("l1_provider_rule")
    for module in (capability.module,)
}

# Operations whose runtime effect can move an over-conviction toward clean.
# Generation guidance and admission validation consume this same table so the
# model cannot be told that an operation is legal while the gate rejects it.
ERROR_TO_CLEAN_ACTIONS: Mapping[str, frozenset[str]] = {
    module: frozenset(
        capability.executable_operation
        for capability in SKILL_CAPABILITY_REGISTRY.for_module(module)
        if "error_to_clean" in capability.supported_directions
    )
    for module in MODULE_ROUTE_IDS
}

CLEAN_TO_ERROR_ACTIONS: Mapping[str, frozenset[str]] = {
    module: frozenset(
        capability.executable_operation
        for capability in SKILL_CAPABILITY_REGISTRY.for_module(module)
        if "clean_to_error" in capability.supported_directions
    )
    for module in MODULE_ROUTE_IDS
    if any(
        "clean_to_error" in capability.supported_directions
        for capability in SKILL_CAPABILITY_REGISTRY.for_module(module)
    )
}


def render_direction_action_contract(
    direction: str, modules: Sequence[str], *, failure_shape: str = "",
) -> str:
    """Render the executable opcode subset for one task direction.

    The opcode set is read from the capability registry with the current
    ``failure_shape`` included in the lookup, so a direction-compatible opcode
    that the registry did not declare for this failure shape never leaks into
    the prompt (e.g. ``construct_candidate`` is clean_to_error-only and
    gold_false_negative_clean-only). An empty ``failure_shape`` keeps the
    direction-only view.
    """
    if direction in {"error_to_clean", "unresolved_to_clean", "clean_to_error", "unresolved_to_error"}:
        lines = []
        for module in modules:
            actions = SKILL_CAPABILITY_REGISTRY.operations_for(
                skill_layer="l1_provider_rule",
                modules=(str(module),),
                failure_shape=failure_shape,
                direction=direction,
            )
            if actions:
                lines.append(f"- {module}: " + ", ".join(sorted(actions)))
        return "\n".join(lines)
    return ""


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
    input_binding_domain: str
    required_binding_fields: tuple[str, ...]
    cardinality: str

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
            input_binding_domain=str(value.get("input_binding_domain") or ""),
            required_binding_fields=tuple(
                str(item) for item in value.get("required_binding_fields") or ()
                if str(item)
            ),
            cardinality=str(value.get("cardinality") or ""),
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
    """Compile one validated registry capability into an ActionSpec."""
    valid, reason = validate_action_surface(module_id, action)
    if not valid:
        raise ValueError(reason)
    operation, separator, raw_target = str(action).lower().partition(":")
    operation = operation.strip()
    output_field = raw_target.strip() if separator else ""
    capability = SKILL_CAPABILITY_REGISTRY.require(operation)
    if capability.module != module_id:
        raise ValueError(f"action_spec_operation_unowned:{module_id}:{operation}")
    observable_field, direction = capability.expected_local_effect
    return ActionSpec(
        module_id=module_id,
        route_ids=capability.route_ids,
        output_object=capability.output_object,
        output_field=output_field,
        operation=operation,
        postcondition=capability.fitness_postcondition,
        observable_field=observable_field,
        expected_direction=direction,
        input_binding_domain=capability.input_binding_domain,
        required_binding_fields=capability.required_binding_fields,
        cardinality=capability.cardinality,
    )


def render_action_spec_contract(module_id: str) -> str:
    """Render the executable intervention choices used by reflection.

    The prompt and the runtime compiler share these operation semantics, so
    reflection selects a registry-backed result transform instead of inventing
    a natural-language effect claim disconnected from runtime writes.
    """
    lines: list[str] = []
    for capability in SKILL_CAPABILITY_REGISTRY.for_module(module_id):
        operation = capability.executable_operation
        targets = capability.operation_targets
        if not targets:
            continue
        spec = compile_action_spec(module_id, f"{operation}: {targets[0]}")
        lines.append(
            f"- {operation}: deterministic post-court transform; "
            f"reads={','.join(capability.reads)}; "
            f"writes={','.join(capability.writes)}; "
            f"output_object={spec.output_object}; "
            f"postcondition={spec.postcondition}; "
            f"binding={spec.input_binding_domain}/{spec.cardinality}; "
            f"required_binding_fields={','.join(spec.required_binding_fields)}; "
            f"observable_effect={spec.observable_field}:{spec.expected_direction}"
        )
        lines.append("  valid targets: " + ", ".join(targets))
        lines.append(
            "  does not support: " + ", ".join(capability.does_not_support)
        )
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
