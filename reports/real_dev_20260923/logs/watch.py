"""Wait for the cycle to finish, reporting ledger progress. Not an experiment."""
import json, pathlib, subprocess, sys, time

LEDGER = pathlib.Path("artifacts/selfevo-real-dev-20260923/ledger.jsonl")
deadline = time.time() + 5.5 * 3600
seen = set()
while time.time() < deadline:
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            key = (d.get("event"), str(d.get("at")))
            if key in seen:
                continue
            seen.add(key)
            print(f"[{d.get('at','?')}] {d.get('event')}", flush=True)
    # Is the cycle process still alive? Filter by command line: a bare
    # process count cannot tell a cycle from its provider workers.
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*evolution cycle*' } | "
         "Measure-Object).Count"],
        capture_output=True, text=True,
    ).stdout.strip()
    if out == "0":
        print("cycle process has exited", flush=True)
        break
    if any(e[0] == "cycle_completed" for e in seen):
        print("cycle_completed recorded", flush=True)
        break
    time.sleep(30)
else:
    print("watcher timed out", flush=True)
