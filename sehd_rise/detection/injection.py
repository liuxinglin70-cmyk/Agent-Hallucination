"""Skill-genome integration adapter for the proof-court detector.

The adapter maps the eight typed genome modules onto the detector's three
runtime stages without changing any proof-court decision rule.  Typed clauses
are appended at provider boundaries as auditable constraints.
"""

from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol

from sehd_rise.skills.action import MODULE_ROUTE_IDS, sanitize_action_spec
from sehd_rise.skills.genome import (
    MODULE_IDS,
    MODULE_DISPLAY_NAMES,
    SkillGenome,
    SkillModule,
    create_baseline_genome,
)
from sehd_rise.skills.rule import (
    evaluate_condition_spec,
    project_route_condition_features,
    sanitize_condition_spec,
)


_PATCH_LOCK = threading.RLock()


class PacaStage(str, Enum):
    """The three stable detector runtime stages."""

    STAGE_1_CENSUS = "stage_1_census"
    STAGE_2_VERIFICATION = "stage_2_verification"
    STAGE_3_CAUSAL_COURT = "stage_3_causal_court"


PACA_MODULE_STAGE: Mapping[str, PacaStage] = {
    "candidate_trigger": PacaStage.STAGE_1_CENSUS,
    "evidence_obligation": PacaStage.STAGE_2_VERIFICATION,
    "candidate_to_verifier_route": PacaStage.STAGE_2_VERIFICATION,
    "atomic_verifier_instruction": PacaStage.STAGE_2_VERIFICATION,
    "clean_defense_boundary": PacaStage.STAGE_2_VERIFICATION,
    "causal_link_test": PacaStage.STAGE_3_CAUSAL_COURT,
    "earliest_origin_tiebreak": PacaStage.STAGE_3_CAUSAL_COURT,
    "abstention_rule": PacaStage.STAGE_3_CAUSAL_COURT,
}

ROUTE_STAGE_KEYS: Mapping[PacaStage, tuple[str, ...]] = {
    PacaStage.STAGE_1_CENSUS: ("candidate_fallback_route", "construct_census_route"),
    PacaStage.STAGE_2_VERIFICATION: (
        "qualification_route", "propagation_route", "focused_prosecution_route",
        "defense_route", "appeal_prosecutor_route", "appeal_defender_route",
    ),
    PacaStage.STAGE_3_CAUSAL_COURT: ("boundary_route", "counterfactual_route"),
}

# Route-level reachability prevents a rule from being injected into a provider
# whose output schema cannot execute that rule.
ROUTE_MODULE_IDS: Mapping[str, tuple[str, ...]] = {
    route_id: tuple(
        module_id
        for module_id, route_ids in MODULE_ROUTE_IDS.items()
        if route_id in route_ids
    )
    for route_id in ROUTE_STAGE_KEYS[PacaStage.STAGE_1_CENSUS]
    + ROUTE_STAGE_KEYS[PacaStage.STAGE_2_VERIFICATION]
    + ROUTE_STAGE_KEYS[PacaStage.STAGE_3_CAUSAL_COURT]
}


