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
import hashlib
import random
from collections import Counter, defaultdict
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


def _case_ref(case_id: str) -> str:
    return "case_" + hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:20]


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
    2. Require an explicit Inner/Outer role on every row
    3. Stratify by (state, gold_label)
    4. Sample validation set first (enriched with unresolved + FP)
    5. Sample evolution pool from remaining
    6. Rest becomes test set

    Returns:
        Dictionary with partition info and case ID lists.
    """
    rng = random.Random(seed)

    # Load predictions
    d_rows = [
        {**row, "_partition_input_role": "D"}
        for row in load_predictions(d_predictions_path)
    ]
    all_rows = list(d_rows)
    p0_rows: list[dict[str, Any]] = []
    if p0_predictions_path is not None and p0_predictions_path.exists():
        p0_rows = [
            {**row, "_partition_input_role": "P0"}
            for row in load_predictions(p0_predictions_path)
        ]
        all_rows.extend(p0_rows)

    print(f"Loaded {len(all_rows)} total predictions")

    # Index by case_ref
    by_ref: dict[str, dict[str, Any]] = {}
    for row in all_rows:
        case_id = str(row.get("case_id") or "")
        ref = str(row.get("case_ref") or (_case_ref(case_id) if case_id else ""))
        if not ref:
            raise RuntimeError("Partition input contains a row without case_ref/case_id")
        if ref in by_ref:
            raise RuntimeError(f"Duplicate case reference across partition inputs: {ref}")
        row["case_ref"] = ref
        by_ref[ref] = row

    # Filter out Outer cases (if marked)
    inner_cases: list[dict[str, Any]] = []
    outer_cases: list[dict[str, Any]] = []
    unmarked_cases: list[str] = []
    for ref, row in by_ref.items():
        raw_split = row.get("split") or row.get("partition")
        if raw_split in (None, ""):
            unmarked_cases.append(ref)
            continue
        split = str(raw_split).lower()
        if split in ("outer", "test_sealed", "outer_sealed"):
            outer_cases.append(row)
        elif split in ("inner", "development", "d", "p0", "train"):
            inner_cases.append(row)
        else:
            raise RuntimeError(f"Unknown partition role {raw_split!r} for {ref}")

    if unmarked_cases:
        raise RuntimeError(
            "Every partition row requires an explicit Inner/Outer role; "
            "unmarked rows cannot be silently treated as Inner: "
            + ", ".join(unmarked_cases[:20])
        )
    if not outer_cases:
        raise RuntimeError(
            "No explicit Outer marker is present in the partition inputs; "
            "refusing to silently classify the entire cache as Inner"
        )

    outer_assignments: list[dict[str, str]] = []
    for row in outer_cases:
        case_id = str(row.get("case_id") or "")
        if not case_id or len(case_id.split("/")) != 2:
            raise RuntimeError(
                "An Outer row lacks an explicit loadable case_id; reverse "
                "discovery from case_ref is forbidden"
            )
        expected_ref = _case_ref(case_id)
        if str(row.get("case_ref") or "") != expected_ref:
            raise RuntimeError(
                f"Outer case_ref does not match its explicit case_id: {case_id}"
            )
        outer_assignments.append(
            {"case_id": case_id, "case_ref": expected_ref, "role": "outer"}
        )
    outer_case_ids = [item["case_id"] for item in outer_assignments]
    if len(outer_case_ids) != len(set(outer_case_ids)):
        raise RuntimeError("Outer role assignments contain duplicate case IDs")

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
        remaining_slots = validation_size - len(validation_refs)
        if remaining_slots <= 0:
            break
        available = [
            r for r in strata.get(stratum_key, [])
            if str(r.get("case_ref") or r.get("case_id")) not in used_refs
        ]
        rng.shuffle(available)
        selected = available[:min(target_count, remaining_slots)]
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
    validation_set = set(validation_refs)
    evolution_set = set(evolution_pool_refs)
    test_set = set(test_refs)
    outer_set = {
        str(row.get("case_ref") or row.get("case_id")) for row in outer_cases
    }
    role_sets = {
        "validation": validation_set,
        "evolution_pool": evolution_set,
        "test": test_set,
        "outer": outer_set,
    }
    overlaps: dict[str, list[str]] = {}
    role_names = list(role_sets)
    for index, left in enumerate(role_names):
        for right in role_names[index + 1:]:
            overlap = sorted(role_sets[left].intersection(role_sets[right]))
            if overlap:
                overlaps[f"{left}__{right}"] = overlap
    inner_refs = {
        str(row.get("case_ref") or row.get("case_id")) for row in inner_cases
    }
    assigned_inner = validation_set | evolution_set | test_set
    missing_inner = sorted(inner_refs - assigned_inner)
    unexpected_inner = sorted(assigned_inner - inner_refs)
    if overlaps or missing_inner or unexpected_inner:
        raise RuntimeError(
            "Partition mutual-exclusion/coverage invariant failed: "
            f"overlaps={overlaps}, missing_inner={missing_inner}, "
            f"unexpected_inner={unexpected_inner}"
        )

    input_role_counts: dict[str, dict[str, int]] = {}
    for role in ("D", "P0"):
        role_rows = [
            row for row in all_rows if row["_partition_input_role"] == role
        ]
        split_counts: Counter[str] = Counter()
        for row in role_rows:
            ref = str(row.get("case_ref") or row.get("case_id"))
            split_counts["outer" if ref in outer_set else "inner"] += 1
        input_role_counts[role] = {
            "total": len(role_rows),
            "inner": split_counts["inner"],
            "outer": split_counts["outer"],
        }

    input_provenance = {
        "D": {
            "path": str(d_predictions_path.resolve()),
            "sha256": _file_sha256(d_predictions_path),
            "row_count": len(d_rows),
        },
        "P0": (
            {
                "path": str(p0_predictions_path.resolve()),
                "sha256": _file_sha256(p0_predictions_path),
                "row_count": len(p0_rows),
            }
            if p0_predictions_path is not None and p0_predictions_path.exists()
            else {"path": None, "sha256": None, "row_count": 0, "status": "not_supplied"}
        ),
    }
    partition = {
        "schema_version": "agenthallu.bccse_v5_data_partition.v2",
        "seed": seed,
        "total_inner": len(inner_cases),
        "total_outer": len(outer_cases),
        "input_provenance": input_provenance,
        "input_role_counts": input_role_counts,
        "outer_role_evidence": {
            "explicit_outer_marker_present": True,
            "all_rows_have_explicit_roles": True,
            "unmarked_row_count": 0,
            "outer_role_assignment_sha256": _canonical_sha256(outer_assignments),
        },
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
        "role_counts": {
            "validation": len(validation_set),
            "evolution_pool": len(evolution_set),
            "test": len(test_set),
            "outer": len(outer_set),
        },
        "role_sequence_hashes": {
            "validation": hashlib.sha256("\n".join(validation_refs).encode("utf-8")).hexdigest(),
            "evolution_pool": hashlib.sha256("\n".join(evolution_pool_refs).encode("utf-8")).hexdigest(),
            "test": hashlib.sha256("\n".join(test_refs).encode("utf-8")).hexdigest(),
            "outer": hashlib.sha256("\n".join(sorted(outer_set)).encode("utf-8")).hexdigest(),
        },
        "mutual_exclusion": {
            "valid": True,
            "pairwise_overlap_counts": {
                f"{left}__{right}": len(role_sets[left].intersection(role_sets[right]))
                for index, left in enumerate(role_names)
                for right in role_names[index + 1:]
            },
            "inner_coverage_complete": True,
            "inner_assigned_count": len(assigned_inner),
        },
    }

    # Save
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "data_partition_v5.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(partition, f, ensure_ascii=False, indent=2)

    outer_manifest = {
        "schema_version": "agenthallu.outer_set_manifest.v1",
        "role": "outer",
        "outer_case_ids": outer_case_ids,
        "outer_case_refs": [item["case_ref"] for item in outer_assignments],
        "role_assignments": outer_assignments,
        "role_assignment_sha256": _canonical_sha256(outer_assignments),
        "partition_payload_sha256": _canonical_sha256(partition),
        "partition_file_sha256": _file_sha256(out_path),
        "input_hashes": {
            role: value.get("sha256") for role, value in input_provenance.items()
        },
        "evaluation_semantics": "role_isolated_final_read_only",
        "custody_and_one_shot_enforcement": "external_governance_required",
        "reverse_discovery_used": False,
    }
    outer_manifest_path = output_dir / "outer_set_ids.json"
    with open(outer_manifest_path, "w", encoding="utf-8") as handle:
        json.dump(outer_manifest, handle, ensure_ascii=False, indent=2)

    print(f"\nPartition saved to: {out_path}")
    print(f"  Validation: {len(validation_refs)} cases")
    print(f"  Evolution pool: {len(evolution_pool_refs)} cases (main: {main_evolution_size})")
    print(f"  Test set: {len(test_refs)} cases")
    print(f"  Outer sealed: {len(outer_cases)} cases")
    print(f"  Outer ID manifest: {outer_manifest_path}")

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
