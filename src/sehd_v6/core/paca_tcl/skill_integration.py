"""BCCSE v4 integration adapter for the frozen PACA-TCL v3.5 detector.

The adapter maps the eight typed genome modules onto PACA-TCL's three runtime
stages without changing the v3.5 proof-court decision rules or CRS weights.
Typed clauses are appended at provider boundaries as auditable constraints.

CESV remains an auxiliary graph enhancement only: PROPAGATE backtracking feeds
``earliest_origin_tiebreak`` and deterministic do-intervention diagnostics feed
``causal_link_test``.  Neither signal replaces PACA-TCL components or changes
the registered official/auxiliary accounting.
"""

from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence

from ..bccse import cesv_contract as cesv
from ..bccse.genome import (
    MODULE_IDS,
    MODULE_DISPLAY_NAMES,
    MODULE_STAGE,
    SkillGenome,
    SkillModule,
    create_baseline_genome,
)


FROZEN_CRS_CLAUSE = (
    "score(factuality=0.35, counterfactual=0.15, recovery=0.20, "
    "process=0.20, scope=0.10)"
)
_PATCH_LOCK = threading.RLock()


class PacaStage(str, Enum):
    """The three stable PACA-TCL v3.5 runtime stages."""

    STAGE_1_CENSUS = "stage_1_census"
    STAGE_2_VERIFICATION = "stage_2_verification"
    STAGE_3_CAUSAL_COURT = "stage_3_causal_court"


MODULE_STAGE: Mapping[str, PacaStage] = {
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
    "candidate_fallback_route": ("candidate_trigger",),
    "construct_census_route": ("candidate_trigger",),
    "qualification_route": (
        "candidate_to_verifier_route",
        "atomic_verifier_instruction",
    ),
    "propagation_route": (
        "evidence_obligation",
        "causal_link_test",
    ),
    "focused_prosecution_route": (
        "evidence_obligation",
        "atomic_verifier_instruction",
        "causal_link_test",
    ),
    "defense_route": (
        "atomic_verifier_instruction",
        "clean_defense_boundary",
    ),
    "appeal_prosecutor_route": (
        "evidence_obligation",
        "atomic_verifier_instruction",
        "causal_link_test",
    ),
    "appeal_defender_route": (
        "atomic_verifier_instruction",
        "clean_defense_boundary",
    ),
    "boundary_route": (
        "clean_defense_boundary",
        "causal_link_test",
        "earliest_origin_tiebreak",
        "abstention_rule",
    ),
    "counterfactual_route": (
        "causal_link_test",
        "earliest_origin_tiebreak",
    ),
}


class ProviderCall(Protocol):
    """PACA-TCL provider-call signature."""

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
    """Deterministically compiled active modules for one PACA stage."""

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
    """Complete, content-addressed PACA-TCL integration artifact."""

    genome_hash: str
    stages: Mapping[PacaStage, CompiledStageSkill]
    module_texts: Mapping[str, str]
    frozen_crs_weights: tuple[float, ...] = (0.35, 0.15, 0.20, 0.20, 0.10)
    schema_version: str = "agenthallu.paca_tcl_skill_integration.v4"

    def for_stage(self, stage: PacaStage) -> CompiledStageSkill:
        return self.stages[stage]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "genome_hash": self.genome_hash,
            "frozen_crs_weights": list(self.frozen_crs_weights),
            "module_texts": dict(self.module_texts),
            "stages": {
                stage.value: compiled.to_dict()
                for stage, compiled in self.stages.items()
            },
        }


@dataclass(frozen=True)
class CausalLinkDiagnostic:
    """CESV do-intervention evidence routed to ``causal_link_test``."""

    candidate_id: str
    observed_origin: str
    propagate_root: str
    propagate_path: tuple[str, ...]
    interventional_effect: float
    affected_downstream: int
    total_downstream: int
    causal_link_supported: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "observed_origin": self.observed_origin,
            "propagate_root": self.propagate_root,
            "propagate_path": list(self.propagate_path),
            "interventional_effect": self.interventional_effect,
            "affected_downstream": self.affected_downstream,
            "total_downstream": self.total_downstream,
            "causal_link_supported": self.causal_link_supported,
        }


