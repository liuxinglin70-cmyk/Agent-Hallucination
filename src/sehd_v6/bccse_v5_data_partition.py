"""BCCSE v5 — Data partition for evolution experiments.

Partitions the 587 cases (P0=64 + D=523) into:
- Evolution pool: 200 cases (main experiment uses first 50)
- Quick validation set: 50 cases (for iterative evaluation)
- Full test set: remaining ~337 cases (final evaluation)
- Outer 106: sealed (never used)

Design principles:
- Stratified by detection state (verified_error / verified_clean / unresolved)
- Stratified by gold label (positive / negative)
- Quick validation set enriched with "informative" cases (unresolved + FP)
- Reproducible via fixed random seed
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


def load_predictions(path: Path) -> list[dict[str, Any]]:
    """Load predictions from JSONL file."""
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def partition_data(
    *,
    d_predictions_path: Path,
    p0_predictions_path: Path | None = None,
    output_dir: Path,
    seed: int = 42,
    evolution_pool_size: int = 200,
    validation_size: int = 50,
    main_evolution_size: int = 50,
) -> dict[str, Any]:
    """Partition cases into evolution pool, validation set, and test set.

    Strategy:
    1. Load all predictions (D=523 + P0=64 = 587)
    2. Exclude Outer 106 (identified by split field or separate list)
    3. Stratify by (state, gold_label)
    4. Sample validation set first (enriched with unresolved + FP)
    5. Sample evolution pool from remaining
    6. Rest becomes test set

    Returns:
        Dictionary with partition info and case ID lists.
    """
    rng = random.Random(seed)

    # Load predictions
    all_rows = load_predictions(d_predictions_path)
    if p0_predictions_path is not None and p0_predictions_path.exists():
        all_rows.extend(load_predictions(p0_predictions_path))

    print(f"Loaded {len(all_rows)} total predictions")

    # Index by case_ref
    by_ref: dict[str, dict[str, Any]] = {}
    for row in all_rows:
        ref = str(row.get("case_ref") or row.get("case_id") or "")
        if ref:
            by_ref[ref] = row

    # Filter out Outer cases (if marked)
    inner_cases: list[dict[str, Any]] = []
    outer_cases: list[dict[str, Any]] = []
    for ref, row in by_ref.items():
        split = str(row.get("split") or row.get("partition") or "inner")
        if split.lower() in ("outer", "test_sealed"):
            outer_cases.append(row)
        else:
            inner_cases.append(row)

    # If no explicit outer marking, use all as inner
    if not outer_cases:
        inner_cases = list(by_ref.values())
        outer_cases = []

    print(f"Inner: {len(inner_cases)}, Outer: {len(outer_cases)}")

    # Stratify inner cases
    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in inner_cases:
        state = str(row.get("state") or "unknown")
        gold = row.get("gold_label")
        if gold is None:
            gold_str = "unknown"
        else:
            gold_str = "positive" if gold else "negative"
        strata[f"{state}_{gold_str}"].append(row)

    print("\nStrata distribution:")
    for key, cases in sorted(strata.items()):
        print(f"  {key}: {len(cases)}")

    # --- Sample validation set (enriched with informative cases) ---
    validation_refs: list[str] = []
    validation_target = {
        "unresolved_negative": 15,  # unresolved cases (gold=neg)
        "unresolved_positive": 10,  # unresolved cases (gold=pos)
        "verified_error_negative": 10,  # FP cases
        "verified_error_positive": 10,  # TP cases
        "verified_clean_negative": 3,  # TN cases
        "verified_clean_positive": 2,  # FN cases
    }

    used_refs: set[str] = set()
    for stratum_key, target_count in validation_target.items():
        available = [
            r for r in strata.get(stratum_key, [])
            if str(r.get("case_ref") or r.get("case_id")) not in used_refs
        ]
        rng.shuffle(available)
        selected = available[:target_count]
        for row in selected:
            ref = str(row.get("case_ref") or row.get("case_id"))
            validation_refs.append(ref)
            used_refs.add(ref)

    # Fill remaining validation slots if needed
    if len(validation_refs) < validation_size:
        remaining = [
            r for r in inner_cases
            if str(r.get("case_ref") or r.get("case_id")) not in used_refs
        ]
        rng.shuffle(remaining)
        for row in remaining:
            if len(validation_refs) >= validation_size:
                break
            ref = str(row.get("case_ref") or row.get("case_id"))
            validation_refs.append(ref)
            used_refs.add(ref)

    # --- Sample evolution pool ---
    evolution_candidates = [
        str(r.get("case_ref") or r.get("case_id"))
        for r in inner_cases
        if str(r.get("case_ref") or r.get("case_id")) not in used_refs
    ]
    rng.shuffle(evolution_candidates)
    evolution_pool_refs = evolution_candidates[:evolution_pool_size]
    used_refs.update(evolution_pool_refs)

    # --- Remaining = test set ---
    test_refs = [
        str(r.get("case_ref") or r.get("case_id"))
        for r in inner_cases
        if str(r.get("case_ref") or r.get("case_id")) not in used_refs
    ]

    # Main evolution set = first N from pool
    main_evolution_refs = evolution_pool_refs[:main_evolution_size]

    # --- Summary ---
    partition = {
        "schema_version": "agenthallu.bccse_v5_data_partition.v1",
        "seed": seed,
        "total_inner": len(inner_cases),
        "total_outer": len(outer_cases),
        "validation_set": {
            "size": len(validation_refs),
            "case_refs": validation_refs,
        },
        "evolution_pool": {
            "size": len(evolution_pool_refs),
            "case_refs": evolution_pool_refs,
            "main_experiment_size": main_evolution_size,
            "main_experiment_refs": main_evolution_refs,
            "scaling_sizes": [10, 20, 50, 100, 200],
        },
        "test_set": {
            "size": len(test_refs),
            "case_refs": test_refs,
        },
        "outer_sealed": {
            "size": len(outer_cases),
            "case_refs": [
                str(r.get("case_ref") or r.get("case_id")) for r in outer_cases
            ],
        },
    }

    # Save
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "data_partition_v5.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(partition, f, ensure_ascii=False, indent=2)

    print(f"\nPartition saved to: {out_path}")
    print(f"  Validation: {len(validation_refs)} cases")
    print(f"  Evolution pool: {len(evolution_pool_refs)} cases (main: {main_evolution_size})")
    print(f"  Test set: {len(test_refs)} cases")
    print(f"  Outer sealed: {len(outer_cases)} cases")

    return partition


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="BCCSE v5 data partition")
    parser.add_argument(
        "--d-predictions",
        type=Path,
        default=Path("/root/autodl-tmp/agent_hallucination/d_baseline_v3_5/round1/predictions.jsonl"),
    )
    parser.add_argument(
        "--p0-predictions",
        type=Path,
        default=Path("/root/autodl-tmp/agent_hallucination/paca_tcl_v3_5_p0_20260723/round1/predictions.jsonl"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/root/autodl-tmp/agent_hallucination/bccse_v5/partition"),
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    partition_data(
        d_predictions_path=args.d_predictions,
        p0_predictions_path=args.p0_predictions,
        output_dir=args.output_dir,
        seed=args.seed,
    )