class ProviderCall(Protocol):
    """Detector provider-call signature."""

    def __call__(
        self,
        *,
        route: dict[str, Any],
        system: str,
        user: str,
        cache_dir: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        ...


@dataclass(frozen=True)
class CompiledStageSkill:
    """Deterministically compiled active modules for one detector stage."""

    stage: PacaStage
    module_ids: tuple[str, ...]
    changed_module_ids: tuple[str, ...]
    text: str
    content_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "module_ids": list(self.module_ids),
            "changed_module_ids": list(self.changed_module_ids),
            "text": self.text,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class CompiledPacaSkill:
    """Complete, content-addressed detector integration artifact."""

    genome_hash: str
    stages: Mapping[PacaStage, CompiledStageSkill]
    module_texts: Mapping[str, str]
    delta_rule_ids: Mapping[str, tuple[tuple[str, str], ...]]
    action_specs: Mapping[str, Mapping[str, Any]]
    rule_conditions: Mapping[str, str]
    condition_specs: Mapping[str, Mapping[str, Any]]
    frozen_crs_weights: tuple[float, ...] = (0.35, 0.15, 0.20, 0.20, 0.10)
    schema_version: str = "agenthallu.paca_tcl_skill_integration.v6"

    def rule_trace(self) -> dict[str, list[dict[str, str]]]:
        """Return the stable rule-level execution trace for audit.

        Every evolved (delta) rule gets one stable rule id (content hash
        prefix) with its module, so the evolution loop can attribute an
        observable no-effect to the injected rule surface instead of
        guessing from the whole genome.
        """
        return {
            module_id: [
                {"rule_id": rule_id, "rule": rule}
                for rule_id, rule in self.delta_rule_ids.get(module_id, ())
            ]
            for module_id in sorted(self.delta_rule_ids)
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "genome_hash": self.genome_hash,
            "frozen_crs_weights": list(self.frozen_crs_weights),
            "module_texts": dict(self.module_texts),
            "delta_rule_ids": {
                module_id: [
                    {"rule_id": rule_id, "rule": rule}
                    for rule_id, rule in rule_ids
                ]
                for module_id, rule_ids in self.delta_rule_ids.items()
            },
            "action_specs": {
                rule_id: dict(spec)
                for rule_id, spec in self.action_specs.items()
            },
            "rule_conditions": dict(self.rule_conditions),
            "condition_specs": {
                rule_id: dict(spec)
                for rule_id, spec in self.condition_specs.items()
            },
            "stages": {
                stage.value: compiled.to_dict()
                for stage, compiled in self.stages.items()
            },
        }


def _stable_hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Module display names for natural language injection.
MODULE_DISPLAY_NAMES_LOCAL: dict[str, str] = MODULE_DISPLAY_NAMES


def _compile_module_delta(
    module: SkillModule,
    baseline_module: SkillModule,
) -> list[str]:
    """Compile only rules that are not already part of the frozen baseline.

    Each rule carries its stable id inline (``[<id>]``) so the receiving
    provider can self-report which evolved rules it actually applied via the
    ``applied_rule_ids`` output field, closing the injection-to-execution
    trace.
    """
    if not module.active:
        return []
    unmatched_baseline = list(baseline_module.rules)
    delta_rules: list[tuple[int, str]] = []
    for index, rule in enumerate(module.rules):
        if rule in unmatched_baseline:
            unmatched_baseline.remove(rule)
        else:
            delta_rules.append((index, rule))
    if not delta_rules:
        return []
    display_name = MODULE_DISPLAY_NAMES_LOCAL.get(
        module.module_id,
        module.module_id,
    )
    return [
        f"【{display_name}】["
        f"{_rule_stable_id(module.module_id, rule, condition=_rule_condition(module, index, rule), condition_spec=_rule_condition_spec(module, index))}] "
        f"{rule}"
        for index, rule in delta_rules
    ]


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


def _delta_rule_conditions(
    module: SkillModule,
    baseline_module: SkillModule,
) -> dict[str, str]:
    """Return authoritative conditions for evolved rules by stable id."""
    if not module.active:
        return {}
    unmatched_baseline = list(baseline_module.rules)
    conditions: dict[str, str] = {}
    for index, rule in enumerate(module.rules):
        if rule in unmatched_baseline:
            unmatched_baseline.remove(rule)
            continue
        condition = _rule_condition(module, index, rule)
        conditions[_rule_stable_id(
            module.module_id, rule, condition=condition,
            condition_spec=_rule_condition_spec(module, index),
        )] = condition
    return conditions


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


def compile_genome_for_paca(
    genome: SkillGenome,
    *,
    baseline: SkillGenome | None = None,
) -> CompiledPacaSkill:
    """Compile genome delta into natural language instruction fragments.

    Only modules that differ from baseline are compiled (delta injection).
    Output is natural language that the detection LLM can directly follow.
    """
    if set(genome.modules) != set(MODULE_IDS):
        raise ValueError("genome must contain exactly the eight registered modules")
    baseline = baseline or create_baseline_genome()

    module_texts: dict[str, str] = {}
    delta_rule_ids: dict[str, tuple[tuple[str, str], ...]] = {}
    action_specs: dict[str, dict[str, Any]] = {}
    rule_conditions: dict[str, str] = {}
    condition_specs: dict[str, dict[str, Any]] = {}
    for module_id in MODULE_IDS:
        delta_lines = _compile_module_delta(
            genome.modules[module_id],
            baseline.modules[module_id],
        )
        module_texts[module_id] = "\n".join(delta_lines)
        delta_rule_ids[module_id] = _delta_rule_ids(
            genome.modules[module_id],
            baseline.modules[module_id],
        )
        action_specs.update(_delta_action_specs(
            genome.modules[module_id], baseline.modules[module_id],
        ))
        rule_conditions.update(_delta_rule_conditions(
            genome.modules[module_id], baseline.modules[module_id],
        ))
        condition_specs.update(_delta_condition_specs(
            genome.modules[module_id], baseline.modules[module_id],
        ))

    stages: dict[PacaStage, CompiledStageSkill] = {}
    for stage in PacaStage:
        module_ids = tuple(
            mid for mid in MODULE_IDS if PACA_MODULE_STAGE[mid] == stage
        )
        changed = tuple(
            module_id for module_id in module_ids if module_texts[module_id]
        )
        lines: list[str] = []
        if changed:
            lines.extend([
                "## 进化规则（你必须在检测过程中严格遵循以下规则）",
                "",
            ])
        for module_id in changed:
            lines.extend(module_texts[module_id].splitlines())
        if changed:
            lines.append("")
        text = "\n".join(lines)
        stages[stage] = CompiledStageSkill(
            stage=stage,
            module_ids=module_ids,
            changed_module_ids=changed,
            text=text,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
    return CompiledPacaSkill(
        genome_hash=genome.execution_hash,
        stages=stages,
        module_texts=module_texts,
        delta_rule_ids=delta_rule_ids,
        action_specs=action_specs,
        rule_conditions=rule_conditions,
        condition_specs=condition_specs,
    )


def route_binding_map(
    config: Mapping[str, Any],
) -> dict[int, tuple[str, PacaStage, tuple[str, ...]]]:
    """Bind each concrete route object to its executable genome modules."""
    stage_by_key = {
        key: stage
        for stage, keys in ROUTE_STAGE_KEYS.items()
        for key in keys
    }
    result: dict[int, tuple[str, PacaStage, tuple[str, ...]]] = {}
    for key, module_ids in ROUTE_MODULE_IDS.items():
        route = config.get(key)
        if not isinstance(route, dict):
            continue
        identity = id(route)
        binding = (key, stage_by_key[key], module_ids)
        existing = result.get(identity)
        if existing is not None and existing != binding:
            raise ValueError(
                f"route object is reused across incompatible bindings: "
                f"{existing[0]} and {key}"
            )
        result[identity] = binding
    return result


def make_skill_aware_provider(
    base_provider: ProviderCall,
    *,
    compiled: CompiledPacaSkill,
    config: Mapping[str, Any],
) -> ProviderCall:
    """Wrap a detector provider call and append the correct stage fragment.

    Execution boundary: ActionSpec is a *prompt-steering* contract, not a
    framework executor. The framework dispatches the rule (injects it into the
    provider system prompt) and collects the provider's self-reported
    compliance (``rule_effects``), but the provider generates the semantic
    content. The framework never fabricates evidence, certificates, or facts.

    Four independently-observable events are distinguished downstream in
    intervention-fitness attribution:
      1. action_dispatched  — rule was injected into the provider prompt
      2. provider_complied  — provider self-reported applying the rule
      3. target_edge_changed — deterministic baseline->replay postcondition met
      4. terminal_changed   — aggregate terminal state moved
    Only (3) grants causal credit; (4) is telemetry (replay is stochastic);
    (2) is advisory (the provider is not an independent witness); (1) is
    necessary but not sufficient.
    """

    bindings = route_binding_map(config)

    def call(
        *,
        route: dict[str, Any],
        system: str,
        user: str,
        cache_dir: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        binding = bindings.get(id(route))
        if binding is None:
            raise ValueError("unregistered provider route cannot receive an evolved skill")
        route_key, stage, module_ids = binding
        candidate_modules = tuple(
            module_id
            for module_id in module_ids
            if compiled.module_texts.get(module_id)
        )
        route_features = project_route_condition_features(route_key, user)
        candidate_rule_ids: list[str] = []
        injected_rule_ids: list[str] = []
        condition_matched_rule_ids: list[str] = []
        condition_not_matched_rule_ids: list[str] = []
        legacy_untyped_rule_ids: list[str] = []
        condition_evaluations: list[dict[str, Any]] = []
        suppressed_rule_ids: list[str] = []
        injected_module_ids: list[str] = []
        lines: list[str] = []
        if candidate_modules:
            lines.extend([
                "## 进化规则（仅适用于当前 provider 的输出职责）",
                "",
            ])
            for module_id in candidate_modules:
                for rule_id, rule in compiled.delta_rule_ids.get(module_id, ()):
                    action_spec = compiled.action_specs.get(rule_id)
                    if action_spec and route_key not in action_spec.get("route_ids", ()):
                        continue
                    candidate_rule_ids.append(rule_id)
                    rule_text = str(rule or "")
                    # Runtime injection guard: an evolved rule is instruction
                    # text placed inside a provider system prompt. Suppress
                    # fragments that attempt to override the role/system
                    # boundary so a case-specific rule cannot re-purpose the
                    # provider role (the evolution-time metamorphic gate is
                    # not a runtime defense).
                    rule_lowered = rule_text.lower()
                    if any(
                        marker in rule_lowered
                        for marker in (
                            "ignore previous instructions",
                            "ignore all previous",
                            "system prompt",
                            "you are now",
                            "act as system",
                            "override all instructions",
                            "do not follow",
                        )
                    ):
                        suppressed_rule_ids.append(rule_id)
                        continue
                    typed_condition = compiled.condition_specs.get(rule_id)
                    if typed_condition:
                        evaluation = evaluate_condition_spec(
                            typed_condition, route_features,
                        )
                        condition_evaluations.append({
                            "rule_id": rule_id,
                            **evaluation.to_dict(),
                        })
                        if not evaluation.matched:
                            condition_not_matched_rule_ids.append(rule_id)
                            continue
                        condition_matched_rule_ids.append(rule_id)
                    else:
                        legacy_untyped_rule_ids.append(rule_id)
                    injected_rule_ids.append(rule_id)
                    if module_id not in injected_module_ids:
                        injected_module_ids.append(module_id)
                    if action_spec and route_key in action_spec.get("route_ids", ()):
                        condition = compiled.rule_conditions.get(rule_id)
                        if not condition:
                            condition = _condition_from_rendered_rule(rule_text)
                        lines.append(json.dumps({
                            "rule_id": rule_id,
                            "condition": condition,
                            "condition_spec": dict(typed_condition or {}),
                            "action_spec": dict(action_spec),
                        }, ensure_ascii=False, sort_keys=True))
                    else:
                        lines.append(
                            f"[{rule_id}] {rule_text}"
                        )
            lines.extend([
                "",
                "For each applicable action_spec, enforce its operation on the "
                "named output_object/output_field before returning JSON. The "
                "postcondition is an output obligation, not explanatory text.",
                "",
                "Execution telemetry is required whenever evolved rules are "
                "injected. Return a top-level rule_effects array even when it is "
                "empty. For every rule whose condition influenced this output, "
                "add exactly {\"rule_id\":\"<injected id>\",\"output_fields\":"
                "[\"<actual JSON field changed by the rule>\"]}. Never report a "
                "rule that did not influence the output.",
                "只要注入了进化规则，就必须返回顶层 rule_effects 数组；未应用任何规则时"
                "返回空数组。实际影响输出的每条规则必须记录准确的规则 id 和发生变化的"
                "JSON 字段，不得报告未影响输出的规则。",
            ])
        injected_modules = tuple(injected_module_ids)
        if not injected_rule_ids:
            lines = []
        fragment_text = "\n".join(lines)
        fragment_hash = hashlib.sha256(fragment_text.encode("utf-8")).hexdigest()
        augmented_system = (
            system if not fragment_text else f"{system}\n\n{fragment_text}"
        )
        value, meta = base_provider(
            route=route,
            system=augmented_system,
            user=user,
            cache_dir=cache_dir,
        )
        rule_effects: list[dict[str, Any]] = []
        applied_rule_ids: list[str] = []
        rejected_self_report_rule_ids: list[str] = []
        injected_rule_id_set = set(injected_rule_ids)
        if isinstance(value, Mapping):
            raw_effects = value.get("rule_effects")
            if isinstance(raw_effects, list):
                for effect in raw_effects:
                    if not isinstance(effect, Mapping):
                        continue
                    rule_id = str(effect.get("rule_id") or "").strip()
                    if not rule_id:
                        continue
                    if rule_id not in injected_rule_id_set:
                        rejected_self_report_rule_ids.append(rule_id)
                        continue
                    fields = effect.get("output_fields")
                    field_list = (
                        [str(field) for field in fields]
                        if isinstance(fields, list) else []
                    )
                    rule_effects.append({
                        "rule_id": rule_id,
                        "output_fields": field_list,
                    })
            if not rule_effects:
                # The flat self-report (id list) still synthesizes effects
                # with no field binding; rule_effects is the field-level
                # contract.
                raw_applied = value.get("applied_rule_ids")
                if isinstance(raw_applied, list):
                    for item in raw_applied:
                        rule_id = str(item).strip()
                        if not rule_id:
                            continue
                        if rule_id not in injected_rule_id_set:
                            rejected_self_report_rule_ids.append(rule_id)
                            continue
                        rule_effects.append({
                            "rule_id": rule_id,
                            "output_fields": [],
                        })
            applied_rule_ids = list(dict.fromkeys(
                str(effect["rule_id"]) for effect in rule_effects
            ))
            # Self-report fields are telemetry, not provider output; strip
            # them so downstream schema validation sees only the base contract.
            value = dict(value)
            value.pop("rule_effects", None)
            value.pop("applied_rule_ids", None)
        return value, {
            **meta,
            "bccse_genome_hash": compiled.genome_hash,
            "bccse_stage": stage.value,
            "bccse_route": route_key,
            "bccse_injected_modules": list(injected_modules),
            "bccse_candidate_rule_ids": list(dict.fromkeys(candidate_rule_ids)),
            "bccse_condition_matched_rule_ids": list(dict.fromkeys(
                condition_matched_rule_ids
            )),
            "bccse_condition_not_matched_rule_ids": list(dict.fromkeys(
                condition_not_matched_rule_ids
            )),
            "bccse_legacy_untyped_rule_ids": list(dict.fromkeys(
                legacy_untyped_rule_ids
            )),
            "bccse_condition_evaluations": condition_evaluations,
            "bccse_condition_features": route_features,
            "bccse_suppressed_rule_ids": list(dict.fromkeys(suppressed_rule_ids)),
            "bccse_injected_rule_ids": injected_rule_ids,
            "bccse_applied_rule_ids": applied_rule_ids,
            "bccse_rule_effects": rule_effects,
            "bccse_rejected_self_report_rule_ids": list(dict.fromkeys(
                rejected_self_report_rule_ids
            )),
            "bccse_stage_skill_hash": fragment_hash,
            "bccse_route_skill_hash": fragment_hash,
        }

    return call


@contextmanager
def integrated_engine_provider(
    *,
    genome: SkillGenome,
    config: Mapping[str, Any],
) -> Iterator[CompiledPacaSkill]:
    """Temporarily attach a genome to the engine's provider call surface.

    The engine imports provider functions by value, so both bindings must be
    replaced.  A process lock prevents concurrent genomes from sharing global
    provider state.  Original functions are restored even if evaluation fails.

    Thread-safety constraint:
        ``_PATCH_LOCK`` serializes all genome evaluations in the process.
        This is intentional: ``provider_call`` is a module-level binding on
        the engine, so concurrent evaluations with different genomes would
        cross-contaminate skill context. Parallel evaluation of different
        genomes must use separate processes (not threads). The baseline
        detector does NOT use this context manager and is unaffected.
    """

    from sehd_rise.detection.court import engine

    compiled = compile_genome_for_paca(genome)
    with _PATCH_LOCK:
        original_call = engine.provider_call
        original_json = engine.json_object_provider_call
        wrapped = make_skill_aware_provider(original_call, compiled=compiled, config=config)
        wrapped_json = make_skill_aware_provider(
            original_json, compiled=compiled, config=config,
        )
        engine.provider_call = wrapped
        engine.json_object_provider_call = wrapped_json
        try:
            yield compiled
        finally:
            engine.provider_call = original_call
            engine.json_object_provider_call = original_json
