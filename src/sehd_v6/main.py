"""BCCSE v5 — Main entry point for online skill evolution experiments.

Usage:
    # Partition once. Repartitioning requires --force-repartition.
    python -m scripts.agenthallu.run_bccse_v5 partition

    # Run a registered evolution set.
    python -m scripts.agenthallu.run_bccse_v5 evolve --size 50

    # Evaluate an explicit genome under a formally authorized config.
    python -m scripts.agenthallu.run_bccse_v5 evaluate \
        --set validation --genome /absolute/path/champion_genome.json

The legacy ``full`` command is deliberately disabled: formal partitioning,
evolution, and evaluation must be separate, reviewable steps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# ============================================================
# Paths (configurable via environment variables or --project-root)
# ============================================================

BASE_ROOT = Path(os.environ.get("AGENT_HALLUCINATION_ROOT", "/root/autodl-tmp/agent_hallucination"))
PROJECT_ROOT = Path(os.environ.get("AGENT_HALLUCINATION_PROJECT_ROOT", str(BASE_ROOT / "agent-hallucination-pilot")))
D_PREDICTIONS = Path(os.environ.get("AGENT_HALLUCINATION_D_PREDICTIONS", str(BASE_ROOT / "d_baseline_v3_5" / "round1" / "predictions.jsonl")))
P0_PREDICTIONS = Path(os.environ.get("AGENT_HALLUCINATION_P0_PREDICTIONS", str(BASE_ROOT / "paca_tcl_v3_5_p0_20260723" / "round1" / "predictions.jsonl")))
CENSUS_ROOT = Path(os.environ.get("AGENT_HALLUCINATION_CENSUS_ROOT", str(BASE_ROOT / "paca_tcl_v3_5_p0_20260723" / "round1" / "construct_census_cache")))
DATA_ROOT = Path(os.environ.get("AGENT_HALLUCINATION_DATA_ROOT", str(BASE_ROOT / "data" / "agenthallu" / "raw" / "AgentHallu")))
BCCSE_V5_ROOT = Path(os.environ.get("AGENT_HALLUCINATION_BCCSE_ROOT", str(BASE_ROOT / "bccse_v5")))
V35_CONFIG_PATH = Path(os.environ.get("AGENT_HALLUCINATION_V35_CONFIG", str(PROJECT_ROOT / "configs" / "paca_tcl_proof_p0_v3_5.json")))


def _apply_project_root(project_root: str | None) -> None:
    """Recompute all path globals based on a new project root."""
    if not project_root:
        return
    base = Path(project_root)
    g = globals()
    g["BASE_ROOT"] = base
    g["PROJECT_ROOT"] = Path(os.environ.get("AGENT_HALLUCINATION_PROJECT_ROOT", str(base / "agent-hallucination-pilot")))
    g["D_PREDICTIONS"] = Path(os.environ.get("AGENT_HALLUCINATION_D_PREDICTIONS", str(base / "d_baseline_v3_5" / "round1" / "predictions.jsonl")))
    g["P0_PREDICTIONS"] = Path(os.environ.get("AGENT_HALLUCINATION_P0_PREDICTIONS", str(base / "paca_tcl_v3_5_p0_20260723" / "round1" / "predictions.jsonl")))
    g["CENSUS_ROOT"] = Path(os.environ.get("AGENT_HALLUCINATION_CENSUS_ROOT", str(base / "paca_tcl_v3_5_p0_20260723" / "round1" / "construct_census_cache")))
    g["DATA_ROOT"] = Path(os.environ.get("AGENT_HALLUCINATION_DATA_ROOT", str(base / "data" / "agenthallu" / "raw" / "AgentHallu")))
    g["BCCSE_V5_ROOT"] = Path(os.environ.get("AGENT_HALLUCINATION_BCCSE_ROOT", str(base / "bccse_v5")))
    g["V35_CONFIG_PATH"] = Path(os.environ.get("AGENT_HALLUCINATION_V35_CONFIG", str(g["PROJECT_ROOT"] / "configs" / "paca_tcl_proof_p0_v3_5.json")))
ALLOWED_MODELS = {"DeepSeek-V4-Flash", "DeepSeek-V4-Pro"}
VALID_STATES = {"verified_error", "verified_clean", "unresolved"}
PROBE_SET_ALIASES = {"probe", "validation", "test"}
FINAL_HOLDOUT_SET_ALIASES = {"final_holdout", "outer"}

# LLM route config for DeepSeek-V4 (via AutoDL proxy)
MUTATION_ROUTE = {
    "endpoint": "https://www.autodl.art/api/v1",
    "model": "DeepSeek-V4-Pro",
    "api_key_environment": "AUTODL_API_KEY",
    "temperature": 0.3,
    "max_tokens": 1024,
}


def load_config() -> dict[str, Any]:
    """Load and verify the one frozen PACA-TCL v3.5 configuration."""
    if not V35_CONFIG_PATH.exists():
        raise FileNotFoundError(f"Frozen v3.5 config missing: {V35_CONFIG_PATH}")
    with open(V35_CONFIG_PATH, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    _assert_config_contract(config)
    return config


def _assert_config_contract(config: dict[str, Any]) -> None:
    """Fail before provider calls if the frozen detector contract drifted."""
    if bool(config.get("outer_opened")):
        raise RuntimeError("Frozen config has outer_opened=true; execution aborted")
    forbidden = {str(value) for value in config.get("forbidden") or []}
    required_forbidden = {"continuous_CRS", "threshold_search", "outer_feedback"}
    missing = sorted(required_forbidden - forbidden)
    if missing:
        raise RuntimeError(
            "Frozen v3.5 config is missing required prohibitions: "
            + ", ".join(missing)
        )
    route_count = 0
    for key, value in config.items():
        if not key.endswith("_route") or not isinstance(value, dict):
            continue
        route_count += 1
        model = str(value.get("model") or "")
        if model not in ALLOWED_MODELS:
            raise RuntimeError(f"Disallowed model in {key}: {model!r}")
    if route_count == 0:
        raise RuntimeError("Frozen v3.5 config contains no provider routes")


def _assert_stage_authorized(
    config: dict[str, Any],
    *,
    stage: str,
    contains_d_cases: bool = False,
) -> None:
    """Enforce the frozen config's phase authorization before any provider call.

    Evolution is a diagnostic strategy-development path.  Validation and test
    are formal effectiveness paths and therefore cannot inherit a P0-only
    authorization.  D cases require the explicit D authorization in either
    path; an absent flag is fail-closed.
    """
    if config.get("execution_authorized") is not True:
        raise RuntimeError(f"{stage} execution is not authorized by frozen config")
    phase = str(config.get("authorized_phase") or "")
    if not phase:
        raise RuntimeError("Frozen config has no authorized_phase")

    formal = stage in {
        "evaluation",
        "probe",
        "validation",
        "test",
        "final_holdout",
        "outer",
        "effectiveness",
    }
    if formal:
        if phase == "P0_diagnostic_only" or "diagnostic" in phase.lower():
            raise RuntimeError(
                f"Formal {stage} effectiveness cannot run under diagnostic "
                f"phase {phase!r}"
            )
        if config.get("D_execution_authorized") is not True:
            raise RuntimeError(
                f"Formal {stage} effectiveness requires "
                "D_execution_authorized=true"
            )
        if not any(
            marker in phase.lower()
            for marker in (
                "effectiveness",
                "validation",
                "test",
                "oof",
                "outer",
                "final",
            )
        ):
            raise RuntimeError(
                f"Frozen phase {phase!r} does not explicitly authorize a "
                f"formal {stage} effectiveness stage"
            )
        if stage in FINAL_HOLDOUT_SET_ALIASES and config.get("outer_execution_authorized") is not True:
            raise RuntimeError(
                "Formal final-holdout/Outer evaluation requires "
                "outer_execution_authorized=true"
            )
    elif stage != "evolve":
        raise ValueError(f"Unknown execution stage: {stage!r}")

    if contains_d_cases and config.get("D_execution_authorized") is not True:
        raise RuntimeError(
            f"{stage} includes D cases but D_execution_authorized is not true"
        )


def _prediction_state(row: dict[str, Any]) -> str:
    """Compatibility wrapper around the single occurrence-first resolver."""
    from .core.bccse.signal_adapter import resolve_authoritative_state

    return resolve_authoritative_state(row)


def _decision_authority_invalid(row: dict[str, Any]) -> bool:
    """Thin wrapper for formal-scope exclusion of invalid detector rows."""
    from .core.bccse.signal_adapter import decision_authority_invalid

    return decision_authority_invalid(row)


def load_records(
    data_root: Path,
    *,
    allowed_case_ids: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Load only explicitly allowed records for formal BCCSE execution."""
    if allowed_case_ids is not None:
        if len(allowed_case_ids) != len(set(allowed_case_ids)):
            raise ValueError("allowed_case_ids contains duplicates")
        root = data_root.resolve()
        records: dict[str, dict[str, Any]] = {}
        for case_id in allowed_case_ids:
            parts = case_id.split("/")
            if (
                len(parts) != 2
                or not parts[0]
                or not parts[1]
                or any(value in {".", ".."} for value in parts)
            ):
                raise ValueError(f"Invalid case_id path: {case_id!r}")
            path = (root / parts[0] / f"{parts[1]}.json").resolve()
            if root not in path.parents:
                raise ValueError(f"Case path escaped dataset root: {case_id}")
            if not path.exists():
                raise FileNotFoundError(f"Registered case record missing: {path}")
            with open(path, "r", encoding="utf-8") as handle:
                records[case_id] = json.load(handle)
        print(
            f"  Loaded {len(records)} explicitly registered records "
            "(no full-dataset scan)"
        )
        return records

    # Non-formal utility fallback. Formal evolve/evaluate paths always pass an
    # explicit allowlist and never execute this whole-dataset branch.
    try:
        from .utils.preflight import discover_records
        records = discover_records(data_root)
        print(f"  Loaded {len(records)} records via discover_records")
        return records
    except (ImportError, Exception) as e:
        print(f"  discover_records failed: {e}, trying manual load...")

    # Fallback: manual load from subdirectories
    records: dict[str, dict[str, Any]] = {}
    for subdir in sorted(data_root.iterdir()):
        if not subdir.is_dir():
            continue
        for json_file in sorted(subdir.glob("*.json")):
            case_id = f"{subdir.name}/{json_file.stem}"
            with open(json_file, "r", encoding="utf-8") as f:
                records[case_id] = json.load(f)
    print(f"  Loaded {len(records)} records manually")
    return records