@dataclass(frozen=True)
class CesvSkillEnhancement:
    """Auxiliary CESV graph outputs consumed by the two L5 modules."""

    earliest_origin_tiebreak: Mapping[str, str]
    causal_link_test: Mapping[str, CausalLinkDiagnostic]
    counterfactual_rankings: Mapping[str, Mapping[str, Any]]
    official_decision_override: bool = False
    crs_weight_changed: bool = False
    schema_version: str = "agenthallu.cesv_skill_enhancement.v4"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "earliest_origin_tiebreak": dict(self.earliest_origin_tiebreak),
            "causal_link_test": {
                key: value.to_dict() for key, value in self.causal_link_test.items()
            },
            "counterfactual_rankings": {
                key: dict(value) for key, value in self.counterfactual_rankings.items()
            },
            "official_decision_override": self.official_decision_override,
            "crs_weight_changed": self.crs_weight_changed,
        }


def _stable_hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Module display names for natural language injection
MODULE_DISPLAY_NAMES_LOCAL: dict[str, str] = MODULE_DISPLAY_NAMES


def _compile_module(module: SkillModule) -> list[str]:
    """Compile a module's rules into natural language behavioral instructions."""
    if not module.active or not module.rules:
        return []

    display_name = MODULE_DISPLAY_NAMES_LOCAL.get(module.module_id, module.module_id)
    rows = []
    for rule in module.rules:
        rows.append(f"【{display_name}】{rule}")
    return rows


def _compile_module_delta(
    module: SkillModule,
    baseline_module: SkillModule,
) -> list[str]:
    """Compile only rules that are not already part of the frozen baseline."""
    if not module.active:
        return []
    unmatched_baseline = list(baseline_module.rules)
    delta_rules: list[str] = []
    for rule in module.rules:
        if rule in unmatched_baseline:
            unmatched_baseline.remove(rule)
        else:
            delta_rules.append(rule)
    if not delta_rules:
        return []
    display_name = MODULE_DISPLAY_NAMES_LOCAL.get(
        module.module_id,
        module.module_id,
    )
    return [f"【{display_name}】{rule}" for rule in delta_rules]


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
    for module_id in MODULE_IDS:
        delta_lines = _compile_module_delta(
            genome.modules[module_id],
            baseline.modules[module_id],
        )
        module_texts[module_id] = "\n".join(delta_lines)

    stages: dict[PacaStage, CompiledStageSkill] = {}
    for stage in PacaStage:
        module_ids = tuple(mid for mid in MODULE_IDS if MODULE_STAGE[mid] == stage.value)
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
        genome_hash=genome.content_hash,
        stages=stages,
        module_texts=module_texts,
    )


def route_stage_map(config: Mapping[str, Any]) -> dict[int, PacaStage]:
    """Map route object identities to stages, rejecting ambiguous reuse."""

    result: dict[int, PacaStage] = {}
    for stage, keys in ROUTE_STAGE_KEYS.items():
        for key in keys:
            route = config.get(key)
            if not isinstance(route, dict):
                continue
            identity = id(route)
            existing = result.get(identity)
            if existing is not None and existing != stage:
                raise ValueError(f"route object is reused across incompatible stages: {key}")
            result[identity] = stage
    return result


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
    """Wrap a PACA provider call and append the correct stage fragment."""

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
        injected_modules = tuple(
            module_id
            for module_id in module_ids
            if compiled.module_texts.get(module_id)
        )
        lines: list[str] = []
        if injected_modules:
            lines.extend([
                "## 进化规则（仅适用于当前 provider 的输出职责）",
                "",
            ])
            for module_id in injected_modules:
                lines.extend(compiled.module_texts[module_id].splitlines())
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
        return value, {
            **meta,
            "bccse_genome_hash": compiled.genome_hash,
            "bccse_stage": stage.value,
            "bccse_route": route_key,
            "bccse_injected_modules": list(injected_modules),
            "bccse_stage_skill_hash": fragment_hash,
            "bccse_route_skill_hash": fragment_hash,
        }

    return call


