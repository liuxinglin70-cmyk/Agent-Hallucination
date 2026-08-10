"""Concurrent 10-case single-record self-evolution batch driver.

Protocol discipline:
- Sample from datasets/raw/agenthallu with stratified hallucination polarity,
  excluding every case_id in experiments/protocols/used_case_ledger.txt, so
  each batch sees a fresh disjoint sample.
- Run each case as its own one-record ``run_evolution`` invocation
  concurrently; gold is used for polarity credit only (``--use-gold``
  ``--fitness-use-gold``), never for reflection content.
- Write per-case artifacts under experiments/results/<batch>/<case>/ and a
  batch_report.json summarizing trajectories for case-level review.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = REPO_ROOT / "datasets" / "raw" / "agenthallu" / "AgentHallu"
LEDGER_PATH = REPO_ROOT / "experiments" / "protocols" / "used_case_ledger.txt"
DETECTOR_CONFIG = REPO_ROOT / "src" / "sehd_v6" / "configs" / "paca_tcl_detector_v3_5.json"
RESULTS_ROOT = REPO_ROOT / "experiments" / "results"
LOGS_ROOT = REPO_ROOT / "experiments" / "logs"


def display_path(path: Path) -> str:
    """Render *path* relative to the repository when it lives inside it."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)

BATCH_SEED_SALT = "sehd-v6-small-batch"


def load_used_cases() -> set[str]:
    used: set[str] = set()
    for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            used.add(line)
    return used