def load_baseline_predictions() -> dict[str, dict[str, Any]]:
    """Load existing baseline predictions only; never call the provider."""
    predictions: dict[str, dict[str, Any]] = {}
    paths = [
        BCCSE_V5_ROOT / "baseline_predictions_by_id.json",
        D_PREDICTIONS,
        P0_PREDICTIONS,
    ]
    for path in paths:
        if path.exists():
            if path.suffix == ".jsonl":
                with open(path, "r", encoding="utf-8") as f:
                    rows = [json.loads(line) for line in f if line.strip()]
            else:
                with open(path, "r", encoding="utf-8") as f:
                    value = json.load(f)
                if isinstance(value, dict):
                    rows = []
                    for key, row in value.items():
                        if isinstance(row, dict):
                            rows.append({**row, "_cache_key": str(key)})
                else:
                    rows = list(value)
            for row in rows:
                keys = {
                    str(row.get("_cache_key") or ""),
                    str(row.get("case_ref") or ""),
                    str(row.get("case_id") or ""),
                }
                for key in keys - {""}:
                    predictions.setdefault(key, row)
    return predictions


def _load_inner_case_refs() -> set[str]:
    """Return the sealed inner universe from the existing D and P0 caches."""
    refs: set[str] = set()
    for path in (D_PREDICTIONS, P0_PREDICTIONS):
        if not path.exists():
            raise FileNotFoundError(
                f"Cannot prove inner/Outer isolation; cached predictions missing: {path}"
            )
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                role = str(row.get("split") or row.get("partition") or "").lower()
                if role in {"outer", "outer_sealed", "test_sealed"}:
                    continue
                case_ref = str(row.get("case_ref") or "")
                if not case_ref:
                    raise RuntimeError(f"Cached inner row has no case_ref: {path}")
                if case_ref in refs:
                    raise RuntimeError(f"Duplicate inner case_ref across caches: {case_ref}")
                refs.add(case_ref)
    if len(refs) != 587:
        raise RuntimeError(
            f"Inner universe must contain exactly D523+P064=587 cases, got {len(refs)}"
        )
    return refs


def _assert_inner_membership(case_ids: list[str], *, set_name: str) -> None:
    inner_refs = _load_inner_case_refs()
    outside = [case_id for case_id in case_ids if _case_ref(case_id) not in inner_refs]
    if outside:
        raise RuntimeError(
            f"{set_name} contains cases outside the sealed inner universe: "
            + ", ".join(outside)
        )


