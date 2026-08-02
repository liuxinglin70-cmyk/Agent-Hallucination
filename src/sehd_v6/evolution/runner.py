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
    run_online_evolution,
)
from ..core.bccse.genome import SkillGenome
from ..core.paca_tcl.detector import resolve_detector_config
from ..provider_routes import resolve_provider_route


def default_mutation_route() -> dict[str, Any]:
    """Resolve the mutation route from environment with official defaults."""
    return resolve_provider_route(
        {"temperature": 0.3, "max_tokens": 1024},
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
) -> dict[str, Any]:
    if route is None:
        return default_mutation_route()
    if isinstance(route, Path):
        value = json.loads(Path(route).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("mutation route file must contain an object")
        route = value
    return resolve_provider_route(
        dict(route), purpose="mutation", route_name="mutation",
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


def run_evolution(
    *,
    records: Mapping[str, Mapping[str, Any]] | Path,
    detector_config: Path | Mapping[str, Any],
    output_dir: Path,
    census_root: Path | None = None,
    mutation_route: Path | Mapping[str, Any] | None = None,
    initial_genome: Path | SkillGenome | None = None,
    experience_memory_path: Path | None = None,
    use_gold: bool = False,
    fitness_use_gold: bool = False,
    run_id: str | None = None,
    module_cooldown: int = 0,
    max_total_edits: int = 200,
    convergence_window: int = 20,
    max_mutation_attempts_per_case: int = 2,
    canary_limit: int = 2,
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
            ``htc_tes_experience_memory.json`` here.
        census_root: Optional precomputed census cache.  Omitted by default;
            the detector falls back to its provider census route.
        mutation_route: Optional reflection/mutation route.  When omitted the
            official default (``SEHD_MUTATION_*``) is used.
        initial_genome: Optional audited genome to continue evolving.
        experience_memory_path: Optional HTC-TES experience memory artifact to
            seed this run.  The closed loop writes an updated memory back to
            ``output_dir`` for the next run.
        use_gold / fitness_use_gold: Explicit opt-in to gold-enhanced
            attribution / fitness selection.  Default is strict self-supervision.
        run_id: Optional run identifier.
        module_cooldown / max_total_edits / convergence_window /
            max_mutation_attempts_per_case / canary_limit: framework knobs.

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

    return run_online_evolution(
        evolution_case_ids=list(records),
        records=records,
        config=config,
        output_dir=Path(output_dir),
        census_root=Path(census_root) if census_root is not None else None,
        use_gold=use_gold,
        mutation_route=_resolve_mutation_route(mutation_route),
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
        initial_genome=_load_initial_genome(initial_genome),
        run_id=run_id,
    )