def pool_index() -> dict[str, dict[str, object]]:
    index: dict[str, dict[str, object]] = {}
    for path in sorted(DATASET_ROOT.glob("*/*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        source = path.parent.name
        case_id = f"{source}/{path.stem}"
        index[case_id] = {
            "path": str(path),
            "hallucination": str(record.get("is_hallucination")).lower() == "true",
        }
    return index


def sample_batch(
    pool: dict[str, dict[str, object]],
    used: set[str],
    batch_index: int,
    size: int,
) -> list[str]:
    available = sorted(case_id for case_id in pool if case_id not in used)
    if len(available) < size:
        raise ValueError(f"pool exhausted: {len(available)} available < {size}")
    hallucinated = [c for c in available if pool[c]["hallucination"]]
    clean = [c for c in available if not pool[c]["hallucination"]]
    rng = random.Random(f"{BATCH_SEED_SALT}:{batch_index}")
    rng.shuffle(hallucinated)
    rng.shuffle(clean)
    n_hallu = min(len(hallucinated), size // 2 + size % 2)
    n_clean = size - n_hallu
    if len(clean) < n_clean:
        raise ValueError("not enough clean cases left for a stratified batch")
    return sorted(hallucinated[:n_hallu] + clean[:n_clean])


def load_manifest_case_ids(
    manifest_path: Path,
    pool: dict[str, dict[str, object]],
    expected_size: int | None,
) -> list[str]:
    """Load and validate the immutable case identity of a batch replay."""
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    case_ids = payload.get("case_ids") if isinstance(payload, dict) else payload
    if not isinstance(case_ids, list) or not all(
        isinstance(case_id, str) and case_id for case_id in case_ids
    ):
        raise ValueError("batch manifest must contain a non-empty case_ids list")
    if expected_size is not None and len(case_ids) != expected_size:
        raise ValueError(
            f"batch manifest has {len(case_ids)} cases; expected {expected_size}"
        )
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("batch manifest contains duplicate case_ids")
    missing = sorted(case_id for case_id in case_ids if case_id not in pool)
    if missing:
        raise ValueError(f"batch manifest contains unknown case_ids: {missing}")
    if isinstance(payload, dict) and "gold_polarity" in payload:
        polarity = payload["gold_polarity"]
        expected_polarity = {
            case_id: bool(pool[case_id]["hallucination"])
            for case_id in case_ids
        }
        if polarity != expected_polarity:
            raise ValueError("batch manifest gold_polarity does not match the dataset")
    return list(case_ids)


def write_batch_manifest(
    path: Path,
    *,
    batch_name: str,
    case_ids: list[str],
    pool: dict[str, dict[str, object]],
) -> None:
    """Seal the exact cases and gold polarity before any child process runs."""
    path.write_text(json.dumps({
        "schema_version": "sehd.small_batch_manifest.v1",
        "batch": batch_name,
        "case_ids": case_ids,
        "gold_polarity": {
            case_id: bool(pool[case_id]["hallucination"])
            for case_id in case_ids
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def write_record_mapping(
    path: Path,
    case_ids: list[str],
    pool: dict[str, dict[str, object]],
) -> None:
    """Materialize evaluator-only records without adding evolution sources."""
    path.write_text(json.dumps({
        case_id: json.loads(
            Path(str(pool[case_id]["path"])).read_text(encoding="utf-8")
        )
        for case_id in case_ids
    }, ensure_ascii=False), encoding="utf-8")


def run_case(case_id: str, record_path: str, batch_dir: Path, log_dir: Path,
             max_mutation_calls: int,
             reflection_candidates: int,
             provider_cache_mode: str,
             canary_records_path: Path | None,
             canary_limit: int) -> dict[str, object]:
    safe_name = case_id.replace("/", "__")
    case_dir = batch_dir / safe_name
    case_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    records_path = case_dir / "records.json"
    records_path.write_text(json.dumps(
        {"case_id": case_id,
         "record": json.loads(Path(record_path).read_text(encoding="utf-8"))},
        ensure_ascii=False,
    ), encoding="utf-8")
    cmd = [
        sys.executable, "-m", "src.sehd_v6.evolution",
        "--records", str(records_path),
        "--config", str(DETECTOR_CONFIG),
        "--output-dir", str(case_dir / "output"),
        "--use-gold", "--fitness-use-gold",
        "--max-mutation-calls", str(max_mutation_calls),
        "--reflection-candidates", str(reflection_candidates),
        "--run-id", f"{batch_dir.name}-{safe_name}",
    ]
    if canary_records_path is not None:
        cmd.extend([
            "--canary-records", str(canary_records_path),
            "--canary-limit", str(canary_limit),
        ])
    log_path = log_dir / f"{batch_dir.name}-{safe_name}.log"
    child_env = os.environ.copy()
    if provider_cache_mode == "fresh":
        child_env["PACA_GLOBAL_CACHE_ROOT"] = "off"
    started = time.time()
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, stdout=log,
                              stderr=subprocess.STDOUT, env=child_env)
    return {
        "case_id": case_id,
        "returncode": proc.returncode,
        "elapsed_seconds": round(time.time() - started, 1),
        "log": display_path(log_path),
        "output_dir": display_path(case_dir / "output"),
    }


def summarize_case(case_id: str, output_dir: Path) -> dict[str, object]:
    summary: dict[str, object] = {"case_id": case_id}
    result_path = output_dir / "evolution_result.json"
    if result_path.exists():
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        for key in ("triggered_cases", "edits_accepted", "edits_rejected",
                    "stable_cases", "halted", "halt_reason"):
            summary[key] = payload.get(key)
        audit = payload.get("evolution_audit") or {}
        summary["attributions"] = audit.get("attributions") or []
        summary["l2_policy_audit"] = audit.get("l2_policy_audit") or []
    genome_path = output_dir / "champion_genome.json"
    if genome_path.exists():
        genome = json.loads(genome_path.read_text(encoding="utf-8"))
        rules = genome.get("rules") or {}
        summary["genome_rule_count"] = sum(
            len(entries) for entries in rules.values()
        ) if isinstance(rules, dict) else None
    memory_path = output_dir / "htc_tes_experience_memory.json"
    if memory_path.exists():
        memory = json.loads(memory_path.read_text(encoding="utf-8"))
        # v2 schema stores per-type experience trajectories under
        # ``type_experience_memory.types`` and accepted-edit mechanism
        # evidence under ``failure_mechanisms``; there is no top-level
        # ``experiences`` list. Flatten both so the batch report reflects the
        # trajectories the framework actually persisted.
        type_memory = memory.get("type_experience_memory") or {}
        type_groups = type_memory.get("types") or {}
        experiences = [
            entry
            for entries in type_groups.values()
            for entry in (entries or [])
        ]
        summary["experience_count"] = len(experiences)
        summary["failure_mechanism_count"] = len(
            memory.get("failure_mechanisms") or []
        )
        summary["experiences"] = [
            {
                "hallucination_type": e.get("hallucination_type"),
                "direction": e.get("evolution_direction"),
                "success": e.get("detection_improved"),
                "feature_description": e.get("feature_description"),
                "module_targeted": e.get("module_targeted"),
            }
            for e in experiences
        ]
    else:
        summary["experience_count"] = None
    provider_ledger_path = output_dir / "provider_usage_ledger.json"
    if provider_ledger_path.exists():
        provider_ledger = json.loads(
            provider_ledger_path.read_text(encoding="utf-8")
        )
        provider_totals = provider_ledger.get("totals") or {}
        summary["provider_logical_calls"] = provider_totals.get("logical_calls")
        summary["provider_fresh_transport_calls"] = provider_totals.get(
            "fresh_transport_calls"
        )
        summary["provider_cache_hits"] = provider_totals.get("cache_hits")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-index", type=int, required=True)
    parser.add_argument("--size", type=int, default=10)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--max-mutation-calls", type=int, default=100)
    parser.add_argument("--reflection-candidates", type=int, default=4)
    parser.add_argument(
        "--canary-manifest",
        type=Path,
        help="sealed disjoint cases used only for non-regression replay",
    )
    parser.add_argument("--canary-limit", type=int, default=4)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="replay the exact case_ids from a sealed batch manifest",
    )
    parser.add_argument(
        "--provider-cache-mode",
        choices=("fresh", "global"),
        default="fresh",
        help=(
            "fresh disables the cross-run provider cache and requires a new "
            "batch output directory; global permits deliberate cache replay"
        ),
    )
    args = parser.parse_args()

    batch_name = f"batch{args.batch_index:02d}"
    batch_dir = RESULTS_ROOT / batch_name
    if args.provider_cache_mode == "fresh" and batch_dir.exists():
        raise FileExistsError(
            f"fresh provider run requires an absent output directory: {batch_dir}"
        )

    pool = pool_index()
    if args.manifest is not None:
        manifest_path = (
            args.manifest
            if args.manifest.is_absolute()
            else REPO_ROOT / args.manifest
        ).resolve()
        case_ids = load_manifest_case_ids(manifest_path, pool, args.size)
        manifest_source = str(manifest_path)
    else:
        case_ids = sample_batch(
            pool, load_used_cases(), args.batch_index, args.size,
        )
        manifest_source = "sampled_from_unused_pool"
    canary_ids: list[str] = []
    canary_manifest_source = "none"
    if args.canary_manifest is not None:
        canary_manifest_path = (
            args.canary_manifest
            if args.canary_manifest.is_absolute()
            else REPO_ROOT / args.canary_manifest
        ).resolve()
        canary_ids = load_manifest_case_ids(
            canary_manifest_path, pool, expected_size=None,
        )
        overlap = sorted(set(case_ids).intersection(canary_ids))
        if overlap:
            raise ValueError(
                "canary manifest overlaps the evolution batch: "
                + ", ".join(overlap)
            )
        canary_manifest_source = str(canary_manifest_path)
    batch_dir.mkdir(parents=True, exist_ok=True)
    write_batch_manifest(
        batch_dir / "batch_manifest.json",
        batch_name=batch_name,
        case_ids=case_ids,
        pool=pool,
    )
    canary_records_path: Path | None = None
    if canary_ids:
        canary_records_path = batch_dir / "canary_records.json"
        write_record_mapping(canary_records_path, canary_ids, pool)
    print(f"[{batch_name}] sampled {len(case_ids)} cases:")
    for cid in case_ids:
        print(f"  {cid}  hallucination={pool[cid]['hallucination']}")

    outcomes: list[dict[str, object]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool_exec:
        futures = {
            pool_exec.submit(
                run_case, cid, str(pool[cid]["path"]), batch_dir,
                LOGS_ROOT, args.max_mutation_calls, args.reflection_candidates,
                args.provider_cache_mode,
                canary_records_path, args.canary_limit,
            ): cid
            for cid in case_ids
        }
        for future in concurrent.futures.as_completed(futures):
            outcome = future.result()
            outcomes.append(outcome)
            print(f"[done] {outcome['case_id']} rc={outcome['returncode']} "
                  f"elapsed={outcome['elapsed_seconds']}s")

    report = {
        "batch": batch_name,
        "protocol": {
            "gold_labels": True,
            "single_record_experiments": True,
            "workers": args.workers,
            "max_mutation_calls": args.max_mutation_calls,
            "reflection_candidates": args.reflection_candidates,
            "provider_cache_mode": args.provider_cache_mode,
            "manifest_source": manifest_source,
            "canary_manifest_source": canary_manifest_source,
            "canary_case_ids": canary_ids,
            "canary_limit": args.canary_limit,
        },
        "case_ids": case_ids,
        "outcomes": sorted(outcomes, key=lambda o: str(o["case_id"])),
        "cases": [
            summarize_case(cid, batch_dir / cid.replace("/", "__") / "output")
            for cid in case_ids
        ],
    }
    report_path = batch_dir / "batch_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"batch report: {display_path(report_path)}")
    return 0 if all(outcome["returncode"] == 0 for outcome in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