def _contains_d_cases(case_ids: list[str]) -> bool:
    if not D_PREDICTIONS.exists():
        raise FileNotFoundError(
            f"Cannot determine D-stage authorization; cache missing: {D_PREDICTIONS}"
        )
    d_refs: set[str] = set()
    with open(D_PREDICTIONS, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                ref = str(row.get("case_ref") or "")
                if ref:
                    d_refs.add(ref)
    return any(_case_ref(case_id) in d_refs for case_id in case_ids)


def _first_existing_manifest(*relative_paths: str) -> Path | None:
    for relative_path in relative_paths:
        path = BCCSE_V5_ROOT / relative_path
        if path.exists():
            return path
    return None


def _load_registered_evolution_ids() -> list[str]:
    """Load reusable development cases, excluding the fixed gold probe.

    The v3 development manifest is preferred.  The old stratified evolution
    manifest remains a read-compatible fallback.
    """
    path = _first_existing_manifest(
        "development_pool_ids.json",
        "partition/development_pool_ids.json",
        "stratified_evolution_set.json",
    )
    if path is None:
        raise FileNotFoundError(
            "Registered development/evolution manifest missing under "
            f"{BCCSE_V5_ROOT}"
        )
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    case_ids = [
        str(value)
        for value in (
            manifest.get("evolution_case_ids")
            or manifest.get("development_case_ids")
            or []
        )
    ]
    probe_ids = {str(value) for value in manifest.get("probe_case_ids") or []}
    case_ids = [case_id for case_id in case_ids if case_id not in probe_ids]
    if not case_ids:
        raise RuntimeError("Registered development/evolution manifest is empty")
    if len(case_ids) != len(set(case_ids)):
        raise RuntimeError("Registered development manifest contains duplicate case IDs")
    _assert_inner_membership(case_ids, set_name="evolution")
    return case_ids


def _load_probe_ids_optional() -> tuple[list[str], Path | None]:
    """Load the v3 probe or a legacy validation manifest, if registered."""
    path = _first_existing_manifest(
        "probe_set_ids.json",
        "partition/probe_set_ids.json",
        "validation_set_ids.json",
        "partition/validation_set_ids.json",
    )
    if path is None:
        return [], None
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    case_ids = [
        str(value)
        for value in (
            manifest.get("probe_case_ids")
            or manifest.get("validation_case_ids")
            or []
        )
    ]
    if not case_ids or len(case_ids) != len(set(case_ids)):
        raise RuntimeError("Probe/validation manifest is empty or contains duplicates")
    _assert_inner_membership(case_ids, set_name="gold_probe")
    return case_ids, path


def _select_self_supervised_anchor_canaries(
    evolution_ids: list[str],
    *,
    limit: int,
    use_gold: bool = False,
) -> list[tuple[str, str]]:
    """Select stable replay anchors under the explicitly selected supervision."""
    if limit <= 0:
        return []
    from .core.bccse.step_attributor import attribute_failure

    baselines = load_baseline_predictions()
    stable_by_state: dict[str, list[tuple[str, str]]] = {
        "verified_clean": [],
        "verified_error": [],
    }
    for case_id in evolution_ids:
        row = baselines.get(case_id) or baselines.get(_case_ref(case_id))
        if row is None:
            continue
        signal = attribute_failure(row, use_gold=use_gold)
        state = _prediction_state(row)
        if not signal.should_evolve and state in stable_by_state:
            stable_by_state[state].append((case_id, state))

    selected: list[tuple[str, str]] = []
    for state in ("verified_clean", "verified_error"):
        if stable_by_state[state] and len(selected) < limit:
            selected.append(stable_by_state[state][0])
    if len(selected) < limit:
        remaining = [
            item
            for case_id in evolution_ids
            for state in ("verified_clean", "verified_error")
            for item in stable_by_state[state]
            if item[0] == case_id and item not in selected
        ]
        selected.extend(remaining[: limit - len(selected)])
    if not selected:
        raise RuntimeError(
            "No self-supervised stable baseline case is available for replay protection"
        )
    return selected


def _assert_disjoint_from_evolution(case_ids: list[str], *, set_name: str) -> None:
    evolution_ids = set(_load_registered_evolution_ids())
    overlap = sorted(evolution_ids.intersection(case_ids))
    if overlap:
        raise RuntimeError(
            f"Evolution/{set_name} overlap detected; execution aborted: "
            + ", ".join(overlap)
        )


# ============================================================
# Commands
# ============================================================

def cmd_partition(args: argparse.Namespace) -> None:
    """Run data partition."""
    _apply_project_root(getattr(args, "project_root", None))
    from .bccse_v5_data_partition import partition_data

    existing = BCCSE_V5_ROOT / "partition" / "data_partition_v5.json"
    if existing.exists() and not bool(args.force_repartition):
        raise RuntimeError(
            "Frozen partition already exists. Refusing to overwrite it without "
            "--force-repartition."
        )
    partition_data(
        d_predictions_path=Path(args.d_predictions),
        p0_predictions_path=Path(args.p0_predictions),
        output_dir=BCCSE_V5_ROOT / "partition",
        seed=args.seed,
        probe_size=getattr(args, "probe_size", None),
        holdout_size=getattr(args, "holdout_size", None),
        holdout_sealed=not bool(getattr(args, "unsealed_holdout", False)),
    )


def cmd_evolve(args: argparse.Namespace) -> None:
    """Run diagnostic strategy evolution; this is not an effectiveness claim."""
    _apply_project_root(getattr(args, "project_root", None))
    from .core.bccse.evolution_runner import run_online_evolution
    if bool(args.no_fitness_check):
        raise RuntimeError(
            "Registered evolution requires the source replay gate; "
            "--no-fitness-check is not permitted"
        )

    # The partition is retained as a provenance artifact, but the experiment
    # must use the separately registered evolution manifest.
    partition_path = BCCSE_V5_ROOT / "partition" / "data_partition_v5.json"
    if not partition_path.exists():
        raise FileNotFoundError(f"Frozen partition artifact missing: {partition_path}")
    with open(partition_path, "r", encoding="utf-8") as handle:
        partition = json.load(handle)

    registered_evolution_ids = _load_registered_evolution_ids()
    size = int(args.size)
    if size < 1 or size > len(registered_evolution_ids):
        raise ValueError(
            f"--size must be between 1 and {len(registered_evolution_ids)}"
        )
    evolution_ids = registered_evolution_ids[:size]
    config = load_config()
    _assert_stage_authorized(
        config,
        stage="evolve",
        contains_d_cases=_contains_d_cases(evolution_ids),
    )

    probe_ids, probe_path = _load_probe_ids_optional()
    overlap = sorted(set(evolution_ids).intersection(probe_ids))
    if overlap:
        raise RuntimeError(
            "Evolution-source/gold-probe overlap detected before provider calls: "
            + ", ".join(overlap)
        )
    final_refs = set(
        str(value)
        for value in (
            (partition.get("final_holdout") or {}).get("case_refs")
            or (partition.get("outer_sealed") or {}).get("case_refs")
            or []
        )
    )
    final_overlap = sorted(
        case_id for case_id in evolution_ids if _case_ref(case_id) in final_refs
    )
    if final_overlap:
        raise RuntimeError(
            "Development/final-holdout overlap detected before provider calls: "
            + ", ".join(final_overlap)
        )
    development_count = int(
        partition.get("development_case_count")
        or (partition.get("development_pool") or {}).get("size")
        or len(registered_evolution_ids) + len(probe_ids)
    )
    probe_count = int(
        partition.get("probe_case_count")
        or (partition.get("gold_probe") or {}).get("size")
        or len(probe_ids)
    )
    final_holdout_count = int(
        partition.get("final_holdout_count")
        or (partition.get("final_holdout") or {}).get("size")
        or (partition.get("outer_sealed") or {}).get("size")
        or 0
    )
    holdout_sealed = bool(
        partition.get("holdout_sealed")
        if "holdout_sealed" in partition
        else (partition.get("final_holdout") or {}).get("sealed", True)
    )
    probe_interval = int(getattr(args, "probe_interval", 10))
    if probe_interval < 1:
        raise ValueError("--probe-interval must be at least 1")
    requested_probe_budget = getattr(args, "probe_budget", None)
    probe_budget = (
        min(5, probe_count)
        if requested_probe_budget is None
        else int(requested_probe_budget)
    )
    if probe_budget < 0 or probe_budget > probe_count:
        raise ValueError(
            f"--probe-budget must be between 0 and probe_case_count={probe_count}"
        )
    print(
        f"Evolution set: {len(evolution_ids)} reusable development cases "
        f"(probe-source overlap=0, final-holdout exposure=0)"
    )

    # Load evolution sources plus only the budgeted prefix of the registered
    # probe.  Probe IDs are passed separately and never become mutation sources.
    dataset_root = DATA_ROOT
    active_probe_ids = probe_ids[:probe_budget]
    if active_probe_ids:
        _assert_stage_authorized(
            config,
            stage="evolve",
            contains_d_cases=_contains_d_cases(active_probe_ids),
        )
    execution_record_ids = list(dict.fromkeys([*evolution_ids, *active_probe_ids]))
    records = load_records(dataset_root, allowed_case_ids=execution_record_ids)
    missing_records = [
        case_id for case_id in execution_record_ids if case_id not in records
    ]
    if missing_records:
        raise RuntimeError(
            "Evolution records missing; partial evolution is forbidden: "
            + ", ".join(missing_records)
        )
    # Gold is evaluator-side metadata, not detector input. Raw trajectory
    # records do not have to carry labels, so join boolean labels from the
    # registered baseline cache before handing records to the runner. The
    # runner strips these fields at every detector call and only joins them
    # inside the explicit Gold fitness/probe evaluator.
    cached_predictions = load_baseline_predictions()
    for case_id, record in records.items():
        if isinstance(record.get("gold_label"), bool):
            continue
        prediction = (
            cached_predictions.get(case_id)
            or cached_predictions.get(_case_ref(case_id))
        )
        if isinstance(prediction, dict) and isinstance(
            prediction.get("gold_label"), bool
        ):
            record["gold_label"] = bool(prediction["gold_label"])
    print(f"  Available for evolution: {len(evolution_ids)}/{len(evolution_ids)}")

    # Output directory
    run_id = f"evolve_{size}_{int(time.time())}"
    output_dir = BCCSE_V5_ROOT / "runs" / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    evolution_manifest_path = _first_existing_manifest(
        "development_pool_ids.json",
        "partition/development_pool_ids.json",
        "stratified_evolution_set.json",
    )
    if evolution_manifest_path is None:
        raise FileNotFoundError("Registered development/evolution manifest disappeared")
    run_provenance = _build_run_provenance(
        stage="evolve",
        case_ids=evolution_ids,
        config=config,
        partition_path=partition_path,
        case_manifest_path=evolution_manifest_path,
        genome_path=(Path(args.initial_genome) if args.initial_genome else None),
        mutation_route=(None if args.no_llm else MUTATION_ROUTE),
    )
    with open(output_dir / "run_contract.json", "w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema_version": "agenthallu.bccse_v5_run_contract.v1",
                "run_id": run_id,
                "evolution_case_ids": evolution_ids,
                "evolution_case_count": len(evolution_ids),
                "evolution_case_hash": _case_set_hash(evolution_ids),
                "evolution_case_sequence_hash": _case_sequence_hash(evolution_ids),
                "development_case_count": development_count,
                "probe_case_count": probe_count,
                "final_holdout_count": final_holdout_count,
                "probe_interval": probe_interval,
                "probe_budget": probe_budget,
                "probe_trigger_threshold": int(
                    getattr(args, "probe_trigger_threshold", 0)
                ),
                "probe_manifest_path": str(probe_path) if probe_path else None,
                "probe_manifest_provenance": (
                    _path_provenance(probe_path) if probe_path else None
                ),
                "active_probe_case_hash": (
                    _case_set_hash(active_probe_ids) if active_probe_ids else None
                ),
                "probe_execution": "budgeted_direction_or_rollback_gate",
                "probe_eligible_for_opening_by_this_command": bool(active_probe_ids),
                "holdout_sealed": holdout_sealed,
                "evolution_probe_source_overlap": 0,
                "evolution_final_holdout_overlap": 0,
                # Legacy field retained for old run readers.
                "validation_case_count": probe_count,
                "evolution_validation_overlap": 0,
                "inner_universe_only": True,
                "outer_exposure": False,
                "execution_scope": "diagnostic_strategy_evolution",
                "formal_effectiveness_claim": False,
                "authorized_phase": str(config.get("authorized_phase") or ""),
                "D_execution_authorized": config.get("D_execution_authorized") is True,
                "supervision_mode": (
                    "gold_enhanced" if args.use_gold else "self_supervised"
                ),
                "fitness_check": True,
                "fitness_batch_size": args.fitness_batch_size,
                "fitness_use_gold": args.fitness_use_gold or args.use_gold,
                "commit_validation": "source_replay_plus_stable_canaries",
                "stable_canary_limit": args.canary_limit,
                "experience_memory_path": args.experience_memory,
                "initial_genome_path": args.initial_genome,
                "detector_config": str(V35_CONFIG_PATH),
                "mutation_model": (
                    None if args.no_llm else MUTATION_ROUTE["model"]
                ),
                "provenance": run_provenance,
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )

    # Run evolution
    result = run_online_evolution(
        evolution_case_ids=evolution_ids,
        records=records,
        config=config,
        output_dir=output_dir,
        census_root=CENSUS_ROOT,
        use_gold=args.use_gold,
        mutation_route=MUTATION_ROUTE if not args.no_llm else None,
        baseline_cache_root=None,
        module_cooldown=args.cooldown,
        max_total_edits=args.max_edits,
        convergence_window=args.convergence,
        fitness_check=not args.no_fitness_check,
        fitness_batch_size=args.fitness_batch_size,
        fitness_use_gold=args.fitness_use_gold or args.use_gold,
        experience_memory_path=(
            Path(args.experience_memory) if args.experience_memory else None
        ),
        canary_limit=args.canary_limit,
        initial_genome=(
            _load_genome_strict(Path(args.initial_genome))
            if args.initial_genome else None
        ),
        run_id=run_id,
        validation_probe_every=probe_interval,
        probe_ids=active_probe_ids,
        probe_limit=probe_budget,
        validation_probe_trigger_threshold=int(
            getattr(args, "probe_trigger_threshold", 0)
        ),
    )

    print(f"\nResults saved to: {output_dir}")
    return result


def cmd_evaluate(args: argparse.Namespace) -> None:
    """Evaluate evolved skill on a dataset."""
    return cmd_evaluate_concurrent(args)


def _case_ref(case_id: str) -> str:
    return "case_" + hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:20]


