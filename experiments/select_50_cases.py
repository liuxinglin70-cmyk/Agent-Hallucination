#!/usr/bin/env python3
"""Stratified 50-case evolution-set selector for the sequential 50-run.

Selection is stratified over hallucination category, agent family and gold
polarity so the evolution set is as balanced as the available pool allows:

- ~35 hallucinated + ~15 clean (mirrors the corpus ratio)
- every hallucination category represented (tool/reasoning/retrieval/
  planning/interaction/instruction when the pool allows)
- every family represented
- subcategory diversity within each category

Every case_id is disjoint from the used-case ledger (including batches 1-2).

Usage (from repo root):

    python experiments/select_50_cases.py

Artifacts:

    experiments/results/evolve_50/batch_manifest.json
        {"cases": [{case_id, family, gold, category, subcategory}], ...}
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET_ROOT = ROOT / "datasets" / "raw" / "agenthallu" / "AgentHallu"
LEDGER = ROOT / "experiments" / "protocols" / "used_case_ledger.txt"
OUT_DIR = ROOT / "experiments" / "results" / "evolve_50"

random.seed(20260806)

TARGET_HALLU = 35
TARGET_CLEAN = 15

# Category -> target count for the 35 hallucinated cases.
CATEGORY_TARGETS = {
    "tool": 5,
    "reasoning": 10,
    "retrieval": 6,
    "planning": 6,
    "interaction": 6,
    "instruction": 1,
    "other": 1,
}


def balanced_online_order(cases: list[dict]) -> list[dict]:
    """Interleave polarity, family, and category for the online case stream."""
    remaining = list(cases)
    family_targets = Counter(case["family"] for case in cases)
    group_targets = Counter(case["group"] for case in cases if case["gold"])
    family_seen: Counter[str] = Counter()
    group_seen: Counter[str] = Counter()
    ordered: list[dict] = []
    clean_positions = {2, 5, 8}

    while remaining:
        position = len(ordered)
        want_gold = position % 10 not in clean_positions
        candidates = [case for case in remaining if case["gold"] is want_gold]
        if not candidates:
            candidates = remaining
        previous_family = ordered[-1]["family"] if ordered else None

        def score(case: dict) -> tuple:
            family = case["family"]
            group = case["group"]
            return (
                family == previous_family,
                family_seen[family] / family_targets[family],
                (
                    group_seen[group] / group_targets[group]
                    if case["gold"] and group_targets[group]
                    else 0.0
                ),
                case["case_id"],
            )

        selected = min(candidates, key=score)
        remaining.remove(selected)
        ordered.append(selected)
        family_seen[selected["family"]] += 1
        if selected["gold"]:
            group_seen[selected["group"]] += 1
    return ordered


def load_ledger() -> set[str]:
    lines = [
        line.strip()
        for line in LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return set(lines)


def category_group(value: str) -> str:
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


def case_pool() -> list[dict]:
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
                "group": category_group(record.get("hallucination_category")),
            })
    return pool


def select_50() -> list[dict]:
    pool = case_pool()
    hallu = [c for c in pool if c["gold"]]
    clean = [c for c in pool if not c["gold"]]
    random.shuffle(hallu)
    random.shuffle(clean)

    picked: list[dict] = []
    picked_ids: set[str] = set()
    family_count: dict[str, int] = {}

    def take(case: dict) -> None:
        picked.append(case)
        picked_ids.add(case["case_id"])
        family_count[case["family"]] = family_count.get(case["family"], 0) + 1

    # 1) Hallucinated by category quota, rotating families for diversity.
    by_group: dict[str, list[dict]] = {}
    for case in hallu:
        by_group.setdefault(case["group"], []).append(case)
    for group, target in sorted(CATEGORY_TARGETS.items()):
        bucket = by_group.get(group, [])
        # Under-represented families first (round-robin), then subcategory
        # diversity, so no family starves inside a category quota (observed:
        # Camel had 34 eligible hallucinated cases yet only clean cases made
        # the cut).
        bucket.sort(
            key=lambda c: (
                family_count.get(c["family"], 0),
                c["subcategory"],
                random.random(),
            )
        )
        for case in bucket:
            if sum(1 for c in picked if c["gold"] and c["group"] == group) >= target:
                break
            if family_count.get(case["family"], 0) >= 9:
                continue
            take(case)
    # 2) Top up hallucinated if any quota was unmet (e.g. instruction/other).
    for case in hallu:
        if sum(1 for c in picked if c["gold"]) >= TARGET_HALLU:
            break
        if case["case_id"] in picked_ids or family_count.get(case["family"], 0) >= 9:
            continue
        take(case)
    # 3) Clean cases: one per family in round-robin until target.
    families = sorted({c["family"] for c in clean})
    random.shuffle(families)
    clean_by_family: dict[str, list[dict]] = {}
    for case in clean:
        clean_by_family.setdefault(case["family"], []).append(case)
    for bucket in clean_by_family.values():
        random.shuffle(bucket)
    cursor = 0
    for _ in range(TARGET_CLEAN):
        if sum(1 for c in picked if not c["gold"]) >= TARGET_CLEAN:
            break
        for _ in range(len(families)):
            family = families[cursor % len(families)]
            cursor += 1
            bucket = clean_by_family.get(family, [])
            case = next(
                (c for c in bucket if c["case_id"] not in picked_ids), None
            )
            if case is not None:
                take(case)
                break

    if len(picked) != 50:
        raise RuntimeError(
            f"only {len(picked)} eligible cases after stratification; "
            "pool too small"
        )
    return balanced_online_order(picked)


def main() -> int:
    picked = select_50()
    manifest = {
        "size": len(picked),
        "hallucinated": sum(1 for c in picked if c["gold"]),
        "clean": sum(1 for c in picked if not c["gold"]),
        "cases": [
            {
                "sequence_index": index,
                "case_id": c["case_id"],
                "family": c["family"],
                "gold": c["gold"],
                "category": c["category"],
                "subcategory": c["subcategory"],
            }
            for index, c in enumerate(picked)
        ],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "batch_manifest.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    records = {
        case["case_id"]: json.loads(case["record_path"].read_text(encoding="utf-8"))
        for case in picked
    }
    records_path = OUT_DIR / "records.json"
    records_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    # Quick balance summary.
    groups = Counter(c["group"] for c in picked if c["gold"])
    families = Counter(c["family"] for c in picked)
    print(f"hallu={manifest['hallucinated']} clean={manifest['clean']}")
    print("category groups:", dict(groups))
    print("families:", dict(families))
    print(f"manifest written: {path}")
    print(f"records written: {records_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
