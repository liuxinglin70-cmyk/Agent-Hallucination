"""Block until a cycle finishes, then report. Read-only, no writes.

Polling from the chat loop costs a turn per check, so this runs as a
background watcher and prints one final summary. It exits as soon as the
cycle's report file exists or the ledger records a terminal cycle event --
completion is the interesting event; a freeze or interruption is reported the
same way, because both end the run.

    python output/selfevo-run/wait_cycle.py --work-root artifacts/selfevo-bigrun4 --cycle 1
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

TERMINAL_EVENTS = {
    "cycle_completed", "cycle_interrupted", "cycle_blocked", "cycle_frozen",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", default="artifacts/selfevo-bigrun4")
    parser.add_argument("--cycle", type=int, default=1)
    parser.add_argument("--poll", type=float, default=60.0)
    parser.add_argument("--max-hours", type=float, default=20.0)
    args = parser.parse_args()

    root = Path(args.work_root)
    report = root / "cycles" / f"cycle{args.cycle:03d}.json"
    ledger = root / "ledger.jsonl"
    deadline = time.monotonic() + args.max_hours * 3600
    started = time.monotonic()

    while time.monotonic() < deadline:
        if report.exists():
            print(json.dumps({
                "event": "report_written",
                "path": str(report),
                "waited_minutes": round((time.monotonic() - started) / 60, 1),
            }, ensure_ascii=False), flush=True)
            try:
                payload = json.loads(report.read_text(encoding="utf-8-sig"))
                print(json.dumps({
                    "cycle": payload.get("cycle"),
                    "state": payload.get("state"),
                    "admitted": payload.get("active_after_cycle"),
                    "patrol": {
                        key: (payload.get("patrol") or {}).get(key)
                        for key in ("patrol_size", "tp", "fp", "fn", "tn",
                                    "unresolved", "stable_failure_families")
                    },
                }, ensure_ascii=False), flush=True)
            except (OSError, ValueError) as exc:
                print(json.dumps({"read_error": str(exc)}), flush=True)
            return 0
        if ledger.exists():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                event = str(entry.get("event") or "")
                if event in TERMINAL_EVENTS and int(
                    (entry.get("payload") or {}).get("cycle") or 0
                ) == args.cycle:
                    print(json.dumps({
                        "event": event,
                        "payload": entry.get("payload"),
                        "waited_minutes": round((time.monotonic() - started) / 60, 1),
                    }, ensure_ascii=False), flush=True)
                    if event != "cycle_completed":
                        return 1
        time.sleep(args.poll)

    print(json.dumps({
        "event": "timeout",
        "waited_hours": args.max_hours,
        "report_exists": report.exists(),
    }), flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
