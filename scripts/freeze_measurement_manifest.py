#!/usr/bin/env python
"""Freeze the pre-registered measurement order, before any real reading exists.

Why this file exists. The corpus is 693 cases and a full pass costs 21-28h at
``workers=8`` (``artifacts/cost_model_693.json``), so the run will be stopped at
some prefix of whatever order it walks. ``detector.runner.run_batch`` submits
in case-list order, and the corpus lists its sources as contiguous blocks
(BFCL, then Camel, then Magentic_One, ...). Stopping inside that order
therefore measures "the first few sources, minus the slow cases that had not
finished yet" -- a selection bias introduced by the clock, not by the design.

Rotating across sources makes *every* prefix approximately a stratified sample,
which is what turns "how long do we run" from a design-time guess into a
run-time budget decision. The order is frozen here, once, before the first real
reading exists; afterwards the size is chosen by "how far did we get", never by
"which cases did we pick".

Two exclusions, both stated rather than silent:

1. The mini split's own 40 cases (sentinel S, gain G, patrol pool, validation).
   These are the evolution loop's exposure surface. Including any of them in the
   measurement set is what would make a deployed-arm improvement
   indistinguishable from "the loop already looked at these cases" -- the
   constraint §33.2 records after finding that every archived reading came from
   the same ten cases repeated about thirty times.
2. Cases without a resolvable gold label, which no metric can score.

    # write the frozen order (offline, reads only datasets/ and the split)
    python scripts/freeze_measurement_manifest.py

    # see what it would write without writing
    python scripts/freeze_measurement_manifest.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.config import load_config  # noqa: E402
from evolution.splits import build_split, load_gold  # noqa: E402

SCHEMA = "detector.measurement_manifest.v1"
DEFAULT_OUT = ROOT / "datasets" / "protocols" / "measurement_v1.json"
DEFAULT_SEED = 20260923


def rotation_order(
    cases: list[str], *, seed: int, sources: list[str] | None = None,
) -> list[str]:
    """Round-robin across sources, each source shuffled under *seed*.

    Source order itself is shuffled too, so no source systematically leads --
    leaving it alphabetical would make the *shortest* prefixes under-represent
    whichever source sorts last.
    """
    by_source: dict[str, list[str]] = {}
    for case in cases:
        by_source.setdefault(case.split("/")[0], []).append(case)
    rng = random.Random(seed)
    names = list(by_source) if sources is None else [s for s in sources if s in by_source]
    rng.shuffle(names)
    for name in names:
        rng.shuffle(by_source[name])
    ordered: list[str] = []
    depth = 0
    while True:
        added = False
        for name in names:
            bucket = by_source[name]
            if depth < len(bucket):
                ordered.append(bucket[depth])
                added = True
        if not added:
            break
        depth += 1
    return ordered


def build_manifest(
    *,
    config_path: str | Path,
    dataset_root: str | Path,
    seed: int = DEFAULT_SEED,
) -> dict:
    config = load_config(config_path)
    # The evolution loop's exposure surface is the mini pool (S + G + patrol)
    # plus the validation set -- ``build_split(mini=True).pool`` is the 30-case
    # mini pool, so the excluded set is 40, not the 30 a pool-only read implies.
    split = build_split(config=config, dataset_root=dataset_root, mini=True)
    excluded = sorted(
        set(split.sentinel)
        | set(split.gain)
        | set(split.patrol_pool)
        | set(split.validation)
    )
    # The measurement set is the whole corpus minus that surface, so it comes
    # from the exposure manifest (all 693) and not from the split pool.
    exposure_path = ROOT / "datasets/protocols/development_exposure.json"
    for configured in config.get("historical_development_manifests") or ():
        candidate = Path(str(configured))
        if candidate.is_file():
            exposure_path = candidate
            break
    exposure = json.loads(exposure_path.read_text(encoding="utf-8-sig"))
    corpus = [str(case) for case in exposure["cases"]]
    missing_from_corpus = sorted(set(excluded) - set(corpus))
    if missing_from_corpus:
        raise SystemExit(
            "the mini split names cases absent from the exposure manifest: "
            f"{missing_from_corpus[:5]}"
        )
    candidates = [case for case in corpus if case not in set(excluded)]
    gold = {case: load_gold(dataset_root, case) for case in candidates}
    unlabeled = sorted(case for case, label in gold.items() if label is None)
    measurable = [case for case in candidates if gold[case] is not None]
    ordered = rotation_order(measurable, seed=seed)
    sources = sorted({case.split("/")[0] for case in ordered})
    return {
        "schema_version": SCHEMA,
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "policy": (
            "Pre-registered measurement order. Every prefix of 'cases' is an "
            "approximately stratified sample, so the run may be stopped at any "
            "length without biasing which sources or which gold polarities are "
            "covered. Cases excluded here are the evolution loop's own exposure "
            "surface; adding them later would make a deployed-arm improvement "
            "indistinguishable from the loop having already seen the case."
        ),
        "exclusion_rule": (
            "mini split (sentinel + gain + patrol_pool + validation) and any "
            "case whose gold label does not resolve"
        ),
        "rotation": {
            "method": "round_robin_over_sources",
            "sources": sources,
            "source_order_seeded": True,
            "within_source_seeded": True,
        },
        "counts": {
            "corpus": len(corpus),
            "excluded_mini_split": len(excluded),
            "excluded_unlabeled": len(unlabeled),
            "measurable": len(measurable),
        },
        "excluded_mini_split": excluded,
        "excluded_unlabeled": unlabeled,
        "gold_positive": sum(1 for case in ordered if gold[case] is True),
        "gold_negative": sum(1 for case in ordered if gold[case] is False),
        "cases": ordered,
    }


def case_list_digest(cases: list[str]) -> str:
    """Content digest of the frozen list, so a later reader can prove it.

    The order *is* the pre-registration, so the digest is over the ordered ids
    and not over the set: a re-sorted list is a different experiment.
    """
    payload = "\n".join(cases).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default=str(ROOT / "evolution/configs/selfevo_real_dev2_20260923.json"),
        help="config whose mini split defines the excluded exposure surface",
    )
    parser.add_argument(
        "--dataset-root",
        default=str(ROOT / "datasets/raw/agenthallu/AgentHallu"),
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    manifest = build_manifest(
        config_path=args.config,
        dataset_root=args.dataset_root,
        seed=args.seed,
    )
    manifest["cases_digest"] = case_list_digest(manifest["cases"])
    out = Path(args.out)
    if out.exists() and not args.dry_run:
        # The order is a pre-registration: rewriting it after a reading exists
        # would silently re-point the experiment at a different sample.
        raise SystemExit(
            f"{out} already exists; the measurement order is frozen. "
            "Delete it deliberately if you mean to re-register."
        )
    if not args.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({
        "out": None if args.dry_run else str(out),
        "counts": manifest["counts"],
        "gold_positive": manifest["gold_positive"],
        "gold_negative": manifest["gold_negative"],
        "sources": manifest["rotation"]["sources"],
        "first_12": manifest["cases"][:12],
        "cases_digest": manifest["cases_digest"],
        "dry_run": args.dry_run,
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
