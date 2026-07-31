"""BCCSE v5 — Main entry point for online skill evolution experiments.

Usage:
    # Partition once. Repartitioning requires --force-repartition.
    python -m scripts.agenthallu.run_bccse_v5 partition

    # Run a registered evolution set.
    python -m scripts.agenthallu.run_bccse_v5 evolve --size 50

    # Evaluate an explicit champion. Evaluation defaults to 20 workers.
    python -m scripts.agenthallu.run_bccse_v5 evaluate \
        --set validation --genome /absolute/path/champion_genome.json

The legacy ``full`` command is deliberately disabled: formal partitioning,
evolution, and evaluation must be separate, reviewable steps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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


def _load_registered_evolution_ids() -> list[str]:
    path = BCCSE_V5_ROOT / "stratified_evolution_set.json"
    if not path.exists():
        raise FileNotFoundError(f"Registered evolution manifest missing: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    case_ids = [str(value) for value in manifest.get("evolution_case_ids") or []]
    if not case_ids:
        raise RuntimeError("Registered evolution manifest is empty")
    if len(case_ids) != len(set(case_ids)):
        raise RuntimeError("Registered evolution manifest contains duplicate case IDs")
    _assert_inner_membership(case_ids, set_name="evolution")
    return case_ids


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
        state = str(row.get("state") or "")
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
    )


def cmd_evolve(args: argparse.Namespace) -> None:
    """Run online evolution."""
    _apply_project_root(getattr(args, "project_root", None))
    from .core.bccse.evolution_runner import run_online_evolution
    if bool(args.no_fitness_check):
        raise RuntimeError(
            "Formal evolution requires the source/canary replay gate; "
            "--no-fitness-check is not permitted"
        )

    # The partition is retained as a provenance artifact, but the experiment
    # must use the separately registered evolution manifest.
    partition_path = BCCSE_V5_ROOT / "partition" / "data_partition_v5.json"
    if not partition_path.exists():
        raise FileNotFoundError(f"Frozen partition artifact missing: {partition_path}")

    registered_evolution_ids = _load_registered_evolution_ids()
    size = int(args.size)
    if size < 1 or size > len(registered_evolution_ids):
        raise ValueError(
            f"--size must be between 1 and {len(registered_evolution_ids)}"
        )
    evolution_ids = registered_evolution_ids[:size]

    validation_path = BCCSE_V5_ROOT / "validation_set_ids.json"
    if not validation_path.exists():
        raise FileNotFoundError(
            f"Cannot prove evolution/validation isolation: {validation_path}"
        )
    with open(validation_path, "r", encoding="utf-8") as handle:
        validation_manifest = json.load(handle)
    validation_ids = [
        str(value)
        for value in validation_manifest.get("validation_case_ids") or []
    ]
    if not validation_ids or len(validation_ids) != len(set(validation_ids)):
        raise RuntimeError("Validation manifest is empty or contains duplicate IDs")
    overlap = sorted(set(evolution_ids).intersection(validation_ids))
    if overlap:
        raise RuntimeError(
            "Evolution/validation overlap detected before provider calls: "
            + ", ".join(overlap)
        )
    _assert_inner_membership(validation_ids, set_name="validation")
    print(
        f"Evolution set: {len(evolution_ids)} registered inner cases "
        f"(validation overlap=0, Outer exposure=0)"
    )

    # Load records using discover_records
    dataset_root = DATA_ROOT
    records = load_records(dataset_root, allowed_case_ids=evolution_ids)
    config = load_config()
    # Cached baseline states are not correctness anchors. Canary cases are
    # drawn from observed stable evolution cases and evaluated as matched
    # champion-vs-shadow pairs at batch commit time.
    anchor_canaries: list[tuple[str, str]] = []
    print("  Fixed cached-state anchor canaries: disabled")

    missing_records = [case_id for case_id in evolution_ids if case_id not in records]
    if missing_records:
        raise RuntimeError(
            "Evolution records missing; partial evolution is forbidden: "
            + ", ".join(missing_records)
        )
    print(f"  Available for evolution: {len(evolution_ids)}/{len(evolution_ids)}")

    # Output directory
    run_id = f"evolve_{size}_{int(time.time())}"
    output_dir = BCCSE_V5_ROOT / "runs" / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    with open(output_dir / "run_contract.json", "w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema_version": "agenthallu.bccse_v5_run_contract.v1",
                "run_id": run_id,
                "evolution_case_ids": evolution_ids,
                "evolution_case_count": len(evolution_ids),
                "evolution_case_hash": _case_set_hash(evolution_ids),
                "evolution_case_sequence_hash": _case_sequence_hash(evolution_ids),
                "validation_case_count": len(validation_ids),
                "validation_case_hash": _case_set_hash(validation_ids),
                "validation_case_sequence_hash": _case_sequence_hash(validation_ids),
                "evolution_validation_overlap": 0,
                "inner_universe_only": True,
                "outer_exposure": False,
                "supervision_mode": (
                    "gold_enhanced" if args.use_gold else "self_supervised"
                ),
                "fitness_check": True,
                "canary_size": args.canary_size,
                "fitness_batch_size": args.fitness_batch_size,
                "fitness_use_gold": args.fitness_use_gold or args.use_gold,
                "anchor_canaries": [],
                "anchor_canary_source": "disabled_cached_state_is_not_truth",
                "canary_protocol": "matched_champion_vs_shadow_batch_audit",
                "detector_config": str(V35_CONFIG_PATH),
                "mutation_model": (
                    None if args.no_llm else MUTATION_ROUTE["model"]
                ),
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
        canary_size=args.canary_size,
        initial_canaries=anchor_canaries,
        fitness_batch_size=args.fitness_batch_size,
        fitness_use_gold=args.fitness_use_gold or args.use_gold,
        run_id=run_id,
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
    state = str(prediction.get("state") or "")
    if state not in VALID_STATES:
        raise RuntimeError(f"Invalid cached baseline state for {case_id}: {state!r}")
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
    return normalized


def _load_evaluation_ids(
    *,
    set_name: str,
    partition: dict[str, Any],
) -> tuple[list[str], str]:
    if set_name == "validation":
        validation_path = BCCSE_V5_ROOT / "validation_set_ids.json"
        if not validation_path.exists():
            raise FileNotFoundError(
                f"Required independent validation manifest missing: {validation_path}"
            )
        with open(validation_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        case_ids = [str(value) for value in manifest["validation_case_ids"]]
        source = str(validation_path)

    else:
        test_path = BCCSE_V5_ROOT / "test_set_ids.json"
        if not test_path.exists():
            raise FileNotFoundError(
                "Formal test evaluation requires an explicit test_set_ids.json "
                "manifest; case_ref reverse discovery is forbidden"
            )
        with open(test_path, "r", encoding="utf-8") as handle:
            test_manifest = json.load(handle)
        case_ids = [
            str(value)
            for value in test_manifest.get("test_case_ids") or []
        ]
        source = str(test_path)

    if len(case_ids) != len(set(case_ids)):
        raise RuntimeError(f"{set_name} manifest contains duplicate case IDs")
    if not case_ids:
        raise RuntimeError(f"{set_name} manifest is empty")
    _assert_disjoint_from_evolution(case_ids, set_name=set_name)
    if set_name != "validation":
        validation_path = BCCSE_V5_ROOT / "validation_set_ids.json"
        if not validation_path.exists():
            raise FileNotFoundError(
                f"Cannot prove test/validation isolation: {validation_path}"
            )
        with open(validation_path, "r", encoding="utf-8") as handle:
            validation_manifest = json.load(handle)
        validation_ids = {
            str(value)
            for value in validation_manifest.get("validation_case_ids") or []
        }
        overlap = sorted(validation_ids.intersection(case_ids))
        if overlap:
            raise RuntimeError(
                "Validation/test overlap detected; execution aborted: "
                + ", ".join(overlap)
            )
    _assert_inner_membership(case_ids, set_name=set_name)
    return case_ids, source


def cmd_evaluate_concurrent(args: argparse.Namespace) -> None:
    """Evaluate one genome with a single provider patch and true concurrency."""
    _apply_project_root(getattr(args, "project_root", None))
    from .core.paca_tcl import layered_engine as v2
    from .core.paca_tcl import proof_engine as v35
    from .core.paca_tcl.skill_integration import (
        compile_genome_for_paca,
        make_skill_aware_provider,
    )
    from .core.paca_tcl.basic_runner import write_jsonl
    from .core.bccse.step_attributor import attribute_failure

    partition_path = BCCSE_V5_ROOT / "partition" / "data_partition_v5.json"
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
    records = load_records(DATA_ROOT, allowed_case_ids=eval_ids)
    config = load_config()
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

    print(f"Evaluating genome: {genome.content_hash}")
    print(f"  Eval set: {args.set} ({len(eval_ids)} cases)")
    print(f"  Set source: {set_source}")
    print(f"  Set hash: {_case_set_hash(eval_ids)}")
    print(f"  Set sequence hash: {_case_sequence_hash(eval_ids)}")
    print("  Evolution overlap: 0 (asserted before provider calls)")
    print(f"  Baseline: cached only ({len(baseline_by_id)} rows)")
    print(f"  Provider patch: single global patch; workers={workers}")

    compiled = compile_genome_for_paca(genome)
    original_v35 = v35.provider_call
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
    v35.provider_call = wrapped
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
            state = str(result.get("state") or "")
            if state not in VALID_STATES:
                raise RuntimeError(f"detector returned invalid state: {state!r}")
            if result.get("runtime_valid") is False:
                raise RuntimeError("detector returned runtime_valid=false")
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
    metrics = compute_metrics(skill_results, baseline_results)

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
                "baseline_state": str(baseline.get("state") or ""),
                "skill_state": str(skill.get("state") or ""),
                "error": errors.get(case_id),
                "self_supervised_attribution": signal.to_dict(),
            }
        )

    eval_output = {
        "schema_version": "agenthallu.bccse_v5_evaluation.v3",
        "eval_set": args.set,
        "eval_set_source": set_source,
        "eval_set_hash": _case_set_hash(eval_ids),
        "eval_set_sequence_hash": _case_sequence_hash(eval_ids),
        "metric_scope": "exact_declared_eval_manifest_only",
        "metric_scope_case_ids": eval_ids,
        "metric_scope_case_count": len(eval_ids),
        "metric_scope_hash": _case_set_hash(eval_ids),
        "metric_scope_sequence_hash": _case_sequence_hash(eval_ids),
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


def compute_metrics(
    skill_results: list[dict[str, Any]],
    baseline_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute evaluation metrics comparing skill vs baseline."""
    if len(skill_results) != len(baseline_results):
        raise ValueError(
            "Metric inputs must have identical validation-set cardinality: "
            f"{len(skill_results)} != {len(baseline_results)}"
        )
    skill_ids = [str(row.get("case_id") or "") for row in skill_results]
    baseline_ids = [str(row.get("case_id") or "") for row in baseline_results]
    if any(skill_ids) or any(baseline_ids):
        if skill_ids != baseline_ids or any(not value for value in skill_ids):
            raise ValueError("Metric inputs are not aligned to the same case-ID order")

    def _metrics_from_results(results: list[dict[str, Any]]) -> dict[str, float]:
        tp = fp = tn = fn = 0
        unresolved = 0
        for row in results:
            state = str(row.get("state") or "")
            if state not in VALID_STATES:
                raise ValueError(f"Invalid prediction state in metric input: {state!r}")
            gold = row.get("gold_label")
            if gold is None:
                raise ValueError("Metric input contains a row without gold_label")
            if not isinstance(gold, bool):
                raise ValueError(
                    "Metric gold_label must be bool before metric computation"
                )
            gold = bool(gold)
            if state == "verified_error":
                if gold:
                    tp += 1
                else:
                    fp += 1
            elif state == "verified_clean":
                if gold:
                    fn += 1
                else:
                    tn += 1
            else:  # unresolved
                unresolved += 1
                # Count unresolved as negative for F1 calculation
                if gold:
                    fn += 1
                else:
                    tn += 1

        total = tp + fp + tn + fn
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
        unresolved_rate = unresolved / total if total > 0 else 0.0

        return {
            "f1": f1,
            "precision": precision,
            "recall": recall,
            "fpr": fpr,
            "unresolved_rate": unresolved_rate,
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "unresolved": unresolved,
            "total": total,
        }

    skill_m = _metrics_from_results(skill_results)
    base_m = _metrics_from_results(baseline_results)

    return {
        "skill_f1": skill_m["f1"],
        "baseline_f1": base_m["f1"],
        "delta_f1": skill_m["f1"] - base_m["f1"],
        "skill_precision": skill_m["precision"],
        "baseline_precision": base_m["precision"],
        "skill_recall": skill_m["recall"],
        "baseline_recall": base_m["recall"],
        "skill_fpr": skill_m["fpr"],
        "baseline_fpr": base_m["fpr"],
        "skill_unresolved_rate": skill_m["unresolved_rate"],
        "baseline_unresolved_rate": base_m["unresolved_rate"],
        "skill_confusion": skill_m,
        "baseline_confusion": base_m,
    }


