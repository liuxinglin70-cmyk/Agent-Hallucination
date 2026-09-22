"""Build the bigger development split for the framework validation run.

Rule fixed before any measurement:

* the split is drawn from the whole corpus with a fixed seed, stratified by
  source so no single framework dominates;
* ``evolution_set`` (the pool the cycle may patrol, propose on and admit
  against) and ``validation_set`` (read only by the post-cycle effect check)
  are disjoint, which is the mini protocol's own requirement;
* no case is placed in both, and no case is hand-picked by looking at a
  detector result.

This is a *development* split: every case is inside
``datasets/protocols/development_exposure.json``, so no reading from it is a
generalization claim. Building a bigger manifest does not change that, and the
report must say so.
"""

from __future__ import annotations

import collections
import json
import os
import random
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from evolution.splits import bool_gold, load_gold  # noqa: E402

ROOT = os.path.join(REPO, "datasets", "raw", "agenthallu", "AgentHallu")

POOL_SIZE = 44
VALIDATION_SIZE = 12
SEED = 20260921


def main() -> int:
    by_source: dict[str, list[str]] = collections.defaultdict(list)
    for source in sorted(os.listdir(ROOT)):
        source_dir = os.path.join(ROOT, source)
        if not os.path.isdir(source_dir):
            continue
        for name in sorted(os.listdir(source_dir)):
            if name.endswith(".json"):
                by_source[source].append(f"{source}/{name}")

    rng = random.Random(SEED)
    pool_share = POOL_SIZE / (POOL_SIZE + VALIDATION_SIZE)
    pool_by_source: dict[str, list[str]] = {}
    validation: list[str] = []
    for source, cases in sorted(by_source.items()):
        shuffled = list(cases)
        rng.shuffle(shuffled)
        take_pool = round(len(shuffled) * pool_share)
        pool_by_source[source] = list(shuffled[:take_pool])
        validation.extend(shuffled[take_pool:])

    # Round-robin so the corpus's smaller frameworks still appear. Concatenating
    # per-source lists and then truncating would fill the pool with whichever
    # source sorts first (BFCL here), which is exactly the bias stratification
    # exists to avoid.
    pool: list[str] = []
    queues = [list(cases) for _source, cases in sorted(pool_by_source.items())]
    while queues and len(pool) < POOL_SIZE:
        for queue in queues:
            if queue and len(pool) < POOL_SIZE:
                pool.append(queue.pop(0))
    validation = sorted(c for c in validation if c not in set(pool))
    validation = validation[:VALIDATION_SIZE]

    overlap = sorted(set(pool) & set(validation))
    if overlap:
        raise SystemExit(f"split overlaps: {overlap[:5]}")

    payload = {
        "schema_version": "evolution.development_split.v1",
        "purpose": (
            "Bigger development-only replay for the framework validation run. "
            "Disjoint pool/validation, but every case is inside the documented "
            "development exposure; not a generalization benchmark."
        ),
        "seed": SEED,
        "rule": (
            "seeded stratified draw over the whole corpus, proportional to "
            "source size; pool and validation are disjoint; no detector output "
            "and no candidate behaviour entered the selection"
        ),
        "evolution_set": {"size": len(pool), "cases": pool},
        "validation_set": {"size": len(validation), "cases": validation},
    }
    out = os.path.join(REPO, "output", "selfevo-run", "split-big.json")
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    positives = sum(1 for c in pool if bool_gold(load_gold(ROOT, c)))
    val_positives = sum(1 for c in validation if bool_gold(load_gold(ROOT, c)))
    print(f"pool       {len(pool):3d} cases ({positives} gold-true, "
          f"{len(pool) - positives} gold-false)")
    print(f"validation {len(validation):3d} cases ({val_positives} gold-true, "
          f"{len(validation) - val_positives} gold-false)")
    print(f"by source pool: "
          f"{dict(sorted(collections.Counter(c.split('/')[0] for c in pool).items()))}")
    print(f"written -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
