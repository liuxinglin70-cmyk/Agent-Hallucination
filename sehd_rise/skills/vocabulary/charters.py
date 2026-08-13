"""Registry-derived module charters for the L1 post-court transform layer.

Charters describe only deterministic runtime capabilities. They are views
over :mod:`sehd_rise.skills.capabilities`, so reflection, validation, and the
runtime share the same operation, input/output, and negative-capability facts.
"""

from __future__ import annotations

from dataclasses import dataclass

from sehd_rise.skills.capabilities import SKILL_CAPABILITY_REGISTRY
from sehd_rise.skills.vocabulary.contracts import (
    ALLOWED_VERDICTS,
    MODULE_IDS,
    MODULE_STAGE,
)


@dataclass(frozen=True)
class ModuleCharter:
    module_id: str
    stage: str
    influenceable_surfaces: tuple[str, ...]
    forbidden_surfaces: tuple[str, ...]
    allowed_verdicts: frozenset[str]
    in_scope_examples: tuple[str, ...]
    out_of_scope_examples: tuple[str, ...]


def _capabilities(module_id: str):
    return SKILL_CAPABILITY_REGISTRY.for_module(module_id)


def _ordered_unique(values):
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


def _build_charter(module_id: str) -> ModuleCharter:
    capabilities = _capabilities(module_id)
    influenceable = _ordered_unique(
        value
        for capability in capabilities
        for value in capability.writes
    )
    forbidden = _ordered_unique(
        value
        for capability in capabilities
        for value in capability.does_not_support
    )
    in_scope = tuple(
        f"Apply {capability.executable_operation} when its existing-carrier "
        f"bindings are present and its result-side condition matches."
        for capability in capabilities
    )
    out_of_scope = tuple(
        f"Cannot {fact}; no registered runtime capability performs it."
        for fact in forbidden
    )
    return ModuleCharter(
        module_id=module_id,
        stage=MODULE_STAGE[module_id],
        influenceable_surfaces=influenceable,
        forbidden_surfaces=forbidden,
        allowed_verdicts=ALLOWED_VERDICTS.get(module_id, frozenset()),
        in_scope_examples=in_scope,
        out_of_scope_examples=out_of_scope,
    )


MODULE_CHARTERS: dict[str, ModuleCharter] = {
    module_id: _build_charter(module_id) for module_id in MODULE_IDS
}


# Compatibility views, not independent semantic authorities.  All values are
# projected from SkillCapabilityRegistry and are tested for semantic closure.
MODULE_ACTION_OPCODES: dict[str, tuple[tuple[str, str], ...]] = {
    module_id: tuple(
        (
            capability.executable_operation,
            capability.executable_operation.replace("_", " "),
        )
        for capability in _capabilities(module_id)
    )
    for module_id in MODULE_IDS
}

MODULE_OPERATION_TARGETS: dict[str, dict[str, tuple[str, ...]]] = {
    module_id: {
        capability.executable_operation: capability.operation_targets
        for capability in _capabilities(module_id)
    }
    for module_id in MODULE_IDS
}

MODULE_OBSERVABLE_FIELDS: dict[str, tuple[str, ...]] = {
    module_id: _ordered_unique(
        capability.expected_local_effect[0]
        for capability in _capabilities(module_id)
    )
    for module_id in MODULE_IDS
}


_FORBIDDEN_ACTION_TERMS = (
    "create candidate",
    "construct candidate",
    "emit candidate",
    "create certificate",
    "construct certificate",
    "emit certificate",
    "reverify",
    "provider prompt",
    "provider reasoning",
    "final verdict",
    "return clean",
    "return error",
    "return unresolved",
)
MODULE_FORBIDDEN_ACTION_TERMS: dict[str, tuple[str, ...]] = {
    module_id: _FORBIDDEN_ACTION_TERMS for module_id in MODULE_IDS
}


def validate_action_surface(module_id: str, action: str) -> tuple[bool, str]:
    """Validate one ``operation: target`` against the capability registry."""
    lowered = " ".join(str(action or "").lower().split())
    for marker in MODULE_FORBIDDEN_ACTION_TERMS.get(module_id, ()):
        if marker in lowered:
            return False, f"forbidden_action_surface:{marker}"
    operation, separator, raw_target = lowered.partition(":")
    capability = SKILL_CAPABILITY_REGISTRY.get(operation.strip())
    if capability is None or capability.module != module_id:
        return False, f"action_opcode_unowned:{operation.strip() or 'missing'}"
    target = raw_target.strip()
    if capability.operation_targets and not separator:
        return False, f"action_target_required:{operation.strip()}"
    if separator and not target:
        return False, "action_target_unowned:empty"
    if target and target not in capability.operation_targets:
        return False, (
            "action_target_not_owned_by_operation:"
            f"{operation.strip()}:{target}"
        )
    return True, ""


def charter_for(module_id: str) -> ModuleCharter:
    charter = MODULE_CHARTERS.get(module_id)
    if charter is None:
        raise ValueError(f"unknown module_id: {module_id}")
    return charter


def render_charter_text(module_id: str) -> str:
    charter = charter_for(module_id)
    verdicts = ", ".join(sorted(charter.allowed_verdicts)) or "none"
    lines = [
        f"MODULE: {charter.module_id}",
        f"RUNTIME: deterministic post-court transform at {charter.stage}",
        "WRITES:",
        *(f"  - {surface}" for surface in charter.influenceable_surfaces),
        "DOES NOT SUPPORT:",
        *(f"  - {surface}" for surface in charter.forbidden_surfaces),
        f"ALLOWED DIRECT VERDICTS: {verdicts}",
        "EXECUTABLE USE:",
        *(f"  - {example}" for example in charter.in_scope_examples),
    ]
    return "\n".join(lines)


def render_all_charters() -> str:
    return "\n\n".join(render_charter_text(module_id) for module_id in MODULE_IDS)


__all__ = [
    "MODULE_ACTION_OPCODES",
    "MODULE_CHARTERS",
    "MODULE_FORBIDDEN_ACTION_TERMS",
    "MODULE_OBSERVABLE_FIELDS",
    "MODULE_OPERATION_TARGETS",
    "ModuleCharter",
    "charter_for",
    "render_all_charters",
    "render_charter_text",
    "validate_action_surface",
]
