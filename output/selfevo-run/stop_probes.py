"""Stop the read-only timing probes so the decisive run measures a clean box."""
from __future__ import annotations

import os
import sys
import time

import psutil

MARKERS = ("envelope_validation", "timeout_probe", "thinking_probe")
SKIP = {"timeout.exe", "timeout"}


def matched(pid: int) -> tuple[bool, str]:
    if pid in {os.getpid(), os.getppid()}:
        return False, ""
    try:
        proc = psutil.Process(pid)
        cmdline = proc.cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False, ""
    text = " ".join(cmdline)
    if os.path.basename(cmdline[0] if cmdline else "").lower() in SKIP:
        return False, ""
    return any(marker in text for marker in MARKERS), text


def main() -> int:
    targets = [pid for pid in psutil.pids() if matched(pid)[0]]
    for pid in targets:
        print("stopping", pid, matched(pid)[1][:80])
        try:
            psutil.Process(pid).kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    time.sleep(3)
    alive = [pid for pid in psutil.pids() if matched(pid)[0]]
    print("alive after kill:", alive)
    return 1 if alive else 0


if __name__ == "__main__":
    sys.exit(main())
