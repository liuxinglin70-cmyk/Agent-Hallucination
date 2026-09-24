#!/usr/bin/env python
"""One compact status line per in-flight run, for long unattended waits."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TREES = [
    ("nf_roll1", ROOT / "artifacts/noise_floor/runs/nf_roll1", 40),
    ("nf_roll2", ROOT / "artifacts/noise_floor/runs/nf_roll2", 40),
    ("nf_roll3", ROOT / "artifacts/noise_floor/runs/nf_roll3", 40),
    ("g0", ROOT / "artifacts/baseline/runs/g0", 653),
    ("g1", ROOT / "artifacts/baseline/runs/g1", None),
    ("g0_roll2", ROOT / "artifacts/baseline/runs/g0_roll2", None),
]


def main() -> int:
    print(datetime.now(timezone.utc).strftime("%H:%M:%SZ"))
    for name, root, target in TREES:
        cases = root / "cases"
        if not cases.is_dir():
            continue
        dirs = [d for d in cases.iterdir() if (d / "result.json").is_file()]
        if not dirs:
            continue
        newest = max(d.stat().st_mtime for d in dirs)
        states: dict[str, int] = {}
        elapsed = 0.0
        for d in dirs:
            try:
                result = json.loads((d / "result.json").read_text(encoding="utf-8-sig"))
            except ValueError:
                continue
            states[str(result.get("state"))] = states.get(str(result.get("state")), 0) + 1
            stamp = d / "elapsed_seconds.json"
            if stamp.is_file():
                try:
                    elapsed += float(
                        json.loads(stamp.read_text(encoding="utf-8-sig")).get(
                            "elapsed_seconds"
                        ) or 0
                    )
                except ValueError:
                    pass
        share = f"/{target}" if target else ""
        idle = (time.time() - newest) / 60
        print(
            f"  {name:8s} {len(dirs):4d}{share:5s} "
            f"idle={idle:5.1f}m work={elapsed / 60:7.1f}m "
            f"{ {k: v for k, v in sorted(states.items())} }"
        )
    ledger = ROOT / "artifacts/selfevo-real-multigen-20260923/ledger.jsonl"
    if ledger.is_file():
        events = [
            json.loads(line)
            for line in ledger.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
        names = [str(e.get("event")) for e in events]
        print(f"  multigen {len(events)} records: {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