def _case_set_hash(case_ids: list[str]) -> str:
    payload = "\n".join(sorted(case_ids)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _case_sequence_hash(case_ids: list[str]) -> str:
    payload = "\n".join(case_ids).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _path_provenance(
    path: Path,
    *,
    include_suffixes: set[str] | None = None,
) -> dict[str, Any]:
    """Hash a file or deterministic directory manifest without inventing data."""
    resolved = path.resolve()
    entry: dict[str, Any] = {"path": str(resolved), "present": path.exists()}
    if not path.exists():
        entry.update({"status": "missing", "sha256": None})
        return entry
    try:
        if path.is_file():
            entry.update(
                {
                    "kind": "file",
                    "status": "complete",
                    "sha256": _file_sha256(path),
                    "size_bytes": path.stat().st_size,
                }
            )
            return entry

        files = [candidate for candidate in path.rglob("*") if candidate.is_file()]
        if include_suffixes is not None:
            files = [candidate for candidate in files if candidate.suffix in include_suffixes]
        manifest = [
            {
                "path": candidate.relative_to(path).as_posix(),
                "sha256": _file_sha256(candidate),
                "size_bytes": candidate.stat().st_size,
            }
            for candidate in sorted(files, key=lambda candidate: candidate.as_posix())
        ]
        entry.update(
            {
                "kind": "directory_manifest",
                "status": "complete" if manifest else "empty",
                "sha256": _canonical_sha256(manifest) if manifest else None,
                "file_count": len(manifest),
                "size_bytes": sum(item["size_bytes"] for item in manifest),
            }
        )
    except OSError as exc:
        entry.update(
            {
                "status": "unavailable",
                "sha256": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    return entry


def _case_records_provenance(data_root: Path, case_ids: list[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for case_id in case_ids:
        parts = case_id.split("/")
        if len(parts) != 2:
            missing.append(case_id)
            continue
        path = data_root / parts[0] / f"{parts[1]}.json"
        if not path.is_file():
            missing.append(case_id)
            continue
        rows.append(
            {
                "case_id": case_id,
                "sha256": _file_sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return {
        "root": str(data_root.resolve()),
        "status": "complete" if not missing and len(rows) == len(case_ids) else "incomplete",
        "case_count": len(rows),
        "case_sequence_sha256": _case_sequence_hash(case_ids),
        "content_manifest_sha256": _canonical_sha256(rows),
        "missing_case_ids": missing,
    }


def _provider_provenance(
    config: dict[str, Any],
    mutation_route: dict[str, Any] | None,
) -> dict[str, Any]:
    raw_routes = {
        key: value
        for key, value in config.items()
        if key.endswith("_route") and isinstance(value, dict)
    }
    sensitive_markers = ("api_key", "token", "secret", "password", "credential")

    def _redact(route: dict[str, Any] | None) -> dict[str, Any] | None:
        if route is None:
            return None
        return {
            key: (
                "<redacted-present>"
                if any(marker in key.lower() for marker in sensitive_markers)
                and value not in (None, "")
                else value
            )
            for key, value in route.items()
        }

    routes = {key: _redact(value) for key, value in raw_routes.items()}
    sanitized_mutation_route = _redact(mutation_route)
    payload = {
        "detector_routes": routes,
        "mutation_route": sanitized_mutation_route,
    }
    return {
        "status": "complete" if routes else "incomplete",
        "configuration_sha256": _canonical_sha256(payload),
        "detector_models": sorted(
            {str(value.get("model") or "") for value in raw_routes.values()}
        ),
        "mutation_model": (
            str(mutation_route.get("model") or "") if mutation_route else None
        ),
        "credential_values_recorded": False,
        "sensitive_fields_redacted_before_hashing": True,
    }


def _build_run_provenance(
    *,
    stage: str,
    case_ids: list[str],
    config: dict[str, Any],
    partition_path: Path,
    case_manifest_path: Path,
    genome_path: Path | None = None,
    genome_content_hash: str | None = None,
    mutation_route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sources: dict[str, Any] = {
        "code": _path_provenance(Path(__file__).resolve().parent, include_suffixes={".py"}),
        "detector_config": _path_provenance(V35_CONFIG_PATH),
        "partition": _path_provenance(partition_path),
        "case_manifest": _path_provenance(case_manifest_path),
        "case_records": _case_records_provenance(DATA_ROOT, case_ids),
        "case_sequence": {
            "status": "complete",
            "count": len(case_ids),
            "set_sha256": _case_set_hash(case_ids),
            "sequence_sha256": _case_sequence_hash(case_ids),
        },
        "provider": _provider_provenance(config, mutation_route),
        "baseline_caches": {
            "status": "complete",
            "entries": [
                {"role": role, **_path_provenance(path)}
                for role, path in (
                    (
                        "optional_consolidated",
                        BCCSE_V5_ROOT / "baseline_predictions_by_id.json",
                    ),
                    ("D", D_PREDICTIONS),
                    ("P0", P0_PREDICTIONS),
                )
            ],
        },
        "construct_census_cache": _path_provenance(CENSUS_ROOT),
    }
    required_cache_entries = sources["baseline_caches"]["entries"][1:]
    if not all(entry.get("status") == "complete" for entry in required_cache_entries):
        sources["baseline_caches"]["status"] = "incomplete"
        sources["baseline_caches"]["required_entries"] = ["D", "P0"]
    if genome_path is not None:
        sources["genome"] = {
            **_path_provenance(genome_path),
            "content_hash": genome_content_hash,
        }
    elif stage == "evaluation":
        sources["genome"] = {
            "status": "missing",
            "path": None,
            "sha256": None,
            "content_hash": genome_content_hash,
        }

    critical_names = [
        "code",
        "detector_config",
        "partition",
        "case_manifest",
        "case_records",
        "case_sequence",
        "provider",
        "baseline_caches",
        "construct_census_cache",
    ]
    if stage == "evaluation":
        critical_names.append("genome")
    missing = [
        name
        for name in critical_names
        if sources[name].get("status") != "complete"
    ]
    return {
        "schema_version": "agenthallu.run_provenance.v1",
        "stage": stage,
        "sources": sources,
        "reproducibility": {
            "status": "reproducible" if not missing else "not_reproducible",
            "critical_provenance_complete": not missing,
            "missing_or_incomplete": missing,
        },
    }


def _atomic_dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _load_genome_strict(path: Path):
    """Load a genome and verify its serialized content identity."""
    from .core.bccse.genome import (
        MODULE_IDS,
        SkillGenome,
        apply_edit,
        create_baseline_genome,
    )

    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    serialized_modules = payload.get("modules") or {}
    if set(serialized_modules) != set(MODULE_IDS):
        raise RuntimeError("Genome must contain exactly the eight registered modules")
    genome = SkillGenome.from_dict(payload)
    recorded_hash = str(payload.get("content_hash") or "")
    if recorded_hash and recorded_hash != genome.content_hash:
        raise RuntimeError(
            f"Genome content hash mismatch: recorded={recorded_hash}, "
            f"computed={genome.content_hash}"
        )
    if genome.generation != len(genome.edits_applied):
        raise RuntimeError(
            "Genome generation must equal the serialized edit lineage length"
        )
    replayed = create_baseline_genome()
    for edit in genome.edits_applied:
        if not edit.source_case_id:
            raise RuntimeError(f"Genome edit lacks source_case_id: {edit.edit_id}")
        replayed = apply_edit(replayed, edit)
    if replayed.content_hash != genome.content_hash:
        raise RuntimeError(
            "Genome modules do not match a valid replay of the serialized lineage"
        )
    return genome


def _normalize_cached_baseline(
    case_id: str,
    prediction: dict[str, Any],
) -> dict[str, Any]:
    """Validate one cached baseline row without making provider calls."""
    if _decision_authority_invalid(prediction):
        raise RuntimeError(f"Cached baseline decision authority invalid for {case_id}")
    try:
        state = _prediction_state(prediction)
    except ValueError as exc:
        raise RuntimeError(f"Invalid cached baseline state for {case_id}: {exc}") from exc
    if "gold_label" not in prediction or prediction["gold_label"] is None:
        raise RuntimeError(f"Cached baseline gold_label missing for {case_id}")
    if not isinstance(prediction["gold_label"], bool):
        raise RuntimeError(
            f"Cached baseline gold_label must be bool for {case_id}, got "
            f"{type(prediction['gold_label']).__name__}"
        )
    if prediction.get("runtime_valid") is False:
        raise RuntimeError(f"Cached baseline runtime_valid=false for {case_id}")
    normalized = dict(prediction)
    normalized["case_id"] = case_id
    normalized["case_ref"] = _case_ref(case_id)
    normalized["gold_label"] = bool(prediction["gold_label"])
    normalized["state"] = state
    return normalized


def _read_case_id_manifest(
    path: Path,
    *,
    key: str,
    role: str,
) -> tuple[list[str], dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Required independent {role} manifest missing: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    case_ids = [str(value) for value in manifest.get(key) or []]
    if not case_ids:
        raise RuntimeError(f"{role} manifest is empty")
    if len(case_ids) != len(set(case_ids)):
        raise RuntimeError(f"{role} manifest contains duplicate case IDs")
    for case_id in case_ids:
        parts = case_id.split("/")
        if (
            len(parts) != 2
            or not parts[0]
            or not parts[1]
            or any(value in {".", ".."} for value in parts)
        ):
            raise RuntimeError(f"{role} manifest has invalid case ID: {case_id!r}")
    return case_ids, manifest


def _resolve_outer_manifest_path() -> Path:
    candidates = [
        BCCSE_V5_ROOT / "outer_set_ids.json",
        BCCSE_V5_ROOT / "partition" / "outer_set_ids.json",
    ]
    existing = [path for path in candidates if path.exists()]
    if not existing:
        raise FileNotFoundError(
            "Formal Outer evaluation requires an explicit outer_set_ids.json; "
            "case_ref reverse discovery and dataset scanning are forbidden"
        )
    if len(existing) != 1:
        raise RuntimeError(
            "Outer manifest is ambiguous; exactly one registered "
            "outer_set_ids.json must exist"
        )
    return existing[0]


def _assert_outer_membership(
    case_ids: list[str],
    *,
    manifest: dict[str, Any],
    partition: dict[str, Any],
) -> None:
    manifest_role = str(manifest.get("role") or "").lower()
    if manifest_role not in {"outer", "final_holdout"}:
        raise RuntimeError(
            "Final manifest lacks explicit role='final_holdout'/'outer' evidence"
        )
    expected_refs = [_case_ref(case_id) for case_id in case_ids]
    declared_refs = [
        str(value)
        for value in (
            manifest.get("final_holdout_case_refs")
            or manifest.get("outer_case_refs")
            or []
        )
    ]
    if declared_refs != expected_refs:
        raise RuntimeError("Outer manifest case_ref evidence does not match its case IDs")
    assignments = [
        {"case_id": case_id, "case_ref": case_ref, "role": manifest_role}
        for case_id, case_ref in zip(case_ids, expected_refs)
    ]
    if manifest.get("role_assignments") != assignments:
        raise RuntimeError("Outer manifest lacks per-case Outer role assignments")
    if str(manifest.get("role_assignment_sha256") or "") != _canonical_sha256(
        assignments
    ):
        raise RuntimeError("Outer manifest role-assignment hash mismatch")
    if str(manifest.get("partition_payload_sha256") or "") != _canonical_sha256(
        partition
    ):
        raise RuntimeError("Outer manifest is not hash-bound to the loaded partition")
    partition_final_refs = [
        str(value)
        for value in (
            (partition.get("final_holdout") or {}).get("case_refs")
            or (partition.get("outer_sealed") or {}).get("case_refs")
            or []
        )
    ]
    if partition_final_refs:
        if partition_final_refs != expected_refs:
            raise RuntimeError(
                "Final manifest does not match the partition final_holdout"
            )
        development_refs = set(
            str(value)
            for value in (partition.get("development_pool") or {}).get("case_refs") or []
        )
        overlap = sorted(set(expected_refs).intersection(development_refs))
        if overlap:
            raise RuntimeError(
                "Development/final-holdout overlap detected: " + ", ".join(overlap)
            )
    else:
        # Legacy partitions registered Outer outside the Inner cache universe.
        inner_overlap = sorted(set(expected_refs).intersection(_load_inner_case_refs()))
        if inner_overlap:
            raise RuntimeError(
                "Outer manifest contains cases registered in the sealed Inner universe: "
                + ", ".join(inner_overlap)
            )


def _load_evaluation_ids(
    *,
    set_name: str,
    partition: dict[str, Any],
) -> tuple[list[str], str]:
    if set_name not in PROBE_SET_ALIASES | FINAL_HOLDOUT_SET_ALIASES:
        raise ValueError(f"Unknown evaluation set: {set_name!r}")

    final_role = set_name in FINAL_HOLDOUT_SET_ALIASES
    final_manifest: dict[str, Any] | None = None
    if final_role:
        if set_name == "outer":
            path = _resolve_outer_manifest_path()
        else:
            path = _first_existing_manifest(
                "final_holdout_ids.json",
                "partition/final_holdout_ids.json",
                "outer_set_ids.json",
                "partition/outer_set_ids.json",
            )
            if path is None:
                raise FileNotFoundError(
                    "Formal final-holdout evaluation requires an explicit manifest"
                )
        with open(path, "r", encoding="utf-8") as handle:
            final_manifest = json.load(handle)
        raw_ids = (
            final_manifest.get("final_holdout_case_ids")
            or final_manifest.get("outer_case_ids")
            or []
        )
        case_ids = [str(value) for value in raw_ids]
        if not case_ids or len(case_ids) != len(set(case_ids)):
            raise RuntimeError("Final-holdout manifest is empty or contains duplicates")
        source = str(path)
    elif set_name == "test":
        path = _first_existing_manifest("test_set_ids.json", "partition/test_set_ids.json")
        if path is not None:
            case_ids, _ = _read_case_id_manifest(path, key="test_case_ids", role="test")
            source = str(path)
        else:
            case_ids, path = _load_probe_ids_optional()
            if path is None:
                raise FileNotFoundError("No test or probe compatibility manifest exists")
            source = str(path)
    else:
        case_ids, path = _load_probe_ids_optional()
        if path is None:
            raise FileNotFoundError("Required periodic gold-probe manifest is missing")
        source = str(path)

    _assert_disjoint_from_evolution(case_ids, set_name=set_name)
    if final_role:
        legacy_test_path = _first_existing_manifest("test_set_ids.json")
        if legacy_test_path is not None:
            test_ids, _ = _read_case_id_manifest(
                legacy_test_path, key="test_case_ids", role="test"
            )
            test_overlap = sorted(set(test_ids).intersection(case_ids))
        else:
            test_overlap = []
        if test_overlap:
            raise RuntimeError(
                "Test/outer overlap detected; execution aborted: "
                + ", ".join(test_overlap)
            )
        _assert_outer_membership(
            case_ids,
            manifest=final_manifest or {},
            partition=partition,
        )
    else:
        _assert_inner_membership(case_ids, set_name=set_name)
    return case_ids, source


def cmd_evaluate_concurrent(args: argparse.Namespace) -> None:
    """Formally evaluate one genome after a fail-closed phase check."""
    _apply_project_root(getattr(args, "project_root", None))
    config = load_config()
    _assert_stage_authorized(config, stage=str(args.set))

    from .core.paca_tcl import layered_engine as v2
    from .core.paca_tcl import proof_engine as v35
    from .core.paca_tcl.skill_integration import (
        compile_genome_for_paca,
        make_skill_aware_provider,
    )
    from .core.paca_tcl.basic_runner import write_jsonl
    from .core.bccse.step_attributor import attribute_failure

    partition_path = BCCSE_V5_ROOT / "partition" / "data_partition_v5.json"
    if not partition_path.exists():
        raise FileNotFoundError(f"Frozen partition artifact missing: {partition_path}")
    with open(partition_path, "r", encoding="utf-8") as handle:
        partition = json.load(handle)

    if not args.genome:
        raise ValueError(
            "Evaluation requires an explicit --genome path; implicit latest-run "
            "selection is forbidden"
        )
    genome_path = Path(args.genome)
    if not genome_path.exists():
        raise FileNotFoundError(f"Genome not found: {genome_path}")
    genome = _load_genome_strict(genome_path)

    eval_ids, set_source = _load_evaluation_ids(
        set_name=args.set,
        partition=partition,
    )
    _assert_stage_authorized(
        config,
        stage=str(args.set),
        contains_d_cases=_contains_d_cases(eval_ids),
    )
    records = load_records(DATA_ROOT, allowed_case_ids=eval_ids)
    baselines = load_baseline_predictions()
    baseline_by_id: dict[str, dict[str, Any]] = {}
    missing_baseline: list[str] = []
    for case_id in eval_ids:
        prediction = baselines.get(case_id) or baselines.get(_case_ref(case_id))
        if prediction is None:
            missing_baseline.append(case_id)
        else:
            baseline_by_id[case_id] = _normalize_cached_baseline(
                case_id,
                prediction,
            )
    if missing_baseline:
        raise RuntimeError(
            "Cached baseline incomplete; baseline rerun is forbidden. Missing: "
            + ", ".join(missing_baseline)
        )

    workers = int(args.workers)
    if workers < 1 or workers > 20:
        raise ValueError("--workers must be between 1 and 20")
    output_dir = (
        BCCSE_V5_ROOT
        / "eval"
        / f"{args.set}_concurrent_{time.time_ns()}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    run_provenance = _build_run_provenance(
        stage="evaluation",
        case_ids=eval_ids,
        config=config,
        partition_path=partition_path,
        case_manifest_path=Path(set_source),
        genome_path=genome_path,
        genome_content_hash=genome.content_hash,
    )
    canonical_eval_set = (
        "final_holdout"
        if str(args.set) in FINAL_HOLDOUT_SET_ALIASES
        else "gold_probe"
    )
    is_outer_evaluation = canonical_eval_set == "final_holdout"
    holdout_sealed = bool(
        partition.get("holdout_sealed")
        if "holdout_sealed" in partition
        else (partition.get("final_holdout") or {}).get("sealed", True)
    )
    development_case_count = int(
        partition.get("development_case_count")
        or (partition.get("development_pool") or {}).get("size")
        or 0
    )
    probe_case_count = int(
        partition.get("probe_case_count")
        or (partition.get("gold_probe") or {}).get("size")
        or 0
    )
    final_holdout_count = int(
        partition.get("final_holdout_count")
        or (partition.get("final_holdout") or {}).get("size")
        or (partition.get("outer_sealed") or {}).get("size")
        or 0
    )
    final_evaluation_protocol = {
        "is_outer_final_evaluation": is_outer_evaluation,
        "canonical_evaluation_set": canonical_eval_set,
        "requested_set_alias": str(args.set),
        "development_case_count": development_case_count,
        "probe_case_count": probe_case_count,
        "final_holdout_count": final_holdout_count,
        "holdout_sealed": holdout_sealed,
        "probe_role": "online_direction_and_rollback_only",
        "outer_execution_authorized": (
            config.get("outer_execution_authorized") is True
        ),
        "outer_custody_verified": config.get("outer_custody_verified") is True,
        "D_execution_authorized": config.get("D_execution_authorized") is True,
        "authorized_phase": str(config.get("authorized_phase") or ""),
        "genome_access": "read_only",
        "mutation_calls_allowed": False,
        "experience_writeback_allowed": False,
        "champion_writeback_allowed": False,
        "semantics": (
            "role_isolated_final_holdout_read_only"
            if is_outer_evaluation
            else "development_gold_probe_not_generalization_evidence"
        ),
        "one_shot_enforcement": (
            "external_custody_audit_no_durable_cross-process_use_registry"
            if is_outer_evaluation
            else "not_applicable"
        ),
        "audit_limitation": (
            "Code enforces role isolation and hashes, not secrecy or one-shot "
            "custody. outer_custody_verified records an external governance "
            "attestation that must be audited independently."
            if is_outer_evaluation
            else None
        ),
        "outer_manifest_sha256": (
            run_provenance["sources"]["case_manifest"].get("sha256")
            if is_outer_evaluation
            else None
        ),
    }

    print(f"Evaluating genome: {genome.content_hash}")
    print(f"  Eval set: {args.set} ({len(eval_ids)} cases)")
    print(f"  Set source: {set_source}")
    print(f"  Set hash: {_case_set_hash(eval_ids)}")
    print(f"  Set sequence hash: {_case_sequence_hash(eval_ids)}")
    print("  Evolution overlap: 0 (asserted before provider calls)")
    print(f"  Baseline: cached only ({len(baseline_by_id)} rows)")
    print(f"  Provider patch: single global patch; workers={workers}")
    if is_outer_evaluation:
        print("  Outer semantics: role-isolated final evaluation")
        print("  Custody/one-shot enforcement: external audit")
        print("  Genome/experience/champion writeback: disabled")

    compiled = compile_genome_for_paca(genome)
    original_v35 = v35.provider_call
    original_v35_json = v35.json_object_provider_call
    original_v2 = v2.provider_call
    if original_v35 is not original_v2:
        raise RuntimeError(
            "PACA provider bindings diverged; refusing an ambiguous global patch"
        )
    wrapped = make_skill_aware_provider(
        original_v35,
        compiled=compiled,
        config=config,
    )
    wrapped_json = make_skill_aware_provider(
        original_v35_json,
        compiled=compiled,
        config=config,
    )
    v35.provider_call = wrapped
    v35.json_object_provider_call = wrapped_json
    v2.provider_call = wrapped

    def run_one(case_id: str) -> tuple[str, dict[str, Any], str | None]:
        try:
            result = v35.adjudicate_case(
                case_id=case_id,
                record=records[case_id],
                config=config,
                output_dir=output_dir / "with_skill" / case_id,
                census_root=CENSUS_ROOT,
            )
            if not isinstance(result, dict):
                raise RuntimeError("detector returned a non-object result")
            if result.get("runtime_valid") is False:
                raise RuntimeError("detector returned runtime_valid=false")
            if _decision_authority_invalid(result):
                raise RuntimeError("detector returned decision_authority=invalid")
            state = _prediction_state(result)
            compatibility_state = result.get("state")
            if compatibility_state not in (None, "", state):
                result["compatibility_state"] = compatibility_state
            result["state"] = state
            return case_id, result, None
        except Exception as exc:
            return (
                case_id,
                {
                    "case_id": case_id,
                    "case_ref": _case_ref(case_id),
                    "state": "error",
                    "runtime_valid": False,
                },
                f"{type(exc).__name__}: {exc}",
            )

    started = time.time()
    skill_by_id: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_one, case_id) for case_id in eval_ids]
            for completed, future in enumerate(as_completed(futures), 1):
                case_id, result, error = future.result()
                # Evaluation labels are always read from the frozen baseline
                # cache using the required bool conversion.
                result["gold_label"] = bool(
                    baseline_by_id[case_id]["gold_label"]
                )
                result["case_id"] = case_id
                result["case_ref"] = _case_ref(case_id)
                skill_by_id[case_id] = result
                if error:
                    errors[case_id] = error
                if completed % 5 == 0 or completed == len(futures):
                    print(
                        f"Progress: {completed}/{len(futures)} "
                        f"({time.time() - started:.1f}s)"
                    )
    finally:
        v35.provider_call = original_v35
        v35.json_object_provider_call = original_v35_json
        v2.provider_call = original_v2

    if errors:
        failure_path = output_dir / "evaluation_failures.json"
        _atomic_dump_json(
            failure_path,
            {
                "schema_version": "agenthallu.bccse_v5_evaluation_failure.v1",
                "eval_set": args.set,
                "eval_set_hash": _case_set_hash(eval_ids),
                "eval_set_sequence_hash": _case_sequence_hash(eval_ids),
                "failed_case_count": len(errors),
                "errors": errors,
                "metrics_computed": False,
                "provenance": run_provenance,
                "final_evaluation_protocol": final_evaluation_protocol,
            },
        )
        raise RuntimeError(
            f"Evaluation had {len(errors)} runtime failures; metrics were not "
            f"computed. Audit: {failure_path}"
        )

    skill_results = [skill_by_id[case_id] for case_id in eval_ids]
    baseline_results = [baseline_by_id[case_id] for case_id in eval_ids]
    metric_scope_ids = [str(row["case_id"]) for row in skill_results]
    if metric_scope_ids != eval_ids or len(set(metric_scope_ids)) != len(eval_ids):
        raise RuntimeError(
            "Metric scope mismatch: predictions are not exactly the declared "
            f"{args.set} manifest"
        )
    metrics = compute_metrics(
        skill_results,
        baseline_results,
        claim_context={
            "outer_evaluated": is_outer_evaluation,
            "final_holdout_evaluated": is_outer_evaluation,
            "evaluation_set": canonical_eval_set,
            "evaluation_set_alias": str(args.set),
            "holdout_sealed": holdout_sealed,
            "outer_custody_verified": config.get("outer_custody_verified") is True,
            "critical_provenance_complete": run_provenance["reproducibility"][
                "critical_provenance_complete"
            ],
        },
    )

    details: list[dict[str, Any]] = []
    trigger_counts: Counter[str] = Counter()
    crs_source_counts: Counter[str] = Counter()
    all_zero_vectors = 0
    for case_id in eval_ids:
        skill = skill_by_id[case_id]
        baseline = baseline_by_id[case_id]
        signal = attribute_failure(skill, use_gold=False)
        trigger_counts[signal.trigger_source.value] += 1
        crs_source_counts[signal.crs_source] += 1
        all_zero_vectors += int(
            bool(signal.crs_scores)
            and all(value == 0.0 for value in signal.crs_scores.values())
        )
        details.append(
            {
                "case_id": case_id,
                "case_ref": _case_ref(case_id),
                "gold": bool(baseline["gold_label"]),
                "baseline_state": _prediction_state(baseline),
                "skill_state": _prediction_state(skill),
                "error": errors.get(case_id),
                "self_supervised_attribution": signal.to_dict(),
            }
        )

    eval_output = {
        "schema_version": "agenthallu.bccse_v5_evaluation.v5",
        "eval_set": args.set,
        "canonical_eval_set": canonical_eval_set,
        "eval_set_source": set_source,
        "eval_set_hash": _case_set_hash(eval_ids),
        "eval_set_sequence_hash": _case_sequence_hash(eval_ids),
        "metric_scope": "exact_declared_eval_manifest_only",
        "metric_scope_case_ids": eval_ids,
        "metric_scope_case_count": len(eval_ids),
        "metric_scope_hash": _case_set_hash(eval_ids),
        "metric_scope_sequence_hash": _case_sequence_hash(eval_ids),
        "development_case_count": development_case_count,
        "probe_case_count": probe_case_count,
        "final_holdout_count": final_holdout_count,
        "holdout_sealed": holdout_sealed,
        "evolution_overlap_count": 0,
        "concurrency": workers,
        "provider_patch": "single_global_patch",
        "baseline_policy": "cached_only_no_provider_calls",
        "gold_conversion": "bool(pred['gold_label'])",
        "genome_path": str(genome_path),
        "genome_hash": genome.content_hash,
        "genome_generation": genome.generation,
        "n_cases": len(skill_results),
        "elapsed_seconds": time.time() - started,
        "errors": errors,
        "metrics": metrics,
        "claim_eligibility": metrics["claim_eligibility"],
        "provenance": run_provenance,
        "final_evaluation_protocol": final_evaluation_protocol,
        "self_supervision_audit": {
            "mode": "self_supervised",
            "trigger_counts": dict(trigger_counts),
            "crs_source_counts": dict(crs_source_counts),
            "all_zero_crs_vectors": all_zero_vectors,
        },
        "details": details,
    }
    _atomic_dump_json(output_dir / "eval_metrics.json", eval_output)
    write_jsonl(output_dir / "skill_predictions.jsonl", skill_results)

    print("EVALUATION RESULTS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print("SELF-SUPERVISION AUDIT")
    print(json.dumps(eval_output["self_supervision_audit"], ensure_ascii=False, indent=2))
    print(f"Saved to: {output_dir / 'eval_metrics.json'}")


def _exact_mcnemar(baseline_only_correct: int, skill_only_correct: int) -> dict[str, Any]:
    """Dependency-free, two-sided exact McNemar test on discordant pairs."""
    b = int(baseline_only_correct)
    c = int(skill_only_correct)
    if b < 0 or c < 0:
        raise ValueError("McNemar discordant counts must be non-negative")
    discordant = b + c
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(math.comb(discordant, k) for k in range(min(b, c) + 1))
        p_value = min(1.0, 2.0 * tail / (2 ** discordant))
    return {
        "method": "exact_two_sided_binomial_no_scipy",
        "baseline_only_correct": b,
        "skill_only_correct": c,
        "discordant_pairs": discordant,
        "p_value": p_value,
        "alpha": 0.05,
        "significant": p_value < 0.05,
        "direction": "skill_gain" if c > b else ("skill_loss" if b > c else "tie"),
    }


def _paired_net_gain_confidence_interval(
    *,
    baseline_only_correct: int,
    skill_only_correct: int,
    total_pairs: int,
) -> dict[str, Any]:
    """95% paired net-gain CI via Wilson discordant-pair transformation."""
    minimum_pairs = 20
    b = int(baseline_only_correct)
    c = int(skill_only_correct)
    total = int(total_pairs)
    result: dict[str, Any] = {
        "method": (
            "wilson_95_on_discordant_skill_win_probability_transformed_to_"
            "paired_net_correctness_gain_rate"
        ),
        "confidence_level": 0.95,
        "total_pair_denominator": total,
        "discordant_pair_denominator": b + c,
        "baseline_only_correct": b,
        "skill_only_correct": c,
        "minimum_total_pairs_for_claim": minimum_pairs,
        "assumption": (
            "conditions on the observed discordant-pair count; the interval "
            "is for (skill_only_correct-baseline_only_correct)/total_pairs"
        ),
    }
    if total < minimum_pairs:
        return {
            **result,
            "status": "insufficient_sample",
            "lower": None,
            "upper": None,
        }
    discordant = b + c
    if discordant == 0:
        return {**result, "status": "complete", "lower": 0.0, "upper": 0.0}

    z = 1.959963984540054
    win_rate = c / discordant
    denominator = 1.0 + (z * z / discordant)
    center = (win_rate + z * z / (2.0 * discordant)) / denominator
    half_width = (
        z
        * math.sqrt(
            win_rate * (1.0 - win_rate) / discordant
            + z * z / (4.0 * discordant * discordant)
        )
        / denominator
    )
    discordant_fraction = discordant / total
    lower = discordant_fraction * (2.0 * max(0.0, center - half_width) - 1.0)
    upper = discordant_fraction * (2.0 * min(1.0, center + half_width) - 1.0)
    return {
        **result,
        "status": "complete",
        "lower": max(-1.0, lower),
        "upper": min(1.0, upper),
    }


def _claim_eligibility(
    *,
    paired_test: dict[str, Any],
    paired_confidence_interval: dict[str, Any],
    only_abstention_migration: bool,
    claim_context: dict[str, Any] | None,
) -> dict[str, Any]:
    context = dict(claim_context or {})
    outer_evaluated = context.get("outer_evaluated") is True
    evaluation_set = str(context.get("evaluation_set") or "")
    final_holdout_evaluated = (
        context.get("final_holdout_evaluated") is True or outer_evaluated
    )
    outer_final_evaluation = (
        final_holdout_evaluated
        and evaluation_set in FINAL_HOLDOUT_SET_ALIASES
    )
    outer_custody_verified = context.get("outer_custody_verified") is True
    holdout_sealed = (
        context.get("holdout_sealed") is True
        or ("holdout_sealed" not in context and evaluation_set == "outer")
    )
    confidence_interval = (
        paired_confidence_interval if outer_final_evaluation else None
    )
    if outer_final_evaluation:
        context["confidence_interval"] = confidence_interval
    has_ci = (
        isinstance(confidence_interval, dict)
        and confidence_interval.get("status") == "complete"
        and confidence_interval.get("lower") is not None
        and confidence_interval.get("upper") is not None
    )
    ci_supports_gain = has_ci and float(confidence_interval["lower"]) > 0.0
    provenance_complete = context.get("critical_provenance_complete") is True
    paired_gain = (
        paired_test["significant"]
        and paired_test["direction"] == "skill_gain"
    )
    reasons: list[str] = []
    if only_abstention_migration:
        reasons.append("state_changes_are_abstention_migrations_only")
    if not paired_gain:
        reasons.append("no_significant_paired_correctness_gain")
    if not has_ci:
        reasons.append("confidence_interval_missing")
        if (
            outer_final_evaluation
            and confidence_interval.get("status") == "insufficient_sample"
        ):
            reasons.append("paired_ci_insufficient_sample")
    elif not ci_supports_gain:
        reasons.append("confidence_interval_does_not_support_positive_gain")
    if not outer_final_evaluation:
        reasons.append("final_holdout_evaluation_missing")
        # Stable legacy reason for existing dashboards and tests.
        reasons.append("outer_evaluation_missing")
    elif not holdout_sealed:
        reasons.append("final_holdout_not_sealed")
    elif not outer_custody_verified:
        reasons.append("outer_custody_unverified")
    if not provenance_complete:
        reasons.append("critical_provenance_incomplete")
    eligible = not reasons
    return {
        "eligible_for_detector_improvement_claim": eligible,
        "eligible_for_champion_label": eligible,
        "claim_status": "eligible" if eligible else "ineligible_research_observation",
        "blocking_reasons": reasons,
        "paired_significant_correctness_gain": paired_gain,
        "only_abstention_migration": only_abstention_migration,
        "confidence_interval_present": has_ci,
        "confidence_interval_supports_positive_gain": ci_supports_gain,
        "outer_evaluated": outer_evaluated,
        "final_holdout_evaluated": final_holdout_evaluated,
        "outer_final_evaluation": outer_final_evaluation,
        "holdout_sealed": holdout_sealed,
        "outer_custody_verified": outer_custody_verified,
        "critical_provenance_complete": provenance_complete,
        "context": context,
    }


def compute_metrics(
    skill_results: list[dict[str, Any]],
    baseline_results: list[dict[str, Any]],
    *,
    claim_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute occurrence-first three-state and transparent compatibility metrics."""
    if len(skill_results) != len(baseline_results):
        raise ValueError(
            "Metric inputs must have identical evaluation-set cardinality: "
            f"{len(skill_results)} != {len(baseline_results)}"
        )
    skill_ids = [str(row.get("case_id") or "") for row in skill_results]
    baseline_ids = [str(row.get("case_id") or "") for row in baseline_results]
    if any(skill_ids) or any(baseline_ids):
        if skill_ids != baseline_ids or any(not value for value in skill_ids):
            raise ValueError("Metric inputs are not aligned to the same case-ID order")

    def _gold(row: dict[str, Any]) -> bool:
        gold = row.get("gold_label")
        if not isinstance(gold, bool):
            raise ValueError("Metric gold_label must be bool before metric computation")
        return gold

    def _metrics_from_results(results: list[dict[str, Any]]) -> dict[str, Any]:
        three_state = {
            "gold_positive": {state: 0 for state in sorted(VALID_STATES)},
            "gold_negative": {state: 0 for state in sorted(VALID_STATES)},
        }
        for row in results:
            state = _prediction_state(row)
            gold_key = "gold_positive" if _gold(row) else "gold_negative"
            three_state[gold_key][state] += 1

        positive = three_state["gold_positive"]
        negative = three_state["gold_negative"]
        tp = positive["verified_error"]
        fp = negative["verified_error"]
        decisive_fn = positive["verified_clean"]
        decisive_tn = negative["verified_clean"]
        positive_unresolved = positive["unresolved"]
        negative_unresolved = negative["unresolved"]
        unresolved = positive_unresolved + negative_unresolved
        total = sum(sum(row.values()) for row in three_state.values())
        decisive = total - unresolved
        decisive_correct = tp + decisive_tn

        # This explicitly named compatibility view preserves historical binary
        # outputs.  Its TN includes negative unresolved by policy; the canonical
        # three-state and selective metrics above never call those cases clean.
        legacy_fn = decisive_fn + positive_unresolved
        legacy_tn = decisive_tn + negative_unresolved
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + legacy_fn) if tp + legacy_fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        fpr = fp / (fp + legacy_tn) if fp + legacy_tn else 0.0
        selective_accuracy = decisive_correct / decisive if decisive else 0.0
        compatibility = {
            "policy": "legacy_unresolved_collapsed_to_binary_negative",
            "tp": tp,
            "fp": fp,
            "tn": legacy_tn,
            "fn": legacy_fn,
            "f1": f1,
            "precision": precision,
            "recall": recall,
            "fpr": fpr,
            "warning": (
                "tn includes negative unresolved for compatibility only; use "
                "three_state_confusion for clean decisions"
            ),
        }
        return {
            "total": total,
            "three_state_confusion": three_state,
            "decisive_confusion": {
                "tp": tp,
                "fp": fp,
                "tn": decisive_tn,
                "fn": decisive_fn,
                "total_decisive": decisive,
            },
            "binary_compatibility": compatibility,
            "unresolved": unresolved,
            "unresolved_rate": unresolved / total if total else 0.0,
            "positive_unresolved": positive_unresolved,
            "negative_unresolved": negative_unresolved,
            "positive_unresolved_rate": (
                positive_unresolved / sum(positive.values()) if sum(positive.values()) else 0.0
            ),
            "negative_unresolved_rate": (
                negative_unresolved / sum(negative.values()) if sum(negative.values()) else 0.0
            ),
            "decision_coverage": decisive / total if total else 0.0,
            "selective_accuracy": selective_accuracy,
            "selective_risk": 1.0 - selective_accuracy if decisive else 0.0,
        }

    for skill, baseline in zip(skill_results, baseline_results):
        if _gold(skill) != _gold(baseline):
            raise ValueError("Paired metric rows disagree on gold_label")

    skill_m = _metrics_from_results(skill_results)
    base_m = _metrics_from_results(baseline_results)
    states = ("verified_error", "verified_clean", "unresolved")
    transition_table = {
        baseline_state: {skill_state: 0 for skill_state in states}
        for baseline_state in states
    }
    baseline_only_correct = skill_only_correct = 0
    changed_pairs: list[tuple[str, str]] = []
    for skill, baseline in zip(skill_results, baseline_results):
        skill_state = _prediction_state(skill)
        baseline_state = _prediction_state(baseline)
        transition_table[baseline_state][skill_state] += 1
        gold = _gold(skill)
        skill_correct = (
            skill_state == ("verified_error" if gold else "verified_clean")
        )
        baseline_correct = (
            baseline_state == ("verified_error" if gold else "verified_clean")
        )
        baseline_only_correct += int(baseline_correct and not skill_correct)
        skill_only_correct += int(skill_correct and not baseline_correct)
        if baseline_state != skill_state:
            changed_pairs.append((baseline_state, skill_state))

    paired_test = _exact_mcnemar(baseline_only_correct, skill_only_correct)
    only_abstention_migration = bool(changed_pairs) and all(
        "unresolved" in pair for pair in changed_pairs
    )
    total = len(skill_results)
    paired_ci = _paired_net_gain_confidence_interval(
        baseline_only_correct=baseline_only_correct,
        skill_only_correct=skill_only_correct,
        total_pairs=total,
    )
    claim_gate = _claim_eligibility(
        paired_test=paired_test,
        paired_confidence_interval=paired_ci,
        only_abstention_migration=only_abstention_migration,
        claim_context=claim_context,
    )
    skill_binary = skill_m["binary_compatibility"]
    base_binary = base_m["binary_compatibility"]

    return {
        "state_authority": (
            "occurrence_state_then_proof_or_ledger_occurrence_then_"
            "structured_incident_then_legacy_state"
        ),
        "skill_f1": skill_binary["f1"],
        "baseline_f1": base_binary["f1"],
        "delta_f1": skill_binary["f1"] - base_binary["f1"],
        "skill_precision": skill_binary["precision"],
        "baseline_precision": base_binary["precision"],
        "skill_recall": skill_binary["recall"],
        "baseline_recall": base_binary["recall"],
        "skill_fpr": skill_binary["fpr"],
        "baseline_fpr": base_binary["fpr"],
        "skill_unresolved_rate": skill_m["unresolved_rate"],
        "baseline_unresolved_rate": base_m["unresolved_rate"],
        "skill_decision_coverage": skill_m["decision_coverage"],
        "baseline_decision_coverage": base_m["decision_coverage"],
        "skill_selective_accuracy": skill_m["selective_accuracy"],
        "baseline_selective_accuracy": base_m["selective_accuracy"],
        "skill_selective_risk": skill_m["selective_risk"],
        "baseline_selective_risk": base_m["selective_risk"],
        "skill_positive_unresolved_rate": skill_m["positive_unresolved_rate"],
        "baseline_positive_unresolved_rate": base_m["positive_unresolved_rate"],
        "skill_negative_unresolved_rate": skill_m["negative_unresolved_rate"],
        "baseline_negative_unresolved_rate": base_m["negative_unresolved_rate"],
        "skill_three_state_confusion": skill_m["three_state_confusion"],
        "baseline_three_state_confusion": base_m["three_state_confusion"],
        "skill_decisive_confusion": skill_m["decisive_confusion"],
        "baseline_decisive_confusion": base_m["decisive_confusion"],
        "skill_binary_compatibility": skill_binary,
        "baseline_binary_compatibility": base_binary,
        # Stable aliases for older readers; the policy field prevents these
        # compatibility counts from being mistaken for three-state decisions.
        "skill_confusion": skill_binary,
        "baseline_confusion": base_binary,
        "paired_transition_table": transition_table,
        "paired_correctness": {
            "net_gain_count": skill_only_correct - baseline_only_correct,
            "net_gain_rate": (
                (skill_only_correct - baseline_only_correct) / total if total else 0.0
            ),
            "mcnemar": paired_test,
            "net_gain_confidence_interval": paired_ci,
        },
        "claim_eligibility": claim_gate,
    }


def cmd_full(args: argparse.Namespace) -> None:
    """Prevent a convenience command from mutating frozen scientific state."""
    raise RuntimeError(
        "The 'full' command is disabled for the frozen protocol. Run 'evolve' "
        "and then 'evaluate --genome <explicit genome>' as separate audited steps."
    )


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="BCCSE v5: Causal Process Reflection for Self-Supervised Skill Evolution"
    )
    subparsers = parser.add_subparsers(dest="command")

    # partition
    p_part = subparsers.add_parser("partition", help="Partition data")
    p_part.add_argument("--project-root", type=str, default=None, help="Override AGENT_HALLUCINATION_ROOT")
    p_part.add_argument("--d-predictions", default=str(D_PREDICTIONS))
    p_part.add_argument("--p0-predictions", default=str(P0_PREDICTIONS))
    p_part.add_argument("--seed", type=int, default=42)
    p_part.add_argument(
        "--probe-size",
        type=int,
        default=10,
        help="Fixed periodic gold probe sampled inside the development pool",
    )
    p_part.add_argument(
        "--holdout-size",
        type=int,
        default=None,
        help="Final holdout size; omitted preserves explicitly marked Outer rows",
    )
    p_part.add_argument("--unsealed-holdout", action="store_true", help=argparse.SUPPRESS)
    p_part.add_argument("--force-repartition", action="store_true")

    # evolve
    p_evolve = subparsers.add_parser("evolve", help="Run online evolution")
    p_evolve.add_argument("--project-root", type=str, default=None, help="Override AGENT_HALLUCINATION_ROOT")
    p_evolve.add_argument("--size", type=int, default=50, help="Evolution set size")
    p_evolve.add_argument(
        "--use-gold",
        action="store_true",
        help="Explicitly enable gold-enhanced attribution (default: self-supervised)",
    )
    p_evolve.add_argument(
        "--no-gold",
        action="store_false",
        dest="use_gold",
        help=argparse.SUPPRESS,
    )
    p_evolve.set_defaults(use_gold=False)
    p_evolve.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable mutation calls; triggered cases become audited no-edit skips",
    )
    p_evolve.add_argument("--cooldown", type=int, default=0)
    p_evolve.add_argument("--max-edits", type=int, default=200)
    p_evolve.add_argument("--convergence", type=int, default=20)
    p_evolve.add_argument(
        "--no-fitness-check",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    p_evolve.add_argument(
        "--fitness-batch-size",
        type=int,
        default=1,
        help=(
            "Must be 1: each edit is replayed and committed independently so "
            "fitness credit remains attributable."
        ),
    )
    p_evolve.add_argument(
        "--experience-memory",
        type=str,
        default=None,
        help="Prior htc_tes_experience_memory.json to load before evolution",
    )
    p_evolve.add_argument(
        "--initial-genome",
        type=str,
        default=None,
        help="Audited champion_genome.json to continue evolving",
    )
    p_evolve.add_argument(
        "--canary-limit",
        type=int,
        default=2,
        help="Small set of stable development cases used for per-edit regression replay",
    )
    p_evolve.add_argument(
        "--fitness-use-gold",
        action="store_true",
        help=(
            "Use evolution-set labels only inside fitness selection; labels "
            "remain hidden from self-supervised attribution and reflection"
        ),
    )
    p_evolve.add_argument(
        "--probe-interval",
        type=int,
        default=10,
        help="Accepted-edit interval between budgeted gold-probe gates",
    )
    p_evolve.add_argument(
        "--probe-budget",
        type=int,
        default=None,
        help="Maximum probe cases per gate (default: min(5, probe size))",
    )
    p_evolve.add_argument(
        "--probe-trigger-threshold",
        type=int,
        default=0,
        help="Also open a probe gate after this many pending accepted edits (0 disables)",
    )

    # evaluate
    p_eval = subparsers.add_parser(
        "evaluate",
        help="Formally evaluate a genome (requires D/effectiveness authorization)",
    )
    p_eval.add_argument("--project-root", type=str, default=None, help="Override AGENT_HALLUCINATION_ROOT")
    p_eval.add_argument(
        "--set",
        choices=["probe", "final_holdout", "validation", "test", "outer"],
        default="probe",
        help=(
            "probe/final_holdout roles; validation/test/outer remain compatibility "
            "aliases. Final holdout requires a hash-bound manifest and authorization"
        ),
    )
    p_eval.add_argument(
        "--genome",
        type=str,
        required=True,
        help=(
            "Explicit genome JSON path; claim eligibility is determined from "
            "paired evidence, CI, Outer evidence and provenance"
        ),
    )
    p_eval.add_argument(
        "--workers",
        type=int,
        default=20,
        help="Concurrent validation cases (1-20; default: 20)",
    )

    # full
    p_full = subparsers.add_parser(
        "full", help="Disabled: formal stages must be run separately"
    )
    p_full.add_argument("--d-predictions", default=str(D_PREDICTIONS))
    p_full.add_argument("--p0-predictions", default=str(P0_PREDICTIONS))
    p_full.add_argument("--seed", type=int, default=42)

    args = parser.parse_args()

    if args.command == "partition":
        cmd_partition(args)
    elif args.command == "evolve":
        cmd_evolve(args)
    elif args.command == "evaluate":
        cmd_evaluate_concurrent(args)
    elif args.command == "full":
        cmd_full(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
