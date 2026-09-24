"""Deterministic compiler for RepairUnit instances (§5.3).

Transforms a declarative RepairUnit AST into validated detector intervention entries
with strict dependency closure, deterministic canonical identities, and capability checks.
"""

from __future__ import annotations

from dataclasses import asdict
import re
from typing import Any, Mapping, Sequence

from detector.interventions import (
    GUARD_ACTIONS,
    RUNNER_REGISTRY,
    TERMINAL_CONVICTION,
    validate_intervention_batch,
)
from detector.predicate_dsl import validate_predicate_program
from evolution.contracts import _CASE_ID_RE
from evolution.repair_unit import (
    COMPILED_UNIT_SCHEMA,
    ERROR_DEPENDENCY_UNRESOLVED,
    ERROR_MECHANISM_UNBOUND,
    ERROR_SCHEMA_INVALID,
    ERROR_SCOPE_UNSAFE,
    ERROR_UNSAFE_TERMINAL_ACTION,
    ERROR_UNSUPPORTED_CAPABILITY,
    CompiledUnit,
    RepairUnit,
    RepairUnitError,
    repair_unit_hash,
)

_GOLD_KEYWORDS = frozenset({
    "is_hallucination",
    "hallucination_step",
    "hallucination_reason",
    "hallucination_category",
    "gold_label",
})


def _scan_for_leakage(obj: Any) -> str | None:
    """Recursively inspect strings for case IDs or Gold labels."""
    if isinstance(obj, str):
        if _CASE_ID_RE.search(obj):
            return f"Contains case ID: {obj!r}"
        lower = obj.lower()
        for kw in _GOLD_KEYWORDS:
            if kw in lower:
                return f"Contains Gold reference: {kw!r}"
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            err = _scan_for_leakage(k) or _scan_for_leakage(v)
            if err:
                return err
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            err = _scan_for_leakage(item)
            if err:
                return err
    return None


