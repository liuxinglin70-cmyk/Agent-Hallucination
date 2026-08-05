#!/usr/bin/env python3
"""Stratified batch-case selector for the 5x1 concurrent gold experiments.

Each batch consumes exactly five case_ids, one per experiment, all distinct
across batches and disjoint from the used-case ledger.  Selection is
stratified over hallucination category and agent family so that every batch
mixes: (a) hallucinated vs clean polarities, (b) different families,
(c) different hallucination categories when the pool allows.

Usage (from repo root):

    python experiments/select_batch_cases.py --batch 1

Artifacts:

    experiments/results/batch<B>_gold/batch_manifest.json
        {"batch": B, "cases": [{"case_id", "family", "gold",
                                "category", "subcategory"}], ...}
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET_ROOT = ROOT / "datasets" / "raw" / "agenthallu" / "AgentHallu"
LEDGER = ROOT / "experiments" / "protocols" / "used_case_ledger.txt"
RESULTS_ROOT = ROOT / "experiments" / "results"

# Keep randomness deterministic per batch for reproducible manifests.
random.seed(20260805)


def load_ledger() -> set[str]:
    lines = [
        line.strip()
        for line in LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return set(lines)


def case_pool() -> list[dict]:
    """All raw records not yet consumed by any experiment."""
    ledger = load_ledger()
    pool: list[dict] = []
    for family_dir in sorted(DATASET_ROOT.iterdir()):
        if not family_dir.is_dir():
            continue
        for record_path in sorted(family_dir.glob("*.json")):
            case_id = f"{family_dir.name}/{record_path.stem}"
            if case_id in ledger:
                continue
            record = json.loads(record_path.read_text(encoding="utf-8"))
            pool.append({
                "case_id": case_id,
                "family": family_dir.name,
                "record_path": record_path,
                "gold": str(record.get("is_hallucination", "")).lower()
                in ("true", "1"),
                "category": record.get("hallucination_category") or "",
                "subcategory": record.get("hallucination_subcategory") or "",
            })
    return pool


def category_group(value: str) -> str:
    """Reduce the raw category string to a stable evolution-facing group."""
    value = str(value or "")
    if "Tool-Use" in value:
        return "tool"
    if "Reasoning" in value:
        return "reasoning"
    if "Retrieval" in value:
        return "retrieval"
    if "Planning" in value:
        return "planning"
    if "Human-Interaction" in value:
        return "interaction"
    if "Instruction" in value:
        return "instruction"
    return "other"


def select_batch(batch: int, size: int = 10, exclude: set[str] | None = None) -> list[dict]:
    pool = case_pool()
    if exclude:
        pool = [case for case in pool if case["case_id"] not in exclude]
    if len(pool) < size:
        raise RuntimeError(
            f"only {len(pool)} eligible cases; cannot fill batch of {size}"
        )

    picked: list[dict] = []
    used_families: set[str] = set()
    # Bucket hallucinated cases by category group; clean cases share one pool.
    hallu_by_group: dict[str, list[dict]] = {}
    clean_cases: list[dict] = []
    for case in pool:
        if case["gold"]:
            group = category_group(case["category"])
            hallu_by_group.setdefault(group, []).append(case)
        else:
            clean_cases.append(case)
    for bucket in hallu_by_group.values():
        random.shuffle(bucket)
    random.shuffle(clean_cases)
    random.shuffle(pool)

    # Deterministic per-batch rotation over category groups.
    groups = sorted(hallu_by_group)
    if groups:
        start = (batch - 1) % len(groups)
        groups = groups[start:] + groups[:start]

    # 1) Hallucinated: one per distinct group, cycling until the target share.
    hallu_goal = min(round(size * 3 / 5), sum(len(b) for b in hallu_by_group.values()))
    for group in groups:
        if len(picked) >= hallu_goal:
            break
        bucket = hallu_by_group[group]
        case = next((c for c in bucket if c["family"] not in used_families), None)
        if case is None:
            continue
        picked.append(case)
        used_families.add(case["family"])
        bucket.remove(case)
    if len(picked) < hallu_goal:
        for case in pool:
            if len(picked) >= hallu_goal:
                break
            if case in picked or not case["gold"]:
                continue
            picked.append(case)
            used_families.add(case["family"])

    def take(targets: list[dict]) -> None:
        for case in targets:
            if len(picked) >= size:
                break
            if case in picked:
                continue
            picked.append(case)
            used_families.add(case["family"])

    # 2) Clean cases to balance polarity while rotating families.  Ordering
    # all records by whether their family was seen lets the first unseen
    # family monopolize every remaining slot, so select at most one record
    # from each family per pass.
    clean_by_family: dict[str, list[dict]] = {}
    for case in clean_cases:
        clean_by_family.setdefault(case["family"], []).append(case)
    clean_families = sorted(
        clean_by_family,
        key=lambda family: (family in used_families, family),
    )
    while len(picked) < size and any(clean_by_family.values()):
        for family in clean_families:
            bucket = clean_by_family[family]
            if bucket:
                take([bucket.pop()])
            if len(picked) >= size:
                break
    # 3) Fill remaining slots from the full pool (distinct families first).
    take(sorted(pool, key=lambda c: c["family"] in used_families))
    if len(picked) < size:
        raise RuntimeError(
            f"only {len(picked)} eligible cases; cannot fill batch of {size}"
        )
    return picked[:size]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, required=True)
    parser.add_argument("--size", type=int, default=10)
    parser.add_argument(
        "--exclude", type=str, default=None,
        help="comma-separated case_ids to exclude from this batch (e.g. "
        "SmolAgents/024 to swap out a provider-filtered case)",
    )
    args = parser.parse_args()

    exclude = {
        token.strip() for token in (args.exclude or "").split(",") if token.strip()
    }
    picked = select_batch(args.batch, size=args.size, exclude=exclude)
    manifest = {
        "batch": args.batch,
        "size": len(picked),
        "cases": [
            {
                "case_id": case["case_id"],
                "family": case["family"],
                "gold": case["gold"],
                "category": case["category"],
                "subcategory": case["subcategory"],
            }
            for case in picked
        ],
    }
    out_dir = RESULTS_ROOT / f"batch{args.batch}_gold"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "batch_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"[selector] manifest written: {manifest_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
