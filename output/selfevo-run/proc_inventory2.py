"""Identify helper processes by argv position, immune to self-matching."""
from __future__ import annotations

import os
import time

import psutil


def classify(cmdline: list[str]) -> str:
    if not cmdline:
        return ""
    program = os.path.basename(cmdline[0]).lower()
    if not program.startswith("python"):
        return ""
    args = cmdline[1:]
    if not args:
        return ""
    if args[0].endswith("proc_inventory2.py") or args[0].endswith("proc_inventory.py"):
        return ""
    if args[0].endswith("wait_cycle.py"):
        return "wait_cycle"
    if args[0].endswith("progress_dashboard.py"):
        return "dashboard"
    if args[0].endswith("monitor.py"):
        return "monitor"
    # The driver is `-m evolution experiment ...`
    joined = " ".join(args)
    if joined.startswith("-m evolution") or joined.startswith("-m detector.provider_worker"):
        return "driver" if "evolution" in args[1] else "worker"
    return ""


def main() -> int:
    mine = {os.getpid(), os.getppid()}
    found = []
    for proc in psutil.process_iter(["pid", "ppid", "cmdline", "create_time"]):
        if proc.info["pid"] in mine:
            continue
        kind = classify(proc.info["cmdline"] or [])
        if kind:
            found.append((kind, proc.info["pid"], proc.info["ppid"],
                          round(time.time() - (proc.info["create_time"] or 0), 1)))
    counts: dict[str, int] = {}
    for kind, pid, ppid, age in sorted(found):
        counts[kind] = counts.get(kind, 0) + 1
        print(f"  {kind:9s} pid={pid:6d} ppid={ppid:6d} age_s={age}")
    print("counts:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
