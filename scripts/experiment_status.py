#!/usr/bin/env python
"""One compact status line per in-flight run, for long unattended waits."""

from __future__ import annotations

import json
import time
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detector.runner import case_elapsed
from detector.runner import safe_case_id
from detector.runtime import contained_path, exclusive_writer
from experiments.snapshot import run_snapshot

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
    with run_snapshot({name: root for name, root, _ in TREES}) as snapshot:
        for name, root, target in TREES:
            rows = snapshot.rows[name]
            if not rows:
                continue
            newest = max(contained_path(root, "cases", safe_case_id(row["case"]), "result.json").stat().st_mtime for row in rows)
            states: dict[str, int] = {}
            elapsed = 0.0
            for row in rows:
                result = row["result"]
                states[str(result.get("state"))] = states.get(str(result.get("state")), 0) + 1
                elapsed += case_elapsed(result) or 0
            share = f"/{target}" if target else ""
            idle = (time.time() - newest) / 60
            print(
                f"  {name:8s} {len(rows):4d}{share:5s} "
                f"idle={idle:5.1f}m work={elapsed / 60:7.1f}m "
                f"{ {k: v for k, v in sorted(states.items())} }"
            )
    ledger = ROOT / "artifacts/selfevo-real-multigen-20260923/ledger.jsonl"
    if ledger.is_file():
        with exclusive_writer(ledger.parent):
            events = [json.loads(line) for line in ledger.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        names = [str(e.get("event")) for e in events]
        print(f"  multigen {len(events)} records: {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
