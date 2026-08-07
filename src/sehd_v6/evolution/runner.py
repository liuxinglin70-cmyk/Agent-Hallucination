"""Standalone self-evolution public API.

Closed loop:

    raw trajectories -> detector -> self-evolution ->
    experience memory + champion genome

This module owns no experiment protocol.  Partitions, manifests, development
probes, pre-registered canaries and execution authorization live in experiment
orchestration and are not required here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..core.bccse.evolution_runner import (
    OnlineEvolutionResult,
    load_evolution_checkpoint,
    run_online_evolution,
)
from ..core.bccse.genome import SkillGenome
from ..core.bccse.policy_genome import (
    PolicyGenome,
    validate_policy_genome_items,
)
from ..core.paca_tcl.detector import resolve_detector_config
from ..provider_routes import has_provider_credential, resolve_provider_route
from .state_manifest import (
    load_state_manifest,
    write_state_manifest,
)

# Single source of truth for the independent reflection sample count: the
# public API and the CLI share this default so one knob cannot drift into
# two different "default" run costs.
DEFAULT_REFLECTION_CANDIDATES = 1


def default_mutation_route() -> dict[str, Any]:
    """Resolve the mutation route from environment with official defaults.

    The 1024-token budget is too small for DeepSeek-V4-Flash reasoning:
    the model spends most of it on reasoning_content and truncates the
    final JSON (finish_reason=length, empty content), which fails the
    provider empty-content gate and silently kills evolution edits.
    """
    return resolve_provider_route(
        {"temperature": 0.3, "max_tokens": 8192},
        purpose="mutation",
        route_name="mutation",
    )


def load_records(path: Path) -> dict[str, dict[str, Any]]:
    """Load raw trajectories from a JSON mapping or a JSONL sequence.

    Supported inputs:
      - JSON object mapping ``case_id -> record``
      - JSON object with a ``history`` key (a single record)
      - JSONL lines: ``{"case_id": "...", "record": {...}}`` or a record
        with an embedded ``case_id``
    """
    path = Path(path)
    if path.suffix.lower() == ".jsonl":
        rows: list[Any] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("record file must contain an object")
        if "history" in value:
            rows = [value]
        elif value and all(isinstance(item, dict) for item in value.values()):
            return {str(key): dict(item) for key, item in value.items()}
        else:
            raise ValueError("record mapping values must be objects")
    records: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"record {index} must be an object")
        case_id = str(row.get("case_id") or f"case_{index:04d}")
        record = row.get("record") if isinstance(row.get("record"), dict) else row
        if case_id in records:
            raise ValueError(f"duplicate case_id: {case_id}")
        records[case_id] = dict(record)
    return records


def _resolve_mutation_route(
    route: Path | Mapping[str, Any] | None,
    *,
    purpose: str = "mutation",
) -> dict[str, Any]:
    if route is None:
        return default_mutation_route()
    if isinstance(route, Path):
        value = json.loads(Path(route).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("mutation route file must contain an object")
        route = value
    return resolve_provider_route(
        dict(route), purpose=purpose, route_name=purpose,
    )


def _load_initial_genome(
    path: Path | SkillGenome | None,
) -> SkillGenome | None:
    if path is None:
        return None
    if isinstance(path, SkillGenome):
        return path
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("genome file must contain an object")
    return SkillGenome.from_dict(payload)


def _load_initial_policy_genome(
    path: Path | PolicyGenome | None,
) -> PolicyGenome | None:
    if path is None:
        return None
    if isinstance(path, PolicyGenome):
        items_ok, items_reason = validate_policy_genome_items(path)
        if not items_ok:
            raise ValueError(
                "initial policy genome failed the closed vocabulary "
                f"contract: {items_reason}"
            )
        return path
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("policy genome file must contain an object")
    genome = PolicyGenome.from_dict(payload)
    items_ok, items_reason = validate_policy_genome_items(genome)
    if not items_ok:
        raise ValueError(
            "initial policy genome failed the closed vocabulary "
            f"contract: {items_reason}"
        )
    return genome


def run_evolution(
    *,
    records: Mapping[str, Mapping[str, Any]] | Path,
    detector_config: Path | Mapping[str, Any],
    output_dir: Path,
    census_root: Path | None = None,
    mutation_route: Path | Mapping[str, Any] | None = None,
    pro_mutation_route: Path | Mapping[str, Any] | None = None,
    initial_genome: Path | SkillGenome | None = None,
    initial_policy_genome: Path | PolicyGenome | None = None,
    experience_memory_path: Path | None = None,
    use_gold: bool = False,
    fitness_use_gold: bool = False,
    gold_reflection: bool = False,
    run_id: str | None = None,
    module_cooldown: int = 0,
    max_total_edits: int = 200,
    convergence_window: int = 20,
    max_mutation_attempts_per_case: int = 2,
    reflection_candidate_count: int = DEFAULT_REFLECTION_CANDIDATES,
    max_mutation_calls: int = 100,
    canary_limit: int = 2,
    resume_from: Path | None = None,
    inherit_from: Path | None = None,
) -> OnlineEvolutionResult:
    """Run the label-free detector -> evolution -> experience closed loop.

    Args:
        records: Mapping of case_id -> raw trajectory, or a path to a JSON /
            JSONL file (see ``load_records``).  Evaluator-only fields such as
            ``gold_label`` may be present for the optional gold-enhanced mode;
            they are stripped before every detector call.
        detector_config: Detector config path or resolved route mapping.
        output_dir: Artifact directory.  The runner writes
            ``evolution_result.json``, ``champion_genome.json`` and
            ``htc_tes_experience_memory.json`` here, and the run finalizes
            with ``evolution_state_manifest.json`` binding those artifacts.
        census_root: Optional precomputed census cache.  Omitted by default;
            the detector falls back to its provider census route.
        mutation_route: Optional reflection/mutation route.  When omitted the
            official default (``SEHD_MUTATION_*``) is used.
        pro_mutation_route: Optional deeper-diagnosis route, used only when
            a source exhausts its bounded Flash attempts with a categorical
            no-edit outcome; one Pro attempt is then allowed on the same
            source. It must be passed explicitly to enable escalation — when
            omitted no Pro escalation happens.
        initial_genome: Optional audited genome to continue evolving.
        initial_policy_genome: Optional audited L2 adjudication-policy genome
            (a persisted ``policy_genome.json`` path or a PolicyGenome
            instance) to continue evolving alongside the skill genome. When
            omitted the run starts from the empty baseline policy set, so
            this is the inheritance path that carries L2 skills into a fresh
            run.
        experience_memory_path: Optional HTC-TES experience memory artifact to
            seed this run.  The closed loop writes an updated memory back to
            ``output_dir`` for the next run.
        use_gold / fitness_use_gold: Explicit opt-in to gold-enhanced
            attribution / fitness selection.  Default is strict self-supervision.
        run_id: Optional run identifier.
        module_cooldown / max_total_edits / convergence_window /
            max_mutation_attempts_per_case / canary_limit: framework knobs.
        resume_from: Optional directory holding a previously persisted
            checkpoint (``evolution_checkpoint.json`` + champion/shadow
            genome checkpoints + HTC-TES experience memory).  When set, the
            run restores champion / shadow genomes, accumulated steps,
            accepted-edits history, budget usage and stable canaries, then
            resumes the work queue from ``next_case_index``.  The supplied
            ``records`` mapping must contain every case_id from the
            original run (matching the checkpoint's ``evolution_case_ids``),
            otherwise a ``ValueError`` is raised.  ``initial_genome`` and
            ``experience_memory_path`` are ignored when resuming (the
            checkpoint's champion genome and experience memory take
            precedence).  Use ``load_evolution_checkpoint`` for inspection.
        inherit_from: Optional directory of a completed run holding
            ``evolution_state_manifest.json``.  The run starts a NEW case
            stream carrying the previous run's complete genome, policy
            genome and experience memory (validated by the manifest hashes).
            Explicit ``initial_genome`` / ``initial_policy_genome`` /
            ``experience_memory_path`` arguments that conflict with the
            manifest are rejected.  Mutually exclusive with ``resume_from``.

    Returns:
        OnlineEvolutionResult with the champion genome and audit trail.
    """
    if isinstance(records, Path):
        records = load_records(records)
    else:
        if not records:
            raise ValueError("records must not be empty")
        if not all(isinstance(record, Mapping) for record in records.values()):
            raise ValueError("record values must be objects")
        records = {str(key): dict(record) for key, record in records.items()}
    if not records:
        raise ValueError("records must not be empty")

    if isinstance(detector_config, Path):
        config = resolve_detector_config(
            json.loads(Path(detector_config).read_text(encoding="utf-8")),
        )
    else:
        config = resolve_detector_config(dict(detector_config))

    resolved_mutation_route = _resolve_mutation_route(mutation_route)
    if not has_provider_credential(resolved_mutation_route):
        resolved_mutation_route = None

    lineage: list[dict[str, str]] = []
    if inherit_from is not None:
        if resume_from is not None:
            raise ValueError(
                "resume_from and inherit_from are mutually exclusive"
            )
        inherited = load_state_manifest(Path(inherit_from))
        inherited_genome = Path(inherited["provider_rule_genome"]["path"])
        inherited_policy = Path(
            inherited["adjudication_policy_genome"]["path"]
        )
        inherited_memory = Path(inherited["experience_memory"]["path"])
        for label, explicit, inherited_path in (
            ("initial_genome", initial_genome, inherited_genome),
            ("initial_policy_genome", initial_policy_genome, inherited_policy),
            ("experience_memory_path", experience_memory_path, inherited_memory),
        ):
            if explicit is None:
                continue
            explicit_path = (
                Path(explicit)
                if isinstance(explicit, (str, Path))
                else None
            )
            if explicit_path != inherited_path:
                raise ValueError(
                    f"{label} conflicts with the inherited state manifest; "
                    f"inheritance is the single complete state source"
                )
        lineage = [
            {
                "run_id": str(inherited.get("source_run_id") or ""),
                "dir": str(Path(inherit_from)),
            },
            *[
                {
                    "run_id": str(entry.get("run_id") or ""),
                    "dir": str(entry.get("dir") or ""),
                }
                for entry in (inherited.get("lineage") or ())
                if isinstance(entry, Mapping)
            ],
        ]
        initial_genome = inherited_genome
        initial_policy_genome = inherited_policy
        experience_memory_path = inherited_memory

    result = run_online_evolution(
        evolution_case_ids=list(records),
        records=records,
        config=config,
        output_dir=Path(output_dir),
        census_root=Path(census_root) if census_root is not None else None,
        use_gold=use_gold,
        mutation_route=resolved_mutation_route,
        module_cooldown=module_cooldown,
        max_total_edits=max_total_edits,
        convergence_window=convergence_window,
        fitness_check=True,
        fitness_batch_size=1,
        fitness_use_gold=fitness_use_gold,
        experience_memory_path=(
            Path(experience_memory_path)
            if experience_memory_path is not None else None
        ),
        canary_limit=canary_limit,
        canary_case_ids=(),
        validation_probe_every=0,
        probe_ids=(),
        probe_limit=0,
        validation_probe_trigger_threshold=0,
        max_mutation_attempts_per_case=max_mutation_attempts_per_case,
        reflection_candidate_count=reflection_candidate_count,
        max_mutation_calls=max_mutation_calls,
        initial_genome=_load_initial_genome(initial_genome),
        initial_policy_genome=_load_initial_policy_genome(initial_policy_genome),
        pro_mutation_route=_resolve_mutation_route(
            pro_mutation_route, purpose="pro"
        ) if pro_mutation_route is not None else None,
        run_id=run_id,
        resume_from=Path(resume_from) if resume_from is not None else None,
        gold_reflection=gold_reflection,
    )
    write_state_manifest(
        Path(output_dir),
        run_id=result.run_id,
        lineage=lineage,
    )
    return result