def cmd_full(args: argparse.Namespace) -> None:
    """Prevent a convenience command from mutating frozen scientific state."""
    raise RuntimeError(
        "The 'full' command is disabled for the frozen protocol. Run 'evolve' "
        "and then 'evaluate --genome <explicit champion>' as separate audited steps."
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
    p_evolve.add_argument("--canary-size", type=int, default=3)
    p_evolve.add_argument(
        "--fitness-batch-size",
        type=int,
        default=1,
        help=(
            "Locally useful shadow edits per commit/rollback batch (1-10). "
            "Default 1 (incremental commit): each edit validated and committed "
            "independently, avoiding same-module edit interference."
        ),
    )
    p_evolve.add_argument(
        "--fitness-use-gold",
        action="store_true",
        help=(
            "Use evolution-set labels only inside fitness selection; labels "
            "remain hidden from self-supervised attribution and reflection"
        ),
    )

    # evaluate
    p_eval = subparsers.add_parser("evaluate", help="Evaluate evolved skill")
    p_eval.add_argument("--project-root", type=str, default=None, help="Override AGENT_HALLUCINATION_ROOT")
    p_eval.add_argument("--set", choices=["validation", "test"], default="validation")
    p_eval.add_argument(
        "--genome",
        type=str,
        required=True,
        help="Explicit path to the champion genome JSON",
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