@contextmanager
def integrated_v35_provider(
    *,
    genome: SkillGenome,
    config: Mapping[str, Any],
) -> Iterator[CompiledPacaSkill]:
    """Temporarily attach a genome to both v3.5 and nested v2 provider calls.

    The runner imports provider functions by value, so both bindings must be
    replaced.  A process lock prevents concurrent genomes from sharing global
    provider state.  Original functions are restored even if evaluation fails.

    Thread-safety note:
        ``_PATCH_LOCK`` serializes all genome evaluations in the process.
        This is intentional: ``provider_call`` is a module-level binding
        shared by ``proof_engine`` and ``layered_engine``, so concurrent
        evaluations with different genomes would cross-contaminate skill
        context. Parallel evaluation of different genomes must use separate
        processes (not threads). The baseline ``run()`` function does NOT
        use this context manager and is unaffected.

        v3.4 provider bindings are intentionally NOT patched here: v3.5
        only imports pure helper functions from v3.4 (no provider_call
        invocations). If v3.4 helpers are extended to call provider_call
        directly, add ``from . import proof_engine_v3_4 as v34`` and patch
        ``v34.provider_call`` here as well.
    """

    from . import layered_engine as v2
    from . import proof_engine as v35

    compiled = compile_genome_for_paca(genome)
    with _PATCH_LOCK:
        original_v35 = v35.provider_call
        original_v35_json = v35.json_object_provider_call
        original_v2 = v2.provider_call
        wrapped = make_skill_aware_provider(original_v35, compiled=compiled, config=config)
        wrapped_json = make_skill_aware_provider(
            original_v35_json, compiled=compiled, config=config,
        )
        v35.provider_call = wrapped
        v35.json_object_provider_call = wrapped_json
        v2.provider_call = wrapped
        try:
            yield compiled
        finally:
            v35.provider_call = original_v35
            v35.json_object_provider_call = original_v35_json
            v2.provider_call = original_v2


def adjudicate_case_with_genome(
    *,
    genome: SkillGenome,
    case_id: str,
    record: dict[str, Any],
    config: dict[str, Any],
    output_dir: Path,
    census_root: Path,
) -> dict[str, Any]:
    """Run the frozen v3.5 case path with stage-specific evolved skill context."""

    from . import proof_engine as v35

    with integrated_v35_provider(genome=genome, config=config) as compiled:
        output = v35.adjudicate_case(
            case_id=case_id,
            record=record,
            config=config,
            output_dir=output_dir,
            census_root=census_root,
        )
    output["bccse_skill"] = {
        "genome_hash": compiled.genome_hash,
        "stage_skill_hashes": {
            stage.value: item.content_hash for stage, item in compiled.stages.items()
        },
        "crs_weights_changed": False,
        "base_detector": "PACA-TCL v3.5",
    }
    return output


def build_cesv_skill_enhancement(
    *,
    packet: dict[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    genome: SkillGenome,
) -> CesvSkillEnhancement:
    """Route CESV graph signals to the two registered L5 skill modules."""

    graph = cesv.build_provenance_graph(packet)
    engine = cesv.CausalInterventionEngine(graph)
    earliest_active = genome.modules["earliest_origin_tiebreak"].active
    causal_active = genome.modules["causal_link_test"].active
    earliest: dict[str, str] = {}
    diagnostics: dict[str, CausalLinkDiagnostic] = {}
    rankings: dict[str, Mapping[str, Any]] = {}

    for row in candidates:
        candidate_id = str(row.get("candidate_id") or row.get("source_candidate_id") or "")
        origin = str(
            row.get("origin_event_id")
            or row.get("origin_hypothesis_event_id")
            or row.get("event_id")
            or ""
        )
        if not candidate_id or origin not in graph.nodes:
            continue
        path = tuple(engine.causal_backtrack(origin))
        root = path[0] if path else origin
        intervention = engine.do_intervention(root)
        effect = float(intervention.get("interventional_effect") or 0.0)
        diagnostic = CausalLinkDiagnostic(
            candidate_id=candidate_id,
            observed_origin=origin,
            propagate_root=root,
            propagate_path=path,
            interventional_effect=effect,
            affected_downstream=int(intervention.get("affected_downstream") or 0),
            total_downstream=int(intervention.get("total_downstream") or 0),
            causal_link_supported=bool(intervention.get("severed_edge_count")) and effect > 0.0,
        )
        if earliest_active:
            earliest[candidate_id] = root
        if causal_active:
            diagnostics[candidate_id] = diagnostic
            rankings[root] = {
                "causal_sufficiency_score": effect,
                "is_root_cause": root != origin or diagnostic.causal_link_supported,
                "source": "CESV_do_intervention_auxiliary",
            }

    return CesvSkillEnhancement(
        earliest_origin_tiebreak=earliest,
        causal_link_test=diagnostics,
        counterfactual_rankings=rankings,
    )
