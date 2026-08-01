"""BCCSE v5 data protocol: development pool, gold probe, final holdout.

Most non-holdout cases remain reusable development material.  A fixed, small
gold probe is sampled *inside* that development pool for budgeted directional
checks.  The final holdout is disjoint, sealed, and read-only.  Legacy
validation/test/outer fields are emitted or accepted as compatibility aliases;
they no longer imply that three separate forbidden development sets must exist.
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
    evolution_pool_size: int | None = None,
    validation_size: int = 50,
    main_evolution_size: int = 50,
    probe_size: int | None = None,
    holdout_size: int | None = None,
    holdout_sealed: bool = True,
) -> dict[str, Any]:
    """Partition cases into development, periodic probe, and final holdout.

    ``validation_size`` and ``evolution_pool_size`` remain supported for old
    callers.  ``probe_size`` supersedes ``validation_size``.  When
    ``holdout_size`` is omitted, at least one explicitly marked Outer/final
    row is required (the legacy fail-closed behavior).  When it is supplied,
    explicit holdout rows are preserved and any shortfall is deterministically
    sampled before the probe.

    Returns:
        Dictionary with partition info and case ID lists.
    """
    rng = random.Random(seed)
    effective_probe_size = validation_size if probe_size is None else probe_size
    if effective_probe_size < 0:
        raise ValueError("probe_size must be non-negative")
    if holdout_size is not None and holdout_size < 1:
        raise ValueError("holdout_size must be at least 1 when supplied")

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

    # Interpret explicit roles.  "probe" is still a development role; it only
    # requests membership in the fixed periodic probe.
    inner_cases: list[dict[str, Any]] = []
    outer_cases: list[dict[str, Any]] = []
    explicit_probe_refs: list[str] = []
    unmarked_cases: list[str] = []
    for ref, row in by_ref.items():
        raw_split = row.get("split") or row.get("partition")
        if raw_split in (None, ""):
            unmarked_cases.append(ref)
            continue
        split = str(raw_split).lower()
        if split in (
            "outer",
            "test_sealed",
            "outer_sealed",
            "final",
            "final_set",
            "final_holdout",
            "holdout",
        ):
            outer_cases.append(row)
        elif split in (
            "inner",
            "development",
            "development_pool",
            "d",
            "p0",
            "train",
            "probe",
            "gold_probe",
            "validation",
            "test",
        ):
            inner_cases.append(row)
            if split in ("probe", "gold_probe", "validation"):
                explicit_probe_refs.append(ref)
        else:
            raise RuntimeError(f"Unknown partition role {raw_split!r} for {ref}")

    if unmarked_cases:
        raise RuntimeError(
            "Every partition row requires an explicit development/final role; "
            "unmarked rows cannot be silently treated as development: "
            + ", ".join(unmarked_cases[:20])
        )
    explicit_outer_count = len(outer_cases)
    if holdout_size is None and not outer_cases:
        raise RuntimeError(
            "No explicit Outer marker or final-holdout marker is present and no "
            "holdout_size was supplied; refusing an unsealed all-development split"
        )

    if holdout_size is not None:
        if len(outer_cases) > holdout_size:
            raise RuntimeError(
                "Explicit final-holdout rows exceed holdout_size; explicitly "
                "sealed rows cannot be demoted into development"
            )
        shortfall = holdout_size - len(outer_cases)
        if shortfall:
            eligible = [
                row
                for row in inner_cases
                if str(row.get("case_id") or "").count("/") == 1
                and str(row.get("case_ref") or "")
                == _case_ref(str(row.get("case_id")))
            ]
            rng.shuffle(eligible)
            if len(eligible) < shortfall:
                raise RuntimeError(
                    "Not enough loadable development rows to satisfy holdout_size"
                )
            promoted = eligible[:shortfall]
            promoted_refs = {str(row["case_ref"]) for row in promoted}
            outer_cases.extend(promoted)
            inner_cases = [
                row for row in inner_cases if str(row["case_ref"]) not in promoted_refs
            ]
            explicit_probe_refs = [
                ref for ref in explicit_probe_refs if ref not in promoted_refs
            ]

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

    # Stratify the development pool for a reproducible, informative probe.
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

    # --- Sample the small periodic gold probe ---
    probe_refs: list[str] = list(dict.fromkeys(explicit_probe_refs))
    if len(probe_refs) > effective_probe_size:
        raise RuntimeError(
            "Explicit probe rows exceed probe_size; explicitly assigned probe "
            "rows cannot be silently changed"
        )
    probe_target = {
        "unresolved_negative": 15,  # unresolved cases (gold=neg)
        "unresolved_positive": 10,  # unresolved cases (gold=pos)
        "verified_error_negative": 10,  # FP cases
        "verified_error_positive": 10,  # TP cases
        "verified_clean_negative": 3,  # TN cases
        "verified_clean_positive": 2,  # FN cases
    }

    used_refs: set[str] = set(probe_refs)
    for stratum_key, target_count in probe_target.items():
        remaining_slots = effective_probe_size - len(probe_refs)
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
            probe_refs.append(ref)
            used_refs.add(ref)

    # Fill the remaining probe budget without opening the final holdout.
    if len(probe_refs) < effective_probe_size:
        remaining = [
            r for r in inner_cases
            if str(r.get("case_ref") or r.get("case_id")) not in used_refs
        ]
        rng.shuffle(remaining)
        for row in remaining:
            if len(probe_refs) >= effective_probe_size:
                break
            ref = str(row.get("case_ref") or row.get("case_id"))
            probe_refs.append(ref)
            used_refs.add(ref)

    # All remaining development cases are evolution/source-replay material by
    # default.  The legacy cap is still honored when explicitly supplied.
    evolution_candidates = [
        str(r.get("case_ref") or r.get("case_id"))
        for r in inner_cases
        if str(r.get("case_ref") or r.get("case_id")) not in used_refs
    ]
    rng.shuffle(evolution_candidates)
    if evolution_pool_size is None:
        evolution_pool_refs = list(evolution_candidates)
    else:
        if evolution_pool_size < 0:
            raise ValueError("evolution_pool_size must be non-negative")
        evolution_pool_refs = evolution_candidates[:evolution_pool_size]

    # A legacy test remainder may exist for old consumers, but it is still
    # development material under v3 and is not a second final holdout.
    evolution_set = set(evolution_pool_refs)
    test_refs = [
        ref for ref in evolution_candidates if ref not in evolution_set
    ]

    # Main evolution set = first N from pool
    main_evolution_refs = evolution_pool_refs[:main_evolution_size]

    # --- Summary ---
    development_set = {
        str(row.get("case_ref") or row.get("case_id")) for row in inner_cases
    }
    probe_set = set(probe_refs)
    test_set = set(test_refs)
    outer_set = {
        str(row.get("case_ref") or row.get("case_id")) for row in outer_cases
    }
    role_sets = {
        "gold_probe": probe_set,
        "evolution_pool": evolution_set,
        "legacy_test_remainder": test_set,
        "final_holdout": outer_set,
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
    assigned_inner = probe_set | evolution_set | test_set
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
    final_holdout_refs = [item["case_ref"] for item in outer_assignments]
    development_case_ids = [
        str(row.get("case_id") or "") for row in inner_cases
    ]
    development_ids_complete = all(
        case_id and _case_ref(case_id) == str(row.get("case_ref") or "")
        for case_id, row in zip(development_case_ids, inner_cases)
    )
    ids_by_ref = {
        str(row.get("case_ref") or ""): str(row.get("case_id") or "")
        for row in inner_cases
    }
    probe_case_ids = [ids_by_ref.get(ref, "") for ref in probe_refs]
    probe_ids_complete = bool(probe_refs) and all(probe_case_ids)

    partition = {
        "schema_version": "agenthallu.bccse_v5_data_partition.v3",
        "protocol": "development_pool_periodic_gold_probe_final_holdout",
        "seed": seed,
        "total_inner": len(inner_cases),
        "total_outer": len(outer_cases),
        "development_case_count": len(development_set),
        "probe_case_count": len(probe_set),
        "final_holdout_count": len(outer_set),
        "holdout_sealed": bool(holdout_sealed),
        "input_provenance": input_provenance,
        "input_role_counts": input_role_counts,
        "outer_role_evidence": {
            "explicit_outer_marker_present": explicit_outer_count > 0,
            "explicit_outer_marker_count": explicit_outer_count,
            "holdout_size_configured": holdout_size,
            "all_rows_have_explicit_roles": True,
            "unmarked_row_count": 0,
            "outer_role_assignment_sha256": _canonical_sha256(outer_assignments),
        },
        "development_pool": {
            "size": len(development_set),
            "case_refs": sorted(development_set),
            "includes_gold_probe": True,
            "reusable_for_evolution": True,
        },
        "gold_probe": {
            "size": len(probe_refs),
            "case_refs": probe_refs,
            "subset_of": "development_pool",
            "purpose": "budgeted_online_direction_and_rollback_only",
            "final_generalization_evidence": False,
        },
        # Legacy alias: validation means the development gold probe in v3.
        "validation_set": {
            "size": len(probe_refs),
            "case_refs": probe_refs,
            "alias_of": "gold_probe",
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
            "optional_legacy_development_remainder": True,
            "final_generalization_evidence": False,
        },
        "final_holdout": {
            "size": len(final_holdout_refs),
            "case_refs": final_holdout_refs,
            "sealed": bool(holdout_sealed),
            "read_only": True,
        },
        "outer_sealed": {
            "size": len(outer_cases),
            "case_refs": final_holdout_refs,
            "alias_of": "final_holdout",
        },
        "role_counts": {
            "development_pool": len(development_set),
            "gold_probe": len(probe_set),
            "final_holdout": len(outer_set),
            "validation": len(probe_set),
            "evolution_pool": len(evolution_set),
            "test": len(test_set),
            "outer": len(outer_set),
        },
        "role_sequence_hashes": {
            "development_pool": hashlib.sha256("\n".join(sorted(development_set)).encode("utf-8")).hexdigest(),
            "gold_probe": hashlib.sha256("\n".join(probe_refs).encode("utf-8")).hexdigest(),
            "final_holdout": hashlib.sha256("\n".join(final_holdout_refs).encode("utf-8")).hexdigest(),
            "validation": hashlib.sha256("\n".join(probe_refs).encode("utf-8")).hexdigest(),
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
            "probe_final_holdout_overlap_count": len(probe_set & outer_set),
        },
        "manifest_capability": {
            "development_case_ids_complete": development_ids_complete,
            "probe_case_ids_complete": probe_ids_complete,
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
        "canonical_role": "final_holdout",
        "holdout_sealed": bool(holdout_sealed),
        "custody_and_one_shot_enforcement": "external_governance_required",
        "reverse_discovery_used": False,
    }
    outer_manifest_path = output_dir / "outer_set_ids.json"
    with open(outer_manifest_path, "w", encoding="utf-8") as handle:
        json.dump(outer_manifest, handle, ensure_ascii=False, indent=2)

    final_assignments = [
        {"case_id": item["case_id"], "case_ref": item["case_ref"], "role": "final_holdout"}
        for item in outer_assignments
    ]
    final_manifest = {
        **outer_manifest,
        "schema_version": "agenthallu.final_holdout_manifest.v1",
        "role": "final_holdout",
        "final_holdout_case_ids": outer_case_ids,
        "final_holdout_case_refs": final_holdout_refs,
        "role_assignments": final_assignments,
        "role_assignment_sha256": _canonical_sha256(final_assignments),
    }
    final_manifest_path = output_dir / "final_holdout_ids.json"
    with open(final_manifest_path, "w", encoding="utf-8") as handle:
        json.dump(final_manifest, handle, ensure_ascii=False, indent=2)

    if development_ids_complete:
        development_manifest = {
            "schema_version": "agenthallu.development_pool_manifest.v1",
            "role": "development_pool",
            "development_case_ids": development_case_ids,
            "probe_case_ids": probe_case_ids,
            "evolution_case_ids": [
                case_id
                for case_id in development_case_ids
                if case_id not in set(probe_case_ids)
            ],
            "partition_payload_sha256": _canonical_sha256(partition),
        }
        with open(output_dir / "development_pool_ids.json", "w", encoding="utf-8") as handle:
            json.dump(development_manifest, handle, ensure_ascii=False, indent=2)
    if probe_ids_complete:
        probe_manifest = {
            "schema_version": "agenthallu.gold_probe_manifest.v1",
            "role": "gold_probe",
            "probe_case_ids": probe_case_ids,
            "validation_case_ids": probe_case_ids,
            "partition_payload_sha256": _canonical_sha256(partition),
            "final_generalization_evidence": False,
        }
        with open(output_dir / "probe_set_ids.json", "w", encoding="utf-8") as handle:
            json.dump(probe_manifest, handle, ensure_ascii=False, indent=2)

    print(f"\nPartition saved to: {out_path}")
    print(f"  Development pool: {len(development_set)} cases")
    print(f"  Periodic gold probe: {len(probe_refs)} cases")
    print(f"  Evolution pool: {len(evolution_pool_refs)} cases (main: {main_evolution_size})")
    print(f"  Final holdout: {len(outer_cases)} cases (sealed={bool(holdout_sealed)})")
    print(f"  Final-holdout manifest: {final_manifest_path}")

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
    parser.add_argument(
        "--probe-size",
        type=int,
        default=10,
        help="Fixed development-pool gold probe size",
    )
    parser.add_argument(
        "--holdout-size",
        type=int,
        default=None,
        help="Final holdout size; omitted means use all explicitly marked Outer rows",
    )
    args = parser.parse_args()

    partition_data(
        d_predictions_path=args.d_predictions,
        p0_predictions_path=args.p0_predictions,
        output_dir=args.output_dir,
        seed=args.seed,
        probe_size=args.probe_size,
        holdout_size=args.holdout_size,
    )
