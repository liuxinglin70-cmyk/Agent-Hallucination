"""Watch the promotion run and append a timestamped status line every interval.

Reads the process table through psutil-free means: the driver is identified by
its command line, and each provider_worker child is counted separately, because
"no child right now" is normal between the judge's votes and must not be read
as a hang.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOG = REPO / "artifacts/promotion-real/monitor.log"
RUN_LOG = REPO / "artifacts/promotion-real/run.log"
TERMINAL = REPO / "artifacts/promotion-real/run/promotion.json"


def _powershell(script: str) -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip()
    except Exception:
        return "?"


def driver_pids() -> str:
    """The promotion driver's pid, excluding the monitor's own query.

    The filter matches a command line, so this monitor (and the PowerShell
    carrying the filter text) also match unless the driver is pinned by image
    name and the query string is excluded. Reporting those would show phantom
    parallel runs, which is the exact confusion this monitor exists to catch.
    """
    script = (
        "(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' "
        "-and $_.CommandLine -like '*evolution promote*' "
        "-and $_.CommandLine -notlike '*Get-CimInstance*' "
        "-and $_.CommandLine -notlike '*monitor.py*' }).ProcessId -join ','"
    )
    return _powershell(script).strip() or "none"


def worker_count() -> str:
    script = (
        "(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' "
        "-and $_.CommandLine -like '*-m detector.provider_worker*' }).Count"
    )
    return _powershell(script).strip() or "0"


def arm_cases() -> int:
    if not RUN_LOG.is_file():
        return 0
    return sum(
        1 for line in RUN_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.startswith("[evolution]") and "/20" in line
    )


def main() -> int:
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    with LOG.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"monitor start {time.strftime('%H:%M:%S')}\n")
        handle.flush()
        for _ in range(2000):
            stamp = time.strftime("%H:%M:%S")
            if TERMINAL.is_file():
                report = json.loads(TERMINAL.read_text(encoding="utf-8-sig"))
                handle.write(
                    f"{stamp} TERMINAL state={report.get('state')} "
                    f"admitted={report.get('admitted')}\n"
                )
                handle.flush()
                break
            handle.write(
                f"{stamp} driver={driver_pids()} workers={worker_count()} "
                f"armcases={arm_cases()}\n"
            )
            handle.flush()
            time.sleep(interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