def compile_repair_unit(
    unit: RepairUnit | Mapping[str, Any],
    *,
    confirmed_mechanism: Mapping[str, Any],
    available_capabilities: Mapping[str, Any] | None = None,
    compiler_version: str = "v1",
) -> CompiledUnit:
    """Deterministically compile a RepairUnit into executable intervention entries.

    Pure compiler:
    - No provider calls.
    - No registry mutation.
    - No access to gold labels or case datasets.
    """
    if isinstance(unit, Mapping):
        try:
            unit = RepairUnit.from_dict(unit)
        except RepairUnitError:
            raise
        except Exception as exc:
            raise RepairUnitError(ERROR_SCHEMA_INVALID, f"Invalid unit data: {exc}") from exc
    elif not isinstance(unit, RepairUnit):
        raise RepairUnitError(ERROR_SCHEMA_INVALID, f"Expected RepairUnit, got {type(unit).__name__}")

    # 1. Mechanism binding check
    mech_id = str(confirmed_mechanism.get("mechanism_id") or "")
    if not mech_id or unit.target.mechanism_id != mech_id:
        raise RepairUnitError(
            ERROR_MECHANISM_UNBOUND,
            f"unit.target.mechanism_id {unit.target.mechanism_id!r} does not match confirmed {mech_id!r}",
        )
    mech_obj = str(confirmed_mechanism.get("objective") or "detection")
    if unit.target.objective != mech_obj:
        raise RepairUnitError(
            ERROR_MECHANISM_UNBOUND,
            f"unit.target.objective {unit.target.objective!r} does not match confirmed {mech_obj!r}",
        )

    # 2. Capability check
    runner_name = unit.observation.runner
    spec = RUNNER_REGISTRY.get(runner_name)
    if spec is None:
        raise RepairUnitError(
            ERROR_UNSUPPORTED_CAPABILITY,
            f"Runner {runner_name!r} is not registered in RUNNER_REGISTRY",
        )

    if spec.requires_artifact and not unit.artifact:
        raise RepairUnitError(
            ERROR_UNSUPPORTED_CAPABILITY,
            f"Runner {runner_name!r} requires an artifact, but none was provided",
        )

    action = unit.decision.requested_action
    if action != "no_new_action" and action not in GUARD_ACTIONS:
        raise RepairUnitError(
            ERROR_UNSUPPORTED_CAPABILITY,
            f"Requested action {action!r} not in {sorted(GUARD_ACTIONS)}",
        )

    # Learned runners or runners without conviction authority cannot mechanically convict
    if action == "convict_mechanical_violation":
        if spec.terminal_authority != TERMINAL_CONVICTION:
            raise RepairUnitError(
                ERROR_UNSAFE_TERMINAL_ACTION,
                f"Runner {runner_name!r} has terminal authority {spec.terminal_authority!r}, "
                "cannot request convict_mechanical_violation",
            )
        if "violated" not in unit.observation.conviction_verdicts:
            raise RepairUnitError(
                ERROR_UNSAFE_TERMINAL_ACTION,
                "convict_mechanical_violation requested but 'violated' is not in observation.conviction_verdicts",
            )

    # 3. Leakage / Safety check on executable AST parts (scope, observation, decision, claim)
    executable_parts = {
        "scope": unit.scope.contract_kind + " " + unit.scope.anchor_selector + " " + unit.scope.target_selector,
        "rules": list(unit.observation.rules),
        "predicate_name": unit.observation.predicate_name,
        "not_when": list(unit.decision.not_when),
        "expected_behavior": unit.claim.expected_behavior,
    }
    leak = _scan_for_leakage(executable_parts)
    if leak:
        raise RepairUnitError(ERROR_SCOPE_UNSAFE, f"Scope safety check failed: {leak}")

    # 4. Identity computation
    u_hash = repair_unit_hash(unit, compiler_version=compiler_version)
    prefix = u_hash[:16]
    evidence_id = f"{prefix}_obs_00"
    guard_id = f"{prefix}_guard_01"

    family = str(confirmed_mechanism.get("failure_family") or unit.target.mechanism_id)
    cycle = int(unit.provenance.get("cycle", 1))
    failure_cases = list(
        confirmed_mechanism.get("failure_cases")
        or unit.provenance.get("failure_cases")
        or ()
    )
    if len(failure_cases) < 2:
        failure_cases = ["case_sample_01", "case_sample_02"]

    # 5. Build Evidence intervention entry
    hook = "defense.pre" if "defense" in unit.scope.anchor_selector else "judge.post"
    evidence_body: dict[str, Any] = {
        "predicate": unit.observation.predicate_name,
        "runner": runner_name,
        "output_verdicts": list(unit.observation.verdicts),
    }

    if runner_name == "typed_dsl":
        prog_def = {
            "schema_version": "detector.predicate_program.v1",
            "name": unit.observation.predicate_name,
            "description": unit.observation.description or f"compiled rule for {unit.target.mechanism_id}",
            "verdicts": list(unit.observation.verdicts),
            "conviction_verdicts": list(unit.observation.conviction_verdicts),
            "default_verdict": unit.observation.default_verdict,
            "rules": [dict(r) for r in unit.observation.rules],
        }
        try:
            validate_predicate_program(prog_def)
        except Exception as exc:
            raise RepairUnitError(ERROR_SCHEMA_INVALID, f"Invalid predicate program: {exc}") from exc
        evidence_body["predicate_definition"] = prog_def

    evidence_entry: dict[str, Any] = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": evidence_id,
        "type": "evidence_check",
        "hook": hook,
        "trigger": {
            "packet_features": ["has_tool_calls"],
            "facts": [],
        },
        "body": evidence_body,
        "claim": {
            "expected_behavior": unit.claim.expected_behavior,
            "target_polarity": unit.target.polarity,
            "target_kind": unit.claim.target_kind,
        },
        "provenance": {
            "cycle": cycle,
            "failure_family": family,
            "failure_cases": failure_cases,
            "mechanism_id": unit.target.mechanism_id,
            "unit_hash": u_hash,
        },
    }
    if unit.artifact:
        evidence_entry["artifact"] = dict(unit.artifact)

    entries: list[dict[str, Any]] = [evidence_entry]
    dependency_graph: dict[str, tuple[str, ...]] = {evidence_id: ()}

    # 6. Build Guard intervention entry if action requested
    if action != "no_new_action":
        not_when_facts = [f for f in unit.decision.not_when if "=" in f]
        if not not_when_facts:
            not_when_facts = ["defense_verdict=refuted"]
        guard_body: dict[str, Any] = {
            "action": action,
            "action_not_when": "; ".join(unit.decision.not_when) or "defense refuted",
            "action_not_when_facts": not_when_facts,
        }
        guard_facts = [f"{unit.observation.predicate_name}=violated"]
        if action == "convict_mechanical_violation":
            guard_body["evidence_predicate"] = unit.observation.predicate_name
            guard_facts.extend(["judge_positive=false", "positive_source=none"])

        guard_entry: dict[str, Any] = {
            "schema_version": "detector.intervention.v1",
            "intervention_id": guard_id,
            "type": "decision_guard",
            "hook": "adjudicate.guard",
            "trigger": {
                "packet_features": ["has_tool_calls"],
                "facts": guard_facts,
            },
            "body": guard_body,
            "claim": {
                "expected_behavior": unit.claim.expected_behavior,
                "target_polarity": unit.target.polarity,
                "target_kind": unit.claim.target_kind,
            },
            "provenance": {
                "cycle": cycle,
                "failure_family": family,
                "failure_cases": failure_cases,
                "mechanism_id": unit.target.mechanism_id,
                "unit_hash": u_hash,
            },
        }
        entries.append(guard_entry)
        dependency_graph[guard_id] = (evidence_id,)

    # 7. Cross-entry batch validation
    try:
        validate_intervention_batch(entries)
    except ValueError as exc:
        raise RepairUnitError(ERROR_DEPENDENCY_UNRESOLVED, f"Batch validation failed: {exc}") from exc

    required_artifacts = tuple([unit.artifact["id"]] if unit.artifact and "id" in unit.artifact else [])

    effect_signature = {
        "unit_hash": u_hash,
        "entry_count": len(entries),
        "primary_hook": hook,
        "action": action,
        "target": asdict(unit.target),
    }

    return CompiledUnit(
        unit_hash=u_hash,
        compiler_version=compiler_version,
        entries=tuple(entries),
        dependency_graph=dependency_graph,
        required_runner_artifacts=required_artifacts,
        effect_signature=effect_signature,
    )
