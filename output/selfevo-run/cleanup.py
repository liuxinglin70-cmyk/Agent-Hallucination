"""Stop this run's helper processes. Leaves the HTTP server for the user's page."""
from __future__ import annotations

import os
import sys
import time

import psutil

HELPERS = ("wait_" + "cycle.py", "monitor.py", "proc_inventory")


def classify(cmdline: list[str]) -> str:
    if not cmdline:
        return ""
    if not os.path.basename(cmdline[0]).lower().startswith("python"):
        return ""
    args = cmdline[1:]
    if not args:
        return ""
    for helper in HELPERS:
        if args[0].endswith(helper):
            return os.path.basename(args[0])
    # the inline final-waiter
    joined = " ".join(args)
    if joined.startswith("-c") and "experiment.json" in joined:
        return "inline-waiter"
    return ""


def main() -> int:
    mine = {os.getpid(), os.getppid()}
    targets = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        if proc.info["pid"] in mine:
            continue
        kind = classify(proc.info["cmdline"] or [])
        if kind:
            targets.append((proc.info["pid"], kind))
    for pid, kind in targets:
        print("stopping", pid, kind)
        try:
            psutil.Process(pid).kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    time.sleep(2)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
