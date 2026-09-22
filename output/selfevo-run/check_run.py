"""Report which evolution/probe processes are live, without matching self."""
from __future__ import annotations

import os
import psutil

MARKERS = ("-m evolution", "detector" + ".provider_worker",
           "envelope_validation", "timeout_probe", "thinking_probe")


def main() -> int:
    mine = {os.getpid(), os.getppid()}
    found = []
    for proc in psutil.process_iter(["pid", "cmdline", "create_time"]):
        if proc.info["pid"] in mine:
            continue
        cmdline = proc.info["cmdline"] or []
        if cmdline and os.path.basename(cmdline[0]).lower() in {"timeout.exe", "timeout"}:
            continue
        text = " ".join(cmdline)
        if any(marker in text for marker in MARKERS):
            found.append((proc.info["pid"], round(proc.info["create_time"], 1),
                          text[:95]))
    print("live processes:", len(found))
    for pid, created, text in found:
        print(" ", pid, created, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
