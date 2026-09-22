"""Stop the running evolution experiment and its transport children.

Matches on the module path rather than a literal command-line string, so the
script cannot match its own command line and kill itself (which is exactly
what a ``-c`` one-liner containing the search text does).
"""
from __future__ import annotations

import sys
import time

import psutil

TARGET_MODULES = ("evolution", "detector" + ".provider_worker")
# ``python -m evolution experiment`` passes the subcommand as a separate
# argument, so the driver marker is the package alone, not ``evolution.experiment``.
MARKERS = tuple("-m " + module for module in TARGET_MODULES)


def matched(pid: int) -> tuple[bool, str]:
    if pid == __import__("os").getpid():
        return False, ""
    if pid == __import__("os").getppid():
        return False, ""
    try:
        cmdline = psutil.Process(pid).cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False, ""
    text = " ".join(cmdline)
    return any(marker in text for marker in MARKERS), text


def main() -> int:
    targets = [pid for pid in psutil.pids() if matched(pid)[0]]
    if not targets:
        print("nothing to stop")
        return 0
    for pid in targets:
        print("stopping", pid, matched(pid)[1][:90])
    for pid in targets:
        try:
            psutil.Process(pid).kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as exc:
            print("access denied", pid, exc)
    time.sleep(4)
    alive = [pid for pid in psutil.pids() if matched(pid)[0]]
    print("alive after kill:", alive)
    return 1 if alive else 0


if __name__ == "__main__":
    sys.exit(main())
