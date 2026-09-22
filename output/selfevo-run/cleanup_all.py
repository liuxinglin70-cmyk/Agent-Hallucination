"""Stop this project's helper processes before writing the final log."""
from __future__ import annotations

import os
import sys
import time

import psutil

HELPERS = ("wait_" + "cycle.py", "monitor.py", "proc_inventory",
           "progress_" + "dashboard.py", "cleanup")


def classify(cmdline: list[str]) -> str:
    if not cmdline:
        return ""
    program = os.path.basename(cmdline[0]).lower()
    args = cmdline[1:]
    if not args:
        return ""
    joined = " ".join(args)
    # The HTTP server that serves the dashboard page.
    if program.startswith("python") and "http.server" in joined:
        return "http-server"
    if not program.startswith("python"):
        return ""
    for helper in HELPERS:
        if args[0].endswith(helper):
            return os.path.basename(args[0])
    if args[0] == "-c" and ("experiment.json" in joined or "wait" in joined.lower()):
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
