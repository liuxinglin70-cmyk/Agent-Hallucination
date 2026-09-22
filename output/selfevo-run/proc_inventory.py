"""Inventory self-evolution helper processes without matching this check itself."""
from __future__ import annotations

import os
import time

import psutil

TARGETS = ("wait_" + "cycle", "progress_" + "dashboard", "-m evolution",
           "detector" + ".provider_worker")


def main() -> int:
    mine = {os.getpid(), os.getppid()}
    found = []
    for proc in psutil.process_iter(["pid", "ppid", "cmdline", "create_time"]):
        if proc.info["pid"] in mine:
            continue
        cmdline = proc.info["cmdline"] or []
        if cmdline and os.path.basename(cmdline[0]).lower() in {"timeout.exe", "timeout"}:
            continue
        text = " ".join(cmdline)
        for target in TARGETS:
            if target in text:
                found.append((target, proc.info["pid"], proc.info["ppid"],
                              round(time.time() - (proc.info["create_time"] or 0), 1)))
                break
    counts: dict[str, int] = {}
    for target, pid, ppid, age in found:
        counts[target] = counts.get(target, 0) + 1
        print(f"  {target:24s} pid={pid:6d} ppid={ppid:6d} age_s={age}")
    print("counts:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
