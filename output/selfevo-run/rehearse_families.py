"""Read-only rehearsal: can this split produce a diagnosable failure family?

Why this exists. A full cycle is now ~10 serial case evaluations deep, so a run
that cannot possibly yield a family burns a night for nothing. The gates are
pure functions over precomputed rows, so the diagnosis half of a cycle can be
replayed from archived rows with no provider calls at all.

This loads an archived attempt's rows for the *patrol* sample of a cycle,
filters them the way the engine does, and runs the real
``stable_failure_families`` / ``stable_localization_families`` /
``classify_family_actionability`` code. The output is what the cycle's
worksheet would have said, plus the routes each family would take.

It reads only; it writes nothing and calls nothing.

    python output/selfevo-run/rehearse_families.py \
        --work-root output/selfevo-run/attempt2-frozen --cycle 1
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evolution.config import load_config  # noqa: E402
from evolution.splits import build_split, load_gold_step  # noqa: E402
from evolution.worksheet import (  # noqa: E402
    classify_family_actionability,
    localization_state,
    stable_failure_families,
    stable_localization_families,
)


def load_rows(work_root: Path) -> list[dict]:
    rows = []
    for path in sorted(
        work_root.glob("checkpoints/**/arms/*/*.json")
    ):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        row = payload.get("row")
        if isinstance(row, dict):
            row["_path"] = str(path)
            rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="evolution/configs/validation_big_run.json")
    parser.add_argument("--work-root", default="output/selfevo-run/attempt2-frozen")
    parser.add_argument("--cycle", type=int, default=1)
    args = parser.parse_args()

    config = load_config(args.config)
    split = build_split(
        config=config,
        dataset_root=config["dataset_root"],
        mini=True,
    )
    rows = load_rows(Path(args.work_root))
    patrol = set(split.patrol_sample(args.cycle, int(
        (config.get("budget") or {}).get("patrol_cases", 120)
    )))

    print(json.dumps({
        "work_root": args.work_root,
        "cycle": args.cycle,
        "rows_loaded": len(rows),
        "patrol_size": len(patrol),
        "sentinel_size": len(split.sentinel),
        "gain_size": len(split.gain),
    }, ensure_ascii=False), flush=True)

    # Which wave each archived row belongs to tells us how far the attempt got.
    seen = collections.Counter()
    for row in rows:
        case = str(row.get("case") or "")
        if case in patrol:
            seen["patrol"] += 1
        elif case in set(split.sentinel):
            seen["sentinel"] += 1
        elif case in set(split.gain):
            seen["gain"] += 1
        else:
            seen["other"] += 1
    print("archived rows by wave:", dict(seen), flush=True)

    patrol_rows = [r for r in rows if str(r.get("case") or "") in patrol]
    if not patrol_rows:
        print(json.dumps({
            "verdict": "no_patrol_rows_archived",
            "meaning": "this attempt never reached the patrol wave, so family "
                       "formation cannot be rehearsed from it",
        }, ensure_ascii=False))
        return 0

    detection = stable_failure_families(patrol_rows, min_cases=2)
    gold_steps = {
        case: load_gold_step(config["dataset_root"], case)
        for case in split.pool
    }
    localization = stable_localization_families(
        patrol_rows, gold_steps=gold_steps, min_cases=2,
    )

    # The engine skips rows whose stage or authority disqualifies them; report
    # how many patrol rows survive that filter, because a family needs >= 2.
    eligible = 0
    reasons = collections.Counter()
    for row in patrol_rows:
        gold = row.get("gold")
        if gold is not True and gold is not False:
            reasons["gold_missing"] += 1
            continue
        if str(row.get("authority") or "") != "authoritative":
            reasons["not_authoritative"] += 1
            continue
        statuses = (row.get("result") or {}).get("stage_status") or {}
        if any(
            str((s or {}).get("state") or "").startswith(
                ("provider_error", "invalid", "contract_error", "degraded")
            )
            for s in statuses.values()
        ):
            reasons["degraded_stage"] += 1
            continue
        if row.get("prediction") == gold:
            reasons["correct_on_detection"] += 1
            continue
        eligible += 1

    print(json.dumps({
        "patrol_rows": len(patrol_rows),
        "eligible_for_family": eligible,
        "skipped": dict(reasons),
        "detection_families": [
            {k: f[k] for k in ("failure_family", "count", "failure_cases", "sources")}
            for f in detection
        ],
        "localization_families": [
            {k: f[k] for k in ("failure_family", "count", "failure_cases", "sources")}
            for f in localization
        ],
    }, ensure_ascii=False, indent=2), flush=True)

    print("\nroutes each family would take:", flush=True)
    for family in detection:
        routes = classify_family_actionability(family)
        print(json.dumps({
            "family": family["failure_family"],
            "count": family["count"],
            "cases": family["failure_cases"],
            "routes": routes,
        }, ensure_ascii=False), flush=True)

    print("\nper-case localization state:", flush=True)
    for row in sorted(patrol_rows, key=lambda r: str(r.get("case"))):
        case = str(row.get("case") or "")
        print(json.dumps({
            "case": case,
            "gold": row.get("gold"),
            "prediction": row.get("prediction"),
            "loc_state": localization_state(row, gold_steps),
            "gold_step": gold_steps.get(case),
            "observed_step": row.get("responsible_step"),
        }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
