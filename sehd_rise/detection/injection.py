"""Skill-genome compilation for the deterministic L1 execution layer (plan B).

The L1 Skill layer never injects prose into model/provider prompts. This
module compiles a genome's delta rules into the ordered executable rule
set the deterministic executor consumes: each entry is
``(module_id, rule_id, condition_spec, action_spec, binding_entity_id)`` in
genome order. Only slot-contract rules with a compiled action spec are
executable; an untyped prose rule carries no action and is skipped.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from sehd_rise.skills.action import sanitize_action_spec
from sehd_rise.skills.binding import sanitize_binding_proof
from sehd_rise.skills.genome import (
    MODULE_IDS,
    SkillGenome,
    SkillModule,
    create_baseline_genome,
)
from sehd_rise.skills.rule import (
    evaluate_condition_spec,
    project_result_condition_features,
    sanitize_condition_spec,
    sanitize_route_guidance,
)


def _stable_hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _condition_from_rendered_rule(rule: str) -> str:
    """Condition parser for baseline or pre-slot rules only."""
    text = " ".join(str(rule).split())
    lowered = text.lower()
    if lowered.startswith("when "):
        head, _, _tail = text.partition(",")
        return head[len("when "):].strip()
    return text


def _rule_condition(module: SkillModule, index: int, rule: str) -> str:
    """Return the typed condition; do not reverse-parse evolved prose."""
    meta = module.get_rule_meta(index)
    slots = meta.get("rule_slots") if isinstance(meta, Mapping) else None
    if isinstance(slots, Mapping):
        condition = " ".join(str(slots.get("condition") or "").split())
        if condition:
            return condition
    return _condition_from_rendered_rule(rule)


def _rule_condition_spec(module: SkillModule, index: int) -> dict[str, Any]:
    """Return the framework-executable applicability predicate, if typed."""
    return sanitize_condition_spec(
        module.get_rule_meta(index).get("condition_spec")
    )


def _rule_binding_entity_id(module: SkillModule, index: int) -> str:
    """Return the training-time bound entity id, if a binding proof is stored.

    The entity id is the exact entity the source case edited; the executor
    prefers it on source replay so an over-broad generalization predicate never
    turns the source binding into BINDING_AMBIGUOUS. It never bakes into the
    predicate, so generalization on other records still re-evaluates the
    condition over their own universe.
    """
    proof = sanitize_binding_proof(
        module.get_rule_meta(index).get("binding_proof")
    )
    bindings = proof.get("bindings") or ()
    if bindings and isinstance(bindings[0], Mapping):
        return str(bindings[0].get("entity_id") or "")
    return ""


def _rule_stable_id(
    module_id: str,
    rule: str,
    *,
    condition: str | None = None,
    condition_spec: Mapping[str, Any] | None = None,
) -> str:
    """Stable rule id from module + normalized condition clause.

    A wording-level retry that keeps the same condition but rewrites the
    action must keep the same rule identity, so the id survives text drift
    and the evolution loop can attribute repeated no-effect replays to one
    logical rule. Changing the condition (a real semantic change) yields a
    new id.
    """
    exact_condition = (
        " ".join(str(condition).split())
        if condition is not None else _condition_from_rendered_rule(rule)
    )
    typed = sanitize_condition_spec(condition_spec)
    identity = (
        {
            "module_id": module_id,
            "condition_spec": typed,
            "condition": " ".join(exact_condition.lower().split()),
        }
        if typed else {
            "module_id": module_id,
            "legacy_condition": " ".join(exact_condition.lower().split()),
        }
    )
    return _stable_hash(identity)[:12]


def _delta_rule_ids(
    module: SkillModule,
    baseline_module: SkillModule,
) -> tuple[tuple[str, str], ...]:
    """Return (stable_rule_id, rule_text) for every evolved delta rule.

    The id is derived from module + normalized condition (see
    ``_rule_stable_id``), so it is stable across runs, checkpoints and
    wording-level retries of the same logical rule.
    """
    if not module.active:
        return ()
    unmatched_baseline = list(baseline_module.rules)
    delta: list[tuple[str, str]] = []
    for index, rule in enumerate(module.rules):
        if rule in unmatched_baseline:
            unmatched_baseline.remove(rule)
        else:
            delta.append(
                (
                    _rule_stable_id(
                        module.module_id,
                        rule,
                        condition=_rule_condition(module, index, rule),
                        condition_spec=_rule_condition_spec(module, index),
                    ),
                    rule,
                )
            )
    return tuple(delta)


def _delta_action_specs(
    module: SkillModule,
    baseline_module: SkillModule,
) -> dict[str, dict[str, Any]]:
    """Return canonical ActionSpecs for evolved rules, keyed by stable id."""
    if not module.active:
        return {}
    unmatched_baseline = list(baseline_module.rules)
    specs: dict[str, dict[str, Any]] = {}
    for index, rule in enumerate(module.rules):
        if rule in unmatched_baseline:
            unmatched_baseline.remove(rule)
            continue
        value = sanitize_action_spec(module.get_rule_meta(index).get("action_spec"))
        if value:
            specs[_rule_stable_id(
                module.module_id,
                rule,
                condition=_rule_condition(module, index, rule),
                condition_spec=_rule_condition_spec(module, index),
            )] = value
    return specs


def _delta_condition_specs(
    module: SkillModule,
    baseline_module: SkillModule,
) -> dict[str, dict[str, Any]]:
    """Return typed pre-call applicability predicates by stable rule id."""
    if not module.active:
        return {}
    unmatched_baseline = list(baseline_module.rules)
    specs: dict[str, dict[str, Any]] = {}
    for index, rule in enumerate(module.rules):
        if rule in unmatched_baseline:
            unmatched_baseline.remove(rule)
            continue
        condition_spec = _rule_condition_spec(module, index)
        if condition_spec:
            specs[_rule_stable_id(
                module.module_id,
                rule,
                condition=_rule_condition(module, index, rule),
                condition_spec=condition_spec,
            )] = condition_spec
    return specs


def _delta_binding_ids(
    module: SkillModule,
    baseline_module: SkillModule,
) -> dict[str, str]:
    """Return training-time bound entity ids by stable rule id."""
    if not module.active:
        return {}
    unmatched_baseline = list(baseline_module.rules)
    ids: dict[str, str] = {}
    for index, rule in enumerate(module.rules):
        if rule in unmatched_baseline:
            unmatched_baseline.remove(rule)
            continue
        entity_id = _rule_binding_entity_id(module, index)
        if entity_id:
            ids[_rule_stable_id(
                module.module_id,
                rule,
                condition=_rule_condition(module, index, rule),
                condition_spec=_rule_condition_spec(module, index),
            )] = entity_id
    return ids


def compile_genome_rules(
    genome: SkillGenome,
    *,
    baseline: SkillGenome | None = None,
) -> list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, str]]:
    """Compile genome delta rules into an ordered executable rule set.

    Each entry is ``(module_id, rule_id, condition_spec, action_spec,
    binding_entity_id)`` in genome order (``MODULE_IDS`` order = deterministic
    priority). Only slot-contract rules with a compiled action spec are
    executable; an untyped prose rule carries no action and is skipped. The
    trailing ``binding_entity_id`` is the training-time bound entity (empty when
    the rule predates entity-id binding) the executor prefers on source replay.
    The returned list is the single input to
    ``skills.l1_execution.execute_l1_rules`` / ``execute_constructive_rules``.
    """
    baseline = baseline or create_baseline_genome()
    if set(genome.modules) != set(MODULE_IDS):
        raise ValueError("genome must contain exactly the six registered modules")
    rules: list[
        tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, str]
    ] = []
    for module_id in MODULE_IDS:
        module = genome.modules[module_id]
        baseline_module = baseline.modules[module_id]
        action_specs = _delta_action_specs(module, baseline_module)
        condition_specs = _delta_condition_specs(module, baseline_module)
        binding_ids = _delta_binding_ids(module, baseline_module)
        for rule_id, _rule in _delta_rule_ids(module, baseline_module):
            action_spec = action_specs.get(rule_id)
            if not action_spec:
                continue
            rules.append((
                module_id, rule_id,
                condition_specs.get(rule_id),
                action_spec,
                binding_ids.get(rule_id, ""),
            ))
    return rules


@dataclass(frozen=True)
class RouteGuidanceEntry:
    """One compiled route-guidance skill: its applicability predicate plus
    the firewall-checked guidance text. The predicate gates whether the
    guidance is injected at all, symmetric with a deterministic rule's
    ``condition_spec`` gate."""
    condition_spec: Mapping[str, Any] | None
    text: str


def compile_route_guidance(
    genome: SkillGenome,
    *,
    baseline: SkillGenome | None = None,
) -> dict[str, tuple[RouteGuidanceEntry, ...]]:
    """Compile route-guidance skills into owner-keyed, condition-carrying
    entries.

    A route-guidance skill has no deterministic backend: its intervention is
    the firewall-checked ``route_guidance`` text the reflection model wrote,
    gated by the same ``condition_spec`` a deterministic rule carries. This
    returns one entry per owner (module id); the owner -> provider-route
    mapping lives in the causal RouteAddressability table, applied by the
    execution layer (which may import causal). Text that fails the firewall,
    or a rule with no condition, is dropped here — never surfaced to a
    provider.
    """
    baseline = baseline or create_baseline_genome()
    if set(genome.modules) != set(MODULE_IDS):
        raise ValueError("genome must contain exactly the six registered modules")
    by_owner: dict[str, list[RouteGuidanceEntry]] = {}
    for module_id in MODULE_IDS:
        module = genome.modules[module_id]
        baseline_module = baseline.modules[module_id]
        unmatched_baseline = list(baseline_module.rules)
        for index, rule in enumerate(module.rules):
            if rule in unmatched_baseline:
                unmatched_baseline.remove(rule)
                continue
            meta = module.get_rule_meta(index)
            guidance = sanitize_route_guidance(str(meta.get("route_guidance") or ""))
            if not guidance:
                continue
            condition = sanitize_condition_spec(meta.get("condition_spec"))
            if not condition:
                # A route-guidance skill with no applicability predicate is
                # unconditional — the exact generalization hazard the D1
                # firewall exists to prevent. Drop it: it never fires.
                continue
            by_owner.setdefault(module_id, []).append(
                RouteGuidanceEntry(condition_spec=condition, text=guidance)
            )
    return {
        owner: tuple(entries) for owner, entries in by_owner.items()
    }


def select_route_guidance(
    compiled: Mapping[str, tuple[RouteGuidanceEntry, ...]],
    *,
    result: Mapping[str, Any],
    record: Mapping[str, Any] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Evaluate each guidance entry's condition against a court result.

    Only entries whose ``condition_spec`` matches the result's projected
    features are returned, keyed by owner. This is the route-guidance
    counterpart of ``match_condition_bindings`` for deterministic rules: a
    guidance whose condition does not hold on the case is not injected, so an
    accepted guidance never leaks onto an unrelated case or canary.
    """
    features = project_result_condition_features(result, record)
    selected: dict[str, list[str]] = {}
    for owner, entries in compiled.items():
        for entry in entries:
            if entry.condition_spec is None:
                continue
            evaluation = evaluate_condition_spec(entry.condition_spec, features)
            if evaluation.matched:
                selected.setdefault(owner, []).append(entry.text)
    return {owner: tuple(texts) for owner, texts in selected.items()}


__all__ = [
    "compile_genome_rules",
    "compile_route_guidance",
    "select_route_guidance",
    "RouteGuidanceEntry",
]
